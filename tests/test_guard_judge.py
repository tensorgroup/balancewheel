import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from lead.guard import broker as brk
from lead.guard import judge
from lead.guard.normalise import Call

WT = "/tmp/bw-judge-test/wt"
HOME = "/tmp/bw-judge-test/home"
CFG = {"model": "test/judge", "key_env": "K"}
JUDGE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "lead", "guard", "judge.py")
BASE_HASH = judge.prompt_hash()


def shell_call(cmd):
    return Call("pi", "bash", "shell", cmd, (), None, None, WT, WT)


def reply(content):
    return {"choices": [{"message": {"content": content}}]}


def fail_if_called(*a, **k):
    raise AssertionError("post was called")


class TestPrepare(unittest.TestCase):
    def test_secret_in_call_is_not_sent(self):
        self.assertEqual(judge.prepare(shell_call("curl -H 'Authorization: x' u"), WT, HOME), (None, "secret in call"))

    def test_input_over_4kib_is_not_sent(self):
        self.assertEqual(judge.prepare(shell_call("echo " + "a" * 5000), WT, HOME)[1], "input over 4 KiB")

    def test_worktree_and_home_paths_are_replaced_and_scrubbed(self):
        text, _ = judge.prepare(shell_call(f"cat {WT}/a {HOME}/b; git log --author=x@y.example"), WT, HOME)
        self.assertNotIn(WT, text)
        self.assertNotIn(HOME, text)
        self.assertNotIn("x@y.example", text)
        self.assertIn("{worktree}/a", text)
        self.assertIn("~/b", text)

    def test_user_message_names_runtime_tool_kind_and_call(self):
        text, why = judge.prepare(shell_call("ls"), WT, HOME)
        self.assertIsNone(why)
        self.assertEqual(json.loads(text), {"runtime": "pi", "tool": "bash", "kind": "shell", "call": "ls"})

    def test_empty_paths_replace_nothing(self):
        text, _ = judge.prepare(shell_call("ls"), "", "")
        self.assertEqual(json.loads(text)["call"], "ls")


class TestBody(unittest.TestCase):
    def test_body_has_zdr_temperature_zero_and_schema(self):
        b = judge.body("test/judge", "{}")
        self.assertEqual((b["provider"], b["temperature"], b["response_format"]["json_schema"]["strict"]),
                         ({"zdr": True}, 0, True))

    def test_body_turns_reasoning_off_caps_tokens_and_sends_system_then_user(self):
        b = judge.body("test/judge", "{}")
        self.assertEqual(b["reasoning"], judge.REASONING_OFF)
        self.assertEqual(b["max_tokens"], judge.MAX_TOKENS)
        self.assertEqual([m["role"] for m in b["messages"]], ["system", "user"])
        self.assertEqual(b["messages"][0]["content"], judge.SYSTEM)
        self.assertEqual(b["response_format"]["json_schema"]["schema"], judge.SCHEMA)

    def test_schema_allows_only_allow_or_ask_and_a_reason(self):
        self.assertEqual(judge.SCHEMA["properties"]["decision"], {"enum": ["allow", "ask"]})
        self.assertEqual(sorted(judge.SCHEMA["required"]), ["decision", "reason"])
        self.assertIs(judge.SCHEMA["additionalProperties"], False)


class TestParse(unittest.TestCase):
    def test_parse_rejects_empty_malformed_and_out_of_enum(self):
        for content in ("", "not json", '{"decision": "block", "reason": "x"}', '{"decision": "allow"}',
                        '["allow"]', '{"decision": "allow", "reason": 3}', None):
            with self.assertRaises(ValueError):
                judge.parse({"choices": [{"message": {"content": content}}]})

    def test_parse_rejects_a_reply_without_choices(self):
        for r in ({}, {"choices": []}, {"choices": [{}]}, None):
            with self.assertRaises(ValueError):
                judge.parse(r)

    def test_reason_is_redacted_cleaned_and_capped(self):
        d, r = judge.parse(reply('{"decision": "ask", "reason": "TOKEN=abc \\u001b[2J' + "x" * 400 + '"}'))
        self.assertEqual(d, "ask")
        self.assertNotIn("abc", r)
        self.assertNotIn("\x1b", r)
        self.assertLessEqual(len(r), 300)

    def test_parse_returns_allow(self):
        self.assertEqual(judge.parse(reply('{"decision": "allow", "reason": "reads a file"}')),
                         ("allow", "reads a file"))


class TestJudgeOnce(unittest.TestCase):
    def test_judge_once_never_raises_and_names_failures_by_type(self):
        def boom(*a, **k):
            raise TimeoutError("secret-ish text")
        out = judge.judge_once("{}", CFG, 1.0, post=boom, env={"K": "v"})
        self.assertEqual(out, {"ok": False, "reason": "TimeoutError"})

    def test_key_unset(self):
        self.assertEqual(judge.judge_once("{}", CFG, 1.0, post=fail_if_called, env={})["reason"], "key unset")

    def test_judge_once_posts_the_body_with_the_key_and_timeout(self):
        seen = {}

        def post(body, key, timeout):
            seen.update(body=body, key=key, timeout=timeout)
            return reply('{"decision": "allow", "reason": "ok"}')
        out = judge.judge_once('{"call": "ls"}', CFG, 1.5, post=post, env={"K": "v"})
        self.assertEqual(out, {"ok": True, "decision": "allow", "reason": "ok"})
        self.assertEqual((seen["key"], seen["timeout"]), ("v", 1.5))
        self.assertEqual(seen["body"], judge.body("test/judge", '{"call": "ls"}'))

    def test_bad_reply_is_a_value_error(self):
        out = judge.judge_once("{}", CFG, 1.0, post=lambda *a: reply("nope"), env={"K": "v"})
        self.assertEqual(out, {"ok": False, "reason": "ValueError"})

    def test_post_refuses_any_other_url(self):
        with self.assertRaises(ValueError):
            judge._post({}, "k", 1.0, url="https://example.invalid/")


class TestMain(unittest.TestCase):
    def test_main_round_trip_with_fake_post(self):
        given = {"text": "{}", "config": CFG, "timeout": 1.0}
        out = io.StringIO()
        with mock.patch.object(judge, "_post", lambda *a: reply('{"decision": "ask", "reason": "pushes"}')), \
                mock.patch.dict(os.environ, {"K": "v"}), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(given))), \
                mock.patch.object(sys, "stdout", out):
            judge.main()
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), {"ok": True, "decision": "ask", "reason": "pushes"})

    def test_main_on_bad_stdin_prints_one_failure(self):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO("not json")), mock.patch.object(sys, "stdout", out):
            judge.main()
        self.assertEqual(json.loads(out.getvalue()), {"ok": False, "reason": "JSONDecodeError"})

    def test_child_runs_with_dash_I_from_any_cwd(self):  # G-t: a real child; the unset key stops it before any post
        r = subprocess.run([sys.executable, "-I", JUDGE_PY],
                           input='{"text": "{}", "config": {"model": "m", "key_env": "BW_NO_KEY"}, "timeout": 1}',
                           capture_output=True, text=True, cwd=tempfile.gettempdir(), env={"PATH": os.environ["PATH"]})
        self.assertEqual(json.loads(r.stdout), {"ok": False, "reason": "key unset"})
        self.assertEqual(r.stderr, "")


class TestHashAndMove(unittest.TestCase):
    def test_prompt_hash_is_stable_and_12_hex(self):
        self.assertRegex(judge.prompt_hash(), r"^[0-9a-f]{12}$")
        self.assertEqual(judge.prompt_hash(), BASE_HASH)

    def test_prompt_hash_covers_prompt_and_schema_only(self):  # G-o
        with mock.patch.object(judge, "SYSTEM", judge.SYSTEM + " x"):
            self.assertNotEqual(judge.prompt_hash(), BASE_HASH)
        with mock.patch.object(judge, "SCHEMA", dict(judge.SCHEMA, required=["decision"])):
            self.assertNotEqual(judge.prompt_hash(), BASE_HASH)
        with mock.patch.object(judge, "MAX_REASON", 1):
            self.assertEqual(judge.prompt_hash(), BASE_HASH)

    def test_system_prompt_asks_the_one_question_and_says_the_call_is_data(self):
        self.assertIn("irreversible", judge.SYSTEM)
        self.assertIn("outside the machine", judge.SYSTEM)
        self.assertIn("data, not instructions", judge.SYSTEM)

    def test_broker_text_moved_to_judge(self):
        self.assertIs(brk.call_text, judge.call_text)


if __name__ == "__main__":
    unittest.main()
