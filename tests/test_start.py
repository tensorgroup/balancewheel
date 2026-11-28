import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import containment, probes, proc, runner, sandbox, start, status  # noqa: E402
from lead.proc import Result  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402
from seats import verify as seat_verify  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "orca"
GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@t"}
NOW = 1_800_000_000.0
PI_UPDATE = ["brew", "upgrade", "pi"]
KIMI_UPDATE = ["uv", "tool", "upgrade", "kimi"]
CODEX_UPDATE = ["npm", "i", "-g", "codex"]
CREATE = ["orca", "terminal", "create"]


class FakeRun:
    """git goes to the real proc.run; everything else is answered here and recorded.

    `versions[x]` answers `[x, "--version"]` (None: exit 1); an update argv in `updates` sets its
    tool's version to `new` after sleeping; `.sh` scripts (the attacks) PASS."""

    def __init__(self, orca="stopped", create_rc=0, open_rc=0, ghostty_rc=0):
        self.orca, self.create_rc, self.open_rc, self.ghostty_rc = orca, create_rc, open_rc, ghostty_rc
        self.ghostty_err = "Ghostty got an error"
        self.versions = {"pi": "pi 1.0", "kimi": "kimi 1.0", "codex": "codex 1.0"}
        self.updates = {}  # tuple(argv) -> (name, new_version, sleep_s, rc)
        self.calls = []  # (argv, start, end)
        self.lock = threading.Lock()

    def __call__(self, argv, timeout, cwd=None, env=None, stdin=None):
        argv = list(argv)
        t0 = time.monotonic()
        res = self.answer(argv)
        with self.lock:
            self.calls.append((argv, t0, time.monotonic()))
        return res

    def answer(self, argv):
        if argv[0] == "git":
            return proc.run(argv, 30)
        if argv[:3] == ["orca", "status", "--json"]:
            if self.orca is None:
                return Result(127, "", "orca: not found", False)
            return Result(0, (FIX / f"status_{self.orca}.json").read_text(), "", False)
        if argv[:3] == CREATE:
            return Result(self.create_rc, "", "create refused" if self.create_rc else "", False)
        if argv[0] == "osascript":
            if self.ghostty_rc == "timeout":
                return Result(-9, "", "", True)
            return Result(self.ghostty_rc, "", self.ghostty_err if self.ghostty_rc else "", False)
        if argv[0] == "open":
            return Result(self.open_rc, "", "open: no Terminal" if self.open_rc else "", False)
        if argv[0].endswith(".sh"):
            return Result(0, "PASS — evidence in /e\n", "", False)
        if tuple(argv) in self.updates:
            name, new, sleep_s, rc = self.updates[tuple(argv)]
            time.sleep(sleep_s)
            self.versions[name] = new
            return Result(rc, "", "boom" if rc else "", False)
        if len(argv) == 2 and argv[1] == "--version":
            v = self.versions.get(argv[0])
            return Result(0, v + "\n", "", False) if v is not None else Result(1, "", "", False)
        return Result(127, "", f"{argv[0]}: not found", False)

    def argvs(self):
        return [c[0] for c in self.calls]

    def ran(self, prefix):
        return [c for c in self.calls if c[0][:len(prefix)] == prefix]


def make_repo(path):
    path.mkdir(parents=True)
    for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *a], check=True, env=GITENV)
    return path


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self.tty = tty

    def isatty(self):
        return self.tty


class StartCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.repo = make_repo(self.root / "app")
        self.state = str(self.root / "state")
        self.config = {
            "plans": {"pp": {"billing": "plan", "quota": "probe"}, "pk": {"billing": "plan", "quota": "probe"},
                      "pc": {"billing": "plan", "quota": "statusline"}, "ps": {"billing": "metered", "quota": "none"}},
            "leads": {"lp": {"runtime": "pi", "model": "m", "plan": "pp"},
                      "lk": {"runtime": "kimi", "model": "k", "plan": "pk"},
                      "lc": {"runtime": "codex", "model": "c", "plan": "pc"}},
            "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"], "plan": "ps"}},
            "tools": {"pi": {"update": PI_UPDATE}, "kimi": {"update": KIMI_UPDATE},
                      "codex": {"update": CODEX_UPDATE}},
            "profiles": {"p": {"lead_order": ["lp", "lk"], "checkpoints": {"review": {"seats": ["s1"]}}}},
            "default_profile": "p", "projects": {},
            "rotation": {"cap_expiry_hours": 5, "return_above_pct": 50, "probe_every_min": 5},
            "updates": {"min_interval_hours": 0}, "state_dir": self.state, "warnings": []}
        self.bw = str(self.root / "my bw" / "bin" / "bw")
        self.env = {"BALANCEWHEEL_CONFIG": "/cfg dir/c.json", "BW_STATE_DIR": self.state}
        p = mock.patch.object(sandbox, "backend", return_value="sandbox-exec")
        p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(seat_verify, "spawn_verify", return_value=True)  # never a real child
        self.spawn = p.start(); self.addCleanup(p.stop)
        self.saved = {k: os.environ.get(k) for k in ("BW_ORCA_SUPPORT", "BW_CCSWITCH_DIR")}
        os.environ["BW_ORCA_SUPPORT"] = str(self.root / "no-orca")
        os.environ["BW_CCSWITCH_DIR"] = str(self.root / "no-cc-switch")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def start(self, run, path=None, **kw):
        out = io.StringIO()
        kw.setdefault("bw_path", self.bw)
        kw.setdefault("env", self.env)
        kw.setdefault("now", NOW)
        code = start.start(self.config, self.state, str(path or self.repo), run=run, out=out, **kw)
        return code, out.getvalue()

    def record_pi(self, version):
        """A containment pass for pi recorded on the key it would have at `version`."""
        r = FakeRun(); r.versions["pi"] = version
        key = containment.verification_key("pi", ADAPTERS["pi"].version_argv, "sandbox-exec", run=r)
        containment.record_pass(self.state, "pi", key, "/e")

    def attacks(self, run):
        return [c for c in run.calls if c[0][0].endswith(".sh")]

    def start_json(self):
        with open(os.path.join(self.state, "start.json")) as f:
            return json.load(f)

    # --- workspace ------------------------------------------------------------------------

    def test_terminal_canvas_ignores_a_running_orca(self):
        os.environ["BW_ORCA_SUPPORT"] = str(self.root)  # an Orca support dir exists
        run = FakeRun(orca="running")
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [])
        self.assertEqual(run.argvs()[-1][:3], ["open", "-a", "Terminal"])
        self.assertNotIn("orca", out.lower())

    def test_terminal_canvas_dry_run_runs_only_git(self):
        run = FakeRun(orca="running")
        code, out = self.start(run, dry_run=True)
        self.assertEqual({a[0] for a in run.argvs()}, {"git"})
        self.assertIn("would run: open -a Terminal", out)

    def test_terminal_canvas_never_reads_the_orca_app_version(self):
        with mock.patch.object(start.orca, "app_version", side_effect=AssertionError("read")):
            code, out = self.start(FakeRun(orca="running"), verify=False, update=False)
        self.assertEqual(code, 0, out)

    def test_the_example_config_makes_no_orca_call(self):
        os.environ["BW_ORCA_SUPPORT"] = str(self.root)  # an Orca support dir exists
        config = json.loads((ROOT / "balancewheel.example.json").read_text())
        config["state_dir"] = self.state
        config["projects"] = {}
        path = self.root / "example.json"
        path.write_text(json.dumps(config))
        from metrics import config as cfg
        example = cfg.load_config(str(path))
        for dry_run in (True, False):
            run = FakeRun(orca="running")
            out = io.StringIO()
            start.start(example, self.state, str(self.repo), run=run, out=out, bw_path=self.bw,
                        env=self.env, now=NOW, dry_run=dry_run, verify=False, update=False)
            self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [], dry_run)

    def test_terminal_canvas_skips_a_configured_orca_tool(self):
        self.config["tools"]["orca"] = {"version": ["orca", "--version"], "update": ["orca", "self-update"]}
        for dry_run in (False, True):
            run = FakeRun(orca="running")
            self.start(run, dry_run=dry_run, verify=False, update=True)
            self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [], dry_run)

    def test_terminal_canvas_with_an_orca_plan_reads_quota_but_opens_terminal(self):
        self.config["plans"]["pp"]["orca_provider"] = "codex"  # pp is lead lp's plan, in profile p
        run = FakeRun(orca="running")
        self.start(run, verify=False, update=False)
        self.assertEqual(self.ran(run, CREATE), [])
        self.assertTrue(self.ran(run, ["orca", "account", "list"]))
        self.assertEqual(run.argvs()[-1][:3], ["open", "-a", "Terminal"])

    def test_orca_running_opens_pane_with_exact_argv(self):
        self.config["canvas"] = "orca"
        run = FakeRun(orca="running")
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        creates = self.ran(run, CREATE)
        self.assertEqual(len(creates), 1)
        cmd = shlex.join(["env", "BALANCEWHEEL_CONFIG=/cfg dir/c.json", f"BW_STATE_DIR={self.state}", self.bw, "lead"])
        self.assertEqual(creates[0][0], ["orca", "terminal", "create", "--worktree", f"path:{self.repo}",
                                         "--title", "app lead", "--command", cmd])
        self.assertEqual(shlex.split(creates[0][0][-1]),
                         ["env", "BALANCEWHEEL_CONFIG=/cfg dir/c.json", f"BW_STATE_DIR={self.state}", self.bw, "lead"])
        self.assertIn(f"'{self.bw}'", cmd)
        self.assertEqual(self.ran(run, ["open"]), [])

    def ran(self, run, prefix):
        return run.ran(prefix)

    def test_orca_absent_writes_command_file_and_opens_terminal(self):
        self.config["canvas"] = "orca"
        repo = make_repo(self.root / "it's a $repo")
        run = FakeRun(orca=None)
        env = {"BALANCEWHEEL_CONFIG": "/cfg dir/it's.json"}  # BW_STATE_DIR unset: no export for it
        code, out = self.start(run, path=repo, verify=False, update=False, env=env)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ran(run, CREATE), [])
        opens = self.ran(run, ["open"])
        self.assertEqual(len(opens), 1)
        key = runner_key(repo)
        path = os.path.join(self.state, "start", f"{key}.command")
        self.assertEqual(opens[0][0], ["open", "-a", "Terminal", path])
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines, ["#!/bin/sh",
                                 "export BALANCEWHEEL_CONFIG=" + shlex.quote("/cfg dir/it's.json"),
                                 f"cd {shlex.quote(str(repo))} || exit 1",
                                 f"exec {shlex.quote(self.bw)} lead"])
        self.assertEqual(subprocess.run(["sh", "-n", path]).returncode, 0)
        r = subprocess.run(["sh", "-c", lines[2].replace(" || exit 1", " && pwd -P")], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), str(repo))

    def test_relative_config_and_state_paths_are_forwarded_absolute(self):
        self.config["canvas"] = "orca"
        # the lead's shell starts in the target worktree, not where bw start ran
        env = {"BALANCEWHEEL_CONFIG": "conf/c.json", "BW_STATE_DIR": "st"}
        cwd = os.getcwd()
        for orca_state in ("running", None):
            run = FakeRun(orca=orca_state, open_rc=1)
            code, out = self.start(run, verify=False, update=False, env=env)
            want = shlex.join(["env", f"BALANCEWHEEL_CONFIG={os.path.join(cwd, 'conf', 'c.json')}",
                               f"BW_STATE_DIR={os.path.join(cwd, 'st')}", self.bw, "lead"])
            if orca_state:
                (create,) = self.ran(run, CREATE)
                self.assertEqual(create[0][-1], want)
            else:
                self.assertIn(want, out)
                with open(os.path.join(self.state, "start", f"{runner_key(self.repo)}.command")) as f:
                    body = f.read()
                self.assertIn("export BALANCEWHEEL_CONFIG=" + shlex.quote(os.path.join(cwd, "conf", "c.json")), body)
                self.assertIn("export BW_STATE_DIR=" + shlex.quote(os.path.join(cwd, "st")), body)

    def test_quota_and_registry_read_a_fresh_clock_after_updates_and_attacks(self):
        ticks = iter([NOW, NOW + 600, NOW + 1200])  # start, before step 4, before step 5
        seen = {}
        real_refresh, real_ql, real_build = probes.refresh, start._quota_and_lead, start.registry.build

        def refresh(config, state, now, **kw):
            seen["refresh"] = now
            return real_refresh(config, state, now, **kw)

        def ql(config, state, project, profile, plans, now, rep):
            seen["decide"] = now
            return real_ql(config, state, project, profile, plans, now, rep)

        def build(config, state, now, **kw):
            seen["registry"] = now
            return real_build(config, state, now, **kw)

        with mock.patch.object(probes, "refresh", refresh), mock.patch.object(start, "_quota_and_lead", ql), \
                mock.patch.object(start.registry, "build", build):
            code, out = self.start(FakeRun(orca="running"), verify=False, update=False, now=None,
                                   clock=lambda: next(ticks))
        self.assertEqual(code, 0, out)
        self.assertEqual(seen, {"refresh": NOW + 600, "decide": NOW + 600, "registry": NOW + 1200})

    def test_a_pinned_lead_outside_the_order_gets_its_plan_refreshed(self):
        self.config["plans"]["px"] = {"billing": "plan", "quota": "probe"}
        self.config["leads"]["lx"] = {"runtime": "codex", "model": "x", "plan": "px"}
        runner.write_pin(self.state, runner_key(self.repo), "lx")
        seen = []
        real = probes.refresh

        def refresh(config, state, now, plans=None, **kw):
            seen.append(plans)
            return real(config, state, now, plans=plans, **kw)
        with mock.patch.object(probes, "refresh", refresh):
            code, out = self.start(FakeRun(orca="running"), verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertIn("px", seen[0])
        self.assertIn("quota px:", out)

    def test_orca_create_failure_falls_back_to_terminal(self):
        self.config["canvas"] = "orca"
        run = FakeRun(orca="running", create_rc=1)
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ran(run, CREATE)), 1)
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertIn("orca terminal create failed (exit 1: create refused); opening a Terminal tab", out)

    def test_ghostty_canvas_opens_a_tab_through_osascript(self):
        self.config["canvas"] = "ghostty"
        run = FakeRun(orca="running")
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        path = os.path.join(self.state, "start", f"{runner_key(self.repo)}.command")
        calls = self.ran(run, ["osascript"])
        self.assertEqual(len(calls), 1)
        argv = calls[0][0]
        want = shlex.join([start._login_shell(), "-l", "-i", "-c", "exec " + shlex.quote(path)])
        self.assertEqual(argv[-2:], [want, str(self.repo)])  # data as run-handler arguments, never script text
        script = argv[1:-2]
        self.assertEqual(script[::2], ["-e"] * (len(script) // 2))
        self.assertNotIn(path, " ".join(script))
        self.assertNotIn(str(self.repo), " ".join(script))
        self.assertIn('tell application "Ghostty"', script)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o700)
        self.assertEqual(self.ran(run, ["open"]), [])
        self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [])
        self.assertIn(f"opened a Ghostty tab for {self.repo}", out)

    def test_ghostty_command_quotes_a_path_with_spaces_and_quotes(self):
        path = "/st ate/it's $x/k.command"
        cmd = start._ghostty_command(path, shell="/bin/zsh")
        self.assertEqual(shlex.split(cmd), ["/bin/zsh", "-l", "-i", "-c", "exec " + shlex.quote(path)])
        self.assertEqual(shlex.split(shlex.split(cmd)[-1]), ["exec", path])
        argv = start._ghostty_argv(path, "/w t", shell="/bin/zsh")
        self.assertEqual(argv[-2:], [cmd, "/w t"])
        self.assertNotIn(path, " ".join(argv[1:-2]))

    def test_login_shell_falls_back_to_bin_sh(self):
        with mock.patch.object(start.pwd, "getpwuid", side_effect=KeyError("uid")):
            self.assertEqual(start._login_shell(), "/bin/sh")

    def test_ghostty_timeout_falls_back_to_terminal(self):
        self.config["canvas"] = "ghostty"
        run = FakeRun(ghostty_rc="timeout")
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertIn("ghostty: could not open a tab (timed out); opening a Terminal tab", out)

    def test_ghostty_known_errors_are_explained(self):
        for err, want in (("60:67: syntax error: Expected end of line. (-2741)", "Ghostty 1.4 or later not found"),
                          ("execution error: Not authorized to send Apple events to Ghostty. (-1743)",
                           "Automation access to Ghostty refused")):
            self.config["canvas"] = "ghostty"
            run = FakeRun(ghostty_rc=1)
            run.ghostty_err = err
            code, out = self.start(run, verify=False, update=False)
            self.assertEqual(code, 0, out)
            self.assertIn(f"ghostty: could not open a tab ({want}", out)

    def test_ghostty_failure_falls_back_to_terminal(self):
        self.config["canvas"] = "ghostty"
        run = FakeRun(ghostty_rc=1)
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ran(run, ["osascript"])), 1)
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertIn("ghostty: could not open a tab (exit 1: Ghostty got an error); opening a Terminal tab", out)

    def test_ghostty_and_terminal_failing_prints_the_command_and_exits_1(self):
        self.config["canvas"] = "ghostty"
        code, out = self.start(FakeRun(ghostty_rc=1, open_rc=1), verify=False, update=False)
        self.assertEqual(code, 1)
        self.assertIn(f"run it yourself: cd {shlex.quote(str(self.repo))} && ", out)

    def test_ghostty_canvas_dry_run_runs_only_git(self):
        self.config["canvas"] = "ghostty"
        run = FakeRun()
        code, out = self.start(run, dry_run=True)
        self.assertEqual(code, 0, out)
        self.assertEqual({a[0] for a in run.argvs()}, {"git"})
        path = os.path.join(self.state, "start", f"{runner_key(self.repo)}.command")
        self.assertIn(f"would open a Ghostty tab: osascript <new-tab script> "
                      f"{shlex.join([start._ghostty_command(path), str(self.repo)])}", out)
        self.assertFalse(os.path.exists(path))

    def test_neither_orca_nor_terminal_prints_the_command_and_exits_1(self):
        self.config["canvas"] = "orca"
        run = FakeRun(orca="stopped", open_rc=1)
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 1)
        cmd = shlex.join(["env", "BALANCEWHEEL_CONFIG=/cfg dir/c.json", f"BW_STATE_DIR={self.state}", self.bw, "lead"])
        self.assertIn(f"run it yourself: cd {shlex.quote(str(self.repo))} && {cmd}", out)

    def test_held_worktree_lock_opens_nothing(self):
        key = runner_key(self.repo)
        child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), self.state, key],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (child.kill(), child.wait(), child.stdout.close()))
        self.assertEqual(child.stdout.readline().strip(), "ready")
        run = FakeRun(orca="running")
        code, out = self.start(run, verify=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.ran(run, CREATE), [])
        self.assertEqual(self.ran(run, ["open"]), [])
        self.assertIn(f"{self.repo} already has a lead: lx (bw pid {child.pid}, lead pid 99, since {time.ctime(1000)})",
                      out)
        self.assertEqual(len(self.ran(run, PI_UPDATE)), 1)  # updates still ran

    def test_malformed_lock_file_never_breaks_the_holder_line(self):
        key = runner_key(self.repo)
        for content in ("[1, 2]", '{"lead": "lx", "started": "soon"}'):
            child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), self.state, key, content],
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                run = FakeRun(orca="running")
                code, out = self.start(run, verify=False, update=False)
                self.assertEqual(code, 0, out)
                self.assertIn(f"bw start: {self.repo} is held by another bw lead; not opening another", out)
                self.assertEqual(self.ran(run, CREATE), [])
            finally:
                child.kill(); child.wait(); child.stdout.close()

    def test_bw_inside_target_worktree_refused(self):
        for bw in (str(self.repo / "bin" / "bw"), str(self.repo / ".git" / "bw" / "bin" / "bw")):
            run = FakeRun(orca="running")
            code, out = self.start(run, bw_path=bw)
            self.assertEqual(code, 2, bw)
            self.assertIn("refusing", out)
            self.assertEqual({a[0] for a in run.argvs()}, {"git"})
        self.assertFalse(os.path.exists(os.path.join(self.state, "start.json")))

    def test_config_inside_target_worktree_refused(self):
        for cfg in (str(self.repo / "bw.json"), str(self.repo / ".git" / "bw.json")):
            run = FakeRun(orca="running")
            self.config["path"] = cfg
            code, out = self.start(run)
            self.assertEqual(code, 2, cfg)
            self.assertIn(f"refusing: the config ({cfg}) lies inside", out)
            self.assertEqual({a[0] for a in run.argvs()}, {"git"})  # no tool update or version read ran from it
        self.assertFalse(os.path.exists(os.path.join(self.state, "start.json")))

    def test_unresolvable_path_exits_2(self):
        plain = self.root / "plain"; plain.mkdir()
        run = FakeRun()
        code, out = self.start(run, path=plain)
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", out)
        self.assertEqual({a[0] for a in run.argvs()}, {"git"})

    # --- dry run --------------------------------------------------------------------------

    def test_dry_run_runs_nothing_but_git_and_orca_status(self):
        self.config["canvas"] = "orca"
        self.config["tools"]["orca"] = {"self_updating": True}
        self.config["tools"]["mystery"] = {"update": ["mystery", "up"]}
        for orca in ("running", "stopped"):
            run = FakeRun(orca=orca)
            code, out = self.start(run, dry_run=True)
            self.assertEqual(code, 0, out)
            for argv in run.argvs():
                self.assertTrue(argv[0] == "git" or argv == ["orca", "status", "--json"], argv)
            self.assertFalse(os.path.exists(self.state), "dry run wrote to the state dir")
            for tool in ("pi", "kimi", "codex", "orca", "mystery"):
                self.assertIn(f"bw start: would update {tool}:" if tool in ("pi", "kimi", "codex")
                              else f"bw start: {tool}:", out)
            self.assertIn("would update pi: brew upgrade pi · lock runtime-pi · installer brew", out)
            self.assertIn("would update codex: npm i -g codex · lock runtime-codex · installer npm", out)
            self.assertIn("bw start: would check pi: sandbox", out)
            self.assertIn("bw start: would read quota for plan ps", out)
            self.assertIn("bw start: lead: lp (first in the profile's lead order)", out)
            if orca == "running":
                self.assertIn("bw start: would run: orca terminal create --worktree", out)
            else:
                self.assertIn(f"would write {self.state}/start/{runner_key(self.repo)}.command:", out)
                self.assertIn("exec ", out)
                self.assertIn("bw start: would run: open -a Terminal", out)

    # --- updates and re-verify ------------------------------------------------------------

    def test_reverify_runs_with_no_update(self):
        self.record_pi("pi 0.9")  # the pass was on an older version: the key changed
        run = FakeRun()
        code, out = self.start(run, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual([c for c in run.argvs() if tuple(c) in {tuple(PI_UPDATE), tuple(KIMI_UPDATE)}], [])
        self.assertTrue(any("lead_containment.sh" in c[0][0] for c in self.attacks(run)))
        self.assertIn("bw start: pi sandbox: passed", out)

    def test_reverify_of_fast_tool_starts_before_slowest_update_ends(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        run.updates[tuple(KIMI_UPDATE)] = ("kimi", "kimi 1.0", 0.0, 0)
        run.updates[tuple(CODEX_UPDATE)] = ("codex", "codex 2.0", 1.0, 0)
        code, out = self.start(run)
        self.assertEqual(code, 0, out)
        codex_end = self.ran(run, CODEX_UPDATE)[0][2]
        pi_attack = [c for c in self.attacks(run) if "--" in c[0] and "pi" in c[0][c[0].index("--") + 1:]]
        self.assertTrue(pi_attack, out)
        self.assertLess(pi_attack[0][1], codex_end)
        self.assertIn("bw start: pi: updated pi 1.0 → pi 1.1", out)

    def test_unreadable_version_after_update_marks_pending(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", None, 0.0, 0)
        code, out = self.start(run)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: pi sandbox: not tested: version unreadable", out)
        self.assertFalse([c for c in self.attacks(run) if "pi" in c[0]])

    def test_version_ok_follows_the_after_version_not_the_result_label(self):
        run = FakeRun()
        run.versions["pi"] = None  # unreadable before: the label is "unreadable", the after version is not
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        code, out = self.start(run)
        self.assertIn("bw start: pi: updated to pi 1.1; the old version could not be read", out)
        self.assertNotIn("cannot be read", out)
        self.assertIn("bw start: pi sandbox: passed", out)
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.2", 0.0, 3)  # failed, yet a readable version after
        code, out = self.start(run)
        self.assertIn("bw start: pi: update failed (exit 3: boom)", out)
        self.assertIn("bw start: pi sandbox: passed", out)

    def test_no_verify_marks_changed_keys_pending(self):
        self.record_pi("pi 0.9")
        run = FakeRun()
        code, out = self.start(run, verify=False)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: pi sandbox: not tested (--no-verify)", out)
        self.assertIn("bw start: kimi sandbox: not tested (--no-verify)", out)
        self.assertEqual(self.attacks(run), [])

    def test_start_runs_the_guard_eval_when_its_key_changed(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        run = FakeRun()
        with mock.patch.object(start.evaluate, "ensure", return_value=("pass", "guard eval pi: pass — fake")) as ens:
            code, out = self.start(run, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual([c.args[2] for c in ens.call_args_list], ["pi"])  # once, for the guarded runtime only
        self.assertEqual(ens.call_args.kwargs["allow_run"], True)
        self.assertIs(ens.call_args.kwargs["run"], run)
        self.assertIn("bw start: guard eval pi: pass — fake", out)

    def test_the_guard_eval_says_it_is_slow_before_the_judge_call_and_only_when_it_will_run(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        out = io.StringIO()
        seen = []

        def ensure(*a, **kw):
            seen.append(out.getvalue())
            return "pass", "guard eval pi: pass — fake"
        for why, expect in (("its guard settings changed since the last evaluation", True), (None, False)):
            seen.clear(); out.seek(0); out.truncate()
            with mock.patch.object(start.evaluate, "ensure", ensure), \
                    mock.patch.object(start.evaluate, "will_run", return_value=why):
                start.start(self.config, self.state, str(self.repo), run=FakeRun(), out=out, bw_path=self.bw,
                            env=self.env, now=NOW, update=False)
            wait = ("bw start: testing the pi guard against the judge model. This takes about a minute and uses a little "
                    "model quota, because its guard settings changed since the last evaluation. Please wait.")
            self.assertEqual(wait in seen[0], expect)  # printed before the judge call, not after
            self.assertEqual("Please wait" in out.getvalue().split("guard eval pi")[0].split("pi guard:")[-1], expect)

    def test_no_interactive_offer_without_a_judge_model(self):  # the check alone cannot lead to promotion
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}}
        stdin, out = Tty("y\n"), Tty()
        with mock.patch.object(start.evaluate, "ensure", return_value=("pass", "guard eval pi: pass")), \
                mock.patch.object(start.interactive, "run_check") as chk:
            start.start(self.config, self.state, str(self.repo), run=FakeRun(), out=out, bw_path=self.bw,
                        env=self.env, now=NOW, update=False, stdin=stdin)
        chk.assert_not_called()
        self.assertNotIn("interactive check", out.getvalue())

    def test_start_offers_the_interactive_check_when_missing(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        stdin, out = Tty("y\n"), Tty()
        with mock.patch.object(start.evaluate, "ensure", return_value=("pass", "guard eval pi: pass")), \
                mock.patch.object(start.interactive, "run_check", return_value=(True, "bw: recorded")) as chk:
            code = start.start(self.config, self.state, str(self.repo), run=FakeRun(), out=out, bw_path=self.bw,
                               env=self.env, now=NOW, update=False, stdin=stdin)
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("run the guard's interactive check now? [y/N] ", out.getvalue())
        self.assertEqual(chk.call_args.args[2:4], ("pi", "lp"))

    def test_start_offers_before_the_workspace_opens(self):
        order = []
        with mock.patch.object(start.offer, "maybe_offer", side_effect=lambda *a, **k: order.append("offer")) as mo, \
                mock.patch.object(start, "_workspace", side_effect=lambda *a, **k: order.append("workspace") or (0, None)):
            code, out = self.start(FakeRun(), update=False)
        self.assertEqual(order, ["offer", "workspace"])
        self.assertEqual(mo.call_args.args[2], "p")

    def test_no_review_offer_with_no_verify(self):
        with mock.patch.object(start.offer, "maybe_offer") as mo:
            self.start(FakeRun(), update=False, verify=False)
        mo.assert_not_called()

    def test_review_offer_failure_never_ends_the_start(self):
        with mock.patch.object(start.offer, "maybe_offer", side_effect=RuntimeError("secret")):
            code, out = self.start(FakeRun(), update=False)
        self.assertEqual(code, 0, out)
        self.assertIn("guard review offer: failed to run (RuntimeError)", out)
        self.assertNotIn("secret", out)

    def test_no_interactive_offer_without_a_tty(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        with mock.patch.object(start.evaluate, "ensure", return_value=("pass", "guard eval pi: pass")), \
                mock.patch.object(start.interactive, "run_check") as chk:
            code, out = self.start(FakeRun(), update=False, stdin=Tty("y\n", tty=False))
        self.assertNotIn("interactive check", out)
        chk.assert_not_called()

    def test_a_recorded_check_is_not_offered_again(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "acting"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        key = start.interactive.check_key("pi", ADAPTERS["pi"])
        containment.record_pass(self.state, "pi", key, "/e", store=start.interactive.STORE)
        stdin, out = Tty("y\n"), Tty()
        with mock.patch.object(start.evaluate, "ensure", return_value=("pass", "x")), \
                mock.patch.object(start.interactive, "run_check") as chk:
            start.start(self.config, self.state, str(self.repo), run=FakeRun(), out=out, bw_path=self.bw,
                        env=self.env, now=NOW, update=False, stdin=stdin)
        chk.assert_not_called()
        self.assertNotIn("interactive check", out.getvalue())

    def test_start_no_verify_passes_allow_run_false_to_the_guard_eval(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        with mock.patch.object(start.evaluate, "ensure", return_value=("pending", "guard eval pi: pending: --no-verify")) as ens:
            code, out = self.start(FakeRun(), verify=False, update=False)
        self.assertEqual(ens.call_args.kwargs["allow_run"], False)
        self.assertIn("bw start: guard eval pi: pending: --no-verify", out)

    def test_a_failing_guard_eval_is_a_line_not_the_end_of_start(self):
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        self.config["guard"] = {"mode": {"p": "auto"}, "model": "test/judge", "key_env": "BW_TEST_UNSET_JUDGE_KEY"}
        with mock.patch.object(start.evaluate, "ensure", side_effect=ValueError("secret text")):
            code, out = self.start(FakeRun(), verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: guard eval pi: failed to run (ValueError)", out)
        self.assertNotIn("secret text", out)

    def test_no_guard_eval_with_no_judge_model_or_a_shadow_pin(self):   # plan 8 follow-up, g17
        self.config["leads"]["lp"].update(autonomy="guard", autonomy_source="config")
        for guard in ({"mode": {"p": "auto"}}, {"mode": {"p": "shadow"}, "model": "test/judge"}):
            self.config["guard"] = guard
            with mock.patch.object(start.evaluate, "ensure") as ens:
                code, out = self.start(FakeRun(), verify=False, update=False)
            ens.assert_not_called()
            self.assertNotIn("guard eval", out)

    def test_runtime_no_tool_maps_to_prints_no_update_lock_once(self):
        del self.config["tools"]["kimi"]
        run = FakeRun()
        code, out = self.start(run, verify=False)
        self.assertEqual(out.count("no tool in your config updates"), 1)
        self.assertIn("bw start: not updated: no tool in your config updates kimi ", out)
        self.assertNotIn("updates pi", out)
        self.assertIn("bw start: kimi sandbox: not tested (--no-verify)", out)

    def test_start_writes_the_registry_with_versions_from_start_json(self):
        run = FakeRun()
        code, out = self.start(run, verify=False)
        with open(os.path.join(self.state, "registry.json")) as f:
            reg = json.load(f)
        self.assertEqual({t["name"] for t in reg["tools"]}, {"pi", "kimi", "codex"})
        self.assertEqual({t["name"]: t["version"] for t in reg["tools"]},
                         {n: e["after"] for n, e in self.start_json()["tools"].items()})
        self.assertIn("bw start: registry: ", out)

    def test_registry_failure_never_ends_the_start(self):
        with mock.patch("lead.registry.build", side_effect=RuntimeError("secret detail")):
            code, out = self.start(FakeRun(), verify=False)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: registry not written (RuntimeError)", out)
        self.assertNotIn("secret detail", out)

    def test_dry_run_writes_no_registry(self):
        self.start(FakeRun(), dry_run=True)
        self.assertFalse(os.path.exists(os.path.join(self.state, "registry.json")))

    def calls_of(self, run, **kw):
        order = []
        with mock.patch.object(seat_verify, "changed_seats", side_effect=lambda *a, **k: order.append("changed")), \
                mock.patch.object(seat_verify, "seat_key", side_effect=lambda *a, **k: order.append("key")), \
                mock.patch.object(start, "_workspace", side_effect=lambda *a, **k: order.append("workspace") or (0, None)), \
                mock.patch.object(seat_verify, "spawn_verify",
                                  side_effect=lambda *a, **k: order.append("spawn") or True):
            code, out = self.start(run, update=False, **kw)
        return order, out

    def test_lead_opens_before_seat_verify(self):
        run = FakeRun()
        order, out = self.calls_of(run)
        self.assertEqual(order, ["workspace", "spawn"])  # no changed_seats, no seat_key on the start path
        self.assertEqual([c for c in run.calls if "seat_readonly" in c[0][0]], [])  # no seat attack

    def test_spawn_line(self):
        _, out = self.calls_of(FakeRun())
        self.assertIn("bw start: seats: checking in the background. Log: ", out)
        self.assertIn("verify-log.txt", out)

    def test_seat_verify_child_gets_start_env(self):
        # start() may run with an env other than os.environ: the child must read the same config.
        self.start(FakeRun(), update=False)
        self.assertEqual(self.spawn.call_args.kwargs["env"],
                         {"BALANCEWHEEL_CONFIG": "/cfg dir/c.json", "BW_STATE_DIR": os.path.abspath(self.state)})

    def test_already_running(self):
        with mock.patch.object(seat_verify, "spawn_verify", return_value=False):
            _, out = self.start(FakeRun(), update=False)
        self.assertIn("bw start: seats: a verify is already running", out)

    def test_no_verify(self):
        with mock.patch.object(seat_verify, "spawn_verify") as sp:
            _, out = self.start(FakeRun(), update=False, verify=False)
        self.assertIn("bw start: seats: not checked (--no-verify)", out)
        sp.assert_not_called()

    def test_dry_run_reads_no_seat_keys(self):
        with mock.patch.object(seat_verify, "changed_seats") as ch, \
                mock.patch.object(seat_verify, "seat_key") as key, \
                mock.patch.object(seat_verify, "spawn_verify") as sp:
            _, out = self.start(FakeRun(), dry_run=True)
        ch.assert_not_called()
        key.assert_not_called()
        sp.assert_not_called()
        self.assertIn("bw start: would check seats in the background", out)

    def test_quota_and_lead_lines(self):
        run = FakeRun()
        code, out = self.start(run, verify=False, update=False)
        for plan in ("pp", "pk", "ps"):
            self.assertIn(f"bw start: quota {plan}: unknown (bw has no reader for this plan yet)", out)
        self.assertNotIn("quota pc:", out)  # lc is not in this profile
        self.assertIn("bw start: lead: lp (first in the profile's lead order)", out)
        self.assertIn("bw start: cockpit: run `bw cockpit` to see all of this in a browser", out)

    def openrouter_plan(self):
        self.config["plans"]["po"] = {"billing": "metered", "quota": "probe", "probe": "openrouter",
                                      "budget_usd_month": 20}
        self.config["seats"]["s2"] = {"runtime": "pi", "model": "y", "roles": ["review"], "plan": "po"}
        self.config["profiles"]["p"]["checkpoints"]["review"]["seats"].append("s2")
        body = (ROOT / "tests" / "fixtures" / "openrouter" / "key_ok.json").read_bytes()
        calls = []

        def fetch(url, key, timeout):
            calls.append(url)
            return 200, body, ""
        return fetch, calls

    def test_probes_refreshed_and_lines_show_source_and_age(self):
        fetch, calls = self.openrouter_plan()
        code, out = self.start(FakeRun(), verify=False, update=False, fetch=fetch,
                               env=dict(self.env, OPENROUTER_API_KEY="sk-or-SYNTHETIC-CANARY"))
        self.assertEqual(code, 0, out)
        self.assertEqual(len(calls), 1)
        self.assertIn("bw start: quota po: 25% of the monthly budget used · read just now from OpenRouter", out)
        self.assertIn("bw start: quota pp: unknown (bw has no reader for this plan yet)", out)
        self.assertNotIn("SYNTHETIC-CANARY", out)

    def test_a_refresh_that_raises_is_one_line_and_start_continues(self):
        with mock.patch.object(probes, "refresh", side_effect=RuntimeError("boom secret detail")):
            code, out = self.start(FakeRun(), verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("quota refresh failed"), 1, out)
        self.assertIn("bw start: quota refresh failed (RuntimeError); deciding on what is on disk", out)
        self.assertNotIn("boom secret detail", out)
        self.assertIn("bw start: lead:", out)

    def test_dry_run_probes_nothing(self):
        fetch, calls = self.openrouter_plan()
        code, out = self.start(FakeRun(), verify=False, update=False, fetch=fetch, dry_run=True,
                               env=dict(self.env, OPENROUTER_API_KEY="k"))
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.state, "quota")))

    def test_start_clears_an_expired_cap_and_a_dry_run_does_not(self):
        from lead import quota
        quota.record_cap(self.state, "pp", NOW - 7 * 3600, resets_at=NOW - 6 * 3600)  # past its 5 h expiry window
        caps = os.path.join(self.state, "caps", "pp.json")
        code, out = self.start(FakeRun(), verify=False, update=False, dry_run=True)
        self.assertTrue(os.path.exists(caps), out)
        code, out = self.start(FakeRun(), verify=False, update=False)
        self.assertFalse(os.path.exists(caps), out)

    def test_start_json_written(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        code, out = self.start(run, verify=False)
        self.assertEqual(code, 0, out)
        doc = self.start_json()
        self.assertEqual(set(doc), {"ts", "project", "tools", "verify", "orca_version"})
        self.assertEqual(doc["ts"], NOW)
        self.assertEqual(doc["project"], str(self.repo))
        self.assertEqual(doc["tools"]["pi"]["result"], "updated")
        self.assertEqual(doc["tools"]["pi"]["last_update_at"], NOW)
        self.assertEqual(set(doc["tools"]), {"pi", "kimi", "codex"})
        self.assertIn({"runtime": "pi", "kind": "containment", "state": "pending", "reason": "--no-verify"},
                      doc["verify"])

    def test_no_update_keeps_tool_history_and_the_interval_holds(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        self.start(run, verify=False)
        self.assertEqual(self.start_json()["tools"]["pi"]["last_update_at"], NOW)
        code, out = self.start(FakeRun(), verify=False, update=False, now=NOW + 60)
        self.assertEqual(code, 0, out)
        doc = self.start_json()
        self.assertEqual(doc["ts"], NOW + 60)
        self.assertEqual(doc["tools"]["pi"]["last_update_at"], NOW)
        self.config["updates"]["min_interval_hours"] = 1
        run = FakeRun()
        code, out = self.start(run, verify=False, now=NOW + 120)
        self.assertEqual(self.ran(run, PI_UPDATE), [])
        self.assertEqual(self.start_json()["tools"]["pi"]["result"], "skipped-interval")

    def test_status_shows_last_start(self):
        self.start(FakeRun(), verify=False, update=False)
        f = status.facts(self.config, self.state, NOW, run=FakeRun())
        self.assertIn(f"last start: {time.ctime(NOW)}", status.render(f))

    # --- the terminal layout: sections with marks on a terminal, plain `bw start: ` lines elsewhere ---

    def tty_start(self, run, **kw):
        out = Tty()
        kw.setdefault("bw_path", self.bw)
        kw.setdefault("env", self.env)
        kw.setdefault("now", NOW)
        kw.setdefault("width", 88)
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            code = start.start(self.config, self.state, str(self.repo), run=run, out=out, **kw)
        return code, out.getvalue()

    def test_sections_come_in_order_under_a_header_with_the_home_path_as_tilde(self):
        code, out = self.tty_start(FakeRun(orca="running"))
        self.assertEqual(code, 0, out)
        lines = out.splitlines()
        self.assertEqual(lines[0], "bw start · ~/app")
        self.assertEqual([ln for ln in lines[1:] if ln and not ln.startswith(" ")],
                         ["Updates", "Checks", "Quota", "Lead", "Also"])
        self.assertFalse([ln for ln in lines if ln.startswith("bw start:")], out)
        self.assertIn("  ✓ lp (first in the profile's lead order)", lines)
        self.assertIn("  ✓ opened a Terminal tab for ~/app", lines)
        self.assertTrue(any(ln.startswith("  · seats: checking in the background. Log: ~/state/seats/") for ln in lines), out)
        self.assertIn("  · cockpit: run `bw cockpit` to see all of this in a browser", lines)
        self.assertTrue(any(ln.startswith("  · registry: ~/state/registry.json") for ln in lines), out)

    def test_updates_say_what_will_take_time_then_each_tool_in_plain_words(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        run.updates[tuple(KIMI_UPDATE)] = ("kimi", "kimi 1.0", 0.0, 7)
        code, out = self.tty_start(run)
        lines = out.splitlines()
        self.assertIn("  … updating pi, kimi, codex. This can take a minute.", lines)
        self.assertIn("  ✓ pi: updated pi 1.0 → pi 1.1", lines)
        self.assertIn("  ✗ kimi: update failed (exit 7: boom)", lines)
        self.assertIn("  ✗ codex: update failed (exit 127: npm: not found)", lines)
        self.assertLess(lines.index("  … updating pi, kimi, codex. This can take a minute."), lines.index("  ✓ pi: updated pi 1.0 → pi 1.1"))

    def test_a_config_with_no_update_commands_gets_one_line(self):
        self.config["tools"] = {}
        for tty in (True, False):
            code, out = (self.tty_start(FakeRun()) if tty else self.start(FakeRun()))
            want = 'skipped: your config lists no update commands (see "tools" in docs/leads.md)'
            self.assertEqual(out.count(want), 1, out)
            self.assertNotIn("no update lock", out)
            self.assertIn(("  · " if tty else "bw start: ") + want, out.splitlines())

    def test_the_wait_notice_is_printed_before_the_attack_starts_and_says_why(self):
        outs = []

        class Watching(FakeRun):
            def answer(inner, argv):
                if argv[0].endswith(".sh"):
                    outs.append(buf.getvalue())
                return super().answer(argv)
        buf = Tty()
        run = Watching()
        self.record_pi("pi 0.9")  # recorded on another version: pi's key changed
        with mock.patch.dict(os.environ, {"HOME": str(self.root)}):
            start.start(self.config, self.state, str(self.repo), run=run, out=buf, bw_path=self.bw, env=self.env,
                        now=NOW, update=False, width=100)
        self.assertEqual(len(outs), 2)  # pi and kimi each ran an attack
        wait_pi = ("  … testing the pi lead's sandbox. This takes about a minute and uses a little model quota, because "
                   "pi's version changed since its last test. Please wait.")
        wait_kimi = wait_pi.replace("pi's version changed since its last test", "kimi's sandbox has not been tested yet").replace("the pi", "the kimi")
        flat = [" ".join(o.split()) for o in outs]  # the lines wrap at the width
        wait_pi, wait_kimi = " ".join(wait_pi[4:].split()), " ".join(wait_kimi[4:].split())
        self.assertTrue(all(wait_pi in o or wait_kimi in o for o in flat), flat)
        self.assertTrue(any(wait_pi in o for o in flat) and any(wait_kimi in o for o in flat))
        text = buf.getvalue()
        self.assertEqual(text.count("Please wait."), 2)

    def test_the_wait_notice_names_a_tool_that_was_just_updated(self):
        self.record_pi("pi 1.0")
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        code, out = self.tty_start(run)
        self.assertIn("because pi was just updated. Please wait.", " ".join(out.split()))

    def test_nothing_slow_means_no_wait_notice(self):
        code, out = self.tty_start(FakeRun(), verify=False, update=False)
        self.assertNotIn("Please wait", out)
        self.assertNotIn("…", out)
        self.assertNotIn("updating", out)
        code, plain = self.start(FakeRun(), verify=False, update=False)
        self.assertNotIn("Please wait", plain)

    def test_a_failed_attack_names_its_evidence(self):
        class Failing(FakeRun):
            def answer(self, argv):
                if argv[0].endswith(".sh"):
                    return Result(1, "FAIL: outside-written:x — evidence in /ev/guard.1\n", "", False)
                return super().answer(argv)
        code, out = self.tty_start(Failing(), update=False)
        self.assertIn("  ✗ pi sandbox: failed: the containment attack failed. Evidence: /ev/guard.1", out.splitlines())

    def test_quota_rows_are_aligned_and_in_plain_words(self):
        self.config["plans"]["pp"]["probe"] = "openrouter"
        self.config["plans"]["pp"]["budget_usd_month"] = 100
        body = (ROOT / "tests" / "fixtures" / "openrouter" / "key_ok.json").read_bytes()
        code, out = self.tty_start(FakeRun(), verify=False, update=False, fetch=lambda u, k, t: (200, body, ""),
                                   env=dict(self.env, OPENROUTER_API_KEY="k"))
        rows = out.split("\nQuota\n")[1].split("\n\n")[0].splitlines()
        self.assertTrue(rows[0].startswith("  pp  "), rows)
        self.assertIn("of the monthly budget used · read just now from OpenRouter", rows[0])
        self.assertTrue(rows[1].startswith("  pk  unknown (bw has no reader for this plan yet)"), rows)
        self.assertTrue(rows[2].startswith("      using only"), rows)  # a wrapped row hangs under its text
        for word in ("probe", "Task G", "observed caps", "statusline"):
            self.assertNotIn(word, out)

    def test_a_pipe_gets_plain_prefixed_lines_with_no_marks_headers_or_tilde(self):
        for dry in (False, True):
            code, out = self.start(FakeRun(), dry_run=dry)
            for ln in out.splitlines():
                self.assertTrue(ln.startswith("bw start: "), ln)
            for mark in "✓✗…":
                self.assertNotIn(mark, out)
            self.assertNotIn("\nUpdates", "\n" + out)
            self.assertIn(str(self.repo), out)

    def test_only_a_warning_is_a_failure_in_the_also_section(self):
        from lead.checks import Check
        cases = [("unknown", "  · cc-switch: unknown — not installed (Usage view off)"),
                 ("warn", "  ✗ cc-switch: warn — off"), ("ok", "  · cc-switch: ok")]
        for state, want in cases:
            with mock.patch.object(start.ccswitch, "check_settings", return_value=[Check("cc-switch", state, "not installed (Usage view off)" if state == "unknown" else "off")]):
                code, out = self.tty_start(FakeRun(), verify=False, update=False)
            self.assertIn(want, out.splitlines(), state)

    def test_commands_the_user_may_paste_keep_their_absolute_paths(self):
        code, out = self.tty_start(FakeRun(), dry_run=True)
        flat = " ".join(out.split())
        self.assertTrue(any(ln.startswith("  · would run: open -a Terminal " + self.state + "/start/") for ln in out.splitlines()), out)  # not ~/state (HOME is the temp root)
        run = FakeRun(open_rc=1)
        code, out = self.tty_start(run, verify=False, update=False)
        self.assertTrue(any(f"run it yourself: cd {shlex.quote(str(self.repo))} && " in ln and ln.endswith(" lead") for ln in out.splitlines()), out)

    def test_dry_run_on_a_terminal_uses_the_layout(self):
        code, out = self.tty_start(FakeRun(), dry_run=True)
        lines = out.splitlines()
        self.assertEqual(lines[:2], ["bw start · ~/app", "  dry run (profile p): nothing is run or written"])
        self.assertEqual([ln for ln in lines[2:] if ln and not ln.startswith(" ")],
                         ["Updates", "Checks", "Quota", "Lead", "Also"])
        self.assertIn("  · would check pi: sandbox", lines)
        self.assertIn("  · would read quota for plan ps", lines)
        self.assertTrue(any(ln.startswith("  · would write ~/state/start/") for ln in lines), out)
        self.assertIn("      exec " + shlex.quote(self.bw) + " lead", lines)

    def test_a_worktree_that_has_a_lead_prints_one_line_and_no_lead_line(self):
        child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), self.state, runner_key(self.repo)],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        code, out = self.tty_start(FakeRun(), verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertIn("not opening another", out)
        self.assertNotIn("first in the profile", out)

    # --- start the lead in this terminal -------------------------------------------------------

    def here_start(self, run=None, dry_run=False, cwd=None, path=None, stdin_tty=True, out_tty=True, **kw):
        """`bw start` as a person at a terminal: stdin and stdout are terminals, cwd is `cwd`."""
        calls = {"chdir": [], "execve": []}
        out, stdin = Tty(tty=out_tty), Tty(tty=stdin_tty)
        kw.setdefault("verify", False)
        kw.setdefault("update", False)
        code = start.start(self.config, self.state, path, run=run or FakeRun(), out=out, bw_path=self.bw, env=self.env,
                           now=NOW, dry_run=dry_run, stdin=stdin, cwd=str(cwd or self.repo), width=88,
                           chdir=lambda d: calls["chdir"].append(d),
                           execve=lambda prog, argv, env: calls["execve"].append((prog, argv, env)), **kw)
        return code, out.getvalue(), calls

    def test_no_path_in_the_current_worktree_starts_the_lead_in_this_terminal(self):
        run = FakeRun()
        code, out, calls = self.here_start(run)
        self.assertEqual(code, 0, out)
        self.assertEqual(calls["chdir"], [str(self.repo)])
        (prog, argv, env), = calls["execve"]
        self.assertEqual((prog, argv), (self.bw, [self.bw, "lead"]))
        self.assertEqual((env["BALANCEWHEEL_CONFIG"], env["BW_STATE_DIR"]), ("/cfg dir/c.json", self.state))
        self.assertEqual(self.ran(run, ["open"]), [])  # no new tab
        self.assertIn("  ✓ lp (first in the profile's lead order), starting here", out.splitlines())
        self.assertNotIn("opened a", out)

    def test_the_exec_is_the_last_thing_start_does(self):
        out, seen = Tty(), []
        code = start.start(self.config, self.state, None, run=FakeRun(), out=out, bw_path=self.bw, env=self.env, now=NOW,
                           verify=True, update=False, stdin=Tty(), cwd=str(self.repo), width=88, chdir=lambda d: None,
                           execve=lambda prog, argv, env: seen.append(out.getvalue()))
        (at_exec,) = seen
        for part in ("seats: checking in the background", "cockpit: run `bw cockpit`", "registry:", "starting here"):
            self.assertIn(part, at_exec)  # everything was printed before the process was replaced
        self.assertTrue(os.path.exists(os.path.join(self.state, "start.json")))
        self.assertEqual(self.spawn.call_count, 1)  # the seat check was started before the exec

    def test_the_exec_env_is_resolved_against_the_original_cwd_before_the_chdir(self):
        sub = self.repo / "pkg"
        sub.mkdir()
        (sub / "conf").mkdir()
        (sub / "conf" / "c.json").write_text("{}")
        seen, cwd0 = [], os.getcwd()
        self.addCleanup(os.chdir, cwd0)
        os.chdir(sub)  # a real chdir from a subdirectory, with relative paths
        env = {"BALANCEWHEEL_CONFIG": "conf/c.json", "BW_STATE_DIR": "../st"}
        real_state = str(self.repo / "st")
        out = Tty()
        code = start.start(self.config, real_state, None, run=FakeRun(), out=out, bw_path=self.bw, env=env, now=NOW,
                           verify=False, update=False, stdin=Tty(), width=88,
                           execve=lambda prog, argv, e: seen.append((os.getcwd(), e)))
        self.assertEqual(code, 0, out.getvalue())
        (cwd_at_exec, e), = seen
        self.assertEqual(os.path.realpath(cwd_at_exec), str(self.repo))
        self.assertEqual(os.path.realpath(e["BALANCEWHEEL_CONFIG"]), str(sub / "conf" / "c.json"))
        self.assertEqual(os.path.realpath(e["BW_STATE_DIR"]), str(self.repo / "st"))

    def test_the_exec_fallback_command_is_built_before_the_chdir(self):
        sub = self.repo / "pkg"
        sub.mkdir()
        cwd0 = os.getcwd()
        self.addCleanup(os.chdir, cwd0)
        os.chdir(sub)
        out = Tty()

        def boom(prog, argv, e):
            raise OSError(2, "gone")
        start.start(self.config, str(self.repo / "st"), None, run=FakeRun(), out=out, bw_path=self.bw,
                    env={"BW_STATE_DIR": "../st"}, now=NOW, verify=False, update=False, stdin=Tty(), width=500, execve=boom)
        (line,) = [ln for ln in out.getvalue().splitlines() if "run it yourself" in ln]
        self.assertIn(f"BW_STATE_DIR={self.repo / 'st'} ", line.replace("'", ""))

    def test_foreground_helper_compares_the_terminal_group_with_ours(self):
        class Stream:
            def fileno(self):
                return 7
        self.assertTrue(start._foreground(Stream(), tcgetpgrp=lambda fd: 100, getpgrp=lambda: 100))
        self.assertFalse(start._foreground(Stream(), tcgetpgrp=lambda fd: 200, getpgrp=lambda: 100))  # `bw start &`
        def gone(fd):
            raise OSError(25, "not a tty")
        self.assertFalse(start._foreground(Stream(), tcgetpgrp=gone, getpgrp=lambda: 100))
        self.assertTrue(start._foreground(Tty()))  # a stream with no descriptor (a test's) cannot be in the background

    def test_a_background_start_never_takes_the_terminal(self):
        run = FakeRun()
        out, calls = Tty(), []
        code = start.start(self.config, self.state, None, run=run, out=out, bw_path=self.bw, env=self.env, now=NOW,
                           verify=False, update=False, stdin=Tty(), cwd=str(self.repo), width=88,
                           foreground=lambda stream: False, chdir=calls.append, execve=lambda *a: calls.append(a))
        self.assertEqual(calls, [])
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)  # the tab, as for any other start
        self.assertNotIn("starting here", out.getvalue())

    def test_a_wait_from_a_runtime_with_no_update_tool_does_not_open_checks_above_updates(self):
        del self.config["tools"]["kimi"]  # kimi has no update tool: its check starts at once
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.3, 0)
        code, out = self.tty_start(run)
        heads = [ln for ln in out.splitlines() if ln and not ln.startswith(" ")]
        self.assertEqual(heads[1], "Updates", heads)
        self.assertLess(heads.index("Updates"), heads.index("Checks"))
        self.assertEqual(heads.count("Updates"), 1)

    def test_failure_lines_keep_a_spaced_evidence_path_and_do_not_double_the_word_failed(self):
        class Failing(FakeRun):
            def answer(self, argv):
                if argv[0].endswith(".sh"):
                    return Result(1, "FAIL: outside-written:x — evidence in /tmp/with space/guard.1\n", "", False)
                return super().answer(argv)
        code, out = self.start(Failing(), update=False)
        self.assertIn("bw start: pi sandbox: failed: the containment attack failed. Evidence: /tmp/with space/guard.1\n", out)
        start._item_line(rep := start.Report(io.StringIO(), tty=False), start.verifymod.Item("pi", "guard", "failed", "bw: " + start.verifymod.KEY_CHANGED))
        self.assertEqual(rep.out.getvalue(), "bw start: pi guard: failed: key changed during the attack\n")

    def test_lead_reason_words_fit_the_profile(self):
        d = start.Decision("lp", "quota-low", "")
        self.assertEqual(start._lead_reason(d, {"lead_order": ["lp"]}), "every lead's quota is low; this one is first in the order")
        self.assertEqual(start._lead_reason(d, {"lead_order": ["lp", "lk"]}), "every lead's quota is low; this one is first in the order")
        self.assertEqual(start._lead_reason(start.Decision("lk", "quota-low", ""), {"lead_order": ["lp", "lk"]}),
                         "an earlier lead's quota is low")
        self.assertEqual(start._lead_reason(start.Decision("lk", "capped", ""), {"lead_order": ["lp", "lk"]}),
                         "an earlier lead's plan is capped")
        self.assertEqual(start._lead_reason(start.Decision("lp", "pinned", ""), {}), "pinned to this worktree")

    def test_tool_line_for_an_unreadable_version_says_which_one(self):
        from lead.updates import ToolResult
        for res, want in ((ToolResult("pi", "pi", "unreadable", "pi 1.6.1", "unreadable", "version unreadable before update", 0.1),
                           "pi: updated to pi 1.6.1; the old version could not be read"),
                          (ToolResult("pi", "pi", "pi 1.0", "unreadable", "unreadable", "version unreadable after update", 0.1),
                           "pi: updated, but the new version cannot be read (version unreadable after update)")):
            rep = start.Report(io.StringIO(), tty=False)
            start._tool_line(rep, res)
            self.assertEqual(rep.out.getvalue(), f"bw start: {want}\n")

    def test_tab_forces_the_canvas_even_in_the_current_worktree(self):
        run = FakeRun()
        code, out, calls = self.here_start(run, tab=True)
        self.assertEqual(calls["execve"], [])
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertNotIn("starting here", out)
        code, out, calls = self.here_start(FakeRun(), dry_run=True, tab=True)
        self.assertIn("would run: open -a Terminal", out)
        self.assertNotIn("would start the lead here", out)

    def test_a_path_inside_the_same_worktree_counts(self):
        sub = self.repo / "pkg"
        sub.mkdir()
        for path in (None, str(self.repo), str(sub)):
            code, out, calls = self.here_start(path=path, cwd=sub)
            self.assertEqual(len(calls["execve"]), 1, path)
            self.assertEqual(calls["chdir"], [str(self.repo)], path)

    def test_another_path_opens_a_tab(self):
        other = make_repo(self.root / "other")
        run = FakeRun()
        code, out, calls = self.here_start(run, path=str(other))
        self.assertEqual(calls["execve"], [])
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertNotIn("starting here", out)

    def test_cwd_in_another_worktree_opens_a_tab(self):
        other = make_repo(self.root / "other")
        run = FakeRun()
        code, out, calls = self.here_start(run, cwd=other, path=str(self.repo))
        self.assertEqual(calls["execve"], [])
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)

    def test_not_in_a_git_repository_opens_a_tab(self):
        plain = self.root / "plain"
        plain.mkdir()
        run = FakeRun()
        code, out, calls = self.here_start(run, cwd=plain, path=str(self.repo))
        self.assertEqual(calls["execve"], [])
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)

    def test_without_a_terminal_on_stdin_or_stdout_it_opens_a_tab(self):
        for stdin_tty, out_tty in ((False, True), (True, False), (False, False)):
            run = FakeRun()
            code, out, calls = self.here_start(run, stdin_tty=stdin_tty, out_tty=out_tty)
            self.assertEqual(calls["execve"], [], (stdin_tty, out_tty))
            self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1, (stdin_tty, out_tty))

    def test_a_worktree_that_has_a_lead_is_not_started_again_here(self):
        child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), self.state, runner_key(self.repo)],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        code, out, calls = self.here_start()
        self.assertEqual(code, 0, out)
        self.assertEqual(calls["execve"], [])
        self.assertIn("not opening another", " ".join(out.split()))
        self.assertNotIn("starting here", out)

    def test_a_refused_start_never_execs(self):
        code, out, calls = self.here_start(path=str(self.root / "nowhere"))
        self.assertEqual(code, 2)
        self.assertEqual(calls["execve"], [])

    def test_an_exec_that_fails_says_how_to_run_it_by_hand(self):
        def boom(prog, argv, env):
            raise OSError(2, "No such file")
        out, stdin = Tty(), Tty()
        code = start.start(self.config, self.state, None, run=FakeRun(), out=out, bw_path=self.bw, env=self.env, now=NOW,
                           verify=False, update=False, stdin=stdin, cwd=str(self.repo), width=88,
                           chdir=lambda d: None, execve=boom)
        self.assertEqual(code, 1)
        self.assertIn("could not start the lead here (FileNotFoundError", out.getvalue())
        (line,) = [ln for ln in out.getvalue().splitlines() if "run it yourself" in ln]  # one line, never wrapped
        self.assertIn(f"run it yourself: cd {shlex.quote(str(self.repo))} && env ", line)
        self.assertTrue(line.endswith(" lead"), line)

    def test_dry_run_says_what_it_would_start_here(self):
        run = FakeRun()
        code, out, calls = self.here_start(run, dry_run=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(calls["execve"], [])
        cmd = shlex.join(["env", "BALANCEWHEEL_CONFIG=/cfg dir/c.json", f"BW_STATE_DIR={self.state}", self.bw, "lead"])
        self.assertIn(f"  · would start the lead here: {cmd}", out.splitlines())  # one literal line, never wrapped
        self.assertNotIn("starting here", out)  # a dry run starts nothing
        self.assertNotIn(".command", out)
        self.assertEqual({a[0] for a in run.argvs()}, {"git"})
        self.assertFalse(os.path.exists(self.state))

    def test_dry_run_in_a_pipe_keeps_the_tab_plan(self):
        code, out, calls = self.here_start(FakeRun(), dry_run=True, out_tty=False)
        self.assertIn("bw start: would run: open -a Terminal", out)
        self.assertNotIn("would start the lead here", out)

    def test_plain_here_start_says_so_with_the_prefix(self):
        out = io.StringIO()
        out.isatty = lambda: True  # a terminal on stdout, plain layout asked for
        calls = []
        code = start.start(self.config, self.state, None, run=FakeRun(), out=out, bw_path=self.bw, env=self.env, now=NOW,
                           verify=False, update=False, stdin=Tty(), cwd=str(self.repo), tty=False,
                           chdir=lambda d: None, execve=lambda *a: calls.append(a))
        self.assertEqual(len(calls), 1)
        self.assertIn("bw start: lead: lp (first in the profile's lead order), starting here", out.getvalue())


HOLDER = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from lead.runner import Lock
lock = Lock(sys.argv[2], sys.argv[3])
assert lock.acquire()
import os
if len(sys.argv) > 4:
    with open(lock.path, "w") as f:  # lock.fd is read-only: raw content goes through its own open
        f.write(sys.argv[4])
else:
    lock.write({"lead": "lx", "pid": os.getpid(), "child": 99, "started": 1000})
print("ready", flush=True)
time.sleep(60)
"""


def runner_key(repo):
    from lead.projects import worktree_key
    return worktree_key(str(repo))


if __name__ == "__main__":
    unittest.main()
