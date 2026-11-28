"""`bw setup claude-pane` and the live pane check at Claude Code launch (lead/claude_pane.py).

Every call goes to a recorder that answers from a table and keeps the state it changes; no test runs a
real `claude`. BW_CLAUDE_CMD names a binary that does not exist, so a call that slipped past the recorder
would fail, never reach the user's Claude config."""
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import claude_pane as cp, version  # noqa: E402
from lead.proc import Result  # noqa: E402

FAKE_CMD = ["/nonexistent/bw-test-claude"]
OURS = {"name": "balancewheel", "source": "directory", "path": cp.ROOT, "installLocation": cp.ROOT}
FOREIGN = {"name": "balancewheel", "source": "directory", "path": "/elsewhere", "installLocation": "/elsewhere"}


def entry(ver=None, enabled=True):
    return {"id": cp.PLUGIN_ID, "version": ver or version.VERSION, "scope": "user", "enabled": enabled}


class Recorder:
    """A `run` for lead.claude_pane: records each argv (BW_CLAUDE_CMD stripped), answers the read-only
    checks from its state, and applies the changing steps to that state. `slow_s` makes each call take
    that long (cut short at its timeout); `timeout` makes every call time out at once. A call in
    `interrupt` sends this process SIGINT (Ctrl-C at the terminal) and waits for it to land."""

    def __init__(self, version="2.1.332 (Claude Code)", plugins=None, markets=None, worktrees=None,
                 timeout=False, slow_s=0, fail=(), interrupt=()):
        self.version, self.timeout, self.slow_s, self.fail = version, timeout, slow_s, fail
        self.interrupt = interrupt
        self.plugins = [dict(p) for p in (plugins or [])]
        self.markets = [dict(m) for m in (markets or [])]
        self.worktrees = worktrees or {}  # project path -> list of worktree paths
        self.calls, self.timeouts, self.cwds = [], [], []

    def __call__(self, argv, timeout, **kw):
        argv = list(argv)
        self.timeouts.append(timeout)
        if argv[0] == "git":
            self.calls.append(argv)
            if self.timeout:
                return Result(124, "", "", True)
            path = argv[2]
            if path not in self.worktrees:
                return Result(128, "", "fatal: not a git repository", False)
            return Result(0, "".join(f"worktree {w}\nHEAD 0\n\n" for w in self.worktrees[path]), "", False)
        cmd = cp.claude_cmd()
        assert argv[:len(cmd)] == cmd, argv
        args = argv[len(cmd):]
        self.calls.append(args)
        self.cwds.append(kw.get("cwd"))
        if self.timeout:
            return Result(124, "", "", True)
        if self.slow_s:
            time.sleep(min(self.slow_s, timeout))
            if self.slow_s > timeout:
                return Result(124, "", "", True)
        if tuple(args) in self.interrupt:
            os.kill(os.getpid(), signal.SIGINT)
            time.sleep(2)  # KeyboardInterrupt lands here when SIGINT raises it
            return Result(0, "", "", False)
        if tuple(args) in self.fail:
            return Result(1, "", "boom", False)
        if args == ["--version"]:
            return Result(0, self.version + "\n", "", False) if self.version else Result(127, "", "", False)
        if args == ["plugin", "list", "--json"]:
            return Result(0, json.dumps(self.plugins), "", False)
        if args == ["plugin", "marketplace", "list", "--json"]:
            return Result(0, json.dumps(self.markets), "", False)
        if args[:3] == ["plugin", "marketplace", "add"]:
            self.markets.append({"name": "balancewheel", "source": "directory", "path": args[3]})
        elif args == cp.INSTALL:
            self.plugins.append(entry())
        elif args == ["plugin", "update", cp.PLUGIN_ID]:
            for p in self.plugins:
                p["version"] = version.VERSION
        elif args == ["plugin", "uninstall", cp.PLUGIN_ID]:
            self.plugins = [p for p in self.plugins if p["id"] != cp.PLUGIN_ID]
        elif args == ["plugin", "marketplace", "remove", "balancewheel"]:
            self.markets = [m for m in self.markets if m["name"] != "balancewheel"]
        return Result(0, "", "", False)

    def changing(self):
        """The calls that change Claude Code's state: everything but the read-only checks."""
        return [c for c in self.calls if c[0] != "git" and "--version" not in c and "list" not in c]

    def verbs(self):
        """The changing calls as their verb tokens, e.g. "install", "uninstall", "marketplace add"."""
        return [" ".join(c[1:3]) if c[1] == "marketplace" else c[1] for c in self.changing()]

    def fixed(self, *args):
        """Let a failing (or interrupting) call work from now on."""
        self.fail = set(self.fail) - {tuple(args)}
        self.interrupt = set(self.interrupt) - {tuple(args)}


class ClaudePaneCase(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {"BW_CLAUDE_CMD": json.dumps(FAKE_CMD)})
        p.start(); self.addCleanup(p.stop)
        self.config = {"projects": {}, "claude_pane": True}
        self.said = []

    def rec(self, **kw):
        kw.setdefault("plugins", [])
        kw.setdefault("markets", [])
        return Recorder(**kw)

    def setup_out(self, rec, config=None, remove=False):
        out = io.StringIO()
        code = cp.setup(self.config if config is None else config, remove=remove, run=rec, out=out)
        return code, out.getvalue()


class Basics(ClaudePaneCase):
    def test_claude_cmd_reads_bw_claude_cmd(self):
        self.assertEqual(cp.claude_cmd(), FAKE_CMD)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(cp.claude_cmd(), ["claude"])
        for bad in ("claude", "[]", '["a", 1]', "{}"):
            with mock.patch.dict(os.environ, {"BW_CLAUDE_CMD": bad}):
                with self.assertRaises(ValueError, msg=bad):
                    cp.claude_cmd()

    def test_parse_version(self):
        self.assertEqual(cp.parse_version("2.1.332 (Claude Code)\n"), (2, 1, 332))
        self.assertEqual(cp.parse_version("claude 10.0.3-beta"), (10, 0, 3))
        self.assertIsNone(cp.parse_version(""))
        self.assertIsNone(cp.parse_version("no version here"))

    def test_installed_and_marketplace_source_read_the_lists(self):
        rec = self.rec(plugins=[{"id": "other@x", "version": "1", "enabled": True}, entry("0.0.1")],
                       markets=[{"name": "other", "source": "directory", "path": "/o"}, OURS])
        self.assertEqual(cp.installed(run=rec)["version"], "0.0.1")
        self.assertEqual(cp.marketplace_source(rec, cp.CHECK_TIMEOUT_S), cp.ROOT)
        rec = self.rec()
        self.assertIsNone(cp.installed(run=rec))
        self.assertIsNone(cp.marketplace_source(rec, cp.CHECK_TIMEOUT_S))

    def test_a_failed_list_is_an_error_not_absent(self):
        for rec in (self.rec(timeout=True), self.rec(fail={("plugin", "list", "--json"),
                                                         ("plugin", "marketplace", "list", "--json")})):
            with self.assertRaises(cp.CheckFailed):
                cp.installed(run=rec)
            with self.assertRaises(cp.CheckFailed):
                cp.marketplace_source(rec, cp.CHECK_TIMEOUT_S)

    def test_stack_md_records_the_minimum(self):
        self.assertIn(".".join(map(str, cp.MIN_CLAUDE)), (ROOT / "docs" / "stack.md").read_text())

    def test_the_marketplace_file_names_this_marketplace_and_plugin(self):
        m = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(m["name"], cp.MARKETPLACE)
        self.assertEqual(cp.PLUGIN_ID, f"{m['plugins'][0]['name']}@{cp.MARKETPLACE}")


class Refusal(ClaudePaneCase):
    def test_refuses_root_inside_a_project(self):
        msg = cp.refusal({"projects": {cp.ROOT: "p"}}, run=self.rec())
        self.assertIn("refusing", msg)
        self.assertIn(f"bw's checkout ({cp.ROOT}) lies inside {cp.ROOT}", msg)

    def test_refuses_root_inside_a_worktree_of_a_project(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.realpath(d)
            wt = os.path.dirname(cp.ROOT)  # a worktree of the project that holds bw's checkout
            rec = self.rec(worktrees={project: [project, wt]})
            msg = cp.refusal({"projects": {project: "p"}}, run=rec)
            self.assertIn(f"lies inside {wt}", msg)
            self.assertEqual(rec.calls, [["git", "-C", project, "worktree", "list", "--porcelain"]])
            self.assertEqual(rec.timeouts, [cp.CHECK_TIMEOUT_S])

    def test_no_refusal_for_unrelated_projects(self):
        with tempfile.TemporaryDirectory() as d:
            project = os.path.realpath(d)
            rec = self.rec(worktrees={project: [project]})
            self.assertIsNone(cp.refusal({"projects": {project: "p", "/no/such/repo": "q"}}, run=rec))
        self.assertIsNone(cp.refusal(None, run=rec))
        self.assertIsNone(cp.refusal({}, run=rec))

    def test_a_hung_git_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            msg = cp.refusal({"projects": {d: "p"}}, run=self.rec(timeout=True))
        self.assertIn("refusing", msg)


class Setup(ClaudePaneCase):
    def test_fresh_setup_adds_marketplace_then_installs(self):
        rec = self.rec(version="2.1.332 (Claude Code)", plugins=[], markets=[])
        code, out = self.setup_out(rec)
        self.assertEqual(code, 0, out)
        self.assertEqual(rec.changing(), [["plugin", "marketplace", "add", cp.ROOT], cp.INSTALL])
        self.assertTrue(all(t == cp.STEP_TIMEOUT_S for t in rec.timeouts), rec.timeouts)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertIn(f"added the balancewheel marketplace at {cp.ROOT}", lines[0])
        self.assertIn(f"installed {cp.PLUGIN_ID} {version.VERSION}", lines[1])

    def test_setup_is_idempotent_and_updates_on_version_change(self):
        rec = self.rec()
        self.assertEqual(self.setup_out(rec)[0], 0)
        before = len(rec.changing())
        code, out = self.setup_out(rec)
        self.assertEqual(code, 0)
        self.assertEqual(len(rec.changing()), before, "a second run changed something")
        self.assertIn("the live pane is installed and current", out)
        rec.plugins[0]["version"] = "0.0.1"  # bw moved on since the install
        code, out = self.setup_out(rec)
        self.assertEqual(code, 0, out)
        self.assertEqual(rec.changing()[before:], [["plugin", "marketplace", "update", "balancewheel"],
                                                    ["plugin", "update", cp.PLUGIN_ID]])
        self.assertIn(f"updated {cp.PLUGIN_ID} from 0.0.1 to {version.VERSION}", out)

    def test_setup_keeps_a_disabled_plugin_disabled(self):
        rec = self.rec(markets=[OURS], plugins=[entry(enabled=False)])
        code, out = self.setup_out(rec)
        self.assertEqual(code, 0)
        self.assertEqual(rec.changing(), [])
        self.assertIn("disabled", out)

    def test_old_claude_is_refused(self):
        for ver in ("2.0.1 (Claude Code)", "2.1.331 (Claude Code)", None, "garbage"):
            rec = self.rec(version=ver)
            code, out = self.setup_out(rec)
            self.assertEqual(code, 1, ver)
            self.assertIn("mods cannot load", out)
            self.assertEqual(len(out.splitlines()), 1, out)
            self.assertEqual(rec.changing(), [], ver)

    def test_refuses_root_inside_a_project(self):
        rec = self.rec()
        code, out = self.setup_out(rec, config={"projects": {cp.ROOT: "p"}})
        self.assertEqual(code, 1)
        self.assertIn("refusing", out)
        self.assertEqual(rec.calls, [], "a refused setup still called claude")

    def test_config_none_skips_the_project_check(self):
        rec = self.rec()
        self.assertEqual(self.setup_out(rec, config={})[0], 0)
        rec = self.rec()
        out = io.StringIO()
        self.assertEqual(cp.setup(None, run=rec, out=out), 0)
        self.assertFalse([c for c in rec.calls if c[0] == "git"])

    def test_a_foreign_balancewheel_marketplace_is_refused(self):
        rec = self.rec(markets=[FOREIGN], plugins=[])
        code, out = self.setup_out(rec)
        self.assertEqual(code, 1)
        self.assertIn("a marketplace named balancewheel points at /elsewhere, not this checkout; remove it first", out)
        self.assertEqual(rec.changing(), [])

    def test_a_plugin_from_a_foreign_marketplace_is_uninstalled_and_the_marketplace_left(self):
        rec = self.rec(markets=[FOREIGN], plugins=[entry()])
        code, out = self.setup_out(rec)
        self.assertEqual(code, 1)
        self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID]])
        self.assertEqual(rec.markets, [FOREIGN])
        lines = out.splitlines()
        self.assertEqual(len(lines), 2, out)
        self.assertIn(f"uninstalled {cp.PLUGIN_ID}: its marketplace points at /elsewhere, not this checkout", lines[0])
        self.assertIn("remove it first", lines[1])

    def test_an_orphan_plugin_is_reinstalled_from_root(self):
        for plugins in ([entry()], [entry("0.0.1")], [entry(enabled=False)]):
            rec = self.rec(markets=[], plugins=plugins)
            code, out = self.setup_out(rec)
            self.assertEqual(code, 0, out)
            self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID],
                                              ["plugin", "marketplace", "add", cp.ROOT],
                                              cp.INSTALL], plugins)
            self.assertIn("installed with no balancewheel marketplace, so its source is unknown", out)
            self.assertNotIn("installed and current", out)
            self.assertEqual(rec.plugins, [entry()])

    def test_a_non_directory_balancewheel_marketplace_is_refused(self):
        rec = self.rec(markets=[{"name": "balancewheel", "source": "github", "repo": "someone/balancewheel"}])
        code, out = self.setup_out(rec)
        self.assertEqual(code, 1)
        self.assertIn("points at", out)
        self.assertEqual(rec.changing(), [])

    def test_a_failed_step_exits_1(self):
        rec = self.rec(fail={tuple(cp.INSTALL)})
        code, out = self.setup_out(rec)
        self.assertEqual(code, 1)
        self.assertIn("failed", out.splitlines()[-1])

    def test_an_unreadable_list_changes_nothing(self):
        rec = self.rec(fail={("plugin", "marketplace", "list", "--json")})
        code, out = self.setup_out(rec)
        self.assertEqual(code, 1)
        self.assertEqual(rec.changing(), [])

    def test_a_failed_repair_is_retried_and_never_reads_as_current(self):
        for plugins in ([entry()], [entry("0.0.1")]):
            uninstall = ("plugin", "uninstall", cp.PLUGIN_ID)
            rec = self.rec(markets=[], plugins=plugins, fail={uninstall})
            code, out = self.setup_out(rec)
            self.assertEqual(code, 1, out)
            self.assertEqual(rec.verbs(), ["uninstall"])  # no marketplace was added beside the old files
            self.assertEqual(rec.markets, [])
            code, out = self.setup_out(rec)  # still failing: still untrusted
            self.assertEqual(code, 1, out)
            self.assertNotIn("installed and current", out)
            rec.fixed(*uninstall)
            n = len(rec.changing())
            code, out = self.setup_out(rec)
            self.assertEqual(code, 0, out)
            self.assertNotIn("installed and current", out)
            self.assertEqual(rec.verbs()[n:], ["uninstall", "marketplace add", "install"])
            code, out = self.setup_out(rec)  # repaired: now it is current, and from ROOT
            self.assertIn("installed and current", out)

    def test_every_claude_call_runs_from_root(self):
        rec = self.rec(markets=[], plugins=[entry("0.0.1")])
        self.setup_out(rec)
        self.setup_out(rec, remove=True)
        self.assertTrue(rec.cwds)
        self.assertEqual(set(rec.cwds), {cp.ROOT}, rec.cwds)

    def test_remove_touches_only_its_own(self):
        # Controller ruling (fix round 1): a plugin from a foreign marketplace is untrusted, so --remove
        # uninstalls it too; the foreign marketplace itself is never removed.
        rec = self.rec(markets=[FOREIGN], plugins=[entry()])
        code, out = self.setup_out(rec, remove=True)
        self.assertEqual(code, 1)
        self.assertNotIn(["plugin", "marketplace", "remove", "balancewheel"], rec.calls)
        self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID]])
        self.assertIn(f"uninstalled {cp.PLUGIN_ID} (its marketplace points at /elsewhere", out)
        self.assertIn("left that marketplace alone", out)
        rec = self.rec(markets=[FOREIGN], plugins=[])
        self.assertEqual(self.setup_out(rec, remove=True)[0], 1)
        self.assertEqual(rec.changing(), [])

    def test_remove_takes_out_what_setup_added(self):
        rec = self.rec()
        self.setup_out(rec)
        n = len(rec.changing())
        code, out = self.setup_out(rec, remove=True)
        self.assertEqual(code, 0, out)
        self.assertEqual(rec.changing()[n:], [["plugin", "uninstall", cp.PLUGIN_ID],
                                              ["plugin", "marketplace", "remove", "balancewheel"]])
        self.assertEqual((rec.plugins, rec.markets), ([], []))
        code, out = self.setup_out(rec, remove=True)  # again: nothing left to remove
        self.assertEqual(code, 0)
        self.assertEqual(len(rec.changing()), n + 2)
        self.assertIn("nothing to remove", out)

    def test_remove_uninstalls_an_orphan_plugin(self):
        rec = self.rec(plugins=[entry()])
        code, out = self.setup_out(rec, remove=True)
        self.assertEqual(code, 0)
        self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID]])
        self.assertIn(f"uninstalled {cp.PLUGIN_ID} (it was installed with no balancewheel marketplace", out)
        self.assertNotIn("nothing to remove", out)


class Ensure(ClaudePaneCase):
    def ensure(self, rec, config=None, refused=None):
        cp.ensure(self.config if config is None else config, run=rec, say=self.said.append, refused=refused)

    def test_ensure(self):
        for plugins, want in (([], "install"), ([entry("0.0.1")], "update"), ([entry()], None),
                              ([entry("0.0.1", enabled=False)], None)):
            self.said = []
            rec = self.rec(plugins=plugins, markets=[OURS])
            self.ensure(rec)
            verbs = rec.verbs()  # whole tokens: "install" never matches "uninstall"
            if want:
                self.assertIn(want, verbs, (plugins, verbs))
                self.assertEqual(len(self.said), 1, self.said)
                self.assertTrue(self.said[0].startswith("bw: setting up the live pane: "), self.said)
            else:
                self.assertFalse([v for v in verbs if v in ("install", "update", "uninstall")], (plugins, verbs))
                self.assertEqual(self.said, [])

    def test_absent_marketplace_is_added_first(self):
        rec = self.rec()
        self.ensure(rec)
        self.assertEqual(rec.changing(), [["plugin", "marketplace", "add", cp.ROOT], cp.INSTALL])
        self.assertEqual(self.said, [f"bw: setting up the live pane: installing {cp.PLUGIN_ID} {version.VERSION} "
                                     "from this checkout…"])

    def test_update_refreshes_the_marketplace_then_the_plugin(self):
        rec = self.rec(plugins=[entry("0.0.1")], markets=[OURS])
        self.ensure(rec)
        self.assertEqual(rec.changing(), [["plugin", "marketplace", "update", "balancewheel"],
                                          ["plugin", "update", cp.PLUGIN_ID]])
        self.assertEqual(len(self.said), 1, self.said)
        self.assertIn(f"updating {cp.PLUGIN_ID} from 0.0.1 to {version.VERSION}", self.said[0])

    def test_an_orphan_plugin_is_reinstalled_from_root(self):
        for plugins in ([entry("0.0.1")], [entry()]):  # stale or current: its source is unknown either way
            self.said = []
            rec = self.rec(plugins=plugins, markets=[])
            self.ensure(rec)
            self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID],
                                              ["plugin", "marketplace", "add", cp.ROOT],
                                              cp.INSTALL], plugins)
            self.assertEqual(len(self.said), 1, self.said)
            self.assertIn("installing live-pane@balancewheel", self.said[0])
            self.assertIn("so its source is unknown", self.said[0])
            self.assertEqual(rec.plugins, [entry()])

    def test_a_failed_or_interrupted_repair_is_retried_at_the_next_launch(self):
        uninstall = ("plugin", "uninstall", cp.PLUGIN_ID)
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGINT, old)
        for kind in ("fail", "interrupt"):
            self.said = []
            rec = self.rec(markets=[], plugins=[entry()], **{kind: {uninstall}})
            self.ensure(rec)
            self.assertEqual(rec.verbs(), ["uninstall"], kind)
            self.assertEqual(rec.markets, [], kind)  # the old files never sit beside ROOT's marketplace
            rec.fixed(*uninstall)
            self.ensure(rec)
            self.assertEqual(rec.verbs(), ["uninstall", "uninstall", "marketplace add", "install"], kind)
            self.assertEqual((rec.plugins, len(rec.markets)), ([entry()], 1), kind)
            self.assertEqual(set(rec.cwds), {cp.ROOT}, kind)

    def test_a_disabled_orphan_is_left_alone(self):
        rec = self.rec(plugins=[entry(enabled=False)], markets=[])
        self.ensure(rec)
        self.assertEqual(rec.changing(), [])
        self.assertEqual(self.said, [])

    def test_ctrl_c_stops_the_step_and_the_launch_goes_on(self):
        old = signal.signal(signal.SIGINT, signal.SIG_IGN)  # as in bw lead, which ignores SIGINT while it waits
        self.addCleanup(signal.signal, signal.SIGINT, old)
        rec = self.rec(plugins=[entry()], markets=[], interrupt={("plugin", "uninstall", cp.PLUGIN_ID)})
        self.ensure(rec)  # returns: no KeyboardInterrupt escapes
        self.assertEqual(rec.verbs(), ["uninstall"])  # neither the add nor the install ran
        self.assertEqual(len(self.said), 2, self.said)
        self.assertIn("setting up the live pane", self.said[0])
        self.assertIn("interrupted; the lead starts without it", self.said[1])
        self.assertIs(signal.getsignal(signal.SIGINT), signal.SIG_IGN)  # bw's disposition is back

    def test_claude_pane_false_calls_nothing(self):
        rec = self.rec()
        self.ensure(rec, config={**self.config, "claude_pane": False})
        self.assertEqual(rec.calls, [])
        self.assertEqual(self.said, [])

    def test_a_refusal_does_nothing_and_says_nothing(self):
        rec = self.rec()
        self.ensure(rec, refused="refusing: bw (/x) lies inside /x")
        self.assertEqual(rec.calls, [])
        rec = self.rec()
        self.ensure(rec, config={"projects": {cp.ROOT: "p"}})
        self.assertEqual(rec.changing(), [])
        self.assertFalse([c for c in rec.calls if c[0] != "git"])
        self.assertEqual(self.said, [])

    def test_a_foreign_marketplace_is_left_alone(self):
        for plugins in ([], [entry(enabled=False)]):
            rec = self.rec(markets=[FOREIGN], plugins=plugins)
            self.ensure(rec)
            self.assertEqual(rec.changing(), [], plugins)
        self.assertEqual(self.said, [])

    def test_a_plugin_from_a_foreign_marketplace_is_uninstalled(self):
        for plugins in ([entry("0.0.1")], [entry()]):
            self.said = []
            rec = self.rec(markets=[FOREIGN], plugins=plugins)
            self.ensure(rec)
            self.assertEqual(rec.changing(), [["plugin", "uninstall", cp.PLUGIN_ID]], plugins)
            self.assertEqual(rec.markets, [FOREIGN])
            self.assertEqual(len(self.said), 1, self.said)
            self.assertIn("uninstalling live-pane@balancewheel (its marketplace points at /elsewhere", self.said[0])

    def test_an_old_claude_does_nothing(self):
        rec = self.rec(version="2.0.1 (Claude Code)")
        self.ensure(rec)
        self.assertEqual(rec.changing(), [])
        self.assertEqual(self.said, [])

    def test_a_hung_claude_does_not_raise_or_say(self):
        rec = self.rec(timeout=True)
        self.ensure(rec)
        self.assertEqual(rec.changing(), [])
        self.assertEqual(self.said, [])

    def test_a_failed_list_does_not_install(self):
        rec = self.rec(fail={("plugin", "list", "--json")}, markets=[OURS])
        self.ensure(rec)
        self.assertEqual(rec.changing(), [])
        self.assertEqual(self.said, [])

    def test_the_checks_share_one_5_s_limit(self):
        rec = self.rec(slow_s=2, markets=[OURS])  # each check takes 2 s
        t0 = time.monotonic()
        self.ensure(rec)
        self.assertLessEqual(time.monotonic() - t0, cp.CHECK_TIMEOUT_S + 0.5)
        self.assertTrue(all(t <= cp.CHECK_TIMEOUT_S for t in rec.timeouts), rec.timeouts)
        self.assertLess(rec.timeouts[-1], 1.5, rec.timeouts)  # the third check got only what was left
        self.assertEqual(rec.changing(), [])  # the last check ran out of time: nothing is installed
        self.assertEqual(self.said, [])

    def test_ensure_never_raises(self):
        def boom(argv, timeout, **kw):
            raise RuntimeError("injected")
        cp.ensure(self.config, run=boom, say=self.said.append)
        with mock.patch.dict(os.environ, {"BW_CLAUDE_CMD": "not json"}):
            rec = self.rec()
            self.ensure(rec)
            self.assertEqual(rec.calls, [])
        self.assertEqual(self.said, [])

    def test_a_failed_install_says_one_more_line(self):
        rec = self.rec(markets=[OURS], fail={tuple(cp.INSTALL)})
        self.ensure(rec)
        self.assertEqual(len(self.said), 2, self.said)
        self.assertIn("setting up the live pane", self.said[0])
        self.assertIn("was not set up", self.said[1])
        self.assertIn("bw setup claude-pane", self.said[1])


class FakeClaude(unittest.TestCase):
    """tests/fixtures/fake_claude.py: the stand-in the runner driver puts in BW_CLAUDE_CMD."""

    def test_fake_logs_and_answers_from_its_state(self):
        fake = [sys.executable, str(ROOT / "tests" / "fixtures" / "fake_claude.py")]
        with tempfile.TemporaryDirectory() as d:
            log, state = os.path.join(d, "calls.jsonl"), os.path.join(d, "state.json")
            env = {**os.environ, "FAKE_CLAUDE_LOG": log}
            out = subprocess.run(fake + ["plugin", "list", "--json"], env=env, capture_output=True, text=True)
            self.assertEqual(json.loads(out.stdout)[0]["version"], version.VERSION)  # default: installed, current
            with open(state, "w") as f:
                json.dump({"version": "2.0.0 (Claude Code)", "plugins": [], "markets": []}, f)
            env["FAKE_CLAUDE_STATE"] = state
            out = subprocess.run(fake + ["--version"], env=env, capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "2.0.0 (Claude Code)")
            out = subprocess.run(fake + cp.INSTALL, env=env, capture_output=True, text=True)
            self.assertEqual(out.returncode, 0)
            with open(log) as f:
                calls = [json.loads(line) for line in f]
            self.assertEqual(calls, [["plugin", "list", "--json"], ["--version"], cp.INSTALL])


class SetupCli(unittest.TestCase):
    """`bw setup claude-pane` through bin/bw, with the fake claude and a temp config."""

    def test_bw_setup_claude_pane_runs_against_the_fake(self):
        with tempfile.TemporaryDirectory() as d:
            log, state = os.path.join(d, "calls.jsonl"), os.path.join(d, "state.json")
            with open(state, "w") as f:
                json.dump({"version": "2.1.332 (Claude Code)", "plugins": [], "markets": []}, f)
            env = {**os.environ, "FAKE_CLAUDE_LOG": log, "FAKE_CLAUDE_STATE": state,
                   "BW_CLAUDE_CMD": json.dumps([sys.executable, str(ROOT / "tests" / "fixtures" / "fake_claude.py")]),
                   "BALANCEWHEEL_CONFIG": os.path.join(d, "missing.json"), "BW_STATE_DIR": os.path.join(d, "state")}
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "bw"), "setup", "claude-pane"], env=env,
                               capture_output=True, text=True, cwd=d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("config error", r.stdout + r.stderr)  # a missing config: no project check, one line
            with open(log) as f:
                calls = [json.loads(line) for line in f]
            self.assertIn(["plugin", "marketplace", "add", cp.ROOT], calls)
            self.assertIn(cp.INSTALL, calls)
            r = subprocess.run([sys.executable, str(ROOT / "bin" / "bw"), "setup", "claude-pane", "--remove"], env=env,
                               capture_output=True, text=True, cwd=d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)  # the fake keeps no state: nothing to remove


if __name__ == "__main__":
    unittest.main()
