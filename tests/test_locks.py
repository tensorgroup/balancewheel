import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import locks  # noqa: E402

HOLD = ("import sys; sys.path.insert(0, sys.argv[1]); from lead.locks import RuntimeLock; "
        "lk = RuntimeLock(sys.argv[2], sys.argv[3]); assert lk.shared(); print('held', flush=True); sys.stdin.read()")


class RuntimeLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def holder(self, runtime):
        p = subprocess.Popen([sys.executable, "-c", HOLD, str(ROOT), self.state, runtime],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.stdin.close(), p.stdout.close(), p.wait()))
        self.assertEqual(p.stdout.readline().strip(), "held")
        return p

    def test_path_and_mode(self):
        lk = locks.RuntimeLock(self.state, "pi")
        self.addCleanup(lk.release)
        self.assertEqual(lk.path, os.path.join(self.state, "locks", "runtime-pi.lock"))
        self.assertEqual(os.stat(lk.path).st_mode & 0o777, 0o600)

    def test_exclusive_fails_while_shared_held_by_another_process(self):
        self.holder("codex")
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertFalse(lk.try_exclusive())

    def test_shared_locks_coexist(self):
        self.holder("codex")
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertTrue(lk.shared())

    def test_other_runtime_unaffected(self):
        self.holder("codex")
        lk = locks.RuntimeLock(self.state, "pi")
        self.addCleanup(lk.release)
        self.assertTrue(lk.try_exclusive())

    def test_exclusive_succeeds_after_holder_exits(self):
        p = self.holder("codex")
        p.stdin.close()
        p.wait(10)
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertTrue(lk.try_exclusive())

    def test_fd_is_read_only(self):
        import fcntl
        lk = locks.RuntimeLock(self.state, "pi")
        self.addCleanup(lk.release)
        self.assertEqual(fcntl.fcntl(lk.fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)

    def test_try_shared_fails_while_exclusive_held(self):
        held = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(held.release)
        self.assertTrue(held.try_exclusive())
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertFalse(lk.try_shared())
        held.release()
        self.assertTrue(lk.try_shared())

    def test_try_shared_coexists_with_shared(self):
        self.holder("codex")
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertTrue(lk.try_shared())

    def test_release_is_idempotent(self):
        lk = locks.RuntimeLock(self.state, "pi")
        self.assertTrue(lk.shared())
        lk.release()
        lk.release()
        self.assertIsNone(lk.fd)
        other = locks.RuntimeLock(self.state, "pi")
        self.addCleanup(other.release)
        self.assertTrue(other.try_exclusive())


    def unlock_in_child(self, fd):
        """A lead child that inherited `fd` and drops the flock on it."""
        code = "import fcntl, sys; fcntl.flock(int(sys.argv[1]), fcntl.LOCK_UN)"
        subprocess.run([sys.executable, "-c", code, str(fd)], pass_fds=(fd,), check=True, timeout=30)

    def test_child_unlocking_the_passed_fd_does_not_release_the_session(self):
        for take in ("shared", "try_shared"):
            lk = locks.RuntimeLock(self.state, "pi")
            self.assertTrue(getattr(lk, take)())
            self.unlock_in_child(lk.fd)
            other = locks.RuntimeLock(self.state, "pi")
            self.assertFalse(other.try_exclusive(), take)
            lk.release()
            self.assertTrue(other.try_exclusive(), take)
            other.release()

    def test_session_fd_is_separate_read_only_and_not_inheritable(self):
        import fcntl
        lk = locks.RuntimeLock(self.state, "pi")
        self.addCleanup(lk.release)
        self.assertTrue(lk.shared())
        self.assertIsNotNone(lk.session_fd)
        self.assertNotEqual(lk.session_fd, lk.fd)
        self.assertFalse(os.get_inheritable(lk.session_fd))
        self.assertEqual(fcntl.fcntl(lk.session_fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
        lk.release()
        self.assertIsNone(lk.session_fd)

    def test_try_shared_failing_leaves_no_session_fd(self):
        held = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(held.release)
        self.assertTrue(held.try_exclusive())
        lk = locks.RuntimeLock(self.state, "codex")
        self.addCleanup(lk.release)
        self.assertFalse(lk.try_shared())
        self.assertIsNone(lk.session_fd)


class ToolRuntimeTest(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(locks.tool_runtime("claude", {}), "claude-code")
        self.assertEqual(locks.tool_runtime("kimi", None), "kimi")

    def test_config_runtime_wins(self):
        self.assertEqual(locks.tool_runtime("claude", {"runtime": "pi"}), "pi")
        self.assertEqual(locks.tool_runtime("mytool", {"runtime": "codex"}), "codex")

    def test_unmapped_tool_runtime_is_none(self):
        self.assertIsNone(locks.tool_runtime("mytool", {}))
        for t in locks.NON_RUNTIME_TOOLS:
            self.assertIsNone(locks.tool_runtime(t, {}))

    def test_runtime_tools(self):
        cfg = {"tools": {"claude": {}, "orca": {}, "mytool": {}, "piup": {"runtime": "pi"}}}
        self.assertEqual(locks.runtime_tools(cfg), {"claude-code": "claude", "pi": "piup"})
        self.assertEqual(locks.runtime_tools({}), {})


if __name__ == "__main__":
    unittest.main()
