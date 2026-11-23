import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import quota  # noqa: E402
from seats import caps  # noqa: E402


class SeatCaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        self.config = {"seats": {"astra": {"plan": "chatgpt-plan"}, "gem": {"plan": "google-plan"}}}

    def tearDown(self):
        self.tmp.cleanup()

    def test_arm_and_expiry(self):
        rec = caps.arm(self.state, "astra", 1000.0, 5)
        self.assertEqual(rec, {"resets_at": 1000.0 + 5 * 3600, "window": "unknown", "source": "seat"})
        self.assertIsNotNone(caps.read(self.state, "astra", 1001.0))
        self.assertIsNone(caps.read(self.state, "astra", 1000.0 + 5 * 3600))
        self.assertFalse(os.path.exists(os.path.join(self.state, "caps")))

    def test_clear(self):
        caps.arm(self.state, "astra", 1000.0, 5)
        caps.clear(self.state, "astra")
        caps.clear(self.state, "astra")
        self.assertIsNone(caps.read(self.state, "astra", 1001.0))

    def test_capped_seat_or_plan(self):
        self.assertIsNone(caps.capped(self.config, self.state, "astra", 1000.0))
        quota.record_cap(self.state, "chatgpt-plan", 1000.0)
        self.assertRegex(caps.capped(self.config, self.state, "astra", 1001.0), r"^plan chatgpt-plan capped until ")
        caps.arm(self.state, "gem", 1000.0, 5)
        self.assertRegex(caps.capped(self.config, self.state, "gem", 1001.0), r"^seat gem capped until ")


if __name__ == "__main__":
    unittest.main()
