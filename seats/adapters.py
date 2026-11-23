"""Headless seat adapters: how bw runs each review CLI and reads what it prints.

These are separate from the lead adapters (`lead/runtimes.py`), so a seat change never resets a
lead key. The flags and event shapes come from `docs/reference-implementation.md`, section
"Seat runtimes under `bw seat`".

Vendor sandboxes stay off inside `bw sandbox`, because Seatbelt profiles do not nest. The
tool-permission flags let the model try Bash and Write, so the OS sandbox stops them, not a
prompt. The input (brief and task) reaches the CLI on stdin or by a fixed file instruction,
never on argv.
"""
import json
import os
import re
from collections import namedtuple
from typing import Optional

from lead.sandbox import RuntimeGrants

SeatAdapter = namedtuple("SeatAdapter",
    "argv resume_argv session_id usage_limit usage answer errors grants home_env credentials "
    "hook_files project_code inner_sandbox backend version_argv prompt_via ignore_flags env_keys tool_results")

TOP_EFFORT = {"codex": "max", "pi": "xhigh", "kimi": "max"}
# Seat-only code lists (R17): names under a runtime's config dir that load as code. A seat's grants
# deny writes to them inside the seat home (R14); the seat key manifests them.
CLAUDE_CODE = ["settings.json", "settings.local.json", "CLAUDE.md", "hooks", "agents", "commands", "plugins", "skills", ".mcp.json"]
CODEX_CODE = ["config.toml", "AGENTS.md", "prompts", "hooks.json", "skills", "rules"]
AGY_CODE = ["settings.json", "GEMINI.md", "extensions"]
# Quota and usage wording only (moderator's call, plan panel: R21). A bare 429 or "rate limit" with no quota
# word is a transient error, not a cap.
USAGE_LIMIT = re.compile(r"usage limit|quota (exceeded|exhausted|reached)|exceeded your (current )?quota|"
                         r"hit your .{0,40}(usage|quota|plan) limit|out of (credits|quota)|hit your limit|"
                         r"limit reached", re.I)
# A session id goes on argv only when it has this shape; any other id starts a new session.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
FILE_INSTRUCTION = "Read the file {path} and follow it. It holds your brief and your task."
# Runtimes whose login is an env token; without it the seat cannot run.
LOGIN_ENV = {"claude-code": "CLAUDE_CODE_OAUTH_TOKEN"}
LOGIN_HINT = {"claude-code": "no login: run `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN"}


def effort(seat, deep) -> Optional[str]:
    """TOP_EFFORT for the runtime when deep (if it has one), else the seat's own effort."""
    if deep and seat.get("runtime") in TOP_EFFORT:
        return TOP_EFFORT[seat["runtime"]]
    return seat.get("effort")


def json_lines(lines) -> list:
    """The JSON objects among `lines`; a non-JSON, broken, or non-object line is skipped."""
    out = []
    for line in lines:
        try:
            obj = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def usage_limit(error_lines) -> bool:
    """True when an error line has quota or usage-limit wording. Pass error lines only, never the prompt echo."""
    return any(USAGE_LIMIT.search(line) for line in error_lines)


def login_missing(runtime, environ) -> Optional[str]:
    """The reason a seat cannot log in (its login env token is absent or empty), else None."""
    key = LOGIN_ENV.get(runtime)
    if key and not environ.get(key):
        return LOGIN_HINT[runtime]
    return None


def _num(x):
    """A token or cost count; anything that is not a plain number counts as 0."""
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _str(x):
    return x if isinstance(x, str) else ""


def valid_session(sid) -> bool:
    return isinstance(sid, str) and SESSION_ID.fullmatch(sid) is not None


def _resume(new_argv, resume_argv):
    """resume_argv(seat, sid, input_path, deep), or a new session when the id is not safe for argv."""
    return lambda seat, sid, input_path, deep: (resume_argv(seat, sid, input_path, deep) if valid_session(sid)
                                                else new_argv(seat, input_path, deep))


# The denied code names that are dirs, per runtime, relative to the seat home. bw makes each one
# empty before a run: a CLI that tries to create a missing one at start would fail on the deny.
CODE_DIRS = {"codex": ["prompts", "skills", "rules"],
             "claude-code": [".claude/hooks", ".claude/agents", ".claude/commands", ".claude/plugins", ".claude/skills"]}


def make_code_dirs(runtime, seat_home):
    """Create each CODE_DIRS entry under `seat_home`, never through a symlink (O_NOFOLLOW at each
    step). Raises OSError when a component is a symlink or not a dir: the seat home was tampered with."""
    for rel in CODE_DIRS.get(runtime, []):
        fd = os.open(seat_home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in rel.split("/"):
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
        finally:
            os.close(fd)


def _present(target, rels):
    """The paths under `target` that exist, a dangling symlink included."""
    return [p for p in (os.path.join(target, r) for r in rels) if os.path.lexists(p)]


def _grants(seat_home, code_dir, code, env):
    """Writes go to the seat home only; the code names in `code_dir` stay read-only (R14)."""
    return RuntimeGrants(read=[], write=[seat_home], env=env, private_home=True,
                         deny_write=[os.path.join(code_dir, n) for n in code])


# ---- codex: `codex exec --json`, the input on stdin ----

CODEX_INNER = ["--dangerously-bypass-approvals-and-sandbox"]  # also the headless permission flag (R19)
CODEX_PROJECT_CODE = [".codex/config.toml", ".codex/hooks.json", ".codex/skills", ".codex/rules"]
# Without --json, codex prints a transcript on stderr: the prompt echo after a "user" line, then the
# model's text and the tool output. After that marker, only lines that start with "ERROR" are errors.
CODEX_ECHO_START = "user"
CODEX_ERROR_PREFIX = "ERROR"


def _codex_flags(seat, deep):
    e = effort(seat, deep)
    return (["--json", "-m", seat["model"]] + (["-c", f"model_reasoning_effort={e}"] if e else [])
            + CODEX_INNER + list(seat.get("args", [])))


def _codex_session(stdout_lines, stderr_lines):
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "thread.started" and valid_session(ev.get("thread_id")):
            return ev["thread_id"]
    return None


def _codex_items(stdout_lines, kind):
    return [ev["item"] for ev in json_lines(stdout_lines)
            if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict)
            and ev["item"].get("type") == kind]


def _codex_answer(stdout_lines):
    texts = [i.get("text") for i in _codex_items(stdout_lines, "agent_message") if isinstance(i.get("text"), str)]
    return texts[-1] if texts else ""


def _codex_usage(stdout_lines):
    turns = [ev["usage"] for ev in json_lines(stdout_lines)
             if ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict)]
    if not turns:
        return {}
    return {"tokens_in": sum(_num(u.get("input_tokens")) for u in turns),
            "tokens_out": sum(_num(u.get("output_tokens")) for u in turns)}


def _codex_errors(stdout_lines, stderr_lines):
    errs = []
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "error" and _str(ev.get("message")):
            errs.append(ev["message"])
        elif ev.get("type") == "turn.failed":
            err = ev.get("error")
            msg = _str(err.get("message") if isinstance(err, dict) else err)
            if msg:
                errs.append(msg)
    in_transcript = False
    for line in stderr_lines:
        s = line.strip()
        if s == CODEX_ECHO_START:
            in_transcript = True
        elif s and (not in_transcript or s.startswith(CODEX_ERROR_PREFIX)):
            errs.append(s)
    return errs


def _codex_tool_results(stdout_lines):
    out = []
    for ev in json_lines(stdout_lines):
        item = ev.get("item") if ev.get("type") == "item.completed" else None
        if not isinstance(item, dict):
            continue
        if item.get("type") == "command_execution":
            out.append(_str(item.get("aggregated_output")))
        elif item.get("type") == "file_change":
            changes = item.get("changes") if isinstance(item.get("changes"), list) else []
            paths = [c["path"] for c in changes if isinstance(c, dict) and isinstance(c.get("path"), str)]
            out.append(f"file_change {_str(item.get('status'))}: {', '.join(paths)}")
    return out


def _codex_login():
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "auth.json")


CODEX = SeatAdapter(
    argv=lambda seat, input_path, deep: ["codex", "exec", *_codex_flags(seat, deep), "-"],
    # An unsafe session id starts a new session. `codex exec resume` has no -s, -C, or -p: the target is the process cwd.
    resume_argv=_resume(lambda seat, input_path, deep: ["codex", "exec", *_codex_flags(seat, deep), "-"],
                        lambda seat, sid, input_path, deep: ["codex", "exec", "resume", *_codex_flags(seat, deep), sid, "-"]),
    session_id=_codex_session,
    usage_limit=usage_limit,
    usage=_codex_usage,
    answer=_codex_answer,
    errors=_codex_errors,
    grants=lambda seat, seat_home: _grants(seat_home, seat_home, CODEX_CODE, {"CODEX_HOME": seat_home}),
    home_env=lambda seat_home: {"CODEX_HOME": seat_home},
    credentials=[(_codex_login(), "auth.json", True)],  # auth.json holds a refresh token (R11)
    hook_files=lambda seat_home: [os.path.join(seat_home, n) for n in CODEX_CODE],
    project_code=lambda target: _present(target, CODEX_PROJECT_CODE),
    inner_sandbox=CODEX_INNER,
    backend="any",
    version_argv=["codex", "--version"],
    prompt_via="stdin",
    ignore_flags=[],  # no flag makes codex skip project config
    env_keys=[],
    tool_results=_codex_tool_results,
)


# ---- claude-code: `claude -p --output-format stream-json --verbose`, the input on stdin ----
# HOME is the seat home and CLAUDE_CONFIG_DIR is unset (ruling S-claude), so the config dir is
# <seat home>/.claude. The keychain login does not carry over to a private HOME, so the login is
# CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`: never refreshed, no credential file.

CLAUDE_PERMISSION = ["--permission-mode", "bypassPermissions"]
CLAUDE_IGNORE = ["--safe-mode", "--setting-sources", "user", "--strict-mcp-config"]
CLAUDE_PROJECT_SETTINGS = [".claude/settings.json", ".claude/settings.local.json"]
# Settings keys whose values run commands.
CLAUDE_CODE_KEYS = ("hooks", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "otelHeadersHelper", "statusLine",
                    "env", "enabledPlugins", "extraKnownMarketplaces")
CLAUDE_PROJECT_DIRS = [".claude/agents", ".claude/commands", ".claude/skills"]


def _claude_argv(seat, input_path, deep):
    return ["claude", "-p", "--model", seat["model"], "--output-format", "stream-json", "--verbose",
            *CLAUDE_PERMISSION, *CLAUDE_IGNORE, *seat.get("args", [])]


def _claude_session(stdout_lines, stderr_lines):
    events = json_lines(stdout_lines)
    for ev in events:
        if (ev.get("type"), ev.get("subtype")) == ("system", "init") or ev.get("type") == "result":
            if valid_session(ev.get("session_id")):
                return ev["session_id"]
    return None


def _claude_result(stdout_lines):
    results = [ev for ev in json_lines(stdout_lines) if ev.get("type") == "result"]
    return results[-1] if results else None


def _claude_answer(stdout_lines):
    res = _claude_result(stdout_lines)
    if not res or res.get("is_error"):
        return ""
    return _str(res.get("result"))


def _claude_usage(stdout_lines):
    out = {}
    res = _claude_result(stdout_lines)
    if res:
        u = res.get("usage") if isinstance(res.get("usage"), dict) else {}
        out["tokens_in"] = sum(_num(u.get(k)) for k in
                               ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        out["tokens_out"] = _num(u.get("output_tokens"))
        cost = res.get("total_cost_usd")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            out["cost_usd"] = float(cost)
    limits = [ev["rate_limit_info"] for ev in json_lines(stdout_lines)
              if ev.get("type") == "rate_limit_event" and isinstance(ev.get("rate_limit_info"), dict)]
    if limits:
        out["rate_limits"] = limits[-1]
    return out


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_str(c.get("text")) for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


CLAUDE_RATE_LIMIT_ERROR = "rate_limit"
CLAUDE_LIMIT_LINE = "usage limit reached ({})"


def _claude_errors(stdout_lines, stderr_lines):
    """Error events only: an assistant API-error message and an error result. User events hold
    tool results, never errors; without --replay-user-messages there is no prompt echo."""
    errs = []
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "assistant" and ev.get("error"):
            kind = _str(ev["error"]) or "error"
            if kind == CLAUDE_RATE_LIMIT_ERROR:  # the CLI's own error kind, not model text
                errs.append(CLAUDE_LIMIT_LINE.format(kind))
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            text = _text(msg.get("content"))
            errs.append(f"{kind}: {text}" if text else kind)
        elif ev.get("type") == "rate_limit_event":
            info = ev.get("rate_limit_info") if isinstance(ev.get("rate_limit_info"), dict) else {}
            if info.get("status") == "rejected":
                errs.append(CLAUDE_LIMIT_LINE.format(_str(info.get("rateLimitType")) or "unknown window"))
        elif ev.get("type") == "result" and (ev.get("is_error") or ev.get("subtype") != "success"):
            status = ev.get("api_error_status")
            text = _str(ev.get("result")) or _str(ev.get("subtype")) or "error"
            errs.append(f"{status}: {text}" if isinstance(status, (int, str)) else text)
    errs += [line.strip() for line in stderr_lines if line.strip()]
    return errs


def _claude_tool_results(stdout_lines):
    out = []
    for ev in json_lines(stdout_lines):
        msg = ev.get("message") if ev.get("type") == "user" else None
        if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
            continue
        out += [_text(c.get("content")) for c in msg["content"]
                if isinstance(c, dict) and c.get("type") == "tool_result"]
    return out


def _claude_settings_run_code(path):
    """True when the settings file has a key that runs a command, or cannot be read as JSON."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return True
    return not isinstance(data, dict) or any(k in data for k in CLAUDE_CODE_KEYS)


def _claude_project_code(target):
    found = [p for p in _present(target, CLAUDE_PROJECT_SETTINGS) if _claude_settings_run_code(p)]
    return found + _present(target, [".mcp.json"] + CLAUDE_PROJECT_DIRS)


CLAUDE = SeatAdapter(
    argv=_claude_argv,
    resume_argv=_resume(_claude_argv, lambda seat, sid, input_path, deep: _claude_argv(seat, input_path, deep) + ["--resume", sid]),
    session_id=_claude_session,
    usage_limit=usage_limit,
    usage=_claude_usage,
    answer=_claude_answer,
    errors=_claude_errors,
    grants=lambda seat, seat_home: _grants(seat_home, os.path.join(seat_home, ".claude"), CLAUDE_CODE, {}),
    home_env=lambda seat_home: {},
    credentials=[],
    hook_files=lambda seat_home: [os.path.join(seat_home, ".claude", n) for n in CLAUDE_CODE],
    project_code=_claude_project_code,
    inner_sandbox=[],
    backend="any",
    version_argv=["claude", "--version"],
    prompt_via="stdin",
    ignore_flags=CLAUDE_IGNORE,
    env_keys=[LOGIN_ENV["claude-code"]],
    tool_results=_claude_tool_results,
)

SEAT_ADAPTERS = {"codex": CODEX, "claude-code": CLAUDE}  # Task 9 adds pi, kimi, agy
