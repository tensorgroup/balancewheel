import os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lead.projects import resolve_project  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    (path / "f").write_text("x\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "init")
    return path


def cfg(projects, default="d"):
    return {"projects": projects, "default_profile": default}


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_component_match_not_prefix(self):
        make_repo(self.root / "app"); other = make_repo(self.root / "app-other")
        p = resolve_project(str(other), cfg({str(self.root / "app"): "a"}))
        self.assertEqual(p.profile, "d")

    def test_longest_match_wins(self):
        repo = make_repo(self.root / "code" / "app")
        c = cfg({str(self.root / "code"): "outer", str(repo): "inner"})
        self.assertEqual(resolve_project(str(repo), c).profile, "inner")

    def test_subdirectory_resolves_to_repo(self):
        repo = make_repo(self.root / "app"); (repo / "sub").mkdir()
        p = resolve_project(str(repo / "sub"), cfg({str(repo): "a"}))
        self.assertEqual(p.profile, "a")
        self.assertEqual(p.worktree, str(repo))

    def test_linked_worktree_gets_repo_profile_and_own_key(self):
        repo = make_repo(self.root / "app")
        wt = self.root / "wt"
        git(repo, "worktree", "add", "-q", str(wt))
        c = cfg({str(repo): "a"})
        main, linked = resolve_project(str(repo), c), resolve_project(str(wt), c)
        self.assertEqual(linked.profile, "a")
        self.assertEqual(linked.main_worktree, str(repo))
        self.assertEqual(linked.common_dir, main.common_dir)
        self.assertNotEqual(linked.worktree_key, main.worktree_key)

    def test_symlinked_cwd_resolves(self):
        repo = make_repo(self.root / "app")
        link = self.root / "link"; link.symlink_to(repo)
        p = resolve_project(str(link), cfg({str(repo): "a"}))
        self.assertEqual(p.worktree, str(repo))
        self.assertEqual(p.profile, "a")

    def test_main_worktree_common_dir_absolute(self):
        repo = make_repo(self.root / "app")
        p = resolve_project(str(repo), cfg({}))
        self.assertTrue(os.path.isabs(p.common_dir))
        self.assertEqual(p.common_dir, str(repo / ".git"))

    def test_unmatched_gets_default(self):
        repo = make_repo(self.root / "app")
        self.assertEqual(resolve_project(str(repo), cfg({}, "zzz")).profile, "zzz")

    def test_not_a_repo_raises(self):
        d = self.root / "plain"; d.mkdir()
        with self.assertRaises(ValueError) as e:
            resolve_project(str(d), cfg({}))
        self.assertIn("not a git repository", str(e.exception))

    def test_bare_repo_layout_rejected(self):
        bare = self.root / "bare.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        with self.assertRaises(ValueError) as e:
            resolve_project(str(bare), cfg({}))
        self.assertIn("bare", str(e.exception))


if __name__ == "__main__":
    unittest.main()
