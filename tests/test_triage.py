import io, json, os, subprocess, sys, unittest, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import triage  # noqa: E402

GOOD = {"strong_case": 0.9, "only_person_knows": 0.2, "outward_or_irreversible": 0.05, "high_stakes": 0.02}
ASK = "Name it parse_row or parseRow?\nRecommendation: parse_row, the module uses snake_case."


def cfg(**kw):
    return {"model": "m", "key_env": "K", "thresholds": dict(triage.DEFAULT_THRESHOLDS), **kw}


def reply(scores, cost=0.00002):
    return {"answers": {k: {"type": "noul", "noul": v} for k, v in scores.items()}, "usage": {"cost": cost}}


class Policy(unittest.TestCase):
    def test_each_threshold_can_stop_a_decision(self):
        th = triage.DEFAULT_THRESHOLDS
        self.assertEqual(triage.policy(GOOD, th), ("decide", "strong case"))
        for name, bad in (("strong_case", 0.59), ("only_person_knows", 0.71),
                          ("outward_or_irreversible", 0.31), ("high_stakes", 0.31)):
            self.assertEqual(triage.policy({**GOOD, name: bad}, th), ("ask", name))

    def test_boundaries_are_inclusive(self):
        th = triage.DEFAULT_THRESHOLDS
        self.assertEqual(triage.policy(dict(th), th)[0], "decide")


class Prefilter(unittest.TestCase):
    def test_no_recommendation_goes_to_the_user(self):
        self.assertEqual(triage.prefilter("which db?"), "no recommendation")

    def test_a_recommendation_with_no_text_does_not_count(self):
        self.assertEqual(triage.prefilter("which db?\nRecommendation:   "), "no recommendation")

    def test_a_recommendation_mid_line_does_not_count(self):
        self.assertEqual(triage.prefilter("my Recommendation: x"), "no recommendation")

    def test_denylisted_words_go_to_the_user_whatever_the_case(self):
        for word in ("deploy", "Deploying", "push", "publish", "send", "email", "delete", "DROP", "payment",
                     "charge", "refund", "secret", "password", "credential", "production", "prod", "migration"):
            self.assertEqual(triage.prefilter(f"{word} this?\nRecommendation: yes, it is fine"), "denylist", word)

    def test_words_only_match_whole(self):
        self.assertIsNone(triage.prefilter("Is product the right name, or dropdown?\nRecommendation: dropdown"))

    def test_too_long_goes_to_the_user(self):
        self.assertEqual(triage.prefilter("x" * 2001 + "\nRecommendation: y"), "too long")

    def test_a_good_ask_passes(self):
        self.assertIsNone(triage.prefilter(ASK))


class Scrub(unittest.TestCase):
    def test_tokens_emails_and_phones_do_not_leave(self):
        out = triage.scrub("API_KEY=abc123 mail a@b.co or +1 (844) 638-0111, Authorization: Bearer zzz")
        for leaked in ("abc123", "a@b.co", "638-0111", "zzz"):
            self.assertNotIn(leaked, out)


class Judge(unittest.TestCase):
    def test_decides_on_good_scores_and_sends_only_scrubbed_text(self):
        seen = {}

        def post(body, key, timeout):
            seen.update(body=body, key=key)
            return reply(GOOD)
        v = triage.judge(ASK + " cc a@b.co", cfg(), post=post, env={"K": "secret"})
        self.assertEqual(v["verdict"], "decide")
        self.assertEqual(v["scores"], GOOD)
        self.assertEqual(v["cost"], 0.00002)
        self.assertEqual(seen["key"], "secret")
        self.assertEqual(seen["body"]["model"], "m")
        self.assertEqual(sorted(seen["body"]["questions"]), sorted(GOOD))
        self.assertEqual(seen["body"]["provider"], {"zdr": True})
        self.assertNotIn("a@b.co", json.dumps(seen["body"]))

    def test_prefiltered_text_makes_no_request(self):
        def post(*a):
            raise AssertionError("no model call")
        self.assertEqual(triage.judge("drop it?\nRecommendation: yes", cfg(), post=post, env={"K": "s"}),
                         {"verdict": "ask", "reason": "denylist"})

    def test_a_missing_key_fails_toward_the_user(self):
        v = triage.judge(ASK, cfg(), post=lambda *a: reply(GOOD), env={})
        self.assertEqual((v["verdict"], v["reason"]), ("ask", "key unset"))
        self.assertTrue(v["failed"])

    def test_a_request_failure_is_named_never_quoted(self):
        def post(*a):
            raise urllib.error.URLError("https://x?key=SECRET")
        v = triage.judge(ASK, cfg(), post=post, env={"K": "SECRET"})
        self.assertEqual(v, {"verdict": "ask", "reason": "URLError", "failed": True})

    def test_a_reply_without_all_four_scores_asks(self):
        for bad in ({"answers": {}}, {"answers": None}, {}, reply({**GOOD, "high_stakes": "low"}), reply({**GOOD, "high_stakes": 2}),
                    {"answers": {k: "yes" for k in GOOD}}, reply({**GOOD, "high_stakes": True})):
            v = triage.judge(ASK, cfg(), post=lambda *a: bad, env={"K": "s"})
            self.assertEqual((v["verdict"], v.get("failed")), ("ask", True), bad)


class Post(unittest.TestCase):
    def test_refuses_any_other_url(self):
        with self.assertRaises(ValueError):
            triage._post({}, "k", 1, url="https://elsewhere.example/")


class Stdio(unittest.TestCase):
    def test_a_child_with_no_key_prints_one_verdict(self):
        env = {k: v for k, v in os.environ.items() if k != "NOKEY"}
        p = subprocess.run([sys.executable, "-I", str(ROOT / "lead" / "triage.py")],
                           input=json.dumps({"text": ASK, "config": cfg(key_env="NOKEY")}), text=True,
                           capture_output=True, env=env, timeout=20)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["reason"], "key unset")

    def test_bad_input_prints_an_ask(self):
        p = subprocess.run([sys.executable, "-I", str(ROOT / "lead" / "triage.py")], input="not json",
                           text=True, capture_output=True, timeout=20)
        self.assertEqual(json.loads(p.stdout)["verdict"], "ask")
