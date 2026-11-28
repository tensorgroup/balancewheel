import os, subprocess, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import selfupdate  # noqa: E402

GITENV = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=GITENV).stdout.strip()


def commit(repo, name, text="x\n"):
    (Path(repo) / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", name)


def make_install(root):
    """A bare origin, a writer clone that pushes to it, and the install clone bw runs from."""
    origin, writer, install = root / "origin.git", root / "writer", root / "install"
    git(root, "init", "-q", "--bare", "-b", "main", str(origin))
    git(root, "clone", "-q", str(origin), str(writer))
    git(writer, "checkout", "-q", "-b", "main")
    commit(writer, "a")
    git(writer, "push", "-q", "origin", "main")
    git(root, "clone", "-q", str(origin), str(install))
    return writer, install


class SelfUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.writer, self.install = make_install(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_current_when_nothing_new(self):
        r = selfupdate.update(str(self.install))
        self.assertEqual((r.state, r.before), ("current", r.after))

    def test_pulls_a_pushed_commit(self):
        before = git(self.install, "rev-parse", "--short", "HEAD")
        commit(self.writer, "b")
        git(self.writer, "push", "-q", "origin", "main")
        r = selfupdate.update(str(self.install))
        self.assertEqual(r.state, "updated")
        self.assertEqual(r.before, before)
        self.assertEqual(r.after, git(self.install, "rev-parse", "--short", "HEAD"))
        self.assertTrue((self.install / "b").exists())

    def test_local_changes_are_never_touched(self):
        commit(self.writer, "b")
        git(self.writer, "push", "-q", "origin", "main")
        (self.install / "a").write_text("edited\n")
        r = selfupdate.update(str(self.install))
        self.assertEqual(r.state, "skipped")
        self.assertIn("local changes", r.detail)
        self.assertEqual((self.install / "a").read_text(), "edited\n")
        self.assertFalse((self.install / "b").exists())

    def test_untracked_files_do_not_block(self):
        (self.install / "scratch.txt").write_text("x")
        self.assertEqual(selfupdate.update(str(self.install)).state, "current")

    def test_no_upstream_is_skipped(self):
        git(self.install, "checkout", "-q", "-b", "local-only")
        r = selfupdate.update(str(self.install))
        self.assertEqual(r.state, "skipped")
        self.assertIn("no upstream", r.detail)

    def test_diverged_history_fails_and_changes_nothing(self):
        commit(self.writer, "b")
        git(self.writer, "push", "-q", "origin", "main")
        commit(self.install, "c")
        head = git(self.install, "rev-parse", "HEAD")
        r = selfupdate.update(str(self.install))
        self.assertEqual(r.state, "failed")
        self.assertEqual(git(self.install, "rev-parse", "HEAD"), head)

    def test_diverged_says_gits_error_not_its_hint(self):
        commit(self.writer, "b")
        git(self.writer, "push", "-q", "origin", "main")
        commit(self.install, "c")
        r = selfupdate.update(str(self.install))
        self.assertTrue(r.detail.startswith("fatal:"), r.detail)

    def test_detached_head_says_so(self):
        git(self.install, "checkout", "-q", "--detach")
        r = selfupdate.update(str(self.install))
        self.assertEqual(r.state, "skipped")
        self.assertIn("detached HEAD", r.detail)

    def test_git_variables_from_the_shell_never_reach_git(self):
        other = self.root / "other"
        git(self.root, "init", "-q", str(other))
        commit(other, "o")
        commit(self.writer, "b")
        git(self.writer, "push", "-q", "origin", "main")
        environ = {**os.environ, "GIT_DIR": str(other / ".git"), "GIT_WORK_TREE": str(other)}
        r = selfupdate.update(str(self.install), environ=environ)
        self.assertEqual(r.state, "updated")
        self.assertTrue((self.install / "b").exists())
        env = selfupdate.git_env(environ)
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_an_index_lock_says_how_to_clear_it(self):
        from lead.proc import Result
        r = selfupdate._reason(Result(128, "", "hint: x\nfatal: Unable to create '/i/.git/index.lock': File exists.", False))
        self.assertTrue(r.startswith("fatal: Unable to create"))
        self.assertIn("delete that file", r)

    def test_not_a_checkout(self):
        plain = self.root / "plain"; plain.mkdir()
        self.assertEqual(selfupdate.update(str(plain)).state, "skipped")


if __name__ == "__main__":
    unittest.main()
