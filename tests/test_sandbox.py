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
        sealed, view = str(self.state / "sealed"), str(self.state / "inbox-view")
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

    def test_inbox_view_readable_not_writable(self):
        view = self.state / "inbox-view"; view.mkdir(exist_ok=True)
        f = view / "k.md"; f.write_text("ANSWER-c4n4ry\n")
        before = f.read_bytes()
        r = self.sh(self.wt, f'cat "{f}"')
        self.assertIn("ANSWER-c4n4ry", r.stdout, r.stderr)
        r = self.sh(self.wt, f'echo x > "{f}"')
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(f.read_bytes(), before)

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
