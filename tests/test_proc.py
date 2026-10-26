import os
import signal
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from lead import proc


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ProcRunTest(unittest.TestCase):
    def test_success(self):
        r = proc.run(["true"], 5)
        self.assertEqual((r.rc, r.timed_out), (0, False))

    def test_captures_stdout_and_stderr(self):
        r = proc.run(["sh", "-c", "echo out; echo err >&2; exit 3"], 5)
        self.assertEqual((r.rc, r.out, r.err, r.timed_out), (3, "out\n", "err\n", False))

    def test_missing_binary_is_127_not_raised(self):
        r = proc.run(["bw-no-such-binary-xyz"], 5)
        self.assertEqual((r.rc, r.out, r.timed_out), (127, "", False))
        self.assertEqual(r.err, "bw-no-such-binary-xyz: not found")

    def test_not_executable_is_127(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x")
            open(p, "w").close()
            self.assertEqual(proc.run([p], 5).rc, 127)

    def test_timeout_kills_and_returns_quickly(self):
        t = time.time()
        r = proc.run(["sleep", "5"], 0.2)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.rc, 124)
        self.assertLess(time.time() - t, 2)

    def test_grandchild_holding_stdout_does_not_hang(self):
        t = time.time()
        r = proc.run(["sh", "-c", "sleep 5 & echo hi"], 5)
        self.assertEqual(r.out, "hi\n")
        self.assertFalse(r.timed_out)
        self.assertLess(time.time() - t, 2)

    def test_timed_out_tree_is_killed(self):
        with tempfile.TemporaryDirectory() as d:
            pidfile = os.path.join(d, "pid")
            r = proc.run(["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"], 0.5)
            self.assertTrue(r.timed_out)
            with open(pidfile) as f:
                pid = int(f.read())
            for _ in range(20):
                if not alive(pid):
                    break
                time.sleep(0.1)
            if alive(pid):
                os.kill(pid, signal.SIGKILL)
            self.assertFalse(alive(pid), "grandchild survived the timeout")

    def test_interrupt_while_waiting_kills_the_group_and_propagates(self):
        real_wait = subprocess.Popen.wait
        seen = {}

        def wait(self, timeout=None):
            if "pgid" not in seen:
                seen["pgid"] = self.pid
                time.sleep(0.3)  # the backgrounded sleep exists by now
                raise KeyboardInterrupt
            return real_wait(self, timeout)

        with mock.patch.object(subprocess.Popen, "wait", wait):
            with self.assertRaises(KeyboardInterrupt):
                proc.run(["sh", "-c", "sleep 30 & wait"], 60)
        pgid = seen["pgid"]

        def group_gone():
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            return False
        end = time.time() + 2
        while not group_gone() and time.time() < end:
            time.sleep(0.05)
        gone = group_gone()
        if not gone:
            os.killpg(pgid, signal.SIGKILL)
        self.assertTrue(gone, "the child's process group outlived the interrupt")

    def test_cwd_env_and_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            r = proc.run(["sh", "-c", "pwd; echo $BW_T; cat"], 5, cwd=d, env={"PATH": os.environ["PATH"], "BW_T": "v"})
            lines = r.out.split("\n")
            self.assertEqual(os.path.realpath(lines[0]), os.path.realpath(d))
            self.assertEqual(lines[1], "v")
            self.assertEqual(lines[2], "")  # stdin is /dev/null: cat returns at once


if __name__ == "__main__":
    unittest.main()
