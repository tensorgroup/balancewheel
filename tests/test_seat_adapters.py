import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from lead import sandbox
from lead.codepaths import KIMI_CODE, PI_CODE
from metrics import config
from seats import adapters, grants

FIXTURES = Path(__file__).parent / "fixtures" / "seats"
# Task 2's recorded values (docs/reference-implementation.md, "Seat runtimes under `bw seat`").
RECORDED_PERMISSION_FLAG = {"codex": ["--dangerously-bypass-approvals-and-sandbox"],
                            "claude-code": ["--permission-mode", "bypassPermissions"],
                            "pi": [],  # pi has no approval prompts
                            "kimi": ["--auto"],
                            "agy": ["--dangerously-skip-permissions"]}
RECORDED_ENV_KEY = {"claude-code": "CLAUDE_CODE_OAUTH_TOKEN", "pi": "OPENROUTER_API_KEY"}
HOME = "/st/seats/homes/astra"


def fixture(runtime, name):
    base = FIXTURES / runtime / name
    return (base.with_suffix(".stdout").read_text().splitlines(),
            base.with_suffix(".stderr").read_text().splitlines())


def has_run(argv, flags):
    n = len(flags)
    return any(argv[i:i + n] == flags for i in range(len(argv) - n + 1))


class Shared(unittest.TestCase):
    def test_broken_lines_skipped(self):
        self.assertEqual(adapters.json_lines(['{"a":1}', '{"b":', "\x00\xff", "", "5"]), [{"a": 1}])

    def test_effort(self):
        seat = {"runtime": "codex", "model": "m", "effort": "high"}
        self.assertEqual(adapters.effort(seat, False), "high")
        self.assertEqual(adapters.effort(seat, True), "max")
        self.assertIsNone(adapters.effort({"runtime": "codex", "model": "m"}, False))

    def test_transient_429_is_not_a_cap(self):  # R21
        self.assertFalse(adapters.usage_limit(["HTTP 429 Too Many Requests", "rate limit hit, retrying"]))
        self.assertTrue(adapters.usage_limit(["You have exceeded your current quota"]))

    def test_limit_wording(self):
        for line in ["You've hit your limit · resets 3pm", "Claude AI usage limit reached|1900000000"]:
            self.assertTrue(adapters.usage_limit([line]), line)

    def test_deep_nesting_skipped(self):
        self.assertEqual(adapters.json_lines(["[" * 200000 + "]" * 200000, '{"a":1}']), [{"a": 1}])

    def test_session_id_shape(self):
        for good in ["sess-0001", "00000000-0000-4000-8000-000000000001", "a.b:c_d"]:
            self.assertTrue(adapters.valid_session(good), good)
        for bad in ["", "-rf", "a b", "a;b", "a\n", None, 5, "--resume"]:
            self.assertFalse(adapters.valid_session(bad), bad)

    def test_code_dirs_made_without_following_links(self):
        with tempfile.TemporaryDirectory() as home:
            for runtime, ad in adapters.SEAT_ADAPTERS.items():
                adapters.make_code_dirs(runtime, home)
                adapters.make_code_dirs(runtime, home)  # a second run is fine
                for rel in adapters.CODE_DIRS[runtime]:
                    self.assertTrue(os.path.isdir(os.path.join(home, rel)), rel)
                    self.assertIn(os.path.join(home, rel), ad.grants({"model": "m"}, home).deny_write)
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as elsewhere:
            os.symlink(elsewhere, os.path.join(home, ".claude"))
            with self.assertRaises(OSError):
                adapters.make_code_dirs("claude-code", home)
            self.assertEqual(os.listdir(elsewhere), [])

    def test_file_instruction_is_short(self):
        self.assertIn("/s/input.md", adapters.FILE_INSTRUCTION.format(path="/s/input.md"))

    def test_login_missing(self):
        self.assertIsNone(adapters.login_missing("codex", {}))
        self.assertIsNone(adapters.login_missing("claude-code", {"CLAUDE_CODE_OAUTH_TOKEN": "t"}))
        self.assertEqual(adapters.login_missing("claude-code", {}),
                         "no login: run `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN")
        self.assertEqual(adapters.login_missing("claude-code", {"CLAUDE_CODE_OAUTH_TOKEN": ""}),
                         "no login: run `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN")

    def test_login_missing_pi(self):
        self.assertEqual(adapters.login_missing("pi", {}), "no login: export OPENROUTER_API_KEY")
        self.assertIsNone(adapters.login_missing("pi", {"OPENROUTER_API_KEY": "k"}))
        for rt in ("kimi", "agy"):
            self.assertIsNone(adapters.login_missing(rt, {}))


class Codex(unittest.TestCase):
    ad = adapters.SEAT_ADAPTERS["codex"]
    SEAT = {"runtime": "codex", "model": "m-test", "effort": "high", "args": []}

    def test_argv_and_deep(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/input.md", False),
                         ["codex", "exec", "--json", "-m", "m-test", "-c", "model_reasoning_effort=high",
                          "--dangerously-bypass-approvals-and-sandbox", "-"])
        self.assertIn("model_reasoning_effort=max", self.ad.argv(self.SEAT, "/s/input.md", True))

    def test_no_effort_no_flag(self):
        argv = self.ad.argv({"runtime": "codex", "model": "m-test", "args": ["--x"]}, "/s/i", False)
        self.assertNotIn("-c", argv)
        self.assertEqual(argv[-2:], ["--x", "-"])

    def test_resume_repeats_flags(self):
        seat = dict(self.SEAT, args=["--seat-arg"])
        new, res = self.ad.argv(seat, "/s/i", False), self.ad.resume_argv(seat, "sid", "/s/i", False)
        self.assertEqual(res[:3], ["codex", "exec", "resume"])
        for flag in ["--json", "-m", "m-test", "model_reasoning_effort=high", "--seat-arg", *self.ad.inner_sandbox]:
            self.assertIn(flag, new)
            self.assertIn(flag, res)
        self.assertEqual(res[-2:], ["sid", "-"])
        for flag in ["-s", "-C", "-p"]:  # codex exec resume has none of these
            self.assertNotIn(flag, res)

    def test_no_input_text_on_argv(self):
        self.assertTrue(all(len(a) < 200 for a in self.ad.argv(self.SEAT, "/s/i", False)))
        self.assertEqual(self.ad.prompt_via, "stdin")

    def test_parse_ok(self):
        out, err = fixture("codex", "ok")
        self.assertEqual(self.ad.session_id(out, err), "sess-0001")
        self.assertEqual(self.ad.answer(out), "No significant findings.")
        usage = self.ad.usage(out)
        self.assertEqual(usage["tokens_in"], 1200)
        self.assertEqual(usage["tokens_out"], 300)
        self.assertEqual(self.ad.errors(out, err), [])

    def test_parse_resume(self):
        out, err = fixture("codex", "resume")
        self.assertEqual(self.ad.session_id(out, err), "sess-0001")
        self.assertEqual(self.ad.answer(out), "The fix holds.")

    def test_usage_limit_on_error_lines(self):
        out, err = fixture("codex", "usage_limit")
        self.assertTrue(self.ad.usage_limit(self.ad.errors(out, err)))
        self.assertEqual(self.ad.answer(out), "")

    def test_error_not_a_cap(self):
        out, err = fixture("codex", "error")
        errs = self.ad.errors(out, err)
        self.assertTrue(errs)
        self.assertFalse(self.ad.usage_limit(errs))

    def test_prompt_echo_is_not_an_error(self):  # the echoed prompt reviews quota code and says "usage limit"
        out, err = fixture("codex", "prompt_echo")
        self.assertTrue(any("usage limit" in line for line in err))
        self.assertFalse(self.ad.usage_limit(self.ad.errors(out, err)))
        self.assertEqual(self.ad.answer(out), "No significant findings.")

    def test_unsafe_session_id_starts_fresh(self):
        for bad in ["--x", "a b", ""]:
            self.assertEqual(self.ad.resume_argv(self.SEAT, bad, "/s/i", False), self.ad.argv(self.SEAT, "/s/i", False))
        line = json.dumps({"type": "thread.started", "thread_id": "-oops"})
        self.assertIsNone(self.ad.session_id([line], []))

    def test_hostile_lines(self):
        lines = [json.dumps({"type": "turn.completed", "usage": {"input_tokens": "lots", "output_tokens": True}}),
                 json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}),
                 json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": 5}}),
                 json.dumps({"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": 7}}),
                 json.dumps({"type": "item.completed", "item": {"type": "file_change", "status": 1, "changes": "x"}}),
                 json.dumps({"type": "item.completed", "item": {"type": "file_change", "status": "failed",
                                                                "changes": [{"path": 3}, {"path": "w1"}, "z"]}}),
                 json.dumps({"type": "error", "message": {"x": 1}}),
                 json.dumps({"type": "turn.failed", "error": ["x"]}),
                 json.dumps({"type": "item.completed", "item": "x"})]
        self.assertEqual(self.ad.usage(lines), {"tokens_in": 5, "tokens_out": 2})
        self.assertEqual(self.ad.answer(lines), "")
        self.assertEqual(self.ad.tool_results(lines), ["", "file_change : ", "file_change failed: w1"])
        self.assertEqual(self.ad.errors(lines, []), [])

    def test_error_line_after_echo_counts(self):
        err = ["user", "check the usage limit path", "codex", "ERROR: You have hit your usage limit."]
        self.assertEqual(self.ad.errors([], err), ["ERROR: You have hit your usage limit."])

    def test_grants_inside_home(self):
        g = self.ad.grants(self.SEAT, HOME)
        self.assertTrue(g.write)
        self.assertTrue(all(p == HOME or p.startswith(HOME + "/") for p in g.write))
        self.assertEqual(g.env, {"CODEX_HOME": HOME})
        self.assertTrue(g.private_home)

    def test_code_paths_in_home_write_denied(self):  # R14
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(sorted(g.deny_write), sorted(f"{HOME}/{n}" for n in adapters.CODEX_CODE))
        self.assertNotIn(f"{HOME}/sessions", g.deny_write)  # the session store stays writable
        self.assertEqual(sorted(self.ad.hook_files(HOME)), sorted(g.deny_write))

    def test_home_env(self):
        with mock.patch.object(adapters, "CODEX_CA_BUNDLE", "/no/such/bundle.pem"):
            self.assertEqual(self.ad.home_env(HOME), {"CODEX_HOME": HOME})

    def test_ca_bundle_file_set_unless_the_user_set_one(self):
        # Under nono, codex 0.164 cannot load the system root store: every HTTPS connect fails
        # ("workspace routing discovery failed"). With SSL_CERT_FILE it loads the PEM bundle and runs.
        with tempfile.NamedTemporaryFile(suffix=".pem") as f, \
                mock.patch.object(adapters, "CODEX_CA_BUNDLE", f.name), \
                mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.ad.home_env(HOME), {"CODEX_HOME": HOME, "SSL_CERT_FILE": f.name})
            for key in ("SSL_CERT_FILE", "CODEX_CA_CERTIFICATE"):
                with mock.patch.dict(os.environ, {key: "/user/ca.pem"}):
                    self.assertEqual(self.ad.home_env(HOME), {"CODEX_HOME": HOME})  # the user's CA wins
        self.assertEqual(sorted(self.ad.env_keys), ["CODEX_CA_CERTIFICATE", "SSL_CERT_FILE"])
        self.assertEqual(adapters.CODEX_CA_BUNDLE, "/etc/ssl/cert.pem")

    def test_credentials_refresh(self):  # R11: auth.json holds a refresh token
        [(real, name, refreshes)] = self.ad.credentials
        self.assertEqual(name, "auth.json")
        self.assertTrue(real.endswith("/auth.json"))
        self.assertTrue(refreshes)

    def test_permission_flag_present(self):  # R19
        self.assertTrue(has_run(self.ad.argv(self.SEAT, "/s/i", False), RECORDED_PERMISSION_FLAG["codex"]))
        self.assertTrue(has_run(self.ad.resume_argv(self.SEAT, "sid", "/s/i", False),
                                RECORDED_PERMISSION_FLAG["codex"]))

    def test_tool_results_not_prose(self):
        out, _ = fixture("codex", "ok")
        results = self.ad.tool_results(out)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("PROBE:g1\n"))
        self.assertIn("Operation not permitted", results[0])
        self.assertFalse(any("findings" in r or "probe now" in r for r in results))

    def test_project_code(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.ad.project_code(d), [])
            os.makedirs(os.path.join(d, ".codex", "skills"))
            Path(d, ".codex", "config.toml").write_text("x = 1\n")
            self.assertEqual(self.ad.project_code(d),
                             [os.path.join(d, ".codex", "config.toml"), os.path.join(d, ".codex", "skills")])

    def test_static_fields(self):
        self.assertEqual(self.ad.version_argv, ["codex", "--version"])
        self.assertEqual(self.ad.backend, "any")
        self.assertEqual(self.ad.ignore_flags, [])


class ClaudeCode(unittest.TestCase):
    ad = adapters.SEAT_ADAPTERS["claude-code"]
    SEAT = {"runtime": "claude-code", "model": "m-test", "effort": "high", "args": []}

    def test_argv(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/input.md", False),
                         ["claude", "-p", "--model", "m-test", "--output-format", "stream-json", "--verbose",
                          "--permission-mode", "bypassPermissions",
                          "--safe-mode", "--setting-sources", "user", "--strict-mcp-config"])

    def test_no_effort_flag_even_deep(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/i", True), self.ad.argv(self.SEAT, "/s/i", False))
        self.assertNotIn("--effort", self.ad.argv(self.SEAT, "/s/i", True))

    def test_resume_repeats_flags(self):
        seat = dict(self.SEAT, args=["--seat-arg"])
        new, res = self.ad.argv(seat, "/s/i", False), self.ad.resume_argv(seat, "sid", "/s/i", False)
        self.assertEqual(res, new + ["--resume", "sid"])

    def test_no_input_text_on_argv(self):
        self.assertTrue(all(len(a) < 200 for a in self.ad.argv(self.SEAT, "/s/i", False)))
        self.assertEqual(self.ad.prompt_via, "stdin")

    def test_backend(self):  # the login is an env token, not the keychain
        self.assertEqual(self.ad.backend, "any")
        self.assertEqual(self.ad.credentials, [])

    def test_env_key(self):
        self.assertIn(RECORDED_ENV_KEY["claude-code"], self.ad.env_keys)

    def test_home_is_private_without_config_dir(self):  # ruling S-claude: HOME = seat home, no CLAUDE_CONFIG_DIR
        g = self.ad.grants(self.SEAT, HOME)
        self.assertTrue(g.private_home)
        self.assertEqual(self.ad.home_env(HOME), {})
        self.assertEqual(g.env, {})

    def test_grants_inside_home(self):
        g = self.ad.grants(self.SEAT, HOME)
        self.assertTrue(g.write)
        self.assertTrue(all(p == HOME or p.startswith(HOME + "/") for p in g.write))

    def test_code_paths_in_home_write_denied(self):  # R14
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(sorted(g.deny_write), sorted(f"{HOME}/.claude/{n}" for n in adapters.CLAUDE_CODE))
        self.assertNotIn(f"{HOME}/.claude/projects", g.deny_write)
        self.assertEqual(sorted(self.ad.hook_files(HOME)), sorted(g.deny_write))

    def test_permission_flag_present(self):  # R19
        self.assertTrue(has_run(self.ad.argv(self.SEAT, "/s/i", False), RECORDED_PERMISSION_FLAG["claude-code"]))

    def test_ignore_flags_in_argv(self):  # R10: the control removes these
        argv = self.ad.argv(self.SEAT, "/s/i", False)
        self.assertTrue(self.ad.ignore_flags)
        self.assertTrue(all(f in argv for f in self.ad.ignore_flags))

    def test_parse_ok(self):
        out, err = fixture("claude-code", "ok")
        self.assertEqual(self.ad.session_id(out, err), "sess-0002")
        self.assertEqual(self.ad.answer(out), "No significant findings.")
        usage = self.ad.usage(out)
        self.assertEqual(usage["tokens_in"], 1200)
        self.assertEqual(usage["tokens_out"], 300)
        self.assertEqual(usage["cost_usd"], 0.05)
        self.assertEqual(usage["rate_limits"]["status"], "allowed")
        self.assertEqual(self.ad.errors(out, err), [])

    def test_parse_resume(self):
        out, err = fixture("claude-code", "resume")
        self.assertEqual(self.ad.session_id(out, err), "sess-0002")
        self.assertEqual(self.ad.answer(out), "The fix holds.")

    def test_usage_limit_on_error_lines(self):
        out, err = fixture("claude-code", "usage_limit")
        self.assertTrue(self.ad.usage_limit(self.ad.errors(out, err)))
        self.assertEqual(self.ad.answer(out), "")

    def test_error_not_a_cap(self):
        out, err = fixture("claude-code", "error")
        errs = self.ad.errors(out, err)
        self.assertTrue(any("Not logged in" in e for e in errs))
        self.assertFalse(self.ad.usage_limit(errs))
        self.assertEqual(self.ad.answer(out), "")

    def test_tool_results_not_prose(self):
        out, _ = fixture("claude-code", "ok")
        results = self.ad.tool_results(out)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("PROBE:g1\n"))
        self.assertIn("Operation not permitted", results[0])

    def test_rejected_rate_limit_is_a_cap(self):
        rejected = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"}})
        allowed = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"}})
        self.assertTrue(self.ad.usage_limit(self.ad.errors([rejected], [])))
        self.assertEqual(self.ad.errors([allowed], []), [])

    def test_rate_limit_error_kind_is_a_cap(self):
        ev = json.dumps({"type": "assistant", "error": "rate_limit", "is_api_error_message": True,
                         "message": {"role": "assistant", "content": [{"type": "text", "text": "Try again soon."}]}})
        self.assertTrue(self.ad.usage_limit(self.ad.errors([ev], [])))

    def test_model_text_is_never_an_error(self):  # an answer that quotes the limit wording
        ev = json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The code prints: you've hit your limit, usage limit reached."}]}})
        res = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "usage limit reached"})
        self.assertEqual(self.ad.errors([ev, res], []), [])

    def test_unsafe_session_id_starts_fresh(self):
        self.assertEqual(self.ad.resume_argv(self.SEAT, "--x", "/s/i", False), self.ad.argv(self.SEAT, "/s/i", False))
        line = json.dumps({"type": "result", "session_id": "a b"})
        self.assertIsNone(self.ad.session_id([line], []))

    def test_hostile_lines(self):
        lines = [json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": ["x"],
                             "total_cost_usd": "free", "usage": {"input_tokens": "x", "output_tokens": None}}),
                 json.dumps({"type": "user", "message": {"role": "user", "content": [
                     {"type": "tool_result", "content": [{"type": "text", "text": 5}, {"type": "text", "text": "ok"}]}]}}),
                 json.dumps({"type": "rate_limit_event", "rate_limit_info": "x"}),
                 json.dumps({"type": "assistant", "error": 5, "message": "x"})]
        self.assertEqual(self.ad.answer(lines), "")
        self.assertEqual(self.ad.usage(lines), {"tokens_in": 0, "tokens_out": 0})
        self.assertEqual(self.ad.tool_results(lines), ["ok"])
        self.assertEqual(self.ad.errors(lines, []), ["error"])

    def test_env_allowlist_drops_claudecode(self):
        env = grants.seat_env({"PATH": "/bin", "CLAUDECODE": "1", "CLAUDE_CONFIG_DIR": "/c",
                                "CLAUDE_CODE_OAUTH_TOKEN": "t"}, keep=self.ad.env_keys)
        self.assertEqual(env, {"PATH": "/bin", "CLAUDE_CODE_OAUTH_TOKEN": "t"})

    def test_tool_result_errors_are_not_errors(self):  # a tool result that says "usage limit" is not a cap
        line = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "You have hit your usage limit.", "is_error": True}]}})
        self.assertEqual(self.ad.errors([line], []), [])

    def test_project_code(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            Path(d, ".claude", "settings.json").write_text(json.dumps({"permissions": {"allow": []}}))
            self.assertEqual(self.ad.project_code(d), [])
            Path(d, ".claude", "settings.local.json").write_text(json.dumps({"hooks": {"Stop": []}}))
            Path(d, ".mcp.json").write_text("{}")
            self.assertEqual(self.ad.project_code(d),
                             [os.path.join(d, ".claude", "settings.local.json"), os.path.join(d, ".mcp.json")])

    def test_project_code_more_keys_and_dirs(self):
        for key in ("env", "enabledPlugins", "extraKnownMarketplaces"):
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, ".claude"))
                Path(d, ".claude", "settings.json").write_text(json.dumps({key: {}}))
                self.assertEqual(self.ad.project_code(d), [os.path.join(d, ".claude", "settings.json")], key)
        for sub in ("agents", "commands", "skills"):
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, ".claude", sub))
                self.assertEqual(self.ad.project_code(d), [os.path.join(d, ".claude", sub)], sub)

    def test_project_code_unreadable_settings_count(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude"))
            os.symlink(os.path.join(d, "missing"), os.path.join(d, ".claude", "settings.json"))
            self.assertEqual(self.ad.project_code(d), [os.path.join(d, ".claude", "settings.json")])

    def test_project_code_ignore_flags_cover(self):  # ruling H-ignore: per-file coverage
        argv = self.ad.argv({"runtime": "claude-code", "model": "m"}, "/in", False)
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".claude", "agents"))
            os.makedirs(os.path.join(d, ".claude", "skills"))
            os.makedirs(os.path.join(d, ".claude", "commands"))
            Path(d, ".claude", "settings.json").write_text(json.dumps({"hooks": {}}))
            Path(d, ".claude", "settings.local.json").write_text(json.dumps({"env": {}}))
            Path(d, ".mcp.json").write_text("{}")
            # commands: no flag's documented scope covers it, so it stays reported (fail closed)
            self.assertEqual(self.ad.project_code(d, argv), [os.path.join(d, ".claude", "commands")])
            for flag, names in (("--strict-mcp-config", [".mcp.json"]),
                                ("--safe-mode", [".claude/agents", ".claude/skills"])):
                less = [a for a in argv if a != flag]
                got = self.ad.project_code(d, less)
                for n in names:
                    self.assertIn(os.path.join(d, n), got, flag)
            sources = argv.index("--setting-sources")
            for value in ("user,project", "local"):
                other = argv[:sources + 1] + [value] + argv[sources + 2:]
                self.assertIn(os.path.join(d, ".claude", "settings.json"), self.ad.project_code(d, other), value)
            no_sources = argv[:sources] + argv[sources + 2:]
            self.assertIn(os.path.join(d, ".claude", "settings.local.json"), self.ad.project_code(d, no_sources))
            twice = argv + ["--setting-sources", "project"]  # a later override fails closed
            self.assertIn(os.path.join(d, ".claude", "settings.json"), self.ad.project_code(d, twice))

    def test_static_fields(self):
        self.assertEqual(self.ad.version_argv, ["claude", "--version"])
        self.assertEqual(self.ad.inner_sandbox, [])


class Pi(unittest.TestCase):
    ad = adapters.SEAT_ADAPTERS["pi"]
    SEAT = {"runtime": "pi", "model": "m-test", "effort": "high", "args": []}

    def test_argv_and_deep(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/input.md", False),
                         ["pi", "-p", "--mode", "json", "--model", "m-test", "--no-extensions", "--no-mcp",
                          "--no-skills", "--no-approve", "--thinking", "high"])
        self.assertTrue(has_run(self.ad.argv(self.SEAT, "/s/i", True), ["--thinking", adapters.TOP_EFFORT["pi"]]))
        self.assertNotIn("--thinking", self.ad.argv({"runtime": "pi", "model": "m-test"}, "/s/i", False))

    def test_resume_repeats_flags(self):
        seat = dict(self.SEAT, args=["--seat-arg"])
        new, res = self.ad.argv(seat, "/s/i", False), self.ad.resume_argv(seat, "sid", "/s/i", False)
        self.assertEqual(res, new + ["--session", "sid"])
        self.assertEqual(self.ad.resume_argv(seat, "--x", "/s/i", False), new)

    def test_no_input_text_on_argv(self):
        self.assertTrue(all(len(a) < 200 for a in self.ad.argv(self.SEAT, "/s/i", False)))
        self.assertEqual(self.ad.prompt_via, "stdin")

    def test_parse_ok(self):
        out, err = fixture("pi", "ok")
        self.assertEqual(self.ad.session_id(out, err), "sess-0003")
        self.assertEqual(self.ad.answer(out), "No significant findings.")
        self.assertEqual(self.ad.usage(out), {"tokens_in": 1200, "tokens_out": 300, "cost_usd": 0.03})
        self.assertEqual(self.ad.errors(out, err), [])  # the stderr Warning: line is not an error

    def test_prompt_echo_is_not_an_error(self):  # pi echoes the prompt as a user message
        out, err = fixture("pi", "ok")
        self.assertTrue(any("usage limit" in line for line in out))
        self.assertFalse(self.ad.usage_limit(self.ad.errors(out, err)))

    def test_parse_resume(self):
        out, err = fixture("pi", "resume")
        self.assertEqual(self.ad.session_id(out, err), "sess-0003")
        self.assertEqual(self.ad.answer(out), "The fix holds.")

    def test_usage_limit_on_error_lines(self):
        out, err = fixture("pi", "usage_limit")
        self.assertTrue(self.ad.usage_limit(self.ad.errors(out, err)))
        self.assertEqual(self.ad.answer(out), "")

    def test_error_not_a_cap(self):
        out, err = fixture("pi", "error")
        errs = self.ad.errors(out, err)
        self.assertTrue(errs)
        self.assertFalse(self.ad.usage_limit(errs))
        self.assertEqual(self.ad.answer(out), "")

    def test_tool_results_not_prose(self):
        out, _ = fixture("pi", "ok")
        results = self.ad.tool_results(out)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("PROBE:g1\n"))
        self.assertIn("Operation not permitted", results[0])

    def test_hostile_lines(self):
        lines = [json.dumps({"type": "session", "id": "-x"}),
                 json.dumps({"type": "message_end", "message": {"role": "assistant", "content": "x",
                                                                "usage": {"input": "a", "output": None, "cost": 3}}}),
                 json.dumps({"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                                                                "errorMessage": 5}}),
                 json.dumps({"type": "message_end", "message": "x"}),
                 json.dumps({"type": "tool_execution_end", "result": 5})]
        self.assertIsNone(self.ad.session_id(lines, []))
        self.assertEqual(self.ad.answer(lines), "")
        self.assertEqual(self.ad.usage(lines), {"tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0})
        self.assertEqual(self.ad.errors(lines, []), ["error"])
        self.assertEqual(self.ad.tool_results(lines), [""])

    def test_grants_inside_home(self):
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(g.write, [HOME])
        self.assertEqual(g.env, {"PI_CODING_AGENT_DIR": HOME})
        self.assertFalse(g.private_home)  # the real HOME keeps a Volta shim working; the agent dir is the seat home
        self.assertEqual(self.ad.home_env(HOME), {"PI_CODING_AGENT_DIR": HOME})
        self.assertEqual(self.ad.credentials, [])
        self.assertEqual(self.ad.state_dirs, [])

    def test_volta_home_read_through_the_toolchain_list(self):
        # nono denies reads under the home by default. The pi shim is Volta's: without a read of
        # ~/.volta it fails before pi starts ("Could not create directory"). seat_grants reads it.
        from lead import sandbox
        self.assertIn(".volta", grants.SEAT_TOOLCHAIN_READS)
        self.assertEqual(self.ad.grants(self.SEAT, HOME).read, [])

    def test_code_paths_in_home_write_denied(self):  # R14
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(sorted(g.deny_write), sorted(f"{HOME}/{n}" for n in PI_CODE))
        self.assertNotIn(f"{HOME}/sessions", g.deny_write)
        self.assertEqual(sorted(self.ad.hook_files(HOME)), sorted(g.deny_write))

    def test_permission_flag_present(self):  # R19: pi has none; nothing asks
        self.assertEqual(RECORDED_PERMISSION_FLAG["pi"], [])
        self.assertEqual(self.ad.inner_sandbox, [])

    def test_ignore_flags_in_argv(self):
        argv = self.ad.argv(self.SEAT, "/s/i", False)
        self.assertEqual(self.ad.ignore_flags, ["--no-extensions", "--no-mcp", "--no-skills", "--no-approve"])
        self.assertTrue(all(f in argv for f in self.ad.ignore_flags))

    def test_stray_stderr_on_a_good_run_is_not_an_error(self):
        out, _ = fixture("pi", "ok")
        self.assertEqual(self.ad.errors(out, ["notice: usage limit reached on another key"]), [])
        failed = [json.dumps({"type": "message_end", "message": {"role": "user", "content": "x"}})]  # no answer
        self.assertEqual(self.ad.errors(failed, ["fatal: no answer"]), ["fatal: no answer"])

    def test_pi_env_keys(self):
        self.assertIn(RECORDED_ENV_KEY["pi"], self.ad.env_keys)

    def test_static_fields(self):
        self.assertEqual(self.ad.version_argv, ["pi", "--version"])
        self.assertEqual(self.ad.backend, "any")


class Kimi(unittest.TestCase):
    """Kimi's OK-path event shapes were not observed (the plan was capped): those fixtures are
    built from the docs and are unverified."""
    ad = adapters.SEAT_ADAPTERS["kimi"]
    SEAT = {"runtime": "kimi", "model": "m-test", "effort": "high", "args": []}

    def test_argv(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/input.md", False),
                         ["kimi", "-m", "m-test", "--output-format", "stream-json", "--auto",
                          "-p", adapters.FILE_INSTRUCTION.format(path="/s/input.md")])

    def test_deep_without_knob(self):  # kimi 2.3.2 has no effort flag
        self.assertEqual(self.ad.argv(self.SEAT, "/s/i", True), self.ad.argv(self.SEAT, "/s/i", False))

    def test_resume_repeats_flags(self):
        seat = dict(self.SEAT, args=["--seat-arg"])
        new, res = self.ad.argv(seat, "/s/i", False), self.ad.resume_argv(seat, "sid", "/s/i", False)
        self.assertEqual(res, new[:-2] + ["-S", "sid"] + new[-2:])
        self.assertEqual(self.ad.resume_argv(seat, "a b", "/s/i", False), new)

    def test_no_input_text_on_argv(self):
        self.assertTrue(all(len(a) < 200 for a in self.ad.argv(self.SEAT, "/s/i", False)))
        self.assertEqual(self.ad.prompt_via, "file")

    def test_parse_ok(self):
        out, err = fixture("kimi", "ok")
        self.assertEqual(self.ad.session_id(out, err), "sess-0004")
        self.assertEqual(self.ad.answer(out), "No significant findings.")
        self.assertEqual(self.ad.usage(out), {})  # no usage shape is known
        self.assertEqual(self.ad.errors(out, err), [])

    def test_parse_resume(self):
        out, err = fixture("kimi", "resume")
        self.assertEqual(self.ad.session_id(out, err), "sess-0004")
        self.assertEqual(self.ad.answer(out), "The fix holds.")

    def test_usage_limit_on_error_lines(self):  # the observed shape: one stderr error line, then See log:
        out, err = fixture("kimi", "usage_limit")
        errs = self.ad.errors(out, err)
        self.assertTrue(self.ad.usage_limit(errs))
        self.assertFalse(any(e.startswith("See log:") for e in errs))
        self.assertEqual(self.ad.answer(out), "")

    def test_error_not_a_cap(self):
        out, err = fixture("kimi", "error")
        errs = self.ad.errors(out, err)
        self.assertTrue(errs)
        self.assertFalse(self.ad.usage_limit(errs))

    def test_model_text_is_never_an_error(self):
        line = json.dumps({"role": "assistant", "content": "the code prints: usage limit reached"})
        self.assertEqual(self.ad.errors([line], ["usage limit reached in the tool output"]), [])

    def test_tool_results_not_prose(self):
        out, _ = fixture("kimi", "ok")
        results = self.ad.tool_results(out)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("PROBE:g1\n"))

    def test_hostile_lines(self):
        lines = [json.dumps({"type": "session.resume_hint", "session_id": 5}),
                 json.dumps({"role": "assistant", "content": [{"type": "text", "text": 5}]}),
                 json.dumps({"role": "tool", "content": None})]
        self.assertIsNone(self.ad.session_id(lines, []))
        self.assertEqual(self.ad.answer(lines), "")
        self.assertEqual(self.ad.tool_results(lines), [""])

    def test_grants_inside_home(self):
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(g.write, [HOME])
        self.assertEqual(g.env, {"KIMI_CODE_HOME": HOME})
        self.assertTrue(g.private_home)
        self.assertEqual(self.ad.home_env(HOME), {"KIMI_CODE_HOME": HOME})
        self.assertEqual(self.ad.state_dirs, [])

    def test_code_paths_in_home_write_denied(self):  # R14
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(sorted(g.deny_write), sorted(f"{HOME}/{n}" for n in KIMI_CODE))
        self.assertNotIn(f"{HOME}/sessions", g.deny_write)
        self.assertNotIn(f"{HOME}/credentials", g.deny_write)
        self.assertEqual(sorted(self.ad.hook_files(HOME)), sorted(g.deny_write))

    def test_credentials_refresh(self):  # R11: the file under credentials/ holds a refresh token
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "credentials"))
            Path(d, "credentials", "login-x.json").write_text("{}")
            Path(d, "credentials", "notes.txt").write_text("")
            self.assertEqual(adapters._kimi_logins(d),
                             [(os.path.join(d, "credentials", "login-x.json"), "credentials/login-x.json", True)])
        self.assertEqual(adapters._kimi_logins("/nonexistent/kimi"), [])

    def test_permission_flag_present(self):  # R19
        self.assertTrue(has_run(self.ad.argv(self.SEAT, "/s/i", False), RECORDED_PERMISSION_FLAG["kimi"]))
        self.assertTrue(has_run(self.ad.resume_argv(self.SEAT, "sid", "/s/i", False), RECORDED_PERMISSION_FLAG["kimi"]))

    def test_kimi_project_mcp_symlink_counts(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, ".kimi-code"))
            os.symlink(os.path.join(d, "missing"), os.path.join(d, ".kimi-code", "mcp.json"))
            self.assertEqual(self.ad.project_code(d), [os.path.join(d, ".kimi-code", "mcp.json")])

    def test_static_fields(self):
        self.assertEqual(self.ad.version_argv, ["kimi", "--version"])
        self.assertEqual(self.ad.ignore_flags, [])
        self.assertEqual(self.ad.env_keys, [])
        self.assertEqual(self.ad.inner_sandbox, [])


class Agy(unittest.TestCase):
    """agy runs on the real HOME: a private HOME loses the keychain login (ruling S-agy, checked
    2026-11-23). Its writes go to its own state dir only, declared in `state_dirs`."""
    ad = adapters.SEAT_ADAPTERS["agy"]
    SEAT = {"runtime": "agy", "model": "m-test", "args": []}

    def test_argv(self):
        self.assertEqual(self.ad.argv(self.SEAT, "/s/input.md", False),
                         ["agy", "-p", adapters.FILE_INSTRUCTION.format(path="/s/input.md"), "--model", "m-test",
                          "--output-format", "stream-json", "--dangerously-skip-permissions"])
        self.assertNotIn("--sandbox", self.ad.argv(self.SEAT, "/s/i", False))  # Seatbelt does not nest

    def test_deep_without_knob(self):  # --effort conflicts with a variant model id (observed)
        self.assertEqual(self.ad.argv(self.SEAT, "/s/i", True), self.ad.argv(self.SEAT, "/s/i", False))
        self.assertNotIn("--effort", self.ad.argv(self.SEAT, "/s/i", True))

    def test_resume_repeats_flags(self):
        seat = dict(self.SEAT, args=["--seat-arg"])
        new, res = self.ad.argv(seat, "/s/i", False), self.ad.resume_argv(seat, "sid", "/s/i", False)
        self.assertEqual(res, new + ["--conversation", "sid"])
        self.assertEqual(self.ad.resume_argv(seat, "--x", "/s/i", False), new)

    def test_no_input_text_on_argv(self):
        self.assertTrue(all(len(a) < 200 for a in self.ad.argv(self.SEAT, "/s/i", False)))
        self.assertEqual(self.ad.prompt_via, "file")

    def test_parse_ok(self):
        out, err = fixture("agy", "ok")
        self.assertEqual(self.ad.session_id(out, err), "sess-0005")
        self.assertEqual(self.ad.answer(out), "No significant findings.")
        self.assertEqual(self.ad.usage(out), {"tokens_in": 1200, "tokens_out": 300})
        self.assertEqual(self.ad.errors(out, err), [])

    def test_parse_resume(self):
        out, err = fixture("agy", "resume")
        self.assertEqual(self.ad.session_id(out, err), "sess-0005")
        self.assertEqual(self.ad.answer(out), "The fix holds.")

    def test_usage_limit_on_error_lines(self):
        out, err = fixture("agy", "usage_limit")
        self.assertTrue(self.ad.usage_limit(self.ad.errors(out, err)))
        self.assertEqual(self.ad.answer(out), "")

    def test_error_not_a_cap(self):
        out, err = fixture("agy", "error")
        errs = self.ad.errors(out, err)
        self.assertTrue(any("authentication" in e for e in errs))
        self.assertFalse(self.ad.usage_limit(errs))
        self.assertEqual(self.ad.answer(out), "")

    def test_model_text_is_never_an_error(self):
        lines = [json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response",
                                                                     "text_delta": "usage limit reached"}}),
                 json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "usage limit reached"}})]
        self.assertEqual(self.ad.errors(lines, []), [])

    def test_tool_results_not_prose(self):
        out, _ = fixture("agy", "ok")
        results = self.ad.tool_results(out)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].startswith("PROBE:g1\n"))

    def test_tool_output_is_under_tool_info(self):
        # Observed on agy 1.5.15: a tool step has step_type "tool" and its output in tool_info.output.
        # A failed step's error names a path the model chose, and the command line is model text:
        # neither shows that anything ran, so neither is a tool result.
        def step(state, info):
            return json.dumps({"event": "step_update", "step_update": {"state": state, "step_type": "tool",
                                                                       "tool_name": "run_command", "tool_info": info}})
        lines = [step("DONE", {"parameters": {"CommandLine": "echo N1"}, "output": "Command output:\nN1"}),
                 step("ERROR", {"parameters": {"CommandLine": "echo N2"},
                                "error": {"type": "TOOL_ERROR", "message": "open /x/N2: operation not permitted"}}),
                 step("DONE", {"parameters": {"CommandLine": "echo N3"}}),
                 step("DONE", "x")]
        self.assertEqual(self.ad.tool_results(lines), ["Command output:\nN1", "", ""])

    def test_hostile_lines(self):
        lines = [json.dumps({"event": "init", "init": "x", "conversation_id": "a b"}),
                 json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": 5,
                                                           "usage": {"input_tokens": "x", "output_tokens": True}}}),
                 json.dumps({"event": "result", "result": {"status": 7, "error": ["x"]}}),
                 json.dumps({"event": "step_update", "step_update": "x"})]
        self.assertIsNone(self.ad.session_id(lines, []))
        self.assertEqual(self.ad.answer(lines), "")
        self.assertEqual(self.ad.usage(lines), {"tokens_in": 0, "tokens_out": 0})
        self.assertEqual(self.ad.errors(lines, []), ["error"])
        self.assertEqual(self.ad.tool_results(lines), [])

    def test_grants_inside_home(self):
        g = self.ad.grants(self.SEAT, HOME)
        self.assertEqual(g.write, [HOME])
        self.assertFalse(g.private_home)
        self.assertEqual(g.env, {})
        self.assertEqual(self.ad.home_env(HOME), {})
        self.assertEqual(self.ad.state_dirs, [adapters._agy_state()])
        self.assertEqual(self.ad.credentials, [])

    def test_code_paths_in_home_write_denied(self):  # R14: the code names in its state dir stay read-only
        g = self.ad.grants(self.SEAT, HOME)
        state = adapters._agy_state()
        self.assertEqual(sorted(g.deny_write), sorted(os.path.join(state, n) for n in adapters.AGY_CODE))
        for memory in ("knowledge", "brain", "implicit", "annotations"):  # prompt-bearing state a lead reads later
            self.assertIn(os.path.join(state, memory), g.deny_write)
        for own in ("conversations", "conversation_summaries.db", "history.jsonl"):  # the seat's own resume
            self.assertNotIn(os.path.join(state, own), g.deny_write)
        self.assertTrue(set(g.deny_write) <= set(self.ad.hook_files(HOME)))

    def test_hook_files_outside_state(self):
        gemini = os.path.join(os.path.expanduser("~"), ".gemini")
        for p in ("settings.json", "config/mcp_config.json", "skills", "policies"):
            self.assertIn(os.path.join(gemini, p), self.ad.hook_files(HOME))

    def test_stray_stderr_on_a_good_run_is_not_an_error(self):
        out, _ = fixture("agy", "ok")
        self.assertEqual(self.ad.errors(out, ["notice: usage limit reached on another plan"]), [])
        self.assertEqual(self.ad.errors([], ["Error: no result"]), ["Error: no result"])  # no answer: stderr counts

    def test_permission_flag_present(self):  # R19
        self.assertTrue(has_run(self.ad.argv(self.SEAT, "/s/i", False), RECORDED_PERMISSION_FLAG["agy"]))
        self.assertTrue(has_run(self.ad.resume_argv(self.SEAT, "sid", "/s/i", False), RECORDED_PERMISSION_FLAG["agy"]))

    def test_project_code(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.ad.project_code(d), [])
            os.makedirs(os.path.join(d, ".agents", "skills"))
            Path(d, ".agents", "hooks.json").write_text("{}")
            self.assertEqual(self.ad.project_code(d), [os.path.join(d, ".agents", "hooks.json"),
                                                       os.path.join(d, ".agents", "skills")])

    def test_static_fields(self):
        self.assertEqual(self.ad.version_argv, ["agy", "--version"])
        self.assertEqual(self.ad.backend, "sandbox-exec")  # the login is in the keychain
        self.assertEqual(self.ad.inner_sandbox, [])
        self.assertEqual(self.ad.ignore_flags, [])
        self.assertEqual(self.ad.env_keys, [])


class PiProject(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.target = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_pi_project_extensions(self):
        (self.target / ".pi/extensions").mkdir(parents=True)
        self.assertEqual(adapters.SEAT_ADAPTERS["pi"].project_code(str(self.target)), [str(self.target / ".pi/extensions")])

    def test_pi_ignore_flags_cover(self):  # ruling H-ignore: all of PI_IGNORE covers .pi/*
        (self.target / ".pi/extensions").mkdir(parents=True)
        pi = adapters.SEAT_ADAPTERS["pi"]
        argv = pi.argv({"runtime": "pi", "model": "m"}, "/in", False)
        self.assertEqual(pi.project_code(str(self.target), argv), [])
        partial = [a for a in argv if a != "--no-extensions"]
        self.assertEqual(pi.project_code(str(self.target), partial), [str(self.target / ".pi/extensions")])


class Registry(unittest.TestCase):
    def test_all_runtimes(self):
        self.assertEqual(set(adapters.SEAT_ADAPTERS), config.RUNTIMES)
    def test_every_field_set(self):
        for name, ad in adapters.SEAT_ADAPTERS.items():
            for field in adapters.SeatAdapter._fields:
                self.assertIsNotNone(getattr(ad, field), f"{name}.{field}")


if __name__ == "__main__":
    unittest.main()
