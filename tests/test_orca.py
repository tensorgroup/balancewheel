import json
import os
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lead import orca
from lead.proc import Result

FIX = Path(__file__).resolve().parent / "fixtures" / "orca"


def fixture(name):
    return (FIX / name).read_text()


def fake_run(result):
    calls = []

    def run(argv, timeout, **kw):
        calls.append((argv, timeout))
        return result
    run.calls = calls
    return run


class RunningTest(unittest.TestCase):
    def test_running(self):
        run = fake_run(Result(0, fixture("status_running.json"), "", False))
        self.assertEqual(orca.running(run)[0], True)
        self.assertEqual(run.calls, [(["orca", "status", "--json"], 5)])

    def test_stopped(self):
        self.assertEqual(orca.running(fake_run(Result(0, fixture("status_stopped.json"), "", False)))[0], False)

    def test_absent(self):
        ok, why = orca.running(fake_run(Result(127, "", "orca: not found", False)))
        self.assertIsNone(ok)
        self.assertIn("not found", why)

    def test_garbage(self):
        ok, why = orca.running(fake_run(Result(0, "<html>", "", False)))
        self.assertIsNone(ok)
        self.assertIn("JSON", why)

    def test_wrong_shape(self):
        for out in ('[]', '{"result": []}', '{"result": {"app": {"running": "yes"}}}'):
            ok, why = orca.running(fake_run(Result(0, out, "", False)))
            self.assertIsNone(ok, out)
            self.assertTrue(why)

    def test_timeout(self):
        ok, why = orca.running(fake_run(Result(124, "", "", True)))
        self.assertIsNone(ok)
        self.assertIn("timed out", why)


class SupportDirAndVersionTest(unittest.TestCase):
    def test_support_dir_env_and_default(self):
        old = os.environ.pop("BW_ORCA_SUPPORT", None)
        try:
            self.assertEqual(orca.support_dir(), os.path.expanduser("~/Library/Application Support/orca"))
            os.environ["BW_ORCA_SUPPORT"] = "/x/orca"
            self.assertEqual(orca.support_dir(), "/x/orca")
        finally:
            os.environ.pop("BW_ORCA_SUPPORT", None)
            if old is not None:
                os.environ["BW_ORCA_SUPPORT"] = old

    def test_app_version(self):
        with tempfile.TemporaryDirectory() as t:
            app = os.path.join(t, "Fake.app")
            os.makedirs(os.path.join(app, "Contents"))
            with open(os.path.join(app, "Contents", "Info.plist"), "wb") as f:
                plistlib.dump({"CFBundleShortVersionString": "9.8.7"}, f)
            self.assertEqual(orca.app_version(app), "9.8.7")
            self.assertEqual(orca.app_version(os.path.join(t, "None.app")), "unreadable")


class ReadSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(root, "state")
        self.d = os.path.join(root, "orca")
        os.makedirs(self.d)

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, settings_text, profile="prof-1", index=True, row=True):
        if index:
            with open(os.path.join(self.d, "orca-profile-index.json"), "w") as f:
                json.dump({"activeProfileId": profile}, f)
        pdir = os.path.join(self.d, "profiles", profile)
        os.makedirs(pdir, exist_ok=True)
        c = sqlite3.connect(os.path.join(pdir, "profile-state.db"))
        c.execute("CREATE TABLE profile_state_documents (domain TEXT PRIMARY KEY, payload TEXT, "
                  "domain_version INTEGER, revision INTEGER, updated_at INTEGER, content_hash TEXT)")
        c.execute("INSERT INTO profile_state_documents VALUES ('other', '{}', 1, 1, 0, 'h')")
        if row:
            c.execute("INSERT INTO profile_state_documents VALUES ('settings', ?, 1, 1, 0, 'h')", (settings_text,))
        c.commit()
        c.close()

    def test_returns_dict(self):
        for name in ("settings_manual.json", "settings_full_access.json"):
            text = fixture(name)
            self.build(text, profile=name.split(".")[0])
            got, why = orca.read_settings(self.state, self.d)
            self.assertEqual(why, "")
            self.assertEqual(got, json.loads(text))

    def test_missing_index(self):
        got, why = orca.read_settings(self.state, self.d)
        self.assertIsNone(got)
        self.assertIn("orca-profile-index.json", why)

    def test_missing_profile_db(self):
        with open(os.path.join(self.d, "orca-profile-index.json"), "w") as f:
            json.dump({"activeProfileId": "nope"}, f)
        got, why = orca.read_settings(self.state, self.d)
        self.assertIsNone(got)
        self.assertIn("profile-state.db", why)

    def test_unsafe_profile_id(self):
        for pid in ("../x", "", None, 3, ".."):
            with open(os.path.join(self.d, "orca-profile-index.json"), "w") as f:
                json.dump({"activeProfileId": pid}, f)
            got, why = orca.read_settings(self.state, self.d)
            self.assertIsNone(got, pid)
            self.assertIn("activeProfileId", why)

    def test_missing_row(self):
        self.build("{}", row=False)
        got, why = orca.read_settings(self.state, self.d)
        self.assertIsNone(got)
        self.assertIn("settings", why)

    def test_payload_not_object(self):
        for text in ("[1]", "not json"):
            self.build(text, profile="p" + str(len(text)))
            got, why = orca.read_settings(self.state, self.d)
            self.assertIsNone(got, text)
            self.assertIn("payload", why)

    def test_bad_index(self):
        with open(os.path.join(self.d, "orca-profile-index.json"), "w") as f:
            f.write("[")
        got, why = orca.read_settings(self.state, self.d)
        self.assertIsNone(got)
        self.assertIn("orca-profile-index.json", why)


def by_state(checks):
    return {c.state for c in checks}


class CheckSettingsTest(unittest.TestCase):
    def manual(self, **kw):
        s = json.loads(fixture("settings_manual.json"))
        s.update(kw)
        return s

    def test_manual_fixture_all_ok(self):
        cs = orca.check_settings(self.manual(), "")
        self.assertEqual(by_state(cs), {"ok"})

    def test_manual_values_ok(self):
        for v in sorted(orca.MANUAL_VALUES):
            self.assertEqual(by_state(orca.check_settings(self.manual(agentPermissionDefault=v), "")), {"ok"}, v)

    def test_full_access_warns(self):
        for v in sorted(orca.FULL_ACCESS_VALUES):
            cs = orca.check_settings(self.manual(agentPermissionDefault=v), "")
            warns = [c for c in cs if c.state == "warn"]
            self.assertEqual(len(warns), 1, v)
            self.assertIn("Orca starts agents with full access; set Agents → Agent Permissions → Manual", warns[0].detail)

    def test_other_permission_unknown_names_value(self):
        for v in ("accept-edits", "something-new", None):
            s = self.manual(agentPermissionDefault=v)
            cs = orca.check_settings(s, "")
            unk = [c for c in cs if c.state == "unknown"]
            self.assertEqual(len(unk), 1, v)
            self.assertIn(json.dumps(v), unk[0].detail)

    def test_absent_permission_unknown(self):
        s = self.manual()
        del s["agentPermissionDefault"]
        self.assertIn("unknown", by_state(orca.check_settings(s, "")))

    def test_full_access_override_warns_per_agent(self):
        ov = {"codex": "full-access", "claude": "auto", "pi": "own", "kimi": "ask"}
        cs = orca.check_settings(self.manual(agentPermissionOverrides=ov), "")
        warns = sorted(c.detail for c in cs if c.state == "warn")
        self.assertEqual(warns, ["Orca starts claude with full access (override); set it to Manual",
                                 "Orca starts codex with full access (override); set it to Manual"])

    def test_manual_overrides_ok(self):
        cs = orca.check_settings(self.manual(agentPermissionOverrides={"codex": "own", "claude": "ask"}), "")
        self.assertEqual(by_state(cs), {"ok"})

    def test_overrides_not_object_unknown(self):
        cs = orca.check_settings(self.manual(agentPermissionOverrides=["full-access"]), "")
        self.assertIn("unknown", by_state(cs))

    def test_telemetry_warns(self):
        cs = orca.check_settings(self.manual(telemetry={"optedIn": True}), "")
        self.assertEqual([c.state for c in cs if "telemetry" in c.name], ["warn"])

    def test_managed_accounts_warn_with_count(self):
        cs = orca.check_settings(self.manual(claudeManagedAccounts=[{}, {}], codexManagedAccounts=[{}]), "")
        warns = [c for c in cs if c.state == "warn"]
        self.assertEqual(len(warns), 1)
        self.assertIn("Orca manages 3 extra accounts; switching accounts under a running lead makes the logged plan wrong",
                      warns[0].detail)

    def test_managed_accounts_not_list_unknown(self):
        cs = orca.check_settings(self.manual(codexManagedAccounts="x"), "")
        self.assertIn("unknown", by_state(cs))

    def test_status_hooks_ok_either_way(self):
        for v, word in ((True, "on"), (False, "off")):
            cs = [c for c in orca.check_settings(self.manual(agentStatusHooksEnabled=v), "") if "hooks" in c.name]
            self.assertEqual([c.state for c in cs], ["ok"])
            self.assertIn(word, cs[0].detail)

    def test_full_access_fixture(self):
        cs = orca.check_settings(json.loads(fixture("settings_full_access.json")), "")
        self.assertEqual(sum(c.state == "warn" for c in cs), 3)

    def test_none_is_one_unknown_with_reason(self):
        cs = orca.check_settings(None, "no settings row")
        self.assertEqual(len(cs), 1)
        self.assertEqual((cs[0].state, cs[0].detail), ("unknown", "no settings row"))


if __name__ == "__main__":
    unittest.main()
