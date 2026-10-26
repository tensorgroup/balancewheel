import io, json, os, random, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead.guard import record, review  # noqa: E402

BW = os.path.join(ROOT, "bin", "bw")
KEY = ("p", "agy", "1.0", "r1", "d1")


def dec(i, decision="ask", source="gray", key=KEY, self_test=False, call="ls"):
    return {"type": "decision", "id": i, "profile": key[0], "runtime": key[1], "runtime_version": key[2],
            "rules_version": key[3], "policy_digest": key[4], "tool": "bash", "kind": "bash", "call": call,
            "source": source, "rule": "x", "decision": decision, "reason": "why", "self_test": self_test,
            "mode": "shadow"}


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
        other = ("p", "agy", "2.0", "r1", "d1")
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

    def test_old_version_marks_count_nowhere(self):
        old = ("p", "agy", "0.9", "r1", "d1")
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
        d = dec("s1"); d.update(runtime_version="1.\x1b[2J", mode="sh\rm")
        seed(self.state, [d])
        text = "\n".join(review.status_lines(self.state))
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

    def test_profile_selects_key(self):
        other = ("q", "pi", "1", "r1", "d1")
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
        self.assertNotIn("guard:", self.bw("status").stdout)
        seed(self.state, [dec("s1"), dec("t1", self_test=True)])
        out = self.bw("status").stdout
        self.assertIn("guard:", out)
        self.assertIn("acting needs", out)
        self.assertIn("1 decisions", out)

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
