import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from panel import state  # noqa: E402
import store  # noqa: E402  (metrics/, on sys.path through lead.paths)

PANEL = {"worktree": "/w", "profile": "default", "checkpoint": "design", "target": "docs/x.md", "focus": "",
         "stakes": "normal", "paths": [], "tier": "standard", "roster": ["a", "o"], "originals": ["a", "o"],
         "fallbacks": ["g"], "session_id": "s1", "lead": "claude"}
JOB = {"kind": "round1", "pid": None, "owner": "scheduler", "started": 0.0, "deadline": 1.0, "beat": 0.0,
       "seat_pgids": []}

# One child process: N updates on one panel, each appending one ask.
ADD_ASKS = """
import sys
sys.path.insert(0, sys.argv[1])
import os, time
from panel import state
S, pid, tag, go = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
while not os.path.exists(go):
    time.sleep(0.001)
for i in range(20):
    state.update(S, "k", pid, lambda p: p["asks"].append({"id": f"{tag}-{i}", "state": "done"}))
"""

# One child process: reserve its own panel 20 times; while it holds the job, the other panel must hold none.
RACE = """
import sys
sys.path.insert(0, sys.argv[1])
import os, time
from panel import state
S, mine, other, go = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
while not os.path.exists(go):
    time.sleep(0.001)
job = {"kind": "round1", "pid": None, "owner": "scheduler", "started": 0.0, "deadline": 1.0, "beat": 0.0,
       "seat_pgids": []}
won = clash = 0
for _ in range(20):
    while not state.reserve_job(S, "k", mine, job):
        pass
    won += 1
    if state.load(S, "k", other)["job"] is not None:
        clash += 1
    state.release_job(S, "k", mine)
print(won, clash)
"""


class PanelStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        global S, LOG, CFG
        S = os.path.join(self.tmp.name, "state")
        LOG = os.path.join(self.tmp.name, "metrics.jsonl")
        CFG = {"metrics_log": LOG}
        mock_env = {"BW_STATE_DIR": S, "BALANCEWHEEL_METRICS_LOG": LOG}
        self.saved = {k: os.environ.get(k) for k in mock_env}
        os.environ.update(mock_env)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_paths(self):
        self.assertEqual(state.request_dir(S, "k"), os.path.join(S, "handoffs", "k", "panel"))
        self.assertEqual(state.view_root(S, "k"), os.path.join(S, "inbox-view", "k", "panels"))
        self.assertEqual(state.view_dir(S, "k", "p1234567"), os.path.join(S, "inbox-view", "k", "panels", "p1234567"))
        self.assertEqual(state.work_dir(S, "k", "p1234567"), os.path.join(S, "panels", "k", "p1234567"))

    def test_paths_refuse_bad_key_and_id(self):
        for key in ("", "..", "a/b", "../k", "k\n"):
            self.assertRaises(ValueError, state.request_dir, S, key)
            self.assertRaises(ValueError, state.view_root, S, key)
        for pid in ("../p1234567", "p123456", "p12345678", "P1234567", "p123456g", "p1234567\n"):
            self.assertRaises(ValueError, state.view_dir, S, "k", pid)
            self.assertRaises(ValueError, state.work_dir, S, "k", pid)

    def test_constants(self):
        self.assertEqual(state.STATES, ("queued", "running", "ready", "logged", "failed"))
        self.assertEqual(state.OPEN, ("queued", "running", "ready"))
        self.assertEqual((state.MAX_OPEN, state.MAX_ASKS), (3, 20))
        self.assertIsNone(state.PANEL_ID.fullmatch("p1234567x"))

    def test_id_shape(self):
        self.assertRegex(state.new_id(S, "k"), r"^p[0-9a-f]{7}$")

    def test_create_shape(self):
        p = state.create(S, "k", dict(PANEL, junk=1, state="ready", job=JOB))
        self.assertRegex(p["id"], r"^p[0-9a-f]{7}$")
        self.assertEqual((p["v"], p["project"], p["state"], p["job"], p["asks"], p["seats"]), (1, "k", "queued", None, [], {}))
        self.assertEqual((p["error"], p["short"], p["judged"], p["record_id"], p["run_logged"]), (None, None, None, None, False))
        self.assertNotIn("junk", p)
        self.assertIsInstance(p["created"], float)
        self.assertEqual(state.load(S, "k", p["id"]), p)
        b = state.create(S, "k", PANEL)
        self.assertNotEqual(p["id"], b["id"])

    def test_dirs_are_private(self):
        p = state.create(S, "k", PANEL)
        for d in (state.work_dir(S, "k", p["id"]), state.view_dir(S, "k", p["id"]),
                  os.path.join(state.view_dir(S, "k", p["id"]), "seats"),
                  os.path.join(state.view_dir(S, "k", p["id"]), "asks")):
            self.assertTrue(os.path.isdir(d), d)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700, d)

    def test_create_update_status(self):
        p = state.create(S, "k", PANEL)
        out = state.update(S, "k", p["id"], lambda x: x.update(state="running"))
        self.assertEqual(out["state"], "running")
        st = json.loads(Path(state.view_dir(S, "k", p["id"]), "status.json").read_text())
        self.assertEqual(st["state"], "running")
        self.assertNotIn("target", st)

    def test_update_keeps_id_and_project(self):
        p = state.create(S, "k", PANEL)
        out = state.update(S, "k", p["id"], lambda x: x.update(id="p0000000", project="other"))
        self.assertEqual((out["id"], out["project"]), (p["id"], "k"))
        self.assertRaises(ValueError, state.update, S, "k", "p0000000", lambda x: None)

    def test_status_view(self):
        p = dict(state.create(S, "k", PANEL), state="ready", short=True, error=None)
        p["seats"] = {"a": {"status": "absent", "reason": "usage cap"}, "g": {"status": "ok", "stand_in_for": "a"},
                      "o": {"status": "ok"}, "x": {"status": "running"}, "y": {"status": "pending"}}
        p["asks"] = [{"id": "d1-1-o-1", "state": "running", "claim": "SECRET CLAIM"},
                     {"id": "d1-1-o-2", "state": "done", "claim": "SECRET CLAIM"}]
        view = state.view_dir(S, "k", p["id"])
        st = state.status_view(p, view)
        self.assertEqual(st["seats"], {"a": "absent:usage cap", "g": "stand-in:a", "o": "ok", "x": "running",
                                       "y": "pending"})
        self.assertEqual((st["v"], st["panel"], st["checkpoint"], st["state"], st["short"], st["busy"]),
                         (1, p["id"], "design", "ready", True, True))
        self.assertEqual(st["next"], os.path.join(view, "next.md"))
        self.assertEqual(st["asks"]["d1-1-o-1"], {"state": "running", "answer": os.path.join(view, "asks", "d1-1-o-1.md")})
        self.assertNotIn("SECRET", json.dumps(st))
        self.assertNotIn("target", st)
        queued = state.status_view(dict(p, state="queued", asks=[]), view)
        self.assertEqual((queued["next"], queued["busy"]), (None, False))

    def test_concurrent_updates_keep_both(self):
        p = state.create(S, "k", PANEL)
        go = os.path.join(self.tmp.name, "go")
        procs = [subprocess.Popen([sys.executable, "-c", ADD_ASKS, str(ROOT), S, p["id"], tag, go]) for tag in ("x", "y")]
        Path(go).touch()
        for pr in procs:
            self.assertEqual(pr.wait(timeout=60), 0)
        self.assertEqual(len(state.load(S, "k", p["id"])["asks"]), 40)

    def test_open_panels(self):
        ids = []
        for st in ("queued", "ready", "logged", "failed"):
            p = state.create(S, "k", PANEL)
            state.update(S, "k", p["id"], lambda x, st=st: x.update(state=st))
            ids.append(p["id"])
        self.assertEqual([p["id"] for p in state.panels(S, "k")], ids)
        self.assertEqual([p["id"] for p in state.open_panels(S, "k")], ids[:2])
        self.assertEqual([p["id"] for p in state.unlogged(S, "k")], [ids[0], ids[1], ids[3]])
        state.update(S, "k", ids[3], lambda x: x.update(record_id=ids[3]))
        self.assertEqual(len(state.unlogged(S, "k")), 2)
        self.assertEqual(state.panels(S, "nokey"), [])

    def test_open_asks(self):
        p = {"asks": [{"id": "a", "state": "queued"}, {"id": "b", "state": "running"}, {"id": "c", "state": "done"},
                      {"id": "d", "state": "failed"}]}
        self.assertEqual([a["id"] for a in state.open_asks(p)], ["a", "b"])

    def test_load_refuses_bad_id_and_other_worktree(self):
        p = state.create(S, "other", PANEL)
        self.assertIsNone(state.load(S, "k", "../other/" + p["id"]))
        self.assertIsNone(state.load(S, "k", p["id"]))
        self.assertIsNone(state.load(S, "../k", p["id"]))
        # a panel.json copied under another worktree's dir still names its own project
        src = Path(state.work_dir(S, "other", p["id"]), "panel.json")
        dst = Path(state.work_dir(S, "k", p["id"]))
        dst.mkdir(parents=True)
        (dst / "panel.json").write_text(src.read_text())
        self.assertIsNone(state.load(S, "k", p["id"]))
        self.assertEqual(state.load(S, "other", p["id"])["id"], p["id"])

    def test_load_refuses_symlinks(self):
        p = state.create(S, "k", PANEL)
        f = Path(state.work_dir(S, "k", p["id"]), "panel.json")
        real = Path(self.tmp.name, "real.json")
        f.rename(real)
        f.symlink_to(real)
        self.assertIsNone(state.load(S, "k", p["id"]))
        # a symlinked panel dir is refused too
        q = state.create(S, "k", PANEL)
        d = Path(state.work_dir(S, "k", q["id"]))
        moved = Path(self.tmp.name, "moved")
        d.rename(moved)
        d.symlink_to(moved)
        self.assertIsNone(state.load(S, "k", q["id"]))
        self.assertEqual([x["id"] for x in state.panels(S, "k")], [])

    def test_writes_refuse_a_symlinked_view_dir(self):
        p = state.create(S, "k", PANEL)
        view = Path(state.view_dir(S, "k", p["id"]))
        elsewhere = Path(self.tmp.name, "elsewhere")
        elsewhere.mkdir()
        os.rename(view, Path(self.tmp.name, "old-view"))
        view.symlink_to(elsewhere)
        self.assertRaises(OSError, state.update, S, "k", p["id"], lambda x: x.update(state="running"))
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_reserve_job_is_exclusive(self):
        a, b = state.create(S, "k", PANEL), state.create(S, "k", PANEL)
        self.assertFalse(state.has_job(S, "k"))
        self.assertTrue(state.reserve_job(S, "k", a["id"], JOB))
        self.assertTrue(state.has_job(S, "k"))
        self.assertFalse(state.reserve_job(S, "k", b["id"], JOB))
        self.assertFalse(state.reserve_job(S, "k", a["id"], JOB))
        self.assertEqual(state.load(S, "k", a["id"])["job"], JOB)
        state.release_job(S, "k", a["id"])
        self.assertTrue(state.reserve_job(S, "k", b["id"], JOB))
        other = state.create(S, "other", PANEL)
        self.assertTrue(state.reserve_job(S, "other", other["id"], JOB))  # one job per worktree, not global
        self.assertRaises(ValueError, state.reserve_job, S, "k", "p0000000", JOB)
        state.release_job(S, "k", "p0000000")  # a missing panel: nothing to clear

    def test_reserve_job_across_processes(self):
        a, b = state.create(S, "k", PANEL), state.create(S, "k", PANEL)
        go = os.path.join(self.tmp.name, "go")
        procs = [subprocess.Popen([sys.executable, "-c", RACE, str(ROOT), S, mine, other, go], stdout=subprocess.PIPE,
                                  text=True) for mine, other in ((a["id"], b["id"]), (b["id"], a["id"]))]
        Path(go).touch()
        for pr in procs:
            out, _ = pr.communicate(timeout=120)
            self.assertEqual(pr.returncode, 0)
            self.assertEqual(out.split(), ["20", "0"])
        self.assertFalse(state.has_job(S, "k"))

    def test_run_event(self):
        p = state.create(S, "k", PANEL)
        p = dict(p, state="ready", short=False, seats={"a": {"status": "ok"}, "g": {"status": "ok", "stand_in_for": "o"},
                                                        "o": {"status": "absent", "reason": "usage cap"}})
        ev = state.run_event(p, 431.0, 7)
        self.assertEqual(ev, {"type": "panel_run", "panel_id": p["id"], "project": "k", "checkpoint": "design",
                              "tier": "standard", "seats": {"a": "ok", "g": "stand-in:o", "o": "absent:usage cap"},
                              "duration_s": 431.0, "findings_raw": 7, "short": False, "state": "ready"})

    def test_run_event_once(self):
        pid = state.create(S, "k", PANEL)["id"]
        state.update(S, "k", pid, lambda x: x.update(state="ready", short=True))
        state.record_run(CFG, S, "k", pid, 7, 0.0)
        state.record_run(CFG, S, "k", pid, 7, 0.0)
        runs = store.load(LOG)["panel_runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual((runs[0]["panel_id"], runs[0]["findings_raw"], runs[0]["state"]), (pid, 7, "ready"))
        self.assertTrue(state.load(S, "k", pid)["run_logged"])

    def test_run_event_duration(self):
        p = state.create(S, "k", PANEL)
        state.record_run(CFG, S, "k", p["id"], 0, p["created"] + 12.34)
        self.assertEqual(store.load(LOG)["panel_runs"][0]["duration_s"], 12.3)

    def test_corrupt_panel_json_reads_none(self):
        p = state.create(S, "k", PANEL)
        q = state.create(S, "k", PANEL)
        Path(state.work_dir(S, "k", p["id"]), "panel.json").write_text("{not json")
        Path(state.work_dir(S, "k", q["id"]), "panel.json").write_text("[1, 2]")
        self.assertIsNone(state.load(S, "k", p["id"]))
        self.assertIsNone(state.load(S, "k", q["id"]))
        self.assertEqual(state.panels(S, "k"), [])
        self.assertRaises(ValueError, state.update, S, "k", p["id"], lambda x: None)


if __name__ == "__main__":
    unittest.main()
