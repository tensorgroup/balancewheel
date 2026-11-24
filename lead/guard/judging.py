"""judging.py — run the judge in a child, remember its verdicts, and stop asking it while it is down.

The judge (judge.py) is untrusted and may fail. Every failure or skip keeps the rules' decision:
`final_decision` lifts a gray ask to allow only on a real judge verdict, and never changes a rule's
own decision. The child gets the call text and the name of the key's variable on stdin; the key itself
reaches the child only through its environment, never argv, a record, or a log. That environment is
child_env(): the key's variable and a few basics, no other secret of bw's. A cache hit starts no child,
so it reports ms 0 and attempts 0.

A breaker opens after BREAKER_AFTER failures in a row and stays open for BREAKER_S; on opening it sends
one `guard-judge-down` notice, and a later success closes it and allows a new notice. Only successes
are cached, by (digest, cwd), for CACHE_S. One lock makes a runner safe to share between threads.

The budget: the hook waits HOOK_DEADLINE for a reply. The broker spends up to READ_DEADLINE reading the
request and `rules_s` deciding it, and keeps JUDGE_MARGIN to write the record and the reply, so
acting_budget(rules_s) is what is left for the judge. A judge() call makes one attempt with the whole
budget, and one retry only when the first failed with at least MIN_ATTEMPT_S left. A budget below
MIN_ATTEMPT_S at the start starts no child: the result is `judge-skipped`, "no time left". A failure
after the retry counts once for the breaker.
"""
import json
import os
import sys
import tempfile
import threading
import time
from collections import namedtuple

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))

from lead import proc  # noqa: E402
from lead.guard import judge  # noqa: E402
from lead.guard.redact import redact  # noqa: E402

JudgeResult = namedtuple("JudgeResult", "source decision reason ms attempts")
JUDGE_PY = os.path.join(os.path.dirname(os.path.realpath(__file__)), "judge.py")
HOOK_DEADLINE = 2.0  # hook.DEADLINE and pi_guard.ts DEADLINE_MS / 1000 (a test holds them equal)
READ_DEADLINE = 0.5  # the broker's own time limit to read one request (broker.READ_DEADLINE)
JUDGE_MARGIN = 0.15  # the record and the reply, after the judge
MIN_ATTEMPT_S = 0.3  # less than this is too little for a judge call to succeed
CACHE_S = 600
BREAKER_AFTER = 3
BREAKER_S = 300
URL_TIMEOUT_MARGIN = 0.05  # urllib's own timeout ends just before the child is killed
MIN_URL_TIMEOUT = 0.05
MAX_FAIL_REASON = 80
# What the judge child inherits besides the key's variable: no other secret of bw's environment.
CHILD_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "SSL_CERT_FILE", "SSL_CERT_DIR",
             "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")
DOWN_TEXT = ("The guard judge failed %d times in a row. Gray calls keep the rules' decision for %d minutes. "
             "Check the judge's key and model.")


def final_decision(rule_decision, gray, result):
    """The decision to record and, in acting, to apply. Only a judge verdict on a gray ask changes it,
    and only to allow or ask; every failure or skip keeps the rules' decision (the floor)."""
    if (gray and rule_decision == "ask" and result is not None and result.source == "judge"
            and result.decision in ("allow", "ask")):
        return result.decision
    return rule_decision


def acting_budget(rules_s):
    """The time left for the judge, in seconds, when the rules took `rules_s` after the read."""
    return max(0.0, HOOK_DEADLINE - READ_DEADLINE - rules_s - JUDGE_MARGIN)


def clean_reason(text):
    """A judge's reason as bw shows or records it: no control characters, secrets scrubbed, capped."""
    return judge.scrub(redact(judge._clean(text)))[:judge.MAX_REASON]


def child_env(key_env, environ=None):
    """The judge child's environment: the key's variable, PATH, HOME, TMPDIR, LANG and LC_*, and the
    proxy and SSL variables (either case). Nothing else of bw's environment reaches the child."""
    environ = os.environ if environ is None else environ
    keep = set(CHILD_ENV)
    return {k: v for k, v in environ.items()
            if k == key_env or k.upper() in keep or k.startswith("LC_")}


def _skipped(reason):
    return JudgeResult("judge-skipped", None, reason, None, 0)


class JudgeRunner:
    def __init__(self, cfg, worktree, home, run=None, clock=time.monotonic, notice=None):
        self.cfg = {"model": cfg.get("model"), "key_env": cfg.get("key_env")}
        self.worktree, self.home = worktree, home
        self.run = run  # None: proc.run, looked up at call time so a patch of it applies
        self.clock = clock
        self.notice = notice  # the broker sets its own wrapper here
        self.prompt_hash = judge.prompt_hash() if self.cfg["model"] else None
        self.argv = [sys.executable, "-I", JUDGE_PY]
        self._lock = threading.Lock()
        self._cache = {}
        self._fails = 0
        self._open_until = None
        self._noticed = False

    def judge(self, call, digest, budget_s):
        if not self.cfg["model"]:
            return _skipped("no judge")
        start = self.clock()  # read once here and once after each attempt (the tests count on it)
        user, why = judge.prepare(call, self.worktree, self.home)  # its time is in ms too
        if user is None:
            return _skipped(why)
        key = (digest, call.cwd)
        with self._lock:
            if self._open_until is not None and start < self._open_until:
                return _skipped("judge circuit open")
            hit = self._cache.get(key)
            if hit is not None and start - hit[0] < CACHE_S:
                return hit[1]._replace(ms=0, attempts=0)  # no child ran for this call
        if budget_s < MIN_ATTEMPT_S:
            return _skipped("no time left")
        end = start + budget_s
        source, decision, reason = self._attempt(user, budget_s)
        now, attempts = self.clock(), 1
        if source == "judge-failed" and end - now >= MIN_ATTEMPT_S:
            source, decision, reason = self._attempt(user, end - now)
            now, attempts = self.clock(), 2
        result = JudgeResult(source, decision, reason, round((now - start) * 1000, 3), attempts)
        self._settle(key, result)
        return result

    def _attempt(self, user, timeout):
        """(source, decision, reason) from one child that may run for `timeout` seconds."""
        try:
            given = {"text": user, "config": dict(self.cfg),
                     "timeout": max(MIN_URL_TIMEOUT, timeout - URL_TIMEOUT_MARGIN)}
            with tempfile.TemporaryFile("w+") as f:
                json.dump(given, f)
                f.seek(0)
                r = (self.run or proc.run)(list(self.argv), timeout, stdin=f, reap=True,
                                           env=child_env(self.cfg["key_env"]))
            return self._read(r)
        except Exception as e:  # noqa: BLE001 — a type name only: an exception's text can carry the key
            return "judge-failed", None, type(e).__name__

    @staticmethod
    def _read(r):
        if r.timed_out:
            return "judge-failed", None, "timeout"
        if r.rc != 0:
            return "judge-failed", None, "exit %d" % r.rc
        try:
            got = json.loads(r.out)
        except ValueError:
            return "judge-failed", None, "no verdict"
        if not isinstance(got, dict):
            return "judge-failed", None, "no verdict"
        if got.get("ok") is True and got.get("decision") in ("allow", "ask") and isinstance(got.get("reason"), str):
            return "judge", got["decision"], clean_reason(got["reason"])
        if got.get("ok") is False and isinstance(got.get("reason"), str) and got["reason"]:  # G-j
            return "judge-failed", None, redact(judge._clean(got["reason"]))[:MAX_FAIL_REASON]
        return "judge-failed", None, "no verdict"

    def _settle(self, key, result):
        """Cache a success and close the breaker, or count a failure and open it; notice outside the lock."""
        now = self.clock()
        tell = False
        with self._lock:
            if result.source == "judge":
                self._cache = {k: v for k, v in self._cache.items() if now - v[0] < CACHE_S}
                self._cache[key] = (now, result)
                self._fails, self._open_until, self._noticed = 0, None, False
            elif result.source == "judge-failed":
                self._fails += 1
                if self._fails >= BREAKER_AFTER:
                    self._open_until = now + BREAKER_S
                    tell, self._noticed = not self._noticed, True
        if tell and self.notice is not None:
            try:
                self.notice("guard-judge-down", DOWN_TEXT % (BREAKER_AFTER, BREAKER_S // 60))
            except Exception:  # noqa: BLE001 — a failing notice never stops the judge (B6)
                pass
