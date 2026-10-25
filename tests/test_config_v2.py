import copy, json, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402

V1 = HERE / "fixtures" / "config.json"
V2 = HERE / "fixtures" / "config_v2.json"


def v2():
    with open(V2) as f:
        return json.load(f)


def load_obj(obj):
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "config.json"
        p.write_text(json.dumps(obj))
        return cfg.load_config(str(p))


class SchemaV2(unittest.TestCase):
    def rejects(self, obj, needle):
        with self.assertRaises(cfg.ConfigError) as e:
            load_obj(obj)
        self.assertIn(needle, str(e.exception))

    def test_v1_loads_with_empty_default_profile(self):
        c = cfg.load_config(str(V1))
        self.assertEqual(c["default_profile"], "default")
        self.assertEqual(c["profiles"]["default"]["lead_order"], [])
        self.assertEqual(c["leads"], {})
        self.assertEqual(c["plans"], {})

    def test_v2_fixture_loads(self):
        c = cfg.load_config(str(V2))
        self.assertEqual(c["schema_version"], 2)
        self.assertEqual(c["profiles"]["careful"]["lead_order"], ["lead-a", "lead-b"])
        self.assertEqual(c["profiles"]["careful"]["checkpoints"]["review"]["min_seats"], 2)
        self.assertEqual(c["profiles"]["quick"]["checkpoints"]["review"]["required"], [])
        self.assertEqual(c["seats"]["beta"]["fallback"], "alpha")
        self.assertEqual(c["seats"]["beta"]["plan"], "metered")
        self.assertEqual(c["projects"], {"/work/projects/app": "careful"})

    def test_example_config_loads(self):
        c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
        self.assertEqual(c["schema_version"], 2)
        self.assertTrue(c["profiles"])

    def test_rotation_defaults_and_override(self):
        c = cfg.load_config(str(V2))
        self.assertEqual(c["rotation"], {"return_above_pct": 60, "cap_expiry_hours": 5, "probe_every_min": 15})
        self.assertEqual(c["updates"], {"min_interval_hours": 0})

    def test_unknown_plan_billing_rejected(self):
        o = v2(); o["plans"]["plan-a"]["billing"] = "barter"
        self.rejects(o, "plans.plan-a.billing")

    def test_budget_only_on_metered(self):
        o = v2(); o["plans"]["plan-a"]["budget_usd_month"] = 5
        self.rejects(o, "plans.plan-a.budget_usd_month")

    def test_lead_unknown_plan_rejected(self):
        o = v2(); o["leads"]["lead-a"]["plan"] = "nope"
        self.rejects(o, "leads.lead-a.plan")

    def test_lead_unknown_runtime_rejected(self):
        o = v2(); o["leads"]["lead-a"]["runtime"] = "claude"
        self.rejects(o, "leads.lead-a.runtime")

    def test_profile_lead_order_unknown_lead_rejected(self):
        o = v2(); o["profiles"]["careful"]["lead_order"].append("ghost")
        self.rejects(o, "profiles.careful.lead_order")

    def test_checkpoint_required_not_in_seats_rejected(self):
        o = v2(); o["profiles"]["careful"]["checkpoints"]["review"]["required"] = ["delta"]
        self.rejects(o, "profiles.careful.checkpoints.review.required")

    def test_checkpoint_unknown_seat_rejected(self):
        o = v2(); o["profiles"]["quick"]["checkpoints"]["review"]["seats"].append("ghost")
        self.rejects(o, "profiles.quick.checkpoints.review.seats")

    def test_min_seats_exceeds_seats_rejected(self):
        o = v2(); o["profiles"]["careful"]["checkpoints"]["review"]["min_seats"] = 9
        self.rejects(o, "profiles.careful.checkpoints.review.min_seats")

    def test_seat_fallback_must_name_a_seat(self):
        o = v2(); o["seats"]["beta"]["fallback"] = "ghost"
        self.rejects(o, "seats.beta.fallback")

    def test_seat_plan_must_name_a_plan(self):
        o = v2(); o["seats"]["beta"]["plan"] = "ghost"
        self.rejects(o, "seats.beta.plan")

    def test_default_profile_must_exist(self):
        o = v2(); o["default_profile"] = "ghost"
        self.rejects(o, "default_profile")

    def test_project_profile_must_exist(self):
        o = v2(); o["projects"]["/x"] = "ghost"
        self.rejects(o, "projects./x")

    def test_tool_update_must_be_argv(self):
        o = v2(); o["tools"]["claude"]["update"] = "claude update"
        self.rejects(o, "tools.claude.update")

    def test_v2_keys_rejected_in_v1_file(self):
        o = json.loads(V1.read_text()); o["leads"] = {}
        self.rejects(o, "leads")


if __name__ == "__main__":
    unittest.main()
