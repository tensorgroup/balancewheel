import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from panel import merge  # noqa: E402
import watches  # noqa: E402  (merge puts metrics/ on sys.path)

FIX = HERE / "fixtures" / "panel" / "outputs"
WATCHES = {"bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3}


def fixture(name):
    return (FIX / name).read_text()


class Parse(unittest.TestCase):
    def test_clean_block(self):
        f = merge.parse(fixture("clean-a.md"))
        self.assertEqual(len(f), 2)
        self.assertEqual(f[0]["severity"], "major")

    def test_empty_list_is_an_answer(self):
        self.assertEqual(merge.parse(fixture("none.md")), [])

    def test_no_block(self):
        self.assertIsNone(merge.parse(fixture("unparsed.md")))

    def test_none_text(self):
        self.assertIsNone(merge.parse(None))

    def test_last_valid_block_wins(self):
        self.assertEqual(merge.parse(fixture("two-blocks.md"))[0]["title"], "second block")

    def test_hostile_block_bounded_and_escaped(self):
        f = merge.parse(fixture("hostile.md"))
        self.assertEqual(len(f), merge.MAX_PER_SEAT)
        self.assertTrue(all("\x1b" not in x["title"] and "\x1b" not in x["claim"]
                            and len(x["title"]) <= merge.TITLE_CHARS for x in f))
        self.assertEqual(len(f[0]["title"]), merge.TITLE_CHARS)
        self.assertIn("minor", {x["severity"] for x in f})  # "severity": 7 -> minor
        self.assertIn("```", f[2]["claim"])

    def test_bad_entries_skipped(self):
        text = '```json\n[1, {"title": ""}, {"claim": "x"}, {"title": "ok", "severity": "MAJOR"}]\n```\n'
        f = merge.parse(text)
        self.assertEqual([(x["title"], x["severity"]) for x in f], [("ok", "major")])

    def test_python_block_before_findings(self):
        text = "```python\nprint(1)\n```\n\n```json\n[{\"title\": \"found\"}]\n```\n"
        self.assertEqual(merge.parse(text)[0]["title"], "found")

    def test_unclosed_other_block_does_not_hide_findings(self):
        text = "```python\nprint(1)\n\n```json\n[{\"title\": \"found\"}]\n```\n"
        self.assertEqual(merge.parse(text)[0]["title"], "found")

    def test_python_block_between_two_valid_blocks(self):
        b = lambda t: "```json\n[{\"title\": \"%s\"}]\n```\n" % t
        text = b("first") + "```python\nx = 1\n```\n" + b("second")
        self.assertEqual(merge.parse(text)[0]["title"], "second")

    def test_losses_counted(self):
        e, lost = merge.parse_detail(fixture("hostile.md"))
        self.assertEqual(len(e), merge.MAX_PER_SEAT)
        self.assertEqual(lost, {"skipped_blocks": 2, "skipped_entries": 0, "truncated": 10})
        text = "```json\n[1, {\"title\": \"ok\"}]\n```\n```json\nnot json\n```\n"
        self.assertEqual(merge.parse_detail(text)[1], {"skipped_blocks": 1, "skipped_entries": 1, "truncated": 0})
        self.assertEqual(merge.parse_detail(fixture("clean-a.md"))[1],
                         {"skipped_blocks": 0, "skipped_entries": 0, "truncated": 0})

    def test_deep_nesting_is_not_fatal(self):
        text = "```json\n" + "[" * 100000 + "\n```\n"
        self.assertIsNone(merge.parse(text))


class Numbering(unittest.TestCase):
    def test_ids_and_unparsed(self):
        f = merge.number({"a": fixture("clean-a.md"), "b": fixture("unparsed.md")}, ["a", "b"])
        self.assertEqual([x["id"] for x in f], ["f1", "f2", "f3"])
        self.assertEqual((f[2]["seat"], f[2]["title"], f[2]["severity"]), ("b", merge.UNPARSED_TITLE, "minor"))
        self.assertEqual(f[2]["claim"], "bw found no JSON block; read seats/b.md")

    def test_order_follows_the_order_argument(self):
        f = merge.number({"a": fixture("clean-a.md"), "b": fixture("clean-b.md")}, ["b", "a"])
        self.assertEqual([x["seat"] for x in f], ["b", "b", "a", "a"])

    def test_seat_without_output_is_unparsed(self):
        f = merge.number({"a": None}, ["a"])
        self.assertEqual(f[0]["title"], merge.UNPARSED_TITLE)


class Grouping(unittest.TestCase):
    def F(self, i, seat, where, title, sev="major"):
        return {"id": i, "seat": seat, "where": where, "title": title, "severity": sev, "claim": ""}

    def ids(self, g):
        return [[m["id"] for m in x["members"]] for x in g]

    def test_lines_within_five(self):
        g = merge.group([self.F("f1", "a", "m.py:40", "x one"), self.F("f2", "b", "m.py:45", "y two"),
                         self.F("f3", "b", "m.py:46", "z three"), self.F("f4", "a", "m.py:52", "w four")])
        # 40~45 (5 apart) and 45~46 join transitively; 52 is 6 from 46: its own group
        self.assertEqual(self.ids(g), [["f1", "f2", "f3"], ["f4"]])
        self.assertEqual([x["group"] for x in g], ["g1", "g2"])
        self.assertEqual(g[0]["title"], "x one")

    def test_title_jaccard(self):
        g = merge.group([self.F("f1", "a", "", "limit applies after auth"),
                         self.F("f2", "b", "", "the limit applies after auth check")])
        self.assertEqual(len(g), 1)

    def test_transitive(self):
        g = merge.group([self.F("f1", "a", "m.py:10", "alpha beta gamma"),
                         self.F("f2", "b", "m.py:12", "delta epsilon zeta"),
                         self.F("f3", "c", "z.py:99", "delta epsilon zeta")])
        self.assertEqual(self.ids(g), [["f1", "f2", "f3"]])

    def test_other_file_same_line_apart(self):
        g = merge.group([self.F("f1", "a", "a.py:10", "one thing"), self.F("f2", "b", "b.py:10", "other matter")])
        self.assertEqual(len(g), 2)

    def test_where_without_line_does_not_join_by_lines(self):
        g = merge.group([self.F("f1", "a", "m.py", "one thing"), self.F("f2", "b", "m.py", "other matter")])
        self.assertEqual(len(g), 2)

    def test_empty_title_words_never_join(self):  # build ruling, 2026-11-23
        g = merge.group([self.F("f1", "a", "", "t"), self.F("f2", "b", "", "u")])
        self.assertEqual(len(g), 2)
        g = merge.group([self.F("f1", "a", "", "t"), self.F("f2", "b", "", "t")])
        self.assertEqual(len(g), 2)

    def test_group_ids_follow_first_member(self):
        g = merge.group([self.F("f1", "a", "a.py:1", "aaa"), self.F("f2", "a", "b.py:1", "bbb"),
                         self.F("f3", "b", "a.py:2", "ccc")])
        self.assertEqual(self.ids(g), [["f1", "f3"], ["f2"]])

    def test_disputes_need_two_levels(self):
        g = merge.group([self.F("f1", "a", "m.py:1", "t", "critical"), self.F("f2", "b", "m.py:2", "u", "minor"),
                         self.F("f3", "a", "n.py:1", "v", "major"), self.F("f4", "b", "n.py:2", "w", "minor")])
        self.assertEqual(merge.candidate_disputes(g),
                         [{"id": "d1", "group": "g1", "reason": "severity critical vs minor"}])

    def test_one_seat_is_not_a_dispute(self):
        g = merge.group([self.F("f1", "a", "m.py:1", "t", "critical"), self.F("f2", "a", "m.py:2", "u", "minor")])
        self.assertEqual(len(g), 1)
        self.assertEqual(merge.candidate_disputes(g), [])

    def test_fixtures_group_and_dispute(self):
        f = merge.number({"a": fixture("clean-a.md"), "b": fixture("clean-b.md")}, ["a", "b"])
        g = merge.group(f)  # widget.py:40 and widget.py:43 are 3 apart
        self.assertEqual(self.ids(g)[0], ["f1", "f3"])
        self.assertEqual(merge.candidate_disputes(g), [])  # critical vs major is one level


class Build(unittest.TestCase):
    def test_bloat_flag(self):
        self.assertTrue(merge.triage_required(35, 2, WATCHES))
        self.assertFalse(merge.triage_required(10, 2, WATCHES))

    def test_bloat_is_the_watch_predicate(self):  # build ruling, 2026-11-23
        with mock.patch("watches.is_bloated", return_value=True) as b:
            self.assertTrue(merge.triage_required(1, 1, {}))
        b.assert_called_once_with(1, 1, {})

    def test_document_shape(self):
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {"a": fixture("clean-a.md")},
                          {"a": {"model": "model-a", "status": "ok", "on_roster": True},
                           "b": {"status": "absent", "reason": "usage cap", "on_roster": True}},
                          {"b": "usage cap"}, True, ["required seat b absent: usage cap"], WATCHES)
        self.assertEqual(set(doc), {"panel_id", "checkpoint", "target", "tier", "short", "short_reasons", "failed",
                                    "seats", "absent", "groups", "candidate_disputes", "findings_raw",
                                    "triage_required"})
        self.assertEqual(doc["findings_raw"], 2)
        self.assertIsNone(doc["failed"])
        self.assertFalse(doc["triage_required"])

    def test_lost_findings_recorded_per_seat(self):
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {"a": fixture("hostile.md"), "b": fixture("clean-b.md")},
                          {"a": {"status": "ok", "on_roster": True}, "b": {"status": "ok", "on_roster": True}},
                          {}, False, [], WATCHES)
        self.assertEqual(doc["seats"]["a"]["lost"], {"skipped_blocks": 2, "truncated": 10})
        self.assertNotIn("lost", doc["seats"]["b"])

    def test_unexpected_output_is_merged_and_marked(self):
        seats = {"a": {"status": "ok", "on_roster": True}}
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {"a": fixture("clean-a.md"), "z": fixture("clean-b.md")}, seats, {}, False, [], WATCHES)
        self.assertEqual(doc["findings_raw"], 4)
        self.assertTrue(doc["seats"]["z"]["unexpected_output"])
        self.assertNotIn("z", seats)  # the caller's dict is not changed

    def test_failed_reason_carried(self):
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {}, {}, {}, False, [], WATCHES, failed="worker died")
        self.assertEqual(doc["failed"], "worker died")

    def test_no_seat_ran(self):
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {}, {"a": {"status": "absent", "reason": "x", "on_roster": True}}, {"a": "x"},
                          True, [], WATCHES)
        self.assertEqual((doc["groups"], doc["findings_raw"], doc["triage_required"]), ([], 0, False))

    def test_bloat_fixture_triggers_triage(self):
        doc = merge.build({"panel_id": "p1", "checkpoint": "design", "target": "t", "tier": "standard"},
                          {"a": fixture("bloat.md")}, {"a": {"status": "ok", "on_roster": True}}, {}, False, [],
                          WATCHES)
        self.assertEqual(doc["findings_raw"], 35)
        self.assertTrue(doc["triage_required"])


if __name__ == "__main__":
    unittest.main()
