"""lead/runs.py: run request files from contained leads (validate_request, read_requests, ingest)."""
import json
import math
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import handoff, runs  # noqa: E402
from lead.projects import Project  # noqa: E402
from metrics import store  # noqa: E402

K = "wt-k"


class RunRequests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state")
        self.log = os.path.join(self.tmp, "metrics.jsonl")
        wt = os.path.join(self.tmp, "wt")
        os.makedirs(wt)
        self.project = Project(wt, K, None, None, "p")
        self.config = {}
        self.runs_dir = runs.requests_dir(handoff.checkpoint_dir(self.state, K))
        os.makedirs(self.runs_dir)
        self.now = time.time()
        env = mock.patch.dict(os.environ, {"BALANCEWHEEL_METRICS_LOG": self.log, "BW_STATE_DIR": self.state})
        env.start()
        self.addCleanup(env.stop)

    def put(self, name, obj):
        self.put_bytes(name, json.dumps(obj).encode())

    def put_bytes(self, name, data):
        Path(self.runs_dir, name).write_bytes(data)

    def logged(self):
        return store.load(self.log)["runs"]

    # --- the brief's tests ---

    def test_valid_start_and_end_are_appended_with_forced_fields(self):
        self.put("r0123456789ab.start.json", {**runs.start_row("my-seat", "model-a", "/elsewhere", "x", "bw"),
                                              "run_id": "r0123456789ab"})
        self.put("r0123456789ab.end.json", {"run_id": "r0123456789ab", "ok": True, "t": self.now})  # no seat, no dir
        self.assertEqual(runs.ingest(self.config, self.state, self.project, now=self.now), [])
        s, e = self.logged()
        self.assertEqual((s["source"], s["dir"], e["seat"], e["dir"]),
                         ("local", self.project.worktree, "my-seat", self.project.worktree))
        self.assertEqual((s["phase"], e["phase"], e["ok"]), ("start", "end", True))
        self.assertEqual(os.listdir(self.runs_dir), [])

    def test_hostile_files_are_refused(self):
        os.symlink("/etc/hosts", os.path.join(self.runs_dir, "r0123456789ab.start.json"))
        self.put_bytes("r0123456789ac.start.json", b"x" * 5000)
        self.put("r0123456789ad.start.json", {"seat": "BAD NAME", "t": self.now})
        self.put("r0123456789ae.start.json", {"seat": "s", "t": self.now + 10 * 86400})
        self.put("notes.txt", {})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(self.logged(), [])
        self.assertTrue(probs)
        self.assertTrue(os.path.exists(os.path.join(self.runs_dir, "notes.txt")))  # not ours: left alone
        self.assertEqual(os.listdir(self.runs_dir), ["notes.txt"])

    def test_end_for_a_bw_run_is_refused(self):
        store.append(self.log, {**runs.start_row("astra", "m", self.project.worktree, "x", "bw"),
                                "run_id": "r0123456789ab"})
        self.put("r0123456789ab.end.json", {"run_id": "r0123456789ab", "seat": "astra", "ok": True, "t": self.now})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertTrue(probs)
        self.assertIn("bw seat", "; ".join(probs))
        self.assertEqual([r["phase"] for r in self.logged()], ["start"])

    def test_read_only_mode_leaves_files(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        items, _ = runs.read_requests(self.runs_dir, unlink=False)
        self.assertEqual(len(items), 1)
        self.assertEqual(len(os.listdir(self.runs_dir)), 1)

    def test_bounded_per_call(self):
        for i in range(runs.MAX_RUN_REQUESTS + 5):
            self.put(f"r{i:012x}.start.json", {"seat": "s", "t": self.now})
        runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(len(os.listdir(self.runs_dir)), 5)
        self.assertEqual(len(self.logged()), runs.MAX_RUN_REQUESTS)

    # --- F1: run_id and phase come from the file name ---

    def test_name_gives_run_id_phase_and_type(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        (name, obj), = runs.read_requests(self.runs_dir, unlink=False)[0]
        self.assertEqual(name, "r0123456789ab.start.json")
        self.assertEqual((obj["type"], obj["run_id"], obj["phase"]), ("run", "r0123456789ab", "start"))

    def test_body_that_disagrees_with_the_name_is_refused(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now, "run_id": "r0123456789ac"})
        self.put("r0123456789ad.start.json", {"seat": "s", "t": self.now, "phase": "end"})
        items, _ = runs.read_requests(self.runs_dir)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(isinstance(v, ValueError) for _, v in items))
        self.assertEqual(os.listdir(self.runs_dir), [])

    def test_starts_are_taken_before_ends_when_capped(self):
        self.put("r0123456789ab.end.json", {"ok": True, "t": self.now})
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        items, _ = runs.read_requests(self.runs_dir, limit=1)
        self.assertEqual([n for n, _ in items], ["r0123456789ab.start.json"])
        self.assertEqual(os.listdir(self.runs_dir), ["r0123456789ab.end.json"])

    def test_second_hard_link_and_fifo_are_refused(self):
        other = os.path.join(self.tmp, "outside.json")
        Path(other).write_text(json.dumps({"seat": "s", "t": self.now}))
        os.link(other, os.path.join(self.runs_dir, "r0123456789ab.start.json"))
        os.mkfifo(os.path.join(self.runs_dir, "r0123456789ac.start.json"))
        items, _ = runs.read_requests(self.runs_dir)
        self.assertEqual(len(items), 2)
        self.assertTrue(all(isinstance(v, ValueError) for _, v in items))
        self.assertTrue(os.path.exists(other))

    def test_a_missing_dir_is_quiet_and_a_symlinked_dir_is_a_problem(self):
        shutil.rmtree(self.runs_dir)
        self.assertEqual(runs.read_requests(self.runs_dir), ([], []))
        os.symlink(self.tmp, self.runs_dir)
        items, probs = runs.read_requests(self.runs_dir)
        self.assertEqual((items, len(probs)), ([], 1))

    def test_beat_gets_seat_from_a_logged_start(self):
        store.append(self.log, {**runs.start_row("s", None, self.project.worktree, None, "local"),
                                "run_id": "r0123456789ab"})
        self.put("r0123456789ab.beat.json", {"t": self.now})
        self.assertEqual(runs.ingest(self.config, self.state, self.project, now=self.now), [])
        self.assertEqual([(r["phase"], r["seat"]) for r in self.logged()], [("start", "s"), ("beat", "s")])

    def test_end_with_no_start_keeps_seat_null(self):
        self.put("r0123456789ab.end.json", {"ok": False, "reason": "exit 1", "t": self.now})
        self.assertEqual(runs.ingest(self.config, self.state, self.project, now=self.now), [])
        (e,) = self.logged()
        self.assertEqual((e["seat"], e["dir"], e["ok"], e["reason"]), (None, self.project.worktree, False, "exit 1"))
        self.assertLessEqual(set(runs.end_row("r0123456789ab", None, None, False, None, None)), set(e))

    def test_a_request_start_cannot_take_over_a_bw_run(self):
        store.append(self.log, {**runs.start_row("astra", "m", self.project.worktree, "x", "bw"),
                                "run_id": "r0123456789ab"})
        self.put("r0123456789ab.start.json", {"seat": "astra", "t": self.now})
        self.put("r0123456789ab.end.json", {"ok": True, "t": self.now})
        self.assertTrue(runs.ingest(self.config, self.state, self.project, now=self.now))
        self.assertEqual([r["phase"] for r in self.logged()], ["start"])

    def test_problem_lines_are_capped(self):
        for i in range(12):
            self.put(f"r{i:012x}.start.json", {"seat": "BAD", "t": self.now})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertLessEqual(len(probs), 6)
        self.assertTrue(probs[-1].startswith("and "))

    def test_append_failure_is_a_problem_line(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        with mock.patch.object(store, "append", side_effect=OSError(28, "No space left on device")):
            probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(len(probs), 1)

    # --- fix round 1: totality, ownership, fail closed ---

    def test_a_huge_integer_refuses_only_its_file(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        self.put("r0123456789ac.start.json", {"seat": "s", "t": 10 ** 400})
        self.put("r0123456789ad.end.json", {"ok": True, "t": self.now, "duration_s": 10 ** 400})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(len(probs), 2, probs)
        self.assertTrue(all("refused" in p for p in probs))
        self.assertEqual([(r["run_id"], r["phase"]) for r in self.logged()], [("r0123456789ab", "start")])
        self.assertEqual(os.listdir(self.runs_dir), [])

    def test_beat_or_end_for_another_worktrees_run_is_refused(self):
        store.append(self.log, {**runs.start_row("s", None, "/other/wt", None, "local"), "run_id": "r0123456789ab"})
        self.put("r0123456789ab.beat.json", {"seat": "s", "t": self.now})
        self.put("r0123456789ab.end.json", {"seat": "s", "ok": True, "t": self.now})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(len(probs), 2, probs)
        self.assertTrue(all("another worktree" in p for p in probs))
        self.assertEqual([r["phase"] for r in self.logged()], ["start"])

    def test_a_bw_start_is_not_hidden_by_a_later_logged_start(self):
        for source in ("bw", "local"):
            store.append(self.log, {**runs.start_row("s", None, self.project.worktree, None, source),
                                    "run_id": "r0123456789ab"})
        self.put("r0123456789ab.end.json", {"ok": True, "t": self.now})
        self.assertIn("bw seat", "; ".join(runs.ingest(self.config, self.state, self.project, now=self.now)))
        self.assertEqual([r["phase"] for r in self.logged()], ["start", "start"])

    def test_a_duplicate_start_is_refused(self):
        store.append(self.log, {**runs.start_row("s", None, self.project.worktree, None, "local"),
                                "run_id": "r0123456789ab"})
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        probs = runs.ingest(self.config, self.state, self.project, now=self.now)
        self.assertEqual(len(probs), 1)
        self.assertIn("already", probs[0])
        self.assertEqual([r["phase"] for r in self.logged()], ["start"])

    def test_an_unreadable_or_incomplete_tail_leaves_the_files(self):
        self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
        self.put("r0123456789ab.end.json", {"ok": True, "t": self.now})
        for tail in (store.Tail([], False, "Permission denied"), store.Tail([], False, None)):
            with mock.patch.object(runs.store, "read_tail", return_value=tail):
                probs = runs.ingest(self.config, self.state, self.project, now=self.now)
            self.assertEqual(len(probs), 1, probs)
            self.assertEqual(sorted(os.listdir(self.runs_dir)), ["r0123456789ab.end.json", "r0123456789ab.start.json"])
            self.assertEqual(self.logged(), [])
        self.assertEqual(runs.ingest(self.config, self.state, self.project, now=self.now), [])
        self.assertEqual([r["phase"] for r in self.logged()], ["start", "end"])

    # --- validate_request ---

    def test_validate_request_rules(self):
        wt, now = "/w", self.now
        base = {"type": "run", "phase": "start", "run_id": "r0123456789ab", "t": now, "seat": "s"}
        row = runs.validate_request({**base, "source": "bw", "dir": "/x", "pid": 42, "host": "h", "model": "model-a",
                                     "label": "lint", "extra": 1, "panel": "p0123abc"}, wt, now)
        self.assertEqual(row, {**base, "source": "local", "dir": wt, "pid": 42, "host": "h", "model": "model-a",
                               "label": "lint"})
        self.assertEqual(runs.validate_request({**base, "label": "a\x1bb"}, wt, now)["label"], "a\\x1bb")
        bad = [{**base, "seat": None}, {**base, "seat": "BAD"}, {**base, "t": math.nan}, {**base, "t": math.inf},
               {**base, "t": True}, {**base, "t": "1"}, {**base, "t": now - runs.T_SKEW_S - 1},
               {**base, "pid": 0}, {**base, "pid": True}, {**base, "pid": 2 ** 31}, {**base, "pid": 1.5},
               {**base, "label": "two\nlines"}, {**base, "label": "x" * (runs.LABEL_CHARS + 1)},
               {**base, "model": "m" * (runs.MODEL_CHARS + 1)}, {**base, "host": "h" * (runs.HOST_CHARS + 1)},
               {**base, "model": 5}, {**base, "phase": "middle"}, {**base, "run_id": "bad"}, [],
               {**base, "phase": "end"}, {**base, "phase": "end", "ok": 1},
               {**base, "phase": "end", "ok": True, "reason": "r" * (runs.REASON_CHARS + 1)},
               {**base, "phase": "end", "ok": True, "duration_s": -1},
               {**base, "phase": "end", "ok": True, "tokens_in": 1.5},
               {**base, "t": 10 ** 400}, {**base, "t": -10 ** 400}, {**base, "pid": 10 ** 400},
               {**base, "phase": "end", "ok": True, "duration_s": 10 ** 400},
               {**base, "phase": "end", "ok": True, "cost_usd": 10 ** 400},
               {**base, "phase": "end", "ok": True, "tokens_in": 10 ** 400},
               {**base, "phase": "end", "ok": True, "ctx_peak": 2 ** 60}]
        for obj in bad:
            with self.assertRaises(ValueError, msg=repr(obj)[:120]):
                runs.validate_request(obj, wt, now)
        beat = runs.validate_request({**base, "phase": "beat", "seat": None}, wt, now)
        self.assertEqual(beat, {"type": "run", "phase": "beat", "run_id": "r0123456789ab", "t": now, "seat": None,
                                "dir": wt})
        end = runs.validate_request({**base, "phase": "end", "ok": True, "duration_s": 2.5, "tokens_in": 3}, wt, now)
        self.assertEqual(set(end), set(runs.end_row("r0123456789ab", "s", wt, True, None, None, source="local")))
        self.assertEqual((end["duration_s"], end["tokens_in"], end["cost_usd"], end["source"]), (2.5, 3, None, "local"))
        self.assertEqual(runs.validate_request({**base, "phase": "end", "ok": True, "source": "bw"}, wt, now)["source"],
                         "local")  # forced, as for a start


if __name__ == "__main__":
    unittest.main()
