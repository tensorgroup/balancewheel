import subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import desktop  # noqa: E402


class Desktop(unittest.TestCase):
    def test_macos_passes_text_as_arguments_never_into_the_script(self):
        argv = desktop.argv_for("bw", 'say "hi" & do shell script "x"', "darwin", which=lambda n: "/usr/bin/" + n)
        self.assertEqual(argv[:len(desktop.OSASCRIPT)], desktop.OSASCRIPT)
        self.assertEqual(argv[-2:], ["bw", 'say "hi" & do shell script "x"'])

    def test_linux_uses_notify_send_and_nothing_without_it(self):
        self.assertEqual(desktop.argv_for("bw", "t", "linux", which=lambda n: n == "notify-send" and "/x"),
                         ["notify-send", "--", "bw", "t"])
        self.assertIsNone(desktop.argv_for("bw", "t", "linux", which=lambda n: None))

    def test_long_text_is_cut(self):
        argv = desktop.argv_for("bw", "x" * 1000, "linux", which=lambda n: "/x")
        self.assertEqual(len(argv[-1]), desktop.MAX_TEXT)

    def test_notify_detaches_starts_no_thread_and_never_raises(self):
        import threading
        seen = {}

        class P:
            def __init__(self, argv, **kw):
                seen.update(kw)

        before = threading.active_count()
        self.assertIsInstance(desktop.notify("bw", "t", "linux", which=lambda n: "/x", popen=P), P)
        self.assertEqual(threading.active_count(), before)
        self.assertTrue(seen["start_new_session"])
        self.assertEqual(seen["stdin"], subprocess.DEVNULL)
        for exc in (OSError("no"), RuntimeError("no")):
            def boom(*a, exc=exc, **k):
                raise exc
            self.assertIsNone(desktop.notify("bw", "t", "linux", which=lambda n: "/x", popen=boom))

    def test_reap_keeps_only_the_running(self):
        class P:
            def __init__(self, rc):
                self.rc = rc

            def poll(self):
                return self.rc

        running, done = P(None), P(0)
        self.assertEqual(desktop.reap([running, done]), [running])

    def test_a_real_notifier_is_reaped(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertEqual(desktop.reap([p]), [])


if __name__ == "__main__":
    unittest.main()
