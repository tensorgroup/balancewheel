"""render.py — the texts of a panel: the seat brief for each checkpoint, and the lead's `next.md`.

Seats and the lead read these texts, so they use short sentences and literal paths. Text that a seat or
the lead wrote is hostile: `safe()` shows every control character as an escape, and no such text passes
through a `string.Template` as part of the template itself (a `$` inside a value stays as it is).
A path inside a shell command is `shlex.quote`d. A path given to the file-write tool is plain.
"""
import json
import os
import shlex
import string

from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead.guard.review import safe
from panel import merge, roster
from panel import state as pstate
import config as cfgmod

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
ROUND1_PROMPT = "Do the review the brief describes. End with the JSON block it asks for."
ASK_FRAME = "Another reviewer claims: {claim}. Do you agree? Give evidence (file:line) or concede."
VERDICT_CHOICES = "confirmed|partial|refuted|dropped"
DEFAULT_MAX_ROUNDS = 2


def _template(name: str) -> str:
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def brief(checkpoint, target, focus, seat_brief: str) -> str:
    """The brief a seat reads: the checkpoint's text, the target, the focus, the output rule, then the
    seat's own brief (Plan 5)."""
    name = roster.ALIASES.get(checkpoint, checkpoint)
    if name not in cfgmod.ROLES:
        raise ValueError(f"no brief for checkpoint {safe(checkpoint)[:40]}")
    parts = [_template(f"brief-{name}.md").rstrip("\n"), "", f"Target: {safe(target)}"]
    if focus:
        parts.append(f"Focus: {safe(focus)}")
    parts += ["", _template("brief-output.md").rstrip("\n"), "", seat_brief]
    return "\n".join(parts)


TARGET_CHARS = 300


def request_id(kind, pid, n=1) -> str:
    """The request id next.md gives for an ask, a log, or an amend. It holds the panel id, so ids of two
    panels never collide in the worktree's one `responses/` dir."""
    return f"{kind}-{safe(pid)}-{n}"


def _panel_lines(doc, panel) -> str:
    lines = [f"- Checkpoint: {safe(doc['checkpoint'])}. Tier: {safe(doc['tier'])}.",
             f"- Target: {merge.cut(str(doc.get('target') or ''), TARGET_CHARS)}"]
    if doc.get("short"):
        reasons = "; ".join(safe(r) for r in doc.get("short_reasons") or []) or "no reason given"
        lines.append(f"- Size: short. Reasons: {reasons}.")
    else:
        lines.append("- Size: full.")
    for seat, view in pstate.status_view(panel)["seats"].items():
        lines.append(f"- Seat {safe(seat)}: {safe(view)}")
    return "\n".join(lines)


def _needs_verdict(doc, member) -> bool:
    seat = doc["seats"].get(member["seat"]) or {}
    return not (seat.get("unexpected_output") and not seat.get("on_roster", True))


def _findings(doc, view_dir) -> str:
    out = []
    for g in doc["groups"]:
        out += ["", f"### {g['group']} — {g['title']}"]
        seats = []
        for m in g["members"]:
            note = "" if _needs_verdict(doc, m) else " (off roster; no verdict needed)"
            out.append(f"- {m['id']} {safe(m['seat'])} {m['severity']} {m['where']} — {m['title']}{note}")
            out.append(f"  {m['claim']}")
            if m["seat"] not in seats:
                seats.append(m["seat"])
        out.append("  Seat files: " + ", ".join(os.path.join(view_dir, "seats", f"{safe(s)}.md") for s in seats))
    cands = doc.get("candidate_disputes") or []
    if cands:
        out += ["", "Candidate disputes:"]
        out += [f"- {safe(c['id'])} ({c['group']}): {safe(c['reason'])}" for c in cands]
    return "\n".join(out)


def _ids(doc) -> list:
    members = [m for g in doc["groups"] for m in g["members"] if _needs_verdict(doc, m)]
    return sorted((m["id"] for m in members), key=lambda i: int(i[1:]))


def _write_rules(where="responses") -> str:
    return ("Replace every <…> and every a|b value before you write the file. "
            "The nonce is your own short id. bw copies it into the response, so you know the response is yours. "
            f"If the request is bad, bw answers with a refusal in `{where}/<id>.json`.")


def _body(obj) -> str:
    return "```json\n" + json.dumps(obj, indent=2) + "\n```"


def _request_files(request_dir, name):
    """(file-tool path, shell `mv` command) for one request. The request is written to `.tmp-<name>`,
    then renamed to `<name>.request`."""
    quoted = shlex.quote(request_dir)
    return (f"{request_dir}/.tmp-{name}", f"mv {quoted}/.tmp-{name} {quoted}/{name}.request")


def next_md(doc, panel, paths: dict, config) -> str:
    request_dir, view_dir = paths["request_dir"], paths["view_dir"]
    pid = safe(doc["panel_id"])
    rounds = (config.get("panel") or {}).get("max_rounds", DEFAULT_MAX_ROUNDS)
    ask_id, ask_next, log_id, log_next = (request_id("ask", pid), request_id("ask", pid, 2),
                                          request_id("log", pid), request_id("log", pid, 2))
    ask_file, ask_mv = _request_files(request_dir, ask_id)
    log_file, log_mv = _request_files(request_dir, log_id)
    cands = doc.get("candidate_disputes") or []
    dispute = safe(cands[0]["id"]) if cands else "d1"
    responses = responses_dir(view_dir)
    ask = {"v": 1, "type": "ask", "panel": pid, "nonce": "<your own short id>",
           "seat": "<a seat that did not raise the finding>", "dispute": dispute,
           "claim": "<one claim, at most 1500 characters>"}
    log = {"v": 1, "type": "log", "panel": pid, "nonce": "<your own short id>",
           "verdicts": {i: VERDICT_CHOICES for i in _ids(doc)}, "groups": {}, "severity": {}, "added": [],
           "disputes": [], "notes": ""}
    failed = ""
    if doc.get("failed"):
        failed = (f"## Failed\nThis panel failed: {safe(doc['failed'])}.\n"
                  "The findings below are from the seats that finished.\n"
                  "Verify them. Log the panel as usual.\nbw's summary line says it failed.\n\n")
    triage = _template("next-triage.md") if doc.get("triage_required") else ""
    return string.Template(_template("next.md")).substitute(
        failed=failed, panel_id=pid, panel_lines=_panel_lines(doc, panel), findings=_findings(doc, view_dir),
        ask_file=ask_file, ask_mv=ask_mv, ask_body=_body(ask), frame=ASK_FRAME.format(claim="<claim>"),
        max_rounds=rounds, round_word="round" if rounds == 1 else "rounds", view_dir=view_dir,
        triage=triage, write_rules=_write_rules(responses), log_file=log_file, log_mv=log_mv, log_body=_body(log),
        ids=", ".join(_ids(doc)), responses=responses, ask_id=ask_id, ask_next=ask_next, log_id=log_id,
        log_next=log_next)


def responses_dir(view_dir) -> str:
    """The worktree's `responses/` dir, next to the panel view dirs."""
    return os.path.join(os.path.dirname(view_dir), "responses")


def failed_next(pid, why, request_dir, view_dir) -> str:
    """The short next.md of a panel whose full next.md could not be written: it still says how to log."""
    rid = request_id("log", pid)
    path, mv = _request_files(request_dir, rid)
    body = {"v": 1, "type": "log", "panel": safe(pid), "nonce": "<your own short id>", "verdicts": {},
            "notes": ""}
    return (f"# Panel {safe(pid)}\n\nThis panel failed: {safe(why)}.\n"
            f"Read the seat files in {os.path.join(view_dir, 'seats')}, if there are any.\n\n"
            f"## Log\n1. Write only the JSON between the fence lines to {path}. "
            f"{_write_rules(responses_dir(view_dir))}\n{_body(body)}\n2. Run this command: {mv}\n"
            f"3. bw answers in `{responses_dir(view_dir)}/{rid}.json`. Check that the nonce is yours.\n")


def follow_up(sample: list, request_dir: str, pid: str, responses: str) -> str:
    """The section appended after a `verification-skim` watch. `responses` is the absolute responses dir."""
    rid = request_id("amend", pid)
    path, mv = _request_files(request_dir, rid)
    body = {"v": 1, "type": "amend", "panel": safe(pid), "nonce": "<your own short id>",
            "patch": {"verdicts": {safe(sample[0]) if sample else "f1": VERDICT_CHOICES}}}
    return string.Template(_template("next-follow-up.md")).substitute(
        sample=", ".join(safe(s) for s in sample), file=path, mv=mv, body=_body(body),
        write_rules=_write_rules(responses), responses=responses, amend_id=rid,
        amend_next=request_id("amend", pid, 2))
