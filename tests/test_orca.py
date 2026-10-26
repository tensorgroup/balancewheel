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


NOW = 1793000060.0  # a minute after the fixtures' updatedAt (1793000000000 ms)
V = "1.0.0"


def payload(name="account_list_ok.json"):
    return json.loads(fixture(name))


def plan(provider, quota="orca", windows=None):
    return {"quota": quota, "orca_provider": provider, "orca_windows": windows or {}}


def snap(p, provider, now=NOW, every=15, windows=None):
    return orca.snapshot(p, plan(provider, windows=windows), now, every, V)


class AccountListTest(unittest.TestCase):
    def test_ok(self):
        run = fake_run(Result(0, fixture("account_list_ok.json"), "", False))
        doc, why = orca.account_list(run)
        self.assertEqual(why, "")
        self.assertIn("rateLimits", doc["result"])
        self.assertEqual(run.calls, [(["orca", "account", "list", "--json"], 10)])

    def test_absent(self):
        self.assertEqual(orca.account_list(fake_run(Result(127, "", "orca: not found", False))),
                         (None, "orca not installed"))

    def test_timeout_garbage_and_not_ok(self):
        for r, word in ((Result(124, "", "", True), "timed out"), (Result(0, "<html>", "", False), "JSON"),
                        (Result(0, '{"ok": false}', "", False), "ok"), (Result(0, "[]", "", False), "ok")):
            doc, why = orca.account_list(fake_run(r))
            self.assertIsNone(doc)
            self.assertIn(word, why)


class ShapeTest(unittest.TestCase):
    def test_ok_fixture_every_provider(self):
        p = payload()
        for provider in ("claude", "codex", "kimi", "antigravity", "gemini"):
            self.assertEqual(orca.shape_ok(p, provider), (True, ""), provider)

    def test_first_bad_path_named(self):
        ok, why = orca.shape_ok(payload("account_list_shape_changed.json"), "codex")
        self.assertFalse(ok)
        self.assertEqual(why, "rateLimits.codex.weekly.resetsAt: expected int, got str")

    def test_other_providers_not_inspected(self):
        self.assertEqual(orca.shape_ok(payload("account_list_shape_changed.json"), "kimi"), (True, ""))

    def test_rate_limits_not_dict(self):
        for p in ({}, {"result": {}}, {"result": {"rateLimits": []}}, None):
            ok, why = orca.shape_ok(p, "codex")
            self.assertFalse(ok)
            self.assertTrue(why.startswith("rateLimits"), why)

    def test_bool_is_not_int(self):
        p = payload()
        p["result"]["rateLimits"]["codex"]["updatedAt"] = True
        self.assertEqual(orca.shape_ok(p, "codex"), (False, "rateLimits.codex.updatedAt: expected int, got bool"))


class SnapshotTest(unittest.TestCase):
    def test_codex_weekly_only(self):
        s = snap(payload(), "codex")
        self.assertNotIn("unknown", s)
        self.assertEqual(s["windows"], [{"name": "weekly", "used_pct": 25.5, "resets_at": 1793400000.0}])
        self.assertEqual(s["observed_at"], 1793000000.0)
        self.assertEqual((s["source"], s["source_version"], s["pools"]), ("orca", V, []))

    def test_kimi_session_only(self):
        s = snap(payload(), "kimi")
        self.assertEqual(s["windows"], [{"name": "session", "used_pct": 60.0, "resets_at": 1793005000.0}])

    def test_unavailable_carries_error(self):
        s = snap(payload("account_list_unavailable.json"), "codex")
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "orca: unavailable — Not signed in")
        self.assertEqual((s["source"], s["source_version"]), ("orca", V))

    def test_error_with_status_ok(self):
        s = snap(payload("account_list_unavailable.json"), "kimi")
        self.assertTrue(s["unknown"])
        self.assertIn("token refresh failed", s["reason"])

    def test_stale(self):
        s = snap(payload(), "codex", now=1793000000 + 31 * 60, every=15)
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "stale: observed 31 min ago")
        self.assertEqual(s["last_known_at"], 1793000000.0)

    def test_huge_integers_do_not_raise(self):
        p = payload()
        p["result"]["rateLimits"]["codex"]["weekly"]["resetsAt"] = 10 ** 400
        self.assertIn("weekly.resetsAt", snap(p, "codex")["reason"])
        p = payload()
        p["result"]["rateLimits"]["codex"]["updatedAt"] = 10 ** 400
        self.assertEqual(snap(p, "codex")["reason"], "observed in the future")

    def test_future(self):
        s = snap(payload(), "codex", now=1793000000 - 400)
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "observed in the future")

    def test_resets_at_in_seconds(self):
        p = payload()
        p["result"]["rateLimits"]["codex"]["weekly"]["resetsAt"] = 1793400000
        s = snap(p, "codex")
        self.assertTrue(s["unknown"])
        self.assertIn("rateLimits.codex.weekly.resetsAt", s["reason"])

    def test_used_percent_out_of_range_or_wrong_type(self):
        for v in (140, -1, "40", float("nan"), 10 ** 400):
            p = payload()
            p["result"]["rateLimits"]["codex"]["weekly"]["usedPercent"] = v
            s = snap(p, "codex")
            self.assertTrue(s.get("unknown"), v)
            self.assertIn("rateLimits.codex.weekly.usedPercent", s["reason"], v)

    def test_fable_weekly_ignored_without_mapping(self):
        s = snap(payload(), "claude")
        self.assertEqual([w["name"] for w in s["windows"]], ["session", "weekly"])
        self.assertEqual(s["pools"], [])

    def test_fable_weekly_pool_with_mapping(self):
        s = snap(payload(), "claude", windows={"fableWeekly": ["fable-lead", "fable-seat"]})
        self.assertEqual(s["pools"], [{"name": "fableWeekly", "used_pct": 100.0, "resets_at": 1793200000.0,
                                       "applies_to": ["fable-lead", "fable-seat"]}])
        self.assertEqual([w["name"] for w in s["windows"]], ["session", "weekly"])

    def test_bucket_mapped_by_name(self):
        s = snap(payload(), "antigravity", windows={"bucket:model-b": ["agy-b"], "bucket:no-such": ["x"]})
        self.assertEqual(s["pools"], [{"name": "bucket:model-b", "used_pct": 10.0, "resets_at": 1793250000.0,
                                       "applies_to": ["agy-b"]}])

    def test_malformed_mapped_pool_is_unknown_naming_it(self):
        p = payload()
        p["result"]["rateLimits"]["claude"]["fableWeekly"]["resetsAt"] = "soon"
        s = snap(p, "claude", windows={"fableWeekly": ["fable-lead"]})
        self.assertTrue(s["unknown"])
        self.assertIn("rateLimits.claude.fableWeekly.resetsAt", s["reason"])

    def test_shape_changed_codex_unknown_kimi_ok(self):
        p = payload("account_list_shape_changed.json")
        s = snap(p, "codex")
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], f"Orca {V} changed shape: rateLimits.codex.weekly.resetsAt: expected int, got str")
        self.assertNotIn("unknown", snap(p, "kimi"))

    def test_missing_session_and_weekly_keys(self):
        p = payload()
        del p["result"]["rateLimits"]["codex"]["session"]
        del p["result"]["rateLimits"]["codex"]["weekly"]
        s = snap(p, "codex")
        self.assertTrue(s["unknown"])
        self.assertIn("rateLimits.codex.session", s["reason"])

    def test_no_account_wide_window(self):
        p = payload()
        p["result"]["rateLimits"]["codex"]["weekly"] = None
        self.assertEqual(snap(p, "codex")["reason"], "no account-wide window")

    def test_provider_missing(self):
        s = snap(payload(), "zcode")
        self.assertTrue(s["unknown"])
        self.assertIn("zcode", s["reason"])

    def test_never_raises_on_odd_payloads(self):
        for p in (None, [], {}, {"result": None}, {"result": {"rateLimits": {"codex": "x"}}},
                  {"result": {"rateLimits": {"codex": {}}}}):
            self.assertTrue(snap(p, "codex")["unknown"], p)

    def test_configured_siblings_ignored(self):
        p = payload()
        self.assertIs(p["result"]["rateLimits"]["minimaxCookieConfigured"], True)
        self.assertNotIn("unknown", snap(p, "codex"))


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        self.config = {"plans": {"chatgpt": plan("codex"), "kimi-plan": plan("kimi"),
                                 "claude-plan": {"quota": "statusline", "orca_provider": None, "orca_windows": {}}},
                       "rotation": {"probe_every_min": 15}}

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name):
        with open(os.path.join(self.state, "quota", name)) as f:
            return json.load(f)

    def test_writes_one_snapshot_per_mapped_plan_and_no_caps(self):
        run = fake_run(Result(0, fixture("account_list_shape_changed.json"), "", False))
        orca.refresh(self.state, self.config, NOW, run=run, version=V)
        self.assertEqual(len(run.calls), 1)
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, "quota"))),
                         ["chatgpt.orca.json", "kimi-plan.orca.json"])
        self.assertIn("changed shape", self.read("chatgpt.orca.json")["reason"])
        self.assertNotIn("unknown", self.read("kimi-plan.orca.json"))
        self.assertFalse(os.path.exists(os.path.join(self.state, "caps")))

    def test_orca_absent_writes_unknown(self):
        orca.refresh(self.state, self.config, NOW, run=fake_run(Result(127, "", "", False)), version=V)
        s = self.read("chatgpt.orca.json")
        self.assertEqual((s["unknown"], s["reason"]), (True, "orca not installed"))

    def test_no_mapped_plan_no_call(self):
        run = fake_run(Result(0, fixture("account_list_ok.json"), "", False))
        cfg = {"plans": {"claude-plan": self.config["plans"]["claude-plan"]}, "rotation": {"probe_every_min": 15}}
        self.assertIsNone(orca.refresh(self.state, cfg, NOW, run=run, version=V))
        self.assertEqual(run.calls, [])

    def test_full_reading_never_creates_a_cap(self):
        p = payload()
        p["result"]["rateLimits"]["codex"]["weekly"]["usedPercent"] = 100
        orca.refresh(self.state, self.config, NOW, run=fake_run(Result(0, json.dumps(p), "", False)), version=V)
        self.assertEqual(self.read("chatgpt.orca.json")["windows"][0]["used_pct"], 100.0)
        self.assertFalse(os.path.exists(os.path.join(self.state, "caps")))


class CheckVersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_read_records_silently(self):
        self.assertIsNone(orca.check_version(self.state, "1.0.0", {"codex": (True, "")}))
        self.assertTrue(os.path.exists(os.path.join(self.state, "probes", "orca-shape.json")))

    def test_version_change_reported_once_with_per_provider_results(self):
        orca.check_version(self.state, "1.0.0", {"codex": (True, ""), "kimi": (True, "")})
        results = {"codex": (False, "rateLimits.codex.weekly.resetsAt: expected int, got str"), "kimi": (True, "")}
        msg = orca.check_version(self.state, "1.1.0", results)
        self.assertEqual(msg, "Orca 1.0.0 → 1.1.0: shape re-checked: codex failed "
                              "(rateLimits.codex.weekly.resetsAt: expected int, got str), kimi ok")
        self.assertIsNone(orca.check_version(self.state, "1.1.0", results))

    def test_unreadable_version_not_recorded(self):
        orca.check_version(self.state, "1.0.0", {"codex": (True, "")})
        self.assertIsNone(orca.check_version(self.state, "unreadable", {"codex": (True, "")}))
        self.assertIsNone(orca.check_version(self.state, "1.0.0", {"codex": (True, "")}))

    def test_refresh_reports_version_change(self):
        cfg = {"plans": {"chatgpt": plan("codex"), "kimi-plan": plan("kimi")}, "rotation": {"probe_every_min": 15}}
        run = fake_run(Result(0, fixture("account_list_shape_changed.json"), "", False))
        self.assertIsNone(orca.refresh(self.state, cfg, NOW, run=run, version="1.0.0"))
        msg = orca.refresh(self.state, cfg, NOW, run=run, version="1.1.0")
        self.assertIn("Orca 1.0.0 → 1.1.0: shape re-checked: codex failed (rateLimits.codex.weekly.resetsAt", msg)
        self.assertIn("kimi ok", msg)
        self.assertIsNone(orca.refresh(self.state, cfg, NOW, run=run, version="1.1.0"))
        self.assertFalse(os.path.exists(os.path.join(self.state, "caps")))
