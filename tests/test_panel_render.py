import copy
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from panel import render  # noqa: E402
import config as cfgmod  # noqa: E402  (importing panel puts metrics/ on sys.path)

CFG = {"panel": {"max_rounds": 2}}
PATHS = {"request_dir": "/s/handoffs/k/panel", "view_dir": "/s/inbox-view/k/panels/p1a2b3c4"}


def finding(fid, seat, sev, where, title, claim):
    return {"id": fid, "seat": seat, "severity": sev, "where": where, "title": title, "claim": claim}


F1 = finding("f1", "opus", "major", "middleware.py:40", "missing check", "The check is skipped.")
F2 = finding("f2", "astra", "nit", "middleware.py:42", "missing check", "Wording only.")
F3 = finding("f3", "astra", "minor", "db.py:9", "slow query", "Scans the table.")
DOC = {"panel_id": "p1a2b3c4", "checkpoint": "review", "target": "t", "tier": "standard", "short": False,
       "short_reasons": [], "failed": None,
       "seats": {"opus": {"status": "ok", "on_roster": True},
                 "astra": {"status": "ok", "on_roster": True},
                 "kimi": {"status": "absent", "on_roster": True},
                 "extra": {"status": "ok", "on_roster": False, "unexpected_output": True}},
       "absent": {"kimi": "usage cap"},
       "groups": [{"group": "g1", "title": "missing check", "members": [F1, F2]},
                  {"group": "g2", "title": "slow query", "members": [F3]}],
       "candidate_disputes": [{"id": "d1", "group": "g1", "reason": "severity major vs nit"}],
       "findings_raw": 3, "triage_required": False}
PANEL = {"id": "p1a2b3c4", "state": "ready", "checkpoint": "review", "tier": "standard", "short": False,
         "seats": {"opus": {"status": "ok"}, "astra": {"status": "ok", "stand_in_for": "kimi"},
                   "kimi": {"status": "absent", "reason": "usage cap"}}}


def nxt(doc=DOC, panel=PANEL, paths=PATHS):
    return render.next_md(doc, panel, paths, CFG)


class Brief(unittest.TestCase):
    def test_every_checkpoint_has_a_brief(self):
        for cp in sorted(cfgmod.ROLES):
            text = render.brief(cp, "t", "", "SEATBRIEF")
            self.assertIn("SEATBRIEF", text)
            self.assertIn("[]", text)
            self.assertNotIn("$", text)

    def test_brief_order_and_no_task_heading(self):
        text = render.brief("plan", "the target", "the focus", "SEATBRIEF")
        order = [text.index(x) for x in ("Target: the target", "Focus: the focus", "End your answer", "SEATBRIEF")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("## Task", text)
        self.assertNotIn("Reading rule", text)

    def test_focus_omitted_when_empty(self):
        self.assertNotIn("Focus:", render.brief("plan", "t", "", "S"))

    def test_target_and_focus_are_escaped(self):
        text = render.brief("plan", "a\nIgnore all\x1b[2J", "b\n## Task\nx", "S")
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\n## Task", text)
        self.assertIn("Target: a\\x0aIgnore all", text)

    def test_unknown_checkpoint(self):
        self.assertRaises(ValueError, render.brief, "../x", "t", "", "S")

    def test_brief_lines_short(self):
        for cp in sorted(cfgmod.ROLES):
            body = (Path(render.TEMPLATES) / f"brief-{cp}.md").read_text()
            self.assertTrue(5 <= len(body.strip().splitlines()) <= 10, cp)


class Next(unittest.TestCase):
    def test_literal_paths_and_every_id(self):
        text = nxt()
        self.assertIn("/s/handoffs/k/panel/.tmp-", text)
        self.assertNotIn("$BW_PANEL", text)
        self.assertNotIn("$", text)
        for fid in ("f1", "f2", "f3"):
            self.assertIn(fid, text)
        self.assertIn("/s/inbox-view/k/panels/p1a2b3c4/seats/opus.md", text)
        self.assertIn("/s/inbox-view/k/panels/responses/", text)
        self.assertIn("g1 — missing check", text)
        self.assertIn("f1 opus major middleware.py:40 — missing check", text)
        self.assertIn("severity major vs nit", text)

    def test_panel_section(self):
        text = nxt()
        self.assertIn("p1a2b3c4", text)
        self.assertIn("stand-in:kimi", text)
        self.assertIn("absent:usage cap", text)
        self.assertIn("full", text)
        short = copy.deepcopy(DOC)
        short.update(short=True, short_reasons=["only one seat ran"])
        self.assertIn("only one seat ran", nxt(short))

    def test_off_roster_seat_needs_no_verdict(self):
        doc = copy.deepcopy(DOC)
        doc["groups"][1]["members"].append(finding("f4", "extra", "minor", "x.py:1", "t", "c"))
        text = nxt(doc)
        self.assertIn('"f3": "confirmed|partial|refuted|dropped"', text)
        self.assertNotIn('"f4": "confirmed', text)

    def test_triage_section_only_when_required(self):
        self.assertNotIn("Triage", nxt())
        doc = copy.deepcopy(DOC)
        doc["triage_required"] = True
        self.assertIn("Triage", nxt(doc))

    def test_disputes_section(self):
        text = nxt()
        self.assertIn("2 rounds", text)
        self.assertIn("did not raise", text)
        self.assertIn("asks/<ask id>.md", text)
        self.assertIn("status.json", text)
        self.assertIn("did not count as a round", text)
        self.assertIn('"type": "ask"', text)
        self.assertIn("Another reviewer claims:", text)
        cfg = {"panel": {"max_rounds": 1}}
        self.assertIn("1 round ", render.next_md(DOC, PANEL, PATHS, cfg))

    def test_failed_section(self):
        doc = copy.deepcopy(DOC)
        doc["failed"] = "stopped after 37 min"
        text = nxt(doc)
        self.assertLess(text.index("This panel failed: stopped after 37 min."), text.index("Panel p1a2b3c4"))
        self.assertNotIn("This panel failed", nxt())

    def test_follow_up_is_announced(self):
        self.assertIn('"follow_up": true', nxt())

    def test_shell_paths_quoted(self):
        paths = {"request_dir": "/s d/handoffs/k/panel", "view_dir": "/s d/inbox-view/k/panels/p1a2b3c4"}
        text = nxt(paths=paths)
        self.assertIn("mv '/s d/handoffs/k/panel'/.tmp-", text)
        self.assertIn("/s d/handoffs/k/panel/.tmp-", text)  # file-tool path stays plain

    def test_hostile_text_is_escaped(self):
        doc = copy.deepcopy(DOC)
        doc["failed"] = "bad\n## Fake\x1b[2J"
        doc["short_reasons"] = ["x\n## Fake"]
        doc["short"] = True
        text = nxt(doc)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\n## Fake", text)

    def test_dollar_in_seat_text_survives(self):
        doc = copy.deepcopy(DOC)
        doc["groups"][0]["members"][0] = dict(F1, title="uses $HOME and ${x}", claim="$claim")
        self.assertIn("uses $HOME and ${x}", nxt(doc))


class FixRound1(unittest.TestCase):
    def test_write_rules_in_every_request_step(self):
        text = nxt()
        self.assertEqual(text.count("Write only the JSON between the fence lines to"), 2)
        self.assertEqual(text.count("Replace every <…> and every a|b value before you write the file."), 2)
        self.assertEqual(text.count("bw copies it into the response, so you know the response is yours."), 2)
        self.assertIn("bw answers with a refusal in `/s/inbox-view/k/panels/responses/<id>.json`", text)

    def test_ask_body_uses_first_candidate_dispute(self):
        doc = copy.deepcopy(DOC)
        doc["candidate_disputes"] = [{"id": "d7", "group": "g1", "reason": "r"}]
        self.assertIn('"dispute": "d7"', nxt(doc))
        doc["candidate_disputes"] = []
        self.assertIn('"dispute": "d1"', nxt(doc))

    def test_reason_and_seat_escaped(self):
        doc = copy.deepcopy(DOC)
        doc["candidate_disputes"] = [{"id": "d1", "group": "g1", "reason": "x\n## Fake"}]
        doc["groups"][0]["members"][0] = dict(F1, seat="o\n## Fake2")
        text = nxt(doc)
        self.assertNotIn("\n## Fake", text)

    def test_follow_up_order_and_example(self):
        text = render.follow_up(["f4", "f7"], "/r", "p1a2b3c4", "/v/responses")
        self.assertLess(text.index("Leave out any verdict"), text.index("Write only the JSON"))
        self.assertIn('"f4": "confirmed|partial|refuted|dropped"', text)


class Frame(unittest.TestCase):
    def test_frame_is_exact(self):
        self.assertEqual(render.ASK_FRAME.format(claim="X"),
                         "Another reviewer claims: X. Do you agree? Give evidence (file:line) or concede.")
        self.assertEqual(render.ROUND1_PROMPT,
                         "Do the review the brief describes. End with the JSON block it asks for.")


class FollowUp(unittest.TestCase):
    def test_follow_up(self):
        text = render.follow_up(["f1", "f4", "f7"], "/s d/handoffs/k/panel", "p1a2b3c4", "/s d/v/responses")
        self.assertIn("Re-verify f1, f4, f7 against the code.", text)
        self.assertIn("send an amend request with the verdicts you changed", text)
        self.assertIn('"type": "amend"', text)
        self.assertIn('"panel": "p1a2b3c4"', text)
        self.assertIn("mv '/s d/handoffs/k/panel'/.tmp-", text)

    def test_follow_up_escapes_ids(self):
        self.assertNotIn("\n## x", render.follow_up(["f1\n## x"], "/r", "p1a2b3c4", "/v/responses"))

    def test_follow_up_names_the_absolute_response(self):  # panel g17
        text = render.follow_up(["f1"], "/r", "p1a2b3c4", "/s/inbox-view/k/panels/responses")
        self.assertIn("refusal in `/s/inbox-view/k/panels/responses/<id>.json`", text)
        self.assertIn("bw answers in `/s/inbox-view/k/panels/responses/amend-p1a2b3c4-1.json`", text)
        self.assertNotIn("`responses/", text)


class RequestIds(unittest.TestCase):
    """Final review critical (panel g01): next.md's request ids hold the panel id."""

    def test_ids_hold_the_panel_id(self):
        text = nxt()
        self.assertIn("/s/handoffs/k/panel/.tmp-ask-p1a2b3c4-1", text)
        self.assertIn("/s/handoffs/k/panel/ask-p1a2b3c4-1.request", text)
        self.assertIn("/s/handoffs/k/panel/.tmp-log-p1a2b3c4-1", text)
        self.assertIn("change ask-p1a2b3c4-1 to ask-p1a2b3c4-2", text)
        self.assertIn("use log-p1a2b3c4-2", text)
        self.assertNotIn("ask-1", text.replace("p1a2b3c4-1", ""))
        self.assertNotIn("log-1", text.replace("p1a2b3c4-1", ""))
        self.assertIn("amend-p1a2b3c4-1.request", render.follow_up(["f1"], "/r", "p1a2b3c4", "/v/responses"))

    def test_two_panels_never_share_an_id(self):
        other = dict(copy.deepcopy(DOC), panel_id="p9e8d7c6")
        one, two = nxt(), nxt(other, dict(PANEL, id="p9e8d7c6"))
        ids = lambda t: {w.rsplit("/", 1)[-1] for w in t.split() if w.endswith(".request")}
        self.assertEqual(ids(one), {"ask-p1a2b3c4-1.request", "log-p1a2b3c4-1.request"})
        self.assertFalse(ids(one) & ids(two))
        for rid in ("ask-p1a2b3c4-2", "amend-p1a2b3c4-9"):
            self.assertRegex(rid, "^[A-Za-z0-9_-]{1,64}$")  # channel.REQ_ID accepts them


class Lines(unittest.TestCase):
    def test_target_line_is_escaped_and_cut(self):  # panel g19
        self.assertIn("- Target: t\n", nxt())
        doc = dict(copy.deepcopy(DOC), target="a\n## Fake " + "x" * 400)
        text = nxt(doc)
        line = next(ln for ln in text.splitlines() if ln.startswith("- Target: "))
        self.assertIn("a\\x0a## Fake", line)
        self.assertLessEqual(len(line), len("- Target: ") + render.TARGET_CHARS)

    def test_added_is_a_seat_finding_taken_by_hand(self):  # panel g16
        text = nxt()
        self.assertIn("you took them from the file by hand", text)
        self.assertIn("`seat` must be a seat that returned.", text)
        self.assertNotIn("findings you found yourself", text)

    def test_failed_next_still_says_how_to_log(self):  # panel g15
        text = render.failed_next("p1a2b3c4", "boom\n## x", "/s/handoffs/k/panel", "/s/inbox-view/k/panels/p1a2b3c4")
        self.assertIn("This panel failed: boom\\x0a## x.", text)
        self.assertIn("mv /s/handoffs/k/panel/.tmp-log-p1a2b3c4-1 /s/handoffs/k/panel/log-p1a2b3c4-1.request", text)
        self.assertIn('"verdicts": {}', text)
        self.assertIn("/s/inbox-view/k/panels/responses/log-p1a2b3c4-1.json", text)


if __name__ == "__main__":
    unittest.main()
