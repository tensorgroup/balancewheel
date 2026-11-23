import json, os, sys, tempfile, threading, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seats import sessions  # noqa: E402


class Sessions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip_and_model_guard(self):
        sessions.put(self.state, "astra", "/r", "s1", "m1", 1.0)
        self.assertEqual(sessions.get(self.state, "astra", "/r", "m1"), "s1")
        self.assertIsNone(sessions.get(self.state, "astra", "/r", "m2"))
        self.assertIsNone(sessions.get(self.state, "astra", "/other", "m1"))

    def test_corrupt_file_reads_none_and_put_repairs(self):
        p = Path(self.state, "seats", "sessions", "astra.json")
        p.parent.mkdir(parents=True)
        p.write_text("{")
        self.assertIsNone(sessions.get(self.state, "astra", "/r", "m"))
        sessions.put(self.state, "astra", "/r", "s", "m", 1.0)
        self.assertEqual(json.loads(p.read_text())["/r"]["session_id"], "s")

    def test_concurrent_puts_both_survive(self):
        def work(tag):
            for i in range(20):
                sessions.put(self.state, "astra", f"/{tag}{i}", "s", "m", 1.0)
        ts = [threading.Thread(target=work, args=(t,)) for t in "ab"]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        data = json.loads(Path(self.state, "seats", "sessions", "astra.json").read_text())
        self.assertEqual(len(data), 40)

    def test_run_lock_busy(self):
        with sessions.run_lock(self.state, "astra", "/r"):
            with self.assertRaises(sessions.Busy):
                with sessions.run_lock(self.state, "astra", "/r"):
                    pass
            with sessions.run_lock(self.state, "astra", "/other"):
                pass
        with sessions.run_lock(self.state, "astra", "/r"):
            pass


if __name__ == "__main__":
    unittest.main()
