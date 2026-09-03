import hashlib, json, os, sys, tempfile, threading, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import store  # noqa: E402

FX = HERE / "fixtures"


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
