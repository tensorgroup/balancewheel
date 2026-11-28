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
            ("pi", "guard", ["pi", "--model", "m1", "--no-extensions", "-e", "/c/pi_guard.ts", "--x", "hi"]),
            ("pi", "bypass", ["pi", "--model", "m1", "--x", "hi"]),
            ("kimi", "native", ["kimi", "-m", "m1", "--yolo", "--x"]),
            ("kimi", "bypass", ["kimi", "-m", "m1", "--auto", "--x"]),
            ("kimi", "manual", ["kimi", "-m", "m1", "--x"]),
        ]
        for rt, mode, want in cases:
            self.assertEqual(A[rt].argv(lead(rt, mode), "hi", [], client_dir="/c"), want, (rt, mode))

    def test_legacy_argv_is_todays_byte_for_byte(self):
        want = {
            "claude-code": ["claude", "--model", "m1", "--add-dir", "/cp", "--x", "--", "hi"],
            "codex": ["codex", "-m", "m1", "--sandbox", "workspace-write", "--add-dir", "/cp", "--x", "hi"],
            "agy": ["agy", "--model", "m1", "--sandbox", "--add-dir", "/cp", "--x", "-i", "hi"],
            "pi": ["pi", "--model", "m1", "--x", "hi"],
            "kimi": ["kimi", "-m", "m1", "--x"],
        }
        for rt, argv in want.items():
            ld = lead(rt, "native", "legacy")
            self.assertEqual(A[rt].argv(ld, "hi", ["/cp"]), argv, rt)
            self.assertEqual(A[rt].argv(lead(rt), "hi", ["/cp"]), argv, rt)  # no autonomy key at all

    def test_claude_code_gets_add_dir_for_each_writable_dir(self):
        # the Write tool could not reach the checkpoint dir without it
        argv = A["claude-code"].argv(lead("claude-code", "native"), "hi", ["/cp", "/cp2"])
        self.assertEqual(argv, ["claude", "--model", "m1", "--add-dir", "/cp", "--add-dir", "/cp2",
                                "--permission-mode", "auto", "--x", "--", "hi"])

    def test_claude_code_without_writable_dirs_has_no_add_dir_and_no_separator(self):
        argv = A["claude-code"].argv(lead("claude-code", "native"), "hi", [])
        self.assertEqual(argv, ["claude", "--model", "m1", "--permission-mode", "auto", "--x", "hi"])

    def test_claude_code_prompt_follows_a_separator_so_add_dir_cannot_take_it(self):
        # --add-dir takes a list of dirs; a bare prompt after it would be read as one more dir
        argv = A["claude-code"].argv(lead("claude-code"), "hi", ["/cp"])
        self.assertEqual(argv[-2:], ["--", "hi"])
        self.assertEqual(A["claude-code"].argv(lead("claude-code"), None, ["/cp"])[-1], "--x")

    def test_only_claude_code_takes_the_protocol_as_a_system_prompt(self):
        self.assertEqual({rt for rt, ad in A.items() if ad.system_protocol}, {"claude-code"})

    def test_claude_code_system_prompt_is_appended_and_the_prompt_is_only_the_handoff(self):
        ad = A["claude-code"]
        argv = ad.argv(lead("claude-code", "native"), "@next handoff", ["/cp"], system_prompt="RULES\nline 2")
        self.assertEqual(argv, ["claude", "--model", "m1", "--add-dir", "/cp", "--permission-mode", "auto",
                                "--append-system-prompt", "RULES\nline 2", "--x", "--", "@next handoff"])
        # no handoff: no prompt at all, so the session opens with nothing typed
        argv = ad.argv(lead("claude-code", "native"), None, ["/cp"], system_prompt="RULES")
        self.assertEqual(argv[-3:], ["--append-system-prompt", "RULES", "--x"])
        self.assertNotIn("--", argv)
        self.assertEqual(ad.argv(lead("claude-code"), "hi", []), ["claude", "--model", "m1", "--x", "hi"])  # unchanged

    def test_a_users_own_append_system_prompt_is_merged_not_overridden(self):
        ad = A["claude-code"]
        for user_args in (["--append-system-prompt", "mine", "--x"], ["--x", "--append-system-prompt=mine"]):
            ld = lead("claude-code", "native", args=user_args)
            argv = ad.argv(ld, "hi", ["/cp"], system_prompt="RULES")
            self.assertEqual(argv.count("--append-system-prompt"), 1, argv)
            self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "RULES\n\nmine")
            self.assertNotIn("mine", [a for i, a in enumerate(argv) if argv[i - 1] != "--append-system-prompt"])
            self.assertIn("--x", argv)
            self.assertFalse([a for a in argv if a.startswith("--append-system-prompt=")])
        two = ad.argv(lead("claude-code", args=["--append-system-prompt", "a", "--append-system-prompt", "b"]),
                      None, [], system_prompt="RULES")
        self.assertEqual(two, ["claude", "--model", "m1", "--append-system-prompt", "RULES\n\na\n\nb"])
        # no bw protocol: the user's args are left exactly as they are
        keep = ad.argv(lead("claude-code", args=["--append-system-prompt", "mine"]), "hi", [])
        self.assertEqual(keep, ["claude", "--model", "m1", "--append-system-prompt", "mine", "hi"])

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
        self.assertEqual(pi, ["pi", "-p", "--mode", "json", "--model", "m1", "--no-extensions", "-e", "/c/pi_guard.ts"])
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
