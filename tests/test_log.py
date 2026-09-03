import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import log as logmod  # noqa: E402
import store  # noqa: E402

LOG_PY = str(HERE.parent / "metrics" / "log.py")
CONFIG = str(HERE / "fixtures" / "config.json")


def run(args, stdin=None, env=None):
    e = dict(os.environ, BALANCEWHEEL_CONFIG=CONFIG, **(env or {}))
    return subprocess.run([sys.executable, LOG_PY, *args], input=stdin, capture_output=True, text=True, env=e)


def detail(seat, group, verdict="confirmed", severity="major"):
    return {"seat": seat, "group": group, "title": f"{group} title", "claim": "c", "ref": "f:1",
            "severity": severity, "verdict": verdict, "evidence": "e", "action": "a"}


def record(**over):
    rec = {"target": "change X", "kind": "review", "seats": ["alpha", "beta"], "rounds": 1,
           "immediate_agreement": True,
           "findings": {"alpha": {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1},
                        "beta": {"total": 1, "confirmed": 0, "refuted": 1, "partial": 0, "unique": 0}},
           "findings_detail": [detail("alpha", "g1"), detail("alpha", "g2"), detail("beta", "g2", "refuted")],
           "disputes": []}
    rec.update(over)
    return rec


class ComputeCounts(unittest.TestCase):
    def test_unique_is_confirmed_and_group_of_one(self):
        c = logmod.compute_counts([detail("alpha", "g1"), detail("alpha", "g2"), detail("beta", "g2", "refuted"),
                                   detail("beta", "g3", "dropped"), detail("beta", "g4", "partial")])
        self.assertEqual(c["alpha"], {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1})
        self.assertEqual(c["beta"], {"total": 2, "confirmed": 0, "refuted": 1, "partial": 1, "unique": 0})


class ValidatePanel(unittest.TestCase):
    def setUp(self):
        import config as cfg
        self.cfg = cfg.load_config(CONFIG)

    def test_accepts_and_fills_seats_models_and_unique(self):
        rec = logmod.validate_panel(record(), self.cfg, force=False)
        self.assertEqual(rec["seats"], {"alpha": "vendor/model-a", "beta": "router/vendor/model-b"})
        self.assertEqual(rec["findings"]["alpha"]["unique"], 1)
        self.assertEqual(len(rec["id"]), 8)

    def test_rejects_count_mismatch_unless_forced(self):
        bad = record(); bad["findings"]["alpha"]["confirmed"] = 1
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, self.cfg, force=False)
        self.assertIn("alpha", str(e.exception))
        rec = logmod.validate_panel(bad, self.cfg, force=True)
        self.assertIn("count_mismatch", rec["notes"])

    def test_rejects_unknown_seat_unless_forced(self):
        bad = record(seats=["alpha", "beta", "zeta"])  # zeta is not in config; alpha/beta keep their findings
        with self.assertRaises(ValueError):
            logmod.validate_panel(bad, self.cfg, force=False)
        rec = logmod.validate_panel(bad, self.cfg, force=True)
        self.assertIsNone(rec["seats"]["zeta"])

    def test_rejects_bad_kind_verdict_severity_and_missing_detail(self):
        for bad in (record(kind="code"), record(findings_detail=None),
                    record(findings_detail=[dict(detail("alpha", "g1"), verdict="maybe")]),
                    record(findings_detail=[dict(detail("alpha", "g1"), severity="huge")])):
            with self.assertRaises(ValueError):
                logmod.validate_panel(bad, self.cfg, force=False)

    def test_dispute_shape_enforced(self):
        bad = record(disputes=[{"summary": "s", "challenger": "alpha"}])
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, self.cfg, force=False)
        self.assertIn("proposals", str(e.exception))


class Cli(unittest.TestCase):
    def test_end_to_end_usage_panel_check_resolve_amend(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["usage", "--seat", "alpha", "--mode", "new", "--dir", "/work/x", "--duration", "5", "--ok", "true", "--model", "vendor/model-a"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            # a skim-worthy panel: 3 seats, 15 findings, none refuted
            fd = [detail(s, f"g{s}{i}") for s in ("alpha", "beta", "gamma") for i in range(5)]
            counts = {s: {"total": 5, "confirmed": 5, "refuted": 0, "partial": 0, "unique": 5} for s in ("alpha", "beta", "gamma")}
            rec = record(seats=["alpha", "beta", "gamma"], findings=counts, findings_detail=fd)
            r = run(["panel"], stdin=json.dumps(rec), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("logged", r.stdout)
            self.assertIn("WATCH[verification-skim|open]", r.stdout)
            d = store.load(env["BALANCEWHEEL_METRICS_LOG"])
            self.assertEqual(len(d["watches"]), 1)
            wid = d["watches"][0]["id"]
            r = run(["check"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"]), 1)  # deduped
            r = run(["resolve", wid, "--note", "sample held"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"][0]["status"], "resolved")
            r = run(["watches"], env=env)
            self.assertIn(wid, r.stdout); self.assertIn("resolved", r.stdout)
            pid = d["panels"][0]["id"]
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"notes": "amended", "note": "why"}), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["notes"], "amended")
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"findings_detail": [{"seat": "alpha", "group": "g"}]}), env=env)
            self.assertEqual(r.returncode, 2)  # malformed detail is rejected, not a traceback

    def test_amend_recording_triage_flips_open_bloat_watch_to_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            fd = [detail(s, f"g{s}{i}", "refuted" if i == 0 else "confirmed") for s in ("alpha", "beta", "gamma") for i in range(11)]
            counts = {s: {"total": 11, "confirmed": 10, "refuted": 1, "partial": 0, "unique": 10} for s in ("alpha", "beta", "gamma")}
            rec = record(seats=["alpha", "beta", "gamma"], findings=counts, findings_detail=fd)
            r = run(["panel"], stdin=json.dumps(rec), env=env)
            self.assertIn("WATCH[panel-bloat|open]", r.stdout)
            d = store.load(env["BALANCEWHEEL_METRICS_LOG"])
            pid = d["panels"][0]["id"]
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"findings_raw": 60, "findings_after_triage": 33, "note": "triage recorded"}), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WATCH[panel-bloat|resolved] (was open)", r.stdout)
            w = [w for w in store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"] if w["kind"] == "panel-bloat"]
            self.assertEqual(len(w), 1); self.assertEqual(w[0]["status"], "resolved")

    def test_config_subcommand_prints_resolved_json_and_fails_on_bad_config(self):
        r = run(["config"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("alpha", json.loads(r.stdout)["seats"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"; p.write_text("{}")
            r = subprocess.run([sys.executable, LOG_PY, "config"], capture_output=True, text=True,
                               env=dict(os.environ, BALANCEWHEEL_CONFIG=str(p)))
            self.assertEqual(r.returncode, 2)
            self.assertIn("seats", r.stderr)

    def test_panel_rejects_invalid_record_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            for bad in (record(kind="nope"), record(target=""), record(findings={"alpha": {"total": "2"}}),
                        record(disputes=[{"summary": 1, "challenger": "alpha", "proposals": "x", "winner": "alpha", "reason": "r"}]),
                        record(disputes=[{"summary": "s", "challenger": "alpha", "proposals": {"alpha": "x"},
                                          "winner": ["alpha"], "reason": "r"}]),
                        record(findings_raw=-1),
                        {"target": "t"}, [1, 2]):
                r = run(["panel"], stdin=json.dumps(bad), env=env)
                self.assertEqual(r.returncode, 2, f"{bad!r}: {r.stderr}")
                self.assertNotIn("Traceback", r.stderr)
            self.assertFalse(os.path.exists(env["BALANCEWHEEL_METRICS_LOG"]))

    def test_unique_mismatch_is_rejected_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            bad = record(); bad["findings"]["alpha"]["unique"] = 2  # computed is 1
            r = run(["panel"], stdin=json.dumps(bad), env=env)
            self.assertEqual(r.returncode, 2); self.assertIn("alpha.unique", r.stderr)
            r = run(["panel", "--force"], stdin=json.dumps(bad), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            p = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]
            self.assertEqual(p["findings"]["alpha"]["unique"], 1); self.assertIn("count_mismatch", p["notes"])

    def test_usage_requires_fields_and_valid_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["usage", "--seat", "alpha", "--mode", "new"], env=env)
            self.assertEqual(r.returncode, 2)
            r = run(["usage", "--seat", "alpha", "--mode", "new", "--dir", "/w", "--duration", "1", "--ok", "typo"], env=env)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(os.path.exists(env["BALANCEWHEEL_METRICS_LOG"]))

    def test_redundant_seat_watch_fires_once_and_respects_dismissal(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}

            def log_panel(i):
                # beta never has a unique catch (shares every group with alpha); alpha always does
                fd = [detail("alpha", f"g{i}a"), detail("alpha", f"g{i}s"), detail("beta", f"g{i}s")]
                counts = {"alpha": {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1},
                          "beta": {"total": 1, "confirmed": 1, "refuted": 0, "partial": 0, "unique": 0}}
                r = run(["panel"], stdin=json.dumps(record(target=f"change {i}", findings=counts, findings_detail=fd)), env=env)
                self.assertEqual(r.returncode, 0, r.stderr)

            def redundant():
                return [w for w in store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"] if w["kind"] == "redundant-seat"]

            for i in range(7):
                log_panel(i)
            run(["check"], env=env)
            ws = redundant()
            self.assertEqual(len(ws), 1)                      # fired once at panel 5, not again at 6 and 7
            self.assertEqual(ws[0]["target"], "beta")
            run(["resolve", ws[0]["id"], "--status", "dismissed", "--note", "keep beta"], env=env)
            for i in range(7, 10):
                log_panel(i)
            self.assertEqual(len(redundant()), 1)             # 3 more panels: still not re-fired
            for i in range(10, 12):
                log_panel(i)
            ws = redundant()
            self.assertEqual(len(ws), 2)                      # 5 panels after the dismissal: one fresh suggestion
            self.assertEqual([w["status"] for w in ws], ["dismissed", "open"])

    def test_check_does_not_recreate_watches_written_under_older_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "m.jsonl")
            target = "example change with a long target name"
            fd = [detail(s, f"g{s}{i}") for s in ("alpha", "beta", "gamma") for i in range(5)]
            counts = {s: {"total": 5, "confirmed": 5, "refuted": 0, "partial": 0, "unique": 5}
                      for s in ("alpha", "beta", "gamma")}
            panel = {"type": "panel", "id": "p0000009", "ts": "2026-02-01T00:00:00Z", "target": target,
                     "kind": "review", "seats": ["alpha", "beta", "gamma"], "immediate_agreement": True,
                     "findings": counts, "findings_detail": fd}
            # a legacy watch line: no "id" and no "panel_id" (store falls back to legacy_watch_id),
            # and a truncated target — the same shape older loggers wrote before the id formula existed
            legacy_watch = {"type": "watch", "ts": "2026-02-01T00:00:01Z", "kind": "verification-skim",
                             "target": "example change with a lo", "detail": "legacy truncated"}
            with open(log, "w") as f:
                f.write(json.dumps(panel) + "\n")
                f.write(json.dumps(legacy_watch) + "\n")
            r = run(["check"], env={"BALANCEWHEEL_METRICS_LOG": log})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("0 new watch(es)", r.stdout)
            self.assertEqual(len(store.load(log)["watches"]), 1)

    def test_forced_unknown_seat_is_annotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["panel", "--force"], stdin=json.dumps(record(seats=["alpha", "beta", "zeta"])), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("forced: seats not in config: ['zeta']", store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["notes"])

    def test_findings_for_a_seat_not_in_seats_is_a_mismatch(self):
        import config as cfg
        bad = record(); bad["findings"]["omega"] = {"total": 1}
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, cfg.load_config(CONFIG), force=False)
        self.assertIn("findings.omega", str(e.exception))

    def test_amend_with_inconsistent_counts_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            run(["panel"], stdin=json.dumps(record()), env=env)
            pid = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["id"]
            patch = {"findings_detail": [detail("alpha", "g9")], "findings": {"alpha": {"total": 5}}}
            r = run(["amend", "--panel-id", pid], stdin=json.dumps(patch), env=env)
            self.assertEqual(r.returncode, 2); self.assertIn("alpha.total", r.stderr)
            r = run(["amend", "--panel-id", pid], stdin=json.dumps([]), env=env)
            self.assertEqual(r.returncode, 2)

    def test_amend_findings_only_keeps_mismatch_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            run(["panel"], stdin=json.dumps(record()), env=env)
            pid = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["id"]
            patch = {"findings": {"alpha": {"total": 9}}}
            r = run(["amend", "--panel-id", pid, "--force"], stdin=json.dumps(patch), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            notes = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["notes"]
            self.assertIn("count_mismatch", notes)
            self.assertIn("alpha.total", notes)

    def test_amend_findings_raw_type_checked_even_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            run(["panel"], stdin=json.dumps(record()), env=env)
            pid = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["id"]
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"findings_raw": "60"}), env=env)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["amendments"], [])

    def test_amend_findings_only_merges_over_existing_when_no_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "m.jsonl")
            panel = {"type": "panel", "id": "legacy01", "ts": "2026-01-01T00:00:00Z", "target": "legacy change",
                     "kind": "review", "seats": ["alpha", "beta"], "immediate_agreement": True,
                     "findings": {"alpha": {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1},
                                  "beta": {"total": 3, "confirmed": 1, "refuted": 2, "partial": 0, "unique": 0}}}
            with open(log, "w") as f:
                f.write(json.dumps(panel) + "\n")
            env = {"BALANCEWHEEL_METRICS_LOG": log}
            r = run(["amend", "--panel-id", "legacy01"], stdin=json.dumps({"findings": {"alpha": {"total": 9}}}), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            p = store.load(log)["panels"][0]
            self.assertEqual(p["findings"]["alpha"]["total"], 9)
            self.assertEqual(p["findings"]["alpha"]["confirmed"], 2)  # untouched key for alpha kept
            self.assertEqual(p["findings"]["beta"],
                              {"total": 3, "confirmed": 1, "refuted": 2, "partial": 0, "unique": 0})  # beta untouched

    def test_same_target_repeated_panels_each_get_their_own_skim_watch(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            target = "same change every time"

            def skim_panel():
                fd = [detail(s, f"g{s}{i}") for s in ("alpha", "beta", "gamma") for i in range(5)]
                counts = {s: {"total": 5, "confirmed": 5, "refuted": 0, "partial": 0, "unique": 5}
                          for s in ("alpha", "beta", "gamma")}
                r = run(["panel"], stdin=json.dumps(record(target=target, seats=["alpha", "beta", "gamma"],
                                                            findings=counts, findings_detail=fd)), env=env)
                self.assertEqual(r.returncode, 0, r.stderr)

            skim_panel()
            skim_panel()
            watches = [w for w in store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"] if w["kind"] == "verification-skim"]
            self.assertEqual(len(watches), 2)
            self.assertNotEqual(watches[0]["id"], watches[1]["id"])


if __name__ == "__main__":
    unittest.main()
