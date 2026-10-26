"""Bare `bw` does the everyday thing: lead in a worktree, status outside one."""
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import inbox  # noqa: E402
from lead.projects import worktree_key  # noqa: E402


def load_bw():
    loader = importlib.machinery.SourceFileLoader("bw_cli_default", str(ROOT / "bin" / "bw"))
    spec = importlib.util.spec_from_loader("bw_cli_default", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.state = str(self.root / "state")
        self.repo = self.root / "app"; self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        self.outside = self.root / "plain"; self.outside.mkdir()
        cfg = {"schema_version": 2, "plans": {"pa": {"billing": "plan", "quota": "none"}},
               "leads": {"la": {"runtime": "codex", "model": "a", "plan": "pa"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}},
               "profiles": {"p": {"lead_order": ["la"]}}, "default_profile": "p",
               "state_dir": self.state, "metrics_log": str(self.root / "metrics.jsonl")}
        self.cfg = self.root / "config.json"; self.cfg.write_text(json.dumps(cfg))
        self.env = mock.patch.dict(os.environ, {
            "BALANCEWHEEL_CONFIG": str(self.cfg), "BW_STATE_DIR": self.state,
            "BW_ORCA_SUPPORT": str(self.root / "no-orca"), "BW_CCSWITCH_DIR": str(self.root / "no-cc")})
        self.env.start()
        self.cwd = os.getcwd()
        self.bw = load_bw()

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def main(self, argv, cwd):
        os.chdir(cwd)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.bw.main(argv)
        return code, out.getvalue(), err.getvalue()


class DefaultCommand(Case):
    def test_bare_bw_in_a_worktree_leads(self):
        with mock.patch.object(self.bw, "cmd_lead", return_value=0) as lead:
            code, _, _ = self.main([], self.repo)
        self.assertEqual(code, 0)
        a = lead.call_args[0][0]
        self.assertEqual((a.cmd, a.use, a.auto, a.no_rating), ("lead", None, False, False))

    def test_lead_flags_work_without_the_word_lead(self):
        with mock.patch.object(self.bw, "cmd_lead", return_value=0) as lead:
            self.main(["--use", "la", "--no-rating"], self.repo)
        a = lead.call_args[0][0]
        self.assertEqual((a.cmd, a.use, a.no_rating), ("lead", "la", True))

    def test_subcommands_and_help_are_unchanged(self):
        self.assertEqual(self.bw.default_argv(["status"], str(self.repo)), (["status"], False))
        self.assertEqual(self.bw.default_argv(["-h"], str(self.repo)), (["-h"], False))
        self.assertEqual(self.bw.default_argv(["--help"], str(self.outside)), (["--help"], False))

    def test_bare_bw_outside_a_worktree_shows_status_and_a_hint(self):
        with mock.patch.object(self.bw, "cmd_lead") as lead:
            code, out, err = self.main([], self.outside)
        lead.assert_not_called()
        self.assertEqual(code, 0)
        self.assertIn("plans:", out)
        self.assertIn("not in a git worktree", err)
        self.assertIn("bw --help", err)

    def test_every_documented_subcommand_is_known(self):
        import re
        documented = set(re.findall(r"^  bw ([a-z-]+)", self.bw.__doc__, re.M))
        self.assertEqual(documented, set(self.bw.COMMANDS))

    def test_lead_flags_outside_a_worktree_still_mean_lead(self):
        self.assertEqual(self.bw.default_argv(["--auto"], str(self.outside)), (["lead", "--auto"], False))


class LeadNudges(Case):
    def key(self):
        return worktree_key(str(self.repo))

    def test_open_items_for_this_worktree_are_mentioned(self):
        inbox.ask(self.state, self.key(), str(self.repo), "la", "s1", "which DB?", "a1b2c3d4")
        inbox.ask(self.state, "otherkey0000", "/elsewhere", "la", "s2", "not mine", "e5f6a7b8")
        lines = self.bw.lead_nudges(self.state, str(self.repo), time.time())
        self.assertEqual(len(lines), 1)
        self.assertIn("1 open item", lines[0])
        self.assertIn("bw inbox --project", lines[0])

    def test_stale_start_suggests_bw_start(self):
        os.makedirs(self.state, exist_ok=True)
        Path(self.state, "start.json").write_text(json.dumps({"ts": time.time() - 2 * 86400}))
        lines = self.bw.lead_nudges(self.state, str(self.repo), time.time())
        self.assertTrue(any("bw start" in line for line in lines), lines)

    def test_no_nudges_when_fresh_and_empty(self):
        os.makedirs(self.state, exist_ok=True)
        Path(self.state, "start.json").write_text(json.dumps({"ts": time.time() - 3600}))
        self.assertEqual(self.bw.lead_nudges(self.state, str(self.repo), time.time()), [])
        Path(self.state, "start.json").unlink()  # never ran bw start: no nag
        self.assertEqual(self.bw.lead_nudges(self.state, str(self.repo), time.time()), [])

    def test_nudges_never_raise(self):
        os.makedirs(os.path.join(self.state, "inbox.jsonl"))  # unreadable inbox
        Path(self.state, "start.json").write_text("not json")
        self.assertEqual(self.bw.lead_nudges(self.state, str(self.repo), time.time()), [])

    def test_cmd_lead_prints_nudges_before_leading(self):
        inbox.ask(self.state, self.key(), str(self.repo), "la", "s1", "which DB?", "a1b2c3d4")
        with mock.patch.object(self.bw.runner, "run", return_value=0) as run:
            code, out, err = self.main([], self.repo)
        self.assertEqual(code, 0)
        run.assert_called_once()
        self.assertIn("1 open item", err)


if __name__ == "__main__":
    unittest.main()
