import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
sys.path.insert(0, str(HERE))
import config as cfg  # noqa: E402
from test_config_v2 import load_obj  # noqa: E402

V2 = HERE / "fixtures" / "config_v2.json"
V3 = HERE / "fixtures" / "config_v3.json"


def v3():
    return json.loads(V3.read_text())


def v2():
    return json.loads(V2.read_text())


class SchemaV3(unittest.TestCase):
    def rejects(self, obj, *needles):
        with self.assertRaises(cfg.ConfigError) as e:
            load_obj(obj)
        for n in needles:
            self.assertIn(n, str(e.exception))

    def test_defaults_per_runtime_and_source(self):
        c = cfg.load_config(str(V3))
        self.assertEqual(c["schema_version"], 3)
        want = {"lead-cc": "native", "lead-cx": "native", "lead-agy": "guard", "lead-pi": "guard", "lead-kimi": "manual"}
        for name, autonomy in want.items():
            self.assertEqual(c["leads"][name]["autonomy"], autonomy, name)
            self.assertEqual(c["leads"][name]["autonomy_source"], "default", name)

    def test_explicit_autonomy_has_config_source(self):
        c = cfg.load_config(str(V3))
        self.assertEqual(c["leads"]["lead-bypass"]["autonomy"], "bypass")
        self.assertEqual(c["leads"]["lead-bypass"]["autonomy_source"], "config")

    def test_v2_fixture_gets_legacy_autonomy(self):
        c = cfg.load_config(str(V2))
        want = {"lead-a": "native", "lead-b": "manual", "lead-c": "bypass"}
        for name, autonomy in want.items():
            self.assertEqual(c["leads"][name]["autonomy"], autonomy, name)
            self.assertEqual(c["leads"][name]["autonomy_source"], "legacy", name)

    def test_legacy_permission_arg_is_custom(self):
        o = v2(); o["leads"]["lead-a"]["args"] = ["--dangerously-skip-permissions"]
        self.assertEqual(load_obj(o)["leads"]["lead-a"]["autonomy"], "custom")

    def test_legacy_permission_arg_with_equals_is_custom(self):
        o = v2(); o["leads"]["lead-a"]["args"] = ["--permission-mode=plan"]
        c = load_obj(o)
        self.assertEqual(c["leads"]["lead-a"]["autonomy"], "custom")
        self.assertEqual(c["leads"]["lead-a"]["args"], ["--permission-mode=plan"])

    def test_pi_native_rejected(self):
        o = v3(); o["leads"]["lead-pi"]["autonomy"] = "native"
        self.rejects(o, "leads.lead-pi.autonomy: native is not supported for pi; valid: ['bypass', 'guard']")

    def test_codex_bypass_rejected(self):
        o = v3(); o["leads"]["lead-cx"]["autonomy"] = "bypass"
        self.rejects(o, "leads.lead-cx.autonomy: bypass is not supported for codex")

    def test_unknown_autonomy_value_rejected(self):
        o = v3(); o["leads"]["lead-cc"]["autonomy"] = "custom"
        self.rejects(o, "leads.lead-cc.autonomy")

    def test_v3_permission_args_rejected(self):
        o = v3(); o["leads"]["lead-cc"]["args"] = ["--permission-mode=plan"]
        self.rejects(o, "leads.lead-cc.args")
        o = v3(); o["leads"]["lead-cx"]["args"] = ["-a", "never"]
        self.rejects(o, "leads.lead-cx.args")

    def test_v3_unrelated_args_load(self):
        o = v3(); o["leads"]["lead-cc"]["args"] = ["--verbose"]
        self.assertEqual(load_obj(o)["leads"]["lead-cc"]["args"], ["--verbose"])

    def test_autonomy_in_v2_rejected(self):
        o = v2(); o["leads"]["lead-a"]["autonomy"] = "native"
        self.rejects(o, "leads.lead-a.autonomy", "needs schema_version 3")

    def test_guard_and_profile_keys_in_v2_rejected(self):
        o = v2(); o["guard"] = {}
        self.rejects(o, "guard", "needs schema_version 3")
        o = v2(); o["profiles"]["careful"]["protected_branches"] = ["main"]
        self.rejects(o, "profiles.careful.protected_branches", "needs schema_version 3")
        o = v2(); o["profiles"]["careful"]["deploy_commands"] = []
        self.rejects(o, "profiles.careful.deploy_commands", "needs schema_version 3")

    def test_schema_4_rejected_naming_supported(self):
        o = v3(); o["schema_version"] = 4
        self.rejects(o, "1", "2", "3")

    def test_supported_autonomy_covers_exactly_runtimes(self):
        self.assertEqual(set(cfg.SUPPORTED_AUTONOMY), cfg.RUNTIMES)
        self.assertEqual(set(cfg.PERMISSION_ARGS), cfg.RUNTIMES)
        self.assertEqual(set(cfg.DEFAULT_AUTONOMY), cfg.RUNTIMES)
        self.assertEqual(set(cfg.LEGACY_AUTONOMY), cfg.RUNTIMES)
        for rt, d in cfg.DEFAULT_AUTONOMY.items():
            self.assertIn(d, cfg.SUPPORTED_AUTONOMY[rt])

    def test_profile_lists_default_and_explicit(self):
        c = cfg.load_config(str(V3))
        self.assertEqual(c["profiles"]["careful"]["protected_branches"], ["main", "release"])
        self.assertEqual(c["profiles"]["careful"]["deploy_commands"], ["make deploy", "./scripts/ship"])
        self.assertEqual(c["profiles"]["quick"]["protected_branches"], ["main", "master"])
        self.assertEqual(c["profiles"]["quick"]["deploy_commands"], [])
        self.assertEqual(cfg.load_config(str(V2))["profiles"]["careful"]["protected_branches"], ["main", "master"])

    def test_profile_lists_must_be_string_lists(self):
        o = v3(); o["profiles"]["careful"]["protected_branches"] = "main"
        self.rejects(o, "profiles.careful.protected_branches")
        o = v3(); o["profiles"]["careful"]["deploy_commands"] = [1]
        self.rejects(o, "profiles.careful.deploy_commands")

    def test_guard_resolved(self):
        g = cfg.load_config(str(V3))["guard"]
        self.assertEqual(g, {"mode": {"careful": "shadow", "quick": "shadow"}, "model": "vendor/judge-model",
                             "on_judge_failure": "allow", "rules_on_native": False})

    def test_guard_defaults_when_absent(self):
        for path in (V2,):
            g = cfg.load_config(str(path))["guard"]
            self.assertEqual(set(g["mode"].values()), {"shadow"})
            self.assertIsNone(g["model"])
            self.assertEqual(g["on_judge_failure"], "allow")
            self.assertFalse(g["rules_on_native"])
        o = v3(); del o["guard"]
        self.assertIsNone(load_obj(o)["guard"]["model"])

    def test_guard_acting_rejected(self):
        o = v3(); o["guard"]["mode"] = {"careful": "acting"}
        self.rejects(o, "guard.mode.careful", "acting arrives with build step 4 (needs `bw guard eval`)")

    def test_guard_rules_on_native_true_rejected(self):
        o = v3(); o["guard"]["rules_on_native"] = True
        self.rejects(o, "guard.rules_on_native: not built yet")

    def test_guard_mode_unknown_profile_rejected(self):
        o = v3(); o["guard"]["mode"] = {"ghost": "shadow"}
        self.rejects(o, "guard.mode.ghost")

    def test_guard_mode_bad_value_rejected(self):
        o = v3(); o["guard"]["mode"] = {"careful": "loud"}
        self.rejects(o, "guard.mode.careful")

    def test_guard_on_judge_failure_rejected(self):
        o = v3(); o["guard"]["on_judge_failure"] = "deny"
        self.rejects(o, "guard.on_judge_failure")

    def test_guard_unknown_key_rejected(self):
        o = v3(); o["guard"]["extra"] = 1
        self.rejects(o, "guard.extra: unknown key")

    def test_guard_model_equal_to_guarded_lead_rejected(self):
        o = v3(); o["guard"]["model"] = "vendor/model-d"  # lead-agy runs guard
        self.rejects(o, "guard.model", "must differ from the lead's own model")

    def test_guard_model_may_equal_unguarded_lead(self):
        o = v3(); o["guard"]["model"] = "vendor/model-a"  # lead-cc is native
        self.assertEqual(load_obj(o)["guard"]["model"], "vendor/model-a")

    def test_example_config_is_schema_3(self):
        c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
        self.assertEqual(c["schema_version"], 3)
        self.assertEqual(c["leads"]["agy-claude"]["autonomy"], "manual")
        self.assertEqual(c["leads"]["agy-claude"]["autonomy_source"], "config")
        self.assertEqual(c["leads"]["astra"]["autonomy_source"], "default")


class PlanOrcaAndProbeKeys(unittest.TestCase):
    def rejects(self, obj, *needles):
        with self.assertRaises(cfg.ConfigError) as e:
            load_obj(obj)
        for n in needles:
            self.assertIn(n, str(e.exception))

    def plan(self, **kw):
        o = v3()
        o["plans"]["plan-b"].update(kw)
        return o

    def test_defaults(self):
        p = cfg.load_config(str(V3))["plans"]["plan-a"]
        self.assertEqual((p["orca_provider"], p["orca_windows"], p["probe"], p["key_env"]),
                         (None, {}, None, "OPENROUTER_API_KEY"))

    def test_orca_quota_requires_provider(self):
        self.rejects(self.plan(quota="orca"), "plans.plan-b.orca_provider")
        c = load_obj(self.plan(quota="orca", orca_provider="codex"))
        self.assertEqual(c["plans"]["plan-b"]["orca_provider"], "codex")
        self.assertEqual(c["plans"]["plan-b"]["quota"], "orca")

    def test_orca_provider_shape_and_none(self):
        for bad in ("", "1x", "a-b", "a b", 3):
            self.rejects(self.plan(orca_provider=bad), "plans.plan-b.orca_provider")
        self.assertEqual(load_obj(self.plan(orca_provider="opencodeGo"))["plans"]["plan-b"]["orca_provider"], "opencodeGo")
        self.rejects(self.plan(quota="none", orca_provider="codex"), "plans.plan-b.orca_provider")

    def test_orca_windows(self):
        o = self.plan(orca_provider="claude", orca_windows={"fableWeekly": ["alpha"], "bucket:Opus 5.5": ["lead-cc", "beta"]})
        w = load_obj(o)["plans"]["plan-b"]["orca_windows"]
        self.assertEqual(w, {"fableWeekly": ["alpha"], "bucket:Opus 5.5": ["lead-cc", "beta"]})
        self.rejects(self.plan(orca_windows={"fableWeekly": ["alpha"]}), "plans.plan-b.orca_windows")
        for bad in ("weekly", "bucket:", "bucket:a/b"):
            self.rejects(self.plan(orca_provider="claude", orca_windows={bad: ["alpha"]}), "plans.plan-b.orca_windows")
        self.rejects(self.plan(orca_provider="claude", orca_windows={"fableWeekly": ["nobody"]}),
                     "plans.plan-b.orca_windows.fableWeekly", "nobody")
        self.rejects(self.plan(orca_provider="claude", orca_windows={"fableWeekly": "alpha"}), "plans.plan-b.orca_windows.fableWeekly")
        self.rejects(self.plan(orca_provider="claude", orca_windows=[]), "plans.plan-b.orca_windows")

    def test_probe_explicit_derived_and_unknown(self):
        self.assertEqual(load_obj(self.plan(vendor="google"))["plans"]["plan-b"]["probe"], "agy")
        self.assertEqual(load_obj(self.plan(vendor="openrouter"))["plans"]["plan-b"]["probe"], "openrouter")
        self.assertEqual(load_obj(self.plan(vendor="other", probe="agy"))["plans"]["plan-b"]["probe"], "agy")
        self.assertIsNone(load_obj(self.plan(vendor="other"))["plans"]["plan-b"]["probe"])  # still loads
        self.assertIsNone(load_obj(self.plan(vendor="google", quota="statusline"))["plans"]["plan-b"]["probe"])
        self.rejects(self.plan(probe="nope"), "plans.plan-b.probe")

    def test_key_env(self):
        p = self.plan(vendor="other", probe="openrouter", key_env="MY_KEY_2")
        self.assertEqual(load_obj(p)["plans"]["plan-b"]["key_env"], "MY_KEY_2")
        for bad in ("my_key", "1KEY", "A-B", ""):
            self.rejects(self.plan(probe="openrouter", key_env=bad), "plans.plan-b.key_env")
        self.rejects(self.plan(vendor="google", key_env="MY_KEY"), "plans.plan-b.key_env")

    def test_trailing_newline_never_passes_a_pattern(self):
        self.rejects(self.plan(orca_provider="codex\n"), "plans.plan-b.orca_provider")
        self.rejects(self.plan(orca_provider="claude", orca_windows={"fableWeekly\n": ["alpha"]}),
                     "plans.plan-b.orca_windows")
        self.rejects(self.plan(vendor="other", probe="openrouter", key_env="MY_KEY\n"), "plans.plan-b.key_env")

    def test_vendor_must_be_a_string(self):
        for bad in (["google"], {"a": 1}, 3, None):
            self.rejects(self.plan(vendor=bad, quota="probe"), "plans.plan-b.vendor")

    def test_example_has_orca_plans_without_warnings(self):
        c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
        self.assertEqual(c["warnings"], [])
        pl = c["plans"]
        self.assertEqual((pl["chatgpt-plan"]["quota"], pl["chatgpt-plan"]["orca_provider"]), ("orca", "codex"))
        self.assertEqual((pl["kimi-plan"]["quota"], pl["kimi-plan"]["orca_provider"]), ("orca", "kimi"))
        self.assertEqual((pl["claude-plan"]["quota"], pl["claude-plan"]["orca_provider"]), ("statusline", "claude"))
        self.assertEqual(pl["claude-plan"]["orca_windows"], {"fableWeekly": ["fable"]})
        self.assertEqual((pl["google-plan"]["quota"], pl["google-plan"]["orca_provider"], pl["google-plan"]["probe"]),
                         ("probe", "antigravity", "agy"))
        self.assertEqual(pl["openrouter"]["probe"], "openrouter")

    def test_schema_2_accepts_the_same_plan_keys(self):
        o = v2()
        o["plans"]["plan-b"].update(quota="orca", orca_provider="codex", orca_windows={"fableWeekly": ["alpha"]})
        o["plans"]["metered"].update(probe="openrouter", key_env="OR_KEY")
        c = load_obj(o)
        self.assertEqual(c["plans"]["plan-b"]["orca_windows"], {"fableWeekly": ["alpha"]})
        self.assertEqual(c["plans"]["metered"]["key_env"], "OR_KEY")


class Triage(unittest.TestCase):
    def write(self, extra):
        base = {"schema_version": 3, "plans": {"p": {"billing": "plan", "quota": "statusline"}},
                "leads": {"l": {"runtime": "claude-code", "model": "m", "plan": "p"}},
                "seats": {"s": {"runtime": "codex", "model": "x", "roles": ["review"], "plan": "p"}},
                "profiles": {"a": {"lead_order": ["l"]}, "b": {"lead_order": ["l"]}}, "default_profile": "a"}
        base.update(extra)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(base, f)
        self.addCleanup(os.unlink, f.name)
        return f.name

    def load(self, triage):
        return cfg.load_config(self.write({} if triage is None else {"triage": triage}))["triage"]

    def test_absent_is_off_everywhere(self):
        t = self.load(None)
        self.assertEqual(t["mode"], {"a": "off", "b": "off"})
        self.assertIsNone(t["model"])

    def test_modes_merge_and_thresholds_fill_in(self):
        t = self.load({"mode": {"a": "acting"}, "model": "m", "thresholds": {"high_stakes": 0.1}})
        self.assertEqual(t["mode"], {"a": "acting", "b": "off"})
        self.assertEqual(t["thresholds"]["high_stakes"], 0.1)
        self.assertEqual(t["thresholds"]["strong_case"], 0.6)
        self.assertEqual(t["key_env"], "OPENROUTER_API_KEY")

    def test_improve_panel_logs(self):
        self.assertEqual(cfg.load_config(self.write({"improve": {"panel_logs": ["/a/b.jsonl"]}}))["improve"],
                         {"panel_logs": ["/a/b.jsonl"]})
        for bad in ({"improve": {"panel_logs": "x"}}, {"improve": {"nope": 1}}):
            with self.assertRaises(cfg.ConfigError):
                cfg.load_config(self.write(bad))

    def test_the_two_threshold_tables_agree(self):
        sys.path.insert(0, str(HERE.parent))
        from lead import triage as tr
        self.assertEqual(tr.DEFAULT_THRESHOLDS, cfg.TRIAGE_THRESHOLDS)

    def test_bad_input_is_refused(self):
        for bad, needle in (({"mode": {"a": "acting"}}, "triage.model"), ({"mode": {"z": "off"}}, "unknown profile"),
                            ({"mode": {"a": "on"}, "model": "m"}, "must be one of"), ({"nope": 1}, "unknown key"),
                            ({"thresholds": {"x": 1}}, "unknown key"), ({"thresholds": {"high_stakes": 2}}, "0 to 1"),
                            ({"thresholds": {"high_stakes": True}}, "0 to 1"), ({"key_env": ""}, "key_env")):
            with self.assertRaises(cfg.ConfigError) as c:
                self.load(bad)
            self.assertIn(needle, str(c.exception))


if __name__ == "__main__":
    unittest.main()
