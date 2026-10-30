"""triage.py — does a lead's question need the user, or does its recommendation just stand?

A decision model answers four yes/no questions about the text; `policy` here, not the model, turns
the probabilities into a verdict. "ask" is every failure's answer: judge() never raises, and a
question it cannot judge reaches the user exactly as it would have without triage.

Run as a script (`python3 -I lead/triage.py`) it reads {"text", "config"} on stdin and prints one
verdict, so bw can kill it at a real wall-clock bound: a urllib timeout bounds each read, not the
request. The hook imports judge() and bounds it with an alarm.

The text is hostile: `prefilter` sends anything that reads as outward or irreversible to the user
without a model call, and `scrub` is best-effort redaction, not a secrets scanner. A verdict grants
nothing; it only spares the user a question.
"""
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lead.guard.redact import redact  # noqa: E402

URL = "https://openrouter.ai/api/alpha/decisions"
REQUEST_TIMEOUT_S = 2.5
MAX_CHARS = 2000
DEFAULT_THRESHOLDS = {"strong_case": 0.6, "only_person_knows": 0.7, "outward_or_irreversible": 0.3, "high_stakes": 0.3}
_AT_LEAST = ("strong_case",)  # every other threshold is a ceiling

QUESTIONS = {
    "strong_case": {
        "type": "noul",
        "instructions": "Does the text give one clear recommendation with a concrete reason, strong enough that a "
                        "careful colleague would accept it without asking for anything more?",
        "criteria": {"true": "A clear recommendation backed by a concrete reason.",
                     "false": "No clear recommendation, or a weak or unexplained one."},
    },
    "only_person_knows": {
        "type": "noul",
        "instructions": "Does the right answer depend on something only the project's owner can know or decide: "
                        "taste, brand, money, legal position, a relationship, or facts missing from the text?",
        "criteria": {"true": "The owner's own judgment or knowledge is needed.",
                     "false": "Anyone with the text could settle it."},
    },
    "outward_or_irreversible": {
        "type": "noul",
        "instructions": "Would following the recommendation publish, send, spend, delete, change production, or "
                        "otherwise be hard to undo?",
        "criteria": {"true": "It reaches outside the working copy or is hard to undo.",
                     "false": "It stays local and is easy to reverse."},
    },
    "high_stakes": {
        "type": "noul",
        "instructions": "Does this touch authentication, payments, access-control rules, private user data, or "
                        "production data migration?",
        "criteria": {"true": "It touches one of those areas.", "false": "It does not."},
    },
}

_RECOMMENDATION = re.compile(r"(?m)^\s*Recommendation:[ \t]*\S")
_DENY = re.compile(r"(?i)\b(deploy\w*|push\w*|force-push\w*|publish\w*|release\w*|send\w*|email\w*|delet\w*|drops?|dropped|dropping|wipe\w*"
                   r"|pay|pays|paid|payment\w*|charg\w*|refund\w*|billing|secrets?|passwords?|credentials?|api keys?"
                   r"|production|prod|migrat\w*)\b")
_SCRUB = [re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), re.compile(r"\+?\d[\d \-().]{8,}\d")]


def prefilter(text):
    """Why this text goes to the user with no model call, or None when the model may judge it."""
    if len(text) > MAX_CHARS:
        return "too long"
    if not _RECOMMENDATION.search(text):
        return "no recommendation"
    if _DENY.search(text):
        return "denylist"
    return None


def scrub(text):
    text = redact(text)
    for rx in _SCRUB:
        text = rx.sub("[redacted]", text)
    return text


def failing(scores, thresholds):
    """Every question whose score did not clear its threshold, in QUESTIONS order."""
    return [name for name in QUESTIONS if not
            (scores[name] >= thresholds[name] if name in _AT_LEAST else scores[name] <= thresholds[name])]


def policy(scores, thresholds):
    """("decide", why) only when every score clears its threshold, else ("ask", the first that did not)."""
    stopped = failing(scores, thresholds)
    return ("ask", stopped[0]) if stopped else ("decide", "strong case")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None


def _post(body, key, timeout, url=URL):
    if url != URL:
        raise ValueError("triage posts only to its fixed URL")
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    req.add_unredirected_header("Authorization", "Bearer " + key)
    with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as r:
        return json.load(r)


def _scores(reply):
    scores = {}
    for name in QUESTIONS:
        v = reply["answers"][name]["noul"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
            raise ValueError(name)
        scores[name] = v
    return scores


def judge(text, cfg, post=_post, env=os.environ):
    """{"verdict": "decide"|"ask", "reason", and "scores"/"cost" when the model answered, "failed" when it could
    not}. Never raises; failures are named by type, because an exception's text can carry the key."""
    why = prefilter(text)
    if why:
        return {"verdict": "ask", "reason": why}
    key = env.get(cfg["key_env"])
    if not key:
        return {"verdict": "ask", "reason": "key unset", "failed": True}
    body = {"model": cfg["model"], "state": {"text": scrub(text)}, "questions": QUESTIONS, "provider": {"zdr": True}}
    try:
        reply = post(body, key, REQUEST_TIMEOUT_S)
        scores = _scores(reply)
        verdict, reason = policy(scores, cfg["thresholds"])
        usage = reply.get("usage")
        return {"verdict": verdict, "reason": reason, "scores": scores,
                "cost": usage.get("cost") if isinstance(usage, dict) else None}
    except Exception as e:  # noqa: BLE001 — every failure reaches the user as an ordinary question
        return {"verdict": "ask", "reason": type(e).__name__, "failed": True}


def main():
    try:
        given = json.load(sys.stdin)
        out = judge(given["text"], given["config"])
    except Exception as e:  # noqa: BLE001
        out = {"verdict": "ask", "reason": type(e).__name__, "failed": True}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
