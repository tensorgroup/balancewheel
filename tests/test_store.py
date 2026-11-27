import hashlib, json, os, sys, tempfile, threading, unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import store  # noqa: E402

FX = HERE / "fixtures"


def store_ts(n):
    return datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class GuardEvents(unittest.TestCase):
    def log(self, events):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        p = os.path.join(d, "m.jsonl")
        for e in events:
            store.append(p, e)
        return store.load(p)

    def test_session_autonomy_round_trips_and_guards_fold(self):
        guard = {"type": "guard", "session_id": "s1", "project": "k", "profile": "p", "lead": "l", "runtime": "pi",
                 "mode": "shadow", "guard_version": "g", "rules_version": "r", "calls": 3,
                 "decisions": {"allow": 2, "ask": 0, "block": 1}, "gray": 1, "unreachable": 0,
                 "latency_ms": {"p50": 1.0, "p95": 2.0}}
        d = self.log([{"type": "session", "phase": "start", "session_id": "s1", "project": "k", "lead": "l",
                       "autonomy": {"requested": None, "effective": "guard"}, "guard_version": "g"},
                      guard, {"type": "session", "phase": "end", "session_id": "s1", "reason": "exit"}])
        s = d["sessions"][0]
        self.assertEqual(s["autonomy"], {"requested": None, "effective": "guard"})
        self.assertEqual(s["guard_version"], "g")
        self.assertEqual(len(d["guards"]), 1)
        self.assertEqual(d["guards"][0]["calls"], 3)

    def test_load_keeps_guard_mode_events(self):
        ev = {"type": "guard_mode", "profile": "p", "runtime": "pi", "from": "shadow", "to": "acting",
              "reason": "the gate held", "evidence": {"reviewed": 50, "sampled_allows": 20, "false_stop_pct": 0.0,
                                                       "false_allows": 0}}
        d = self.log([ev, {"type": "guard", "session_id": "s1"}])
        self.assertEqual([(e["from"], e["to"], e["evidence"]["reviewed"]) for e in d["guard_modes"]],
                         [("shadow", "acting", 50)])
        self.assertEqual(len(d["guards"]), 1)
        self.assertEqual(store.load(str(FX / "modern.jsonl"))["guard_modes"], [])

    def test_legacy_log_has_no_autonomy_and_no_guards(self):
        d = store.load(str(FX / "modern.jsonl"))
        self.assertEqual(d["guards"], [])
        d = self.log([{"type": "session", "phase": "start", "session_id": "s1", "project": "k", "lead": "l"}])
        self.assertIsNone(d["sessions"][0]["autonomy"])
        self.assertIsNone(d["sessions"][0]["guard_version"])


    def test_asks_and_answers_load_and_older_logs_have_none(self):
        d = store.load(str(FX / "modern.jsonl"))
        self.assertEqual((d["asks"], d["answers"]), ([], []))
        d = self.log([{"type": "ask", "id": "a1", "project": "k", "session_id": "s1", "from": "l"},
                      {"type": "answer", "id": "b2", "ask_id": "a1"}])
        self.assertEqual([a["id"] for a in d["asks"]], ["a1"])
        self.assertEqual([a["ask_id"] for a in d["answers"]], ["a1"])


class ReadAndLoad(unittest.TestCase):
    def test_modern_log_loads_and_folds(self):
        d = store.load(str(FX / "modern.jsonl"))
        self.assertEqual(len(d["usage"]), 3)
        self.assertEqual([p["id"] for p in d["panels"]], ["p0000001", "p0000002"])
        p1 = d["panels"][0]
        self.assertEqual(p1["seats"], {"alpha": "vendor/model-a", "beta": "router/vendor/model-b"})
        self.assertEqual(p1["notes"], "amended note")
        self.assertEqual(p1["amendments"][0]["note"], "added a note")
        self.assertEqual(p1["findings"]["alpha"]["unique"], 1)
        w = d["watches"][0]
        self.assertEqual(w["id"], "w0000001")
        self.assertEqual(w["status"], "resolved")
        self.assertEqual(w["updates"][0]["note"], "re-verified a sample")
        self.assertEqual(d["warnings"], [])

    def test_load_keeps_panel_runs_and_tier(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            store.append(path, {"type": "panel_run", "panel_id": "p1"})
            store.append(path, {"type": "panel", "id": "p1", "target": "t", "kind": "review", "seats": {"a": "m"},
                                "tier": "deep", "findings": {}, "findings_detail": []})
            store.append(path, {"type": "panel", "id": "p2", "target": "t", "kind": "review", "seats": {"a": "m"},
                                "tier": 3, "findings": {}, "findings_detail": []})
            data = store.load(path)
        self.assertEqual([e["panel_id"] for e in data["panel_runs"]], ["p1"])
        self.assertEqual([p["tier"] for p in data["panels"]], ["deep", None])

    def test_load_keeps_one_panel_run_per_panel(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "log.jsonl")
            for pid, duration in (("p1", 1.0), ("p2", 2.0), ("p1", 3.0)):   # a crash between append and mark
                store.append(path, {"type": "panel_run", "panel_id": pid, "duration_s": duration})
            data = store.load(path)
        self.assertEqual([(e["panel_id"], e["duration_s"]) for e in data["panel_runs"]], [("p1", 1.0), ("p2", 2.0)])

    def test_legacy_log_normalises_without_error(self):
        d = store.load(str(FX / "legacy.jsonl"))
        p = d["panels"][0]
        self.assertEqual(p["id"], hashlib.sha1(b"2025-12-01T11:00:00Z").hexdigest()[:8])
        self.assertEqual(p["seats"], {"alpha": None, "beta": None})
        self.assertIsNone(p["findings"]["alpha"]["partial"])
        self.assertIsNone(p["findings"]["alpha"]["unique"])
        self.assertEqual(p["findings"]["alpha"]["confirmed"], 3)
        self.assertEqual(len(p["findings_detail"]), 1)  # from the panel_ts amend
        w = d["watches"][0]
        self.assertEqual(w["id"], store.legacy_watch_id("2025-12-01T11:00:01Z", "verification-skim", "old change"))
        self.assertEqual(w["status"], "dismissed")
        self.assertIsNone(w["panel_id"])
        self.assertEqual(d["watches"][1]["status"], "resolved")  # legacy "performed" maps to resolved
        self.assertEqual(d["watches"][2]["status"], "resolved")  # legacy auto:true with no status, too
        self.assertEqual(d["watches"][2]["detail"], "auto-form legacy watch")  # legacy "summary" → detail
        self.assertEqual(len(d["warnings"]), 2)  # orphan amend + orphan watch-update are warned, not dropped silently
        self.assertTrue(any("unknown panel" in w for w in d["warnings"]))
        self.assertTrue(any("unknown watch" in w for w in d["warnings"]))

    def test_truncated_final_line_is_skipped_with_warning(self):
        d = store.load(str(FX / "truncated.jsonl"))
        self.assertEqual(len(d["panels"]), 1)
        self.assertEqual(len(d["warnings"]), 1)
        self.assertIn("line 5", d["warnings"][0])

    def test_missing_file_is_empty_not_error(self):
        d = store.load("/nonexistent/path/metrics.jsonl")
        self.assertEqual(d["events"], [])
        self.assertEqual(d["warnings"], [])

    def test_non_object_lines_and_cut_multibyte_tail_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            good = json.dumps({"type": "usage", "seat": "alpha"})
            p.write_bytes((good + "\nnull\n[]\n" + '{"type":"usage","seat":"bé"').encode("utf-8")[:-2])
            d = store.load(str(p))
            self.assertEqual(len(d["usage"]), 1)
            self.assertEqual(len(d["warnings"]), 3)

    def test_bool_and_float_counts_become_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            p.write_text(json.dumps({"type": "panel", "ts": "2026-01-01T00:00:00Z", "seats": ["a"],
                                     "findings": {"a": {"total": True, "confirmed": 1.9, "refuted": 2}}}) + "\n")
            f = store.load(str(p))["panels"][0]["findings"]["a"]
            self.assertIsNone(f["total"]); self.assertIsNone(f["confirmed"]); self.assertEqual(f["refuted"], 2)

    def test_duplicate_panel_id_dedupes_to_one_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            first = {"type": "panel", "id": "dup0001", "ts": "2026-01-01T00:00:00Z", "target": "first",
                     "seats": ["a"], "findings": {}}
            second = {"type": "panel", "id": "dup0001", "ts": "2026-01-02T00:00:00Z", "target": "second",
                      "seats": ["a"], "findings": {}}
            p.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
            d = store.load(str(p))
            self.assertEqual(len(d["panels"]), 1)
            self.assertEqual(d["panels"][0]["target"], "second")  # last occurrence survives
            self.assertEqual(sum("duplicate panel id" in w for w in d["warnings"]), 1)

    def test_duplicate_ts_among_legacy_panels_is_warned(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            ts = "2025-12-01T11:00:00Z"
            first = {"type": "panel", "ts": ts, "target": "first", "seats": ["a"], "findings": {}}
            second = {"type": "panel", "ts": ts, "target": "second", "seats": ["a"], "findings": {}}
            p.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n")
            d = store.load(str(p))
            self.assertTrue(any("duplicate panel ts" in w for w in d["warnings"]))

    def test_append_after_a_cut_line_repairs_the_newline_instead_of_concatenating(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            existing = json.dumps({"type": "usage", "seat": "existing"})
            # a damaged final line with no trailing newline, as a crash mid-write would leave it
            cut = '{"type": "usage", "seat": "cut off'
            p.write_bytes((existing + "\n" + cut).encode("utf-8"))
            store.append(str(p), {"type": "usage", "seat": "next"})
            d = store.load(str(p))
            self.assertEqual([u["seat"] for u in d["usage"]], ["existing", "next"])  # not concatenated onto "cut"
            self.assertEqual(len(d["warnings"]), 1)


class Ids(unittest.TestCase):
    def test_watch_id_is_stable_and_timestamp_free(self):
        a = store.watch_id("panel-bloat", "t", "p1")
        self.assertEqual(a, store.watch_id("panel-bloat", "t", "p1"))
        self.assertEqual(len(a), 8)
        self.assertNotEqual(a, store.watch_id("panel-bloat", "t", "p2"))


class Append(unittest.TestCase):
    def test_append_writes_one_line_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "sub", "m.jsonl")
            store.append(p, {"type": "usage", "seat": "alpha"})
            store.append(p, {"type": "usage", "seat": "beta"})
            lines = Path(p).read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["seat"], "beta")
            self.assertTrue(json.loads(lines[0])["ts"].endswith("Z"))

    def test_concurrent_appends_do_not_interleave(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "m.jsonl")
            payload = {"type": "usage", "dir": "x" * 20000}
            threads = [threading.Thread(target=store.append, args=(p, dict(payload, seat=str(i)))) for i in range(20)]
            for t in threads: t.start()
            for t in threads: t.join()
            events, warnings = store.read_events(p)
            self.assertEqual(len(events), 20)
            self.assertEqual(warnings, [])


class Tail(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d, ignore_errors=True)
        self.log = os.path.join(d, "m.jsonl")

    def test_read_tail_matches_forward_read_for_every_chunk_size(self):
        rows = [{"type": "run", "phase": "start", "run_id": f"r{i:012x}", "t": 1000.0 + i, "seat": "s"} for i in range(40)]
        rows[15]["label"] = "x" * 300                     # in range, and longer than every small chunk
        body = "\n".join(json.dumps(r) for r in rows) + "\n\n" + '{"type": "run", "t": 99'  # blank line + cut tail
        Path(self.log).write_text(body)
        want = [r for r in rows if r["t"] >= 1010.0]
        for chunk in range(1, 65):
            t = store.read_tail(self.log, 1010.0, chunk=chunk)
            self.assertEqual((t.events, t.complete, t.error), (want, True, None), chunk)

    def test_a_late_row_with_an_old_t_does_not_stop_the_read(self):
        rows = [{"type": "run", "t": 1000.0 + i, "ts": store_ts(1000 + i)} for i in range(30)]
        rows.append({"type": "run", "t": 900.0, "ts": store_ts(1030)})   # ingested late: old t, new ts
        Path(self.log).write_text("".join(json.dumps(r) + "\n" for r in rows))
        t = store.read_tail(self.log, 1010.0, chunk=40)
        self.assertEqual([e["t"] for e in t.events], [1000.0 + i for i in range(10, 30)])

    def test_read_tail_stops_at_the_cap(self):
        lines = [json.dumps({"type": "usage", "t": 1000.0 + i}) + "\n" for i in range(100)]   # every line is 31 bytes
        Path(self.log).write_text("".join(lines))
        t = store.read_tail(self.log, 0.0, cap=230, chunk=100)   # 230 is not a multiple of 100
        self.assertFalse(t.complete)
        self.assertEqual([e["t"] for e in t.events], [1093.0 + i for i in range(7)])   # only lines whole in the last 230 bytes

    def test_a_file_smaller_than_the_cap_is_complete(self):
        Path(self.log).write_text(json.dumps({"type": "usage", "t": 0.0}) + "\n")   # t == 0.0 is a valid time
        t = store.read_tail(self.log, 0.0, cap=10_000, chunk=7)
        self.assertEqual((len(t.events), t.complete), (1, True))

    def test_read_tail_missing_and_unreadable(self):
        self.assertEqual(store.read_tail(self.log + ".nope", 0.0), store.Tail([], True, None))
        os.mkdir(self.log)                                 # a directory: open() fails
        t = store.read_tail(self.log, 0.0); self.assertEqual((t.events, t.complete), ([], False)); self.assertTrue(t.error)

    def test_row_time_prefers_t_then_ts(self):
        self.assertEqual(store.row_time({"t": 5.5, "ts": "2026-11-27T00:00:00Z"}), 5.5)
        self.assertEqual(store.row_time({"ts": "1970-01-01T00:00:10Z"}), 10.0)
        self.assertIsNone(store.row_time({"t": True})); self.assertIsNone(store.row_time({"ts": "bad"}))

    def test_load_keeps_run_rows(self):
        store.append(self.log, {"type": "run", "phase": "start", "run_id": "r0123456789ab", "t": 1.0})
        self.assertEqual([e["run_id"] for e in store.load(self.log)["runs"]], ["r0123456789ab"])


class LogPath(unittest.TestCase):
    def test_env_overrides_config(self):
        os.environ["BALANCEWHEEL_METRICS_LOG"] = "/tmp/x.jsonl"
        try:
            self.assertEqual(store.log_path({"metrics_log": "/elsewhere"}), "/tmp/x.jsonl")
        finally:
            del os.environ["BALANCEWHEEL_METRICS_LOG"]
        self.assertEqual(store.log_path({"metrics_log": "/elsewhere"}), "/elsewhere")


if __name__ == "__main__":
    unittest.main()
