"""judging.py — run the judge in a child, remember its verdicts, and stop asking it while it is down.

The judge (judge.py) is untrusted and may fail. Every failure or skip keeps the rules' decision:
`final_decision` lifts a gray ask to allow only on a real judge verdict, and never changes a rule's
own decision. The child gets the call text and the name of the key's variable on stdin; the key itself
reaches the child only through its inherited environment, never argv, a record, or a log.

A breaker opens after BREAKER_AFTER failures in a row and stays open for BREAKER_S; on opening it sends
one `guard-judge-down` notice, and a later success closes it and allows a new notice. Only successes
are cached, by (digest, cwd), for CACHE_S. One lock makes a runner safe to share between threads.
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
SHADOW_BUDGET_S = 1.35  # stands in for acting_budget(rules_s) until the acting waits arrive (Task 5)
CACHE_S = 600
BREAKER_AFTER = 3
BREAKER_S = 300
URL_TIMEOUT_MARGIN = 0.05  # urllib's own timeout ends just before the child is killed
MIN_URL_TIMEOUT = 0.05
MAX_FAIL_REASON = 80
DOWN_TEXT = ("The guard judge failed %d times in a row. Gray calls keep the rules' decision for %d minutes. "
             "Check the judge's key and model.")


def final_decision(rule_decision, gray, result):
    """The decision to record and, in acting, to apply. Only a judge verdict on a gray ask changes it,
    and only to allow or ask; every failure or skip keeps the rules' decision (the floor)."""
    if (gray and rule_decision == "ask" and result is not None and result.source == "judge"
            and result.decision in ("allow", "ask")):
        return result.decision
    return rule_decision


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
        user, why = judge.prepare(call, self.worktree, self.home)
        if user is None:
            return _skipped(why)
        key = (digest, call.cwd)
        now = self.clock()
        with self._lock:
            if self._open_until is not None and now < self._open_until:
                return _skipped("judge circuit open")
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < CACHE_S:
                return hit[1]
        result = self._attempt(user, budget_s)
        self._settle(key, result)
        return result

    def _attempt(self, user, budget_s):
        t0 = self.clock()
        try:
            given = {"text": user, "config": dict(self.cfg),
                     "timeout": max(MIN_URL_TIMEOUT, budget_s - URL_TIMEOUT_MARGIN)}
            with tempfile.TemporaryFile("w+") as f:
                json.dump(given, f)
                f.seek(0)
                r = (self.run or proc.run)(list(self.argv), budget_s, stdin=f, reap=True)
            source, decision, reason = self._read(r)
        except Exception as e:  # noqa: BLE001 — a type name only: an exception's text can carry the key
            source, decision, reason = "judge-failed", None, type(e).__name__
        ms = round((self.clock() - t0) * 1000, 3)
        return JudgeResult(source, decision, reason, ms, 1)

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
            return "judge", got["decision"], redact(got["reason"])[:judge.MAX_REASON]
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
