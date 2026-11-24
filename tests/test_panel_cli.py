import importlib.machinery
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from lead.paths import load_config  # noqa: E402
from lead.projects import resolve_project  # noqa: E402
from panel import cli, roster, run, state  # noqa: E402
from seats import verify  # noqa: E402

FIX = HERE / "fixtures" / "panel"
FAKE = FIX / "fake_seat.py"
CONFIG = FIX / "config.json"

# `bin/bw` with the seat-verification probes patched out (they would run real version commands).
BW_SRC = """
import os, runpy, sys
root = sys.argv[1]
sys.path.insert(0, root)
from unittest import mock
from panel import roster
from seats import verify
with mock.patch.object(roster, "pending_verify", return_value=[]), mock.patch.object(verify, "spawn_verify", return_value=False):
    sys.argv = ["bw"] + sys.argv[2:]
    runpy.run_path(os.path.join(root, "bin", "bw"), run_name="__main__")
"""
HOLD_SRC = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)
fcntl.flock(fd, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(60)
"""


def wait_for(cond, timeout=15.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


class FakeJobs:
    def __init__(self, on_spawn=None):
        self.argvs, self.on_spawn = [], on_spawn

    def spawn(self, argv, log_path):
        self.argvs.append(argv)
        if self.on_spawn:
            self.on_spawn()
        return 99999991

    def alive(self, pid):
        return True

    def reap(self):
        pass


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(os.path.realpath(self.tmp.name))
        self.T, self.S = t, str(t / "state")
        self.OUT = t / "out"
        self.OUT.mkdir()
        self.wt = t / "wt"
        self.wt.mkdir()
        repo = ["git", "-C", str(self.wt), "-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(self.wt)], check=True)
        (self.wt / "widget.py").write_text("x = 1\n")
        subprocess.run(repo + ["add", "widget.py"], check=True)
        subprocess.run(repo + ["commit", "-q", "-m", "init"], check=True)
        self.env = mock.patch.dict(os.environ, {
            "BW_STATE_DIR": self.S, "BALANCEWHEEL_METRICS_LOG": str(t / "metrics.jsonl"),
            "BALANCEWHEEL_CONFIG": str(CONFIG), "BW_PANEL_SEAT_CMD": json.dumps([sys.executable, str(FAKE)]),
            "FAKE_OUT": str(self.OUT)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.CFG = load_config()
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.wt)
        self.project = resolve_project(str(self.wt), self.CFG)
        self.K = self.project.worktree_key
        for target, name, kw in ((run, "POLL_S", {"new": 0.02}), (roster, "pending_verify", {"return_value": []}),
                                 (verify, "spawn_verify", {"return_value": False})):
            p = mock.patch.object(target, name, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.children = []
        self.addCleanup(self.kill_all)

    def kill_all(self):
        pids = [c.pid for c in self.children]
        for f in self.OUT.glob("meta-*.json"):
            try:
                m = json.loads(f.read_text())
            except ValueError:
                continue
            pids += [p for p in (m.get("pid"), m.get("child")) if p]
        for pid in pids:
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        for c in self.children:
            c.wait()
            if c.stdout:
                c.stdout.close()

    def cli(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, self.CFG, self.S, out=out, err=err, **kw)
        return code, out.getvalue(), err.getvalue()

    def panel(self, pid):
        return state.load(self.S, self.K, pid)

    def ids(self, pid):
        doc = json.loads(Path(state.work_dir(self.S, self.K, pid), "findings.json").read_text())
        return [m["id"] for g in doc["groups"] for m in g["members"]]

    def ready(self, checkpoint="design"):
        code, out, _ = self.cli([checkpoint, "t"])
        self.assertEqual(code, 0)
        return re.search(r"panel (p[0-9a-f]{7})", out)[1]

    def test_foreground_panel_then_log(self):
        code, out, _ = self.cli(["design", "t"])
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^bw: panel p[0-9a-f]{7} ready \((short: .+|full)\) — next steps in .*/next\.md\n$")
        pid = re.search(r"panel (p[0-9a-f]{7})", out)[1]
        self.assertEqual(self.panel(pid)["state"], "ready")
        self.assertFalse(state.has_job(self.S, self.K))
        verdicts = {f: "confirmed" for f in self.ids(pid)}
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"verdicts": verdicts}))):
            code, out2, _ = self.cli(["log", pid])
        self.assertEqual(code, 0)
        self.assertIn("Design panel", out2)
        self.assertEqual(self.panel(pid)["state"], "logged")

    def test_code_review_alias(self):
        code, out, _ = self.cli(["code-review", "t"])
        self.assertEqual(code, 0)
        pid = re.search(r"panel (p[0-9a-f]{7})", out)[1]
        self.assertEqual(self.panel(pid)["checkpoint"], "review")

    def test_refusal_exits_2(self):
        code, out, err = self.cli(["nonsense", "t"])
        self.assertEqual((code, out), (2, ""))
        self.assertTrue(err.startswith("bw panel: checkpoint"), err)
        self.assertEqual(state.panels(self.S, self.K), [])
        code, _, err = self.cli(["design", "--stakes", "extreme"])
        self.assertEqual(code, 2)
        self.assertIn("bw panel: usage error", err)

    def test_background_runs(self):
        seen = {}

        def at_spawn():
            (p,) = state.panels(self.S, self.K)
            seen.update(p["job"])
        jobs = FakeJobs(at_spawn)
        code, out, _ = self.cli(["design", "t", "--background"], jobs=jobs)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^bw: panel p[0-9a-f]{7} running in the background\n$")
        self.assertEqual(len(jobs.argvs), 1)
        self.assertIsNone(seen["pid"])  # reserved before the spawn
        self.assertEqual(seen["kind"], "round1")
        (p,) = state.panels(self.S, self.K)
        self.assertEqual(p["job"]["pid"], 99999991)
        self.assertEqual(p["state"], "queued")

    def hold_lock(self):
        os.makedirs(os.path.join(self.S, "locks"), exist_ok=True)
        child = subprocess.Popen([sys.executable, "-c", HOLD_SRC, os.path.join(self.S, "locks", f"{self.K}.lock")],
                                 stdout=subprocess.PIPE, text=True)
        self.children.append(child)
        self.assertEqual(child.stdout.readline().strip(), "held")
        return child

    def test_background_queues_only_for_a_bw_lead(self):
        first = state.create(self.S, self.K, {"worktree": str(self.wt), "profile": "p", "checkpoint": "design",
                                              "target": "t", "roster": [], "seats": {}})
        self.assertTrue(state.reserve_job(self.S, self.K, first["id"], {"kind": "round1", "pid": os.getpid()}))
        child = self.hold_lock()
        jobs = FakeJobs()
        code, out, _ = self.cli(["design", "t", "--background"], jobs=jobs)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"queued; bw lead starts it next")
        self.assertEqual((len(state.panels(self.S, self.K)), jobs.argvs), (2, []))
        child.kill()
        child.wait()
        code, out, err = self.cli(["design", "t", "--background"], jobs=jobs)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no bw lead runs here", err)
        self.assertEqual(len(state.panels(self.S, self.K)), 2)  # the refusal left no panel

    def test_foreground_reserves_and_releases(self):
        real, seen, nested = run.round1, {}, {}

        def spy(config, st, key, pid, *a, **k):
            seen["has_job"] = state.has_job(st, key)
            seen["job"] = dict(state.load(st, key, pid)["job"])
            nested["r"] = self.cli(["design", "t"])
            nested["n"] = len(state.panels(st, key))
            return real(config, st, key, pid, *a, **k)
        with mock.patch.object(run, "round1", spy):
            code, _, _ = self.cli(["design", "t"])
        self.assertEqual(code, 0)
        self.assertTrue(seen["has_job"])
        self.assertEqual((seen["job"]["owner"], seen["job"]["pid"]), ("foreground", os.getpid()))
        self.assertGreater(seen["job"]["deadline"], seen["job"]["started"])
        self.assertEqual(nested["r"][0], 2)
        self.assertIn("a panel job is running", nested["r"][2])
        self.assertEqual(nested["n"], 1)  # the refusal left no second panel
        self.assertFalse(state.has_job(self.S, self.K))

    def test_foreground_reservation_lost_in_the_race_leaves_the_panel_queued(self):
        with mock.patch.object(state, "reserve_job", return_value=False):
            code, _, err = self.cli(["design", "t"])
        self.assertEqual(code, 2)
        self.assertIn("a panel job is running", err)
        (p,) = state.panels(self.S, self.K)
        self.assertEqual(p["state"], "queued")

    def test_foreground_sighup_fails_cleanly(self):
        env = {**os.environ, "FAKE_SEAT_a": "sleep", "FAKE_SEAT_o": "sleep", "FAKE_SEAT_g": "sleep"}
        proc = subprocess.Popen([sys.executable, "-c", BW_SRC, str(ROOT), "panel", "design", "t"], cwd=self.wt, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.children.append(proc)
        if not wait_for(lambda: len(list(self.OUT.glob("meta-*.json"))) >= 2):
            proc.kill()
            self.fail("no seat started: %r" % (proc.communicate(),))
        proc.send_signal(signal.SIGHUP)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1, (out, err))
        (p,) = state.panels(self.S, self.K)
        self.assertEqual((p["state"], p["error"]), ("failed", "stopped"))
        self.assertIsNone(p["job"])
        self.assertIn("failed", err)

    def test_ask_claim_cap_on_cli(self):
        code, out, err = self.cli(["ask", "p1234567", "a", "x" * 1501])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("over 1500", err)
        code, _, err = self.cli(["ask", "p1234567", "a", "claim", "--dispute", "../x"])
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("bw panel:"))
        code, _, err = self.cli(["ask", "p1234567", "a", "claim"])  # a panel that is not here
        self.assertEqual(code, 2)
        self.assertIn("unknown panel", err)

    def test_foreground_ask(self):
        pid = self.ready()
        code, out, err = self.cli(["ask", pid, "a", "Is the loop bound wrong?"])
        self.assertEqual(code, 0, err)
        answer = re.search(r"read (\S+)", out)[1]
        self.assertTrue(os.path.isfile(answer), out)
        self.assertEqual(answer, os.path.join(state.view_dir(self.S, self.K, pid), "asks", "d1-1-a-1.md"))
        self.assertFalse(state.has_job(self.S, self.K))
        self.assertEqual(self.panel(pid)["asks"][0]["state"], "done")
        state.reserve_job(self.S, self.K, pid, {"kind": "round1", "pid": os.getpid()})
        code, _, err = self.cli(["ask", pid, "a", "again"])
        self.assertEqual(code, 2)
        self.assertIn("a panel job is running", err)
        self.assertEqual(len(self.panel(pid)["asks"]), 1)  # the refusal queued nothing

    def test_status_lines(self):
        self.assertEqual(self.cli(["status"]), (0, "", ""))
        pid = self.ready()
        code, out, _ = self.cli(["status"])
        self.assertEqual(code, 0)
        view = state.view_dir(self.S, self.K, pid)
        self.assertRegex(out, rf"^{pid}  design  ready( \(short\))?  {re.escape(view)}\n$")
        code, out, _ = self.cli(["status", pid])
        self.assertEqual(json.loads(out)["state"], "ready")
        code, _, err = self.cli(["status", "pffffff0"])
        self.assertEqual(code, 2)
        self.assertIn("unknown panel", err)

    def test_usage_map_lists_panel_subcommands(self):
        spec = importlib.util.spec_from_file_location("usage_gen", ROOT / "docs" / "usage-map" / "generate.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        ids = {i["id"] for items in gen.commands().values() for i in items}
        committed = (ROOT / "docs" / "usage-map" / "data.generated.js").read_text()
        for cid in ("cmd:panel", "cmd:panel.ask", "cmd:panel.log", "cmd:panel.amend", "cmd:panel.status"):
            self.assertIn(cid, ids)
            self.assertIn(cid, committed)

    def test_bw_dispatch(self):
        loader = importlib.machinery.SourceFileLoader("bw_cli_panel", str(ROOT / "bin" / "bw"))
        spec = importlib.util.spec_from_loader("bw_cli_panel", loader)
        bw = importlib.util.module_from_spec(spec)
        loader.exec_module(bw)
        self.assertIn("panel", bw.COMMANDS)
        self.assertIn("  bw panel <checkpoint>", bw.__doc__)
        with mock.patch.object(bw.panel_cli, "main", return_value=7) as m, \
                mock.patch.object(bw, "load_config", return_value=self.CFG):
            self.assertEqual(bw.main(["panel", "status", "--x"]), 7)
        self.assertEqual(m.call_args[0][0], ["status", "--x"])


class WaitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(os.path.realpath(self.tmp.name))
        self.helper = self.root / "bw-panel-wait"
        shutil.copy(ROOT / "bin" / "bw-panel-wait", self.helper)

    def put(self, name, st, **extra):
        d = self.root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps({"state": st, **extra}))

    def wait(self, *args):
        r = subprocess.run([sys.executable, str(self.helper), *args], capture_output=True, text=True, timeout=30)
        return r.returncode, r.stdout.splitlines(), r.stderr

    def test_wait_helper(self):
        self.put("p1111111", "running")
        code, lines, _ = self.wait("--max", "1")
        self.assertEqual((code, lines), (3, ["p1111111 running", "still working; run this command again"]))
        self.put("p2222222", "ready", busy=False)
        code, lines, _ = self.wait()
        self.assertEqual(code, 0)
        self.assertIn(f"p2222222 ready — follow {self.root}/p2222222/next.md", lines)
        self.assertIn("p1111111 running", lines)
        self.put("p2222222", "ready", busy=True)
        code, lines, _ = self.wait("--max", "1")
        self.assertEqual((code, lines[-1]), (3, "still working; run this command again"))
        self.assertIn("p2222222 running", lines)
        self.put("p3333333", "failed", busy=False)
        code, lines, _ = self.wait()
        self.assertEqual(code, 0)
        self.assertTrue(any(line.startswith("p3333333 failed — follow ") for line in lines))

    def test_nothing_open(self):
        self.assertEqual(self.wait(), (0, [], ""))
        self.put("p4444444", "logged")
        self.assertEqual(self.wait(), (0, [], ""))

    def test_a_queued_panel_counts_as_working(self):
        self.put("p5555555", "queued")
        code, lines, _ = self.wait("--max", "1")
        self.assertEqual((code, lines[0]), (3, "p5555555 queued"))

    def test_other_dir_names_are_never_read(self):
        for name in ("px", "p123456", "p12345678", "P1111111", "p111111g"):
            self.put(name, "failed")
        self.assertEqual(self.wait(), (0, [], ""))
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "status.json").write_text(json.dumps({"state": "failed"}))
        os.symlink(outside, self.root / "p6666666")  # a link is not followed
        self.assertEqual(self.wait(), (0, [], ""))

    def test_bad_status_is_ignored(self):
        d = self.root / "p7777777"
        d.mkdir()
        (d / "status.json").write_text("not json")
        self.assertEqual(self.wait(), (0, [], ""))

    def test_usage(self):
        code, _, err = self.wait("p1111111")
        self.assertEqual(code, 2)
        self.assertIn("usage", err)


if __name__ == "__main__":
    unittest.main()
