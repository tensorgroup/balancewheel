import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "metrics"))
from lead import inbox, triage, triage_hook as hook, triage_install as inst  # noqa: E402

CFG = {"mode": "acting", "model": "m", "key_env": "K", "thresholds": dict(triage.DEFAULT_THRESHOLDS)}
EVENT = {"tool_name": "AskUserQuestion", "tool_input": {"questions": [
    {"question": "Which name?", "options": [{"label": "parse_row (Recommended)", "description": "matches the module"},
                                            {"label": "parseRow", "description": "camel"}]}]}}
SCORES = {"strong_case": 0.9, "only_person_knows": 0.1, "outward_or_irreversible": 0.1, "high_stakes": 0.1}
YES = lambda text, cfg, env: {"verdict": "decide", "reason": "strong case", "scores": SCORES}  # noqa: E731
NO = lambda text, cfg, env: {"verdict": "ask", "reason": "high_stakes"}  # noqa: E731


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.asks = Path(self.tmp.name) / "asks"; self.asks.mkdir()

    def env(self, mode="acting"):
        return {"BW_TRIAGE_CONFIG": json.dumps({**CFG, "mode": mode}), "BW_ASKS": str(self.asks), "K": "s"}


class Render(unittest.TestCase):
    def test_renders_the_recommendation_as_a_line(self):
        text = hook.render(EVENT["tool_input"]["questions"])
        self.assertIn("\nRecommendation: parse_row — matches the module", text)
        self.assertIsNone(triage.prefilter(text))

    def test_a_risk_on_the_other_option_reaches_the_denylist(self):
        qs = [{"question": "Which?", "options": [{"label": "A (Recommended)", "description": "fine"},
                                                 {"label": "B", "description": "do not send the customer email"}]}]
        self.assertEqual(triage.prefilter(hook.render(qs)), "denylist")

    def test_the_marker_is_found_in_any_case(self):
        qs = [{"question": "Which?", "options": [{"label": "A (recommended)"}, {"label": "B"}]}]
        self.assertIn("Recommendation: A", hook.render(qs))

    def test_every_question_needs_a_recommendation(self):
        qs = EVENT["tool_input"]["questions"] + [{"question": "Other?", "options": [{"label": "a"}, {"label": "b"}]}]
        self.assertIsNone(hook.render(qs))
        self.assertIsNone(hook.render([]))


class Decide(Case):
    def test_decided_writes_a_receipt_and_denies(self):
        out = json.loads(hook.decide(EVENT, self.env(), judge=YES))["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("not the user's answer", out["permissionDecisionReason"])
        (f,) = self.asks.glob("*.ask")
        self.assertTrue(f.read_text().startswith("@session decided\n"))

    def test_undecided_is_silent_with_no_record(self):
        self.assertEqual(hook.decide(EVENT, self.env(), judge=NO), "")
        self.assertEqual(list(self.asks.iterdir()), [])

    def test_shadow_records_and_stays_silent(self):
        self.assertEqual(hook.decide(EVENT, self.env("shadow"), judge=YES), "")
        self.assertEqual(len(list(self.asks.glob("*.ask"))), 1)

    def test_other_tools_are_ignored(self):
        self.assertEqual(hook.decide({"tool_name": "Bash", "tool_input": {}}, self.env(), judge=YES), "")

    def test_no_receipt_means_no_decision(self):
        env = {**self.env(), "BW_ASKS": str(self.asks / "gone")}
        with self.assertRaises(OSError):
            hook.decide(EVENT, env, judge=YES)

    def test_the_receipt_round_trips_through_ingest(self):
        hook.decide(EVENT, self.env(), judge=YES)
        state = self.tmp.name + "/state"
        import shutil
        dest = Path(inbox.asks_dir(state, "k")); dest.parent.mkdir(parents=True)
        shutil.copytree(self.asks, dest)
        (r,) = inbox.ingest(state, "k", "/w", "la", "s", receipts=True)[0]
        self.assertEqual(r["source"], "session")
        (i,) = inbox.load(state)["items"]
        self.assertIn("Which name?", i["text"])
        self.assertEqual(i["status"], "answered")


class Timed(unittest.TestCase):
    def test_the_alarm_bounds_a_hung_judge_and_is_cancelled_after(self):
        import signal, time
        with self.assertRaises(TimeoutError):
            hook.timed(lambda: time.sleep(5), seconds=1)()
        self.assertEqual(hook.timed(lambda: "ok", seconds=1)(), "ok")
        self.assertEqual(signal.alarm(0), 0)


class AsAProcess(Case):
    def run_hook(self, stdin, env):
        return subprocess.run(["/usr/bin/python3", "-I", str(ROOT / "lead" / "triage_hook.py")], input=stdin, text=True,
                              capture_output=True, env={"PATH": "/usr/bin", **env}, timeout=20)

    def test_a_missing_key_shows_the_popup(self):
        env = self.env(); del env["K"]
        p = self.run_hook(json.dumps(EVENT), env)
        self.assertEqual((p.returncode, p.stdout), (0, ""))

    def test_garbage_shows_the_popup(self):
        p = self.run_hook("not json", self.env())
        self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))

    def test_the_installed_command_is_inert_without_bw(self):
        p = subprocess.run(["/bin/sh", "-c", inst.COMMAND.split(" ", 2)[2].strip("'")], input="x", text=True,
                           capture_output=True, env={"PATH": "/usr/bin"}, timeout=10)
        self.assertEqual((p.returncode, p.stdout), (0, ""))


class Install(Case):
    def path(self, data=None):
        p = Path(self.tmp.name) / "settings.json"
        if data is not None:
            p.write_text(json.dumps(data))
        return str(p)

    def test_adds_one_entry_and_keeps_the_rest(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
        p = self.path({"model": "opus", "hooks": {"PreToolUse": [other], "Stop": [1]}})
        inst.install(p); inst.install(p)
        d = json.loads(Path(p).read_text())
        self.assertEqual(d["model"], "opus")
        self.assertEqual(d["hooks"]["Stop"], [1])
        self.assertEqual(d["hooks"]["PreToolUse"][0], other)
        self.assertEqual(len(d["hooks"]["PreToolUse"]), 2)
        e = d["hooks"]["PreToolUse"][1]
        self.assertEqual(e["matcher"], "AskUserQuestion")
        self.assertGreater(e["hooks"][0]["timeout"], hook.ALARM_S)

    def test_uninstall_removes_only_ours(self):
        other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}
        p = self.path({"hooks": {"PreToolUse": [other]}})
        inst.install(p); inst.uninstall(p)
        self.assertEqual(json.loads(Path(p).read_text())["hooks"]["PreToolUse"], [other])
        self.assertEqual(inst.uninstall(p), "bw: nothing to remove")

    def test_a_hook_sharing_our_entry_survives_both_ways(self):
        ours = {"type": "command", "command": inst.COMMAND}
        keep = {"type": "command", "command": "policy"}
        p = self.path({"hooks": {"PreToolUse": [{"matcher": "AskUserQuestion", "hooks": [ours, keep]}]}})
        inst.install(p)
        entries = json.loads(Path(p).read_text())["hooks"]["PreToolUse"]
        self.assertEqual(entries[0]["hooks"], [keep])
        inst.uninstall(p)
        self.assertEqual(json.loads(Path(p).read_text())["hooks"]["PreToolUse"], [entries[0]])

    def test_the_file_mode_is_kept(self):
        p = self.path({})
        os.chmod(p, 0o600)
        inst.install(p)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)

    def test_null_hooks_are_filled_in(self):
        p = self.path({"hooks": None})
        inst.install(p)
        self.assertEqual(len(json.loads(Path(p).read_text())["hooks"]["PreToolUse"]), 1)

    def test_a_file_it_cannot_read_is_left_alone(self):
        p = Path(self.path()); p.write_text("{nope")
        with self.assertRaises(ValueError):
            inst.install(str(p))
        self.assertEqual(p.read_text(), "{nope")

    def test_a_missing_file_is_created(self):
        p = self.path()
        inst.install(p)
        self.assertIn("PreToolUse", json.loads(Path(p).read_text())["hooks"])
