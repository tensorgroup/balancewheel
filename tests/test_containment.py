import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import containment  # noqa: E402
import lead.guard as guard  # noqa: E402
from lead.proc import Result  # noqa: E402


class GuardKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(os.path.realpath(self.tmp.name))
        self.ext = self.d / "extensions"; self.ext.mkdir()
        (self.ext / "a.ts").write_text("one")
        self.script = self.d / "notify.sh"; self.script.write_text("echo 1")
        self.hooks = self.d / "hooks.json"
        self.hooks.write_text(json.dumps({"x": {"PreToolUse": [{"matcher": "*", "hooks": [
            {"type": "command", "command": f"/bin/sh {self.script} --flag", "timeout": 5}]}]}}))
        self.settings = self.d / "settings.json"; self.settings.write_text("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, files=None, version="1.0"):
        with mock.patch.object(containment, "_first_line", return_value=version):
            return containment.guard_key("pi", ["pi", "--version"], "macos",
                                         files or [str(self.hooks), str(self.settings), str(self.ext)])

    def test_stable_and_has_expected_fields(self):
        k = self.key()
        self.assertEqual(k, self.key())
        for f in ("runtime", "runtime_version", "backend", "backend_version", "guard_version", "adapter_sha", "hook_files"):
            self.assertIn(f, k)
        self.assertEqual(k["guard_version"], guard.guard_version())

    def test_changes_on_in_place_extension_edit(self):
        k = self.key(); (self.ext / "a.ts").write_text("two"); self.assertNotEqual(k, self.key())

    def test_changes_on_new_extension_file(self):
        k = self.key(); (self.ext / "b.ts").write_text("x"); self.assertNotEqual(k, self.key())

    def test_changes_on_script_named_by_hook_command(self):
        k = self.key(); self.script.write_text("echo 2"); self.assertNotEqual(k, self.key())

    def test_changes_on_symlink_retarget(self):
        (self.ext / "l.ts").symlink_to(self.d / "settings.json")
        k = self.key(); os.unlink(self.ext / "l.ts"); (self.ext / "l.ts").symlink_to(self.script)
        self.assertNotEqual(k, self.key())

    def test_changes_on_edit_inside_linked_extension_dir(self):
        other = self.d / "other"; other.mkdir(); (other / "x.ts").write_text("a")
        (self.ext / "linked").symlink_to(other)
        k = self.key(); (other / "x.ts").write_text("b")
        self.assertNotEqual(k, self.key())

    def test_symlink_cycle_terminates(self):
        (self.ext / "loop").symlink_to(self.ext)
        self.key()

    def test_changes_on_guard_version_and_runtime_version(self):
        k = self.key()
        with mock.patch.object(containment, "guard_version", return_value="other"):
            self.assertNotEqual(k, self.key())
        self.assertNotEqual(k, self.key(version="2.0"))

    def test_changes_on_settings_edit(self):
        k = self.key(); self.settings.write_text('{"a": 1}'); self.assertNotEqual(k, self.key())

    def test_store_parameter_leaves_containment_json_alone(self):
        s = str(self.d / "state"); os.makedirs(s)
        key = self.key()
        containment.record_pass(s, "pi", key, "/ev", store="guard-verify.json")
        self.assertFalse(os.path.exists(os.path.join(s, "containment.json")))
        self.assertTrue(containment.is_verified(s, "pi", key, store="guard-verify.json"))
        self.assertFalse(containment.is_verified(s, "pi", key))
        containment.record_pass(s, "pi", {"k": 1}, "/ev")
        self.assertTrue(containment.is_verified(s, "pi", key, store="guard-verify.json"))
        self.assertTrue(containment.is_verified(s, "pi", {"k": 1}))


class InjectableKeys(unittest.TestCase):
    def test_verification_key_uses_the_given_run(self):
        calls = []

        def run(argv, timeout, **kw):
            calls.append(list(argv))
            return Result(0, "pi 9.9\nmore\n", "", False)

        with mock.patch("subprocess.Popen", side_effect=AssertionError("spawned a process")):
            key = containment.verification_key("pi", ["pi", "--version"], "sandbox-exec", run=run)
        self.assertEqual(calls, [["pi", "--version"]])
        self.assertEqual(key["runtime_version"], "pi 9.9")

    def test_first_line_unreadable_on_missing_binary_or_timeout(self):
        self.assertEqual(containment._first_line(["x"], run=lambda a, t, **k: Result(127, "", "x: not found", False)),
                         "unreadable")
        self.assertEqual(containment._first_line(["x"], run=lambda a, t, **k: Result(124, "", "", True)), "unreadable")
        self.assertEqual(containment._first_line(["x"], run=lambda a, t, **k: Result(1, "", "v from stderr\n", False)),
                         "v from stderr")
        self.assertEqual(containment._first_line(["x"], run=lambda a, t, **k: Result(0, "", "", False)), "unknown")

    def test_guard_key_passes_run_through(self):
        calls = []

        def run(argv, timeout, **kw):
            calls.append(list(argv))
            return Result(0, "v\n", "", False)

        with mock.patch("subprocess.Popen", side_effect=AssertionError("spawned a process")):
            key = containment.guard_key("pi", ["pi", "--version"], "sandbox-exec", [], run=run)
        self.assertEqual(calls, [["pi", "--version"]])
        self.assertIn("guard_version", key)


if __name__ == "__main__":
    unittest.main()
