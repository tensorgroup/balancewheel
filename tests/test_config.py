import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402

FIXTURE = HERE / "fixtures" / "config.json"


def write(tmp, obj):
    p = Path(tmp) / "config.json"
    p.write_text(json.dumps(obj))
    return str(p)


def load_fixture():
    with open(FIXTURE) as f:
        return json.load(f)


class LoadConfig(unittest.TestCase):
    def test_fixture_loads_with_defaults_and_absolute_paths(self):
        c = cfg.load_config(str(FIXTURE))
        self.assertEqual(c["schema_version"], 1)
        self.assertTrue(os.path.isabs(c["state_dir"]))
        self.assertEqual(c["metrics_log"], os.path.join(c["state_dir"], "metrics.jsonl"))
        self.assertEqual(c["watches"]["bloat_total"], 30)
        self.assertEqual(c["watches"]["redundant_window_panels"], 10)
        self.assertTrue(c["seats"]["alpha"]["enabled"])
        self.assertEqual(c["seats"]["alpha"]["roles"], ["review"])
        self.assertEqual(c["seats"]["alpha"]["args"], [])

    def test_env_var_overrides_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write(tmp, load_fixture())
            os.environ["BALANCEWHEEL_CONFIG"] = p
            try:
                self.assertEqual(cfg.config_path(), p)
            finally:
                del os.environ["BALANCEWHEEL_CONFIG"]

    def test_unknown_top_level_key_names_key_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture(); obj["metric_log"] = "x"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("metric_log", str(e.exception))

    def test_unknown_seat_key_names_key_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture(); obj["seats"]["alpha"]["efort"] = "high"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.efort", str(e.exception))

    def test_unknown_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture(); obj["seats"]["alpha"]["runtime"] = "mystery"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.runtime", str(e.exception))

    def test_effort_rejected_for_agy_and_claude_code(self):
        for rt in ("agy", "claude-code"):
            with tempfile.TemporaryDirectory() as tmp:
                obj = load_fixture()
                obj["seats"]["alpha"].update({"runtime": rt, "effort": "high"})
                with self.assertRaises(cfg.ConfigError) as e:
                    cfg.load_config(write(tmp, obj))
                self.assertIn("seats.alpha.effort", str(e.exception))

    def test_effort_level_validated_per_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture()
            obj["seats"]["alpha"].update({"runtime": "pi", "effort": "max"})
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("xhigh", str(e.exception))

    def test_bad_seat_name_and_bad_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture(); obj["seats"]["Bad Name"] = obj["seats"]["alpha"]
            with self.assertRaises(cfg.ConfigError):
                cfg.load_config(write(tmp, obj))
        with tempfile.TemporaryDirectory() as tmp:
            obj = load_fixture(); obj["seats"]["alpha"]["roles"] = ["review", "qa"]
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.roles", str(e.exception))

    def test_types_are_checked_not_coerced(self):
        for patch, key in (({"schema_version": True}, "schema_version"), ({"state_dir": None}, "state_dir"),
                           ({"watches": {"bloat_total": True}}, "watches.bloat_total"),
                           ({"watches": {"redundant_min_panels": 0}}, "redundant_min_panels")):
            with tempfile.TemporaryDirectory() as tmp:
                obj = load_fixture(); obj.update(patch)
                with self.assertRaises(cfg.ConfigError) as e:
                    cfg.load_config(write(tmp, obj))
                self.assertIn(key, str(e.exception))

    def test_json_syntax_error_reports_line_and_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"; p.write_text('{"seats": {,}}')
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(str(p))
            self.assertRegex(str(e.exception), r"line \d+ column \d+")

    def test_example_config_is_valid(self):
        c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
        self.assertGreaterEqual(len(c["seats"]), 3)


if __name__ == "__main__":
    unittest.main()
