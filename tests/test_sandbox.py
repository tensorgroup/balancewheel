import json, os, shutil, socket, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

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

    def test_both_backends_deny_canvas_control_channels(self):
        prof = sandbox.seatbelt_profile(self.grants(self.wt), str(self.root / "scratch"))
        raw = "\n".join(sandbox.nono_profile(self.grants(self.wt), str(self.root / "scratch"))["unsafe_macos_seatbelt_rules"])
        for d in ("~/Library/Application Support/orca", "~/.config/herdr"):
            p = os.path.realpath(os.path.expanduser(d))
            for rule in (f'(deny file-read* (subpath "{p}"))',
                         f'(deny network-outbound (remote unix-socket (subpath "{p}")))'):
                self.assertIn(rule, prof)
                self.assertIn(rule, raw)

    def test_ccswitch_dir_is_a_secret_read(self):
        prof = sandbox.seatbelt_profile(self.grants(self.wt), str(self.root / "scratch"))
        p = os.path.realpath(os.path.expanduser("~/.cc-switch"))
        deny = next(part for part in prof.split(")(") if part.startswith("deny file-read* (subpath"))
        self.assertIn(f'(subpath "{p}")', deny)

    def test_ports_denied_in_both_backends(self):
        ports = sandbox.denied_ports({"cockpit": {"port": 9100}})
        self.assertEqual(ports, [6768, 8765, 9100, 15721])
        self.assertEqual(sandbox.denied_ports(), [6768, 8765, 15721])
        g = self.grants(self.wt)
        prof = sandbox.seatbelt_profile(g, str(self.root / "scratch"), ports)
        raw = "\n".join(sandbox.nono_profile(g, str(self.root / "scratch"), ports)["unsafe_macos_seatbelt_rules"])
        for port in (6768, 15721, 8765, 9100):
            rule = f'(deny network-outbound (remote ip "*:{port}"))'
            self.assertIn(rule, prof)
            self.assertIn(rule, raw)

    def test_sealed_dir_denied_and_inbox_view_readable(self):
        g = self.grants(self.wt)
        key = resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}).worktree_key
        sealed, view = str(self.state / "sealed"), str(self.state / "inbox-view" / key)
        prof = sandbox.seatbelt_profile(g, str(self.root / "scratch"))
        self.assertIn(f'(deny file-read* (subpath "{sealed}"))', prof)
        p = sandbox.nono_profile(g, str(self.root / "scratch"))
        fs = p["filesystem"]
        for key in ("allow", "write", "read", "read_file"):
            self.assertFalse(any("sealed" in x for x in fs[key]), (key, fs[key]))
        self.assertFalse(any("sealed" in r and "allow" in r for r in p["unsafe_macos_seatbelt_rules"]))
        self.assertIn(view, g.read)
        self.assertIn(view, fs["read"])
        for _, _, path in g.rules:
            self.assertFalse(view == path or view.startswith(path.rstrip("/") + "/"), path)

    def test_only_the_own_view_is_granted_and_the_inbox_record_is_denied(self):
        g = self.grants(self.wt)
        key = resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}).worktree_key
        views, record = str(self.state / "inbox-view"), str(self.state / "inbox.jsonl")
        own = os.path.join(views, key)
        self.assertNotIn(views, g.read)
        self.assertIn(own, g.read)
        for prof in (sandbox.seatbelt_profile(g, str(self.root / "scratch")),
                     "".join(sandbox.nono_profile(g, str(self.root / "scratch"))["unsafe_macos_seatbelt_rules"])):
            deny_views, allow_own = f'(deny file-read* (subpath "{views}"))', f'(allow file-read* (subpath "{own}"))'
            self.assertIn(f'(deny file-read* (subpath "{record}"))', prof)
            self.assertIn(deny_views, prof)
            self.assertGreater(prof.index(allow_own), prof.index(deny_views))  # the later rule wins

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

    def test_runtime_write_covering_state_refused(self):
        rt = sandbox.RuntimeGrants(read=[], write=[str(self.state)], env={}, private_home=False)
        with self.assertRaises(ValueError):
            sandbox.lead_grants(resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}), rt, str(self.state))

    def test_state_dir_inside_worktree_refused(self):
        inner = self.wt / "state"; inner.mkdir()
        with self.assertRaises(ValueError):
            sandbox.lead_grants(resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}), self.rt, str(inner))

    def test_adapters_keep_runtime_code_and_config_read_only(self):
        from lead.runtimes import ADAPTERS
        lead = {"model": "m", "plan": "p"}
        for rt, must in (("kimi", ["bin", "config.toml", "mcp.json"]), ("pi", ["extensions", "settings.json", "models.json"])):
            g = ADAPTERS[rt].grants(lead)
            for name in must:
                self.assertTrue(any(d.endswith("/" + name) for d in g.deny_write), (rt, name))

    def test_self_contained_leads_can_write_their_checkpoint_dir(self):
        from lead.runtimes import ADAPTERS
        lead = {"model": "m", "plan": "p"}
        for rt in ("codex", "agy"):
            argv = ADAPTERS[rt].argv(lead, "hi", ["/state/handoffs/k"])
            i = argv.index("--add-dir")
            self.assertEqual(argv[i + 1], "/state/handoffs/k", rt)
        self.assertNotIn("--add-dir", ADAPTERS["claude-code"].argv(lead, "hi", ["/state/handoffs/k"]))

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


@unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec required")
class EscapesUnderSeatbelt(unittest.TestCase):
    """No model: a shell under the generated profile tries what reviewers reproduced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name)); self.root = root
        self.repo = root / "app"; self.repo.mkdir()
        git(self.repo, "init", "-q"); (self.repo / "f").write_text("x"); git(self.repo, "add", "."); git(self.repo, "commit", "-qm", "i")
        (self.repo / ".git" / "modules" / "sub" / "hooks").mkdir(parents=True)
        self.wt = root / "wt"; git(self.repo, "worktree", "add", "-q", str(self.wt))
        self.state = root / "state"; self.state.mkdir()
        self.rtdir = root / "rt"; (self.rtdir / "bin").mkdir(parents=True); (self.rtdir / "sessions").mkdir()
        self.scratch = root / "scratch"; self.scratch.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    backend = "sandbox-exec"

    def sh(self, where, script):
        rt = sandbox.RuntimeGrants(read=[], write=[str(self.rtdir)], env={}, private_home=False,
                                   deny_write=[str(self.rtdir / "bin")])
        g = sandbox.lead_grants(resolve_project(str(where), {"projects": {}, "default_profile": "d"}), rt, str(self.state))
        argv, cleanup = sandbox.wrap(["/bin/sh", "-c", script], g, self.backend, os.path.expanduser("~"),
                                     str(self.scratch), {})
        try:
            return subprocess.run(argv, cwd=str(where), capture_output=True, text=True)
        finally:
            cleanup()

    def assert_denied(self, where, script, path):
        before = path.read_bytes() if path.is_file() else None
        self.sh(where, script)
        after = path.read_bytes() if path.is_file() else None
        self.assertEqual(before, after, f"{script!r} changed {path}")

    def test_linked_commondir_and_gitdir_pointers_denied(self):
        admin = self.repo / ".git" / "worktrees" / "wt"
        self.assert_denied(self.wt, f"echo {self.wt}/evil > {admin}/commondir", admin / "commondir")
        self.assert_denied(self.wt, f"echo /tmp/x > {admin}/gitdir", admin / "gitdir")

    def test_main_git_dir_cannot_be_renamed_or_replaced(self):
        self.sh(self.repo, "mv .git .git-old; cp -R .git-old evil.git; echo 'gitdir: evil.git' > .git")
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertFalse((self.repo / ".git-old").exists())

    def test_submodule_and_info_dirs_denied(self):
        hook = self.repo / ".git" / "modules" / "sub" / "hooks" / "post-checkout"
        self.sh(self.repo, f"echo x > {hook}; echo '* filter=x' > {self.repo}/.git/info/attributes")
        self.assertFalse(hook.exists())
        self.assertFalse((self.repo / ".git" / "info" / "attributes").exists())

    def test_runtime_bin_denied_but_state_writable(self):
        self.sh(self.wt, f"echo x > {self.rtdir}/bin/tool; echo ok > {self.rtdir}/sessions/s")
        self.assertFalse((self.rtdir / "bin" / "tool").exists())
        self.assertTrue((self.rtdir / "sessions" / "s").exists())

    def test_launchctl_cannot_be_copied_and_run(self):
        r = self.sh(self.wt, f"cp /bin/launchctl {self.scratch}/l && {self.scratch}/l version")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.scratch / "l").exists())

    def test_canvas_control_channel_unreachable(self):
        # A canvas (Orca) that runs commands on request is a way out that no write rule
        # covers: its token file and socket must be unreadable and unconnectable inside.
        home = self.root / "h"
        ctl = home / "Library" / "Application Support" / "orca"
        ctl.mkdir(parents=True)
        (ctl / "orca-runtime.json").write_text("TOKEN-c4n4ry")
        server = socket.socket(socket.AF_UNIX)
        cwd = os.getcwd()
        os.chdir(ctl)  # bind relative: the full path is longer than sun_path allows
        try:
            server.bind("c.sock")
        finally:
            os.chdir(cwd)
        server.listen(1)
        probe = (f'cd "{ctl}" && cat orca-runtime.json; cd "{ctl}" && /usr/bin/python3 -c '
                 '"import socket; s=socket.socket(socket.AF_UNIX); s.connect(\'c.sock\'); print(\'CONNECTED\')"')
        try:
            control = subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True)
            self.assertIn("CONNECTED", control.stdout)  # the probe works outside, so a pass means something
            self.assertIn("TOKEN-c4n4ry", control.stdout)
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                r = self.sh(self.wt, probe)
            self.assertNotIn("TOKEN-c4n4ry", r.stdout)
            self.assertNotIn("CONNECTED", r.stdout)
        finally:
            server.close()

    def test_ccswitch_db_unreadable(self):
        home = self.root / "h"
        (home / ".cc-switch").mkdir(parents=True)
        (home / ".cc-switch" / "cc-switch.db").write_bytes(b"SQLite format 3\x00CCSW-c4n4ry")
        probe = f'cat "{home}/.cc-switch/cc-switch.db"'
        self.assertIn("CCSW-c4n4ry", subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True).stdout)
        with mock.patch.dict(os.environ, {"HOME": str(home)}):
            r = self.sh(self.wt, probe)
        self.assertNotIn("CCSW-c4n4ry", r.stdout)

    def test_sealed_copy_unreadable(self):
        d = self.state / "sealed" / "x"; d.mkdir(parents=True)
        (d / "copy.db").write_text("SEALED-c4n4ry")
        probe = f'cat "{d}/copy.db"'
        self.assertIn("SEALED-c4n4ry", subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True).stdout)
        self.assertNotIn("SEALED-c4n4ry", self.sh(self.wt, probe).stdout)

    def own_key(self):
        return resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}).worktree_key

    def test_inbox_view_readable_not_writable(self):
        view = self.state / "inbox-view" / self.own_key(); view.mkdir(parents=True, exist_ok=True)
        f = view / "answers.md"; f.write_text("ANSWER-c4n4ry\n")
        before = f.read_bytes()
        r = self.sh(self.wt, f'cat "{f}"')
        self.assertIn("ANSWER-c4n4ry", r.stdout, r.stderr)
        r = self.sh(self.wt, f'echo x > "{f}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(f.read_bytes(), before)

    def test_other_worktrees_views_and_the_inbox_record_unreadable(self):
        own = self.state / "inbox-view" / self.own_key(); own.mkdir(parents=True, exist_ok=True)
        (own / "answers.md").write_text("OWN-c4n4ry\n")
        other = self.state / "inbox-view" / "0123456789ab"; other.mkdir(parents=True)
        (other / "answers.md").write_text("OTHER-c4n4ry\n")
        legacy = self.state / "inbox-view" / "0123456789ab.md"; legacy.write_text("LEGACY-c4n4ry\n")
        (self.state / "inbox.jsonl").write_text('{"type": "ask", "text": "RECORD-c4n4ry"}\n')
        probe = (f'cat "{own}/answers.md" "{other}/answers.md" "{legacy}" "{self.state}/inbox.jsonl"; '
                 f'ls "{self.state}/inbox-view"')
        outside = subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True).stdout
        for c in ("OWN", "OTHER", "LEGACY", "RECORD"):
            self.assertIn(f"{c}-c4n4ry", outside)  # the probe works outside
        r = self.sh(self.wt, probe)
        self.assertIn("OWN-c4n4ry", r.stdout, r.stderr)
        for c in ("OTHER", "LEGACY", "RECORD"):
            self.assertNotIn(f"{c}-c4n4ry", r.stdout, c)
        self.assertNotIn("0123456789ab", r.stdout)  # other worktrees' keys are not even listed

    PORT_PROBE = ('/usr/bin/python3 -c "import socket\n'
                  'try:\n socket.create_connection((\'127.0.0.1\', {port}), 2); print(\'CONNECTED\')\n'
                  'except OSError as e: print(\'REFUSED\', e)"')

    def assert_port_unconnectable(self, port):
        server = socket.socket()
        try:
            server.bind(("127.0.0.1", port))
            server.listen(4)
        except OSError:
            server.close(); server = None
            try:
                socket.create_connection(("127.0.0.1", port), 2).close()  # held by its owner (Orca owns 6768)
            except OSError:
                self.skipTest(f"port {port} is taken and refuses connections; nothing to probe")
        try:
            probe = self.PORT_PROBE.format(port=port)
            control = subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True)
            self.assertIn("CONNECTED", control.stdout, control.stderr)  # the probe works outside
            r = self.sh(self.wt, probe)
            self.assertNotIn("CONNECTED", r.stdout, port)
        finally:
            if server is not None:
                server.close()

    def test_denied_ports_unconnectable(self):
        for port in (6768, sandbox.DEFAULT_COCKPIT_PORT):
            with self.subTest(port=port):
                self.assert_port_unconnectable(port)

    def test_denied_port_unconnectable_through_own_interface(self):
        # A service bound to 0.0.0.0 is reachable through the Mac's own non-loopback address.
        probe_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe_sock.connect(("192.0.2.1", 9))  # no packet sent; picks the outbound interface
            addr = probe_sock.getsockname()[0]
        except OSError:
            addr = None
        finally:
            probe_sock.close()
        if not addr or addr.startswith("127."):
            self.skipTest("no non-loopback interface address")
        port = sandbox.DEFAULT_COCKPIT_PORT
        server = socket.socket()
        try:
            server.bind(("0.0.0.0", port))
        except OSError:
            server.close()
            self.skipTest(f"port {port} is taken")
        server.listen(4)
        probe = self.PORT_PROBE.format(port=port).replace("127.0.0.1", addr)
        try:
            control = subprocess.run(["/bin/sh", "-c", probe], capture_output=True, text=True)
            self.assertIn("CONNECTED", control.stdout, control.stderr)
            self.assertNotIn("CONNECTED", self.sh(self.wt, probe).stdout)
        finally:
            server.close()

    def test_ccswitch_proxy_port_unconnectable(self):
        self.assert_port_unconnectable(15721)

    def test_commit_inside_still_works(self):
        r = self.sh(self.wt, "echo y > g && git add g && git -c user.email=t@t -c user.name=t commit -qm in")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_commit_in_main_worktree_still_works(self):
        r = self.sh(self.repo, "echo y > g && git add g && git -c user.email=t@t -c user.name=t commit -qm in")
        self.assertEqual(r.returncode, 0, r.stderr)


@unittest.skipUnless(shutil.which("nono"), "nono not installed")
class EscapesUnderNono(EscapesUnderSeatbelt):
    backend = "nono"


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
        self.scratch = tempfile.mkdtemp(dir=sandbox.seat_scratch_root())
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
        return sandbox.seat_grants(self.project, self.home, rt or self.none, self.state, self.scratch, **kw)

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
        g = sandbox.seat_grants(self.project, self.home, self.none, self.state_link, scratch_link)
        self.assertIn(os.path.realpath(self.state_link), g.deny_read)
        self.assertNotIn(self.state_link, g.deny_read)
        self.assertEqual(list(g.reread), [self.home, self.scratch])

    def test_state_and_scratch_root_read_denied_own_reread(self):
        g = self.grants()
        self.assertIn(self.state, g.deny_read)
        self.assertIn(sandbox.seat_scratch_root(), g.deny_read)
        self.assertEqual(list(g.reread), [self.home, self.scratch])
        self.assertIn(self.project.worktree, g.read)
        self.assertIn(self.project.common_dir, g.read)

    def test_lead_session_stores_read_denied_unless_seat_writes_them(self):
        fake_home = self.root / "user"
        (fake_home / sandbox.LEAD_SESSION_STORES[0]).mkdir(parents=True)
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            g = self.grants()
            for s in sandbox.LEAD_SESSION_STORES:  # present or not yet created: all denied
                self.assertIn(str(fake_home / s), g.deny_read)
            self.home = str(fake_home / ".codex")  # a seat whose home holds a lead store keeps it
            g = self.grants()
            self.assertNotIn(str(fake_home / ".codex" / "sessions"), g.deny_read)
            self.assertIn(str(fake_home / ".claude" / "projects"), g.deny_read)

    def test_target_inside_state_refused(self):
        project = self.repo_at(Path(self.state) / "repo")
        with self.assertRaises(ValueError):
            sandbox.seat_grants(project, self.home, self.none, self.state, self.scratch)

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
            sandbox.seat_grants(inside_home, self.home, self.none, self.state, self.scratch)
        sub = os.path.join(self.root, "rt", "sub")
        target_holds_home = self.repo_at(self.root / "rt")
        with self.assertRaises(ValueError):  # the target contains the seat home
            sandbox.seat_grants(target_holds_home, sub, self.none, self.state, self.scratch)
        in_scratch = self.repo_at(os.path.join(self.scratch, "repo"))
        with self.assertRaises(ValueError):
            sandbox.seat_grants(in_scratch, self.home, self.none, self.state, self.scratch)

    def test_scratch_outside_the_scratch_root_refused(self):
        for bad in (self.other, sandbox.seat_scratch_root()):
            with self.assertRaises(ValueError, msg=bad):
                sandbox.seat_grants(self.project, self.home, self.none, self.state, bad)

    def test_target_holding_a_secret_path_refused(self):
        fake_home = self.root / "user"
        project = self.repo_at(fake_home)  # ~/.ssh, ~/.aws, ... lie under this target
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}):
            with self.assertRaises(ValueError):
                sandbox.seat_grants(project, self.home, self.none, self.state, self.scratch)
            sandbox.seat_grants(self.project, self.home, self.none, self.state, self.scratch)  # a plain repo is fine

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
        self.assertEqual(sandbox.seat_env(src, keep=["OPENROUTER_API_KEY"]),
                         {"PATH": "/bin", "HOME": "/h", "LANG": "C", "LC_ALL": "C", "TERM": "x", "OPENROUTER_API_KEY": "k"})
        self.assertNotIn("OPENROUTER_API_KEY", sandbox.seat_env(src))

    def test_scratch_root_ignores_tmpdir(self):
        with mock.patch.dict(os.environ, {"TMPDIR": self.d}):
            root = sandbox.seat_scratch_root()
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
        self.scratch = tempfile.mkdtemp(dir=sandbox.seat_scratch_root())
        self.other = tempfile.mkdtemp(dir=sandbox.seat_scratch_root())
        Path(self.other, "brief").write_text("other-seat\n")

    def tearDown(self):
        shutil.rmtree(self.scratch, ignore_errors=True); shutil.rmtree(self.other, ignore_errors=True)
        self.tmp.cleanup()

    def sh(self, script, credentials=(), user_home=None):
        """`user_home`: the home the profile is built for (secret denies); the backend still runs with the real HOME."""
        rt = sandbox.RuntimeGrants([], [], {}, True, [str(self.home / "hooks")])
        project = resolve_project(str(self.repo), {"projects": {}, "default_profile": "d"})
        with mock.patch.dict(os.environ, {"HOME": user_home} if user_home else {}):
            g = sandbox.seat_grants(project, str(self.home), rt, str(self.state), self.scratch, credentials=credentials)
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

    def test_bw_state_and_other_seat_scratch_unreadable(self):
        r = self.sh(f"cat {self.state}/inbox.jsonl; cat {self.other}/brief; ls {self.state}")
        self.assertNotIn("secret-state", r.stdout)
        self.assertNotIn("other-seat", r.stdout)
        self.assertNotIn("inbox.jsonl", r.stdout)


@unittest.skipUnless(shutil.which("nono"), "nono not installed")
class SeatEscapesUnderNono(SeatEscapesUnderSeatbelt):
    backend = "nono"


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
        self.assertEqual(set(key), {"runtime", "runtime_version", "backend", "backend_version", "sandbox_sha", "adapter_sha",
                                    "codepaths_sha", "denied_ports"})
        self.assertTrue(key["runtime_version"])
        self.assertEqual(key["denied_ports"], sandbox.denied_ports())

    def test_cockpit_port_is_part_of_the_containment_and_guard_keys(self):
        argv = [sys.executable, "--version"]
        base = containment.verification_key("pi", argv, "sandbox-exec", ports=sandbox.denied_ports({}))
        moved = containment.verification_key("pi", argv, "sandbox-exec",
                                             ports=sandbox.denied_ports({"cockpit": {"port": 9100}}))
        self.assertEqual(moved["denied_ports"], [6768, 8765, 9100, 15721])
        self.assertNotEqual(base, moved)
        g1 = containment.guard_key("pi", argv, "sandbox-exec", [], ports=[1, 2])
        g2 = containment.guard_key("pi", argv, "sandbox-exec", [], ports=[1, 3])
        self.assertNotEqual(g1, g2)


if __name__ == "__main__":
    unittest.main()
