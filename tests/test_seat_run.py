import io, json, os, stat, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import containment, proc, sandbox  # noqa: E402
from lead.locks import RuntimeLock  # noqa: E402
from lead.paths import load_config  # noqa: E402
from lead.proc import Result  # noqa: E402
from seats import adapters, brief, caps, grants, run, sessions, verify  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "balancewheel.example.json"
FIX = Path(__file__).resolve().parent / "fixtures" / "seats"
CLI_RUNTIME = {"codex": "codex", "claude": "claude-code", "agy": "agy", "pi": "pi", "kimi": "kimi"}
OK_FIXTURES = ("ok", "resume")


class FakeRun:
    """git runs for real; `<cli> --version` answers; a seat run returns a fixture and records its call."""

    def __init__(self):
        self.queues, self.calls = {}, []
        self.argv = self.env = self.out_dir = self.stdin = self.timeout = self.cwd = None
        self.version_rc, self.raise_ = 0, None

    def queue(self, cli, name):
        self.queues.setdefault(cli, []).append(name)

    @staticmethod
    def cli(argv):
        i = argv.index("/usr/bin/env") + 1
        while "=" in argv[i]:
            i += 1
        return argv[i]

    def __call__(self, argv, timeout, cwd=None, env=None, stdin=subprocess.DEVNULL, out_dir=None, reap=False):
        if argv[0] == "git":
            return proc.run(argv, timeout, cwd=cwd, env=env)
        if len(argv) == 2 and argv[1] == "--version":
            if self.version_rc:
                return Result(self.version_rc, "", f"{argv[0]}: not found", False)
            return Result(0, f"{argv[0]} 9.9\n", "", False)
        assert hasattr(stdin, "read"), "stdin must be a file object, not a path"
        self.argv, self.env, self.out_dir, self.timeout, self.cwd = argv, env, out_dir, timeout, cwd
        self.reap = reap
        self.stdin = stdin.read()
        cli = self.cli(argv)
        self.calls.append(cli)
        if self.raise_:
            raise self.raise_
        q = self.queues.get(cli)
        name = q.pop(0) if q else "ok"
        if isinstance(name, Result):
            return name
        d = FIX / CLI_RUNTIME[cli]
        return Result(0 if name in OK_FIXTURES else 1, (d / f"{name}.stdout").read_text(),
                      (d / f"{name}.stderr").read_text(), False)


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


def lock_free(state, runtime):
    """True when nothing holds the runtime lock; the probe lock is released at once."""
    lock = RuntimeLock(state, runtime)
    try:
        return lock.try_exclusive()
    finally:
        lock.release()


def make_repo(path):
    os.makedirs(path)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "t@example.invalid")
    git(path, "config", "user.name", "t")
    Path(path, "README.md").write_text("# repo\n")
    os.makedirs(os.path.join(path, "sub"))
    Path(path, "sub", "mod.py").write_text("x = 1\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "init")


class SeatRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = os.path.realpath(self.tmp.name)
        self.state = os.path.join(root, "state")
        self.home = os.path.join(root, "home")
        os.makedirs(self.home)
        self.log = os.path.join(root, "metrics.jsonl")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BW_", "BALANCEWHEEL_"))}
        env.update({"HOME": self.home, "BW_STATE_DIR": self.state, "BALANCEWHEEL_METRICS_LOG": self.log,
                    "CLAUDE_CODE_OAUTH_TOKEN": "tok-test"})
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        # Login files live under the temp HOME, not the real one (credentials are read at call time).
        auth = os.path.join(self.home, ".codex", "auth.json")
        os.makedirs(os.path.dirname(auth))
        Path(auth).write_text("{}")
        self.config = load_config(str(EXAMPLE))
        self.wt = os.path.join(root, "wt")
        make_repo(self.wt)
        self.fake = FakeRun()
        self.said = []
        for seat in ("astra", "gem", "opus"):
            self._pass(seat)

    def _pass(self, seat):
        """A pass on a fresh home: no code dirs are made before the key (the seat key makes them)."""
        key = verify.seat_key(self.config, self.state, seat, run=self.fake)
        containment.record_pass(self.state, seat, key, "/ev", store="seat-verify.json")
        return key

    def test_pass_on_fresh_home_survives_runs(self):
        import shutil
        shutil.rmtree(os.path.join(self.state, "seats", "homes", "astra"))
        os.remove(os.path.join(self.state, "seat-verify.json"))
        key = self._pass("astra")
        self.assertTrue(self.seat().ok)
        self.assertTrue(self.seat().ok)  # the first run did not change the key
        self.assertEqual(verify.seat_key(self.config, self.state, "astra", run=self.fake), key)

    def test_brief_diff_does_not_hide_cap(self):
        line = "ERROR: You have hit your usage limit."
        Path(self.wt, "README.md").write_text(f"# repo\n{line}\n")  # in the brief's diff, not in the prompt
        self.fake.queue("codex", Result(1, "", f"user\n{line}\n", False))
        r = self.seat(brief=True, fallback=False)
        self.assertIn(line, self.fake.stdin)
        self.assertEqual(r.reason, "usage cap")
        self.assertIsNotNone(caps.read(self.state, "astra", time.time()))

    def test_invalid_session_id_refused(self):
        r = self.seat(session_id="bad id;x")
        self.assertEqual((r.exit_code, r.reason, r.ran), (75, "refused: invalid session id", False))
        self.assertIsNone(self.fake.argv)

    def usage(self):
        if not os.path.exists(self.log):
            return []
        return [json.loads(line) for line in Path(self.log).read_text().splitlines() if line.strip()]

    def seat(self, seat="astra", prompt="review x", **kw):
        kw.setdefault("target", self.wt)
        kw.setdefault("run", self.fake)
        kw.setdefault("say", self.said.append)
        return run.run_seat(self.config, self.state, seat, prompt, **kw)

    def scratch_dirs(self, seat="astra"):
        return {n for n in os.listdir(grants.seat_scratch_root()) if n.startswith(seat + "-")}

    # ---- the run ----

    def test_ok_run(self):
        r = run.run_seat(self.config, self.state, "astra", "review x", target=self.wt, run=self.fake)
        self.assertEqual((r.ok, r.reason, r.exit_code, r.mode, r.session_id), (True, "ok", 0, "new", "sess-0001"))
        self.assertEqual(self.fake.argv[0], sandbox.backend()); self.assertNotIn("--allow-cwd", self.fake.argv)
        self.assertEqual(self.usage()[-1]["ran"], True)
        self.assertEqual(self.fake.out_dir, self.fake.env["TMPDIR"])  # R3: output files in the granted scratch
        self.assertEqual((r.seat, r.requested, r.stand_in_for, r.runtime), ("astra", "astra", None, "codex"))
        self.assertEqual((r.tokens_in, r.tokens_out, r.answer), (1200, 300, "No significant findings."))
        self.assertEqual(self.fake.cwd, self.wt)
        self.assertEqual(self.fake.timeout, 12 * 60)
        row = self.usage()[-1]
        self.assertEqual({k: row[k] for k in ("type", "seat", "model", "mode", "dir", "ok", "reason", "session_id",
                                              "tokens_in", "tokens_out")},
                         {"type": "usage", "seat": "astra", "model": "gpt-6-astra", "mode": "new", "dir": self.wt,
                          "ok": True, "reason": "ok", "session_id": "sess-0001", "tokens_in": 1200, "tokens_out": 300})
        self.assertNotIn("stand_in_for", row); self.assertNotIn("cost_usd", row)
        self.assertEqual(sessions.get(self.state, "astra", self.wt, "gpt-6-astra"), "sess-0001")
        self.assertEqual(len(self.usage()), 1)

    def test_lock_before_verification(self):  # R2
        calls = []
        real_shared, real_verified = RuntimeLock.shared, verify.seat_key

        def shared(lock):
            calls.append("lock")
            return real_shared(lock)

        def verified(*a, **kw):
            calls.append("verify")
            return real_verified(*a, **kw)

        with mock.patch.object(RuntimeLock, "shared", shared), mock.patch("seats.verify.seat_key", verified):
            r = self.seat()
        self.assertTrue(r.ok)
        self.assertEqual(calls[:2], ["lock", "verify"])
        self.assertEqual(calls.count("verify"), 1)  # the key is read once per run

    def test_runtime_lock_held_during_run(self):
        seen = []

        def during(argv, timeout, **kw):
            if argv[0] != "git" and "--version" not in argv:
                seen.append(lock_free(self.state, "codex"))
            return self.fake(argv, timeout, **kw)

        self.assertTrue(self.seat(run=during).ok)
        self.assertEqual(seen, [False])
        self.assertTrue(lock_free(self.state, "codex"))  # released after the run

    def test_seat_home_symlink_refused(self):  # R15
        home = os.path.join(self.state, "seats", "homes", "astra")
        other = os.path.join(self.tmp.name, "elsewhere")
        os.rename(home, other)  # same contents, so the seat key (hook manifests) still matches
        os.symlink(other, home)
        before = sorted(os.listdir(other))
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason), (75, "sandbox: seat home tampered: astra"))
        self.assertEqual(sorted(os.listdir(other)), before)  # no credential link written there
        self.assertIsNone(self.fake.argv)
        self.assertEqual(self.usage()[-1]["ran"], False)

    def test_credential_link_tampered(self):
        home = os.path.join(self.state, "seats", "homes", "astra")
        Path(home, "auth.json").write_text("planted")
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason), (75, "sandbox: seat home tampered: auth.json"))

    def test_credential_linked(self):
        self.assertTrue(self.seat().ok)
        link = os.path.join(self.state, "seats", "homes", "astra", "auth.json")
        self.assertEqual(os.readlink(link), os.path.join(self.home, ".codex", "auth.json"))
        self.assertTrue(self.seat().ok)  # the existing link is the expected one

    def test_brief_built_from_worktree(self):  # R18
        sub = os.path.join(self.wt, "sub")
        with mock.patch("seats.brief.build", wraps=brief.build) as b:
            r = self.seat(target=sub, brief=True)
        self.assertTrue(r.ok)
        b.assert_called_once_with(self.state, self.wt)
        self.assertEqual((self.fake.cwd, r.dir), (self.wt, self.wt))

    def test_brief_never_on_argv(self):
        self.seat(brief=True, prompt="TASK-MARKER review")
        text = Path(brief.build(self.state, self.wt)).read_text()
        self.assertIn(text, self.fake.stdin)
        self.assertFalse(any(text[:200] in a for a in self.fake.argv))
        self.assertIn(run.READING_RULE, self.fake.stdin)
        self.assertTrue(self.fake.stdin.endswith("TASK-MARKER review"))
        self.assertFalse(any("TASK-MARKER" in a for a in self.fake.argv))

    def test_brief_file(self):
        f = os.path.join(self.tmp.name, "b.md")
        Path(f).write_text("BRIEF-FILE-BODY\n")
        self.seat(brief_file=f)
        self.assertTrue(self.fake.stdin.startswith("BRIEF-FILE-BODY"))
        self.assertIn(run.READING_RULE, self.fake.stdin)

    def test_no_brief_prompt_only(self):
        self.seat(prompt="just this")
        self.assertEqual(self.fake.stdin, "just this")

    def test_deep_cap_and_effort(self):
        self.seat(deep=True)
        self.assertEqual(self.fake.timeout, 30 * 60)
        self.assertIn("model_reasoning_effort=max", self.fake.argv)

    def test_resume_stored_and_missing(self):
        r = self.seat(resume=True)
        self.assertEqual(r.mode, "new")
        self.assertEqual(self.said, [f"bw seat: no session for astra in {self.wt}; starting a new one"])
        sessions.put(self.state, "astra", self.wt, "s1", "gpt-6-astra", time.time())
        self.fake.queue("codex", "resume")
        r = self.seat(resume=True, brief=True, prompt="again")
        self.assertEqual(r.mode, "resume")
        self.assertIn("resume", self.fake.argv)
        self.assertEqual(self.fake.argv[self.fake.argv.index("s1") + 1], "-")
        self.assertEqual(self.fake.stdin, "again")  # a resume gets the prompt only

    def test_explicit_session(self):
        sessions.put(self.state, "astra", self.wt, "s1", "gpt-6-astra", time.time())
        r = self.seat(session_id="p1", resume=True)
        self.assertEqual(r.mode, "resume")
        self.assertIn("p1", self.fake.argv); self.assertNotIn("s1", self.fake.argv)

    # ---- caps and stand-ins ----

    def test_usage_limit_arms_cap_and_stands_in(self):
        self.fake.queue("codex", "usage_limit")
        r = run.run_seat(self.config, self.state, "astra", "p", target=self.wt, run=self.fake, say=self.said.append)
        self.assertEqual((r.seat, r.requested, r.stand_in_for), ("gem", "astra", "astra"))
        self.assertIsNotNone(caps.read(self.state, "astra", time.time()))
        self.assertEqual([(u["seat"], u["ran"], u["reason"]) for u in self.usage()][-2:],
                         [("astra", True, "usage cap"), ("gem", True, "ok")])
        self.assertEqual(len(self.usage()), 2)
        self.assertEqual(self.usage()[-1]["stand_in_for"], "astra")
        self.assertEqual(self.fake.calls, ["codex", "agy"])
        self.assertEqual(len(self.said), 1)
        self.assertRegex(self.said[0], r"^bw seat: astra capped \(seat astra capped until \d\d:\d\d\); gem stands in$")
        self.assertNotIn("BW_SEAT_STANDIN", self.fake.env)  # the seat env is an allowlist (R13)

    def test_prompt_echo_does_not_arm(self):
        self.fake.queue("codex", "prompt_echo")
        r = self.seat(prompt='Review the quota module. It prints "You have hit your usage limit." when a plan is capped.')
        self.assertTrue(r.reason.startswith("error:"), r.reason)
        self.assertEqual(r.exit_code, 1)
        self.assertIsNone(caps.read(self.state, "astra", time.time()))
        self.assertEqual(self.fake.calls, ["codex"])

    def test_echoed_error_line_dropped(self):
        line = "ERROR: You have hit your usage limit."
        self.fake.queue("codex", Result(1, "", f"user\n{line}\n", False))
        r = self.seat(prompt=line)  # the line is in input.md: the vendor echoed it
        self.assertFalse(r.reason == "usage cap")
        self.assertIsNone(caps.read(self.state, "astra", time.time()))

    def test_no_fallback(self):
        self.fake.queue("codex", "usage_limit")
        r = self.seat(fallback=False)
        self.assertEqual((r.reason, r.exit_code, r.seat, r.ran), ("usage cap", 1, "astra", True))
        self.assertEqual(self.fake.calls, ["codex"])
        self.assertIsNotNone(caps.read(self.state, "astra", time.time()))

    def test_capped_before_start(self):
        caps.arm(self.state, "astra", time.time(), 5)
        r = self.seat()
        self.assertEqual((r.seat, r.stand_in_for, r.ok), ("gem", "astra", True))
        self.assertEqual([(u["seat"], u["ran"], u["reason"]) for u in self.usage()],
                         [("astra", False, "usage cap"), ("gem", True, "ok")])
        first = self.usage()[0]
        self.assertEqual((first["mode"], first["duration_s"]), (None, 0))
        self.assertEqual(self.fake.calls, ["agy"])

    def test_capped_no_fallback(self):
        caps.arm(self.state, "astra", time.time(), 5)
        r = self.seat(fallback=False)
        self.assertEqual((r.exit_code, r.reason, r.ran), (75, "usage cap", False))
        self.assertEqual([(u["seat"], u["ran"]) for u in self.usage()], [("astra", False)])
        self.assertEqual(self.fake.calls, [])

    def test_standin_env_blocks_standin(self):
        caps.arm(self.state, "astra", time.time(), 5)
        with mock.patch.dict(os.environ, {"BW_SEAT_STANDIN": "x"}):
            r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.seat), (75, "usage cap", "astra"))

    def test_fallback_cycle_one_hop(self):
        self.config["seats"]["gem"]["fallback"] = "astra"
        caps.arm(self.state, "astra", time.time(), 5)
        caps.arm(self.state, "gem", time.time(), 5)
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.seat, r.requested), (75, "usage cap", "gem", "astra"))
        self.assertEqual([(u["seat"], u["ran"], u["reason"]) for u in self.usage()],
                         [("astra", False, "usage cap"), ("gem", False, "usage cap")])
        self.assertEqual(self.fake.calls, [])

    def test_standin_hits_limit_one_hop(self):
        self.config["seats"]["gem"]["fallback"] = "astra"
        self.fake.queue("codex", "usage_limit")
        self.fake.queue("agy", "usage_limit")
        r = self.seat()
        self.assertEqual((r.seat, r.reason, r.ran), ("gem", "usage cap", True))
        self.assertEqual(self.fake.calls, ["codex", "agy"])

    # ---- refusals ----

    def test_unverified(self):
        os.remove(os.path.join(self.state, "seat-verify.json"))
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.ran), (75, "unverified", False))
        self.assertEqual(self.usage()[-1]["ran"], False)
        self.fake.version_rc = 127
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason), (75, "unverified: codex version unreadable"))
        self.assertIsNone(self.fake.argv)

    def test_attack_token_skips_verification(self):
        os.remove(os.path.join(self.state, "seat-verify.json"))
        self.assertEqual(self.seat(attack=True).reason, "unverified")
        verify.issue_token(self.state, "gem", time.time(), 600)
        self.assertEqual(self.seat(attack=True).reason, "unverified")
        verify.issue_token(self.state, "astra", time.time(), 600)
        self.assertEqual(self.seat().reason, "unverified")  # the token counts only with attack=True
        self.assertTrue(self.seat(attack=True).ok)

    def test_exec_sandboxed_needs_token(self):
        self.fake.queue("/bin/echo", Result(0, "hi\n", "", False))
        r = run.exec_sandboxed(self.config, self.state, "astra", self.wt, ["/bin/echo", "hi"], run=self.fake)
        self.assertEqual((r.rc, r.out, r.err), (75, "", "no attack token"))
        self.assertIsNone(self.fake.argv)
        verify.issue_token(self.state, "astra", time.time(), 600)
        r = run.exec_sandboxed(self.config, self.state, "astra", self.wt, ["/bin/echo", "hi"], run=self.fake)
        self.assertEqual((r.rc, r.out), (0, "hi\n"))
        self.assertEqual(self.fake.argv[0], sandbox.backend())  # wrapped by the seat's sandbox
        self.assertEqual(self.fake.argv[-2:], ["/bin/echo", "hi"])
        self.assertEqual(self.fake.cwd, self.wt)
        self.assertEqual(self.fake.env["CODEX_HOME"], os.path.join(self.state, "seats", "homes", "astra"))
        self.assertEqual(self.fake.out_dir, self.fake.env["TMPDIR"])
        self.assertEqual(self.fake.stdin, "")
        self.assertEqual(self.scratch_dirs(), set())  # the scratch dir is removed
        self.assertEqual(self.usage(), [])  # a plain process is not a seat run: no usage row

    def test_exec_control_argv_drops_ignore_flags(self):
        verify.issue_token(self.state, "opus", time.time(), 600)
        argv = run.control_argv(self.config["seats"]["opus"])
        for flag in adapters.CLAUDE_IGNORE:
            self.assertNotIn(flag, argv)
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(run.control_argv(self.config["seats"]["astra"]),
                         adapters.CODEX.argv(self.config["seats"]["astra"], run.INPUT_PLACEHOLDER, False))
        self.fake.queue("claude", Result(0, "ok\n", "", False))
        r = run.exec_sandboxed(self.config, self.state, "opus", self.wt, argv, run=self.fake, input_text="reply ok")
        self.assertEqual(r.rc, 0)
        self.assertEqual(self.fake.stdin, "reply ok")
        self.assertNotIn("--safe-mode", self.fake.argv)

    def test_agy_runs_on_its_own_gemini_dir(self):
        # --gemini_dir moves agy's config and state into the seat home; the keychain login still loads.
        self.assertTrue(self.seat("gem").ok)
        home = os.path.join(self.state, "seats", "homes", "gem")
        i = self.fake.argv.index("agy")
        self.assertEqual(self.fake.argv[i + 1], f"--gemini_dir={home}/gemini")
        argv = run.control_argv(self.config["seats"]["gem"])
        self.assertEqual(argv[:2], ["agy", f"--gemini_dir={run.HOME_PLACEHOLDER}/gemini"])
        verify.issue_token(self.state, "gem", time.time(), 600)
        run.exec_sandboxed(self.config, self.state, "gem", self.wt, argv, run=self.fake, input_text="reply ok")
        self.assertIn(f"--gemini_dir={home}/gemini", self.fake.argv)
        self.assertEqual(run.control_argv(self.config["seats"]["astra"])[:2], ["codex", "exec"])  # no home flag

    def test_dir_outside_its_worktree_refused(self):
        # A planted core.worktree aims git at another tree: the seat would read a dir it was not given.
        other = os.path.join(os.path.dirname(self.wt), "other")
        os.makedirs(other)
        git(self.wt, "config", "core.worktree", other)
        r = self.seat()
        self.assertFalse(r.ran)
        self.assertIn("not inside its git worktree", r.reason)

    def test_deny_read_env_adds_denies(self):
        extra = os.path.join(os.path.dirname(self.wt), "prod-state")
        real = grants.seat_grants
        with mock.patch.dict(os.environ, {run.DENY_READ_ENV: f"{extra}{os.pathsep}relative"}), \
                mock.patch.object(grants, "seat_grants", side_effect=real) as spy:
            self.assertTrue(self.seat().ok)
        self.assertEqual(spy.call_args.kwargs["deny_read"], [extra])
        self.assertNotIn(run.DENY_READ_ENV, self.fake.env)  # bw drops BW_* from the seat env

    def test_seat_reads_the_install_its_adapter_names(self):
        # codex links into PATH from a package dir under the home; under nono the seat must read it.
        real = grants.seat_grants
        with mock.patch.object(grants, "seat_grants", side_effect=real) as spy:
            self.assertTrue(self.seat().ok)
        self.assertEqual(spy.call_args.kwargs["installs"], ["codex"])
        self.assertEqual(adapters.CODEX.installs, ["codex"])
        self.assertEqual([n for n, a in adapters.SEAT_ADAPTERS.items() if a.installs], ["codex"])

    def test_control_argv_trusts_project_files_for_pi(self):
        # pi -p loads no project-local file unless trusted: without the ignore flags alone, the control
        # would load nothing and prove nothing. The control adds --approve; the real argv never has it.
        seat = {"runtime": "pi", "model": "m"}
        argv = run.control_argv(seat)
        for flag in adapters.PI_IGNORE:
            self.assertNotIn(flag, argv)
        self.assertEqual(argv[-1:], ["--approve"])
        self.assertNotIn("--approve", adapters.PI.argv(seat, run.INPUT_PLACEHOLDER, False))
        self.assertEqual(adapters.CODEX.control_flags, [])

    def test_without_removes_flags_at_flag_positions_only(self):  # fix 6
        argv = ["claude", "-p", "--model", "user", "--append-system-prompt", "--safe-mode", "--safe-mode",
                "--setting-sources", "user", "--strict-mcp-config", "--setting-sources", "user,project"]
        self.assertEqual(run._without(argv, adapters.CLAUDE_IGNORE),
                         ["claude", "-p", "--model", "user", "--append-system-prompt", "--safe-mode",
                          "--setting-sources", "user,project"])
        self.assertEqual(run._without(["pi", "-p", "--no-skills", "--no-mcp"], adapters.PI_IGNORE), ["pi", "-p"])

    def test_exec_input_path_replaces_placeholder(self):
        verify.issue_token(self.state, "gem", time.time(), 600)
        argv = run.control_argv(self.config["seats"]["gem"])  # agy: the input comes by a file instruction
        self.fake.queue("agy", Result(0, "ok\n", "", False))
        run.exec_sandboxed(self.config, self.state, "gem", self.wt, argv, run=self.fake, input_text="reply ok")
        text = " ".join(self.fake.argv)
        self.assertNotIn(run.INPUT_PLACEHOLDER, text)
        self.assertIn(os.path.join(self.fake.env["TMPDIR"], "input.md"), text)

    def test_exec_entry(self):
        verify.issue_token(self.state, "astra", time.time(), 600)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(run, "exec_sandboxed", return_value=Result(3, "o\n", "e\n", False)) as m, \
                mock.patch.object(run, "load_config", return_value=self.config):
            code = run.main(["exec", "--seat", "astra", "-d", self.wt, "--", "/bin/sh", "-c", "echo x"], out=out, err=err)
        self.assertEqual((code, out.getvalue(), err.getvalue()), (3, "o\n", "e\n"))
        self.assertEqual(m.call_args.args[2:5], ("astra", self.wt, ["/bin/sh", "-c", "echo x"]))
        self.assertEqual(m.call_args.args[1], self.state)
        with mock.patch.object(run, "exec_sandboxed", return_value=Result(0, "", "", False)) as m, \
                mock.patch.object(run, "load_config", return_value=self.config):
            run.main(["exec", "--seat", "opus", "-d", self.wt, "--control", "reply ok"], out=out, err=err)
        self.assertEqual(m.call_args.args[4], run.control_argv(self.config["seats"]["opus"]))
        self.assertEqual(m.call_args.kwargs["input_text"], "reply ok")
        self.assertEqual(run.main(["exec", "--seat", "astra", "-d", self.wt], out=out, err=err), 2)  # no argv

    def test_disabled_unknown(self):
        self.config["seats"]["astra"]["enabled"] = False
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.ran), (75, "disabled", False))
        with self.assertRaises(KeyError):
            self.seat("nope")

    def test_login_missing(self):
        del os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
        r = self.seat("opus")
        self.assertEqual(r.exit_code, 75)
        self.assertEqual(r.reason, adapters.LOGIN_HINT["claude-code"])

    def test_not_a_worktree(self):
        d = os.path.join(self.tmp.name, "plain")
        os.makedirs(d)
        r = self.seat(target=d)
        self.assertEqual((r.exit_code, r.reason), (75, "refused: not a git worktree"))

    def test_timeout(self):
        self.fake.queue("codex", Result(124, "", "", True))
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.ok, r.ran), (124, "timeout", False, True))
        self.assertEqual(self.usage()[-1]["reason"], "timeout")

    def test_hostile_covered_by_ignore_flag_runs(self):  # ruling H-ignore
        Path(self.wt, ".mcp.json").write_text("{}")
        r = self.seat("opus")
        self.assertTrue(r.ok, r.reason)
        self.assertIn("--strict-mcp-config", self.fake.argv)

    def test_hostile_repo_refused(self):  # a file no ignore flag on the argv covers
        os.makedirs(os.path.join(self.wt, ".claude", "commands"))
        r = self.seat("opus")
        self.assertEqual((r.exit_code, r.reason), (75, "hostile: .claude/commands loads code for claude-code"))
        self.assertIsNone(self.fake.argv)

    def test_hostile_refused_without_ignore_flag(self):
        self.config["seats"]["opus"]["args"] = ["--setting-sources", "user,project"]
        self._pass("opus")  # args are in the seat key
        os.makedirs(os.path.join(self.wt, ".claude"))
        Path(self.wt, ".claude", "settings.json").write_text(json.dumps({"hooks": {}}))
        r = self.seat("opus")
        self.assertEqual(r.reason, "hostile: .claude/settings.json loads code for claude-code")

    def test_hostile_codex_always_refused(self):  # codex has no ignore flags
        os.makedirs(os.path.join(self.wt, ".codex"))
        Path(self.wt, ".codex", "config.toml").write_text("x = 1\n")
        self.assertEqual(self.seat().reason, "hostile: .codex/config.toml loads code for codex")

    def test_env_allowlist(self):  # R13
        with mock.patch.dict(os.environ, {"OTHER_API_KEY": "x", "BW_PANEL": "1", "LC_ALL": "C"}):
            self.assertTrue(self.seat("opus").ok)
        env = self.fake.env
        self.assertNotIn("OTHER_API_KEY", env); self.assertNotIn("BW_PANEL", env); self.assertNotIn("BW_STATE_DIR", env)
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok-test")
        self.assertEqual(env["LC_ALL"], "C")
        self.seat()
        self.assertEqual(self.fake.env["CODEX_HOME"], os.path.join(self.state, "seats", "homes", "astra"))

    def test_target_overlaps_state(self):
        inner = os.path.join(self.state, "repo")
        make_repo(inner)
        r = self.seat(target=inner)
        self.assertEqual(r.exit_code, 75)
        self.assertTrue(r.reason.startswith("sandbox: refusing: "), r.reason)

    def test_quote_in_path(self):
        q = os.path.join(self.tmp.name, 'we"ird')
        make_repo(q)
        r = self.seat(target=q)
        self.assertEqual(r.exit_code, 75)
        self.assertTrue(r.reason.startswith("sandbox: refusing a sandbox path "), r.reason)

    def test_busy(self):
        with sessions.run_lock(self.state, "astra", self.wt):
            r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.ran), (75, "busy", False))
        self.assertIsNone(self.fake.argv)

    def test_sandbox_start_failure(self):
        self.fake.queue("codex", Result(71, "", "sandbox-exec: sandbox_apply: Operation not permitted\n", False))
        r = self.seat()
        self.assertEqual((r.exit_code, r.reason, r.ran),
                         (75, "sandbox: sandbox-exec: sandbox_apply: Operation not permitted", False))

    # ---- scratch, output, interrupts ----

    def test_tmpdir_in_scratch_root(self):
        self.seat()
        root = grants.seat_scratch_root()
        for k in ("TMPDIR", "NODE_COMPILE_CACHE"):
            self.assertTrue(self.fake.env[k].startswith(root + "/astra-"), self.fake.env[k])

    def test_scratch_removed(self):
        before = self.scratch_dirs()
        self.seat()
        self.fake.queue("codex", Result(124, "", "", True)); self.seat()
        self.fake.queue("codex", "error"); self.seat()
        self.assertEqual(self.scratch_dirs(), before)

    def test_interrupted(self):
        before = self.scratch_dirs()
        self.fake.raise_ = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            self.seat()
        row = self.usage()[-1]
        self.assertEqual((row["reason"], row["ok"], row["ran"]), ("interrupted", False, True))
        self.assertEqual(self.scratch_dirs(), before)
        self.assertTrue(lock_free(self.state, "codex"))
        self.fake.raise_ = SystemExit(143)
        with mock.patch("seats.run._row", wraps=run._row) as row_of, self.assertRaises(SystemExit):
            self.seat()
        self.assertEqual(self.usage()[-1]["reason"], "interrupted")
        result = row_of.call_args[0][0]
        self.assertEqual((result.reason, result.exit_code), ("interrupted", 143))

    def test_out_and_transcript(self):
        out, tr = os.path.join(self.tmp.name, "answer.md"), os.path.join(self.tmp.name, "t.txt")
        self.seat(out=out, transcript=tr)
        self.assertEqual(Path(out).read_text(), "No significant findings.")
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        d = FIX / "codex"
        self.assertEqual(Path(tr).read_text(), (d / "ok.stdout").read_text() + (d / "ok.stderr").read_text())

    def test_transcript_keeps_the_last_event_on_its_own_line(self):
        tr = os.path.join(self.tmp.name, "t.txt")
        self.fake.queue("codex", Result(0, '{"type": "x"}', "warn\n", False))
        self.seat(transcript=tr)
        self.assertEqual(Path(tr).read_text(), '{"type": "x"}\nwarn\n')

    def test_hostile_refusal_before_the_brief(self):  # no git work in a repo bw refuses
        os.makedirs(os.path.join(self.wt, ".claude", "commands"))
        with mock.patch.object(brief, "build", side_effect=AssertionError("brief built")):
            r = self.seat("opus", brief=True)
        self.assertTrue(r.reason.startswith("hostile:"), r.reason)

    def test_sandbox_line_after_a_real_run_is_not_a_refusal(self):
        self.fake.queue("codex", Result(1, '{"type": "turn.failed", "error": {"message": "boom"}}\n',
                                        "nono: note\n", False))
        r = self.seat()
        self.assertTrue(r.ran)
        self.fake.queue("codex", Result(1, "", "nono: could not apply the profile\n", False))
        r = self.seat()
        self.assertFalse(r.ran)
        self.assertTrue(r.reason.startswith("sandbox: nono"), r.reason)

    def test_seat_process_group_reaped(self):
        self.seat()
        self.assertTrue(self.fake.reap)

    def test_error_reason_redacted(self):
        self.fake.queue("codex", Result(1, "", "error: API_KEY=abc123 bad " + "x" * 300 + "\n", False))
        r = self.seat()
        self.assertTrue(r.reason.startswith("error: "), r.reason)
        self.assertIn("[REDACTED]", r.reason); self.assertNotIn("abc123", r.reason)
        self.assertLessEqual(len(r.reason), 200)

    def test_error_fixture(self):
        self.fake.queue("codex", "error")
        r = self.seat()
        self.assertEqual((r.reason, r.exit_code, r.ok), ("error: stream disconnected before completion", 1, False))
        self.assertIsNone(caps.read(self.state, "astra", time.time()))

    def test_result_json(self):
        r = self.seat()
        j = run.result_json(r)
        self.assertEqual(set(j), {"v", *run.SeatResult._fields} - {"answer"})
        self.assertEqual(j["v"], 1)
        json.dumps(j)


if __name__ == "__main__":
    unittest.main()
