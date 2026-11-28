"""The live pane plugin (`claude/live-pane/`): versions agree, no model ids, and the plugin's own tests.

The last test runs `claude plugin validate` and `claude plugin test` only when `BW_PLUGIN_TESTS=1`: the real
binary reads the user's Claude config and writes `.claude-plugin/types/` into the tree, so the default suite
stays hermetic (F13).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import version  # noqa: E402

PLUGIN = ROOT / "claude" / "live-pane"


class LivePanePlugin(unittest.TestCase):
    def test_versions_agree(self):
        self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"], version.VERSION)
        m = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(m["name"], "balancewheel")
        self.assertEqual([(p["name"], p["source"], p["version"]) for p in m["plugins"]],
                         [("live-pane", "./claude/live-pane", version.VERSION)])

    def test_no_model_ids_and_no_h_variable(self):
        text = "".join(p.read_text() for p in PLUGIN.rglob("*.ts*") if ".claude-plugin" not in p.parts)
        self.assertIsNone(re.search(r"claude-(opus|sonnet|haiku|fable)-\d|gpt-\d", text))
        self.assertIsNone(re.search(r"\b(const|let|var)\s+h\b", text))

    def test_claude_plugin_validate_and_test(self):
        if os.environ.get("BW_PLUGIN_TESTS") != "1":    # the default suite stays hermetic
            self.skipTest("set BW_PLUGIN_TESTS=1 to run claude plugin validate and test")
        exe = shutil.which("claude")
        if not exe:
            self.skipTest("claude is not on PATH: the plugin's own tests did not run")
        for argv in ([exe, "plugin", "validate", str(PLUGIN)], [exe, "plugin", "validate", str(ROOT)],
                     [exe, "plugin", "test", str(PLUGIN)]):
            r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
