import fcntl
import io
import json
import os
import re
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
from lead.projects import Project  # noqa: E402
from panel import channel, roster, run, service, state  # noqa: E402
from seats import verify  # noqa: E402
import store  # noqa: E402  (metrics/, on sys.path through lead.paths)

FIX = HERE / "fixtures" / "panel"
FAKE = FIX / "fake_seat.py"
CONFIG = FIX / "config.json"
K = "wt-k"
LEAD = {"name": "lead-x", "runtime": "codex", "model": "model-l", "plan": "plan-x"}

# The real worker CLI, with pending_verify patched (it would run real version commands).
WORKER_SRC = """
import sys
sys.path.insert(0, sys.argv[1])
from unittest import mock
from panel import roster, run
with mock.patch.object(roster, "pending_verify", return_value=[]):
    sys.exit(run.main(sys.argv[2:]))
"""
# A worker that clears its job and exits at once (faster than the scheduler's pid write).
RELEASE_SRC = """
import sys
sys.path.insert(0, sys.argv[1])
from panel import state
a = sys.argv[2:]
state.release_job(a[a.index("--state") + 1], a[a.index("--key") + 1], a[a.index("--panel") + 1])
"""


def _alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_for(cond, timeout=15.0, step=0.05) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return bool(cond())


class FakeJobs:
    """Jobs that start nothing: every spawn gets a pid no process has, and reads as alive."""

    def __init__(self):
        self.argvs = []

    def spawn(self, argv, log_path):
        self.argvs.append(argv)
        return 99999990 + len(self.argvs)

    def alive(self, pid):
        return True

    def reap(self):
        pass


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.T = t
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
        self.project = Project(str(self.wt), K, None, None, "p")
        self.env = mock.patch.dict(os.environ, {
            "BW_STATE_DIR": self.S, "BALANCEWHEEL_METRICS_LOG": self.LOG, "BALANCEWHEEL_CONFIG": str(CONFIG),
            "BW_PANEL_SEAT_CMD": json.dumps([sys.executable, str(FAKE)]), "FAKE_OUT": str(self.OUT)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.CFG = load_config()
        for target, name, value in ((run, "POLL_S", 0.02), (service, "WORKER", [sys.executable, "-c", WORKER_SRC, str(ROOT)])):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        for target, name, value in ((roster, "pending_verify", []), (verify, "spawn_verify", False)):
            p = mock.patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)
        self.jobs = service.Jobs()
        self.addCleanup(self.kill_all)
        self.notices = []

    def kill_all(self):
        """No survivors: every worker, fake seat, and fake seat child is gone."""
        pids = list(self.jobs._procs)
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
        for proc in self.jobs._procs.values():
            proc.wait()

    # --- helpers ---

    def notice(self, kind, text, key=None):
        self.notices.append((kind, text, key))

    def config(self, **top):
        cfg = json.loads(CONFIG.read_text())
        for k, v in top.items():
            cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
        path = self.T / f"config-{len(list(self.T.glob('config-*.json')))}.json"
        path.write_text(json.dumps(cfg))
        with mock.patch.dict(os.environ, {"BALANCEWHEEL_CONFIG": str(path)}):
            return load_config()

    def publish(self, rid, obj):
        os.close(channel.open_request_dir(self.S, K))
        d = state.request_dir(self.S, K)
        tmp = os.path.join(d, f".tmp-{rid}")
        Path(tmp).write_text(json.dumps(obj))
        os.replace(tmp, os.path.join(d, f"{rid}.request"))

    def responses(self):
        return Path(state.view_root(self.S, K), "responses")

    def response(self, rid):
        return json.loads((self.responses() / f"{rid}.json").read_text())

    def clear_responses(self):
        for f in self.responses().glob("*.json"):
            f.unlink()

    def drain(self, jobs=None, config=None, **kw):
        kw.setdefault("lead", LEAD)
        kw.setdefault("session_id", "s1")
        service.drain(config or self.CFG, self.S, self.project, jobs or self.jobs, notice=self.notice, **kw)

    def panel(self, pid):
        return state.load(self.S, K, pid)

    def until(self, pid, want, jobs=None, timeout=15.0):
        ok = wait_for(lambda: (self.drain(jobs) or True) and (self.panel(pid) or {}).get("state") == want,
                      timeout=timeout, step=0.2)
        self.assertTrue(ok, f"{pid} never reached {want}: {self.panel(pid)}")

    def findings(self, pid):
        return json.loads(Path(state.work_dir(self.S, K, pid), "findings.json").read_text())

    def ids(self, doc):
        return sorted((m["id"] for g in doc["groups"] for m in g["members"]), key=lambda i: int(i[1:]))

    def all_confirmed(self, pid):
        return {f: "confirmed" for f in self.ids(self.findings(pid))}

    def open(self, checkpoint="design", config=None, **req):
        p = service.open_panel(config or self.CFG, self.S, self.project, {"checkpoint": checkpoint, "target": "widget.py", **req},
                               lead=LEAD, session_id="s1")
        return p["id"]

    def ready(self, env=None, config=None, checkpoint="design"):
        """A panel opened and run in-process to `ready`."""
        pid = self.open(checkpoint, config=config)
        with mock.patch.dict(os.environ, env or {}):
            run.round1(config or self.CFG, self.S, K, pid)
        self.assertEqual(self.panel(pid)["state"], "ready")
        return pid

    def log(self, pid, rid="l1", config=None, **fields):
        if "verdicts" not in fields:
            fields["verdicts"] = self.all_confirmed(pid)
        self.publish(rid, {"v": 1, "type": "log", "panel": pid, **fields})
        self.drain(FakeJobs(), config=config)
        return self.response(rid)

    def set_asks(self, pid, asks):
        state.update(self.S, K, pid, lambda p: p.update(asks=asks))

    def job(self, pid):
        return (self.panel(pid) or {}).get("job")

    # --- requests to a logged record ---

    def test_request_to_logged(self):
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        self.drain()
        pid = self.response("r1")["panel"]
        self.until(pid, "ready")
        doc = self.findings(pid)
        self.publish("r2", {"v": 1, "type": "log", "panel": pid, "nonce": "n2",
                            "verdicts": {f: "confirmed" for f in self.ids(doc)}})
        self.drain()
        r = self.response("r2")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["summary"].startswith("Design panel ("), r["summary"])
        self.assertEqual(r["ignored"], [])
        self.assertEqual(r["nonce"], "n2")
        (p,) = store.load(self.LOG)["panels"]
        self.assertEqual((p["id"], p["kind"]), (pid, "design"))
        self.assertEqual(p["moderator"]["lead"], LEAD["name"])
        self.assertEqual(self.panel(pid)["state"], "logged")
        self.assertEqual(self.panel(pid)["record_id"], pid)

    def test_one_job_per_worktree(self):
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        self.publish("r2", {"v": 1, "type": "panel", "checkpoint": "review", "target": "t"})
        with mock.patch.dict(os.environ, {"FAKE_DELAY": "1"}):
            self.drain()
            p1, p2 = self.response("r1")["panel"], self.response("r2")["panel"]
            self.assertIsNotNone(self.job(p1))
            self.assertIsNone(self.job(p2))
            self.drain()
            self.assertEqual(self.panel(p2)["state"], "queued")
            self.until(p1, "ready")
            self.until(p2, "ready")

    def test_fourth_open_panel_refused(self):
        a, b, c = self.open(), self.open(), self.open()
        state.update(self.S, K, a, lambda p: p.update(state="ready"))
        state.update(self.S, K, b, lambda p: p.update(state="running"))
        self.publish("r4", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        self.drain(FakeJobs())
        r = self.response("r4")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], f"3 panels are open: {a} ready, {b} running, {c} queued. Log a ready panel "
                                     "(follow its next.md), or wait for a running one, then send this request "
                                     "again with a new id.")

    def test_unknown_checkpoint_refused(self):
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "nope", "target": "t"})
        self.drain(FakeJobs())
        r = self.response("r1")
        self.assertFalse(r["ok"])
        self.assertIn("it defines: design, review", r["error"])

    def test_paths_outside_refused(self):
        with self.assertRaisesRegex(ValueError, "^paths must be repo-relative: path outside the target"):
            self.open(paths=["../x"])
        outside = self.T / "outside"
        outside.mkdir()
        os.symlink(outside, self.wt / "link")
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t", "paths": ["link/x"]})
        self.drain(FakeJobs())
        r = self.response("r1")
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"].startswith("paths must be repo-relative: path outside the target"), r)
        self.assertEqual(state.panels(self.S, K), [])

    def test_panel_of_another_worktree_refused(self):
        other = state.create(self.S, "otherkey", {"worktree": str(self.wt), "profile": "p", "checkpoint": "design",
                                                  "target": "t", "roster": ["a"]})
        state.update(self.S, "otherkey", other["id"], lambda p: p.update(state="ready"))
        other = state.load(self.S, "otherkey", other["id"])
        for req in ({"type": "log", "verdicts": {}}, {"type": "amend", "patch": {}},
                    {"type": "ask", "seat": "a", "claim": "c", "dispute": "d1"}):
            self.publish("x", {"v": 1, "panel": other["id"], **req})
            self.drain(FakeJobs())
            self.assertIn("unknown panel", self.response("x")["error"])
            self.clear_responses()
        self.assertEqual(state.load(self.S, "otherkey", other["id"]), other)

    def test_log_states(self):
        pid = self.ready()
        state.update(self.S, K, pid, lambda p: p.update(state="running"))
        self.assertIn(f"panel {pid} is running; log it when it is ready", self.log(pid, "l1")["error"])
        state.update(self.S, K, pid, lambda p: p.update(state="ready"))
        self.set_asks(pid, [{"id": "d1-1-o-1", "seat": "o", "dispute": "d1", "round": 1, "state": "queued"}])
        self.assertEqual(self.log(pid, "l2")["error"], f"panel {pid} has open asks: d1-1-o-1; wait for asks, then log")
        self.set_asks(pid, [])
        self.assertTrue(self.log(pid, "l3")["ok"])
        self.assertEqual(self.log(pid, "l4")["error"], f"panel {pid} is already logged; send an amend")
        self.assertIn("unknown panel", self.log("p0000000", "l5", verdicts={})["error"])
        self.assertEqual(len(store.load(self.LOG)["panels"]), 1)

    def test_log_refuses_a_duplicate_record(self):
        pid = self.ready()
        self.assertTrue(self.log(pid, "l1")["ok"])
        state.update(self.S, K, pid, lambda p: p.update(state="ready", record_id=None))   # the state write was lost
        self.assertEqual(self.log(pid, "l2")["error"], f"panel {pid} is already logged; send an amend")
        self.assertEqual(len(store.load(self.LOG)["panels"]), 1)
        self.assertEqual((self.panel(pid)["state"], self.panel(pid)["record_id"]), ("logged", pid))   # recovered
        self.assertTrue(self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})["ok"])

    def test_crash_between_append_and_state_write(self):
        pid = self.ready()
        def lost(*a, **kw):   # the first write after the append is lost, as in a crash
            raise OSError("lost")
        with mock.patch.object(service.pstate, "update", side_effect=lost):
            r = self.log(pid, "l1")
        self.assertFalse(r["ok"])
        self.assertEqual(len(store.load(self.LOG)["panels"]), 1)
        self.assertEqual(self.panel(pid)["state"], "ready")
        self.assertEqual(self.log(pid, "l2")["error"], f"panel {pid} is already logged; send an amend")
        self.assertEqual(self.panel(pid)["state"], "logged")
        r = self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.panel(pid)["judged"]["verdicts"]["f1"], "refuted")

    def test_watch_failure_after_append_leaves_panel_amendable(self):
        pid = self.ready()
        with mock.patch.object(service.metrics_log, "run_check", side_effect=RuntimeError("watches")):
            r = self.log(pid, "l1")
        self.assertFalse(r["ok"])
        self.assertEqual((self.panel(pid)["state"], self.panel(pid)["record_id"]), ("logged", pid))
        self.assertIsNotNone(self.panel(pid)["judged"])
        self.assertTrue(self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})["ok"])

    def test_open_limit_checked_under_the_lock(self):
        for _ in range(3):
            self.open()
        seen = []

        def check(open_now):   # called under the worktree lock, before the id is taken
            seen.append(len(open_now))
            raise ValueError(service._open_refusal(open_now))
        with self.assertRaisesRegex(ValueError, "^3 panels are open"):
            state.create(self.S, K, {"checkpoint": "design"}, check=check)
        self.assertEqual((seen, len(state.panels(self.S, K))), ([3], 3))

    def test_amend_needs_a_patch(self):
        pid = self.ready()
        self.publish("a1", {"v": 1, "type": "amend", "panel": pid})
        self.drain(FakeJobs())
        self.assertEqual(self.response("a1")["error"], "amend needs a patch")
        r = service._answer(self.CFG, self.S, self.project, {"type": "amend", "panel": pid}, None, None, "a2",
                            self.notice)
        self.assertEqual(r, {"ok": False, "error": "amend needs a patch"})

    def test_missing_verdict_refused(self):
        pid = self.ready()
        verdicts = self.all_confirmed(pid)
        del verdicts["f2"]
        r = self.log(pid, verdicts=verdicts)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "missing verdicts: f2")
        self.assertEqual(self.panel(pid)["state"], "ready")

    def test_malformed_fields_get_a_response(self):
        pid = self.ready()
        for i, extra in enumerate(({"groups": None}, {"disputes": [{}]}, {"added": "x"})):
            r = self.log(pid, f"m{i}", **extra)
            self.assertFalse(r["ok"], extra)
            self.assertTrue(r["error"])
        self.assertEqual(store.load(self.LOG)["panels"], [])

    def test_shape_faults_map_to_malformed(self):
        pid = self.ready()
        with mock.patch.object(service, "log_panel", side_effect=KeyError("x")):
            r = self.log(pid)
        self.assertEqual(r, {**r, "ok": False, "error": "malformed request: KeyError"})

    def test_ignored_keys_named(self):
        pid = self.ready()
        r = self.log(pid, nonce="n1", colour="blue")
        self.assertTrue(r["ok"])
        self.assertEqual(r["ignored"], ["colour"])

    def test_reused_id_gets_refusal_and_notice(self):
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        self.drain(FakeJobs())
        first = self.response("r1")
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t", "nonce": "n9"})
        self.drain(FakeJobs())
        again = json.loads((self.responses() / "r1.reused-1.json").read_text())
        self.assertFalse(again["ok"])
        self.assertEqual(again["nonce"], "n9")
        self.assertIn("was used before", again["error"])
        self.assertEqual(self.response("r1"), first)
        self.assertEqual([(k, key) for k, _, key in self.notices], [("panel", "r1")])
        self.assertEqual(len(state.panels(self.S, K)), 1)

    def test_drain_answers_at_most_a_bounded_number(self):
        pid = self.ready()
        for i in range(service.MAX_REQUESTS + 3):
            self.publish(f"x{i:02d}", {"v": 1, "type": "amend", "panel": pid, "patch": {}})
        self.drain(FakeJobs())
        self.assertEqual(len(list(self.responses().glob("*.json"))), service.MAX_REQUESTS)
        self.assertEqual(len(list(Path(state.request_dir(self.S, K)).glob("*.request"))), 3)
        self.drain(FakeJobs())
        self.assertEqual(len(list(self.responses().glob("*.json"))), service.MAX_REQUESTS + 3)

    # --- asks ---

    def test_ask_rules(self):
        pid = self.ready({"FAKE_SEAT_g": "timeout", "FAKE_SEAT_o": "nosession"})
        with self.assertRaisesRegex(ValueError, "^seat g did not return in round 1$"):
            self.ask_on(pid, "g")
        with self.assertRaisesRegex(ValueError, "^seat o has no round-1 session to resume; rule on it yourself$"):
            self.ask_on(pid, "o")
        self.set_asks(pid, [{"id": f"d1-{r}-a-1", "seat": "a", "dispute": "d1", "round": r, "attempt": 1,
                             "state": "done", "lead_session": "s0"} for r in (1, 2)])
        with self.assertRaisesRegex(ValueError, "^dispute d1 has had 2 rounds; rule on it yourself$"):
            self.ask_on(pid, "a")
        self.set_asks(pid, [{"id": f"d{i}-1-a-1", "seat": "a", "dispute": f"d{i}", "round": 1, "attempt": 1,
                             "state": "failed", "lead_session": "s1"} for i in range(20)])
        with self.assertRaisesRegex(ValueError, "20 asks"):
            self.ask_on(pid, "a", dispute="d1")
        self.ask_on(pid, "a", dispute="d1", session_id="s2")   # another session has its own count
        with self.assertRaisesRegex(ValueError, "over 1500 characters"):
            self.ask_on(pid, "a", claim="x" * 1501)
        with self.assertRaisesRegex(ValueError, "dispute must be a dispute id"):
            self.ask_on(pid, "a", dispute="bad id!")
        state.update(self.S, K, pid, lambda p: p.update(state="logged"))
        with self.assertRaisesRegex(ValueError, f"panel {pid} is not ready"):
            self.ask_on(pid, "a", dispute="d50", session_id="s3")

    def test_dispute_ids_are_the_panels_own(self):  # panel g03
        pid = self.ready()
        allowed = service.disputes(self.findings(pid))
        self.assertIn("d1", allowed)
        self.assertEqual(len(allowed), len(set(allowed)))
        with self.assertRaisesRegex(ValueError, "^dispute d999 is not a dispute of this panel; use one of: d1"):
            self.ask_on(pid, "a", dispute="d999")
        self.assertEqual(service.disputes({"groups": [], "candidate_disputes": []}), ["d1"])
        self.assertEqual(service.disputes({"groups": [{}, {}], "candidate_disputes": [{"id": "d2"}, {"id": "d7"}]}),
                         ["d2", "d7", "d1"])

    def test_queued_asks_count_toward_max_rounds(self):  # panel g02
        pid = self.ready()
        self.ask_on(pid, "a")
        self.ask_on(pid, "o")  # two queued asks: the cap of 2 rounds is reached
        with self.assertRaisesRegex(ValueError, "^dispute d1 has had 2 rounds; rule on it yourself$"):
            self.ask_on(pid, "g")
        state.update(self.S, K, pid, lambda p: p["asks"][0].update(state="failed"))
        self.assertEqual(self.ask_on(pid, "g")["round"], 2)  # a failed ask frees its round

    def ask_on(self, pid, seat, dispute="d1", claim="X", session_id="s1"):
        return service.queue_ask(self.CFG, self.S, self.project,
                                 {"panel": pid, "seat": seat, "claim": claim, "dispute": dispute}, session_id=session_id)

    def test_failed_ask_retry_gets_new_id(self):
        pid = self.ready()
        first = self.ask_on(pid, "o")
        self.assertEqual((first["id"], first["round"], first["state"]), ("d1-1-o-1", 1, "queued"))
        self.set_asks(pid, [dict(first, state="failed")])
        retry = self.ask_on(pid, "o")
        self.assertEqual((retry["id"], retry["round"]), ("d1-1-o-2", 1))
        self.set_asks(pid, [dict(first, state="failed"), dict(retry, state="done")])
        second = self.ask_on(pid, "a")
        self.assertEqual((second["id"], second["round"], second["attempt"]), ("d1-2-a-1", 2, 1))

    def test_ask_job_lifecycle(self):
        pid = self.ready()
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "echo", "FAKE_SEAT_g": "echo"}):
            for rid, seat in (("k1", "o"), ("k2", "g")):
                self.publish(rid, {"v": 1, "type": "ask", "panel": pid, "seat": seat, "claim": "X", "dispute": "d1"})
            self.drain()
            r = self.response("k1")
            self.assertEqual((r["ok"], r["ask"]), (True, "d1-1-o-1"))
            self.assertEqual(r["answer"], os.path.join(state.view_dir(self.S, K, pid), "asks", "d1-1-o-1.md"))
            self.assertEqual(self.job(pid)["ask"], "d1-1-o-1")
            ok = wait_for(lambda: (self.drain() or True)
                          and all(a["state"] == "done" for a in self.panel(pid)["asks"]), step=0.2)
            self.assertTrue(ok, self.panel(pid)["asks"])
        self.assertTrue(Path(r["answer"]).exists())
        self.assertTrue(wait_for(lambda: not state.has_job(self.S, K)))
        self.assertEqual(self.panel(pid)["state"], "ready")

    def wait_running(self, pid, pgids):
        ok = wait_for(lambda: len(((self.panel(pid) or {}).get("job") or {}).get("seat_pgids") or []) == pgids)
        self.assertTrue(ok, self.panel(pid))
        return self.job(pid)

    def kill_worker(self, job):
        os.kill(job["pid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: self.jobs.alive(job["pid"]) is False))

    def test_dead_ask_worker_fails_the_ask(self):
        pid = self.ready()
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "sleep"}):
            self.ask_on(pid, "o")
            service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)
            job = self.wait_running(pid, 1)
        self.kill_worker(job)
        t = time.time()
        service.schedule(self.CFG, self.S, K, self.jobs, now=t, notice=self.notice)
        self.assertEqual(self.job(pid)["stop"], "panel worker exited")
        self.assertTrue(wait_for(lambda: not service.tree_alive(job)))
        service.schedule(self.CFG, self.S, K, self.jobs, now=t + service.KILL_GRACE_S + 1, notice=self.notice)
        p = self.panel(pid)
        self.assertEqual((p["asks"][0]["state"], p["asks"][0]["reason"]), ("failed", "panel worker exited"))
        self.assertEqual((p["state"], p["job"]), ("ready", None))

    def test_ask_waits_for_running_job(self):
        b = self.ready()
        with mock.patch.dict(os.environ, {"FAKE_DELAY": "1", "FAKE_SEAT_o": "echo"}):
            self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "review", "target": "t"})
            self.drain()
            a = self.response("r1")["panel"]
            self.publish("k1", {"v": 1, "type": "ask", "panel": b, "seat": "o", "claim": "X", "dispute": "d1"})
            self.drain()
            self.assertTrue(self.response("k1")["ok"])
            self.assertEqual(self.panel(b)["asks"][0]["state"], "queued")
            self.assertIsNotNone(self.job(a))
            self.until(a, "ready")
            ok = wait_for(lambda: (self.drain() or True) and self.panel(b)["asks"][0]["state"] == "done", step=0.2)
            self.assertTrue(ok, self.panel(b)["asks"])

    def test_rounds_from_asks(self):
        pid = self.ready()
        self.set_asks(pid, [{"id": "d1-1-o-1", "seat": "o", "dispute": "d1", "round": 1, "attempt": 1, "state": "done"}])
        self.assertTrue(self.log(pid)["ok"])
        (p,) = store.load(self.LOG)["panels"]
        self.assertEqual((p["rounds"], p["immediate_agreement"]), (2, False))

    # --- the scheduler and the watchdog ---

    def test_reserve_before_spawn(self):
        pid = self.open()
        real = self.jobs.spawn

        def slow_spawn(argv, log_path):   # the worker finishes before the scheduler writes its pid
            child = real(argv, log_path)
            self.jobs._procs[child].wait()
            return child
        with mock.patch.object(service, "WORKER", [sys.executable, "-c", RELEASE_SRC, str(ROOT)]), \
                mock.patch.object(self.jobs, "spawn", side_effect=slow_spawn) as spawn:
            for n in range(1, service.MAX_SPAWNS + 1):
                service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)
                p = self.panel(pid)
                self.assertEqual((p["job"], p["state"], p["spawns"]), (None, "queued", n))
            self.assertEqual(self.notices, [])
            service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)   # 3 starts never reached running
            self.assertEqual(spawn.call_count, service.MAX_SPAWNS)
        p = self.panel(pid)
        self.assertEqual((p["state"], p["error"], p["job"]), ("failed", "worker did not start", None))
        self.assertEqual([(k, key) for k, _, key in self.notices], [("panel", pid)])
        why = "panel failed: worker did not start"
        self.assertEqual(self.findings(pid)["absent"], {"a": why, "o": why, "g": why})
        r = self.log(pid, verdicts={})   # the lead logs the all-absent panel
        self.assertTrue(r["ok"], r)
        self.assertIn("failed: worker did not start", r["summary"])
        self.assertEqual(self.panel(pid)["state"], "logged")
        (rec,) = store.load(self.LOG)["panels"]
        self.assertEqual((rec["seats"], rec["absent"]), ({}, {"a": why, "o": why, "g": why}))
        self.assertEqual(service.open_lines(self.S, K), [])

    def test_failed_spawn_releases_and_notices(self):
        pid = self.open()
        with mock.patch.object(self.jobs, "spawn", side_effect=OSError("no")):
            service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)
        self.assertIsNone(self.job(pid))
        self.assertEqual([(k, key) for k, _, key in self.notices], [("panel", pid)])

    def test_reserved_job_with_no_pid_released_after_30_s(self):
        pid = self.open()
        job = {"kind": "round1", "ask": None, "pid": None, "owner": "scheduler", "started": 100.0,
               "deadline": 1e12, "beat": 100.0, "seat_pgids": []}
        self.assertTrue(state.reserve_job(self.S, K, pid, job))
        jobs = FakeJobs()
        service.schedule(self.CFG, self.S, K, jobs, now=120.0)
        self.assertEqual((self.job(pid)["pid"], jobs.argvs), (None, []))
        service.schedule(self.CFG, self.S, K, jobs, now=131.0)
        self.assertEqual(len(jobs.argvs), 1)   # released, then the queued round 1 started again
        self.assertEqual(self.job(pid)["pid"], 99999991)

    def test_deadline_counts_fallbacks(self):
        seats = {n: {"runtime": "codex", "model": f"model-{n}", "roles": ["design"], "fallback": f"f{n}"}
                 for n in ("a", "b", "c", "d")}
        seats.update({f"f{n}": {"runtime": "codex", "model": f"model-f{n}", "roles": ["design"]} for n in "abcd"})
        cfg = self.config(seats=seats, profiles={"p": {"checkpoints": {"design": {"seats": list("abcd")}}}},
                          panel={"cap_min": 1, "max_parallel": 1})
        pid = self.open(config=cfg)
        self.assertEqual((self.panel(pid)["originals"], self.panel(pid)["fallbacks"]), (4, 4))
        service.schedule(cfg, self.S, K, FakeJobs(), now=1000.0)
        job = self.job(pid)
        self.assertEqual(job["deadline"], 1000.0 + (4 + 4 + 1) * 60 + 300)
        self.assertEqual((job["kind"], job["owner"], job["beat"]), ("round1", "scheduler", 1000.0))

    def test_ask_deadline(self):
        pid = self.ready()
        self.ask_on(pid, "o")
        service.schedule(self.CFG, self.S, K, FakeJobs(), now=1000.0)
        self.assertEqual(self.job(pid)["deadline"], 1000.0 + 60 + 300)

    def watchdog_path(self):
        """Round 1 with a returning, and o and g sleeping; stopped at the deadline."""
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "sleep", "FAKE_SEAT_g": "sleep"}):
            pid = self.open()
            service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)
            job = self.wait_running(pid, 2)
            self.assertTrue(wait_for(lambda: self.panel(pid)["seats"]["a"]["status"] == "ok"))
        minutes = round((job["deadline"] - job["started"]) / 60)
        t = job["deadline"] + 1
        service.schedule(self.CFG, self.S, K, self.jobs, now=t, notice=self.notice)
        p = self.panel(pid)   # the worker may already have failed the panel and cleared its job
        self.assertIn(p["state"], ("running", "failed"))
        if p["job"] is not None:
            self.assertEqual((p["job"]["stop"], p["job"]["kill_at"]), (f"stopped after {minutes} min", t))
        else:
            self.assertEqual(p["error"], f"stopped after {minutes} min")
        # alive() first: it reaps the worker, and a zombie's group still reads as existing
        self.assertTrue(wait_for(lambda: self.jobs.alive(job["pid"]) is False and not service.tree_alive(job)))
        return pid, job, t, minutes

    def test_watchdog_kills_and_fails(self):
        pid, job, t, minutes = self.watchdog_path()
        self.assertEqual(minutes, 7)   # (1 wave + 0 fallbacks + 1 verification wait) * 1 min + 5 min
        findings = Path(state.work_dir(self.S, K, pid), "findings.json")
        ino = findings.stat().st_ino
        service.schedule(self.CFG, self.S, K, self.jobs, now=t + service.KILL_GRACE_S + 1, notice=self.notice)
        p = self.panel(pid)
        self.assertEqual((p["state"], p["error"], p["job"]), ("failed", "stopped after 7 min", None))
        doc = self.findings(pid)
        self.assertEqual(doc["seats"]["a"]["status"], "ok")
        self.assertEqual(doc["absent"], {"o": "panel failed: stopped after 7 min", "g": "panel failed: stopped after 7 min"})
        self.assertEqual(len(store.load(self.LOG)["panel_runs"]), 1)
        self.assertEqual(findings.stat().st_ino, ino)   # written once

    def test_no_survivors(self):
        pid, job, t, _ = self.watchdog_path()
        service.schedule(self.CFG, self.S, K, self.jobs, now=t + service.KILL_GRACE_S + 1, notice=self.notice)
        self.assertFalse(service.tree_alive(job))
        metas = [json.loads(f.read_text()) for f in self.OUT.glob("meta-*.json")]
        self.assertEqual(len([m for m in metas if m["child"]]), 2)
        for m in metas:
            self.assertTrue(wait_for(lambda: not _alive(m["pid"]) and not (m["child"] and _alive(m["child"]))),
                            m)
        self.assertFalse(_alive(job["pid"]))

    def test_stale_beat_checked_before_pid(self):
        pid = self.open()
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])   # not a group leader
        self.addCleanup(other.wait)
        self.addCleanup(other.kill)
        now = 5000.0
        job = {"kind": "round1", "ask": None, "pid": other.pid, "owner": "scheduler", "started": now - 200,
               "deadline": 1e12, "beat": now - service.BEAT_STALE_S - 1, "seat_pgids": []}
        self.assertTrue(state.reserve_job(self.S, K, pid, job))
        jobs = service.Jobs()   # this bw did not start it
        with mock.patch.object(os, "killpg", wraps=os.killpg) as killpg, mock.patch.object(os, "kill", wraps=os.kill) as kill:
            service.schedule(self.CFG, self.S, K, jobs, now=now)
            self.assertEqual(self.job(pid)["stop"], "panel worker stopped")
            service.schedule(self.CFG, self.S, K, jobs, now=now + service.KILL_GRACE_S + 1)
        self.assertIsNone(other.poll())   # alive: its own group was never signalled
        self.assertNotIn(os.getpgrp(), [c.args[0] for c in killpg.call_args_list])
        self.assertNotIn(other.pid, [c.args[0] for c in kill.call_args_list])
        p = self.panel(pid)
        self.assertEqual((p["state"], p["error"], p["job"]), ("failed", "panel worker stopped", None))

    def test_reused_pids_that_lead_groups_are_never_signalled(self):
        pid = self.open()
        sleep = [sys.executable, "-c", "import time; time.sleep(30)"]
        leaders = [subprocess.Popen(sleep, start_new_session=True) for _ in range(2)]   # pids reused by others
        for proc in leaders:
            self.addCleanup(proc.wait)
            self.addCleanup(proc.kill)
        now = 5000.0
        job = {"kind": "round1", "ask": None, "pid": leaders[0].pid, "owner": "scheduler", "started": now - 200,
               "deadline": 1e12, "beat": now - service.BEAT_STALE_S - 1, "seat_pgids": [leaders[1].pid]}
        self.assertTrue(state.reserve_job(self.S, K, pid, job))
        jobs = service.Jobs()
        with mock.patch.object(os, "killpg", wraps=os.killpg) as killpg:
            service.schedule(self.CFG, self.S, K, jobs, now=now)
            self.assertEqual(self.job(pid)["skipped"], sorted(f"{p.pid}: stale pid not ours" for p in leaders))
            service.schedule(self.CFG, self.S, K, jobs, now=now + service.KILL_GRACE_S + 1)
        signalled = [c.args[0] for c in killpg.call_args_list if c.args[1] != 0]
        self.assertEqual(signalled, [])
        self.assertEqual([p.poll() for p in leaders], [None, None])
        self.assertIn("stale pid not ours", Path(state.work_dir(self.S, K, pid), "log.txt").read_text())
        self.assertEqual(self.panel(pid)["state"], "failed")

    def test_worker_and_seat_commands_are_recognised(self):
        self.assertTrue(service._is_worker("python3 -m panel.run round1 --panel p1234567", "p1234567", "scheduler"))
        self.assertFalse(service._is_worker("python3 -m panel.run round1 --panel p7654321", "p1234567", "scheduler"))
        self.assertFalse(service._is_worker("sleep 30", "p1234567", "scheduler"))
        self.assertTrue(service._is_worker("python3 /x/bin/bw panel design t", "p1234567", "foreground"))
        self.assertTrue(service._is_worker("python3 /x/bin/bw panel ask p1234567 o x", "p1234567", "foreground"))
        self.assertFalse(service._is_worker("python3 /x/bin/bw panel ask p7654321 o x", "p1234567", "foreground"))
        self.assertTrue(service._is_seat("python3 /x/bin/bw seat a --no-fallback"))
        self.assertTrue(service._is_seat(f"{sys.executable} {FAKE} a --no-fallback"))
        self.assertFalse(service._is_seat("sleep 30"))
        self.assertEqual(service._command(999999999), "")

    def test_sigkill_reaches_a_group_whose_leader_ended_on_sigterm(self):
        """Each pid is confirmed once, at SIGTERM. The leader ends then; a member that ignores SIGTERM
        stays in the group, and the SIGKILL step still reaches it."""
        pid = self.open()
        member_file = self.T / "member"
        seat = self.T / "seat_group.py"
        seat.write_text(
            "import signal, subprocess, sys, time\n"
            "m = subprocess.Popen([sys.executable, '-c', 'import signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
            f"open({str(member_file)!r}, 'w').write(str(m.pid))\n"
            "time.sleep(30)\n")
        worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "panel.run", "round1", "--panel", pid],
                                  start_new_session=True)
        with mock.patch.dict(os.environ, {"BW_PANEL_SEAT_CMD": json.dumps([sys.executable, str(seat)])}):
            leader = subprocess.Popen([sys.executable, str(seat), "a"], start_new_session=True)
            for proc in (worker, leader):
                self.addCleanup(proc.wait)
                self.addCleanup(proc.kill)
            self.assertTrue(wait_for(member_file.exists))
            time.sleep(0.2)   # the member sets SIG_IGN
            member = int(member_file.read_text())
            self.addCleanup(lambda: _alive(member) and os.kill(member, signal.SIGKILL))
            now = 5000.0
            self.assertTrue(state.reserve_job(self.S, K, pid, {
                "kind": "round1", "ask": None, "pid": worker.pid, "owner": "scheduler", "started": now - 200,
                "deadline": 1e12, "beat": now - service.BEAT_STALE_S - 1, "seat_pgids": [leader.pid]}))
            jobs = service.Jobs()
            service.schedule(self.CFG, self.S, K, jobs, now=now)
            self.assertTrue(wait_for(lambda: leader.poll() is not None and worker.poll() is not None))
            self.assertTrue(_alive(member))
            job = self.job(pid)
            self.assertEqual((job.get("skipped"), sorted(job["kill_groups"])), (None, sorted([worker.pid, leader.pid])))
            service.schedule(self.CFG, self.S, K, jobs, now=now + service.KILL_GRACE_S + 1)
        self.assertTrue(wait_for(lambda: not _alive(member)))
        self.assertEqual(self.panel(pid)["state"], "failed")
        self.assertNotIn("not signalled", Path(state.work_dir(self.S, K, pid), "log.txt").read_text())

    def test_failed_panels_do_not_block_new_panels(self):
        pids = [self.open() for _ in range(3)]
        for pid in pids:
            service.fail(self.CFG, self.S, K, pid, "worker did not start")
        fourth = self.open()
        self.assertEqual(self.panel(fourth)["state"], "queued")
        lines = service.open_lines(self.S, K)
        self.assertEqual(len(lines), 4)
        self.assertTrue(all("failed (worker did not start) — follow" in line for line in lines[:3]))

    def test_dead_worker_kills_seat_groups(self):
        with mock.patch.dict(os.environ, {"FAKE_SEAT_o": "sleep", "FAKE_SEAT_g": "sleep"}):
            pid = self.open()
            service.schedule(self.CFG, self.S, K, self.jobs, notice=self.notice)
            job = self.wait_running(pid, 2)
            self.assertTrue(wait_for(lambda: self.panel(pid)["seats"]["a"]["status"] == "ok"))
        self.kill_worker(job)
        self.assertTrue(service.tree_alive(job))   # the seat groups outlive the worker
        t = time.time()
        service.schedule(self.CFG, self.S, K, self.jobs, now=t, notice=self.notice)
        self.assertEqual(self.job(pid)["stop"], "panel worker exited")
        self.assertTrue(wait_for(lambda: not service.tree_alive(job)))
        self.assertEqual(self.panel(pid)["state"], "running")
        service.schedule(self.CFG, self.S, K, self.jobs, now=t + service.KILL_GRACE_S + 1, notice=self.notice)
        p = self.panel(pid)
        self.assertEqual((p["state"], p["error"], p["job"]), ("failed", "panel worker exited", None))
        self.assertEqual(self.findings(pid)["absent"], {"o": "panel failed: panel worker exited",
                                                        "g": "panel failed: panel worker exited"})
        log = Path(state.work_dir(self.S, K, pid), "log.txt")
        self.assertNotIn("not signalled", log.read_text() if log.exists() else "")   # no false note

    def test_stop_tree_skips_unsafe_groups(self):
        with mock.patch.object(os, "killpg") as killpg:
            service.stop_tree({"pid": None, "seat_pgids": [0, 1, -5, os.getpgrp(), "x", True, 99999993]}, signal.SIGTERM)
        self.assertEqual([c.args for c in killpg.call_args_list], [(99999993, signal.SIGTERM)])

    # --- failed panels ---

    def failed_panel(self):
        """A panel whose worker died after seat a returned: fail() writes it as failed."""
        pid = self.open()
        view = Path(state.view_dir(self.S, K, pid), "seats")
        view.mkdir(parents=True, exist_ok=True)
        (view / "a.md").write_text((FIX / "outputs" / "clean-a.md").read_text())
        seats = {"a": {"status": "ok", "on_roster": True, "model": "model-a", "session_id": "s-a"},
                 "o": {"status": "running", "on_roster": True}, "g": {"status": "running", "on_roster": True}}
        state.update(self.S, K, pid, lambda p: p.update(state="running", seats=seats))
        service.fail(self.CFG, self.S, K, pid, "panel worker exited")
        self.assertEqual(self.panel(pid)["state"], "failed")
        return pid

    def test_fail_is_one_writer(self):
        pid = self.failed_panel()
        findings = Path(state.work_dir(self.S, K, pid), "findings.json")
        ino = findings.stat().st_ino
        service.fail(self.CFG, self.S, K, pid, "again")
        self.assertEqual((findings.stat().st_ino, self.panel(pid)["error"]), (ino, "panel worker exited"))
        self.assertEqual(len(store.load(self.LOG)["panel_runs"]), 1)

    def test_failed_panel_logs(self):
        pid = self.failed_panel()
        r = self.log(pid)
        self.assertTrue(r["ok"], r)
        self.assertIn("failed: panel worker exited", r["summary"])
        (p,) = store.load(self.LOG)["panels"]
        self.assertEqual(p["absent"], {"o": "panel failed: panel worker exited", "g": "panel failed: panel worker exited"})
        self.assertEqual(service.open_lines(self.S, K), [])

    def test_failed_panel_without_findings_logs(self):
        pid = self.failed_panel()
        os.unlink(Path(state.work_dir(self.S, K, pid), "findings.json"))
        r = self.log(pid, verdicts={})
        self.assertTrue(r["ok"], r)
        self.assertIn("failed: panel worker exited", r["summary"])

    def test_failed_panel_in_open_lines(self):
        pid = self.failed_panel()
        view = state.view_dir(self.S, K, pid)
        self.assertEqual(service.open_lines(self.S, K),
                         [f"{pid} design: failed (panel worker exited) — follow {view}/next.md, then log it"])

    def test_open_lines(self):
        ready, queued = self.ready(), self.open(checkpoint="review")
        self.assertEqual(service.open_lines(self.S, K), [
            f"{ready} design: ready — follow {state.view_dir(self.S, K, ready)}/next.md, then log it",
            f"{queued} review: running — read {state.view_dir(self.S, K, queued)}/status.json at task boundaries"])
        self.set_asks(ready, [{"id": "d1-1-o-1", "seat": "o", "dispute": "d1", "round": 1, "state": "running"}])
        self.assertIn("running", service.open_lines(self.S, K)[0])

    # --- the skim follow-up and amend ---

    def test_skim_follow_up(self):
        cfg = self.config(watches={"skim_min_seats": 2, "skim_min_findings": 2})
        pid = self.ready(config=cfg)
        r = self.log(pid, config=cfg)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["follow_up"])
        self.assertTrue(any(w.startswith("WATCH[verification-skim|open]") for w in r["watches"]), r["watches"])
        text = Path(state.view_dir(self.S, K, pid), "next.md").read_text()
        self.assertIn("## Re-verify", text)
        self.assertIn("Re-verify f1, f3, f5 against the code", text)

    def test_no_follow_up_without_skim(self):
        pid = self.ready()
        r = self.log(pid)
        self.assertEqual((r["follow_up"], r["watches"]), (False, []))
        self.assertNotIn("Re-verify", Path(state.view_dir(self.S, K, pid), "next.md").read_text())

    def follow_log(self, pid):
        """Log a panel the way a lead does: by the letter of its next.md (the file, the body, the mv)."""
        text = Path(state.view_dir(self.S, K, pid), "next.md").read_text()
        log = text.split("## Log", 1)[1]
        path = re.search(r"Write only the JSON between the fence lines to (\S+)\. ", log).group(1)
        body = json.loads(re.search(r"```json\n(.*?)\n```", log, re.S).group(1))
        body.update(nonce=f"n-{pid}", verdicts=self.all_confirmed(pid), notes="")
        os.close(channel.open_request_dir(self.S, K))  # bw lead makes it at session start
        Path(path).write_text(json.dumps(body))
        mv = re.search(r"Run this command: (mv .*)\n", log).group(1)
        subprocess.run(mv, shell=True, check=True)
        rid = os.path.basename(mv.split()[-1])[:-len(".request")]
        self.drain(FakeJobs())
        return rid, self.response(rid)

    def test_two_panels_follow_next_md_literally(self):  # final review critical (panel g01)
        a = self.ready()
        rid_a, r = self.follow_log(a)
        self.assertTrue(r["ok"], r)
        self.assertEqual((rid_a, r["nonce"]), (f"log-{a}-1", f"n-{a}"))
        b = self.ready()
        rid_b, r = self.follow_log(b)
        self.assertNotEqual(rid_a, rid_b)
        self.assertTrue(r["ok"], r)  # never refused as a reused id, and never panel a's old answer
        self.assertEqual((r["panel"], r["nonce"]), (b, f"n-{b}"))
        self.assertEqual(sorted(p["id"] for p in store.load(self.LOG)["panels"]), sorted([a, b]))

    def amend(self, pid, rid, patch, config=None):
        self.publish(rid, {"v": 1, "type": "amend", "panel": pid, "patch": patch})
        self.drain(FakeJobs(), config=config)
        return self.response(rid)

    def detail(self):
        (p,) = store.load(self.LOG)["panels"]
        return {f["id"]: f for f in p["findings_detail"]}, p

    def test_amend(self):
        pid = self.ready()
        self.assertTrue(self.log(pid)["ok"])
        r = self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["summary"].startswith("Design panel ("))
        detail, p = self.detail()
        self.assertEqual(detail["f1"]["verdict"], "refuted")
        self.assertEqual(p["findings"][detail["f1"]["seat"]]["refuted"], 1)
        self.assertEqual(self.panel(pid)["judged"]["verdicts"]["f1"], "refuted")

    def test_two_amends_keep_both(self):
        pid = self.ready()
        added = [{"seat": "o", "title": "missed check", "severity": "minor", "verdict": "confirmed"}]
        self.assertTrue(self.log(pid, groups={"f2": "gx"}, added=added)["ok"])
        self.assertTrue(self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})["ok"])
        self.assertTrue(self.amend(pid, "a2", {"verdicts": {"f2": "refuted"}})["ok"])
        detail, _ = self.detail()
        self.assertEqual((detail["f1"]["verdict"], detail["f2"]["verdict"]), ("refuted", "refuted"))
        self.assertEqual(detail["f2"]["group"], "gx")
        self.assertEqual(detail["a1"]["title"], "missed check")

    def test_amend_names_ignored_keys(self):
        pid = self.ready()
        self.assertTrue(self.log(pid)["ok"])
        r = self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}, "disputes": []})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["ignored"], ["disputes"])

    def test_amend_before_log_refused(self):
        pid = self.ready()
        r = self.amend(pid, "a1", {"verdicts": {"f1": "refuted"}})
        self.assertEqual(r["error"], f"panel {pid} is not logged; send a log request first")
        self.assertEqual(store.load(self.LOG)["panels"], [])

    def test_moderator_resolved(self):
        os.makedirs(os.path.join(self.S, "models"))
        Path(self.S, "models", "plan-c.json").write_text(json.dumps({"model": "model-seen"}))
        cc = {"name": "lead-c", "runtime": "claude-code", "model": "model-cfg", "plan": "plan-c"}
        self.assertEqual(service.moderator_for(self.CFG, self.S, cc),
                         {"lead": "lead-c", "runtime": "claude-code", "model": "model-seen", "resolved": True})
        self.assertEqual(service.moderator_for(self.CFG, self.S, dict(LEAD, plan="plan-c")),
                         {"lead": "lead-x", "runtime": "codex", "model": "model-l", "resolved": False})
        self.assertIsNone(service.moderator_for(self.CFG, self.S, None))

    # --- processes and the drain ---

    def test_spawned_worker_inherits_no_lock_fd(self):
        lockfile, out, log = (str(self.T / n) for n in ("lock", "fds", "worker.log"))
        low = os.open(lockfile, os.O_RDWR | os.O_CREAT)
        self.addCleanup(os.close, low)
        fcntl.flock(low, fcntl.LOCK_EX)
        fd = fcntl.fcntl(low, fcntl.F_DUPFD, 50)   # above any fd the child opens itself
        self.addCleanup(os.close, fd)
        os.set_inheritable(fd, True)
        pid = self.jobs.spawn([sys.executable, "-c", f"import os; open({out!r}, 'w').write(' '.join(os.listdir('/dev/fd')))"],
                              log)
        self.assertTrue(wait_for(lambda: Path(out).exists() and self.jobs.alive(pid) is False))
        self.assertNotIn(str(fd), Path(out).read_text().split())
        self.assertIsNone(self.jobs.alive(12345678))
        self.jobs.reap()
        self.assertIsNone(self.jobs.alive(pid))

    def test_own_refuses_unknown(self):
        with self.assertRaisesRegex(ValueError, "^unknown panel p0000000$"):
            service.own(self.S, self.project, "p0000000")

    def test_drain_never_raises(self):
        with mock.patch.object(channel, "ingest", side_effect=RuntimeError("boom")):
            self.drain(FakeJobs())
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0][0], "panel")

    def test_drain_prints_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
            self.drain()
            pid = self.response("r1")["panel"]
            self.until(pid, "ready")
            self.publish("l1", {"v": 1, "type": "log", "panel": pid, "verdicts": self.all_confirmed(pid)})
            self.drain()
            self.publish("a1", {"v": 1, "type": "amend", "panel": pid, "patch": {"verdicts": {"f1": "refuted"}}})
            self.drain()
        self.assertTrue(self.response("l1")["ok"])
        self.assertTrue(self.response("a1")["ok"])
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))


if __name__ == "__main__":
    unittest.main()
