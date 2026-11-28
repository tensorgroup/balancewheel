"""lead/runs.py: the run row builders, ids, and labels."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import runs  # noqa: E402

END_KEYS = {"type", "phase", "run_id", "t", "seat", "dir", "ok", "reason", "duration_s", "tokens_in", "tokens_out",
            "cost_usd", "ctx_peak"}


class Runs(unittest.TestCase):
    def test_new_run_id_matches_and_differs(self):
        a, b = runs.new_run_id(), runs.new_run_id()
        self.assertTrue(runs.RUN_ID.fullmatch(a), a)
        self.assertNotEqual(a, b)

    def test_start_row_omits_none_optionals(self):
        s = runs.start_row("astra", "model-a", "/w", "review x", "bw", t=5.0)
        self.assertEqual(set(s), {"type", "phase", "run_id", "t", "seat", "model", "dir", "label", "source"})
        self.assertEqual((s["type"], s["phase"], s["t"], s["seat"], s["model"], s["dir"], s["label"], s["source"]),
                         ("run", "start", 5.0, "astra", "model-a", "/w", "review x", "bw"))
        self.assertTrue(runs.RUN_ID.fullmatch(s["run_id"]))
        full = runs.start_row("gem", "model-b", "/w", "l", "bw", pid=42, host="h", panel="p0123abc",
                              session_id="0123456789ab", stand_in_for="astra")
        self.assertEqual((full["pid"], full["host"], full["panel"], full["session_id"], full["stand_in_for"]),
                         (42, "h", "p0123abc", "0123456789ab", "astra"))
        self.assertIsInstance(full["t"], float)

    def test_end_row_keeps_every_spec_key(self):
        e = runs.end_row("r0123456789ab", "astra", "/w", False, "timeout", 1.5, t=9.0)
        self.assertEqual(set(e), END_KEYS)
        self.assertEqual((e["type"], e["phase"], e["run_id"], e["ok"], e["reason"], e["duration_s"], e["t"]),
                         ("run", "end", "r0123456789ab", False, "timeout", 1.5, 9.0))
        self.assertEqual((e["tokens_in"], e["tokens_out"], e["cost_usd"], e["ctx_peak"]), (None, None, None, None))
        e = runs.end_row("r0123456789ab", "astra", "/w", True, None, 2.0, tokens_in=1, tokens_out=2, cost_usd=0.5,
                         ctx_peak=100)
        self.assertEqual((e["tokens_in"], e["tokens_out"], e["cost_usd"], e["ctx_peak"], e["reason"]),
                         (1, 2, 0.5, 100, None))

    def test_label_first_line_and_length(self):
        self.assertEqual(runs.label("a\nb" + "x" * 200), "a")
        self.assertEqual(len(runs.label("y" * 200)), runs.LABEL_CHARS)
        self.assertEqual(runs.label(""), "")

    def test_label_holds_no_control_character(self):
        lab = runs.label("\x1b[2J" + "x")
        self.assertNotIn("\x1b", lab)
        self.assertTrue(lab.endswith("x"))

    def test_label_is_redacted(self):
        self.assertNotIn("abc123", runs.label("API_KEY=abc123 review"))

    def test_patterns(self):
        self.assertTrue(runs.LEAD_SESSION.fullmatch("0123456789ab"))
        self.assertFalse(runs.LEAD_SESSION.fullmatch("zz"))
        self.assertEqual(runs.END_LOOKBACK_S, 7 * 86400)


if __name__ == "__main__":
    unittest.main()
