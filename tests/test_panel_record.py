import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from panel import merge, record  # noqa: E402
import log  # noqa: E402  (merge puts metrics/ on sys.path)

CFG = {"seats": {"a": {"model": "model-a", "roles": ["design"]}, "b": {"model": "model-b", "roles": ["design"]},
                 "c": {"model": "model-c", "roles": ["design"]}}}


def member(fid, seat, sev, where, title):
    return {"id": fid, "seat": seat, "severity": sev, "where": where, "title": title, "claim": "c"}


def make_doc(**kw):
    doc = {"panel_id": "p3f9a1c2", "checkpoint": "design", "target": "the plan", "tier": "standard",
           "short": True, "short_reasons": [], "failed": None,
           "seats": {"a": {"model": "model-a", "status": "ok", "on_roster": True},
                     "b": {"model": "model-b", "status": "ok", "on_roster": True, "stand_in_for": "c"},
                     "c": {"status": "absent", "reason": "usage cap", "on_roster": True}},
           "absent": {"c": "usage cap"},
           "groups": [{"group": "g1", "title": "limit", "members": [
                           member("f1", "a", "major", "m.py:40", "limit"), member("f2", "b", "minor", "m.py:42", "limit")]},
                      {"group": "g2", "title": "other", "members": [member("f3", "a", "minor", "z.py:1", "other")]}],
           "candidate_disputes": [], "findings_raw": 3, "triage_required": False}
    doc.update(kw)
    return doc


DOC = make_doc()
ALL_OK = {"verdicts": {"f1": "confirmed", "f2": "confirmed", "f3": "confirmed"}}
ADDED = {"seat": "a", "title": "t", "severity": "minor", "verdict": "confirmed"}
DISPUTE = {"summary": "s", "challenger": "a", "proposals": {"a": "x", "b": "y"}, "winner": "b", "reason": "r"}
ALL_ABSENT_DOC = make_doc(seats={"a": {"status": "absent", "reason": "usage cap", "on_roster": True},
                                 "b": {"status": "absent", "reason": "timeout", "on_roster": True}},
                          absent={"a": "usage cap", "b": "timeout"}, groups=[], findings_raw=0)


class Build(unittest.TestCase):
    def test_counts_match_compute_counts_and_validate(self):
        rec, ignored = record.build(DOC, {"verdicts": {"f1": "confirmed", "f2": "confirmed", "f3": "refuted"}}, CFG)
        self.assertEqual(rec["findings"], log.compute_counts(rec["findings_detail"]))
        log.validate_panel(rec, CFG, force=False, panel_id=DOC["panel_id"])
        self.assertEqual((rec["seats"], rec["absent"], ignored), ({"a": "model-a", "b": "model-b"}, {"c": "usage cap"}, []))

    def test_missing_verdict_listed(self):
        with self.assertRaisesRegex(ValueError, r"missing verdicts: f2, f3"):
            record.build(DOC, {"verdicts": {"f1": "confirmed"}}, CFG)

    def test_unknown_ids_and_bad_values(self):
        for bad in ({"verdicts": {**ALL_OK["verdicts"], "f9": "confirmed"}},
                    {**ALL_OK, "groups": {"f9": "g1"}}, {**ALL_OK, "severity": {"f9": "minor"}},
                    {"verdicts": {**ALL_OK["verdicts"], "f1": "great"}}, {**ALL_OK, "severity": {"f1": "huge"}},
                    {**ALL_OK, "groups": {"f1": "bad id!"}}):
            self.assertRaises(ValueError, record.build, DOC, bad, CFG)
        with self.assertRaisesRegex(ValueError, "unknown finding ids: f9"):
            record.build(DOC, {**ALL_OK, "groups": {"f9": "g1"}}, CFG)

    def test_mechanical_fields_from_lead_ignored(self):
        rec, ignored = record.build(DOC, {**ALL_OK, "findings": {"a": {"total": 99}}, "target": "x", "seats": ["z"]}, CFG)
        self.assertEqual(rec["target"], DOC["target"])
        self.assertEqual(sorted(ignored), ["findings", "seats", "target"])

    def test_regroup_and_severity(self):
        rec, _ = record.build(DOC, {**ALL_OK, "groups": {"f3": "g1"}, "severity": {"f2": "critical"}}, CFG)
        d = {x["id"]: x for x in rec["findings_detail"]}
        self.assertEqual((d["f3"]["group"], d["f2"]["severity"]), ("g1", "critical"))
        self.assertEqual(rec["findings"]["a"]["unique"], 0)

    def test_added_needs_a_seat_that_ran(self):
        rec, _ = record.build(DOC, {**ALL_OK, "added": [ADDED]}, CFG)
        d = {x["id"]: x for x in rec["findings_detail"]}
        self.assertEqual(d["a1"]["group"], "ga1")
        self.assertRaises(ValueError, record.build, DOC, {**ALL_OK, "added": [{**ADDED, "seat": "c"}]}, CFG)
        with self.assertRaisesRegex(ValueError, r"added\[0\] missing title"):
            record.build(DOC, {**ALL_OK, "added": [{"seat": "a"}]}, CFG)
        rec, _ = record.build(DOC, {**ALL_OK, "added": [{**ADDED, "group": "g2"}]}, CFG)
        self.assertEqual(rec["findings_detail"][-1]["group"], "g2")

    def test_triage_count_only_when_required(self):
        rec, _ = record.build(DOC, {"verdicts": {"f1": "dropped", "f2": "confirmed", "f3": "confirmed"}}, CFG)
        self.assertNotIn("findings_after_triage", rec)
        doc = make_doc(triage_required=True)
        rec, _ = record.build(doc, {"verdicts": {"f1": "dropped", "f2": "confirmed", "f3": "confirmed"}}, CFG)
        self.assertEqual((rec["findings_raw"], rec["findings_after_triage"]), (3, 2))

    def test_rounds_and_agreement(self):
        self.assertTrue(record.build(DOC, ALL_OK, CFG)[0]["immediate_agreement"])
        self.assertFalse(record.build(DOC, ALL_OK, CFG, rounds=2)[0]["immediate_agreement"])
        rec, _ = record.build(DOC, {**ALL_OK, "disputes": [DISPUTE]}, CFG)
        self.assertFalse(rec["immediate_agreement"])
        log.validate_panel(rec, CFG, force=False)

    def test_dispute_seats_checked(self):
        for bad in ({**DISPUTE, "winner": "c"}, {**DISPUTE, "challenger": "c"}, {**DISPUTE, "proposals": {"c": "x"}}):
            self.assertRaises(ValueError, record.build, DOC, {**ALL_OK, "disputes": [bad]}, CFG)
        with self.assertRaisesRegex(ValueError, r"disputes\[0\] missing winner"):
            record.build(DOC, {**ALL_OK, "disputes": [{"summary": "s", "challenger": "a", "reason": "r", "proposals": {}}]}, CFG)

    def test_moderator(self):
        mod = {"lead": "x", "runtime": "y", "model": "z", "resolved": True}
        self.assertEqual(record.build(DOC, ALL_OK, CFG, moderator=mod)[0]["moderator"], mod)

    def test_all_absent(self):
        rec, _ = record.build(ALL_ABSENT_DOC, {"verdicts": {}}, CFG)
        log.validate_panel(rec, CFG, force=False)
        self.assertEqual(record.summary_line(rec, ALL_ABSENT_DOC),
                         "Design panel, short (no seat returned; a absent: usage cap, b absent: timeout): "
                         "0 findings, 0 confirmed, 0 refuted, 0 dropped.")

    def test_shape_faults_are_value_errors(self):
        for bad in ({"groups": None}, {"added": "x"}, {"disputes": [{}]}, {"severity": {"f1": 3}}, {"notes": 5},
                    {"disputes": [DISPUTE, 4]}, {"added": [{**ADDED, "title": 3}]},
                    {"disputes": [{**DISPUTE, "proposals": []}]}, {"verdicts": []}):
            self.assertRaises(ValueError, record.build, DOC, {**ALL_OK, **bad}, CFG)
        self.assertRaises(ValueError, record.build, DOC, [], CFG)

    def test_lead_text_escaped_and_cut(self):
        added = {**ADDED, "title": "t" * 900, "where": "\x1b[2J" + "w" * 900}
        d = {**DISPUTE, "summary": "s" * 900, "reason": "\x1b" + "r" * 900, "proposals": {"a": "p" * 3000}}
        rec, _ = record.build(DOC, {**ALL_OK, "notes": "\x1b[2J" + "n" * 5000, "added": [added], "disputes": [d]}, CFG)
        self.assertNotIn("\x1b", rec["notes"])
        self.assertLessEqual(len(rec["notes"]), 2000)
        a1 = rec["findings_detail"][-1]
        self.assertLessEqual(len(a1["title"]), merge.TITLE_CHARS)
        self.assertLessEqual(len(a1["where"]), merge.WHERE_CHARS)
        self.assertNotIn("\x1b", a1["where"])
        dd = rec["disputes"][0]
        self.assertEqual((len(dd["summary"]), len(dd["reason"]), len(dd["proposals"]["a"])), (500, 500, 1500))
        self.assertNotIn("\x1b", dd["reason"])
        rec, _ = record.build(make_doc(target="\x1b[2Jx"), ALL_OK, CFG)
        self.assertNotIn("\x1b", rec["target"])


class Summary(unittest.TestCase):
    def test_summary_line(self):
        rec, _ = record.build(DOC, {"verdicts": {"f1": "confirmed", "f2": "partial", "f3": "dropped"}}, CFG)
        self.assertEqual(record.summary_line(rec, DOC),
                         "Design panel, short (a, b; c absent: usage cap): 3 findings, 1 confirmed, 1 partial, "
                         "0 refuted, 1 dropped.")

    def test_failed_summary(self):
        doc = make_doc(failed="stopped after 37 min")
        rec, _ = record.build(doc, ALL_OK, CFG)
        self.assertTrue(record.summary_line(rec, doc).startswith("Design panel, failed: stopped after 37 min (a, b; "))

    def test_deep_standin_off_roster_and_not_short(self):
        doc = make_doc(tier="deep", short=False)
        doc["seats"]["g"] = {"model": "model-g", "status": "ok", "on_roster": False, "stand_in_for": "c"}
        doc["seats"]["b"].pop("stand_in_for")
        rec, _ = record.build(doc, ALL_OK, CFG)
        self.assertTrue(record.summary_line(rec, doc).startswith("Deep Design panel (a, b, g for c; c absent: usage cap): 3 findings"))


class Judged(unittest.TestCase):
    def test_judged_keeps_request_keys_only(self):
        self.assertEqual(record.judged({**ALL_OK, "target": "x"}), ALL_OK)

    def test_merge_patch_keeps_earlier_judgement(self):
        old = {"verdicts": {"f1": "confirmed", "f2": "confirmed", "f3": "refuted"}, "groups": {"f3": "g1"},
               "added": [ADDED], "disputes": [DISPUTE], "notes": "n"}
        new = record.merge_patch(old, {"verdicts": {"f2": "refuted"}, "disputes": [], "target": "x"})
        self.assertEqual((new["groups"], new["added"], new["disputes"], new["verdicts"]["f2"]),
                         ({"f3": "g1"}, [ADDED], [DISPUTE], "refuted"))
        self.assertNotIn("target", new)
        self.assertEqual(old["verdicts"]["f2"], "confirmed")
        self.assertEqual(record.merge_patch(new, {"notes": "m"})["verdicts"]["f2"], "refuted")
        self.assertEqual(record.merge_patch(new, {"added": []})["added"], [])


if __name__ == "__main__":
    unittest.main()
