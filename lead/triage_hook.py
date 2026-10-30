"""triage_hook.py — Claude Code PreToolUse hook on AskUserQuestion: settle the pop-up if the model says so.

Installed once by `bw triage install claude`; inert unless bw set BW_TRIAGE_HOOK and
BW_TRIAGE_CONFIG for this lead. Not decided, or anything at all going wrong, prints nothing: the
pop-up shows exactly as it would have, and there is no record to lose or duplicate. Decided: write
a receipt to BW_ASKS (no receipt, no decision), then, in acting mode, deny with a reason that tells
the lead to take the recommended option. An alarm bounds the model call; the settings entry's
timeout is the backstop, and a killed hook lets the pop-up show.
"""
import json
import os
import re
import secrets
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lead import triage  # noqa: E402

ALARM_S = 4
MARK = re.compile(r"\(recommended\)", re.I)
REASON = ("bw settled this question without the user: take the option marked Recommended, say in one line what you "
          "chose, and carry on. This is not the user's answer and not permission for anything you would otherwise "
          "have to ask for.")


def render(questions):
    """The questions with every option's detail, because a risk can be stated only on the option not
    recommended; None unless every question has a recommended option."""
    parts = []
    for q in questions:
        options = q["options"]
        rec = next((o for o in options if MARK.search(o["label"])), None)
        if rec is None:
            return None
        lines = [f"[{q['header']}] {q['question']}" if q.get("header") else q["question"]]
        lines += [f"- {o['label']}: {o.get('description', '')}" for o in options]
        detail = f" — {rec['description']}" if rec.get("description") else ""
        lines.append(f"Recommendation: {MARK.sub('', rec['label']).strip()}{detail}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else None


def write_receipt(asks, mode, scores, text):
    """Published the way any ask is, under a fresh name, so a re-asked question gets its own record."""
    name = secrets.token_hex(8)
    tmp = os.path.join(asks, f".tmp-{name}")
    with open(tmp, "w") as f:
        f.write(f"@session decided\n{json.dumps({'mode': mode, 'scores': scores})}\n{text}\n")
    os.rename(tmp, os.path.join(asks, f"{name}.ask"))


def _alarm(*_):
    raise TimeoutError


def timed(judge, seconds=ALARM_S):
    """judge under an alarm that covers the model call alone, so it can never fire between the
    receipt and the deny and leave a record of a decision the pop-up contradicts."""
    def run(*a, **kw):
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(seconds)
        try:
            return judge(*a, **kw)
        finally:
            signal.alarm(0)
    return run


def decide(event, env, judge=triage.judge):
    """The hook's stdout, "" for no opinion."""
    cfg = json.loads(env["BW_TRIAGE_CONFIG"])
    if event.get("tool_name") != "AskUserQuestion":
        return ""
    text = render(event["tool_input"]["questions"])
    if text is None:
        return ""
    v = judge(text, cfg, env=env)
    if v["verdict"] != "decide":
        return ""
    write_receipt(env["BW_ASKS"], cfg["mode"], v.get("scores"), text)
    if cfg["mode"] != "acting":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                              "permissionDecisionReason": REASON}})


def main():
    try:
        out = decide(json.load(sys.stdin), os.environ, judge=timed(triage.judge))
        if out:
            print(out)
    except BaseException:  # noqa: BLE001 — a broken hook must never cost the user a question
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
