import os
import plistlib
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import updates  # noqa: E402
from lead.locks import RuntimeLock  # noqa: E402
from lead.proc import Result  # noqa: E402

NOW = 1_800_000_000


class FakeRun:
    """Keyed by argv tuple. `updates` maps an update argv to (rc, sleep, timed_out); `versions`
    maps a version argv to a list of outputs, consumed in order (the last one repeats)."""

    def __init__(self, updates=None, versions=None):
        self.updates = {tuple(k): v for k, v in (updates or {}).items()}
        self.versions = {tuple(k): list(v) for k, v in (versions or {}).items()}
        self.calls = []  # (argv, timeout, start, end)
        self.lock = threading.Lock()

    def __call__(self, argv, timeout, cwd=None, env=None, stdin=None):
        key = tuple(argv)
        start = time.monotonic()
        if key in self.updates:
            rc, sleep, timed_out = self.updates[key]
            time.sleep(sleep)
            res = Result(124 if timed_out else rc, "", "boom\nmore" if rc else "", timed_out)
        elif key in self.versions:
            outs = self.versions[key]
            out = outs.pop(0) if len(outs) > 1 else outs[0]
            res = Result(0 if out is not None else 1, out or "", "", False)
        else:
            res = Result(127, "", f"{argv[0]}: not found", False)
        with self.lock:
            self.calls.append((key, timeout, start, time.monotonic()))
        return res

    def ran(self, argv):
        return [c for c in self.calls if c[0] == tuple(argv)]


def cfg(tools, interval=0):
    return {"tools": tools, "updates": {"min_interval_hours": interval}}


class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_three_installers_run_in_parallel(self):
        tools = {"claude": {"update": ["claude", "update"], "version": ["v1"]},
                 "codex": {"update": ["npm", "i", "-g", "codex"], "version": ["v2"]},
                 "pi": {"update": ["pnpm", "add", "pi"], "version": ["v3"]}}
        run = FakeRun(updates={("claude", "update"): (0, 0.4, False), ("npm", "i", "-g", "codex"): (0, 0.4, False),
                               ("pnpm", "add", "pi"): (0, 0.4, False)},
                      versions={("v1",): ["1"], ("v2",): ["2"], ("v3",): ["3"]})
        t0 = time.monotonic()
        out = updates.update_all(cfg(tools), self.state, NOW, run=run)
        self.assertLess(time.monotonic() - t0, 1.0)
        self.assertEqual({k: v["result"] for k, v in out.items()},
                         {"claude": "current", "codex": "current", "pi": "current"})

    def test_same_installer_serialises(self):
        tools = {"claude": {"update": ["brew", "upgrade", "claude"], "version": ["v1"]},
                 "cc-switch": {"update": ["brew", "upgrade", "--cask", "cc-switch"], "version": ["v2"]}}
        run = FakeRun(updates={("brew", "upgrade", "claude"): (0, 0.4, False),
                               ("brew", "upgrade", "--cask", "cc-switch"): (0, 0.4, False)},
                      versions={("v1",): ["1"], ("v2",): ["2"]})
        t0 = time.monotonic()
        updates.update_all(cfg(tools), self.state, NOW, run=run)
        self.assertGreaterEqual(time.monotonic() - t0, 0.8)
        a, = run.ran(["brew", "upgrade", "claude"])
        b, = run.ran(["brew", "upgrade", "--cask", "cc-switch"])
        self.assertTrue(a[3] <= b[2] or b[3] <= a[2], "brew updates overlapped")

    def test_locked_runtime_skipped(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from lead.locks import RuntimeLock; "
                "lk = RuntimeLock(sys.argv[2], 'claude-code'); lk.shared(); print('held', flush=True); sys.stdin.read()")
        child = subprocess.Popen([sys.executable, "-c", code, str(ROOT), self.state],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            run = FakeRun(updates={("claude", "update"): (0, 0, False)}, versions={("v1",): ["1"]})
            out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}),
                                     self.state, NOW, run=run)
        finally:
            child.stdin.close()
            child.stdout.close()
            child.wait()
        self.assertEqual(out["claude"]["result"], "skipped-locked")
        self.assertEqual(out["claude"]["runtime"], "claude-code")
        self.assertEqual(run.ran(["claude", "update"]), [])

    def test_lock_released_after_update(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, False)}, versions={("v1",): ["1"]})
        updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}), self.state, NOW, run=run)
        lk = RuntimeLock(self.state, "claude-code")
        try:
            self.assertTrue(lk.try_exclusive())
        finally:
            lk.release()

    def test_unmapped_tool_skipped_no_lock(self):
        run = FakeRun(updates={("foo", "up"): (0, 0, False)}, versions={("foo", "-v"): ["1.0"]})
        seen = []
        out = updates.update_all(cfg({"foo": {"update": ["foo", "up"], "version": ["foo", "-v"]}}),
                                 self.state, NOW, run=run, on_done=seen.append)
        self.assertEqual(out["foo"]["result"], "skipped-no-lock")
        self.assertIn("set tools.foo.runtime", out["foo"]["detail"])
        self.assertIsNone(out["foo"]["runtime"])
        self.assertEqual(run.ran(["foo", "up"]), [])
        self.assertEqual(seen[0].result, "skipped-no-lock")

    def test_explicit_runtime_key_maps_lock(self):
        run = FakeRun(updates={("foo", "up"): (0, 0, False)}, versions={("foo", "-v"): ["1.0", "1.1"]})
        out = updates.update_all(cfg({"foo": {"update": ["foo", "up"], "version": ["foo", "-v"], "runtime": "pi"}}),
                                 self.state, NOW, run=run)
        self.assertEqual(out["foo"]["runtime"], "pi")
        self.assertEqual(out["foo"]["result"], "updated")
        self.assertTrue(os.path.exists(os.path.join(self.state, "locks", "runtime-pi.lock")))

    def test_explicit_runtime_lock_held_skips(self):
        held = RuntimeLock(self.state, "pi")
        self.assertTrue(held.try_exclusive())  # another updater (or any holder) has it
        try:
            run = FakeRun(updates={("foo", "up"): (0, 0, False)}, versions={("foo", "-v"): ["1.0"]})
            out = updates.update_all(cfg({"foo": {"update": ["foo", "up"], "version": ["foo", "-v"], "runtime": "pi"}}),
                                     self.state, NOW, run=run)
        finally:
            held.release()
        self.assertEqual(out["foo"]["result"], "skipped-locked")
        self.assertEqual(run.ran(["foo", "up"]), [])

    def test_timeout_reported_and_version_rechecked(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, True)}, versions={("v1",): ["1.0", "1.0"]})
        out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"], "timeout_s": 7}}),
                                 self.state, NOW, run=run)
        r = out["claude"]
        self.assertEqual(r["result"], "timed-out")
        self.assertEqual((r["before"], r["after"]), ("1.0", "1.0"))
        self.assertEqual(run.ran(["claude", "update"])[0][1], 7)
        self.assertEqual(len(run.ran(["v1"])), 2)
        self.assertTrue(all(c[1] == 10 for c in run.ran(["v1"])))
        self.assertEqual(r["last_update_at"], NOW)

    def test_default_update_timeout_is_600(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, False)}, versions={("v1",): ["1"]})
        updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}), self.state, NOW, run=run)
        self.assertEqual(run.ran(["claude", "update"])[0][1], 600)

    def test_failed_update_with_unreadable_version(self):
        run = FakeRun(updates={("claude", "update"): (3, 0, False)}, versions={("v1",): ["2.0", None]})
        out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}),
                                 self.state, NOW, run=run)
        r = out["claude"]
        self.assertEqual(r["result"], "failed")
        self.assertEqual(r["before"], "2.0")
        self.assertEqual(r["after"], "unreadable")
        self.assertIn("exit 3", r["detail"])
        self.assertIn("boom", r["detail"])
        self.assertNotIn("more", r["detail"])

    def test_self_updating_reports_version_only(self):
        app = Path(self.state) / "Orca.app"
        (app / "Contents").mkdir(parents=True)
        with open(app / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleShortVersionString": "1.4.2"}, f)
        run = FakeRun()
        out = updates.update_all(cfg({"orca": {"app": str(app), "self_updating": True}}), self.state, NOW, run=run)
        r = out["orca"]
        self.assertEqual((r["result"], r["before"], r["after"], r["runtime"]), ("self-updating", "1.4.2", "1.4.2", None))
        self.assertEqual(run.calls, [])

    def test_no_update_command(self):
        run = FakeRun(versions={("bar", "--version"): ["bar 9"]})
        out = updates.update_all(cfg({"bar": {"version": ["bar", "--version"]}}), self.state, NOW, run=run)
        self.assertEqual((out["bar"]["result"], out["bar"]["after"]), ("no-update-command", "bar 9"))

    def test_interval_skips_recent_update(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, False)}, versions={("v1",): ["1"]})
        prev = {"claude": {"last_update_at": NOW - 3600, "result": "current"}}
        out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}, interval=6),
                                 self.state, NOW, run=run, previous=prev)
        self.assertEqual(out["claude"]["result"], "skipped-interval")
        self.assertEqual(out["claude"]["last_update_at"], NOW - 3600)
        self.assertEqual(out["claude"]["after"], "1")
        self.assertEqual(run.ran(["claude", "update"]), [])

    def test_interval_elapsed_runs_update(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, False)}, versions={("v1",): ["1"]})
        prev = {"claude": {"last_update_at": NOW - 7 * 3600}}
        out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}, interval=6),
                                 self.state, NOW, run=run, previous=prev)
        self.assertEqual(out["claude"]["result"], "current")
        self.assertEqual(out["claude"]["last_update_at"], NOW)

    def test_before_after_versions_recorded(self):
        run = FakeRun(updates={("claude", "update"): (0, 0, False)},
                      versions={("v1",): ["claude 1.0.0 (Code)\n", "\n  claude 1.1.0 (Code)\nextra"]})
        out = updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}),
                                 self.state, NOW, run=run)
        r = out["claude"]
        self.assertEqual((r["before"], r["after"], r["result"]), ("claude 1.0.0 (Code)", "claude 1.1.0 (Code)", "updated"))
        self.assertEqual(r["tool"], "claude")
        self.assertIsInstance(r["seconds"], float)

    def test_on_done_called_per_tool_as_it_finishes(self):
        tools = {"slow": {"update": ["slowinst", "x"], "version": ["vs"], "runtime": "pi"},
                 "fast": {"update": ["fastinst", "x"], "version": ["vf"], "runtime": "kimi"}}
        run = FakeRun(updates={("slowinst", "x"): (0, 0.5, False), ("fastinst", "x"): (0, 0.05, False)},
                      versions={("vs",): ["1"], ("vf",): ["1"]})
        main = threading.current_thread()
        seen = []

        def on_done(r):
            seen.append((r.tool, time.monotonic(), threading.current_thread() is main))

        t0 = time.monotonic()
        updates.update_all(cfg(tools), self.state, NOW, run=run, on_done=on_done)
        self.assertEqual([s[0] for s in seen], ["fast", "slow"])
        self.assertLess(seen[0][1] - t0, 0.4)  # reported before the slow one finished
        self.assertTrue(all(s[2] for s in seen))

    def one(self, versions, rc=0):
        run = FakeRun(updates={("claude", "update"): (rc, 0, False)}, versions={("v1",): versions})
        return updates.update_all(cfg({"claude": {"update": ["claude", "update"], "version": ["v1"]}}),
                                  self.state, NOW, run=run)["claude"]

    def test_success_with_unreadable_after_version_is_unreadable_not_updated(self):
        r = self.one(["1.0", None])
        self.assertEqual((r["before"], r["after"], r["result"], r["detail"]),
                         ("1.0", "unreadable", "unreadable", "version unreadable after update"))

    def test_success_with_unreadable_before_version_is_not_updated(self):
        r = self.one([None, "1.1"])
        self.assertEqual((r["before"], r["after"], r["result"], r["detail"]),
                         ("unreadable", "1.1", "unreadable", "version unreadable before update"))

    def test_success_with_equal_versions_is_current(self):
        r = self.one(["1.0", "1.0"])
        self.assertEqual((r["result"], r["detail"]), ("current", ""))

    def test_on_done_raising_is_recorded_and_never_escapes(self):
        tools = {"a": {"update": ["ainst", "x"], "version": ["va"], "runtime": "pi"},
                 "b": {"update": ["binst", "x"], "version": ["vb"], "runtime": "kimi"}}
        run = FakeRun(updates={("ainst", "x"): (0, 0, False), ("binst", "x"): (0, 0.2, False)},
                      versions={("va",): ["1", "2"], ("vb",): ["1", "2"]})
        seen = []

        def on_done(r):
            seen.append(r.tool)
            if r.tool == "a":
                raise RuntimeError("display broke")

        out = updates.update_all(cfg(tools), self.state, NOW, run=run, on_done=on_done)
        self.assertEqual(sorted(out), ["a", "b"])
        self.assertEqual(seen, ["a", "b"])
        self.assertEqual(out["a"]["result"], "updated")
        self.assertIn("on_done failed: RuntimeError: display broke", out["a"]["detail"])
        self.assertEqual((out["b"]["result"], out["b"]["detail"]), ("updated", ""))

    def test_installer_of(self):
        self.assertEqual(updates.installer_of({"update": ["/opt/homebrew/bin/brew", "upgrade"]}), "brew")
        self.assertEqual(updates.installer_of({}), "")

    def test_installer_of_without_update_is_empty(self):
        self.assertEqual(updates.installer_of({"app": "/Applications/X.app", "self_updating": True}), "")


class ReadVersion(unittest.TestCase):
    def test_version_argv_first_nonempty_line(self):
        run = FakeRun(versions={("t", "-V"): ["\n\n t 2.3 \nbuild"]})
        self.assertEqual(updates.read_version("t", {"version": ["t", "-V"]}, run=run), "t 2.3")

    def test_falls_back_to_adapter_version_argv(self):
        run = FakeRun(versions={("claude", "--version"): ["9.9"]})
        self.assertEqual(updates.read_version("claude", {}, run=run), "9.9")

    def test_unreadable(self):
        self.assertEqual(updates.read_version("foo", {}, run=FakeRun()), "unreadable")

    def test_missing_plist_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(updates.read_version("orca", {"app": d}, run=FakeRun()), "unreadable")


if __name__ == "__main__":
    unittest.main()
