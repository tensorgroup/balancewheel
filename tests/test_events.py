import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics")); sys.path.insert(0, str(HERE))
import store  # noqa: E402
from test_log import CONFIG, LOG_PY, record  # noqa: E402


def run(args, log, stdin=None):
    env = dict(os.environ, BALANCEWHEEL_CONFIG=CONFIG, BALANCEWHEEL_METRICS_LOG=log)
    return subprocess.run([sys.executable, LOG_PY, *args], input=stdin, capture_output=True, text=True, env=env)


START = ["session", "--phase", "start", "--project", "abc123", "--worktree", "/w", "--lead", "lead-a",
         "--runtime", "claude-code", "--model", "vendor/model-a", "--resolved", "true", "--plan", "plan-a",
         "--session-id", "s1"]


class Events(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.tmp.name, "m.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_start_and_end_fold_into_sessions(self):
        self.assertEqual(run(START, self.log).returncode, 0)
        r = run(["session", "--phase", "end", "--project", "abc123", "--session-id", "s1", "--reason", "switch"], self.log)
        self.assertEqual(r.returncode, 0, r.stderr)
        s = store.load(self.log)["sessions"]
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["model"], "vendor/model-a")
        self.assertTrue(s[0]["resolved"])
        self.assertEqual(s[0]["end_reason"], "switch")

    def test_session_start_requires_model_and_resolved(self):
        bad = [a for a in START if a not in ("--model", "vendor/model-a")]
        self.assertEqual(run(bad, self.log).returncode, 2)
        bad = START[:START.index("--resolved")] + START[START.index("--resolved") + 2:]
        self.assertEqual(run(bad, self.log).returncode, 2)

    def test_session_end_requires_reason(self):
        r = run(["session", "--phase", "end", "--project", "abc123", "--session-id", "s1"], self.log)
        self.assertEqual(r.returncode, 2)

    def test_lost_session_without_start_still_recorded(self):
        run(["session", "--phase", "end", "--project", "p", "--session-id", "ghost", "--reason", "lost"], self.log)
        s = store.load(self.log)["sessions"]
        self.assertEqual(s[0]["end_reason"], "lost")

    def test_lead_event(self):
        r = run(["lead", "--project", "abc123", "--from-json", '{"lead": "a", "runtime": "claude-code", "model": "m"}',
                 "--to-json", '{"lead": "b", "runtime": "codex", "model": "n"}', "--reason", "capped",
                 "--quota-json", "{}"], self.log)
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = store.load(self.log)["leads"][0]
        self.assertEqual((ev["from"]["lead"], ev["to"]["lead"], ev["reason"]), ("a", "b", "capped"))

    def test_lead_event_rejects_bad_json(self):
        r = run(["lead", "--project", "p", "--from-json", "{", "--to-json", "{}", "--reason", "capped"], self.log)
        self.assertEqual(r.returncode, 2)

    def test_rating(self):
        self.assertEqual(run(["rating", "--session-id", "s1", "--value", "good", "--note", "fine"], self.log).returncode, 0)
        self.assertEqual(run(["rating", "--session-id", "s1", "--value", "meh"], self.log).returncode, 2)
        self.assertEqual(store.load(self.log)["ratings"][0]["value"], "good")

    def test_panel_moderator_round_trip(self):
        rec = record(moderator={"lead": "lead-a", "runtime": "claude-code", "model": "vendor/model-a", "resolved": True, "session": "s1"})
        r = run(["panel"], self.log, stdin=json.dumps(rec))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(store.load(self.log)["panels"][0]["moderator"]["lead"], "lead-a")

    def test_panel_moderator_shape_enforced(self):
        rec = record(moderator={"lead": "lead-a"})
        r = run(["panel"], self.log, stdin=json.dumps(rec))
        self.assertEqual(r.returncode, 2)
        self.assertIn("moderator", r.stderr)


if __name__ == "__main__":
    unittest.main()
