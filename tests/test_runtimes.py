import os, sys, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import runtimes  # noqa: E402
from lead.paths import ROOT  # noqa: E402  (sets up metrics/ on sys.path)
import config as cfg  # noqa: E402

A = runtimes.ADAPTERS


def lead(rt, autonomy=None, source="config", args=("--x",)):
    d = {"runtime": rt, "model": "m1", "plan": "p", "args": list(args)}
    if autonomy:
        d["autonomy"], d["autonomy_source"] = autonomy, source
    return d


class Flags(unittest.TestCase):
    def test_keys_match_supported_autonomy(self):
        self.assertEqual({k: set(v) for k, v in runtimes.AUTONOMY_FLAGS.items()}, cfg.SUPPORTED_AUTONOMY)

    def test_flags_per_runtime_per_autonomy(self):
        for rt, table in runtimes.AUTONOMY_FLAGS.items():
            for mode, flags in table.items():
                got = runtimes.autonomy_flags(rt, lead(rt, mode), "/c")
                self.assertEqual(got, [f.replace("{client}", "/c/pi_guard.ts") for f in flags], (rt, mode))

    def test_legacy_and_custom_add_nothing(self):
        self.assertEqual(runtimes.autonomy_flags("claude-code", lead("claude-code", "native", "legacy"), None), [])
        self.assertEqual(runtimes.autonomy_flags("pi", lead("pi", "custom", "legacy"), None), [])
        self.assertEqual(runtimes.autonomy_flags("codex", lead("codex"), None), [])

    def test_exact_argv_in_ruleset(self):
        cases = [
            ("claude-code", "native", ["claude", "--model", "m1", "--permission-mode", "auto", "--x", "hi"]),
            ("claude-code", "bypass", ["claude", "--model", "m1", "--permission-mode", "bypassPermissions", "--x", "hi"]),
            ("claude-code", "manual", ["claude", "--model", "m1", "--permission-mode", "default", "--x", "hi"]),
            ("codex", "native", ["codex", "-m", "m1", "--sandbox", "workspace-write", "--approve-for-me", "--x", "hi"]),
            ("codex", "manual", ["codex", "-m", "m1", "--sandbox", "workspace-write", "--x", "hi"]),
            ("agy", "guard", ["agy", "--model", "m1", "--sandbox", "--dangerously-skip-permissions", "--x", "-i", "hi"]),
            ("agy", "manual", ["agy", "--model", "m1", "--sandbox", "--x", "-i", "hi"]),
            ("pi", "guard", ["pi", "--model", "m1", "-e", "/c/pi_guard.ts", "--x", "hi"]),
            ("pi", "bypass", ["pi", "--model", "m1", "--x", "hi"]),
            ("kimi", "native", ["kimi", "-m", "m1", "--yolo", "--x"]),
            ("kimi", "bypass", ["kimi", "-m", "m1", "--auto", "--x"]),
            ("kimi", "manual", ["kimi", "-m", "m1", "--x"]),
        ]
        for rt, mode, want in cases:
            self.assertEqual(A[rt].argv(lead(rt, mode), "hi", [], client_dir="/c"), want, (rt, mode))

    def test_legacy_argv_is_todays_byte_for_byte(self):
        want = {
            "claude-code": ["claude", "--model", "m1", "--x", "hi"],
            "codex": ["codex", "-m", "m1", "--sandbox", "workspace-write", "--add-dir", "/cp", "--x", "hi"],
            "agy": ["agy", "--model", "m1", "--sandbox", "--add-dir", "/cp", "--x", "-i", "hi"],
            "pi": ["pi", "--model", "m1", "--x", "hi"],
            "kimi": ["kimi", "-m", "m1", "--x"],
        }
        for rt, argv in want.items():
            ld = lead(rt, "native", "legacy")
            self.assertEqual(A[rt].argv(ld, "hi", ["/cp"]), argv, rt)
            self.assertEqual(A[rt].argv(lead(rt), "hi", ["/cp"]), argv, rt)  # no autonomy key at all

    def test_pi_guard_grants_include_client_dir(self):
        g = A["pi"].grants(lead("pi", "guard"), client_dir="/c")
        self.assertIn("/c", g.read)
        self.assertNotIn("/c", A["pi"].grants(lead("pi", "bypass"), client_dir="/c").read)
        self.assertEqual(A["pi"].grants(lead("pi", "bypass", "legacy")), A["pi"].grants(lead("pi", "bypass")))

    def test_guarded_pi_without_client_dir_raises(self):
        with self.assertRaisesRegex(ValueError, "guarded pi lead needs a client dir"):
            A["pi"].grants(lead("pi", "guard"))
        with self.assertRaisesRegex(ValueError, "guarded pi lead needs a client dir"):
            runtimes.autonomy_flags("pi", lead("pi", "guard"), None)
        with self.assertRaises(ValueError):
            A["pi"].argv(lead("pi", "guard"), "hi", [])

    def test_other_grants_accept_client_dir(self):
        for rt in ("claude-code", "codex", "agy", "kimi"):
            A[rt].grants(lead(rt), client_dir="/c")

    def test_guard_argv(self):
        pi = A["pi"].guard_argv(lead("pi", "guard"), "/c")
        self.assertEqual(pi, ["pi", "-p", "--mode", "json", "--model", "m1", "-e", "/c/pi_guard.ts"])
        self.assertNotIn("--no-extensions", pi)
        self.assertEqual(A["agy"].guard_argv(lead("agy", "guard"), "/c"),
                         ["agy", "-p", "--model", "m1", "--sandbox", "--dangerously-skip-permissions"])
        for rt in ("claude-code", "codex", "kimi"):
            self.assertIsNone(A[rt].guard_argv)

    def test_guard_hook_files(self):
        with mock.patch.dict(os.environ, {"BW_AGY_HOOKS": "/h/hooks.json", "BW_PI_AGENT_DIR": "/pi"}):
            self.assertEqual(A["agy"].guard_hook_files()[0], "/h/hooks.json")
            self.assertTrue(A["agy"].guard_hook_files()[1].endswith(".gemini/settings.json"))
            self.assertEqual(A["pi"].guard_hook_files(), ["/pi/settings.json", "/pi/extensions"])
        self.assertEqual(A["kimi"].guard_hook_files(), [])

    def test_adapter_defaults_keep_old_constructions_working(self):
        ad = runtimes.Adapter(argv=None, contained_by="self", grants=None, prompt_mode="arg",
                              version_argv=[], verify_argv=None, verify_flags=None)
        self.assertIsNone(ad.guard_argv)
        self.assertEqual(ad.guard_hook_files(), [])


if __name__ == "__main__":
    unittest.main()
