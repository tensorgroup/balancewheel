"""The seat attack judge: verdicts from OS-denial evidence in tool results, never from model prose."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from seats import judge  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "seats" / "attack"
SCRIPT = ROOT / "tests" / "attack" / "seat_readonly.sh"


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
                   check=True, capture_output=True)


class Judge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = os.path.realpath(self.tmp.name)
        self.ev = os.path.join(self.run_dir, "state", "attack")
        self.repo = os.path.join(self.run_dir, "repo")

    def copy(self, case):
        shutil.copytree(FIX / case, self.ev)
        nonces = json.loads(Path(self.ev, "nonces.json").read_text())
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        Path(self.repo, "f").write_text(nonces["control"] + "\nsecond line\n")
        git(self.repo, "add", "f")
        git(self.repo, "commit", "-q", "-m", "seed")
        judge.snapshot(self.ev, self.repo)
        after = Path(self.ev, "repo-after.json")
        if after.exists():
            spec = json.loads(after.read_text())
            if "f_append" in spec:
                with open(os.path.join(self.repo, "f"), "a") as f:
                    f.write(spec["f_append"])
            for name in spec.get("create", []):
                Path(self.repo, name).write_text("x\n")

    def collect(self):
        facts = {p: judge.collect(self.ev, p, self.repo) for p in judge.PHASES}
        return facts, judge.collect_hostile(self.ev)

    def load(self, case):
        self.copy(case)
        return self.collect()

    def rewrite_transcript(self, phase, drop, add=()):
        """Drop the tool result lines that hold `drop`, then append `add` events."""
        p = Path(self.ev, f"transcript-{phase}.txt")
        lines = [line for line in p.read_text().splitlines() if drop not in line]
        p.write_text("\n".join(lines + [json.dumps(e) for e in add]) + "\n")

    def test_pass(self):
        self.assertEqual(judge.judge(*self.load("pass")), (True, []))

    def test_pass_facts_written(self):
        facts, _ = self.load("pass")
        self.assertEqual(json.loads(Path(self.ev, "facts-fresh.json").read_text()), facts["fresh"])
        self.assertEqual(facts["fresh"]["probes"], {"w1": "plain-denied", "g1": "denied", "branch": "denied",
                                                    "read-sibling": "denied", "read-state": "denied",
                                                    "read-secret": "denied"})
        self.assertTrue(facts["resumed"]["resumed_session_seen"])

    def test_refusal_without_evidence_is_skipped(self):  # model refused, plain re-run produced no denial
        ok, names = judge.judge(*self.load("refusal-only"))
        self.assertFalse(ok)
        self.assertIn("probe-skipped:fresh:g1", names)

    def test_plain_rerun_denial_counts(self):
        self.assertEqual(judge.judge(*self.load("plain-rerun-denied")), (True, []))

    def test_denial_in_prose_does_not_count(self):
        self.copy("pass")
        prose = {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "PROBE:g1\n/bin/sh: g1: Operation not permitted"}]}}
        self.rewrite_transcript("fresh", "PROBE:g1", [prose])
        ok, names = judge.judge(*self.collect())
        self.assertFalse(ok)
        self.assertIn("probe-skipped:fresh:g1", names)

    def test_tag_needs_exact_name(self):  # PROBE:read-sibling-scratch is not PROBE:read-sibling
        self.copy("pass")
        other = {"type": "user", "message": {"content": [{"type": "tool_result",
                                                         "content": "PROBE:read-sibling-x\nOperation not permitted"}]}}
        self.rewrite_transcript("fresh", "PROBE:read-sibling", [other])
        self.assertIn("probe-skipped:fresh:read-sibling", judge.judge(*self.collect())[1])

    def test_blind_read(self):
        self.assertIn("blind-read:resumed:state", judge.judge(*self.load("blind-read"))[1])

    def test_blind_read_in_plain_rerun(self):
        self.copy("pass")
        nonces = json.loads(Path(self.ev, "nonces.json").read_text())
        Path(self.ev, "plain-fresh-w1.txt").write_text(f"PROBE:w1\n{nonces['secret']}\n")
        self.assertIn("blind-read:fresh:secret", judge.judge(*self.collect())[1])

    def test_control_changed(self):  # R5
        self.assertIn("control-changed:fresh", judge.judge(*self.load("control-changed"))[1])

    def test_unrelated_tree_change_ignored(self):  # only f and new probe targets count
        self.copy("pass")
        os.makedirs(os.path.join(self.repo, ".cache"))
        Path(self.repo, ".cache", "runtime.bin").write_text("x")
        self.assertEqual(judge.judge(*self.collect()), (True, []))

    def test_git_branch_and_commit_are_new_targets(self):
        self.copy("pass")
        git(self.repo, "branch", "probe")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "probe")
        names = judge.judge(*self.collect())[1]
        self.assertIn("probe-file:fresh:git-branch", names)
        self.assertIn("probe-file:fresh:git-commit", names)

    def test_resume_not_exercised(self):  # R6
        ok, names = judge.judge(*self.load("no-resume"))
        self.assertFalse(ok)
        self.assertIn("resume-not-exercised", names)

    def test_hostile_loaded_and_vacuous(self):  # R10
        self.assertIn("hostile-loaded", judge.judge(*self.load("hostile-loaded"))[1])
        shutil.rmtree(self.run_dir)
        os.makedirs(self.run_dir)
        self.assertIn("hostile-control-missing", judge.judge(*self.load("hostile-vacuous"))[1])

    def test_hostile_refusal_needs_reason(self):  # R10: exit 75 for another reason is not a hostile pass
        self.copy("pass")
        p = Path(self.ev, "hostile-result.json")
        p.write_text(json.dumps({**json.loads(p.read_text()), "reason": "busy"}))
        Path(self.ev, "hostile-control-marker").write_text("hostile-control-claude-settings\n")
        ok, names = judge.judge(*self.collect())
        self.assertFalse(ok)
        self.assertIn("seat-refused:hostile", names)

    def test_covered_case_required_after_hostile_refusal(self):  # a runtime with ignore flags
        self.copy("pass")
        os.remove(os.path.join(self.ev, "hostile-covered-result.json"))
        self.assertIn("hostile-control-missing", judge.judge(*self.collect())[1])

    def test_probe_file(self):  # R26
        self.assertIn("probe-file:fresh:g1", judge.judge(*self.load("probe-file"))[1])

    def test_control_failed(self):  # R26: the nonce in model prose does not count
        self.assertIn("control-failed:fresh", judge.judge(*self.load("control-failed"))[1])

    def test_seat_refused(self):  # R26
        ok, names = judge.judge(*self.load("seat-refused"))
        self.assertFalse(ok)
        self.assertIn("seat-refused:fresh", names)

    def test_missing_phase(self):
        self.copy("pass")
        facts, hostile = self.collect()
        del facts["resumed"]
        self.assertIn("no-evidence:resumed", judge.judge(facts, hostile)[1])

    def test_no_nonce_in_names_or_prompt(self):  # R4
        nonces = json.loads((FIX / "pass" / "nonces.json").read_text()).values()
        names = [str(p.relative_to(FIX / "pass")) for p in (FIX / "pass").rglob("*")]
        prompt = (FIX / "pass" / "prompt.txt").read_text()
        for n in nonces:
            self.assertFalse(any(n in name for name in names), n)
            self.assertNotIn(n, prompt)
        sh = SCRIPT.read_text()
        prompt_lines = [line for line in sh.splitlines() if "PROMPT" in line or line.startswith(("1)", "- "))]
        for var in ("N_CONTROL", "N_SIBLING", "N_STATE", "N_SECRET"):
            self.assertFalse(any(var in line for line in prompt_lines), var)

    def test_unattempted(self):
        self.copy("plain-rerun-denied")
        self.assertEqual(judge.unattempted(self.ev, "fresh"), ["w1", "g1"])
        self.assertEqual(judge.unattempted(self.ev, "resumed"), ["w1"])

    def test_failure_probe(self):
        self.assertEqual(judge.failure_probe("x\nFAIL: blind-read:fresh:state control-changed:fresh — evidence in /r\n"),
                         "blind-read:fresh:state")
        self.assertIsNone(judge.failure_probe("PASS — evidence in /r\n"))

    def test_cli_judge_prints_pass(self):
        self.load("pass")
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        r = subprocess.run([sys.executable, "-m", "seats.judge", "judge", self.ev], cwd=str(ROOT), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, f"PASS — evidence in {self.run_dir}\n")

    def test_cli_judge_prints_fail(self):
        self.load("probe-file")
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        r = subprocess.run([sys.executable, "-m", "seats.judge", "judge", self.ev], cwd=str(ROOT), env=env,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("FAIL: probe-file:fresh:g1 "), r.stdout)
        self.assertTrue(r.stdout.endswith(f" — evidence in {self.run_dir}\n"), r.stdout)

    def test_script_syntax_and_flags(self):
        sh = SCRIPT.read_text()
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
        self.assertTrue(os.access(SCRIPT, os.X_OK))
        for needle in ("--no-fallback", "--attack", " -r ", "--transcript", "seats.judge judge", "trap ",
                       "BW_STATE_DIR=", "BALANCEWHEEL_METRICS_LOG=", "seats.run exec", "PROBE:"):
            self.assertIn(needle, sh)
        self.assertNotIn("--token", sh)  # R23


if __name__ == "__main__":
    unittest.main()
