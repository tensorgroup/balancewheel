import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lead import status
from lead.proc import Result

REPO = Path(__file__).resolve().parent.parent
BW = str(REPO / "bin" / "bw")
FIX = REPO / "tests" / "fixtures" / "orca"


def fake_run(out):
    calls = []

    def run(argv, timeout, **kw):
        calls.append(argv)
        return Result(0, out, "", False)
    run.calls = calls
    return run


class StatusCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.state = str(self.root / "state")
        self.config = {"schema_version": 2,
                       "plans": {"pa": {"billing": "plan", "quota": "statusline"}},
                       "leads": {"la": {"runtime": "codex", "model": "a", "plan": "pa"}},
                       "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}}, "profiles": {"p": {"lead_order": ["la"]}}, "default_profile": "p",
                       "state_dir": self.state, "metrics_log": str(self.root / "metrics.jsonl"),
                       "rotation": {"cap_expiry_hours": 5}}
        self.saved = {k: os.environ.get(k) for k in ("BW_ORCA_SUPPORT", "BW_CCSWITCH_DIR")}
        os.environ["BW_ORCA_SUPPORT"] = str(self.root / "orca")
        os.environ["BW_CCSWITCH_DIR"] = str(self.root / "cc-switch")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def make_orca(self, settings_fixture):
        d = self.root / "orca"
        (d / "profiles" / "p1").mkdir(parents=True)
        (d / "orca-profile-index.json").write_text(json.dumps({"activeProfileId": "p1"}))
        c = sqlite3.connect(str(d / "profiles" / "p1" / "profile-state.db"))
        c.execute("CREATE TABLE profile_state_documents (domain TEXT PRIMARY KEY, payload TEXT, "
                  "domain_version INTEGER, revision INTEGER, updated_at INTEGER, content_hash TEXT)")
        c.execute("INSERT INTO profile_state_documents VALUES ('settings', ?, 1, 1, 0, 'h')",
                  ((FIX / settings_fixture).read_text(),))
        c.commit()
        c.close()

    def test_no_orca_dir_single_unknown_line_and_no_shell_out(self):
        run = fake_run("")
        lines = status.render(status.facts(self.config, self.state, time.time(), run=run))
        orca = [ln for ln in lines if ln.startswith("orca: ")]
        self.assertEqual(len(orca), 1, lines)
        self.assertTrue(orca[0].startswith("orca: unknown — "), orca)
        self.assertTrue(any(ln.startswith("cc-switch: ") for ln in lines), lines)
        self.assertEqual(run.calls, [])

    def test_orca_manual_running_is_ok(self):
        self.make_orca("settings_manual.json")
        run = fake_run((FIX / "status_running.json").read_text())
        f = status.facts(self.config, self.state, time.time(), run=run)
        self.assertEqual({c.state for c in f["checks"]["orca"]}, {"ok"})
        self.assertIn("orca: ok", status.render(f))
        self.assertEqual(run.calls, [["orca", "status", "--json"]])

    def test_orca_full_access_lines(self):
        self.make_orca("settings_full_access.json")
        f = status.facts(self.config, self.state, time.time(), run=fake_run((FIX / "status_stopped.json").read_text()))
        orca = [ln for ln in status.render(f) if ln.startswith("orca: ")]
        self.assertEqual(len(orca), 3, orca)
        self.assertTrue(all(ln.startswith("orca: warn — ") for ln in orca), orca)

    def test_facts_keys_and_todays_lines(self):
        f = status.facts(self.config, self.state, time.time(), run=fake_run(""))
        for k in ("plans", "worktrees", "leads", "guard", "checks", "last_start"):
            self.assertIn(k, f)
        self.assertEqual(set(f["checks"]), {"orca", "cc-switch"})
        self.assertIsNone(f["last_start"])
        lines = status.render(f)
        self.assertIn("plans:", lines)
        self.assertIn("  pa (plan): quota unknown", lines)
        self.assertIn("leads:", lines)
        self.assertTrue(any("bw cap <plan>" in ln for ln in lines))

    def test_last_start_rendered(self):
        os.makedirs(self.state)
        with open(os.path.join(self.state, "start.json"), "w") as fh:
            json.dump({"ts": 1000.0}, fh)
        f = status.facts(self.config, self.state, time.time(), run=fake_run(""))
        self.assertEqual(f["last_start"], {"ts": 1000.0})
        self.assertIn(f"last start: {time.ctime(1000.0)}", status.render(f))

    def test_bw_status_without_orca_or_ccswitch_exits_0(self):
        cfg = self.root / "config.json"
        cfg.write_text(json.dumps({k: v for k, v in self.config.items() if k != "rotation"}))
        env = {**os.environ, "BALANCEWHEEL_CONFIG": str(cfg)}
        r = subprocess.run([sys.executable, BW, "status"], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("orca: unknown — ", r.stdout)
        self.assertIn("cc-switch: unknown — ", r.stdout)


if __name__ == "__main__":
    unittest.main()
