import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from panel import roster  # noqa: E402

NEVER = lambda c, s, n, t: None  # noqa: E731


def cfg(**extra):
    seats = {
        "a": {"runtime": "codex", "fallback": "g", "roles": ["design"]},
        "o": {"runtime": "claude-code", "fallback": "g", "roles": ["design"]},
        "g": {"runtime": "pi", "roles": ["design"]},
        "r": {"runtime": "claude-code", "tier": "reserved", "roles": ["design"]},
        "x": {"runtime": "kimi", "enabled": False, "roles": ["design"]},
    }
    seats.update(extra)
    return {"seats": seats}


CFG = cfg()
CHECKPOINT = {"seats": ["a", "o", "g", "x"], "required": ["a"], "min_seats": 2}
PROFILE = {"checkpoints": {"design": CHECKPOINT}, "depth": "standard"}
PROFILE_WITH_REVIEW = {"checkpoints": {"design": CHECKPOINT, "review": CHECKPOINT}}


class RosterTest(unittest.TestCase):
    def test_alias_and_unknown(self):
        self.assertEqual(roster.checkpoint(PROFILE_WITH_REVIEW, "code-review"), "review")
        self.assertRaisesRegex(ValueError, r"it defines: design, review", roster.checkpoint, PROFILE_WITH_REVIEW, "plan")
        self.assertRaisesRegex(ValueError, "defines no checkpoints", roster.checkpoint, {}, "plan")

    def test_first_verification_covers_fallbacks(self):  # live run 2026-11-23 (neon was never verified)
        c = cfg(k={"runtime": "kimi", "fallback": "n", "roles": ["design"]},
                n={"runtime": "agy", "roles": ["design"]},
                q={"runtime": "kimi", "fallback": "r", "roles": ["design"]})
        self.assertEqual(roster.with_fallbacks(c, ["a", "k"], ["a", "k", "x"], "standard"), ["a", "k", "g", "n"])
        self.assertEqual(roster.with_fallbacks(c, ["q"], ["q"], "standard"), ["q"])  # a reserved fallback: deep only
        self.assertEqual(roster.with_fallbacks(c, ["q"], ["q"], "deep"), ["q", "r"])
        self.assertEqual(roster.with_fallbacks(c, [], ["x"], "standard"), [])  # a disabled seat brings none

    def test_tier(self):
        self.assertEqual(roster.tier({"depth": "deep-on-stakes"}, "high", False), "deep")
        self.assertEqual(roster.tier({"depth": "deep-on-stakes"}, "normal", False), "standard")
        self.assertEqual(roster.tier({"depth": "standard"}, "normal", True), "deep")
        self.assertEqual(roster.tier({"depth": "deep"}, "normal", False), "deep")

    def test_reserved_only_on_deep(self):
        self.assertNotIn("r", roster.roster(CFG, PROFILE, "design", "standard"))
        self.assertEqual(roster.roster(CFG, PROFILE, "design", "deep")[-1], "r")

    def test_disabled_reserved_not_added(self):
        c = cfg(r2={"runtime": "claude-code", "tier": "reserved", "enabled": False, "roles": ["design"]})
        self.assertNotIn("r2", roster.roster(c, PROFILE, "design", "deep"))

    def test_reserved_listed_directly_on_standard(self):
        runs, seats = roster.prelaunch(CFG, {}, ["o", "r"], "standard", 0.0, capped=NEVER)
        self.assertEqual((runs, seats["r"]["reason"]), (["o"], roster.RESERVED_REASON))
        self.assertEqual(seats["r"]["status"], "absent")

    def test_reserved_fallback_never_stands_in_on_standard(self):
        c = cfg()
        c["seats"]["a"]["fallback"] = "r"
        cap_a = lambda c_, s, n, t: "capped" if n == "a" else None  # noqa: E731
        runs, _ = roster.prelaunch(c, {}, ["a", "o"], "standard", 0.0, capped=cap_a)
        self.assertEqual(runs, ["o"])
        runs, seats = roster.prelaunch(c, {}, ["a", "o"], "deep", 0.0, capped=cap_a)
        self.assertEqual(runs, ["o", "r"])
        self.assertEqual(seats["r"]["stand_in_for"], "a")

    def test_one_agy_seat_per_panel(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        runs, seats = roster.prelaunch(c, {}, ["a1", "o", "a2"], "standard", 0.0, capped=NEVER)
        self.assertEqual(runs, ["a1", "o"])
        self.assertEqual((seats["a2"]["status"], seats["a2"]["reason"]), ("absent", "one agy seat per panel"))

    def test_agy_stand_in_skipped_when_agy_taken(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        c["seats"]["a"]["fallback"] = "a2"
        cap_a = lambda c_, s, n, t: "capped" if n == "a" else None  # noqa: E731
        runs, seats = roster.prelaunch(c, {}, ["a", "a1"], "standard", 0.0, capped=cap_a)
        self.assertEqual(runs, ["a1"])
        self.assertNotIn("a2", seats)

    def test_agy_lead_does_not_absent_the_agy_seat(self):
        c = cfg(a1={"runtime": "agy"})
        c["leads"] = {"l": {"runtime": "agy"}}
        runs, _ = roster.prelaunch(c, {}, ["a1"], "standard", 0.0, capped=NEVER)
        self.assertEqual(runs, ["a1"])

    def test_capped_seat_stands_in_once_dedupe(self):
        cap_a = lambda c, s, n, t: "seat a capped until 10:00" if n == "a" else None  # noqa: E731
        runs, seats = roster.prelaunch(CFG, {}, ["a", "o", "g", "x"], "standard", 0.0, capped=cap_a)
        self.assertEqual(runs, ["o", "g"])
        self.assertEqual((seats["a"]["reason"], seats["g"]["stand_in_for"], seats["x"]["reason"]),
                         ("usage cap", "a", "disabled"))

    def test_stand_in_added_when_not_on_roster(self):
        cap_a = lambda c, s, n, t: "capped" if n == "a" else None  # noqa: E731
        runs, seats = roster.prelaunch(CFG, {}, ["a", "o"], "standard", 0.0, capped=cap_a)
        self.assertEqual(runs, ["o", "g"])
        self.assertFalse(seats["g"]["on_roster"])
        self.assertTrue(seats["o"]["on_roster"])

    def test_capped_fallback_no_hop(self):
        cap = lambda c, s, n, t: "capped" if n in ("a", "g") else None  # noqa: E731
        runs, seats = roster.prelaunch(CFG, {}, ["a", "o"], "standard", 0.0, capped=cap)
        self.assertEqual(runs, ["o"])
        self.assertEqual(seats["a"]["reason"], "usage cap")
        self.assertNotIn("g", seats)

    def test_mid_run_cap_of_a_stand_in_no_second_hop(self):
        c = cfg()
        c["seats"]["g"]["fallback"] = "o"
        seats = {"a": {"status": "absent", "reason": "usage cap", "on_roster": True},
                 "g": {"status": "pending", "stand_in_for": "a", "on_roster": False}}
        self.assertIsNone(roster.standin(c, {}, "g", seats, "standard", 0.0, capped=NEVER))

    def test_mid_run_cap_of_original(self):
        seats = {"a": {"status": "pending", "on_roster": True}, "o": {"status": "pending", "on_roster": True}}
        self.assertEqual(roster.standin(CFG, {}, "a", seats, "standard", 0.0, capped=NEVER), "g")
        self.assertEqual(seats["g"], {"status": "pending", "stand_in_for": "a", "on_roster": False})

    def test_absent_fallback_not_marked(self):
        seats = {"a": {"status": "pending", "on_roster": True},
                 "g": {"status": "absent", "reason": "timeout", "on_roster": True}}
        self.assertIsNone(roster.standin(CFG, {}, "a", seats, "standard", 0.0, capped=NEVER))
        self.assertNotIn("stand_in_for", seats["g"])

    def test_mid_run_agy_stand_in_refused_while_agy_runs(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        c["seats"]["a"]["fallback"] = "a2"
        seats = {"a": {"status": "pending", "on_roster": True}, "a1": {"status": "pending", "on_roster": True}}
        self.assertIsNone(roster.standin(c, {}, "a", seats, "standard", 0.0, capped=NEVER))
        self.assertNotIn("a2", seats)
        seats["a1"]["status"] = "absent"  # the agy seat is gone: the stand-in may run
        self.assertEqual(roster.standin(c, {}, "a", seats, "standard", 0.0, capped=NEVER), "a2")

    def test_running_agy_seat_holds_the_agy_place(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        c["seats"]["a"]["fallback"] = "a2"
        seats = {"a": {"status": "absent", "reason": "usage cap", "on_roster": True},
                 "a1": {"status": "running", "on_roster": True}}   # a1 is live; a capped mid-run
        self.assertIsNone(roster.standin(c, {}, "a", seats, "standard", 0.0, capped=NEVER))
        self.assertNotIn("a2", seats)

    def test_capped_agy_seat_does_not_hold_the_agy_place(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        cap_a1 = lambda c_, s, n, t: "capped" if n == "a1" else None  # noqa: E731
        runs, seats = roster.prelaunch(c, {}, ["a1", "a2"], "standard", 0.0, capped=cap_a1)
        self.assertEqual(runs, ["a2"])
        self.assertEqual(seats["a1"]["reason"], "usage cap")

    def test_agy_fallback_on_roster_while_agy_runs(self):
        c = cfg(a1={"runtime": "agy"}, a2={"runtime": "agy"})
        c["seats"]["a"]["fallback"] = "a2"
        cap_a = lambda c_, s, n, t: "capped" if n == "a" else None  # noqa: E731
        runs, seats = roster.prelaunch(c, {}, ["a", "a1", "a2"], "standard", 0.0, capped=cap_a)
        self.assertEqual(runs, ["a1"])
        self.assertEqual(seats["a2"]["reason"], "one agy seat per panel")
        self.assertNotIn("stand_in_for", seats["a2"])

    def test_two_capped_seats_share_a_fallback_first_mark_kept(self):
        cap = lambda c, s, n, t: "capped" if n in ("a", "o") else None  # noqa: E731
        runs, seats = roster.prelaunch(CFG, {}, ["a", "o"], "standard", 0.0, capped=cap)
        self.assertEqual(runs, ["g"])
        self.assertEqual(seats["g"]["stand_in_for"], "a")

    def test_run_counts(self):
        self.assertEqual(roster.run_counts(CFG, ["a", "o", "x"], "standard"), (2, 1))
        self.assertEqual(roster.run_counts(CFG, ["a", "o", "g"], "standard"), (3, 0))
        self.assertEqual(roster.run_counts(CFG, ["r"], "standard"), (0, 0))
        self.assertEqual(roster.run_counts(CFG, ["r"], "deep"), (1, 0))

    def test_pending_verify(self):
        entries = {
            "a": {"verified": False, "failed_probe": "never verified"},
            "o": {"verified": False, "failed_probe": "key changed"},
            "g": {"verified": False, "failed_probe": "probe-skipped:fresh:g1"},
            "r": {"verified": False, "failed_probe": "unverified: pi version unreadable"},
            "x": {"verified": True, "failed_probe": None},
        }
        with mock.patch.object(roster.verify, "write_seats_json", return_value=entries) as w:
            self.assertEqual(roster.pending_verify(CFG, {}, ["a", "o", "g", "r", "x", "zz"]), ["a", "o"])
            self.assertEqual(w.call_count, 1)

    def test_verify_running(self):
        with tempfile.TemporaryDirectory() as state:
            os.makedirs(os.path.join(state, "seats"))
            self.assertFalse(roster.verify_running(state))
            code = ("import fcntl,os,sys,time;fd=os.open(sys.argv[1],os.O_RDONLY|os.O_CREAT);"
                    "fcntl.flock(fd,fcntl.LOCK_EX);print('ok',flush=True);time.sleep(30)")
            child = subprocess.Popen([sys.executable, "-c", code, os.path.join(state, "seats", "verify.lock")],
                                     stdout=subprocess.PIPE)
            try:
                child.stdout.readline()
                self.assertTrue(roster.verify_running(state))
            finally:
                child.kill()
                child.wait()
                child.stdout.close()
            self.assertFalse(roster.verify_running(state))

    def test_shortness(self):
        short, why = roster.shortness({"required": ["a"], "min_seats": 2}, {
            "a": {"status": "absent", "reason": "usage cap"}, "o": {"status": "ok"},
            "g": {"status": "ok", "stand_in_for": "a"}})
        self.assertEqual((short, why), (True, ["required seat a absent: usage cap"]))

    def test_shortness_count_and_clean(self):
        spec = {"required": ["a"], "min_seats": 3}
        short, why = roster.shortness(spec, {"a": {"status": "ok"}, "o": {"status": "ok"},
                                             "g": {"status": "absent", "reason": "timeout"}})
        self.assertEqual((short, why), (True, ["2 of 3 seats returned (at least 3)"]))
        self.assertEqual(roster.shortness({"required": [], "min_seats": 1}, {"a": {"status": "ok"}}), (False, []))


if __name__ == "__main__":
    unittest.main()
