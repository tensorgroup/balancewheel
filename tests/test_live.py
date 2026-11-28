"""`bw live --json` (lead/live.py): open and recent runs of one worktree, read-only (spec §4, PD-12, PD-13)."""
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import handoff, live, runs  # noqa: E402
from lead.projects import worktree_key  # noqa: E402
from metrics import store  # noqa: E402
from panel import state as pstate  # noqa: E402

EXAMPLE = ROOT / "balancewheel.example.json"
NOW = float(int(time.time()))
OPEN_KEYS = {"run_id", "seat", "model", "label", "dir", "start", "source", "panel", "ask", "state", "icon", "color",
             "ctx", "window", "cost_usd"}
RECENT_KEYS = {"run_id", "seat", "model", "label", "start", "end", "ok", "reason", "source", "stand_in_for",
               "tokens_in", "tokens_out", "cost_usd", "ctx_peak"}
LOCAL_SEAT = {"name": "my-seat", "model": "model-a", "match": "my-seat.sh", "icon": "🦉", "color": "#2dd4bf"}


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


def make_repo(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    Path(path, "README.md").write_text("# repo\n")
    os.makedirs(os.path.join(path, "sub"))
    Path(path, "sub", "mod.py").write_text("x = 1\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")


def snapshot(root):
    """{relpath: (mode, size, mtime_ns)} of every file and dir under `root`."""
    out = {}
    for d, dirs, files in os.walk(root):
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            out[os.path.relpath(p, root)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    return out


def ts(t):
    return time.strftime(store.TS_FORMAT, time.gmtime(t))


class Live(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.log = os.path.join(self.tmp, "metrics.jsonl")
        self.cfg = os.path.join(self.tmp, "config.json")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BW_", "BALANCEWHEEL_"))}
        env.update({"HOME": self.home, "BW_STATE_DIR": self.state, "BALANCEWHEEL_METRICS_LOG": self.log,
                    "BALANCEWHEEL_CONFIG": self.cfg, "CODEX_HOME": os.path.join(self.home, ".codex"),
                    "CLAUDE_CONFIG_DIR": os.path.join(self.home, ".claude")})
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.wt = os.path.join(self.tmp, "wt")
        make_repo(self.wt)
        self.other = os.path.join(self.tmp, "other")
        self.key = worktree_key(self.wt)
        self.config = self.with_local()

    # --- helpers ---

    def with_local(self, **local):
        obj = json.loads(EXAMPLE.read_text())
        obj["local"] = {"seats": [dict(LOCAL_SEAT)], "families": {"model": "#112233"}, **local}
        Path(self.cfg).write_text(json.dumps(obj))
        return store_config()

    def with_local_logs(self, logs):
        return self.with_local(logs=logs)

    def start(self, seat, source="local", t=None, dir=None, **kw):
        row = runs.start_row(seat, "model-a", dir or self.wt, f"{seat} job", source, t=NOW - 10 if t is None else t, **kw)
        store.append(self.log, row)
        return row

    def end(self, s, ok=True, t=None, **kw):
        row = runs.end_row(s["run_id"], s["seat"], s["dir"], ok, None if ok else "exit 1", 5.0,
                           t=NOW - 5 if t is None else t, **kw)
        store.append(self.log, row)
        return row

    def req_dir(self):
        return runs.requests_dir(handoff.checkpoint_dir(self.state, self.key))

    def put_request(self, name, body):
        d = self.req_dir()
        os.makedirs(d, exist_ok=True)
        Path(d, name).write_text(json.dumps(body))
        return os.path.join(d, name)

    def panel_with(self, seats, asks=(), state="running", job=None, created=None):
        p = pstate.create(self.state, self.key, {"worktree": self.wt, "created": NOW - 60 if created is None else created})

        def fn(q):
            q.update(seats=seats, asks=list(asks), state=state, job=job)
        pstate.update(self.state, self.key, p["id"], fn)
        return p["id"]

    def build(self, **kw):
        return live.build(self.config, self.state, self.wt, now=NOW, **kw)

    def main(self, argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}):
            rc = live.main(argv, out, err)
        self.last_err = err.getvalue()
        text = out.getvalue()
        return rc, (json.loads(text) if text.strip() else None)

    # --- the document ---

    def test_empty_state_gives_a_v1_document(self):
        d = self.build()
        self.assertEqual(set(d), {"v", "now", "complete", "open", "recent", "usage", "local", "panels"})
        self.assertEqual((d["v"], d["open"], d["recent"], d["complete"]), (1, [], [], True))
        self.assertEqual((d["now"], d["usage"], d["panels"]), (NOW, [], []))

    def test_entry_keys_are_exact(self):
        s = self.start("my-seat", pid=os.getpid(), host=socket.gethostname())
        done = self.start("astra", source="bw")
        self.end(done)
        d = self.build()
        self.assertEqual(set(d["open"][0]), OPEN_KEYS)
        self.assertEqual(set(d["recent"][0]), RECENT_KEYS)
        self.assertEqual(d["open"][0]["run_id"], s["run_id"])
        self.assertEqual(set(d["local"]), {"seats", "logs", "families", "run_max_s"})
        self.assertEqual(d["local"]["seats"], [{"name": "my-seat", "match": "my-seat.sh", "metric": "my-seat",
                                                "model": "model-a", "icon": "🦉", "color": "#2dd4bf"}])
        self.assertEqual(d["local"]["families"], {"model": "#112233"})

    def test_liveness_rules(self):  # Review Focus 4
        host = socket.gethostname()

        def mk(**k):
            return {"t": NOW - 10, **k}

        def alive(pid):
            return pid == 42
        self.assertEqual(live.run_state(mk(pid=42, host=host), NOW, host, 2700, alive), "live")
        self.assertEqual(live.run_state(mk(pid=43, host=host), NOW, host, 2700, alive), "unknown")
        self.assertEqual(live.run_state(mk(pid=43, host="other"), NOW, host, 2700, alive), "live")
        self.assertEqual(live.run_state({"t": NOW - 2701}, NOW, host, 2700, alive), "stale")
        self.assertEqual(live.run_state({"t": NOW - 2701}, NOW, host, 2700, alive, last=NOW - 5), "live")
        self.assertEqual(live.run_state(mk(pid=True, host=host), NOW, host, 2700, alive), "live")  # a bool is no pid
        self.assertEqual(live.run_state({"ts": "2026-11-27T00:00:00Z"},
                                        store.row_time({"ts": "2026-11-27T00:00:00Z"}) + 5, host, 2700, alive), "live")
        self.assertEqual(live.run_state({}, NOW, host, 2700, alive), "stale")

    def test_pid_alive(self):
        self.assertTrue(live.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(live.pid_alive(p.pid))

    def test_open_recent_and_join(self):
        s = self.start("astra", source="bw")
        self.end(s, ok=True)
        store.append(self.log, {"type": "usage", "seat": "astra", "run_id": s["run_id"], "tokens_in": 10,
                                "tokens_out": 2, "cost_usd": 0.01, "dir": self.wt})
        o = self.start("my-seat", source="local", pid=os.getpid(), host=socket.gethostname())
        d = self.build()
        self.assertEqual([x["run_id"] for x in d["open"]], [o["run_id"]])
        self.assertEqual((d["recent"][0]["tokens_in"], d["recent"][0]["cost_usd"]), (10, 0.01))
        self.assertEqual(d["open"][0]["icon"], "🦉")
        self.assertEqual((d["open"][0]["color"], d["open"][0]["state"], d["open"][0]["start"]),
                         ("#2dd4bf", "live", NOW - 10))
        r = d["recent"][0]
        self.assertEqual((r["run_id"], r["model"], r["label"], r["start"], r["end"], r["source"], r["ok"]),
                         (s["run_id"], "model-a", "astra job", NOW - 10, NOW - 5, "bw", True))
        self.assertEqual(d["usage"], [])  # a usage row with a run_id is joined, not listed

    def test_end_tokens_win_over_the_usage_row(self):
        s = self.start("astra", source="bw")
        self.end(s, tokens_in=7, cost_usd=None)
        store.append(self.log, {"type": "usage", "seat": "astra", "run_id": s["run_id"], "tokens_in": 10,
                                "tokens_out": 2, "cost_usd": 0.5, "dir": self.wt})
        r = self.build()["recent"][0]
        self.assertEqual((r["tokens_in"], r["tokens_out"], r["cost_usd"]), (7, 2, 0.5))

    def test_other_worktree_and_old_rows_are_out(self):
        self.start("my-seat", dir=self.other, pid=os.getpid(), host=socket.gethostname())
        s = self.start("astra", source="bw", dir=self.other)
        self.end(s)
        old = runs.start_row("my-seat", "m", self.wt, "old", "local", t=NOW - 3 * 86400)
        store.append(self.log, {**old, "ts": ts(NOW - 3 * 86400)})
        old_end = runs.end_row(old["run_id"], "my-seat", self.wt, True, None, 1.0, t=NOW - 3 * 86400 + 5)
        store.append(self.log, {**old_end, "ts": ts(NOW - 3 * 86400 + 5)})
        store.append(self.log, {"type": "usage", "seat": "astra", "dir": self.other, "ok": True})
        d = self.build()
        self.assertEqual((d["open"], d["recent"], d["usage"]), ([], [], []))

    def test_an_end_closes_only_its_own_worktree_run(self):  # join by (run_id, dir), never run_id alone
        s = self.start("my-seat", pid=os.getpid(), host=socket.gethostname())
        store.append(self.log, runs.end_row(s["run_id"], "my-seat", self.other, True, None, 1.0, t=NOW - 2))
        store.append(self.log, runs.end_row(s["run_id"], None, None, True, None, None, t=NOW - 2))
        d = self.build()
        self.assertEqual([o["run_id"] for o in d["open"]], [s["run_id"]])
        self.assertEqual(d["recent"], [])

    def test_an_end_with_no_start_in_reach_opens_nothing_and_is_not_recent(self):
        store.append(self.log, runs.end_row("r0123456789ab", "my-seat", self.wt, True, None, 1.0, t=NOW - 2))
        store.append(self.log, runs.end_row("r0123456789ac", None, None, False, "exit 1", None, t=NOW - 2))
        d = self.build()
        self.assertEqual((d["open"], d["recent"]), ([], []))

    def test_since_and_limit(self):
        for i in range(4):
            s = self.start("my-seat", t=NOW - 100 + i)
            self.end(s, t=NOW - 50 + i)
        for i in range(3):
            store.append(self.log, {"type": "usage", "seat": "my-seat", "dir": self.wt, "ok": True, "t": NOW - 40 + i})
        d = self.build(limit=2)
        self.assertEqual([r["end"] for r in d["recent"]], [NOW - 47, NOW - 48])
        self.assertEqual([u["t"] for u in d["usage"]], [NOW - 38, NOW - 39])
        d = self.build(since=NOW - 48.5)
        self.assertEqual([r["end"] for r in d["recent"]], [NOW - 47, NOW - 48])
        d = self.build(since=NOW - 38.5)
        self.assertEqual((d["recent"], [u["t"] for u in d["usage"]]), ([], [NOW - 38]))

    def test_usage_rows_without_run_id_listed(self):
        store.append(self.log, {"type": "usage", "seat": "my-seat", "dir": self.wt, "ok": False, "t": NOW - 3})
        store.append(self.log, {"type": "usage", "seat": "my-seat", "dir": self.wt, "ok": True, "t": NOW - 2,
                                "run_id": "r0123456789ab"})
        store.append(self.log, {"type": "usage", "seat": "my-seat", "dir": self.other, "ok": True, "t": NOW - 1})
        self.assertEqual(self.build()["usage"],
                         [{"seat": "my-seat", "t": NOW - 3, "dir": self.wt, "ok": False, "source": "bw"}])

    def test_extra_log_rows_are_local(self):
        extra = os.path.join(self.tmp, "extra.jsonl")
        store.append(extra, {"type": "usage", "seat": "my-seat", "dir": self.wt, "ok": True, "t": NOW - 3})
        s = runs.start_row("my-seat", "m", self.wt, "x", "bw", t=NOW - 9, pid=os.getpid(), host=socket.gethostname())
        store.append(extra, s)
        d = live.build(self.with_local_logs([extra]), self.state, self.wt, now=NOW)
        self.assertEqual([u["source"] for u in d["usage"]], ["local"])
        self.assertEqual([(o["run_id"], o["source"]) for o in d["open"]], [(s["run_id"], "local")])
        self.assertEqual(d["local"]["logs"], [{"path": extra, "ok": True, "error": None}])

    def test_foreign_rows_never_raise(self):
        extra = os.path.join(self.tmp, "extra.jsonl")
        for row in ({"type": "run", "phase": "start", "run_id": ["x"], "dir": self.wt},
                    {"type": "run", "phase": "end", "run_id": {"a": 1}, "dir": self.wt},
                    {"type": "run", "phase": ["start"], "run_id": "r0123456789ab", "dir": self.wt},
                    {"type": "run", "phase": "start", "run_id": "r0123456789ab", "dir": [self.wt]},
                    {"type": "run", "phase": "beat", "run_id": "r0123456789ac", "dir": self.wt, "seat": 5, "t": "x"},
                    {"type": "usage", "dir": self.wt, "run_id": ["r"], "seat": {"x": 1}},
                    {"type": "run", "phase": "start", "run_id": "r0123456789ad", "dir": self.wt, "pid": 10 ** 30,
                     "host": socket.gethostname(), "seat": "my-seat", "t": NOW - 5}):
            store.append(extra, row)
        d = live.build(self.with_local_logs([extra]), self.state, self.wt, now=NOW)
        self.assertEqual(d["v"], 1)
        json.dumps(d)

    # --- panels ---

    def test_panel_seat_is_never_shown_twice(self):
        pid = self.panel_with(seats={"astra": {"status": "running"}}, asks=[{"id": "a1", "seat": "astra", "state": "running"}])
        self.start("astra", source="bw", panel=pid, pid=os.getpid(), host=socket.gethostname())
        d = self.build()
        self.assertEqual([(o["seat"], o["panel"], o["ask"]) for o in d["open"]], [("astra", pid, "a1")])

    def test_two_open_rows_of_one_panel_seat_show_once(self):
        pid = self.panel_with(seats={"astra": {"status": "ok"}}, asks=[{"id": "a1", "seat": "astra", "state": "running"}])
        self.start("astra", source="bw", panel=pid, t=NOW - 50, pid=os.getpid(), host=socket.gethostname())
        newer = self.start("astra", source="bw", panel=pid, t=NOW - 5, pid=os.getpid(), host=socket.gethostname())
        self.assertEqual([o["run_id"] for o in self.build()["open"]], [newer["run_id"]])

    def test_panel_done_closes_an_open_row(self):
        pid = self.panel_with(seats={"astra": {"status": "ok"}, "gem": {"status": "absent"},
                                     "opus": {"status": "running"}})
        for seat in ("astra", "gem", "opus"):
            self.start(seat, source="bw", panel=pid, pid=os.getpid(), host=socket.gethostname())
        self.assertEqual([o["seat"] for o in self.build()["open"]], ["opus"])

    def test_a_logged_panel_closes_its_rows_too(self):
        pid = self.panel_with(seats={"astra": {"status": "ok"}}, state="logged")
        self.start("astra", source="bw", panel=pid, pid=os.getpid(), host=socket.gethostname())
        d = self.build()
        self.assertEqual((d["open"], d["panels"]), ([], []))

    def test_running_ask_without_a_run_row_shows(self):
        self.panel_with(seats={"astra": {"status": "ok", "session_id": "s1"}},
                        asks=[{"id": "a1", "seat": "astra", "state": "running", "ts": NOW - 3}])
        d = self.build()
        self.assertEqual([(o["seat"], o["ask"], o["run_id"]) for o in d["open"]], [("astra", "a1", None)])
        o = d["open"][0]
        self.assertEqual((o["source"], o["state"], o["start"], o["dir"], o["model"]),
                         ("bw", "live", NOW - 3, self.wt, self.config["seats"]["astra"]["model"]))
        self.assertEqual(set(o), OPEN_KEYS)

    def test_running_panel_seat_without_a_run_row_shows(self):
        pid = self.panel_with(seats={"astra": {"status": "running"}, "gem": {"status": "pending"}},
                              job={"kind": "round1", "started": NOW - 20})
        d = self.build()
        self.assertEqual([(o["seat"], o["panel"], o["ask"], o["start"]) for o in d["open"]], [("astra", pid, None, NOW - 20)])
        pid2 = self.panel_with(seats={"opus": {"status": "running"}}, created=NOW - 30)
        self.assertIn(("opus", NOW - 30), [(o["seat"], o["start"]) for o in self.build()["open"] if o["panel"] == pid2])

    def test_failed_panel_running_seat_is_not_shown(self):
        self.panel_with(seats={"astra": {"status": "running"}}, state="failed")
        d = self.build()
        self.assertEqual((d["open"], d["panels"]), ([], []))

    def test_panels_section(self):
        pid = self.panel_with(seats={"astra": {"status": "running"}, "gem": {}},
                              asks=[{"id": "a1", "seat": "astra", "state": "queued", "claim": "c"}])
        self.assertEqual(self.build()["panels"], [{"panel": pid, "state": "running",
                                                   "seats": {"astra": "running", "gem": "pending"},
                                                   "asks": [{"id": "a1", "seat": "astra", "state": "queued"}]}])

    # --- pending request files (contained leads) ---

    def test_pending_request_shows_without_a_service(self):
        path = self.put_request("r0123456789ab.start.json", {"seat": "my-seat", "t": NOW - 5, "label": "wrap"})
        d = self.build()
        self.assertEqual([(o["run_id"], o["seat"], o["source"], o["dir"], o["label"], o["state"]) for o in d["open"]],
                         [("r0123456789ab", "my-seat", "local", self.wt, "wrap", "live")])
        self.assertTrue(os.path.exists(path))

    def test_pending_pair_and_pending_end_are_joined_first(self):
        # start and end both as request files, no service: the run is in recent, not open
        self.put_request("r0123456789ab.start.json", {"seat": "s", "t": NOW - 5})
        self.put_request("r0123456789ab.end.json", {"ok": True, "t": NOW - 1})
        # a logged start whose end is only pending is closed too
        store.append(self.log, {**runs.start_row("t", None, self.wt, "x", "local"), "run_id": "r0123456789ac", "t": NOW - 5})
        self.put_request("r0123456789ac.end.json", {"ok": False, "reason": "exit 1", "t": NOW - 1})
        d = self.build()
        self.assertEqual(d["open"], [])
        self.assertEqual(sorted(r["run_id"] for r in d["recent"]), ["r0123456789ab", "r0123456789ac"])
        self.assertEqual({r["run_id"]: r["seat"] for r in d["recent"]}, {"r0123456789ab": "s", "r0123456789ac": "t"})

    def test_pending_rows_that_ingest_refuses_are_ignored(self):
        s = self.start("astra", source="bw", pid=os.getpid(), host=socket.gethostname())
        self.put_request(f"{s['run_id']}.end.json", {"ok": True, "t": NOW - 1})  # bw seat closes its own run
        self.put_request(f"{s['run_id']}.start.json", {"seat": "my-seat", "t": NOW - 1})  # a duplicate start
        self.put_request("r0123456789ab.start.json", {"seat": "BAD!", "t": NOW - 1})  # invalid
        self.put_request("r0123456789ac.start.json", "not an object")
        d = self.build()
        self.assertEqual([(o["run_id"], o["seat"], o["source"]) for o in d["open"]], [(s["run_id"], "astra", "bw")])
        self.assertEqual(d["recent"], [])

    # --- PD-13 beats ---

    def test_a_beat_keeps_an_old_run_found_and_live(self):  # PD-13
        s = self.start("my-seat", source="local", t=NOW - 3 * 2700, host="other")
        store.append(self.log, runs.beat_row(s["run_id"], "my-seat", self.wt, t=NOW - 100))
        self.assertEqual([(o["run_id"], o["state"]) for o in self.build()["open"]], [(s["run_id"], "live")])

    def test_a_beat_finds_a_run_whose_start_is_out_of_reach(self):  # F2
        s = runs.start_row("my-seat", "model-a", self.wt, "long wrap", "local", t=NOW - 8100, host="other")
        store.append(self.log, {**s, "ts": ts(NOW - 8100)})
        store.append(self.log, {**runs.beat_row(s["run_id"], "my-seat", self.wt, t=NOW - 4000), "ts": ts(NOW - 4000)})
        store.append(self.log, runs.beat_row(s["run_id"], "my-seat", self.wt, t=NOW - 100))
        d = self.build(since=NOW - 3600)
        self.assertEqual([(o["run_id"], o["seat"], o["state"], o["start"], o["model"], o["label"], o["source"], o["icon"])
                          for o in d["open"]], [(s["run_id"], "my-seat", "live", None, None, None, "local", "🦉")])
        store.append(self.log, runs.end_row(s["run_id"], "my-seat", self.wt, True, None, 8099.0, t=NOW - 1))
        d = self.build(since=NOW - 3600)
        self.assertEqual((d["open"], d["recent"]), ([], []))  # closed; no start joins it, so not in recent

    def test_an_old_beat_is_stale(self):
        s = runs.start_row("my-seat", "model-a", self.wt, "x", "local", t=NOW - 9000)
        store.append(self.log, {**s, "ts": ts(NOW - 9000)})
        store.append(self.log, runs.beat_row(s["run_id"], "my-seat", self.wt, t=NOW - 2800))
        self.assertEqual([o["state"] for o in self.build(since=NOW - 3600)["open"]], ["stale"])

    # --- local section and logs ---

    def test_local_holds_run_max_s(self):
        self.assertEqual(live.build(self.with_local(run_max_s=60), self.state, self.wt, now=NOW)["local"]["run_max_s"], 60)
        self.assertEqual(self.build()["local"]["run_max_s"], 2700)

    def test_run_max_s_sets_the_expiry(self):
        self.start("my-seat", t=NOW - 100)
        cfg = self.with_local(run_max_s=60)
        self.assertEqual([o["state"] for o in live.build(cfg, self.state, self.wt, now=NOW)["open"]], ["stale"])

    def test_extra_log_missing_is_reported(self):
        d = live.build(self.with_local_logs(["/nonexistent/x.jsonl"]), self.state, self.wt, now=NOW)
        self.assertEqual(d["local"]["logs"], [{"path": "/nonexistent/x.jsonl", "ok": False, "error": "missing"}])
        self.assertTrue(d["complete"])

    def test_extra_log_unreadable_is_reported(self):
        d = live.build(self.with_local_logs([self.tmp]), self.state, self.wt, now=NOW)
        [entry] = d["local"]["logs"]
        self.assertEqual((entry["path"], entry["ok"]), (self.tmp, False))
        self.assertIsInstance(entry["error"], str)
        self.assertFalse(d["complete"])

    def test_dir_resolution(self):
        sub = os.path.join(self.wt, "sub")
        link = os.path.join(self.tmp, "wt-link")
        os.symlink(self.wt, link)
        for d in (sub, link):
            self.assertEqual(live.worktree(d, self.config), self.wt)
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        self.assertEqual(live.worktree(plain, self.config), os.path.realpath(plain))  # no traceback outside git
        s = self.start("my-seat", pid=os.getpid(), host=socket.gethostname())
        self.assertEqual([o["run_id"] for o in live.build(self.config, self.state, sub, now=NOW)["open"]], [s["run_id"]])

    # --- main ---

    def test_main_prints_the_document(self):
        s = self.start("my-seat", pid=os.getpid(), host=socket.gethostname(), t=time.time() - 1)
        rc, doc = self.main(["--json", "--dir", self.wt, "--since", str(int(time.time()) - 60), "--limit", "5"])
        self.assertEqual((rc, doc["v"], [o["run_id"] for o in doc["open"]]), (0, 1, [s["run_id"]]))
        self.assertNotIn("error", doc)

    def test_main_config_error_and_usage_errors(self):
        rc, doc = self.main(["--json"], env={"BALANCEWHEEL_CONFIG": "/nonexistent.json"})
        self.assertEqual((rc, doc["v"], doc["complete"]), (0, 1, False))
        self.assertIn("config error", doc["error"])
        self.assertEqual((doc["open"], doc["recent"], doc["usage"], doc["panels"]), ([], [], [], []))
        self.assertEqual(doc["local"]["run_max_s"], 2700)
        for argv in ([], ["--json", "--limit", "0"], ["--json", "--since", "nan"], ["--json", "--since", "inf"],
                     ["--json", "--limit", "501"], ["--json", "--limit", "x"], ["--json", "--bogus"],
                     ["--json", "--dir"]):
            self.assertEqual(self.main(argv)[0], 2, argv)
        self.main([])
        self.assertIn("bw live: only --json output exists", self.last_err)

    def test_live_writes_nothing(self):
        self.start("astra", source="bw", pid=os.getpid(), host=socket.gethostname())
        self.panel_with(seats={"astra": {"status": "running"}})
        self.put_request("r0123456789ab.start.json", {"seat": "s", "t": NOW})
        before = snapshot(self.state), Path(self.log).read_bytes(), snapshot(self.home)
        rc, _ = self.main(["--json", "--dir", self.wt])
        self.assertEqual(rc, 0)
        self.assertEqual((snapshot(self.state), Path(self.log).read_bytes(), snapshot(self.home)), before)


def store_config():
    from lead.paths import load_config
    return load_config()


if __name__ == "__main__":
    unittest.main()
