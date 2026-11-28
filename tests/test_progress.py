import sys, threading, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead.progress import Progress, duration  # noqa: E402


class ProgressTest(unittest.TestCase):
    def test_duration(self):
        self.assertEqual([duration(s) for s in (0, 59.9, 60, 125)], ["0s", "59s", "1m 00s", "2m 05s"])

    def test_waiting_line_names_what_runs_and_how_long(self):
        said, t = [], [100.0]
        p = Progress(said.append, clock=lambda: t[0])
        p.begin("a", "pi update")
        p.begin("b", "verify pi containment")
        t[0] = 190.0
        self.assertEqual(said, [])  # begin says nothing; only the heartbeat speaks
        self.assertEqual(p.waiting_line(), "still waiting on: pi update (1m 30s), verify pi containment (1m 30s)")
        p.end("a"); p.end("b"); p.end("missing")
        self.assertIsNone(p.waiting_line())

    def test_heartbeat_repeats_while_something_runs_and_stops_on_exit(self):
        said, seen = [], threading.Event()

        def say(line):
            said.append(line)
            seen.set()

        with Progress(say, interval=0.01) as p:
            p.begin("a", "codex update")
            self.assertTrue(seen.wait(2))
        n = len(said)
        self.assertTrue(said[0].startswith("still waiting on: codex update ("))
        threading.Event().wait(0.05)
        self.assertEqual(len(said), n)

    def test_a_part_younger_than_one_interval_is_not_yet_named(self):
        t = [0.0]
        p = Progress(lambda line: None, interval=15, clock=lambda: t[0])
        p.begin("old", "codex update")
        t[0] = 20.0
        p.begin("new", "pi checks")
        t[0] = 21.0
        self.assertEqual(p.waiting_line(), "still waiting on: codex update (21s)")

    def test_no_heartbeat_when_nothing_runs(self):
        said = []
        with Progress(said.append, interval=0.01):
            threading.Event().wait(0.05)
        self.assertEqual(said, [])


if __name__ == "__main__":
    unittest.main()
