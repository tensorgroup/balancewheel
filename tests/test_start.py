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
from lead import containment, proc, runner, sandbox, start, status  # noqa: E402
from lead.proc import Result  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402

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

    def __init__(self, orca="stopped", create_rc=0, open_rc=0):
        self.orca, self.create_rc, self.open_rc = orca, create_rc, open_rc
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

    def test_orca_running_opens_pane_with_exact_argv(self):
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

    def test_orca_create_failure_falls_back_to_terminal(self):
        run = FakeRun(orca="running", create_rc=1)
        code, out = self.start(run, verify=False, update=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.ran(run, CREATE)), 1)
        self.assertEqual(len(self.ran(run, ["open", "-a", "Terminal"])), 1)
        self.assertIn("orca terminal create failed (exit 1: create refused); opening a Terminal tab", out)

    def test_neither_orca_nor_terminal_prints_the_command_and_exits_1(self):
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

    def test_unresolvable_path_exits_2(self):
        plain = self.root / "plain"; plain.mkdir()
        run = FakeRun()
        code, out = self.start(run, path=plain)
        self.assertEqual(code, 2)
        self.assertIn("not a git repository", out)
        self.assertEqual({a[0] for a in run.argvs()}, {"git"})

    # --- dry run --------------------------------------------------------------------------

    def test_dry_run_runs_nothing_but_git_and_orca_status(self):
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
            self.assertIn("bw start: would check keys for pi: containment", out)
            self.assertIn("bw start: would read quota for plan ps", out)
            self.assertIn("bw start: lead: lp (order)", out)
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
        self.assertIn("bw start: verify pi containment: passed", out)

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
        self.assertIn("bw start: pi: pi 1.0 → pi 1.1 updated (", out)

    def test_unreadable_version_after_update_marks_pending(self):
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", None, 0.0, 0)
        code, out = self.start(run)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: verify pi containment: pending — version unreadable", out)
        self.assertFalse([c for c in self.attacks(run) if "pi" in c[0]])

    def test_version_ok_follows_the_after_version_not_the_result_label(self):
        run = FakeRun()
        run.versions["pi"] = None  # unreadable before: the label is "unreadable", the after version is not
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.1", 0.0, 0)
        code, out = self.start(run)
        self.assertIn("pi: unreadable → pi 1.1 unreadable", out)
        self.assertIn("bw start: verify pi containment: passed", out)
        run = FakeRun()
        run.updates[tuple(PI_UPDATE)] = ("pi", "pi 1.2", 0.0, 3)  # failed, yet a readable version after
        code, out = self.start(run)
        self.assertIn("pi 1.2 failed", out)
        self.assertIn("bw start: verify pi containment: passed", out)

    def test_no_verify_marks_changed_keys_pending(self):
        self.record_pi("pi 0.9")
        run = FakeRun()
        code, out = self.start(run, verify=False)
        self.assertEqual(code, 0, out)
        self.assertIn("bw start: verify pi containment: pending — --no-verify", out)
        self.assertIn("bw start: verify kimi containment: pending — --no-verify", out)
        self.assertEqual(self.attacks(run), [])

    def test_runtime_no_tool_maps_to_prints_no_update_lock_once(self):
        del self.config["tools"]["kimi"]
        run = FakeRun()
        code, out = self.start(run, verify=False)
        self.assertEqual(out.count("bw start: no update lock for runtime kimi"), 1)
        self.assertNotIn("no update lock for runtime pi", out)
        self.assertIn("bw start: verify kimi containment: pending — --no-verify", out)

    def test_quota_and_lead_lines(self):
        run = FakeRun()
        code, out = self.start(run, verify=False, update=False)
        for plan in ("pp", "pk", "ps"):
            self.assertIn(f"bw start: quota {plan}: quota unknown", out)
        self.assertNotIn("quota pc:", out)  # lc is not in this profile
        self.assertIn("bw start: lead: lp (order)", out)
        self.assertIn("bw start: cockpit: run bw cockpit", out)

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
        self.assertIn("bw start: quota po: budget 75% · probe 0m", out)
        self.assertIn("bw start: quota pp: quota unknown — probe: no probe for this plan", out)
        self.assertNotIn("SYNTHETIC-CANARY", out)

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


HOLDER = """
import json, sys, time
sys.path.insert(0, sys.argv[1])
from lead.runner import Lock
lock = Lock(sys.argv[2], sys.argv[3])
assert lock.acquire()
import os
if len(sys.argv) > 4:
    os.ftruncate(lock.fd, 0); os.write(lock.fd, sys.argv[4].encode())
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
