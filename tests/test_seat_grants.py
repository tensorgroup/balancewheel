import json, os, shutil, socket, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lead import sandbox  # noqa: E402
from lead.projects import resolve_project  # noqa: E402
from seats import grants  # noqa: E402

GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True, env=GITENV)


class SeatGrants(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name)); self.root = root
        repo = root / "app"; repo.mkdir()
        git(repo, "init", "-q"); (repo / "f").write_text("x"); git(repo, "add", "."); git(repo, "commit", "-qm", "i")
        self.project = resolve_project(str(repo), {"projects": {}, "default_profile": "d"})
        self.state = str(root / "state"); os.mkdir(self.state)
        self.state_link = str(root / "state-link"); os.symlink(self.state, self.state_link)
        self.home = os.path.join(self.state, "seats", "homes", "s"); os.makedirs(self.home)
        self.scratch = tempfile.mkdtemp(dir=grants.seat_scratch_root())
        self.other = str(root / "other"); os.mkdir(self.other)
        self.cred = os.path.join(self.home, "auth.json"); Path(self.cred).write_text("{}")
        self.d = str(root)
        self.none = sandbox.RuntimeGrants([], [], {}, False, ())

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True)
        self.tmp.cleanup()

    def repo_at(self, where):
        where = Path(where); where.mkdir(parents=True, exist_ok=True)
        git(where, "init", "-q"); (where / "f").write_text("x"); git(where, "add", "."); git(where, "commit", "-qm", "i")
        return resolve_project(str(where), {"projects": {}, "default_profile": "d"})

    def grants(self, rt=None, **kw):
        return grants.seat_grants(self.project, self.home, rt or self.none, self.state, self.scratch, **kw)

    def test_only_seat_home_is_writable(self):
        g = self.grants(sandbox.RuntimeGrants([], [self.home + "/sub"], {}, True, ()))
        self.assertEqual([r for r in g.rules if r[0] == "allow"],
                         [("allow", "subpath", self.home), ("allow", "subpath", self.home + "/sub")])

    def test_write_outside_home_refused(self):
        with self.assertRaises(ValueError):
            self.grants(sandbox.RuntimeGrants([], [self.other], {}, False, ()))

    def test_code_paths_in_home_denied_and_credential_literal(self):
        g = self.grants(sandbox.RuntimeGrants([], [], {}, False, [self.home + "/hooks"]), write_files=[self.cred])
        self.assertIn(("deny", "subpath", self.home + "/hooks"), g.rules)
        self.assertIn(("allow", "literal", self.cred), g.rules)
        self.assertLess(g.rules.index(("deny", "subpath", self.home + "/hooks")),
                        g.rules.index(("allow", "literal", self.cred)))

    def test_symlinked_credential_refused(self):
        link = os.path.join(self.home, "auth-link.json"); os.symlink(self.cred, link)
        with self.assertRaises(ValueError):
            self.grants(write_files=[link])

    def test_missing_or_dir_credential_refused(self):
        for p in (os.path.join(self.home, "absent.json"), self.other):
            with self.assertRaises(ValueError):
                self.grants(write_files=[p])

    def test_symlinked_state_and_scratch_realpathed(self):
        scratch_link = str(self.root / "scratch-link"); os.symlink(self.scratch, scratch_link)
        g = grants.seat_grants(self.project, self.home, self.none, self.state_link, scratch_link)
        self.assertIn(os.path.realpath(self.state_link), g.deny_read)
        self.assertNotIn(self.state_link, g.deny_read)
        self.assertEqual(list(g.reread), [self.home, self.scratch])

    def test_git_global_config_readable_through_its_link(self):
        # nono denies reads under the home by default; git dies when ~/.gitconfig is unreadable
        # ("fatal: unable to access"), so a seat could run no git command. Read only, as for a lead.
        fake_home = self.root / "user"; dots = fake_home / "dots"; dots.mkdir(parents=True)
        (dots / ".gitconfig").write_text("[user]\n")
        os.symlink("dots/.gitconfig", fake_home / ".gitconfig")
        (fake_home / ".config" / "git").mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants()
        for p in (str(dots / ".gitconfig"), str(fake_home / ".gitconfig"), str(fake_home / ".config" / "git")):
            self.assertIn(p, g.read)
        self.assertNotIn(str(fake_home / ".gitconfig"), [r[2] for r in g.rules])  # never a write grant

    def test_toolchain_dirs_read_only_existing_real_paths(self):
        fake_home = self.root / "user"
        for rel in (".pyenv", ".cargo", ".local/lib", "tools/rbenv"):
            (fake_home / rel).mkdir(parents=True)
        os.symlink("tools/rbenv", fake_home / ".rbenv")
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants()
        self.assertEqual(sorted(g.tool_read), sorted([str(fake_home / ".pyenv"), str(fake_home / ".cargo"),
                                                      str(fake_home / ".local/lib"), str(fake_home / "tools/rbenv"),
                                                      str(fake_home / ".rbenv")]))  # the link node as well
        self.assertNotIn(str(fake_home / ".nvm"), g.tool_read)  # missing: not granted
        written = [r[2] for r in g.rules]
        self.assertFalse([p for p in g.tool_read if any(p == w or p.startswith(w + "/") for w in written)])
        self.assertEqual(sorted(grants.SEAT_TOOLCHAIN_READS),
                         sorted([".pyenv", ".volta", ".nvm", ".rbenv", ".cargo", ".rustup", ".local/bin", ".local/lib"]))

    def test_toolchain_dir_in_a_secret_or_holding_the_home_dropped(self):
        fake_home = self.root / "user"; (fake_home / ".ssh" / "py").mkdir(parents=True)
        os.symlink(".ssh/py", fake_home / ".pyenv")  # a toolchain name that resolves into a secret dir
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants()
        self.assertEqual(list(g.tool_read), [])

    def _codex_install(self, fake_home, real_bin):
        """`.local/bin/codex` -> `.codex/packages/standalone/current/bin/codex`, where `current` links
        to a release dir (the codex standalone layout). Returns the PATH dir."""
        real = fake_home / real_bin; real.parent.mkdir(parents=True); real.write_text("#!/bin/sh\n")
        real.chmod(0o755)
        path_dir = fake_home / ".local" / "bin"; path_dir.mkdir(parents=True)
        return path_dir, real

    def test_install_of_a_symlinked_binary_read_through_its_links(self):
        # The codex standalone install puts a link in ~/.local/bin; the binary and its resources live
        # under ~/.codex/packages. nono denies those reads, and `env` then says "codex: No such file".
        fake_home = self.root / "user"
        path_dir, real = self._codex_install(fake_home, ".codex/packages/standalone/releases/1.0/bin/codex")
        current = fake_home / ".codex/packages/standalone/current"; os.symlink(str(real.parent.parent), current)
        os.symlink(str(current / "bin" / "codex"), path_dir / "codex")
        (fake_home / ".codex" / "sessions").mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(fake_home), "PATH": str(path_dir)}):
            g = self.grants(installs=["codex"])
        release = str(fake_home / ".codex/packages/standalone/releases/1.0")
        self.assertIn(release, g.tool_read)
        self.assertIn(str(current), g.tool_read)  # the link node in the middle of the path
        self.assertIn(str(path_dir / "codex"), g.tool_read)
        self.assertNotIn(str(fake_home / ".codex"), g.tool_read)
        self.assertNotIn(release, g.read)  # a tool read never lifts a secret deny
        self.assertFalse([r for r in g.rules if r[2].startswith(str(fake_home / ".codex"))])  # no write rule

    def test_install_root_overlapping_a_store_or_not_a_symlink_dropped(self):
        fake_home = self.root / "user"
        path_dir, real = self._codex_install(fake_home, ".codex/bin/codex")  # root .codex holds sessions/
        (fake_home / ".codex" / "sessions").mkdir()
        os.symlink(str(real), path_dir / "codex")
        (path_dir / "plain").write_text("#!/bin/sh\n"); (path_dir / "plain").chmod(0o755)
        with mock.patch.dict(os.environ, {"HOME": str(fake_home), "PATH": str(path_dir)}):
            g = self.grants(installs=["codex", "plain", "absent"])
        self.assertFalse([p for p in g.tool_read if p.startswith(str(fake_home / ".codex"))])
        self.assertEqual(sorted(g.tool_read), [str(fake_home / ".local/bin")])  # the toolchain dir only

    def test_cargo_registry_tokens_are_secrets(self):
        self.assertIn(".cargo/credentials", sandbox.SECRET_READS)
        self.assertIn(".cargo/credentials.toml", sandbox.SECRET_READS)

    def test_state_and_scratch_root_read_denied_own_reread(self):
        g = self.grants()
        self.assertIn(self.state, g.deny_read)
        self.assertIn(grants.seat_scratch_root(), g.deny_read)
        self.assertEqual(list(g.reread), [self.home, self.scratch])
        self.assertIn(self.project.worktree, g.read)
        self.assertIn(self.project.common_dir, g.read)

    def test_lead_session_stores_read_denied_unless_seat_writes_them(self):
        fake_home = self.root / "user"
        (fake_home / grants.LEAD_SESSION_STORES[0]).mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants()
            for s in grants.LEAD_SESSION_STORES:  # present or not yet created: all denied
                self.assertIn(str(fake_home / s), g.deny_read)
            self.home = str(fake_home / ".codex")  # a seat whose home holds a lead store keeps it
            g = self.grants()
            self.assertNotIn(str(fake_home / ".codex" / "sessions"), g.deny_read)
            self.assertIn(str(fake_home / ".claude" / "projects"), g.deny_read)

    def test_target_inside_state_refused(self):
        project = self.repo_at(Path(self.state) / "repo")
        with self.assertRaises(ValueError):
            grants.seat_grants(project, self.home, self.none, self.state, self.scratch)

    def test_write_file_outside_home_or_under_code_path_refused(self):
        in_state = os.path.join(self.state, "containment.json"); Path(in_state).write_text("{}")
        in_target = os.path.join(self.project.worktree, "f")
        hooks = os.path.join(self.home, "hooks"); os.mkdir(hooks)
        under_code = os.path.join(hooks, "h.json"); Path(under_code).write_text("{}")
        settings = os.path.join(self.home, "settings.json"); Path(settings).write_text("{}")
        rt = sandbox.RuntimeGrants([], [], {}, False, [hooks, settings])
        for p in (in_state, in_target, under_code, settings):
            with self.assertRaises(ValueError, msg=p):
                self.grants(rt, write_files=[p])

    def test_declared_credential_literal_read_and_write(self):  # ruling C-cred
        fake_home = self.root / "user"; (fake_home / ".codex").mkdir(parents=True)
        auth = fake_home / ".codex" / "auth.json"; auth.write_text("{}")
        (fake_home / ".codex" / "config.toml").write_text("")
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants(credentials=[str(auth)])
        self.assertIn(("allow", "literal", str(auth)), g.rules)
        self.assertIn(str(auth), g.reread)
        self.assertNotIn(("allow", "literal", str(fake_home / ".codex" / "config.toml")), g.rules)
        self.assertFalse(any(r[2] == str(fake_home / ".codex") for r in g.rules))

    def test_undeclared_write_file_outside_home_still_refused(self):
        outside = os.path.join(self.other, "auth.json"); Path(outside).write_text("{}")
        with self.assertRaises(ValueError):
            self.grants(write_files=[outside])
        self.grants(credentials=[outside])  # the same file as a declared credential is allowed

    def test_credential_refused_in_state_target_code_scratch_or_as_link(self):
        in_state = os.path.join(self.state, "auth.json"); Path(in_state).write_text("{}")
        in_target = os.path.join(self.project.worktree, "f")
        in_scratch = os.path.join(self.scratch, "auth.json"); Path(in_scratch).write_text("{}")
        hooks = os.path.join(self.home, "hooks"); os.mkdir(hooks)
        under_code = os.path.join(hooks, "auth.json"); Path(under_code).write_text("{}")
        good = os.path.join(self.other, "auth.json"); Path(good).write_text("{}")
        link = os.path.join(self.other, "link.json"); os.symlink(good, link)
        rt = sandbox.RuntimeGrants([], [], {}, False, [hooks])
        for p in (in_state, in_target, in_scratch, under_code, link, self.other, os.path.join(self.other, "absent")):
            with self.assertRaises(ValueError, msg=p):
                self.grants(rt, credentials=[p])

    def test_declared_state_dir_writable_code_names_denied(self):  # ruling S-agy
        fake_home = self.root / "user"; agy = fake_home / ".gemini" / "agy-state"; agy.mkdir(parents=True)
        rt = sandbox.RuntimeGrants([], [], {}, False, [str(agy / "settings.json")])
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants(rt, state_dirs=[str(agy)])
        allow, deny = ("allow", "subpath", str(agy)), ("deny", "subpath", str(agy / "settings.json"))
        self.assertIn(allow, g.rules)
        self.assertLess(g.rules.index(allow), g.rules.index(deny))  # the code deny comes after the allow
        self.assertFalse(any(r[2] == str(fake_home / ".gemini") for r in g.rules))

    def test_agy_lead_stores_read_denied_except_for_the_agy_seat(self):
        from seats import adapters
        fake_home = self.root / "user"; (fake_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
        agy_stores = [".gemini/antigravity-cli/conversations", ".gemini/antigravity-cli/conversation_summaries.db",
                      ".gemini/antigravity-cli/history.jsonl"]
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            for name in ("codex", "claude-code"):
                g = self.grants(adapters.SEAT_ADAPTERS[name].grants({"model": "m"}, self.home))
                for s in agy_stores:
                    self.assertIn(str(fake_home / s), g.deny_read, (name, s))
            agy = adapters.SEAT_ADAPTERS["agy"]
            state = adapters._agy_state()
            g = self.grants(agy.grants({"model": "m"}, self.home), state_dirs=[state])
            profile = sandbox.seatbelt_profile(g, self.scratch)
        for s in agy_stores:  # inside its own state dir: the agy seat reads and writes them
            self.assertNotIn(str(fake_home / s), g.deny_read)
            self.assertNotIn(f'(deny file-read* (subpath "{fake_home / s}"))', profile)
        self.assertIn(str(fake_home / ".gemini" / "tmp"), g.deny_read)
        allow = f'(allow file-write* (subpath "{state}"))'
        self.assertIn(allow, profile)
        for n in adapters.AGY_CODE:  # knowledge, brain, implicit, annotations included
            deny = f'(deny file-write* (subpath "{os.path.join(state, n)}"))'
            self.assertIn(deny, profile, n)
            self.assertLess(profile.index(allow), profile.index(deny))

    def test_state_dir_refused_outside_home_in_bw_paths_or_holding_secrets(self):
        fake_home = self.root  # bw state, the target, and the seat home all lie under it
        (fake_home / ".gemini" / "s").mkdir(parents=True)
        outside = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, outside, True)
        link = fake_home / "link"; os.symlink(self.other, link)
        code_dir = fake_home / "code"; (code_dir / "sub").mkdir(parents=True)
        os.makedirs(os.path.join(self.state, "x"))
        rt = sandbox.RuntimeGrants([], [], {}, False, [str(code_dir)])
        bad = [str(fake_home), str(fake_home / ".gemini"),  # the home itself; a dir that holds a secret path
               outside, self.state, os.path.join(self.state, "x"), self.home, self.scratch,
               self.project.worktree, os.path.join(self.project.worktree, ".git"),
               str(link), str(fake_home / "absent"), str(code_dir / "sub")]
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            for p in bad:
                with self.assertRaises(ValueError, msg=p):
                    self.grants(rt, state_dirs=[p])
            for good in (str(fake_home / ".gemini" / "s"), self.other):  # plain dirs under the home
                self.grants(rt, state_dirs=[good])

    def test_target_overlapping_a_seat_write_grant_refused(self):
        inside_home = self.repo_at(os.path.join(self.home, "repo"))
        with self.assertRaises(ValueError):
            grants.seat_grants(inside_home, self.home, self.none, self.state, self.scratch)
        sub = os.path.join(self.root, "rt", "sub")
        target_holds_home = self.repo_at(self.root / "rt")
        with self.assertRaises(ValueError):  # the target contains the seat home
            grants.seat_grants(target_holds_home, sub, self.none, self.state, self.scratch)
        in_scratch = self.repo_at(os.path.join(self.scratch, "repo"))
        with self.assertRaises(ValueError):
            grants.seat_grants(in_scratch, self.home, self.none, self.state, self.scratch)

    def test_scratch_outside_the_scratch_root_refused(self):
        for bad in (self.other, grants.seat_scratch_root()):
            with self.assertRaises(ValueError, msg=bad):
                grants.seat_grants(self.project, self.home, self.none, self.state, bad)

    def test_target_holding_a_secret_path_refused(self):
        fake_home = self.root / "user"
        project = self.repo_at(fake_home)  # ~/.ssh, ~/.aws, ... lie under this target
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            with self.assertRaises(ValueError):
                grants.seat_grants(project, self.home, self.none, self.state, self.scratch)
            grants.seat_grants(self.project, self.home, self.none, self.state, self.scratch)  # a plain repo is fine

    def test_seatbelt_rereads_after_deny(self):
        p = sandbox.seatbelt_profile(self.grants(), self.scratch)
        self.assertLess(p.index(f'(deny file-read* (subpath "{self.state}"))'),
                        p.index(f'(allow file-read* (subpath "{self.home}"))'))

    def test_nono_without_allow_cwd(self):
        argv, cleanup = sandbox.wrap(["x"], self.grants(), "nono", self.home, self.scratch, {}, allow_cwd=False)
        cleanup()
        self.assertNotIn("--allow-cwd", argv)

    def test_lead_wrap_keeps_allow_cwd(self):
        argv, cleanup = sandbox.wrap(["x"], self.grants(), "nono", self.home, self.scratch, {})
        cleanup()
        self.assertIn("--allow-cwd", argv)

    def test_seat_env_allowlist(self):
        src = {"PATH": "/bin", "HOME": "/h", "LANG": "C", "LC_ALL": "C", "TERM": "x", "OPENROUTER_API_KEY": "k",
               "OTHER_API_KEY": "o", "ORCA_X": "1", "BW_GUARD_SOCK": "s", "BW_PANEL_VIEW": "v", "GIT_DIR": "/g"}
        self.assertEqual(grants.seat_env(src, keep=["OPENROUTER_API_KEY"]),
                         {"PATH": "/bin", "HOME": "/h", "LANG": "C", "LC_ALL": "C", "TERM": "x", "OPENROUTER_API_KEY": "k"})
        self.assertNotIn("OPENROUTER_API_KEY", grants.seat_env(src))

    def test_scratch_root_ignores_tmpdir(self):
        with mock.patch.dict(os.environ, {"TMPDIR": self.d}):
            root = grants.seat_scratch_root()
        self.assertFalse(root.startswith(self.d))
        self.assertEqual(root, os.path.realpath(root))
        self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)


@unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec required")
class SeatEscapesUnderSeatbelt(unittest.TestCase):
    """No model: a shell under a seat profile tries to write the target, read bw state, and read
    another seat's scratch dir."""

    backend = "sandbox-exec"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "app"; self.repo.mkdir()
        git(self.repo, "init", "-q"); (self.repo / "f").write_text("x"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "i")
        self.state = root / "state"; self.state.mkdir()
        (self.state / "inbox.jsonl").write_text("secret-state\n")
        self.home = self.state / "seats" / "homes" / "s"; (self.home / "hooks").mkdir(parents=True)
        self.scratch = tempfile.mkdtemp(dir=grants.seat_scratch_root())
        self.other = tempfile.mkdtemp(dir=grants.seat_scratch_root())
        Path(self.other, "brief").write_text("other-seat\n")

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True); shutil.rmtree(self.other, ignore_errors=True)
        self.tmp.cleanup()

    def sh(self, script, credentials=(), user_home=None):
        """`user_home`: the home the profile is built for (secret denies); the backend still runs with the real HOME."""
        rt = sandbox.RuntimeGrants([], [], {}, True, [str(self.home / "hooks")])
        project = resolve_project(str(self.repo), {"projects": {}, "default_profile": "d"})
        with mock.patch.dict(os.environ, {"HOME": user_home} if user_home else {}):
            g = grants.seat_grants(project, str(self.home), rt, str(self.state), self.scratch, credentials=credentials)
            argv, cleanup = sandbox.wrap(["/bin/sh", "-c", script], g, self.backend, str(self.home), self.scratch, {},
                                         allow_cwd=False)
        try:
            return subprocess.run(argv, cwd=str(self.repo), capture_output=True, text=True)
        finally:
            cleanup()

    def test_target_readable_not_writable(self):
        r = self.sh(f"cat {self.repo}/f; echo; echo y > {self.repo}/f; echo z > {self.repo}/new")
        self.assertEqual(r.stdout.splitlines()[:1], ["x"], r.stderr)
        self.assertEqual((self.repo / "f").read_text(), "x")
        self.assertFalse((self.repo / "new").exists())

    def test_home_and_scratch_writable_code_paths_not(self):
        r = self.sh(f"echo a > {self.home}/session; echo b > {self.scratch}/out; echo c > {self.home}/hooks/h;"
                    f" cat {self.home}/session {self.scratch}/out")
        self.assertEqual(r.stdout.splitlines(), ["a", "b"], r.stderr)
        self.assertFalse((self.home / "hooks" / "h").exists())

    def test_declared_credential_read_and_refreshed_sibling_denied(self):  # ruling C-cred
        fake_home = Path(os.path.realpath(self.tmp.name)) / "user"; codex = fake_home / ".codex"; codex.mkdir(parents=True)
        auth = codex / "auth.json"; auth.write_text("old-login\n")
        sibling = codex / "config.toml"; sibling.write_text("cfg\n")
        script = f"cat {auth}; echo new-login > {auth}; echo bad > {sibling}; echo bad > {codex}/planted"
        control = self.sh(f"cat {auth}", user_home=str(fake_home))  # undeclared: the secret deny holds
        r = self.sh(script, credentials=[str(auth)], user_home=str(fake_home))
        if self.backend == "sandbox-exec":  # nono's secret denies follow the real HOME, not this fake one
            self.assertNotIn("old-login", control.stdout)
        self.assertIn("old-login", r.stdout, r.stderr)
        self.assertEqual(auth.read_text(), "new-login\n")
        self.assertEqual(sibling.read_text(), "cfg\n")
        self.assertFalse((codex / "planted").exists())

    def test_toolchain_readable_not_writable_secrets_in_it_denied(self):
        fake_home = Path(os.path.realpath(self.tmp.name)) / "user"
        (fake_home / ".pyenv").mkdir(parents=True); (fake_home / ".pyenv" / "x").write_text("tool-code\n")
        (fake_home / ".cargo").mkdir(); (fake_home / ".ssh").mkdir()
        (fake_home / ".cargo" / "credentials.toml").write_text("cargo-token-toml\n")
        (fake_home / ".cargo" / "credentials").write_text("cargo-token-old\n")
        (fake_home / ".ssh" / "id").write_text("ssh-key\n")
        r = self.sh(f"cat {fake_home}/.pyenv/x; cat {fake_home}/.cargo/credentials.toml {fake_home}/.cargo/credentials;"
                    f" cat {fake_home}/.ssh/id; echo bad > {fake_home}/.pyenv/y; echo bad > {fake_home}/.cargo/z",
                    user_home=str(fake_home))
        self.assertIn("tool-code", r.stdout, r.stderr)
        self.assertNotIn("cargo-token", r.stdout)
        if self.backend == "sandbox-exec":  # nono's own secret denies follow the real HOME, not this fake one
            self.assertNotIn("ssh-key", r.stdout)
        self.assertFalse((fake_home / ".pyenv" / "y").exists())
        self.assertFalse((fake_home / ".cargo" / "z").exists())

    def test_bw_state_and_other_seat_scratch_unreadable(self):
        r = self.sh(f"cat {self.state}/inbox.jsonl; cat {self.other}/brief; ls {self.state}")
        self.assertNotIn("secret-state", r.stdout)
        self.assertNotIn("other-seat", r.stdout)
        self.assertNotIn("inbox.jsonl", r.stdout)


@unittest.skipUnless(shutil.which("nono"), "nono not installed")
class SeatEscapesUnderNono(SeatEscapesUnderSeatbelt):
    backend = "nono"


if __name__ == "__main__":
    unittest.main()
