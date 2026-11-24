import inspect, io, json, os, random, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead.guard import record, review  # noqa: E402

BW = os.path.join(ROOT, "bin", "bw")
KEY = ("p", "agy", "r1", "d1", "test/judge", "h1")
CFG = {"profiles": {"p": {"lead_order": [], "protected_branches": ["main"], "deploy_commands": []}},
       "guard": {"mode": {"p": "auto"}}}
NOW = 1_800_000_000.0


def dec(i, decision="ask", source="gray", key=KEY, self_test=False, call="ls", rule_decision=None):
    return {"type": "decision", "id": i, "profile": key[0], "runtime": key[1], "runtime_version": "1.0",
            "rules_version": key[2], "policy_digest": key[3], "judge_model": key[4], "prompt_hash": key[5],
            "tool": "bash", "kind": "bash", "call": call, "source": source, "rule": "x", "decision": decision,
            "rule_decision": decision if rule_decision is None else rule_decision, "reason": "why", "self_test": self_test, "mode": "shadow"}


def seed(state, decisions, marks=()):
    for d in decisions:
        record.append(state, d)
    for m in marks:
        record.append(state, {"type": "mark", **m})


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def loaded(self):
        return record.load(self.state)["decisions"]

    def test_pick_one_key_unmarked_and_sample_size(self):
        other = ("p", "agy", "r2", "d1", "test/judge", "h1")
        seed(self.state, [dec("s1"), dec("s2"), dec("s3"), dec("a1", "allow"), dec("a2", "allow"),
                          dec("o1", key=other), dec("t1", self_test=True)],
             [{"decision_id": "s1", "verdict": "right", "sampled": False}])
        got = review.pick(self.loaded(), KEY, 20, 5, random.Random(1))
        ids = sorted(d["id"] for d in got)
        self.assertEqual(ids, ["a1", "a2", "s2", "s3"])
        self.assertTrue(all(d.get("sampled") for d in got if d["decision"] == "allow"))
        self.assertEqual(len(review.pick(self.loaded(), KEY, 1, 1, random.Random(1))), 2)

    def test_safe_distinguishes_typed_escape_from_real_one(self):
        self.assertEqual(review.safe("\\x1b"), "\\\\x1b")
        self.assertEqual(review.safe("\x1b"), "\\x1b")
        self.assertNotEqual(review.safe("\\x1b"), review.safe("\x1b"))

    def test_old_rules_marks_count_nowhere(self):
        old = ("p", "agy", "r0", "d1", "test/judge", "h1")
        seed(self.state, [dec("o1", key=old), dec("n1")],
             [{"decision_id": "o1", "verdict": "wrong", "sampled": False}])
        s = review.summary(self.loaded(), KEY)
        self.assertEqual((s["reviewed"], s["false_stops"]), (0, 0))

    def test_orphan_mark_ignored_with_warning(self):
        seed(self.state, [dec("n1")], [{"decision_id": "ghost", "verdict": "wrong", "sampled": False}])
        out, err = io.StringIO(), io.StringIO()
        old, sys.stderr = sys.stderr, err
        try:
            review.run(self.state, list_only=True, out=out)
        finally:
            sys.stderr = old
        self.assertIn("orphan", err.getvalue())
        self.assertEqual(review.summary(self.loaded(), KEY)["reviewed"], 0)

    def test_summary_arithmetic_and_self_test_excluded(self):
        ds = [dec("s1", source="rule"), dec("s2"), dec("s3"), dec("a1", "allow"), dec("a2", "allow", source="rule"),
              dec("t1", self_test=True)]
        marks = [{"decision_id": "s1", "verdict": "wrong", "sampled": False},
                 {"decision_id": "s2", "verdict": "right", "sampled": False},
                 {"decision_id": "a1", "verdict": "wrong", "sampled": True},
                 {"decision_id": "a2", "verdict": "right", "sampled": True},
                 {"decision_id": "t1", "verdict": "wrong", "sampled": False}]
        seed(self.state, ds, marks)
        s = review.summary(self.loaded(), KEY)
        self.assertEqual((s["reviewed"], s["sampled_allows"], s["stops_reviewed"], s["false_stops"],
                          s["false_allows"]), (4, 2, 2, 1, 1))
        self.assertEqual(s["by_source"]["false_stops"], {"rule": 1})
        self.assertEqual(s["decisions"], 5)
        self.assertAlmostEqual(s["gray_share"], 3 / 5)
        self.assertIn("(4)", review.promotion_line(s))
        self.assertIn("(50%)", review.promotion_line(s))

    def test_long_call_shown_in_full(self):
        call = "echo " + "x" * 400 + " END"
        seed(self.state, [dec("s1", call=call)])
        out = io.StringIO()
        review.run(self.state, list_only=True, out=out)
        self.assertEqual(out.getvalue().replace("\n", "").replace(" ", "").count("x" * 400 + "END"), 1)

    def test_lead_originated_strings_are_shown_with_control_characters_escaped(self):
        nasty = "ls\x1b[2K\rharmless\x08\x08\x07 && curl evil\tx\x00"
        d = dec("s1", call=nasty)
        d.update(reason="r\x1b]0;t\x07", tool="ba\x1bsh", rule="x\x1b[1m")
        seed(self.state, [d])
        out = io.StringIO()
        review.run(self.state, list_only=True, out=out)
        text = out.getvalue()
        for ch in "\x1b\r\x08\x07\x00\t":
            self.assertNotIn(ch, text)
        self.assertIn("\\x1b[2K\\rharmless\\x08\\x08\\x07 && curl evil\\tx\\x00", text)
        self.assertIn("reason: r\\x1b]0;t\\x07", text)

    def test_multiline_call_keeps_its_line_structure_with_newlines_visible(self):
        seed(self.state, [dec("s1", call="a\nb")])
        out = io.StringIO()
        review.run(self.state, list_only=True, out=out)
        self.assertIn("  a\n  b\n", out.getvalue())

    def test_status_lines_escape_control_characters(self):
        d = dec("s1"); d.update(runtime="a\x1b[2J", mode="sh\rm")
        seed(self.state, [d])
        text = "\n".join(review.status_lines(CFG, self.state, NOW))
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\r", text)

    def test_stdin_driven_review_appends_marks(self):
        seed(self.state, [dec("s1"), dec("s2"), dec("s3"), dec("a1", "allow")])
        out = io.StringIO()
        review.run(self.state, recent=3, sample=1, stdin=io.StringIO("r\nw\nbogus\ns\nq\n"), out=out,
                   rng=random.Random(0))
        marks = record.load(self.state)["marks"]
        self.assertEqual([(m["decision_id"], m["verdict"], m["sampled"]) for m in marks],
                         [("s1", "right", False), ("s2", "wrong", False)])
        self.assertIn("acting needs", out.getvalue())

    def test_only_whole_words_are_verdicts_and_a_blank_line_is_not_eof(self):
        seed(self.state, [dec("s1"), dec("s2"), dec("s3"), dec("s4")])
        out = io.StringIO()
        review.run(self.state, recent=4, sample=0, out=out, rng=random.Random(0),
                   stdin=io.StringIO("really not sure\nwhat?\n\n  \nRIGHT \nw\nskip\nquit\n"))
        marks = record.load(self.state)["marks"]
        self.assertEqual([(m["decision_id"], m["verdict"]) for m in marks], [("s1", "right"), ("s2", "wrong")])
        self.assertEqual(out.getvalue().count("[r]ight"), 8)  # s1: 4 re-prompts + the verdict; s2, s3, s4: one each

    def test_eof_behaves_like_quit_and_sampled_flag(self):
        seed(self.state, [dec("a1", "allow")])
        review.run(self.state, stdin=io.StringIO("w\n"), out=io.StringIO(), rng=random.Random(0))
        review.run(self.state, stdin=io.StringIO(""), out=io.StringIO(), rng=random.Random(0))
        marks = record.load(self.state)["marks"]
        self.assertEqual([(m["verdict"], m["sampled"]) for m in marks], [("wrong", True)])

    def test_list_writes_nothing(self):
        seed(self.state, [dec("s1")])
        before = open(record.path(self.state), "rb").read()
        review.run(self.state, list_only=True, out=io.StringIO())
        self.assertEqual(open(record.path(self.state), "rb").read(), before)

    def test_evidence_key_drops_runtime_version(self):
        a, b = dec("a1", key=KEY), dict(dec("a2", key=KEY), runtime_version="9.9")
        self.assertEqual(record.evidence_key(a), record.evidence_key(b))

    def test_evidence_key_has_judge_fields(self):
        self.assertNotEqual(record.evidence_key(dec("a")), record.evidence_key(dict(dec("b"), prompt_hash="other")))
        self.assertNotEqual(record.evidence_key(dec("a")), record.evidence_key(dict(dec("b"), judge_model="other")))

    def test_policy_digest_ignores_on_judge_failure(self):  # G-f: two arguments, as today (build ruling, 2026-11-23)
        self.assertEqual(list(inspect.signature(record.policy_digest).parameters),
                         ["protected_branches", "deploy_commands"])
        self.assertEqual(record.policy_digest(["main"], []), "eab241176238")  # the value before this task, at 032ec44

    def test_is_gray_and_rule_decision(self):
        self.assertFalse(record.is_gray({"source": "rule"}))
        for s in ("judge", "judge-failed", "judge-skipped", "gray"):
            self.assertTrue(record.is_gray({"source": s}))
        self.assertEqual(record.rule_decision({"decision": "allow", "rule_decision": "ask"}), "ask")
        self.assertEqual(record.rule_decision({"decision": "ask"}), "ask")  # a legacy row has no rule_decision

    def test_pick_samples_judge_allows_as_allows(self):
        seed(self.state, [dec("j1", decision="allow", source="judge", rule_decision="ask")])
        got = review.pick(self.loaded(), KEY, 0, 5, random.Random(1))
        self.assertEqual([(d["id"], d.get("sampled")) for d in got], [("j1", True)])

    def test_pick_stratifies_judge_allows_so_the_gate_can_be_met(self):
        seed(self.state, [dec(f"j{n}", "allow", source="judge") for n in range(10)] +
             [dec(f"r{n}", "allow", source="rule") for n in range(100)])
        for seed_n in range(20):
            got = review.pick(self.loaded(), KEY, 0, 5, random.Random(seed_n))
            self.assertEqual(len(got), 5)
            self.assertGreaterEqual(sum(d["source"] == "judge" for d in got), 3)
            self.assertTrue(all(d["sampled"] for d in got))

    def test_pick_fills_from_either_side_when_one_is_short(self):
        seed(self.state, [dec("j0", "allow", source="judge")] + [dec(f"r{n}", "allow", source="rule") for n in range(9)])
        got = review.pick(self.loaded(), KEY, 0, 5, random.Random(0))
        self.assertEqual(sorted(d["source"] for d in got), ["judge", "rule", "rule", "rule", "rule"])
        other = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, other, ignore_errors=True)
        seed(other, [dec(f"j{n}", "allow", source="judge") for n in range(9)] + [dec("r0", "allow", source="rule")])
        got = review.pick(record.load(other)["decisions"], KEY, 0, 5, random.Random(0))
        self.assertEqual(sorted(d["source"] for d in got), ["judge"] * 4 + ["rule"])

    def test_promotion_line_names_all_six_conditions(self):
        from lead.guard import gate
        conds = [gate.Condition(n, ok, f"{n} detail") for n, ok in
                 (("mode", True), ("marks", False), ("judge", True), ("eval", None), ("attack", True),
                  ("interactive", False), ("cooldown", True))]
        s = review.summary([], KEY)
        line = review.promotion_line(s, conds)
        for name in ("marks", "judge", "eval", "attack", "interactive", "cooldown"):
            self.assertIn(f"{name} ", line)
        self.assertIn("marks open (marks detail)", line)
        self.assertIn("eval unknown (eval detail)", line)
        self.assertIn("cooldown ok (cooldown detail)", line)
        self.assertIn("≥50 reviewed", review.promotion_line(s))  # without conditions: as before

    def test_status_says_no_judge_when_model_unset(self):
        seed(self.state, [dec("s1")])
        self.assertIn("  no judge: gray calls are recorded as asks, nothing is stopped; the guard stays shadow "
                      "(set guard.model to let it act)", review.status_lines(CFG, self.state, NOW))
        with_model = dict(CFG, guard={"mode": {"p": "auto"}, "model": "test/judge"})
        self.assertFalse(any("no judge" in l for l in review.status_lines(with_model, self.state, NOW)))

    def test_status_lists_the_gate_conditions_for_the_current_key(self):
        from lead.guard import gate
        key = gate.current_key(CFG, "p", "pi")
        seed(self.state, [dec("s1", key=key)])
        text = "\n".join(review.status_lines(CFG, self.state, NOW))
        self.assertIn("judge open (no judge: stays shadow)", text)
        self.assertIn("attack unknown", text)

    def test_status_after_a_demotion_says_why_and_what_is_needed(self):  # G-m
        from lead.guard import gate
        since = 1_796_040_000.0  # 2026-11-30 12:00 UTC
        entry = {"mode": "shadow", "since": since, "reason": "a sampled allow was marked wrong",
                 "key": {"evidence": [], "verify": ""},
                 "demotions": [{"key": [], "ts": since, "reason": "a sampled allow was marked wrong", "kind": "evidence"}]}
        with open(gate.path(self.state), "w") as f:
            json.dump({"p": {"pi": entry}}, f)
        self.assertIn("  guard shadow for profile p (pi) since 2026-11-30: a sampled allow was marked wrong; "
                      "acting again after 2026-12-07 and 5 new marks", review.status_lines(CFG, self.state, NOW))

    def test_status_after_a_demotion_prints_the_real_requirement_per_reason(self):
        from lead.guard import gate
        since = 1_796_040_000.0  # 2026-11-30 12:00 UTC
        cases = (
            ("a sampled allow was marked wrong (j0)", "evidence",
             "the allow re-marked right, if the mark was a mistake (`bw guard review --remark j0`), or a key change",
             "; acting again needs the allow re-marked right, if the mark was a mistake (`bw guard review --remark j0`), "
             "or a key change, and 5 new marks after 2026-12-07"),
            ("the eval is fail on its current key", "evidence", "an eval pass (`bw guard eval --runtime pi`)",
             "; acting again needs an eval pass (`bw guard eval --runtime pi`), and 5 new marks after 2026-12-07"),
            ("false stops reached 12% of reviewed stops", "evidence", "false stops under 10% of reviewed stops",
             "; acting again needs false stops under 10% of reviewed stops, and 5 new marks after 2026-12-07"),
            ("guard key unverified; run `bw guard verify pi`", "key", None, ""),  # the reason names the fix
            ("the evidence key changed", "key", "the gate to hold on the new key",
             "; acting again needs the gate to hold on the new key"))
        for reason, kind, need, tail in cases:
            with self.subTest(reason):
                dem = {"key": [], "ts": since, "reason": reason, "kind": kind, "need": need}
                entry = {"mode": "shadow", "since": since, "reason": reason, "key": {"evidence": [], "verify": ""},
                         "demotions": [dem]}
                with open(gate.path(self.state), "w") as f:
                    json.dump({"p": {"pi": entry}}, f)
                self.assertIn(f"  guard shadow for profile p (pi) since 2026-11-30: {reason}{tail}",
                              review.status_lines(CFG, self.state, NOW))

    def test_remark_corrects_a_wrong_mark(self):
        seed(self.state, [dec("a1", "allow", source="judge")], [{"decision_id": "a1", "verdict": "wrong", "sampled": True}])
        self.assertEqual(review.summary(self.loaded(), KEY)["false_allows"], 1)
        out = io.StringIO()
        self.assertEqual(review.remark(self.state, "a1", stdin=io.StringIO("r\n"), out=out), 0)
        self.assertIn("its mark is wrong", out.getvalue())
        s = review.summary(self.loaded(), KEY)
        self.assertEqual((s["false_allows"], s["sampled_allows"], s["reviewed"]), (0, 1, 1))
        self.assertEqual(record.load(self.state)["marks"][-1],
                         {**record.load(self.state)["marks"][-1], "verdict": "right", "sampled": True, "remark": True})

    def test_remark_quit_writes_nothing_and_unknown_or_unmarked_ids_fail(self):
        seed(self.state, [dec("a1", "allow"), dec("s1")], [{"decision_id": "a1", "verdict": "wrong", "sampled": True}])
        self.assertEqual(review.remark(self.state, "a1", stdin=io.StringIO("q\n"), out=io.StringIO()), 0)
        self.assertEqual(len(record.load(self.state)["marks"]), 1)
        with mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(review.remark(self.state, "nope", stdin=io.StringIO("r\n"), out=io.StringIO()), 2)
            self.assertEqual(review.remark(self.state, "s1", stdin=io.StringIO("r\n"), out=io.StringIO()), 2)
        self.assertIn("no guard decision nope", err.getvalue())
        self.assertIn("has no mark yet", err.getvalue())
        self.assertEqual(len(record.load(self.state)["marks"]), 1)

    def test_gray_share_counts_every_non_rule_source(self):  # judge, judge-failed, judge-skipped, legacy gray
        seed(self.state, [dec("j", "allow", source="judge"), dec("f", source="judge-failed"),
                          dec("k", source="judge-skipped"), dec("g", source="gray"), dec("r", "block", source="rule")])
        self.assertAlmostEqual(review.summary(self.loaded(), KEY)["gray_share"], 0.8)

    LEGACY = {"type": "decision", "id": "old", "profile": "p", "runtime": "agy", "runtime_version": "0.85",
              "rules_version": "r1", "policy_digest": "d1", "source": "gray", "rule": "gray:option",
              "decision": "ask", "mode": "shadow"}  # before this change: no rule_decision, no judge fields

    def test_legacy_rows_stay_out_of_a_judge_key(self):
        seed(self.state, [self.LEGACY, dec("new")])
        self.assertEqual([d["id"] for d in review.of_key(self.loaded(), KEY)], ["new"])
        self.assertTrue(review.status_lines(CFG, self.state, NOW))  # no KeyError on the legacy row

    def test_legacy_rules_only_rows_and_marks_carry_into_the_no_judge_key(self):
        rules_only = ("p", "agy", "r1", "d1", None, None)
        seed(self.state, [self.LEGACY, dec("new", key=rules_only)],
             [{"decision_id": "old", "verdict": "right", "sampled": False}])
        self.assertEqual(record.evidence_key(self.LEGACY), rules_only)
        self.assertEqual([d["id"] for d in review.of_key(self.loaded(), rules_only)], ["old", "new"])
        s = review.summary(self.loaded(), rules_only)
        self.assertEqual((s["decisions"], s["reviewed"], s["stops_reviewed"]), (2, 1, 1))
        self.assertEqual(record.rule_decision(self.loaded()[0]), "ask")
        self.assertTrue(review.status_lines(CFG, self.state, NOW))

    def test_key_label_names_the_six_parts(self):
        self.assertEqual(review.key_label(KEY), "profile=p runtime=agy rules=r1 policy=d1 judge=test/judge prompt=h1")

    def test_profile_selects_key(self):
        other = ("q", "pi", "r1", "d1", None, None)
        seed(self.state, [dec("s1"), dec("o1", key=other)])
        self.assertEqual(review.select_key(self.loaded()), other)
        self.assertEqual(review.select_key(self.loaded(), "p"), KEY)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.env = {**os.environ, "BW_STATE_DIR": self.state,
                    "BALANCEWHEEL_CONFIG": os.path.join(ROOT, "balancewheel.example.json"),
                    "BW_ORCA_SUPPORT": os.path.join(self.tmp.name, "no-orca"),
                    "BW_CCSWITCH_DIR": os.path.join(self.tmp.name, "no-cc-switch")}

    def bw(self, *args, inp=None):
        return subprocess.run([sys.executable, BW, *args], env=self.env, capture_output=True, text=True, input=inp)

    def test_status_guard_lines_only_with_decisions(self):
        first = self.bw("status").stdout  # the example is auto with no judge: only the note, no evidence line
        self.assertNotIn("decisions ·", first)
        self.assertIn("no judge: gray calls are recorded as asks", first)
        seed(self.state, [dec("s1"), dec("t1", self_test=True)])
        out = self.bw("status").stdout
        self.assertIn("guard:", out)
        self.assertIn("acting needs", out)
        self.assertIn("1 decisions", out)

    def test_cli_remark(self):
        seed(self.state, [dec("s1")], [{"decision_id": "s1", "verdict": "wrong", "sampled": False}])
        r = self.bw("guard", "review", "--remark", "s1", inp="r\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(record.load(self.state)["decisions"][0]["mark"]["verdict"], "right")

    def test_cli_review_pipe(self):
        seed(self.state, [dec("s1")])
        r = self.bw("guard", "review", "--sample", "0", inp="w\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(record.load(self.state)["marks"][0]["verdict"], "wrong")

    def test_negative_counts_are_rejected(self):
        for flag in ("--sample", "--recent"):
            r = self.bw("guard", "review", flag, "-1")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("must be", r.stderr)

    def test_help_lists_guard_subcommands(self):
        out = self.bw("--help").stdout
        self.assertIn("guard review", out)
        self.assertIn("guard install", out)


if __name__ == "__main__":
    unittest.main()
