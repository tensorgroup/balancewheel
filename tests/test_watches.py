import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import watches  # noqa: E402

W = {"bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
     "skim_min_seats": 3, "skim_min_findings": 15,
     "redundant_min_panels": 5, "redundant_window_panels": 10}
CFG = {"watches": W, "seats": {"a": {}, "b": {}, "c": {}}}


def panel(pid, seats, counts, raw=None, triaged=None):
    return {"id": pid, "ts": f"2026-01-{int(pid[1:]):02d}T00:00:00Z", "target": f"t{pid}",
            "seats": {s: None for s in seats},
            "findings": {s: dict(total=t, confirmed=c, refuted=r, partial=p, unique=u)
                         for s, (t, c, r, p, u) in counts.items()},
            "findings_raw": raw, "findings_after_triage": triaged}


class Bloat(unittest.TestCase):
    def test_fires_on_total_and_marks_performed_when_triaged(self):
        p = panel("p1", "abc", {"a": (12, 12, 0, 0, 1), "b": (10, 10, 0, 0, 1), "c": (10, 10, 0, 0, 1)}, raw=40, triaged=20)
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "resolved")
        self.assertIn("performed", out[0]["detail"])

    def test_fires_on_per_seat_average_with_min_seats_and_suggests_when_not_triaged(self):
        p = panel("p1", "abc", {"a": (9, 9, 0, 0, 1), "b": (8, 8, 0, 0, 1), "c": (8, 8, 0, 0, 1)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(out[0]["status"], "open")
        p2 = panel("p2", "ab", {"a": (9, 9, 0, 0, 1), "b": (9, 9, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p2], CFG) if w["kind"] == "panel-bloat"], [])

    def test_below_threshold_is_silent(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"], [])


class Skim(unittest.TestCase):
    def test_fires_when_nothing_refuted_or_partial(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "open")

    def test_partial_counts_as_verification(self):
        p = panel("p1", "abc", {"a": (5, 4, 0, 1, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"], [])

    def test_legacy_none_partial_treated_as_zero(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, None, None), "b": (5, 5, 0, None, None), "c": (5, 5, 0, None, None)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"]
        self.assertEqual(len(out), 1)


class Redundant(unittest.TestCase):
    def test_fires_after_min_panels_with_zero_unique(self):
        ps = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, 0), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        out = [w for w in watches.evaluate(ps, CFG) if w["kind"] == "redundant-seat"]
        self.assertEqual([w["target"] for w in out], ["a"])
        self.assertEqual(out[0]["panel_id"], "p5")

    def test_null_unique_panels_are_excluded_not_counted_as_zero(self):
        ps = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, None), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        self.assertEqual([w for w in watches.evaluate(ps, CFG) if w["kind"] == "redundant-seat"], [])

    def test_window_limits_lookback(self):
        old = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, 0), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        new = [panel(f"p{i}", "bc", {"b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(6, 17)]
        out = [w for w in watches.evaluate(old + new, CFG) if w["kind"] == "redundant-seat"]
        self.assertEqual(out, [])  # a's zero-unique panels fell outside the 10-panel window


class Ids(unittest.TestCase):
    def test_ids_are_deterministic_across_runs(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        a = watches.evaluate([p], CFG); b = watches.evaluate([p], CFG)
        self.assertEqual([w["id"] for w in a], [w["id"] for w in b])


class Edges(unittest.TestCase):
    def test_zero_min_panels_does_not_crash_and_never_fires(self):
        cfg = {"watches": dict(W, redundant_min_panels=0), "seats": {"a": {}}}
        self.assertEqual(watches.evaluate([], cfg), [])
        p = panel("p1", "a", {"a": (3, 3, 0, 0, 0)})
        self.assertEqual([w for w in watches.evaluate([p], cfg) if w["kind"] == "redundant-seat"], [])

    def test_seat_count_uses_declared_seats_when_counts_are_missing(self):
        p = panel("p1", "abc", {"a": (20, 20, 0, 0, 1)})  # b and c sat but have no counts (legacy)
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(out, [])  # 20 raw / 3 seats < 8 per seat; would have fired at 20/1


if __name__ == "__main__":
    unittest.main()
