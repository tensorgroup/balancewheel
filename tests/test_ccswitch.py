import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lead import ccswitch, checks

REPO = Path(__file__).resolve().parent.parent


def slurp(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def spit(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)


REQUIRED = {"enableLocalProxy": False, "enableStackMode": False, "enableFailoverToggle": False,
            "unifyCodexSessionHistory": False, "launchOnStartup": False,
            "checkToolUpdatesOnStartup": False, "sessionAutoSyncEnabled": True}


class WriteSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "cc-switch")
        self.f = os.path.join(self.d, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contract_values(self):
        self.assertEqual(ccswitch.REQUIRED_SETTINGS, REQUIRED)

    def test_data_dir_env_and_default(self):
        old = os.environ.pop("BW_CCSWITCH_DIR", None)
        try:
            self.assertEqual(ccswitch.data_dir(), os.path.expanduser("~/.cc-switch"))
            os.environ["BW_CCSWITCH_DIR"] = self.d
            self.assertEqual(ccswitch.data_dir(), self.d)
        finally:
            os.environ.pop("BW_CCSWITCH_DIR", None)
            if old is not None:
                os.environ["BW_CCSWITCH_DIR"] = old

    def test_absent_dir_creates_exactly_the_seven_keys_private(self):
        rc, msg = ccswitch.write_settings(self.d)
        self.assertEqual(rc, 0)
        self.assertTrue(msg.startswith("wrote "))
        self.assertEqual(json.loads(slurp(self.f)), REQUIRED)
        self.assertEqual(stat.S_IMODE(os.stat(self.f).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.d).st_mode), 0o700)

    def test_second_write_is_already_set(self):
        ccswitch.write_settings(self.d)
        before = slurp(self.f, "rb")
        self.assertEqual(ccswitch.write_settings(self.d), (0, "already set"))
        self.assertEqual(slurp(self.f, "rb"), before)

    def test_existing_superset_with_required_values_is_already_set(self):
        os.makedirs(self.d)
        spit(self.f, json.dumps(dict(REQUIRED, theme="dark")))
        self.assertEqual(ccswitch.write_settings(self.d), (0, "already set"))

    def test_differing_file_is_refused_and_untouched(self):
        os.makedirs(self.d)
        raw = json.dumps({"enableLocalProxy": True, "userKey": 1}).encode()
        spit(self.f, raw)
        rc, msg = ccswitch.write_settings(self.d)
        self.assertEqual(rc, 1)
        self.assertIn("refusing to overwrite " + self.f, msg)
        self.assertIn("enableLocalProxy: True → False", msg)
        self.assertEqual(slurp(self.f, "rb"), raw)

    def test_invalid_json_is_refused_and_untouched(self):
        os.makedirs(self.d)
        spit(self.f, "{not json")
        rc, msg = ccswitch.write_settings(self.d)
        self.assertEqual(rc, 1)
        self.assertIn("refusing to overwrite", msg)
        self.assertEqual(slurp(self.f), "{not json")

    def test_dir_without_settings_gets_the_file(self):
        os.makedirs(self.d)
        self.assertEqual(ccswitch.write_settings(self.d)[0], 0)
        self.assertEqual(json.loads(slurp(self.f)), REQUIRED)


class CheckSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.join(self.tmp.name, "cc-switch")

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, obj):
        os.makedirs(self.d, exist_ok=True)
        spit(os.path.join(self.d, "settings.json"), json.dumps(obj))

    def test_required_file_is_all_ok(self):
        self.put(REQUIRED)
        cs = ccswitch.check_settings(self.d)
        self.assertEqual([c.state for c in cs], ["ok"])
        self.assertEqual(checks.lines("cc-switch", cs), ["cc-switch: ok"])

    def test_one_wrong_key_is_one_warn_naming_it(self):
        self.put(dict(REQUIRED, sessionAutoSyncEnabled=False))
        warns = [c for c in ccswitch.check_settings(self.d) if c.state == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("sessionAutoSyncEnabled", warns[0].name + warns[0].detail)

    def test_missing_key_is_warn(self):
        o = dict(REQUIRED)
        del o["enableLocalProxy"]
        self.put(o)
        self.assertEqual([c.state for c in ccswitch.check_settings(self.d) if c.state != "ok"], ["warn"])

    def test_no_dir_is_one_unknown(self):
        cs = ccswitch.check_settings(os.path.join(self.tmp.name, "nope"))
        self.assertEqual(cs, [checks.Check("cc-switch", "unknown", "not installed (Usage view off)")])

    def test_unreadable_is_one_unknown(self):
        os.makedirs(self.d)
        spit(os.path.join(self.d, "settings.json"), "{bad")
        cs = ccswitch.check_settings(self.d)
        self.assertEqual([c.state for c in cs], ["unknown"])

    def test_dir_without_settings_is_unknown(self):
        os.makedirs(self.d)
        self.assertEqual([c.state for c in ccswitch.check_settings(self.d)], ["unknown"])


class ChecksLinesTest(unittest.TestCase):
    def test_lines(self):
        cs = [checks.Check("a", "ok", ""), checks.Check("b", "warn", "bad"), checks.Check("c", "unknown", "?")]
        self.assertEqual(checks.lines("t", cs), ["  b: warn — bad", "  c: unknown — ?"])
        self.assertEqual(checks.lines("t", cs[:1]), ["t: ok"])


class SetupCommandTest(unittest.TestCase):
    def test_bw_setup_cc_switch_needs_no_config(self):
        with tempfile.TemporaryDirectory() as t:
            env = dict(os.environ, BALANCEWHEEL_CONFIG=os.path.join(t, "missing.json"),
                       BW_CCSWITCH_DIR=os.path.join(t, "ccs"))
            r = subprocess.run([sys.executable, str(REPO / "bin" / "bw"), "setup", "cc-switch"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("wrote", r.stdout)
            self.assertEqual(json.loads(slurp(os.path.join(t, "ccs", "settings.json"))), REQUIRED)
            r = subprocess.run([sys.executable, str(REPO / "bin" / "bw"), "setup", "cc-switch"],
                               env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, "already set"))

    def test_refusal_exits_1(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "ccs"))
            spit(os.path.join(t, "ccs", "settings.json"), '{"enableLocalProxy": true}')
            env = dict(os.environ, BALANCEWHEEL_CONFIG=os.path.join(t, "missing.json"),
                       BW_CCSWITCH_DIR=os.path.join(t, "ccs"))
            r = subprocess.run([sys.executable, str(REPO / "bin" / "bw"), "setup", "cc-switch"],
                               env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("refusing to overwrite", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
