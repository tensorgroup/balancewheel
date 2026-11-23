import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CodePaths(unittest.TestCase):
    def test_lists_live_in_codepaths(self):
        from lead import codepaths, runtimes
        self.assertIs(runtimes.PI_CODE, codepaths.PI_CODE)
        self.assertIs(runtimes.KIMI_CODE, codepaths.KIMI_CODE)
        self.assertIn("extensions", codepaths.PI_CODE)
        self.assertIn("APPEND_SYSTEM.md", codepaths.PI_CODE)  # pi appends it to the system prompt
        self.assertEqual({n for n in dir(codepaths) if n.isupper()}, {"PI_CODE", "KIMI_CODE"})

    def test_codex_override_instructions_are_code(self):
        from seats import adapters
        self.assertIn("AGENTS.override.md", adapters.CODEX_CODE)  # codex reads it before AGENTS.md


if __name__ == "__main__":
    unittest.main()
