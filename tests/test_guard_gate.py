"""The guard gate: promotion to acting, demotion to shadow, and the refusal, on synthetic state."""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead import containment  # noqa: E402
from lead.guard import evaluate, gate, interactive, record, review  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402

DAY = 86400
NOW = 1_800_000_000.0
VKEY = {"runtime": "pi", "runtime_version": "pi 1.0", "guard_version": "g1"}
VKEY2 = {"runtime": "pi", "runtime_version": "pi 1.1", "guard_version": "g1"}
PROFILE = {"lead_order": [], "protected_branches": ["main"], "deploy_commands": []}


def cfg(mode="auto", model="test/judge", branches=("main",)):
    guard = {"mode": {"p": mode}}
    if model:
        guard["model"] = model
    return {"profiles": {"p": dict(PROFILE, protected_branches=list(branches))}, "guard": guard}


CFG_AUTO, CFG_ACTING, CFG_SHADOW = cfg("auto"), cfg("acting"), cfg("shadow")


class GateCase(unittest.TestCase):
    def setUp(self):
        self.state = self.fresh_dir()

    def fresh_dir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def seed(self, state=None, config=CFG_AUTO, stops=30, wrong_stops=0, judge=10, rule=10, wrong_allows=0,
             mark_ts=NOW - 10 * DAY, eval_status="pass", attack=True, checked=True, row_mode="shadow"):
        state = state or self.state
        key = gate.current_key(config, "p", "pi")
        fields = dict(zip(record.EVIDENCE_KEY, key))

        def add(i, decision, source, verdict, sampled):
            record.append(state, {"type": "decision", "id": i, **fields, "decision": decision, "source": source,
                                  "rule_decision": "ask" if source == "judge" else decision, "self_test": False,
                                  "mode": row_mode, "call": "x"})
            record.append(state, {"type": "mark", "decision_id": i, "verdict": verdict, "sampled": sampled,
                                  "ts": mark_ts})
        for n in range(stops):
            add(f"s{n}", "ask", "rule", "wrong" if n < wrong_stops else "right", False)
        for n in range(judge):
            add(f"j{n}", "allow", "judge", "wrong" if n < wrong_allows else "right", True)
        for n in range(rule):
            add(f"r{n}", "allow", "rule", "right", True)
        if eval_status:
            evaluate.write(state, "pi", gate.eval_key(config, "pi"), {"status": eval_status})
        if attack:
            containment.record_pass(state, "pi", VKEY, "/evidence", store=gate.VERIFY_STORE)
        if checked:
            containment.record_pass(state, "pi", interactive.check_key("pi", ADAPTERS["pi"]), "/evidence",
                                    store=interactive.STORE)
        return state

    def fresh(self, config=CFG_AUTO, **kw):
        return config, self.seed(state=self.fresh_dir(), config=config, **kw)

    def promote(self, config=CFG_AUTO):
        o = gate.decide(config, self.state, "p", "pi", VKEY, NOW)
        self.assertEqual(o.mode, "acting", gate.conditions(config, self.state, "p", "pi", VKEY, NOW))
        return o

    def mark(self, ids, verdict="right", sampled=False, ts=NOW + 100, state=None):
        for i in ids:
            record.append(state or self.state, {"type": "mark", "decision_id": i, "verdict": verdict,
                                                "sampled": sampled, "ts": ts})

    def stored(self, state=None):
        return gate.read(state or self.state, "p", "pi")


# breakers: each returns (config, state) with exactly one condition open
def pin_shadow(t): return t.fresh(CFG_SHADOW)
def too_few_reviewed(t): return t.fresh(stops=29)
def too_few_sampled(t): return t.fresh(stops=31, rule=9)
def false_stops_10pct(t): return t.fresh(wrong_stops=3)
def one_false_allow(t): return t.fresh(wrong_allows=1)
def four_judge_allows_with_a_model(t): return t.fresh(judge=4, rule=16)
def eval_failed(t): return t.fresh(eval_status="fail")
def eval_incomplete(t): return t.fresh(eval_status="incomplete")
def no_attack_pass(t): return t.fresh(attack=False)
def no_interactive(t): return t.fresh(checked=False)


def demoted_3_days_ago(t):
    config, state = t.fresh()
    entry = {"mode": "shadow", "since": NOW - 3 * DAY, "reason": "x", "key": {"evidence": [], "verify": ""},
             "demotions": [{"key": list(gate.current_key(config, "p", "pi")), "ts": NOW - 3 * DAY,
                            "reason": "x", "kind": "evidence"}]}
    with open(gate.path(state), "w") as f:
        json.dump({"p": {"pi": entry}}, f)
    t.mark([f"s{n}" for n in range(5)], ts=NOW - 2 * DAY, state=state)
    return config, state


# triggers: each takes a promoted self.state and returns (config, state) with one trigger holding
def sampled_allow_wrong(t):
    t.mark(["j0"], verdict="wrong", sampled=True)
    return CFG_AUTO, t.state


def evidence_key_changed(t): return cfg(branches=("main", "release")), t.state


def verify_key_unverified(t):
    containment.record_pass(t.state, "pi", VKEY2, "/evidence", store=gate.VERIFY_STORE)
    return CFG_AUTO, t.state


def eval_fails(t):
    evaluate.write(t.state, "pi", gate.eval_key(CFG_AUTO, "pi"), {"status": "fail"})
    return CFG_AUTO, t.state


def cumulative_false_stops_10pct(t):
    t.mark(["s0", "s1", "s2"], verdict="wrong")
    return CFG_AUTO, t.state


class Promotion(GateCase):
    def test_all_six_hold_promotes_once_with_event_and_line(self):
        self.seed()
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW)
        self.assertEqual(o.mode, "acting"); self.assertEqual(o.event["to"], "acting")
        self.assertIn("guard acting for profile p (pi)", o.line)
        self.assertEqual(o.line, "bw: guard acting for profile p (pi): 50 reviewed, 0 false allows, 0% false stops")
        self.assertEqual(o.event, {"type": "guard_mode", "profile": "p", "runtime": "pi", "from": "shadow",
                                   "to": "acting", "reason": "the gate held",
                                   "evidence": {"reviewed": 50, "sampled_allows": 20, "false_stop_pct": 0.0,
                                                "false_allows": 0}})
        self.assertIsNone(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW).event)  # no second event
        st = self.stored()
        self.assertEqual((st["mode"], st["key"]["evidence"], st["key"]["verify"]),
                         ("acting", list(gate.current_key(CFG_AUTO, "p", "pi")), gate.verify_sha(VKEY)))

    def test_conditions_are_in_gate_order(self):
        self.seed()
        names = [c.name for c in gate.conditions(CFG_AUTO, self.state, "p", "pi", VKEY, NOW)]
        self.assertEqual(names, ["mode", "marks", "judge", "eval", "attack", "interactive", "cooldown"])

    def test_each_condition_alone_blocks_promotion(self):
        for breaker in (pin_shadow, too_few_reviewed, too_few_sampled, false_stops_10pct, one_false_allow,
                        four_judge_allows_with_a_model, eval_failed, eval_incomplete, no_attack_pass,
                        no_interactive, demoted_3_days_ago):
            with self.subTest(breaker.__name__):
                self.assertEqual(gate.decide(*breaker(self), "p", "pi", VKEY, NOW).mode, "shadow")

    def test_the_fixture_without_a_breaker_promotes(self):  # so each breaker is what blocks
        self.assertEqual(gate.decide(*self.fresh(), "p", "pi", VKEY, NOW).mode, "acting")
        self.assertEqual(gate.decide(*self.fresh(CFG_ACTING), "p", "pi", VKEY, NOW).mode, "acting")

    def test_pinned_acting_refuses_on_an_unverified_key(self):
        config, state = self.fresh(CFG_ACTING, attack=False)
        o = gate.decide(config, state, "p", "pi", VKEY, NOW)
        self.assertTrue(o.refuse)
        self.assertIn("bw guard verify pi", o.line)
        self.assertIsNotNone(gate.refuses(config, state, "p", "pi", VKEY))
        self.assertIsNone(gate.refuses(CFG_AUTO, state, "p", "pi", VKEY))
        containment.record_pass(state, "pi", VKEY, "/evidence", store=gate.VERIFY_STORE)
        self.assertIsNone(gate.refuses(config, state, "p", "pi", VKEY))

    def test_pinned_acting_with_an_open_gate_runs_shadow_and_names_what_is_open(self):
        config, state = self.fresh(CFG_ACTING, eval_status="fail")
        o = gate.decide(config, state, "p", "pi", VKEY, NOW)
        self.assertEqual((o.mode, o.refuse, o.event), ("shadow", False, None))
        self.assertIn("eval (fail)", o.line)

    def test_auto_with_unverified_key_runs_shadow_and_says_so(self):
        config, state = self.fresh(attack=False)
        o = gate.decide(config, state, "p", "pi", VKEY, NOW)
        self.assertEqual((o.mode, o.refuse, o.event), ("shadow", False, None))
        self.assertIn("guard key unverified: shadow", o.line)

    def test_eval_incomplete_never_promotes(self):  # G-d: even after a pass on the same key
        self.seed()
        evaluate.write(self.state, "pi", gate.eval_key(CFG_AUTO, "pi"), {"status": "incomplete"})
        self.assertTrue(evaluate.passed_on(self.state, "pi", gate.eval_key(CFG_AUTO, "pi")))
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW).mode, "shadow")

    def test_no_judge_model_never_promotes(self):  # G-m
        config, state = self.fresh(cfg(model=None))
        o = gate.decide(config, state, "p", "pi", VKEY, NOW)
        self.assertEqual(o.mode, "shadow")
        judge = [c for c in gate.conditions(config, state, "p", "pi", VKEY, NOW) if c.name == "judge"][0]
        self.assertEqual((judge.ok, judge.detail), (False, "no judge: stays shadow"))

    def test_no_judge_key_has_no_judge_allow_floor(self):  # the floor applies only with a model
        config, state = self.fresh(cfg(model=None), judge=0, rule=20)
        marks = [c for c in gate.conditions(config, state, "p", "pi", VKEY, NOW) if c.name == "marks"][0]
        self.assertTrue(marks.ok, marks.detail)

    def test_a_pinned_shadow_profile_writes_nothing(self):
        config, state = self.fresh(CFG_SHADOW)
        self.assertEqual(gate.decide(config, state, "p", "pi", VKEY, NOW), gate.Outcome("shadow", False, None, None))
        self.assertFalse(os.path.exists(gate.path(state)))


class Demotion(GateCase):
    def test_each_demotion_trigger(self):
        for trigger in (sampled_allow_wrong, evidence_key_changed, verify_key_unverified, eval_fails,
                        cumulative_false_stops_10pct):
            with self.subTest(trigger.__name__):
                self.state = self.fresh_dir()
                self.seed()
                self.promote()
                o = gate.decide(*trigger(self), "p", "pi", VKEY, NOW)
                self.assertEqual((o.mode, o.event["from"], o.event["to"]), ("shadow", "acting", "shadow"))
                self.assertIn("guard shadow for profile p (pi)", o.line)
                self.assertEqual(self.stored()["mode"], "shadow")

    def test_demotion_kinds(self):
        for trigger, kind in ((sampled_allow_wrong, "evidence"), (evidence_key_changed, "key"),
                              (verify_key_unverified, "key"), (eval_fails, "evidence"),
                              (cumulative_false_stops_10pct, "evidence")):
            with self.subTest(trigger.__name__):
                self.state = self.fresh_dir()
                self.seed()
                self.promote()
                gate.decide(*trigger(self), "p", "pi", VKEY, NOW + 5)
                d = self.stored()["demotions"][-1]
                self.assertEqual((d["kind"], d["ts"]), (kind, NOW + 5))
                self.assertEqual(d["key"], list(gate.current_key(CFG_AUTO, "p", "pi")))

    def test_verified_runtime_upgrade_keeps_acting_without_an_event(self):
        self.seed()
        self.promote()
        containment.record_pass(self.state, "pi", VKEY2, "/evidence", store=gate.VERIFY_STORE)
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY2, NOW + 10)
        self.assertEqual((o.mode, o.event), ("acting", None))
        self.assertEqual(self.stored()["key"]["verify"], gate.verify_sha(VKEY2))
        self.assertEqual(self.stored().get("demotions"), [])

    def test_key_change_demotion_does_not_start_the_cooldown(self):
        self.seed()
        self.promote()
        containment.record_pass(self.state, "pi", VKEY2, "/evidence", store=gate.VERIFY_STORE)
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 10).mode, "shadow")  # VKEY lost its pass
        self.assertEqual(self.stored()["demotions"][-1]["kind"], "key")
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY2, NOW + 20)
        self.assertEqual((o.mode, o.event["to"]), ("acting", "acting"))

    def test_evidence_demotion_needs_seven_days_and_new_marks(self):  # G-e
        self.seed()
        self.promote()
        eval_fails(self)
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW).mode, "shadow")
        evaluate.write(self.state, "pi", gate.eval_key(CFG_AUTO, "pi"), {"status": "pass"})
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 7 * DAY + 1).mode, "shadow")
        self.mark([f"s{n}" for n in range(5)], ts=NOW + 100)
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 6 * DAY).mode, "shadow")
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 7 * DAY + 1)
        self.assertEqual((o.mode, o.event["to"]), ("acting", "acting"))

    def test_four_new_marks_are_not_enough(self):
        self.seed()
        self.promote()
        eval_fails(self)
        gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW)
        evaluate.write(self.state, "pi", gate.eval_key(CFG_AUTO, "pi"), {"status": "pass"})
        self.mark([f"s{n}" for n in range(4)], ts=NOW + 100)
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 8 * DAY).mode, "shadow")

    def test_next_start_after_a_demotion_stays_shadow(self):  # G-e: same marks, one start later
        self.seed()
        self.promote()
        o1 = gate.decide(*sampled_allow_wrong(self), "p", "pi", VKEY, NOW)
        o2 = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 60)
        self.assertEqual((o1.mode, o2.mode, o2.event), ("shadow", "shadow", None))

    def test_promotion_and_demotion_use_one_false_stop_measure(self):  # G-e: 2 of 60 stops wrong, all in acting
        self.seed(stops=60, wrong_stops=2, row_mode="acting")
        self.promote()
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 60)
        self.assertEqual((o.mode, o.event), ("acting", None))

    def test_pin_back_to_shadow_resets_the_stored_mode(self):  # B8.2
        self.seed()
        self.promote()
        o = gate.decide(CFG_SHADOW, self.state, "p", "pi", VKEY, NOW + 10)
        self.assertEqual((o.mode, o.event["from"], o.event["to"], o.event["reason"]),
                         ("shadow", "acting", "shadow", "pinned shadow"))
        self.assertEqual((self.stored()["mode"], self.stored()["reason"]), ("shadow", "pinned shadow"))
        self.assertIsNone(gate.decide(CFG_SHADOW, self.state, "p", "pi", VKEY, NOW + 20).event)  # one event
        eval_fails(self)  # a later auto starts from shadow: an open gate is no demotion
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 30)
        self.assertEqual((o.mode, o.event), ("shadow", None))

    def test_eval_incomplete_keeps_acting_only_after_a_pass_on_the_same_key(self):  # G-d
        self.seed()
        self.promote()
        ek = gate.eval_key(CFG_AUTO, "pi")
        evaluate.write(self.state, "pi", ek, {"status": "incomplete"})  # last_pass stays on ek
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 10).mode, "acting")
        with open(evaluate.path(self.state), "w") as f:  # an incomplete with no pass on this key
            json.dump({"pi": {"key": ek, "result": {"status": "incomplete"}, "ts": NOW, "last_pass": None}}, f)
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 20)
        self.assertEqual((o.mode, o.event["to"]), ("shadow", "shadow"))

    def test_last_ten_demotions_are_kept(self):
        self.seed()
        for n in range(12):
            self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + n * 10 * DAY).mode, "acting")
            gate.decide(*verify_key_unverified(self), "p", "pi", VKEY, NOW + n * 10 * DAY + 1)
            containment.record_pass(self.state, "pi", VKEY, "/evidence", store=gate.VERIFY_STORE)
        self.assertEqual(len(self.stored()["demotions"]), 10)


class FixWave(GateCase):
    def test_a_false_allow_demotion_names_the_remark_and_a_remark_reopens_the_gate(self):
        import io
        self.seed()
        self.promote()
        o = gate.decide(*sampled_allow_wrong(self), "p", "pi", VKEY, NOW)
        self.assertIn("a sampled allow was marked wrong (j0)", o.line)
        self.assertIn("`bw guard review --remark j0`", o.line)
        self.assertIn("or a key change", o.line)
        self.assertIn("bw guard review --remark j0", self.stored()["demotions"][-1]["need"])
        later = NOW + 7 * DAY + 1
        self.mark([f"s{n}" for n in range(5)], ts=NOW + 100)
        self.assertEqual(gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, later).mode, "shadow")  # j0 still wrong
        self.assertEqual(review.remark(self.state, "j0", stdin=io.StringIO("r\n"), out=io.StringIO()), 0)
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, later)
        self.assertEqual((o.mode, o.event["to"]), ("acting", "acting"))

    def test_each_demotion_reason_names_its_fix(self):
        for trigger, words in ((verify_key_unverified, "run `bw guard verify pi`"),
                               (eval_fails, "an eval pass (`bw guard eval --runtime pi`)"),
                               (cumulative_false_stops_10pct, "false stops under 10% of reviewed stops")):
            with self.subTest(trigger.__name__):
                self.state = self.fresh_dir()
                self.seed()
                self.promote()
                o = gate.decide(*trigger(self), "p", "pi", VKEY, NOW)
                self.assertIn(words, o.line)

    def test_no_judge_model_prints_one_plain_line(self):
        config, state = self.fresh(cfg(model=None))
        self.assertEqual(gate.decide(config, state, "p", "pi", VKEY, NOW),
                         gate.Outcome("shadow", False, gate.NO_JUDGE_LINE, None))
        self.assertEqual(gate.NO_JUDGE_LINE,
                         "guard: no judge model set — the guard stays in shadow (set guard.model to let it act)")
        config, state = self.fresh(cfg(mode="shadow", model=None))
        self.assertIsNone(gate.decide(config, state, "p", "pi", VKEY, NOW).line)

    def test_acting_with_no_current_eval_result_says_so(self):
        self.seed()
        self.promote()
        os.remove(evaluate.path(self.state))
        o = gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 10)
        self.assertEqual((o.mode, o.event), ("acting", None))
        self.assertIn("no eval result on the current key; run `bw guard eval --runtime pi`", o.line)

    def test_decide_reads_the_log_once_and_not_at_all_for_a_shadow_pin(self):
        from unittest import mock
        self.seed()
        real, calls = record.load, []

        def load(*a):
            calls.append(a)
            return real(*a)
        with mock.patch.object(record, "load", load):
            gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW)
            self.assertEqual(len(calls), 1)
            calls.clear()
            gate.decide(CFG_AUTO, self.state, "p", "pi", VKEY, NOW + 1)  # acting now: the trigger path
            self.assertEqual(len(calls), 1)
            calls.clear()
            config, state = self.fresh(CFG_SHADOW)
            calls.clear()
            gate.decide(config, state, "p", "pi", None, NOW)
            self.assertEqual(calls, [])

    def test_the_last_evidence_demotion_per_key_outlives_the_cap(self):
        ev = {"key": ["k1"], "ts": 1, "reason": "x", "kind": "evidence"}
        keys = [{"key": ["k1"], "ts": 10 + n, "reason": "y", "kind": "key"} for n in range(12)]
        other = {"key": ["k2"], "ts": 0, "reason": "x", "kind": "evidence"}
        got = gate._trim([other, ev] + keys)
        self.assertEqual(got, [other, ev] + keys[-gate.KEEP_DEMOTIONS:])
        self.assertEqual(gate._trim(keys[:5]), keys[:5])
        newer = dict(ev, ts=50)
        self.assertEqual(gate._trim([ev] + keys + [newer]), keys[-9:] + [newer])  # a newer one replaces it
        self.assertEqual(gate.last_evidence_demotion({"demotions": gate._trim([ev] + keys)}, ("k1",)), ev)


class Summary(GateCase):
    def test_summary_counts_judge_sampled_allows_and_marks_since(self):
        self.seed()
        key = gate.current_key(CFG_AUTO, "p", "pi")
        self.mark(["s0", "s1"], ts=NOW + 5)
        s = review.summary(record.load(self.state)["decisions"], key, since=NOW)
        self.assertEqual((s["judge_sampled_allows"], s["marked_since"]), (10, 2))
        self.assertEqual(review.summary(record.load(self.state)["decisions"], key)["marked_since"], 0)

    def test_marks_since_reads_the_ts_store_append_writes(self):  # `bw guard review` marks carry no ts of their own
        import time
        self.seed(mark_ts=1000.0)  # the seeded marks are long before now
        key = gate.current_key(CFG_AUTO, "p", "pi")
        before = time.time() - 5
        for i in ("s0", "s1"):
            record.append(self.state, {"type": "mark", "decision_id": i, "verdict": "right", "sampled": False})
        ds = record.load(self.state)["decisions"]
        self.assertIsInstance([d for d in ds if d["id"] == "s0"][0]["mark"]["ts"], str)
        self.assertEqual(review.summary(ds, key, since=before)["marked_since"], 2)
        self.assertEqual(review.summary(ds, key, since=time.time() + 5)["marked_since"], 0)
        self.assertEqual((review.epoch("2026-11-30T12:00:00Z"), review.epoch("bad"), review.epoch(None)),
                         (1_796_040_000.0, 0.0, 0.0))

    def test_load_keeps_the_mark_ts(self):
        self.seed()
        d = [d for d in record.load(self.state)["decisions"] if d["id"] == "s0"][0]
        self.assertEqual(d["mark"]["ts"], NOW - 10 * DAY)


if __name__ == "__main__":
    unittest.main()
