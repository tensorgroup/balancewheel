import json, sys, unittest
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
            self.assertEqual(g["on_judge_failure"], "ask")
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


if __name__ == "__main__":
    unittest.main()
