import os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from seats import brief  # noqa: E402

GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
ARCH_BODY = "the architecture body text"


def git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, text=True, env=GITENV).stdout.strip()


def commit_all(repo, msg="c"):
    git(repo, "add", "."); git(repo, "commit", "-qm", msg)


class Brief(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.state = root / "state"
        self.wt = root / "app"; self.wt.mkdir()
        git(self.wt, "init", "-q", "-b", "main")
        (self.wt / "AGENTS.md").write_text("\n".join(f"line {i}" for i in range(1, 101)))
        (self.wt / "README.md").write_text("readme")
        (self.wt / "docs").mkdir(); (self.wt / "docs/architecture.md").write_text(ARCH_BODY)
        (self.wt / "src").mkdir()
        for n in ("parser", "utils", "io"):
            (self.wt / f"src/{n}.py").write_text(f"# {n}\n")
        (self.wt / "src/user.py").write_text("import parser\nimport utils\nimport io\n")
        commit_all(self.wt, "init")
        git(self.wt, "checkout", "-q", "-b", "feat")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def text(self, **kw):
        return Path(brief.build(str(self.state), str(self.wt), **kw)).read_text()

    def test_sections_and_head(self):
        text = self.text()
        self.assertIn("line 80", text); self.assertNotIn("line 81", text)
        self.assertIn("docs/architecture.md", text); self.assertNotIn(ARCH_BODY, text)
        order = ["Instructions (head of AGENTS.md)", "Read next", "Base", "Diff stat", "## Diff\n", "Untracked files", "Direct callers"]
        pos = [text.index(s) for s in order]
        self.assertEqual(pos, sorted(pos))

    def test_start_with_paths(self):
        self.assertIn("## Start with\n\n- src/parser.py", self.text(paths=["src/parser.py"]))

    def test_diff_against_merge_base_includes_uncommitted(self):
        (self.wt / "committed.txt").write_text("committed-marker\n"); commit_all(self.wt)
        (self.wt / "src/io.py").write_text("# io\nunstaged-marker\n")
        text = self.text()
        self.assertIn("committed-marker", text); self.assertIn("unstaged-marker", text)

    def test_untracked_listed(self):
        (self.wt / "fresh.txt").write_text("x")
        self.assertIn("fresh.txt", self.text().split("## Untracked files")[1])

    def test_callers_skip_generic_and_short(self):
        for n in ("parser", "utils", "io"):
            (self.wt / f"src/{n}.py").write_text(f"# {n} changed\n")
        callers = self.text().split("## Direct callers")[1]
        self.assertIn("- parser: src/user.py", callers)
        self.assertNotIn("- utils", callers); self.assertNotIn("- io", callers)

    def test_cache_hit_and_edit_rebuilds(self):
        a = brief.build(str(self.state), str(self.wt)); b = brief.build(str(self.state), str(self.wt))
        self.assertEqual(a, b)
        (self.wt / "src/parser.py").write_text("changed")
        self.assertNotEqual(brief.build(str(self.state), str(self.wt)), a)

    def test_moved_base_rebuilds(self):
        a = brief.build(str(self.state), str(self.wt))
        git(self.wt, "checkout", "-q", "main"); (self.wt / "m.txt").write_text("m"); commit_all(self.wt)
        git(self.wt, "checkout", "-q", "feat"); git(self.wt, "merge", "-q", "--no-edit", "main")
        self.assertNotEqual(brief.build(str(self.state), str(self.wt)), a)

    def test_no_base(self):
        trunk = self.root / "trunk"; trunk.mkdir()
        git(trunk, "init", "-q", "-b", "trunk"); (trunk / "a").write_text("1"); commit_all(trunk)
        self.assertIsNone(brief.resolve_base(str(trunk)))
        text = Path(brief.build(str(self.state), str(trunk))).read_text()
        self.assertIn("base unknown; diff against HEAD", text)

    def test_unborn_and_detached(self):
        empty = self.root / "empty"; empty.mkdir(); git(empty, "init", "-q", "-b", "main")
        (empty / "u.txt").write_text("x")
        self.assertIn("u.txt", Path(brief.build(str(self.state), str(empty))).read_text())
        git(self.wt, "checkout", "-q", "--detach")
        self.assertIn("Base", self.text())

    def test_textconv_and_ext_diff_not_run(self):
        marker = self.root / "marker"
        (self.wt / ".gitattributes").write_text("*.py diff=evil\n")
        git(self.wt, "config", "diff.evil.textconv", f"touch {marker} #")
        git(self.wt, "config", "diff.external", f"touch {marker} #")
        (self.wt / "src/parser.py").write_text("changed\n")
        self.text()
        self.assertFalse(marker.exists())

    def test_git_env_scrubbed(self):
        with mock.patch.dict(os.environ, {"GIT_DIR": "/nonexistent"}):
            brief.build(str(self.state), str(self.wt))

    def test_symlinked_instruction_file_skipped(self):
        (self.wt / "AGENTS.md").unlink()
        secret = self.root / "secret.txt"; secret.write_text("secret-text")
        (self.wt / "AGENTS.md").symlink_to(secret)
        (self.wt / "CLAUDE.md").write_text("claude-text")
        text = self.text()
        self.assertNotIn("secret-text", text); self.assertIn("claude-text", text)

    def test_diff_capped(self):
        (self.wt / "big.txt").write_text("".join(f"row {i}\n" for i in range(4000)))
        git(self.wt, "add", "-N", "big.txt")
        self.assertIn("cut at 3000 lines", self.text())

    def test_paths_outside_refused(self):
        (self.wt / "link-out").symlink_to(self.root)
        for p in ("../x", "/etc/hosts", "link-out/f"):
            self.assertRaises(ValueError, brief.safe_paths, str(self.wt), [p])
        self.assertEqual(brief.safe_paths(str(self.wt), ["src/parser.py"]), ["src/parser.py"])

    def test_clean_filter_in_info_attributes_not_run(self):
        marker = self.root / "marker"
        with open(self.wt / ".git/info/attributes", "a") as f:
            f.write("*.py filter=evil\n")
        for part in ("clean", "smudge", "process"):
            git(self.wt, "config", f"filter.evil.{part}", f"touch {marker} #")
        (self.wt / "src/parser.py").write_text("changed\n")
        self.text()
        self.assertFalse(marker.exists())

    def test_fifo_instruction_file_does_not_hang(self):
        (self.wt / "AGENTS.md").unlink()
        os.mkfifo(self.wt / "AGENTS.md")
        (self.wt / "CLAUDE.md").write_text("claude-text")
        self.assertIn("claude-text", self.text())

    def test_non_utf8_diff_builds(self):
        (self.wt / "src/io.py").write_bytes(b"caf\xe9\n")
        self.assertIn("## Diff", self.text())

    def test_failed_git_is_not_cached(self):
        real = brief._git_rc
        def flaky(target, *args):
            return (1, "", "boom") if args[0] == "diff" else real(target, *args)
        with mock.patch.object(brief, "_git_rc", flaky):
            path = brief.build(str(self.state), str(self.wt))
        text = Path(path).read_text()
        self.assertIn("(git diff failed: boom)", text)
        self.assertNotIn("(no changes)", text.split("## Diff\n")[1].split("##")[0])
        good = brief.build(str(self.state), str(self.wt))
        self.assertNotEqual(good, path)
        self.assertNotIn("git diff failed", Path(good).read_text())

    def test_non_ascii_names_unquoted(self):
        (self.wt / "caf\u00e9.txt").write_text("x")
        self.assertIn("caf\u00e9.txt", self.text().split("## Untracked files")[1])

    def test_paths_with_control_chars_or_dot_refused(self):
        for p in ("a\nb", "a\x01b", "."):
            self.assertRaises(ValueError, brief.safe_paths, str(self.wt), [p])


if __name__ == "__main__":
    unittest.main()
