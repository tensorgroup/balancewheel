import os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lead.handoff import build_handoff, checkpoint_dir, first_prompt, protocol_text  # noqa: E402

GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True, env=GITENV).stdout.strip()


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "app"; self.repo.mkdir()
        git(self.repo, "init", "-q"); (self.repo / "a").write_text("1"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "init")
        self.base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "b").write_text("2"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "add the b feature")
        (self.repo / "dirty").write_text("x")
        self.state = root / "state"
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_handoff_has_branch_commits_status_and_checkpoint(self):
        d = Path(checkpoint_dir(str(self.state), "k1")); d.mkdir(parents=True)
        (d / "checkpoint.md").write_text("@next checkpoint\ngoal: ship b\nnext: tests")
        h = build_handoff(str(self.repo), self.base, str(self.state), "k1", ["@human ask: which db?"])
        self.assertIn("@next handoff", h)
        self.assertIn(git(self.repo, "branch", "--show-current"), h)
        self.assertIn("add the b feature", h)
        self.assertIn("dirty", h)
        self.assertIn("goal: ship b", h)
        self.assertIn("which db?", h)

    def test_missing_checkpoint(self):
        h = build_handoff(str(self.repo), self.base, str(self.state), "k2", [])
        self.assertIn("checkpoint: none", h)

    def test_planted_hook_and_fsmonitor_do_not_run(self):
        marker = self.root / "pwned"
        hook = self.repo / ".git" / "hooks" / "post-checkout"
        script = f"#!/bin/sh\ntouch {marker}\n"
        for name in ("post-checkout", "post-index-change", "reference-transaction", "fsmonitor-watchman"):
            p = self.repo / ".git" / "hooks" / name
            p.write_text(script); p.chmod(0o755)
        git(self.repo, "config", "core.fsmonitor", str(hook))
        build_handoff(str(self.repo), self.base, str(self.state), "k3", [])
        self.assertFalse(marker.exists(), "a planted hook ran while building the handoff")

    def test_worktree_attribute_filters_do_not_run(self):
        marker = self.root / "pwned-filter"
        git(self.repo, "config", "filter.x.clean", f"sh -c 'touch {marker}; cat'")
        (self.repo / ".gitattributes").write_text("* filter=x\n")
        (self.repo / "b").write_text("changed")
        build_handoff(str(self.repo), self.base, str(self.state), "k4", [])
        self.assertFalse(marker.exists(), "a clean filter ran while building the handoff")

    def test_symlinked_checkpoint_not_followed(self):
        secret = self.root / "secret"; secret.write_text("TOKEN-123")
        d = Path(checkpoint_dir(str(self.state), "k5")); d.mkdir(parents=True)
        (d / "checkpoint.md").symlink_to(secret)
        h = build_handoff(str(self.repo), self.base, str(self.state), "k5", [])
        self.assertNotIn("TOKEN-123", h)

    def test_first_prompt_starts_with_protocol(self):
        p = first_prompt("@next handoff\nbranch: x", protocol_text())
        self.assertTrue(p.startswith(protocol_text()))
        self.assertIn("@next handoff", p)
        self.assertIn("checkpoint", protocol_text())


if __name__ == "__main__":
    unittest.main()


class HostileHandoffInput(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(os.path.realpath(self.tmp.name)) / "state"

    def tearDown(self):
        self.tmp.cleanup()

    def test_fifo_checkpoint_returns_at_once(self):
        cp = Path(checkpoint_dir(str(self.state), "k"))
        cp.mkdir(parents=True)
        os.mkfifo(cp / "checkpoint.md")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from lead.handoff import read_checkpoint; "
                "print(repr(read_checkpoint(sys.argv[2], 'k')))")
        try:
            r = subprocess.run([sys.executable, "-c", code, str(HERE.parent), str(self.state)],
                               capture_output=True, text=True, timeout=1)
        except subprocess.TimeoutExpired:
            self.fail("read_checkpoint blocked on a FIFO")
        self.assertEqual(r.stdout.strip(), "None", r.stderr)

    def test_huge_git_output_is_bounded_per_section(self):
        from unittest import mock
        from lead import handoff
        huge = "\n".join(f"?? file-{i:06d}-{'x' * 60}" for i in range(200000))
        with mock.patch.object(handoff, "_git", side_effect=lambda wt, *a: "main" if a[0] == "branch" else huge):
            text = build_handoff("/nowhere", "abc123", str(self.state), "k", [])
        self.assertLess(len(text.encode()), 64 * 1024)  # was 15 MB: E2BIG on the next lead's argv
        self.assertEqual(text.count(" more"), 3, text[-400:])
        self.assertIn(f"… {200000 - handoff.SECTION_LINES} more", text)

    def test_one_overlong_line_is_counted_not_kept(self):
        from lead import handoff
        self.assertEqual(handoff._bounded("y" * (handoff.SECTION_BYTES + 1)), "… 1 more")
        self.assertEqual(handoff._bounded("a\nb"), "a\nb")
