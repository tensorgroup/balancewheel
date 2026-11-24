import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from lead.paths import load_config  # noqa: E402
from panel import merge, render, roster, run, state  # noqa: E402
from seats import verify  # noqa: E402
import store  # noqa: E402  (metrics/, on sys.path through lead.paths)

FIX = HERE / "fixtures" / "panel"
FAKE = FIX / "fake_seat.py"
CONFIG = FIX / "config.json"
K = "wt-k"
JOB = {"kind": "round1", "pid": None, "owner": "scheduler", "started": 0.0, "deadline": 1e12, "beat": 0.0,
       "seat_pgids": []}

# Runs the real worker CLI with pending_verify patched (it would run real version commands).
WORKER = """
import sys
sys.path.insert(0, sys.argv[1])
from unittest import mock
from panel import roster, run
with mock.patch.object(roster, "pending_verify", return_value=[]):
    sys.exit(run.main(sys.argv[2:]))
"""


def _alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class PanelRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.S = str(t / "state")
        self.OUT = t / "out"
        self.OUT.mkdir()
        self.LOG = str(t / "metrics.jsonl")
        self.wt = t / "wt"
        self.wt.mkdir()
        git = ["git", "-C", str(self.wt), "-c", "user.email=t@example.com", "-c", "user.name=t"]
        subprocess.run(["git", "init", "-q", str(self.wt)], check=True)
        (self.wt / "widget.py").write_text("x = 1\n")
        subprocess.run(git + ["add", "widget.py"], check=True)
        subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
        env = mock.patch.dict(os.environ, {
            "BW_STATE_DIR": self.S, "BALANCEWHEEL_METRICS_LOG": self.LOG, "BALANCEWHEEL_CONFIG": str(CONFIG),
            "BW_PANEL_SEAT_CMD": json.dumps([sys.executable, str(FAKE)]), "FAKE_OUT": str(self.OUT)})
        env.start()
        self.addCleanup(env.stop)
        self.CFG = load_config()
        for name, value in (("POLL_S", 0.02), ("VERIFY_POLL_S", 0.05)):
            p = mock.patch.object(run, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.pending = mock.patch.object(roster, "pending_verify", return_value=[])
        self.pending.start()
        self.addCleanup(self.pending.stop)
        self.sv = mock.patch.object(verify, "spawn_verify", return_value=False)
        self.spawn = self.sv.start()
        self.addCleanup(self.sv.stop)
        self.pid = self.new_panel()

    def new_panel(self, tier="standard", checkpoint="design", config=None):
        config = config or self.CFG
        names = roster.roster(config, config["profiles"]["p"], checkpoint, tier)
        p = state.create(self.S, K, {
            "worktree": str(self.wt), "profile": "p", "checkpoint": checkpoint, "target": "widget.py",
            "focus": "", "stakes": "normal", "paths": [], "tier": tier, "roster": names,
            "originals": names, "fallbacks": [], "session_id": "sess-1", "lead": None})
        self.assertTrue(state.reserve_job(self.S, K, p["id"], JOB))
        return p["id"]

    def seats_json(self, probes):
        entries = {s: {"verified": probe is None, "runtime_version": "1", "verified_at": None, "failed_probe": probe}
                   for s, probe in probes.items()}
        Path(self.S).mkdir(parents=True, exist_ok=True)
        Path(self.S, "seats.json").write_text(json.dumps(entries))

    def argvs(self, seat="*"):
        return [json.loads(f.read_text()) for f in sorted(self.OUT.glob(f"seat-{seat}-*.json"))]

    def last_argv(self, seat):
        files = sorted(self.OUT.glob(f"seat-{seat}-*.json"), key=lambda f: int(f.stem.rsplit("-", 1)[1]))
        return json.loads(files[-1].read_text())

    def metas(self):
        return [json.loads(f.read_text()) for f in sorted(self.OUT.glob("meta-*.json"))]

    def view(self, pid=None):
        return Path(state.view_dir(self.S, K, pid or self.pid))

    def round1(self, env=None, pid=None, config=None):
        with mock.patch.dict(os.environ, env or {}):
            return run.round1(config or self.CFG, self.S, K, pid or self.pid)

    # --- round 1 ---

    def test_round1_all_ok(self):
        doc = self.round1()
        self.assertEqual((doc["short"], doc["findings_raw"]), (False, 6))
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["seats"]["a"]["session_id"]), ("ready", "s-a"))
        self.assertEqual((p["seats"]["a"]["model"], p["seats"]["a"]["status"]), ("model-a", "ok"))
        self.assertTrue((self.view() / "next.md").exists())
        self.assertEqual({f.name for f in (self.view() / "seats").iterdir()}, {"a.md", "o.md", "g.md"})
        saved = json.loads(Path(state.work_dir(self.S, K, self.pid), "findings.json").read_text())
        self.assertEqual(saved, doc)
        self.assertEqual(len(store.load(self.LOG)["panel_runs"]), 1)
        self.assertEqual(p["job"], None)

    def test_same_brief_bytes_and_flags(self):
        self.round1()
        argvs = self.argvs()
        self.assertEqual(len(argvs), 3)
        brief = os.path.join(state.work_dir(self.S, K, self.pid), "brief.md")
        self.assertEqual({a[a.index("--brief-file") + 1] for a in argvs}, {brief})
        self.assertTrue(all("--no-fallback" in a for a in argvs))
        self.assertTrue(all(a[-1] == render.ROUND1_PROMPT and a[-2] == "--" for a in argvs))
        self.assertTrue(all(a[a.index("-d") + 1] == str(self.wt) for a in argvs))
        self.assertIn("Target: widget.py", Path(brief).read_text())

    def test_seat_argv_order(self):
        with mock.patch.dict(os.environ, {"BW_PANEL_SEAT_CMD": json.dumps(["x", "y"])}):
            self.assertEqual(run.seat_argv("o", "-p", worktree="/w", out="/o", result="/r", session="s1", deep=True),
                             ["x", "y", "o", "--no-fallback", "-d", "/w", "--out", "/o", "--result", "/r",
                              "--session", "s1", "--deep", "--", "-p"])
        with mock.patch.dict(os.environ):
            del os.environ["BW_PANEL_SEAT_CMD"]
            self.assertEqual(run.seat_cmd(), [sys.executable, os.path.join(run.ROOT, "bin", "bw"), "seat"])

    def test_capped_seat_gets_stand_in_and_panel_is_short(self):
        doc = self.round1({"FAKE_SEAT_a": "cap"})
        self.assertEqual(doc["absent"], {"a": "usage cap"})
        self.assertEqual(doc["seats"]["g"]["stand_in_for"], "a")
        self.assertTrue(doc["short"])
        self.assertEqual(len(self.argvs("g")), 1)   # g is on the roster: it runs once
        self.assertFalse((self.view() / "seats" / "a.md").exists())

    def test_standin_off_roster_is_launched(self):
        pid = self.new_panel_after_release(checkpoint="review")
        doc = self.round1({"FAKE_SEAT_a": "cap"}, pid=pid)
        self.assertEqual(doc["seats"]["g"]["stand_in_for"], "a")
        self.assertEqual((doc["seats"]["g"]["status"], doc["seats"]["g"]["on_roster"]), ("ok", False))
        self.assertEqual(len(self.argvs("g")), 1)
        self.assertIn("--no-fallback", self.last_argv("g"))

    def new_panel_after_release(self, **kw):
        state.release_job(self.S, K, self.pid)
        return self.new_panel(**kw)

    def test_precap_is_a_cap(self):
        pid = self.new_panel_after_release(checkpoint="review")
        doc = self.round1({"FAKE_SEAT_a": "precap"}, pid=pid)
        self.assertEqual(doc["absent"], {"a": "usage cap"})
        self.assertEqual(doc["seats"]["g"]["stand_in_for"], "a")
        self.assertEqual(len(self.argvs("g")), 1)

    def test_timeout_is_absent(self):
        doc = self.round1({"FAKE_SEAT_o": "timeout"})
        self.assertEqual(doc["absent"], {"o": "timeout"})

    def test_part_file_removed_on_failure(self):
        self.round1({"FAKE_SEAT_o": "timeout"})
        names = {f.name for f in (self.view() / "seats").iterdir()}
        self.assertEqual(names, {"a.md", "g.md"})

    def test_unverified_spawns_verify_once(self):
        self.seats_json({"a": None, "o": "key changed", "g": "never verified"})
        doc = self.round1({"FAKE_SEAT_o": "unverified", "FAKE_SEAT_g": "unverified"})
        self.assertEqual(doc["absent"], {"o": "unverified", "g": "unverified"})
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.spawn.call_args.args[2], ["o", "g"])
        self.assertIn("spawn_verify", Path(state.work_dir(self.S, K, self.pid), "log.txt").read_text())

    def test_recorded_failure_not_verified_again(self):
        self.seats_json({"a": None, "o": "write-file", "g": None})
        doc = self.round1({"FAKE_SEAT_o": "unverified"})
        self.assertEqual(doc["absent"], {"o": "unverified"})
        self.spawn.assert_not_called()

    def test_no_spawn_verify_for_version_unreadable(self):
        self.seats_json({"a": None, "o": "never verified", "g": None})
        doc = self.round1({"FAKE_SEAT_o": "reason:unverified: codex version unreadable"})
        self.assertEqual(doc["absent"], {"o": "unverified: codex version unreadable"})
        self.spawn.assert_not_called()

    def test_reason_decides_not_the_word_rate_limit(self):
        pid = self.new_panel_after_release(checkpoint="review")
        doc = self.round1({"FAKE_SEAT_a": "ratelimit"}, pid=pid)
        self.assertEqual(doc["absent"], {"a": "error: 429 too many requests"})
        self.assertNotIn("g", doc["seats"])
        self.assertEqual(self.argvs("g"), [])

    def test_unknown_reason_absent_with_its_text(self):
        pid = self.new_panel_after_release(checkpoint="review")
        doc = self.round1({"FAKE_SEAT_a": "reason:no login: export OPENROUTER_API_KEY"}, pid=pid)
        self.assertEqual(doc["absent"], {"a": "no login: export OPENROUTER_API_KEY"})
        self.assertEqual(self.argvs("g"), [])

    def test_missing_brief_file_is_absent_no_result(self):
        real = run._write_text

        def write_then_lose(path, text):
            real(path, text)
            if path.endswith("brief.md"):
                os.unlink(path)

        with mock.patch.object(run, "_write_text", side_effect=write_then_lose):
            doc = self.round1()
        self.assertEqual(doc["absent"], {"a": "error: no result", "o": "error: no result", "g": "error: no result"})
        self.assertIn("Traceback", Path(state.work_dir(self.S, K, self.pid), "log.txt").read_text())

    def test_dash_prompt_passes_after_double_dash(self):
        with mock.patch.object(render, "ROUND1_PROMPT", "-x"):
            doc = self.round1({"FAKE_SEAT_o": "echo"})
        self.assertEqual(doc["seats"]["o"]["status"], "ok")
        self.assertEqual((self.view() / "seats" / "o.md").read_text().strip(), "-x")

    def test_seat_env_has_bw_state_dir(self):
        with mock.patch.dict(os.environ):
            del os.environ["BW_STATE_DIR"]
            self.round1()
        self.assertEqual({m["state_dir"] for m in self.metas()}, {self.S})

    def test_hostile_repo_is_absent(self):
        doc = self.round1({"FAKE_SEAT_o": "hostile"})
        self.assertTrue(doc["absent"]["o"].startswith("hostile: "))

    def test_all_absent_ready_and_short(self):
        self.seats_json({"a": "never verified", "o": "never verified", "g": "never verified"})
        doc = self.round1({"FAKE_SEAT_a": "unverified", "FAKE_SEAT_o": "unverified", "FAKE_SEAT_g": "unverified"})
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["short"], doc["findings_raw"]), ("ready", True, 0))
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.spawn.call_args.args[2], ["a", "o", "g"])

    def test_deep_adds_reserved_and_flag(self):
        pid = self.new_panel_after_release(tier="deep")
        doc = self.round1(pid=pid)
        self.assertEqual(doc["seats"]["r"]["status"], "ok")
        argvs = self.argvs()
        self.assertEqual(len(argvs), 4)
        self.assertTrue(all("--deep" in a for a in argvs))

    def test_max_parallel(self):
        config = json.loads(json.dumps(self.CFG))
        config["panel"]["max_parallel"] = 1
        self.round1({"FAKE_DELAY": "0.3"}, config=config)
        spans = sorted((m["t0"], m["t1"]) for m in self.metas())
        self.assertEqual(len(spans), 3)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertGreaterEqual(start, end)

    def _run_with_sleeping_o(self):
        """Round 1 in a thread with seat o asleep; returns (thread, result holder, o's meta)."""
        out = {}
        env = {"FAKE_SEAT_o": "sleep"}

        def go():
            with mock.patch.dict(os.environ, env):
                out["doc"] = run.round1(self.CFG, self.S, K, self.pid)

        with mock.patch.dict(os.environ, env):
            th = threading.Thread(target=go)
            th.start()
            seats = self.view() / "seats"
            deadline = time.time() + 20
            meta = None
            while time.time() < deadline:
                f = self.OUT / "meta-o-1.json"
                meta = json.loads(f.read_text()) if f.exists() else None
                if meta and meta["child"] and (seats / "a.md").exists() and (seats / "g.md").exists():
                    break
                time.sleep(0.02)
        return th, out, meta

    def test_no_seat_file_until_its_run_ends(self):
        th, out, meta = self._run_with_sleeping_o()
        seats = self.view() / "seats"
        self.assertFalse((seats / "o.md").exists())
        p = state.load(self.S, K, self.pid)
        self.assertEqual(p["job"]["seat_pgids"], [meta["pid"]])
        self.assertEqual(p["seats"]["o"]["status"], "running")
        os.killpg(meta["pid"], signal.SIGTERM)
        th.join(20)
        self.assertFalse(th.is_alive())
        self.assertFalse((seats / "o.md").exists())

    def test_interrupted_seat_alone(self):
        th, out, meta = self._run_with_sleeping_o()
        os.killpg(meta["pid"], signal.SIGTERM)
        th.join(20)
        self.assertEqual(out["doc"]["absent"], {"o": "interrupted"})
        self.assertEqual(state.load(self.S, K, self.pid)["state"], "ready")
        self.assertFalse(_alive(meta["child"]))

    def test_interrupted_while_stopping_takes_stop_reason(self):
        th, out, meta = self._run_with_sleeping_o()
        state.update(self.S, K, self.pid, lambda p: p["job"].update(stop="stopped after 1 min"))
        os.killpg(meta["pid"], signal.SIGTERM)   # the watchdog's SIGTERM reaches the seat first
        th.join(20)
        self.assertEqual(out["doc"]["absent"], {"o": "panel failed: stopped after 1 min"})

    def test_mid_run_agy_stand_in_refused_while_agy_runs(self):
        config = json.loads(json.dumps(self.CFG))
        config["seats"]["y"] = dict(config["seats"]["g"], runtime="agy")
        config["seats"]["z"] = dict(config["seats"]["g"], runtime="agy")
        config["seats"]["a"]["fallback"] = "z"
        config["profiles"]["p"]["checkpoints"]["review"]["seats"] = ["a", "y"]
        pid = self.new_panel_after_release(checkpoint="review", config=config)
        out = {}
        env = {"FAKE_SEAT_a": "cap", "FAKE_SEAT_y": "sleep"}

        def go():
            with mock.patch.dict(os.environ, env):
                out["doc"] = run.round1(config, self.S, K, pid)

        th = threading.Thread(target=go)
        th.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            seats = (state.load(self.S, K, pid) or {}).get("seats") or {}
            f = self.OUT / "meta-y-1.json"
            if (seats.get("a") or {}).get("status") == "absent" and f.exists() and json.loads(f.read_text())["child"]:
                break
            time.sleep(0.02)
        self.assertEqual(state.load(self.S, K, pid)["seats"]["y"]["status"], "running")
        os.killpg(json.loads((self.OUT / "meta-y-1.json").read_text())["pid"], signal.SIGTERM)
        th.join(20)
        self.assertEqual(out["doc"]["absent"], {"a": "usage cap", "y": "interrupted"})
        self.assertNotIn("z", out["doc"]["seats"])
        self.assertEqual(self.argvs("z"), [])

    def test_failed_write_falls_back_to_a_minimal_failed_state(self):
        real, calls = run._write_text, []

        def fail_once(path, text):
            if path.endswith("findings.json") and not calls:
                calls.append(path)
                raise OSError("disk full")
            real(path, text)

        with mock.patch.object(merge, "build", side_effect=RuntimeError("boom")), \
                mock.patch.object(run, "_write_text", side_effect=fail_once):
            doc = self.round1()
        self.assertEqual(len(calls), 1)
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["error"], doc["failed"]), ("failed", "RuntimeError", "RuntimeError"))
        self.assertFalse(state.has_job(self.S, K))
        self.assertEqual(len(store.load(self.LOG)["panel_runs"]), 1)

    def test_fallback_never_raises(self):
        real = run._write_text

        def write_nothing_out(path, text):   # the brief still writes; findings.json and next.md never do
            if path.endswith("brief.md"):
                return real(path, text)
            raise OSError("disk full")

        with mock.patch.object(merge, "build", side_effect=RuntimeError("boom")), \
                mock.patch.object(run, "_write_text", side_effect=write_nothing_out), \
                mock.patch.object(state, "record_run", side_effect=OSError("log full")):
            doc = self.round1()
        self.assertEqual(doc["failed"], "RuntimeError")
        self.assertEqual(state.load(self.S, K, self.pid)["state"], "failed")
        self.assertFalse(state.has_job(self.S, K))

    def test_keyboard_interrupt_fails_the_panel_and_stops_seats(self):
        with mock.patch.object(run, "classify", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            self.round1({"FAKE_SEAT_o": "sleep"})
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["error"]), ("failed", "KeyboardInterrupt"))
        self.assertEqual({e["status"] for e in p["seats"].values()}, {"absent"})
        self.assertIsNone(p["job"])
        for m in self.metas():
            self.assertFalse(_alive(m["pid"]))

    def test_exception_marks_failed(self):
        with mock.patch.object(merge, "build", side_effect=RuntimeError("boom")):
            doc = self.round1()
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["error"]), ("failed", "RuntimeError"))
        saved = json.loads(Path(state.work_dir(self.S, K, self.pid), "findings.json").read_text())
        self.assertEqual((saved["failed"], doc["failed"]), ("RuntimeError", "RuntimeError"))
        self.assertTrue((self.view() / "next.md").exists())
        self.assertFalse(state.has_job(self.S, K))
        self.assertEqual(len(store.load(self.LOG)["panel_runs"]), 1)

    def test_exception_while_seats_run_marks_them_absent(self):
        with mock.patch.object(run, "classify", side_effect=RuntimeError("boom")):
            doc = self.round1({"FAKE_SEAT_o": "sleep"})
        self.assertEqual(set(doc["absent"].values()), {"panel failed: RuntimeError"})
        self.assertEqual(set(doc["absent"]), {"a", "o", "g"})
        self.assertEqual(state.load(self.S, K, self.pid)["state"], "failed")
        for m in self.metas():
            self.assertFalse(_alive(m["pid"]))
            if m["child"]:
                self.assertFalse(_alive(m["child"]))
        self.assertEqual(list((self.view() / "seats").iterdir()), [])

    def test_job_released_after_ready(self):
        self.round1()
        self.assertFalse(state.has_job(self.S, K))

    def test_first_verification_wait(self):
        self.seats_json({"a": None, "o": "never verified", "g": None})
        calls = []

        def running(_state):
            calls.append(1)
            if len(calls) == 2:
                self.seats_json({"a": None, "o": None, "g": None})
            return True

        with mock.patch.object(roster, "pending_verify", return_value=["o"]) as pv, \
                mock.patch.object(roster, "verify_running", side_effect=running):
            self.spawn.return_value = True
            doc = self.round1()
        self.assertEqual(pv.call_args.args[2], ["a", "o", "g"])
        self.assertEqual(doc["seats"]["o"]["status"], "ok")
        self.assertEqual(self.spawn.call_count, 1)
        self.assertEqual(self.spawn.call_args.args[2], ["o"])
        self.assertEqual(len(calls), 2)

    def test_wait_bounded_by_cap(self):
        self.seats_json({"a": None, "o": "never verified", "g": None})
        with mock.patch.object(roster, "pending_verify", return_value=["o"]), \
                mock.patch.object(roster, "verify_running", return_value=True), \
                mock.patch.object(run, "cap_seconds", return_value=1):
            t0 = time.time()
            doc = self.round1({"FAKE_SEAT_o": "unverified"})
        self.assertGreaterEqual(time.time() - t0, 1.0)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(doc["absent"], {"o": "unverified"})
        self.assertEqual(self.spawn.call_args_list[0].args[2], ["o"])

    def test_no_wait_after_a_recorded_failure(self):
        with mock.patch.object(roster, "verify_running") as vr:
            self.round1()
        vr.assert_not_called()
        self.spawn.assert_not_called()

    # --- ask ---

    def add_ask(self, ask_id, seat, claim, pid=None):
        pid = pid or self.pid
        state.update(self.S, K, pid, lambda p: p["asks"].append(
            {"id": ask_id, "dispute": "d1", "round": 1, "seat": seat, "attempt": 1, "claim": claim,
             "state": "queued", "reason": None, "session_id": None, "ts": 0.0}))
        self.assertTrue(state.reserve_job(self.S, K, pid, dict(JOB, kind="ask", ask=ask_id)))

    def test_ask_resumes_round1_session(self):
        self.round1()
        self.add_ask("d1-1-o-1", "o", "X")
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "echo"}):
            entry = run.ask(self.CFG, self.S, K, self.pid, "d1-1-o-1")
        argv = self.last_argv("o")
        self.assertEqual(argv[argv.index("--session") + 1], "s-o")
        self.assertNotIn("--brief-file", argv)
        self.assertNotIn("--deep", argv)
        self.assertEqual(argv[-1], render.ASK_FRAME.format(claim="X"))
        self.assertEqual((self.view() / "asks" / "d1-1-o-1.md").read_text().strip(), render.ASK_FRAME.format(claim="X"))
        self.assertEqual((entry["state"], entry["reason"]), ("done", "ok"))
        p = state.load(self.S, K, self.pid)
        self.assertEqual(p["asks"][0]["state"], "done")
        self.assertEqual(p["state"], "ready")
        self.assertFalse(state.has_job(self.S, K))

    def test_ask_on_deep_panel_passes_deep(self):
        pid = self.new_panel_after_release(tier="deep")
        self.round1(pid=pid)
        self.add_ask("d1-1-o-1", "o", "X", pid=pid)
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "echo"}):
            run.ask(self.CFG, self.S, K, pid, "d1-1-o-1")
        self.assertIn("--deep", self.last_argv("o"))

    def test_ask_failure_sets_failed_and_drops_answer(self):
        self.round1()
        self.add_ask("d1-1-o-1", "o", "X")
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "timeout"}):
            entry = run.ask(self.CFG, self.S, K, self.pid, "d1-1-o-1")
        self.assertEqual((entry["state"], entry["reason"]), ("failed", "timeout"))
        self.assertFalse((self.view() / "asks" / "d1-1-o-1.md").exists())
        self.assertTrue((self.view() / "seats" / "o.md").exists())   # round 1's file is not touched
        self.assertFalse(state.has_job(self.S, K))

    def test_ask_without_round1_session_is_refused(self):
        self.round1({"FAKE_SEAT_o": "nosession"})
        self.add_ask("d1-1-o-1", "o", "X")
        entry = run.ask(self.CFG, self.S, K, self.pid, "d1-1-o-1")
        self.assertEqual((entry["state"], entry["reason"]), ("failed", "error: no round-1 session"))
        self.assertEqual(len(self.argvs("o")), 1)   # no resume was started
        self.assertFalse(state.has_job(self.S, K))

    def test_ask_claim_over_limit_is_refused(self):
        self.round1()
        self.add_ask("d1-1-o-1", "o", "x" * 1501)
        entry = run.ask(self.CFG, self.S, K, self.pid, "d1-1-o-1")
        self.assertEqual((entry["state"], entry["reason"]), ("failed", "error: bad claim"))
        self.assertEqual(len(self.argvs("o")), 1)

    def test_main_resets_the_stop_flag(self):
        def stop_then_run(*a, **k):
            run._stop["why"] = "stopped"
            return {"failed": "stopped"}

        with mock.patch.object(run, "round1", side_effect=stop_then_run):
            self.assertEqual(run.main(["round1", "--state", self.S, "--key", K, "--panel", self.pid]), 1)
        self.assertIsNone(run._stop["why"])

    def test_ask_bad_id_refused_and_job_released(self):
        self.round1()
        self.assertTrue(state.reserve_job(self.S, K, self.pid, dict(JOB, kind="ask", ask="../x")))
        self.assertRaises(ValueError, run.ask, self.CFG, self.S, K, self.pid, "../x")
        self.assertFalse(state.has_job(self.S, K))
        self.assertEqual(len(self.argvs("o")), 1)

    # --- signals and classify ---

    def test_sigterm_stops_seats_and_fails(self):
        env = dict(os.environ, FAKE_SEAT_a="sleep", FAKE_SEAT_o="sleep", FAKE_SEAT_g="sleep")
        proc = subprocess.Popen([sys.executable, "-c", WORKER, str(ROOT), "round1", "--state", self.S, "--key", K,
                                 "--panel", self.pid], env=env, cwd=str(ROOT), start_new_session=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.time() + 20
        while time.time() < deadline:
            metas = self.metas()
            if len(metas) == 3 and all(m["child"] for m in metas):
                break
            time.sleep(0.05)
        self.assertEqual(len(self.metas()), 3)
        proc.send_signal(signal.SIGTERM)
        _, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 1, err.decode())
        p = state.load(self.S, K, self.pid)
        self.assertEqual((p["state"], p["error"]), ("failed", "stopped"))
        self.assertEqual({s: e["reason"] for s, e in p["seats"].items()},
                         {s: "panel failed: stopped" for s in ("a", "o", "g")})
        self.assertIsNone(p["job"])
        for m in self.metas():
            self.assertFalse(_alive(m["pid"]))
            self.assertFalse(_alive(m["child"]))

    def test_main_usage(self):
        with mock.patch("sys.stderr"):
            self.assertRaises(SystemExit, run.main, ["round1", "--state", self.S])

    def test_classify(self):
        self.assertEqual(run.classify(143, None), ("absent", "interrupted"))
        self.assertEqual(run.classify(124, None), ("absent", "timeout"))
        self.assertEqual(run.classify(1, None), ("absent", "error: no result"))
        self.assertEqual(run.classify(-15, None), ("absent", "interrupted"))
        self.assertEqual(run.classify(2, None), ("absent", "error: bw seat usage error"))
        self.assertEqual(run.classify(75, None), ("absent", "error: refused with no result"))
        self.assertEqual(run.classify(0, {"ok": True, "reason": "ok"}), ("ok", "ok"))
        self.assertEqual(run.classify(1, {"ok": False, "reason": "usage cap"}), ("absent", "usage cap"))
        self.assertEqual(run.classify(75, {"ok": False, "reason": None}), ("absent", "error: bad result"))


if __name__ == "__main__":
    unittest.main()
