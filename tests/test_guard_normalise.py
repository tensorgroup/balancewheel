import json
import os
import shutil
import tempfile
import unittest

from lead import guard
from lead.guard import normalise as N
from lead.guard import redact

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "guard")
CWD, WT = "/work/wt", "/work/wt"


def fixture(name):
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)["event"]


def call(runtime, tool, kind, command=None, paths=(), server=None, args=None):
    return N.Call(runtime, tool, kind, command, tuple(paths), server, args, CWD, WT)


class NormaliseTest(unittest.TestCase):
    def pi(self, name):
        return N.normalise("pi", fixture(name), CWD, WT)

    def test_fixtures(self):
        self.assertEqual(self.pi("pi_bash.json"), call("pi", "bash", "shell", "ls"))
        self.assertEqual(self.pi("pi_write.json"), call("pi", "write", "write", paths=["/work/wt/b.txt"]))
        self.assertEqual(self.pi("pi_edit.json"), call("pi", "edit", "write", paths=["/work/wt/b.txt"]))
        self.assertEqual(self.pi("pi_read.json"), call("pi", "read", "read", paths=["/work/wt/a.txt"]))
        self.assertEqual(self.pi("pi_grep.json"), call("pi", "grep", "read", paths=["/work/wt"]))
        self.assertEqual(self.pi("pi_find.json"), call("pi", "find", "read", paths=["/work/wt"]))
        self.assertEqual(self.pi("pi_ls.json"), call("pi", "ls", "read", paths=["/work/wt"]))

    def test_paths_resolve_against_cwd_and_dotdot(self):
        ev = {"toolName": "write", "input": {"path": "../x/../y"}}
        self.assertEqual(N.normalise("pi", ev, "/work/wt/sub", WT).paths, ("/work/wt/y",))

    def test_symlink_is_canonicalised(self):
        d = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d)
        os.mkdir(os.path.join(d, "real"))
        os.symlink(os.path.join(d, "real"), os.path.join(d, "link"))
        ev = {"toolName": "write", "input": {"path": "link/f"}}
        self.assertEqual(N.normalise("pi", ev, d, d).paths, (os.path.join(d, "real", "f"),))

    def test_edit_path_is_taken_whatever_the_edit_form(self):
        for inp in ({"file_path": "b.txt", "old_string": "a", "new_string": "b"},
                    {"path": "b.txt", "oldText": "a", "newText": "b"},
                    {"edits": [{"path": "b.txt", "oldText": "a", "newText": "b"}]}):
            c = N.normalise("pi", {"toolName": "edit", "input": inp}, CWD, WT)
            self.assertEqual((c.kind, c.paths), ("write", ("/work/wt/b.txt",)), inp)

    def test_write_without_a_path_has_none(self):
        self.assertEqual(N.normalise("pi", {"toolName": "write", "input": {"content": "x"}}, CWD, WT).paths, ())

    def test_multi_edit_collects_every_path(self):
        ev = {"toolName": "edit", "input": {"path": "a", "edits": [{"path": "b"}, {"path": "../c"}]}}
        self.assertEqual(N.normalise("pi", ev, CWD, WT).paths, ("/work/wt/a", "/work/wt/b", "/work/c"))

    def test_tilde_and_at_paths_keep_both_readings(self):
        ev = {"toolName": "read", "input": {"path": "~/.ssh/id"}}
        got = N.normalise("pi", ev, CWD, WT).paths
        self.assertIn(os.path.realpath(os.path.expanduser("~/.ssh/id")), got)
        self.assertIn("/work/wt/~/.ssh/id", got)
        ev = {"toolName": "read", "input": {"path": "@a.txt"}}
        got = N.normalise("pi", ev, CWD, WT).paths
        self.assertEqual(set(got), {"/work/wt/@a.txt", "/work/wt/a.txt"})

    def test_non_string_path_is_other(self):
        c = N.normalise("pi", {"toolName": "read", "input": {"path": ["a"]}}, CWD, WT)
        self.assertEqual((c.kind, c.tool), ("other", "read"))

    def test_unknown_tool_is_other_with_raw_name(self):
        c = N.normalise("pi", {"toolName": "frobnicate", "input": {"a": 1}}, CWD, WT)
        self.assertEqual((c.kind, c.tool, c.args), ("other", "frobnicate", {"a": 1}))
        self.assertEqual(N.normalise("pi", {"toolName": "Bash", "input": {"command": "ls"}}, CWD, WT).kind, "other")

    def test_malformed_events_are_other(self):
        for ev in ({"toolName": "bash", "input": {"command": 5}}, {"toolName": "bash"}, {}, [], "x"):
            self.assertEqual(N.normalise("pi", ev, CWD, WT).kind, "other", ev)

    def test_oversized_payload_and_stub(self):
        big = {"toolName": "write", "input": {"path": "a", "content": "x" * (256 * 1024 + 1)}}
        for ev in (big, {"oversized": True}):
            c = N.normalise("pi", ev, CWD, WT)
            self.assertEqual((c.kind, c.tool, c.paths), ("other", "oversized", ()))

    def test_just_under_the_cap_is_normal(self):
        ev = {"toolName": "write", "input": {"path": "a", "content": "x" * (200 * 1024)}}
        self.assertEqual(N.normalise("pi", ev, CWD, WT).kind, "write")

    def test_agy_and_unknown_runtimes_are_always_other(self):
        c = N.normalise("agy", {"toolName": "run_command", "input": {"command": "ls"}}, CWD, WT)
        self.assertEqual((c.kind, c.tool, c.command), ("other", "run_command", None))
        self.assertEqual(N.normalise("agy", {"x": 1}, CWD, WT).tool, "unknown")
        self.assertEqual(N.normalise("zzz", {"toolName": "bash", "input": {"command": "ls"}}, CWD, WT).kind, "other")


class RedactTest(unittest.TestCase):
    def test_patterns(self):
        cases = {
            "curl -H 'Authorization: Bearer abc.def-1' u": "abc.def",
            "curl -H 'Authorization: Basic dXNlcjpwYXNz' u": "dXNlcjpwYXNz",
            "echo Bearer sk-12345678": "sk-12345678",
            "API_KEY=hunter2 run": "hunter2",
            "AWS_SECRET_ACCESS_KEY='a b c' run": "a b c",
            "x --password hunter2 y": "hunter2",
            "x --token=hunter2 y": "hunter2",
            "git clone https://bob:hunter2@host/r": "hunter2",
            "MYTOKEN=hunter2": "hunter2",
            "PASSWORD=hunter2": "hunter2",
        }
        for src, secret in cases.items():
            out = redact.redact(src)
            self.assertNotIn(secret, out, src)
            self.assertIn("[REDACTED]", out, src)

    def test_leaves_ordinary_text(self):
        for s in ("git commit -m 'fix the token parser'", "ls -la", "https://host/path", "KEYBOARD=us"):
            self.assertEqual(redact.redact(s), s)

    def test_keeps_the_names(self):
        self.assertEqual(redact.redact("API_KEY=hunter2 run"), "API_KEY=[REDACTED] run")
        self.assertEqual(redact.redact("https://bob:pw@h/r"), "https://bob:[REDACTED]@h/r")

    def test_digest_is_of_the_unredacted_call_and_ignores_key_order(self):
        a = call("pi", "bash", "shell", "TOKEN=abc ls")
        b = call("pi", "bash", "shell", "TOKEN=abd ls")
        self.assertEqual(len(redact.digest(a)), 16)
        self.assertNotEqual(redact.digest(a), redact.digest(b))
        x = a._replace(args={"a": 1, "b": {"c": 2, "d": 3}})
        y = a._replace(args={"b": {"d": 3, "c": 2}, "a": 1})
        self.assertEqual(redact.digest(x), redact.digest(y))
        self.assertEqual(redact.digest(x._asdict()), redact.digest(x))


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.dir = os.path.join(self.tmp, "guard")
        shutil.copytree(os.path.dirname(guard.__file__), self.dir, ignore=shutil.ignore_patterns("__pycache__"))

    def test_shapes(self):
        self.assertRegex(guard.rules_version(), r"^[0-9a-f]{12}$")
        self.assertRegex(guard.guard_version(), r"^[0-9a-f]{12}$")
        self.assertIs(guard.AGY_SPIKE_PASSED, False)

    def test_rules_version_ignores_a_non_deciding_file(self):
        before = (guard.rules_version(self.dir), guard.guard_version(self.dir))
        with open(os.path.join(self.dir, "__init__.py"), "a") as f:
            f.write("# edit\n")
        with open(os.path.join(self.dir, "other.py"), "w") as f:
            f.write("x = 1\n")
        self.assertEqual(guard.rules_version(self.dir), before[0])
        self.assertNotEqual(guard.guard_version(self.dir), before[1])

    def test_rules_version_changes_with_a_deciding_file(self):
        before = guard.rules_version(self.dir)
        with open(os.path.join(self.dir, "rules.py"), "a") as f:
            f.write("# edit\n")
        self.assertNotEqual(guard.rules_version(self.dir), before)

    def test_pycache_is_excluded(self):
        before = guard.guard_version(self.dir)
        os.mkdir(os.path.join(self.dir, "__pycache__"))
        with open(os.path.join(self.dir, "__pycache__", "x.pyc"), "w") as f:
            f.write("x")
        self.assertEqual(guard.guard_version(self.dir), before)


if __name__ == "__main__":
    unittest.main()
