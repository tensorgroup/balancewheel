"""Headless seat adapters: how bw runs each review CLI and reads what it prints.

These are separate from the lead adapters (`lead/runtimes.py`), so a seat change never resets a
lead key. The flags and event shapes come from `docs/reference-implementation.md`, section
"Seat runtimes under `bw seat`".

Vendor sandboxes stay off inside `bw sandbox`, because Seatbelt profiles do not nest. The
tool-permission flags let the model try Bash and Write, so the OS sandbox stops them, not a
prompt. The input (brief and task) reaches the CLI on stdin or by a fixed file instruction,
never on argv.
"""
import glob
import json
import os
import re
from collections import namedtuple
from typing import Optional

from lead.codepaths import KIMI_CODE, PI_CODE
from lead.sandbox import RuntimeGrants

SeatAdapter = namedtuple("SeatAdapter",
    "argv resume_argv session_id usage_limit usage answer errors grants home_env credentials "
    "hook_files project_code inner_sandbox backend version_argv prompt_via ignore_flags env_keys tool_results "
    "state_dirs")
# project_code(target, argv=()): the hostile project files present that `argv` would load. A file the
# argv's ignore flags neutralize is not reported (ruling H-ignore); with no argv, every present file
# is reported (fail closed).
# state_dirs: the CLI's own state dirs under the user's home that the seat writes, for a runtime that
# cannot run on a private home (agy). They go to lead.sandbox.seat_grants(..., state_dirs=...).

TOP_EFFORT = {"codex": "max", "pi": "xhigh", "kimi": "max"}
# Seat-only code lists (R17): names under a runtime's config dir that load as code. A seat's grants
# deny writes to them inside the seat home (R14); the seat key manifests them.
CLAUDE_CODE = ["settings.json", "settings.local.json", "CLAUDE.md", "hooks", "agents", "commands", "plugins", "skills", ".mcp.json"]
CODEX_CODE = ["config.toml", "AGENTS.md", "prompts", "hooks.json", "skills", "rules"]
# agy's names are relative to its state dir under the real home (see the agy section below). The
# last four are persistent memory that later sessions read as prompt text, lead sessions included.
AGY_CODE = ["settings.json", "GEMINI.md", "extensions", "bin", "builtin", "updater",
            "knowledge", "brain", "implicit", "annotations"]
# Quota and usage wording only (moderator's call, plan panel: R21). A bare 429 or "rate limit" with no quota
# word is a transient error, not a cap.
USAGE_LIMIT = re.compile(r"usage limit|quota (exceeded|exhausted|reached)|exceeded your (current )?quota|"
                         r"hit your .{0,40}(usage|quota|plan) limit|out of (credits|quota)|hit your limit|"
                         r"limit reached", re.I)
# A session id goes on argv only when it has this shape; any other id starts a new session.
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")
FILE_INSTRUCTION = "Read the file {path} and follow it. It holds your brief and your task."
# Runtimes whose login is an env token; without it the seat cannot run.
LOGIN_ENV = {"claude-code": "CLAUDE_CODE_OAUTH_TOKEN", "pi": "OPENROUTER_API_KEY"}
LOGIN_HINT = {"claude-code": "no login: run `claude setup-token` and export CLAUDE_CODE_OAUTH_TOKEN",
              "pi": "no login: export OPENROUTER_API_KEY"}


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
             "claude-code": [".claude/hooks", ".claude/agents", ".claude/commands", ".claude/plugins", ".claude/skills"],
             "pi": ["extensions", "skills", "prompts", "themes"],
             "kimi": ["bin", "skills", "plugins", "hooks", "agents"],
             "agy": []}  # agy writes its real state dir, where its code dirs already exist


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
    project_code=lambda target, argv=(): _present(target, CODEX_PROJECT_CODE),
    inner_sandbox=CODEX_INNER,
    backend="any",
    version_argv=["codex", "--version"],
    prompt_via="stdin",
    ignore_flags=[],  # no flag makes codex skip project config
    env_keys=[],
    tool_results=_codex_tool_results,
    state_dirs=[],
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


def _flag_values(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == flag]


def _claude_user_settings_only(argv):
    """True when argv has at least one `--setting-sources` and each one names `user` only."""
    values = _flag_values(argv, "--setting-sources")
    return bool(values) and all(set(v.split(",")) == {"user"} for v in values)


def _has(flag):
    return lambda argv: flag in argv


# The flag that neutralizes each project file (ruling H-ignore). A name not listed here is always
# reported: `.claude/commands` is in no flag's documented scope, so it fails closed.
CLAUDE_COVERS = {
    ".claude/settings.json": _claude_user_settings_only,
    ".claude/settings.local.json": _claude_user_settings_only,
    ".mcp.json": _has("--strict-mcp-config"),
    ".claude/agents": _has("--safe-mode"),
    ".claude/skills": _has("--safe-mode"),
}


def _claude_project_code(target, argv=()):
    found = [p for p in _present(target, CLAUDE_PROJECT_SETTINGS) if _claude_settings_run_code(p)]
    found += _present(target, [".mcp.json"] + CLAUDE_PROJECT_DIRS)
    argv = list(argv)
    return [p for p in found if not CLAUDE_COVERS.get(os.path.relpath(p, target), lambda a: False)(argv)]


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
    state_dirs=[],
)

# ---- pi: `pi -p --mode json`, the input on stdin ----
# HOME stays the real one, because a Volta shim finds its tools under $HOME. nono denies reads under
# the home by default, so the seat reads ~/.volta (read only, as the pi lead does). PI_CODING_AGENT_DIR
# moves pi's config dir and session store to the seat home. A pi seat runs on OpenRouter only for
# now, so its login env key is OPENROUTER_API_KEY (in env_keys); no credential file. pi has no
# approval prompts, so it needs no permission flag. pi echoes the prompt as a user message: errors
# come from assistant messages only, and stderr counts only when the run failed.

# Extensions, MCP servers, skills, and project-local files off (pi 1.6.0 --help).
PI_IGNORE = ["--no-extensions", "--no-mcp", "--no-skills", "--no-approve"]
PI_PROJECT_CODE = [".pi/settings.json", ".pi/extensions", ".pi/skills", ".pi/prompts", ".pi/themes", ".pi/SYSTEM.md",
                   ".pi/npm", ".pi/git"]
PI_WARNING = "Warning:"  # an stderr line that starts with this is not an error (an unknown model id, for one)


def _pi_argv(seat, input_path, deep):
    e = effort(seat, deep)
    return (["pi", "-p", "--mode", "json", "--model", seat["model"], *PI_IGNORE]
            + (["--thinking", e] if e else []) + list(seat.get("args", [])))


def _pi_session(stdout_lines, stderr_lines):
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "session" and valid_session(ev.get("id")):
            return ev["id"]
    return None


def _pi_assistant(stdout_lines):
    return [ev["message"] for ev in json_lines(stdout_lines)
            if ev.get("type") == "message_end" and isinstance(ev.get("message"), dict)
            and ev["message"].get("role") == "assistant"]


def _pi_answer(stdout_lines):
    msgs = _pi_assistant(stdout_lines)
    if not msgs or msgs[-1].get("stopReason") == "error":
        return ""
    return _text(msgs[-1].get("content"))


def _pi_usage(stdout_lines):
    used = [m["usage"] for m in _pi_assistant(stdout_lines) if isinstance(m.get("usage"), dict)]
    if not used:
        return {}
    cost = sum(_num(u["cost"].get("total")) for u in used if isinstance(u.get("cost"), dict))
    return {"tokens_in": sum(_num(u.get("input")) for u in used),
            "tokens_out": sum(_num(u.get("output")) for u in used),
            "cost_usd": round(float(cost), 6)}


def _pi_errors(stdout_lines, stderr_lines):
    """Assistant error messages. stderr lines (minus Warning: lines) count only when the run failed:
    an error message or no answer. A stray notice on a good run is not an error."""
    errs = [_str(m.get("errorMessage")) or "error" for m in _pi_assistant(stdout_lines) if m.get("stopReason") == "error"]
    if errs or not _pi_answer(stdout_lines):
        errs += [s for s in (line.strip() for line in stderr_lines) if s and not s.startswith(PI_WARNING)]
    return errs


def _pi_tool_results(stdout_lines):
    out = []
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "tool_execution_end":
            res = ev.get("result")
            out.append(_text(res.get("content")) if isinstance(res, dict) else _str(res))
    return out


PI = SeatAdapter(
    argv=_pi_argv,
    resume_argv=_resume(_pi_argv, lambda seat, sid, input_path, deep: _pi_argv(seat, input_path, deep) + ["--session", sid]),
    session_id=_pi_session,
    usage_limit=usage_limit,
    usage=_pi_usage,
    answer=_pi_answer,
    errors=_pi_errors,
    grants=lambda seat, seat_home: RuntimeGrants(read=["~/.volta"], write=[seat_home], env={"PI_CODING_AGENT_DIR": seat_home},
                                                 private_home=False,
                                                 deny_write=[os.path.join(seat_home, n) for n in PI_CODE]),
    home_env=lambda seat_home: {"PI_CODING_AGENT_DIR": seat_home},
    credentials=[],  # an API key in the env; auth.json is for OAuth providers only
    hook_files=lambda seat_home: [os.path.join(seat_home, n) for n in PI_CODE],
    # PI_IGNORE covers every .pi/ name (ruling H-ignore) only when all its flags are on the argv.
    project_code=lambda target, argv=(): ([] if all(f in argv for f in PI_IGNORE)
                                          else _present(target, PI_PROJECT_CODE)),
    inner_sandbox=[],
    backend="any",
    version_argv=["pi", "--version"],
    prompt_via="stdin",
    ignore_flags=PI_IGNORE,
    env_keys=[LOGIN_ENV["pi"]],
    tool_results=_pi_tool_results,
    state_dirs=[],
)


# ---- kimi: `kimi ... --output-format stream-json -p <file instruction>` ----
# `-p` needs an argument, and stdin is not proven, so the input comes by a fixed file instruction.
# KIMI_CODE_HOME and HOME are the seat home. Kimi Code ignores its own deny rules in -p mode: only
# the OS sandbox enforces. kimi 2.3.2 has no effort flag. Only the usage-limit error path was
# observed (stderr `error: ...`, then `See log:`); the session, answer, and tool shapes come from
# the docs and are UNVERIFIED until one OK run is recorded.

KIMI_AUTO = ["--auto"]  # "Never Ask" mode, the headless permission flag (R19)
KIMI_PROJECT_CODE = [".kimi-code/mcp.json", ".kimi-code/skills"]
KIMI_ERROR = re.compile(r"error\b", re.I)


def _kimi_flags(seat):
    return ["-m", seat["model"], "--output-format", "stream-json", *KIMI_AUTO, *seat.get("args", [])]


def _kimi_argv(seat, input_path, deep):
    return ["kimi", *_kimi_flags(seat), "-p", FILE_INSTRUCTION.format(path=input_path)]


def _kimi_resume(seat, sid, input_path, deep):
    return ["kimi", *_kimi_flags(seat), "-S", sid, "-p", FILE_INSTRUCTION.format(path=input_path)]


def _kimi_session(stdout_lines, stderr_lines):
    for ev in json_lines(stdout_lines):
        if ev.get("type") == "session.resume_hint" and valid_session(ev.get("session_id")):
            return ev["session_id"]
    return None


def _kimi_role(stdout_lines, role):
    return [ev for ev in json_lines(stdout_lines) if ev.get("role") == role]


def _kimi_answer(stdout_lines):
    msgs = _kimi_role(stdout_lines, "assistant")
    return _text(msgs[-1].get("content")) if msgs else ""


def _kimi_errors(stdout_lines, stderr_lines):
    """The stderr lines that start with `error`. Model text and tool output never count."""
    return [s for s in (line.strip() for line in stderr_lines) if KIMI_ERROR.match(s)]


def _kimi_logins(config_dir):
    """(real file, name in the seat home, refreshes) for each JSON login file under credentials/.
    Each one holds a refresh token (R11)."""
    paths = sorted(glob.glob(os.path.join(glob.escape(config_dir), "credentials", "*.json")))
    return [(p, "credentials/" + os.path.basename(p), True) for p in paths if os.path.isfile(p) and not os.path.islink(p)]


KIMI = SeatAdapter(
    argv=_kimi_argv,
    resume_argv=_resume(_kimi_argv, _kimi_resume),
    session_id=_kimi_session,
    usage_limit=usage_limit,
    usage=lambda stdout_lines: {},  # no usage shape is known
    answer=_kimi_answer,
    errors=_kimi_errors,
    grants=lambda seat, seat_home: _grants(seat_home, seat_home, KIMI_CODE, {"KIMI_CODE_HOME": seat_home}),
    home_env=lambda seat_home: {"KIMI_CODE_HOME": seat_home},
    credentials=_kimi_logins(os.environ.get("KIMI_CODE_HOME") or os.path.expanduser("~/.kimi-code")),
    hook_files=lambda seat_home: [os.path.join(seat_home, n) for n in KIMI_CODE],
    project_code=lambda target, argv=(): _present(target, KIMI_PROJECT_CODE),
    inner_sandbox=[],
    backend="any",
    version_argv=["kimi", "--version"],
    prompt_via="file",
    ignore_flags=[],  # no flag makes kimi skip project config
    env_keys=[],
    tool_results=lambda stdout_lines: [_text(ev.get("content")) for ev in _kimi_role(stdout_lines, "tool")],
    state_dirs=[],
)


# ---- agy: `agy -p <file instruction> --output-format stream-json` ----
# agy keeps its login in the macOS keychain, which it finds through $HOME. A private HOME sends it to
# a browser login (checked 2026-11-23, ruling S-agy). So agy keeps the real HOME, and it writes only
# its own state dir, which `state_dirs` declares; the AGY_CODE names in that dir stay read-only.
# Its vendor sandbox stays off (no --sandbox): Seatbelt profiles do not nest. No effort flag:
# --effort conflicts with a model id that names a variant (observed), and the config takes only
# model ids for agy. The event key is `event`, not `type`. Tool step shapes are UNVERIFIED.

AGY_PERMISSION = ["--dangerously-skip-permissions"]
AGY_PROJECT_CODE = [".agents/hooks.json", ".agents/plugins.json", ".agents/plugins", ".agents/skills",
                    ".agents/workflows", ".agents/agents", ".agents/rules"]
AGY_TEXT_STEPS = {"user_input", "agent_response"}
AGY_OK = "SUCCESS"


def _agy_state():
    return os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli")


def _agy_config():
    """Paths outside the state dir that agy loads as code or policy; already read-only, but in the
    seat key, so the key changes when they change."""
    gemini = os.path.join(os.path.expanduser("~"), ".gemini")
    return [os.path.join(gemini, "settings.json"), os.path.join(gemini, "config", "mcp_config.json"),
            os.path.join(gemini, "skills"), os.path.join(gemini, "policies")]


def _agy_argv(seat, input_path, deep):
    return ["agy", "-p", FILE_INSTRUCTION.format(path=input_path), "--model", seat["model"],
            "--output-format", "stream-json", *AGY_PERMISSION, *seat.get("args", [])]


def _agy_bodies(stdout_lines, name):
    return [ev[name] for ev in json_lines(stdout_lines) if ev.get("event") == name and isinstance(ev.get(name), dict)]


def _agy_session(stdout_lines, stderr_lines):
    """`conversation_id` on the `init` event, or in the `result` body."""
    for ev in json_lines(stdout_lines):
        name = ev.get("event")
        if name not in ("init", "result"):
            continue
        body = ev.get(name) if isinstance(ev.get(name), dict) else {}
        for sid in (ev.get("conversation_id"), body.get("conversation_id")):
            if valid_session(sid):
                return sid
    return None


def _agy_result(stdout_lines):
    results = _agy_bodies(stdout_lines, "result")
    return results[-1] if results else None


def _agy_answer(stdout_lines):
    res = _agy_result(stdout_lines)
    return _str(res.get("response")) if res and res.get("status") == AGY_OK else ""


def _agy_usage(stdout_lines):
    res = _agy_result(stdout_lines)
    if res is None:
        return {}
    u = res.get("usage") if isinstance(res.get("usage"), dict) else {}
    return {"tokens_in": _num(u.get("input_tokens")), "tokens_out": _num(u.get("output_tokens"))}


def _agy_errors(stdout_lines, stderr_lines):
    """Results whose status is not SUCCESS. stderr lines count only when the run failed: such a
    result or no answer. A stray notice on a good run is not an error."""
    errs = [_str(r.get("error")) or _str(r.get("status")) or "error"
            for r in _agy_bodies(stdout_lines, "result") if r.get("status") != AGY_OK]
    if errs or not _agy_answer(stdout_lines):
        errs += [line.strip() for line in stderr_lines if line.strip()]
    return errs


def _agy_tool_results(stdout_lines):
    return [_str(s.get("output")) for s in _agy_bodies(stdout_lines, "step_update")
            if s.get("state") == "DONE" and s.get("step_type") not in AGY_TEXT_STEPS]


AGY = SeatAdapter(
    argv=_agy_argv,
    resume_argv=_resume(_agy_argv, lambda seat, sid, input_path, deep: _agy_argv(seat, input_path, deep) + ["--conversation", sid]),
    session_id=_agy_session,
    usage_limit=usage_limit,
    usage=_agy_usage,
    answer=_agy_answer,
    errors=_agy_errors,
    grants=lambda seat, seat_home: RuntimeGrants(read=[], write=[seat_home], env={}, private_home=False,
                                                 deny_write=[os.path.join(_agy_state(), n) for n in AGY_CODE]),
    home_env=lambda seat_home: {},
    credentials=[],  # the login is in the keychain
    hook_files=lambda seat_home: [os.path.join(_agy_state(), n) for n in AGY_CODE] + _agy_config(),
    project_code=lambda target, argv=(): _present(target, AGY_PROJECT_CODE),
    inner_sandbox=[],  # vendor sandbox off: omit --sandbox
    backend="sandbox-exec",
    version_argv=["agy", "--version"],
    prompt_via="file",
    ignore_flags=[],  # no flag makes agy skip project config
    env_keys=[],
    tool_results=_agy_tool_results,
    state_dirs=[_agy_state()],
)

SEAT_ADAPTERS = {"codex": CODEX, "claude-code": CLAUDE, "pi": PI, "kimi": KIMI, "agy": AGY}
