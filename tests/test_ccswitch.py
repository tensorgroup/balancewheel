import datetime
import hashlib
import json
import os
import sqlite3
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


SCHEMA = REPO / "tests" / "fixtures" / "ccswitch" / "schema.sql"


def epoch(day, hour=12):
    return int(datetime.datetime.strptime(f"{day} {hour}", "%Y-%m-%d %H").timestamp())


def fingerprint(path):
    return hashlib.sha256(slurp(path, "rb")).hexdigest(), os.stat(path).st_mtime_ns


class UsageBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(root, "state")
        self.d = os.path.join(root, "ccs")
        os.makedirs(self.d)
        self.db = os.path.join(self.d, "cc-switch.db")
        self.writer = sqlite3.connect(self.db)
        self.writer.execute("PRAGMA journal_mode = WAL")
        self.writer.execute("PRAGMA wal_autocheckpoint = 0")
        self.writer.executescript(slurp(SCHEMA))
        self.writer.commit()
        self.n = 0
        self.since = epoch("2026-10-01", 0)

    def tearDown(self):
        self.writer.close()
        self.tmp.cleanup()

    def log(self, source="session_log", model="m1", day="2026-10-10", status=200, cost="0.01",
            tin=10, tout=5, cr=2, cw=1, app="claude"):
        self.n += 1
        self.writer.execute(
            "INSERT INTO proxy_request_logs (request_id, provider_id, app_type, model, input_tokens, "
            "output_tokens, cache_read_tokens, cache_creation_tokens, total_cost_usd, status_code, "
            "created_at, data_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"r{self.n}", "p", app, model, tin, tout, cr, cw, cost, status, epoch(day), source))
        self.writer.commit()

    def rollup(self, day="2026-10-05", app="claude", model="m1", n=4, ok=3, cost="1.5"):
        self.writer.execute(
            "INSERT INTO usage_daily_rollups (date, app_type, provider_id, model, request_count, "
            "success_count, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
            "total_cost_usd) VALUES (?,?,?,?,?,?,100,50,20,10,?)", (day, app, "p", model, n, ok, cost))
        self.writer.commit()

    def usage(self, **kw):
        return ccswitch.usage(self.state, self.since, d=self.d, **kw)


class UsageTest(UsageBase):
    def test_proxy_rows_excluded_and_sources_map_to_tools(self):
        self.log(source="proxy", model="px")
        for src, tool in [("session_log", "claude"), ("codex_session", "codex"), ("gemini_session", "gemini"),
                          ("pi_session", "pi"), ("kimi_session", "kimi"), ("agy_session", "agy")]:
            self.log(source=src, model=f"m-{tool}")
        out = self.usage()
        self.assertTrue(out["available"], out["reason"])
        self.assertEqual({g["model"]: g["tool"] for g in out["groups"]},
                         {"m-claude": "claude", "m-codex": "codex", "m-gemini": "gemini",
                          "m-pi": "pi", "m-kimi": "kimi", "m-agy": "agy"})
        self.assertFalse(any(g["unmapped"] for g in out["groups"]))

    def test_unknown_source_is_its_own_tool_and_unmapped(self):
        self.log(source="foo_session")
        (g,) = self.usage()["groups"]
        self.assertEqual((g["tool"], g["unmapped"], g["origin"]), ("foo_session", True, "log"))

    def test_group_sums_tokens_requests_and_errors(self):
        self.log(status=200)
        self.log(status=500)
        self.log(status=None)
        self.log(status=429)
        (g,) = self.usage()["groups"]
        self.assertEqual((g["requests"], g["errors"]), (4, 2))
        self.assertEqual((g["tokens_in"], g["tokens_out"], g["cache_read"], g["cache_write"]), (40, 20, 8, 4))
        self.assertEqual(g["day"], "2026-10-10")

    def test_null_status_is_not_an_error(self):
        self.log(status=None)
        self.assertEqual(self.usage()["groups"][0]["errors"], 0)

    def test_days_split_in_local_time(self):
        self.log(day="2026-10-10")
        self.log(day="2026-10-11")
        self.assertEqual([g["day"] for g in self.usage()["groups"]], ["2026-10-10", "2026-10-11"])

    def test_since_and_until_bound_log_rows(self):
        self.log(day="2026-09-20")
        self.log(day="2026-10-10")
        self.log(day="2026-10-20")
        out = self.usage(until=epoch("2026-10-15"))
        self.assertEqual([g["day"] for g in out["groups"]], ["2026-10-10"])

    def test_rollups_have_origin_and_errors_from_counts(self):
        self.rollup(n=4, ok=3)
        (g,) = self.usage()["groups"]
        self.assertEqual((g["origin"], g["requests"], g["errors"], g["unmapped"]), ("rollup", 4, 1, False))
        self.assertEqual((g["tokens_in"], g["tokens_out"], g["cache_read"], g["cache_write"]), (100, 50, 20, 10))
        self.assertEqual(g["cost_usd"], "1.5")

    def test_rollups_before_since_are_excluded(self):
        self.rollup(day="2026-09-15")
        self.assertEqual(self.usage()["groups"], [])

    def test_costs_sum_exactly(self):
        for _ in range(3):
            self.log(cost="0.0123")
        self.assertEqual(self.usage()["groups"][0]["cost_usd"], "0.0369")
        self.log(model="m2", cost="0.1")
        self.log(model="m2", cost="0.2")
        self.assertEqual(self.usage()["groups"][1]["cost_usd"], "0.3")

    def test_unparseable_cost_makes_the_group_cost_none(self):
        self.log(cost="0.01")
        self.log(cost="n/a")
        self.log(model="m2", cost="0.02")
        by = {g["model"]: g for g in self.usage()["groups"]}
        self.assertIsNone(by["m1"]["cost_usd"])
        self.assertEqual(by["m1"]["requests"], 2)
        self.assertEqual(by["m2"]["cost_usd"], "0.02")

    def test_non_finite_cost_is_none(self):
        self.log(cost="NaN")
        self.log(model="m2", cost="Infinity")
        self.assertEqual([g["cost_usd"] for g in self.usage()["groups"]], [None, None])

    def test_dropped_required_column_is_unavailable_naming_it(self):
        self.writer.execute("ALTER TABLE proxy_request_logs DROP COLUMN total_cost_usd")
        self.writer.commit()
        out = self.usage()
        self.assertFalse(out["available"])
        self.assertIn("total_cost_usd", out["reason"])
        self.assertEqual(out["groups"], [])

    def test_missing_table_is_unavailable_naming_it(self):
        self.writer.execute("DROP TABLE usage_daily_rollups")
        self.writer.commit()
        out = self.usage()
        self.assertFalse(out["available"])
        self.assertIn("usage_daily_rollups", out["reason"])

    def test_extra_column_is_ignored(self):
        self.writer.execute("ALTER TABLE proxy_request_logs ADD COLUMN shiny TEXT")
        self.writer.commit()
        self.log()
        out = self.usage()
        self.assertTrue(out["available"], out["reason"])
        self.assertEqual(len(out["groups"]), 1)

    def test_absent_dir_is_not_installed(self):
        path = os.path.join(self.d, "nope")
        out = ccswitch.usage(self.state, self.since, d=path)
        self.assertFalse(out["available"])
        self.assertEqual(out["reason"], f"cc-switch not installed ({os.path.join(path, 'cc-switch.db')} absent)")

    def test_default_dir_follows_env(self):
        self.log()
        old = os.environ.get("BW_CCSWITCH_DIR")
        os.environ["BW_CCSWITCH_DIR"] = self.d
        try:
            self.assertTrue(ccswitch.usage(self.state, self.since)["available"])
        finally:
            if old is None:
                del os.environ["BW_CCSWITCH_DIR"]
            else:
                os.environ["BW_CCSWITCH_DIR"] = old

    def test_originals_untouched_and_sealed_dir_empty(self):
        self.log()
        self.rollup()
        files = [self.db, self.db + "-wal"]
        self.assertTrue(os.path.exists(files[1]))
        before = {p: fingerprint(p) for p in files}
        listing = sorted(os.listdir(self.d))
        self.usage()
        ccswitch.prices(self.state, d=self.d)
        self.assertEqual({p: fingerprint(p) for p in files}, before)
        self.assertEqual(sorted(os.listdir(self.d)), listing)
        self.assertEqual(os.listdir(os.path.join(self.state, "sealed")), [])

    def test_prices_reads_model_pricing(self):
        self.writer.execute("INSERT INTO model_pricing (model_id, display_name, input_cost_per_million, "
                            "output_cost_per_million) VALUES ('m1', 'M One', '3.00', '15.00')")
        self.writer.commit()
        self.assertEqual(ccswitch.prices(self.state, d=self.d), {"m1": {"input": "3.00", "output": "15.00"}})

    def test_prices_empty_when_absent(self):
        self.assertEqual(ccswitch.prices(self.state, d=self.d), {})
        self.assertEqual(ccswitch.prices(self.state, d=os.path.join(self.d, "nope")), {})
        self.writer.execute("DROP TABLE model_pricing")
        self.writer.commit()
        self.assertEqual(ccswitch.prices(self.state, d=self.d), {})


class UsageRobustnessTest(UsageBase):
    def raw_log(self, at, **kw):
        self.log(**kw)
        self.writer.execute("UPDATE proxy_request_logs SET created_at = ? WHERE request_id = ?", (at, f"r{self.n}"))
        self.writer.commit()

    def test_out_of_range_timestamps_are_skipped_and_counted(self):
        self.log()
        for bad in (1.7e12, 1e18, float("inf")):
            self.raw_log(bad)
        out = self.usage()
        self.assertTrue(out["available"], out["reason"])
        self.assertEqual(out["skipped_rows"], 3)
        self.assertEqual([g["requests"] for g in out["groups"]], [1])

    def test_no_skips_reports_zero(self):
        self.log()
        self.assertEqual(self.usage()["skipped_rows"], 0)

    def test_bad_bounds_are_unavailable_with_a_reason(self):
        for bad in (1.7e12, float("nan"), float("inf"), "x", None, True):
            out = ccswitch.usage(self.state, bad, d=self.d)
            self.assertFalse(out["available"], bad)
            self.assertIn("since", out["reason"])
        out = ccswitch.usage(self.state, self.since, until=1.7e12, d=self.d)
        self.assertFalse(out["available"])
        self.assertIn("until", out["reason"])

    def test_rollup_for_the_until_day_excluded_at_local_midnight(self):
        self.rollup(day="2026-10-14")
        self.rollup(day="2026-10-15")
        self.assertEqual([g["day"] for g in self.usage(until=epoch("2026-10-15", 0))["groups"]], ["2026-10-14"])
        self.assertEqual([g["day"] for g in self.usage(until=epoch("2026-10-15", 1))["groups"]],
                         ["2026-10-14", "2026-10-15"])

    def set_tokens(self, value):
        self.writer.execute("UPDATE proxy_request_logs SET input_tokens = ?, output_tokens = 5", (value,))
        self.writer.commit()

    def test_tokens_coerce_integral_floats_and_numeric_strings(self):
        self.log()
        self.log()
        self.writer.execute("UPDATE proxy_request_logs SET input_tokens = 7.0 WHERE request_id = 'r1'")
        self.writer.execute("UPDATE proxy_request_logs SET input_tokens = ' 8 ' WHERE request_id = 'r2'")
        self.writer.commit()
        self.assertEqual(self.usage()["groups"][0]["tokens_in"], 15)

    def test_unreadable_tokens_make_that_field_none_never_zero(self):
        for bad in ("lots", 1.5, "2.5", b"x", float("inf")):
            self.writer.execute("DELETE FROM proxy_request_logs")
            self.log()
            self.log()
            self.writer.execute("UPDATE proxy_request_logs SET input_tokens = ? WHERE request_id = 'r%d'" % self.n, (bad,))
            self.writer.commit()
            g = self.usage()["groups"][0]
            self.assertIsNone(g["tokens_in"], bad)
            self.assertEqual(g["tokens_out"], 10)
