import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402
import store  # noqa: E402
import summary  # noqa: E402
from tests.test_runner import RunnerCase  # noqa: E402

FX = HERE / "fixtures"
CONFIG = cfg.load_config(str(FX / "config.json"))


def fd(seat, group, verdict="confirmed", severity="major"):
    return {"seat": seat, "group": group, "severity": severity, "verdict": verdict}


def panel(pid, ts, details, seats=("alpha", "beta"), **over):
    counts = {s: {"total": 0, "confirmed": 0, "refuted": 0, "partial": 0, "unique": 0} for s in seats}
    p = {"type": "panel", "ts": ts, "id": pid, "target": "t" + pid, "kind": "review", "seats": list(seats),
         "findings": counts, "findings_detail": details, "disputes": []}
    p.update(over)
    return p


def load(events):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "m.jsonl")
        for e in events:
            store.append(path, e)
        return store.load(path)


class SeatRows(unittest.TestCase):
    def rows(self, d):
        return {r["seat"]: r for r in summary.seat_rows(d, CONFIG)}

    def test_numbers_match_the_dashboard_fixture(self):
        r = self.rows(store.load(str(FX / "modern.jsonl")))
        a, b = r["alpha"], r["beta"]
        self.assertEqual((a["confirmed"], a["partial"], a["refuted"], a["n"]), (2, 1, 0, 3))
        self.assertEqual((b["confirmed"], b["partial"], b["refuted"], b["n"]), (2, 0, 1, 3))
        self.assertEqual((a["calls"], b["calls"]), (1, 2))
        self.assertEqual((a["challenges"], a["wins"], a["involved"]), (1, 1, 1))
        self.assertEqual((a["unique_recent"], a["recent_panels"]), (1, 2))
        self.assertEqual(r["gamma"]["n"], 0)

    def test_standout_counts_once_per_panel_and_not_for_shared_groups(self):
        d = load([
            panel("p1", "2026-01-01T00:00:00Z", [fd("alpha", "g1"), fd("alpha", "g2", severity="critical"), fd("beta", "g3")]),
            panel("p2", "2026-01-02T00:00:00Z", [fd("alpha", "g1"), fd("beta", "g1")]),           # shared
            panel("p3", "2026-01-03T00:00:00Z", [fd("alpha", "g1", severity="minor"), fd("beta", "g2", "refuted")]),
        ])
        r = self.rows(d)
        self.assertEqual(r["alpha"]["standout_panels"], 1)   # two standout findings in p1, one panel
        self.assertEqual(r["beta"]["standout_panels"], 1)    # p1 g3 only; p3's is refuted

    def test_tokens_and_cost_are_none_until_reported_and_sum_when_reported(self):
        u = {"type": "usage", "seat": "alpha", "mode": "new", "dir": "/w", "duration_s": 1, "ok": True}
        d = load([u, dict(u, tokens_in=10, cost_usd=0.5), dict(u, tokens_in=5)])
        a = self.rows(d)["alpha"]
        self.assertEqual((a["calls"], a["tokens_in"], a["tokens_out"], a["cost_usd"]), (3, 15, None, 0.5))
        self.assertIsNone(self.rows(load([u]))["alpha"]["tokens_in"])
        self.assertIsNone(self.rows(load([]))["alpha"]["cost_usd"])


class Views(unittest.TestCase):
    def test_watches_lists_only_open(self):
        d = load([{"type": "watch", "id": "w1", "kind": "k", "target": "t", "ts": "2026-01-01T00:00:00Z"},
                  {"type": "watch", "id": "w2", "kind": "k", "target": "t", "ts": "2026-01-02T00:00:00Z"},
                  {"type": "watch-update", "watch_id": "w1", "status": "resolved"}])
        self.assertEqual([w["id"] for w in summary.watches(d)], ["w2"])

    def test_timeline_disputes_findings_markers(self):
        d = load([panel("p1", "2026-01-01T00:00:00Z", [fd("alpha", "g1")], seats=("alpha",)),
                  panel("p2", "2026-01-02T00:00:00Z", [], disputes=[{"summary": "s", "winner": "alpha"}]),
                  {"type": "session", "phase": "start", "session_id": "s1", "project": "k", "lead": "la", "runtime": "codex", "model": "m", "ts": "2026-01-01T01:00:00Z"},
                  {"type": "lead", "project": "k", "to": {"lead": "lb"}, "reason": "override", "ts": "2026-01-01T02:00:00Z"},
                  {"type": "session", "phase": "end", "session_id": "s1", "project": "k", "reason": "exit", "ts": "2026-01-01T03:00:00Z"}])
        t = summary.timeline(d)
        self.assertEqual([x["disputes"] for x in t], [0, 1])
        self.assertEqual(summary.disputes(d)[0]["winner"], "alpha")
        self.assertEqual(summary.findings(d)[1]["rows"][0]["group"], "g1")
        m = summary.markers(d)
        self.assertEqual([(x["kind"], x["lead"], x["reason"]) for x in m],
                         [("session-start", "la", None), ("lead", "lb", "override"), ("session-end", "la", "exit")])


class LeadRows(unittest.TestCase):
    def test_counts_sessions_minutes_switches_overrides_ratings_asks(self):
        s = lambda sid, lead, rt, model, ts: {"type": "session", "phase": "start", "session_id": sid, "project": "k",
                                              "lead": lead, "runtime": rt, "model": model, "ts": ts}
        e = lambda sid, reason, ts: {"type": "session", "phase": "end", "session_id": sid, "project": "k", "reason": reason, "ts": ts}
        d = load([s("s1", "la", "codex", "a", "2026-01-01T00:00:00Z"), e("s1", "switch", "2026-01-01T00:30:00Z"),
                  s("s2", "lb", "pi", "b", "2026-01-01T00:30:00Z"), e("s2", "exit", "2026-01-01T01:00:00Z"),
                  s("s3", "la", "codex", "a", "2026-01-02T00:00:00Z"),
                  {"type": "lead", "project": "k", "to": {"lead": "la"}, "reason": "override"},
                  {"type": "lead", "project": "k", "to": {"lead": "la"}, "reason": "cap"},
                  {"type": "rating", "session_id": "s1", "value": "good"}, {"type": "rating", "session_id": "s2", "value": "bad"},
                  {"type": "ask", "id": "a1", "session_id": "s1"}])
        inbox = [{"type": "ask", "id": "a1", "session_id": "s1"}, {"type": "ask", "id": "a2", "session_id": "s1"},
                 {"type": "notice", "id": "n1", "session_id": "s1"}]
        rows = {r["runtime"]: r for r in summary.lead_rows(d, inbox)}
        a, b = rows["codex"], rows["pi"]
        self.assertEqual((a["sessions"], a["minutes"], a["cap_switches"], a["overrides"], a["good"], a["bad"], a["asks"]),
                         (2, 30.0, 1, 1, 1, 0, 2))
        self.assertEqual((b["sessions"], b["minutes"], b["cap_switches"], b["overrides"], b["good"], b["bad"], b["asks"]),
                         (1, 30.0, 0, 0, 0, 1, 0))


class RunnerToSummary(RunnerCase):
    def test_use_override_run_is_counted_by_lead_rows(self):
        p = self.start("--use", "lb", env={"FAKE_MODE_b": "exit"})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        d = store.load(str(self.log))
        (row,) = summary.lead_rows(d, [])
        self.assertEqual((row["runtime"], row["model"], row["sessions"], row["overrides"]), ("pi", "b", 1, 1))


if __name__ == "__main__":
    unittest.main()
