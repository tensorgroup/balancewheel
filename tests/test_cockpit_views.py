import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(HERE))
from cockpit import views  # noqa: E402
from lead.proc import Result  # noqa: E402
from test_config_v2 import load_obj  # noqa: E402

NOW = 1_800_000_000.0
FIX = json.loads((ROOT / "tests" / "fixtures" / "config_v2.json").read_text())


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def no_run(argv, timeout, **kw):
    return Result(127, "", "not found", False)


class ViewsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.state = str(self.root / "state")
        os.makedirs(self.state)
        self.log = self.root / "metrics.jsonl"
        self.env = {k: os.environ.get(k) for k in ("BALANCEWHEEL_METRICS_LOG", "BW_ORCA_SUPPORT", "BW_CCSWITCH_DIR")}
        os.environ["BALANCEWHEEL_METRICS_LOG"] = str(self.log)
        os.environ["BW_ORCA_SUPPORT"] = str(self.root / "orca")
        os.environ["BW_CCSWITCH_DIR"] = str(self.root / "cc-switch")
        obj = json.loads(json.dumps(FIX))
        obj["plans"]["orca-plan"] = {"vendor": "vendor-c", "billing": "plan", "quota": "orca", "orca_provider": "codex"}
        obj["state_dir"] = self.state
        self.config = load_obj(obj)

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def built(self):
        return views.build(self.config, self.state, now_fn=lambda: NOW, run=no_run)

    def all_json(self, b):
        out = {}
        for name in ("models", "usage", "history", "now", "graph"):
            out[name] = json.loads(json.dumps(b[name](), allow_nan=False))
        return out

    def write_log(self, events):
        self.log.write_text("".join(json.dumps(e) + "\n" for e in events))

    def synthetic(self):
        t = iso(NOW - 3600)
        self.write_log([
            {"type": "usage", "ts": t, "seat": "alpha", "model": "vendor/model-b", "mode": "new", "dir": "/w",
             "duration_s": 3, "ok": True, "tokens_in": 100, "tokens_out": 20, "cost_usd": 0.5},
            {"type": "usage", "ts": t, "seat": "beta", "model": "router/vendor/model-c", "mode": "new", "dir": "/w",
             "duration_s": 3, "ok": True},
            {"type": "panel", "id": "p1", "ts": t, "target": "plan x", "kind": "plan",
             "seats": {"alpha": "vendor/model-b", "beta": "router/vendor/model-c"},
             "findings": {"alpha": {"total": 2, "confirmed": 1, "partial": 1, "refuted": 0, "unique": 1}},
             "disputes": [{"summary": "d", "challenger": "beta", "winner": "alpha"}]},
            {"type": "watch", "id": "w1", "ts": t, "kind": "panel-bloat", "target": "plan x", "status": "open"},
            {"type": "watch", "id": "w2", "ts": t, "kind": "verification-skim", "target": "plan y", "status": "resolved"},
            {"type": "session", "phase": "start", "session_id": "s1", "ts": iso(NOW - 7200), "project": "k1",
             "worktree": "/w", "lead": "lead-a", "runtime": "claude-code", "model": "vendor/model-a",
             "resolved": True, "plan": "plan-a"},
            {"type": "session", "phase": "end", "session_id": "s1", "ts": iso(NOW - 3600), "project": "k1",
             "reason": "exit"},
        ])

    def test_every_view_serialises_on_an_empty_state(self):
        out = self.all_json(self.built())
        self.assertEqual(out["models"]["leads"], [])
        self.assertEqual(out["models"]["small_n"], 10)
        self.assertIn("throughput per task", out["models"]["lead_columns_pending"])
        self.assertEqual(out["history"]["timeline"], [])
        self.assertEqual(out["now"]["watches"], [])
        self.assertEqual(out["graph"], {"available": False, "reason": "not built yet"})

    def test_every_view_serialises_on_a_synthetic_state(self):
        self.synthetic()
        out = self.all_json(self.built())
        self.assertEqual({s["seat"] for s in out["models"]["seats"]} >= {"alpha", "beta"}, True)
        self.assertEqual(out["models"]["leads"][0]["sessions"], 1)
        self.assertEqual(len(out["history"]["timeline"]), 1)
        self.assertEqual(len(out["history"]["disputes"]), 1)
        self.assertEqual(len(out["history"]["findings"]), 1)
        self.assertEqual([m["kind"] for m in out["history"]["markers"]], ["session-start", "session-end"])

    def test_a_plan_with_an_orca_reading_shows_source_orca_and_its_age(self):
        qd = Path(self.state) / "quota"
        qd.mkdir()
        (qd / "orca-plan.orca.json").write_text(json.dumps(
            {"source": "orca", "observed_at": NOW - 120,
             "windows": [{"name": "five_hour", "used_pct": 40, "resets_at": NOW + 3600}]}))
        plan = next(p for p in self.built()["now"]()["plans"] if p["name"] == "orca-plan")
        self.assertEqual(plan["source"], "orca")
        self.assertEqual(plan["age_s"], 120)
        self.assertEqual(plan["windows"][0]["used_pct"], 40)

    def test_usage_without_cc_switch_is_unavailable_with_the_reason_and_seat_rows_stay(self):
        self.synthetic()
        u = self.built()["usage"]()
        self.assertFalse(u["ccswitch"]["available"])
        self.assertIn("cc-switch not installed", u["ccswitch"]["reason"])
        seats = {r["seat"]: r for r in u["seats"]}
        self.assertEqual(seats["alpha"]["calls"], 1)
        self.assertEqual(seats["alpha"]["tokens_in"], 100)
        self.assertEqual(seats["alpha"]["cost_usd"], 0.5)

    def test_a_seat_cost_never_reported_is_null_not_zero(self):
        self.synthetic()
        beta = next(r for r in self.built()["usage"]()["seats"] if r["seat"] == "beta")
        self.assertIsNone(beta["cost_usd"])
        self.assertIsNone(beta["tokens_in"])
        self.assertIsNone(beta["tokens_out"])
        model_beta = next(r for r in self.built()["models"]()["seats"] if r["seat"] == "beta")
        self.assertIsNone(model_beta["cost_usd"])

    def test_now_lists_open_watches_only(self):
        self.synthetic()
        self.assertEqual([w["id"] for w in self.built()["now"]()["watches"]], ["w1"])

    def test_now_metered_plan_spend_from_the_probe_snapshot_else_unknown(self):
        plan = next(p for p in self.built()["now"]()["plans"] if p["name"] == "metered")
        self.assertTrue(plan["spend"]["unknown"])
        self.assertEqual(plan["spend"]["budget_usd_month"], 20)
        qd = Path(self.state) / "quota"
        qd.mkdir()
        (qd / "metered.probe.json").write_text(json.dumps(
            {"source": "probe", "observed_at": NOW - 60, "spend_usd": 4.5, "budget_usd_month": 20,
             "windows": [{"name": "budget", "used_pct": 22.5, "resets_at": NOW + 86400}]}))
        plan = next(p for p in self.built()["now"]()["plans"] if p["name"] == "metered")
        self.assertEqual((plan["spend"]["spend_usd"], plan["spend"]["budget_usd_month"]), (4.5, 20))
        self.assertNotIn("spend", next(p for p in self.built()["now"]()["plans"] if p["name"] == "plan-a"))

    def test_now_worktrees_carry_current_and_pinned_lead_and_open_inbox_counts(self):
        locks = Path(self.state) / "locks"
        locks.mkdir()
        (locks / "k1.json").write_text(json.dumps({"lead": "lead-a", "worktree": "/w", "started": NOW - 60}))
        (Path(self.state) / "leads.json").write_text(json.dumps({"k1": "lead-b"}))
        (Path(self.state) / "inbox.jsonl").write_text(
            json.dumps({"type": "ask", "id": "a1", "project": "k1", "worktree": "/w", "text": "q?", "ts": NOW}) + "\n"
            + json.dumps({"type": "notice", "id": "n1", "project": "k1", "kind": "inbox", "text": "x", "ts": NOW}) + "\n")
        w = self.built()["now"]()["worktrees"]
        self.assertEqual(w, [{"worktree": "/w", "lead": "lead-a", "since": NOW - 60, "pinned": "lead-b",
                              "asks": 1, "notices": 1}])

    def test_capped_plan_shows_cap_until(self):
        caps = Path(self.state) / "caps"
        caps.mkdir()
        (caps / "plan-a.json").write_text(json.dumps({"resets_at": NOW + 600, "window": "unknown"}))
        plan = next(p for p in self.built()["now"]()["plans"] if p["name"] == "plan-a")
        self.assertEqual(plan["cap_until"], NOW + 600)


if __name__ == "__main__":
    unittest.main()
