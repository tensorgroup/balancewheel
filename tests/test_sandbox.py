import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lead import containment, sandbox  # noqa: E402
from lead.projects import resolve_project  # noqa: E402

GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, env=GITENV)


class Grants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "app"; self.repo.mkdir()
        git(self.repo, "init", "-q"); (self.repo / "f").write_text("x"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "i")
        self.wt = root / "wt"; git(self.repo, "worktree", "add", "-q", str(self.wt))
        self.other = root / "wt2"; git(self.repo, "worktree", "add", "-q", str(self.other))
        self.state = root / "state"; self.state.mkdir()
        self.common = str(self.repo / ".git")
        self.rt = sandbox.RuntimeGrants(read=[str(root / "rt-install")], write=[str(root / "rt-state")], env={}, private_home=False)
        (root / "rt-install").mkdir(); (root / "rt-state").mkdir()
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def grants(self, where):
        return sandbox.lead_grants(resolve_project(str(where), {"projects": {}, "default_profile": "d"}), self.rt, str(self.state))

    def rules(self, g, action):
        return [(k, p) for a, k, p in g.rules if a == action]

    def test_linked_lead_writes_worktree_runtime_state_and_handoffs_only(self):
        g = self.grants(self.wt)
        allows = [p for _, p in self.rules(g, "allow")]
        key = resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}).worktree_key
        self.assertIn(str(self.wt), allows)
        self.assertIn(str(self.root / "rt-state"), allows)
        self.assertIn(str(self.state / "handoffs" / key), allows)
        for p in allows:
            self.assertFalse(p.startswith(str(self.state)) and "handoffs" not in p, p)

    def test_git_control_files_denied(self):
        g = self.grants(self.wt)
        denied = [p for _, p in self.rules(g, "deny")]
        name = os.path.basename(str(self.wt))
        for p in ("hooks", "config", "config.worktree", "worktrees"):
            self.assertIn(os.path.join(self.common, p), denied)
        self.assertIn(os.path.join(self.common, "worktrees", name, "config.worktree"), denied)
        self.assertIn(os.path.join(str(self.wt), ".git"), denied)

    def test_own_admin_dir_reallowed_after_worktrees_deny(self):
        g = self.grants(self.wt)
        name = os.path.basename(str(self.wt))
        own = os.path.join(self.common, "worktrees", name)
        idx_deny = next(i for i, r in enumerate(g.rules) if r == ("deny", "subpath", os.path.join(self.common, "worktrees")))
        idx_allow = next(i for i, r in enumerate(g.rules) if r == ("allow", "subpath", own))
        idx_cfg = next(i for i, r in enumerate(g.rules) if r[2] == os.path.join(own, "config.worktree"))
        self.assertLess(idx_deny, idx_allow)
        self.assertLess(idx_allow, idx_cfg)
        other = os.path.join(self.common, "worktrees", os.path.basename(str(self.other)))
        self.assertNotIn(("allow", "subpath", other), g.rules)

    def test_main_worktree_grants(self):
        g = self.grants(self.repo)
        self.assertIn(("allow", "subpath", self.common), g.rules)
        self.assertIn(("deny", "subpath", os.path.join(self.common, "worktrees")), g.rules)

    def test_seatbelt_profile_ipc_and_no_regex(self):
        prof = sandbox.seatbelt_profile(self.grants(self.wt), str(self.root / "scratch"))
        for rule in ("(deny lsopen)", "(deny appleevent-send)", "(deny user-preference-write)", '"/usr/bin/osascript"', '"/usr/bin/defaults"'):
            self.assertIn(rule, prof)
        self.assertNotIn("regex", prof)
        self.assertTrue(prof.index("(deny file-write*)") < prof.index(f'(subpath "{self.wt}")'))

    def test_nono_profile_shape(self):
        p = sandbox.nono_profile(self.grants(self.wt), str(self.root / "scratch"))
        self.assertEqual(p["extends"], "default")
        self.assertIn("system_write_macos", p["groups"]["exclude"])
        raw = "\n".join(p["unsafe_macos_seatbelt_rules"])
        self.assertIn("(deny appleevent-send)", raw)
        self.assertIn(os.path.join(self.common, "hooks"), raw)
        self.assertNotIn("regex", raw)
        self.assertIn(str(self.root / "scratch"), p["filesystem"]["allow"])

    def test_special_characters_handled_literally(self):
        d = self.root / "we+ird.(dir)"; d.mkdir()
        git(d, "init", "-q"); (d / "f").write_text("x"); git(d, "add", "."); git(d, "commit", "-qm", "i")
        prof = sandbox.seatbelt_profile(self.grants(d), str(self.root / "scratch"))
        self.assertIn(f'(subpath "{d}")', prof)

    def test_symlinked_read_grants_link_node(self):
        real = self.root / "dotfiles-cfg"; real.write_text("x")
        link = self.root / "cfg-link"; link.symlink_to(real)
        rt = sandbox.RuntimeGrants(read=[str(link)], write=[], env={}, private_home=False)
        g = sandbox.lead_grants(resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}), rt, str(self.state))
        p = sandbox.nono_profile(g, str(self.root / "scratch"))
        self.assertIn(str(real), p["filesystem"]["read_file"])
        self.assertIn(f'(allow file-read* (literal "{link}"))', p["unsafe_macos_seatbelt_rules"])

    def test_quote_in_path_refused(self):
        with self.assertRaises(ValueError):
            sandbox.check_path('/tmp/a"b')
        with self.assertRaises(ValueError):
            sandbox.check_path("/tmp/a\\b")

    def test_overlap_refused_both_directions(self):
        with self.assertRaises(ValueError):
            sandbox.refuse_overlap("/a/b", ["/a"])
        with self.assertRaises(ValueError):
            sandbox.refuse_overlap("/a", ["/a/b"])
        sandbox.refuse_overlap("/a/b", ["/a/c"])

    def test_wrap_sets_home_and_tmpdir(self):
        g = self.grants(self.wt)
        argv, cleanup = sandbox.wrap(["echo", "hi"], g, "sandbox-exec", "/h", str(self.root / "scratch"), {"K": "V"})
        try:
            self.assertEqual(argv[0], "sandbox-exec")
            self.assertIn("HOME=/h", argv)
            self.assertIn(f"TMPDIR={self.root / 'scratch'}", argv)
            self.assertIn("K=V", argv)
            self.assertEqual(argv[-2:], ["echo", "hi"])
        finally:
            cleanup()


class Containment(unittest.TestCase):
    def test_is_verified_requires_exact_key(self):
        with tempfile.TemporaryDirectory() as state:
            key = {"runtime": "pi", "runtime_version": "1.0", "backend": "nono", "backend_version": "0.8",
                   "sandbox_sha": "a", "adapter_sha": "b"}
            self.assertFalse(containment.is_verified(state, "pi", key))
            containment.record_pass(state, "pi", key, "/evidence")
            self.assertTrue(containment.is_verified(state, "pi", key))
            for field in key:
                changed = dict(key, **{field: key[field] + "x"})
                self.assertFalse(containment.is_verified(state, "pi", changed), field)

    def test_verification_key_fields(self):
        key = containment.verification_key("pi", [sys.executable, "--version"], "sandbox-exec")
        self.assertEqual(set(key), {"runtime", "runtime_version", "backend", "backend_version", "sandbox_sha", "adapter_sha"})
        self.assertTrue(key["runtime_version"])


if __name__ == "__main__":
    unittest.main()
