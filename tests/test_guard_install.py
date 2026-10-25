import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead.guard import AGY_AWAITS, install  # noqa: E402
from lead.guard.hook import INERT_ANSWER  # noqa: E402


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = str(self.dir / "config" / "hooks.json")

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        return json.loads(Path(self.path).read_text())

    def test_creates_missing_file(self):
        msg = install.install_agy(self.path, str(self.dir / "state"))
        self.assertTrue(install.agy_installed(self.path))
        self.assertIn(self.path, msg)
        self.assertIn("re-verified", msg)
        entry = self.load()["bw-guard"]["PreToolUse"][0]
        self.assertEqual(entry["matcher"], "*")
        h = entry["hooks"][0]
        self.assertEqual(h["timeout"], 5)
        self.assertEqual(h["type"], "command")
        self.assertIn(INERT_ANSWER, h["command"])
        self.assertIn('exec /usr/bin/python3 -I "$BW_GUARD_DIR/hook.py" agy', h["command"])
        self.assertTrue(h["command"].startswith("/bin/sh -c '[ -n \"$BW_GUARD_MODE\" ]"))

    def test_keeps_other_entries_and_is_idempotent(self):
        os.makedirs(os.path.dirname(self.path))
        other = {"orca-status": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "x", "timeout": 1}]}]}}
        Path(self.path).write_text(json.dumps(other))
        install.install_agy(self.path, "/s")
        first = Path(self.path).read_text()
        install.install_agy(self.path, "/s")
        self.assertEqual(Path(self.path).read_text(), first)
        self.assertEqual(self.load()["orca-status"], other["orca-status"])
        self.assertEqual(install.other_pretooluse_entries(self.path), ["orca-status"])

    def test_invalid_json_refuses_and_writes_nothing(self):
        os.makedirs(os.path.dirname(self.path))
        Path(self.path).write_text("{not json")
        with self.assertRaises(ValueError):
            install.install_agy(self.path, "/s")
        self.assertEqual(Path(self.path).read_text(), "{not json")
        self.assertEqual([p for p in os.listdir(os.path.dirname(self.path))], ["hooks.json"])

    def test_agy_installed_checks_the_whole_entry(self):
        install.install_agy(self.path, "/s")
        base = self.load()
        for mutate in (
            lambda d: d["bw-guard"].__setitem__("PostToolUse", d["bw-guard"].pop("PreToolUse")),
            lambda d: d["bw-guard"]["PreToolUse"][0].__setitem__("matcher", "Bash"),
            lambda d: d["bw-guard"]["PreToolUse"][0]["hooks"][0].__setitem__("timeout", 99),
            lambda d: d["bw-guard"]["PreToolUse"][0]["hooks"][0].__setitem__("command", "true"),
        ):
            d = json.loads(json.dumps(base)); mutate(d)
            Path(self.path).write_text(json.dumps(d))
            self.assertFalse(install.agy_installed(self.path))
        self.assertFalse(install.agy_installed(str(self.dir / "absent.json")))

    def test_uninstall_removes_only_bw_guard(self):
        os.makedirs(os.path.dirname(self.path))
        Path(self.path).write_text(json.dumps({"keep": {"Stop": []}}))
        install.install_agy(self.path, "/s")
        self.assertIn("bw-guard", self.load())
        install.uninstall_agy(self.path)
        self.assertEqual(self.load(), {"keep": {"Stop": []}})
        install.uninstall_agy(str(self.dir / "absent.json"))  # no file: fine

    def test_hooks_path_env(self):
        with mock.patch.dict(os.environ, {"BW_AGY_HOOKS": "/x/h.json"}):
            self.assertEqual(install.hooks_path(), "/x/h.json")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BW_AGY_HOOKS", None)
            self.assertTrue(install.hooks_path().endswith(".gemini/config/hooks.json"))


class CliRefusal(unittest.TestCase):
    def test_install_refuses_while_spike_open(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            hooks = os.path.join(t, "hooks.json")
            env = {**os.environ, "BW_AGY_HOOKS": hooks, "BW_STATE_DIR": t}
            root = Path(__file__).resolve().parent.parent
            r = subprocess.run([sys.executable, str(root / "bin" / "bw"), "guard", "install", "agy"],
                               capture_output=True, text=True, env=env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(AGY_AWAITS, r.stdout + r.stderr)
            self.assertFalse(os.path.exists(hooks))
            r = subprocess.run([sys.executable, str(root / "bin" / "bw"), "guard", "uninstall", "agy"],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
