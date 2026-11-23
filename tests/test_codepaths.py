import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class CodePaths(unittest.TestCase):
    def test_lists_live_in_codepaths(self):
        from lead import codepaths, runtimes
        self.assertIs(runtimes.PI_CODE, codepaths.PI_CODE)
        self.assertIs(runtimes.KIMI_CODE, codepaths.KIMI_CODE)
        self.assertIn("extensions", codepaths.PI_CODE)
        self.assertEqual({n for n in dir(codepaths) if n.isupper()}, {"PI_CODE", "KIMI_CODE"})


if __name__ == "__main__":
    unittest.main()
