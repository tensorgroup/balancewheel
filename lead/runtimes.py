"""runtimes.py — how bw launches each lead runtime interactively, and how it is contained.

contained_by:
  "bw"   — no verified containment of its own (pi, Kimi): runs under bw sandbox, and only after
           `bw sandbox verify <runtime>` passed on the current verification key.
  "self" — the runtime's own sandbox and interactive permission prompts, with the user's real
           HOME so its settings, statusline, commands, and login keep working (Claude Code,
           Codex with workspace-write, agy with --sandbox). Same trust as running it by hand.
prompt_mode "arg": the CLI takes an initial interactive prompt; "file": bw writes the first
prompt to the checkpoint dir and prints its path (Kimi Code has no initial-prompt flag).
"""
import os
from collections import namedtuple

from .guard import install as _guard_install
from .sandbox import RuntimeGrants

Adapter = namedtuple("Adapter", "argv contained_by grants prompt_mode version_argv verify_argv verify_flags "
                                "guard_argv guard_hook_files")
# guard_argv(lead, client_dir) -> the headless command the guard attack runs (None: no guard attack for
# this runtime); guard_hook_files() -> files and dirs whose content decides what the guard hooks run.
Adapter.__new__.__defaults__ = (None, lambda: [])

# Guarded pi leads run with --no-extensions (the explicit -e client still loads): a project-local
# extension the lead wrote into its worktree would otherwise run in the guard client's own process.
# Permission posture per runtime and autonomy (schema 3). "{client}" is the session's pi_guard.ts.
# Schema-2 (legacy) and custom leads get no flags: their argv is what it always was.
AUTONOMY_FLAGS = {
    "claude-code": {"native": ["--permission-mode", "auto"], "bypass": ["--permission-mode", "bypassPermissions"],
                    "manual": ["--permission-mode", "default"]},
    "codex": {"native": ["--approve-for-me"], "manual": []},
    "agy": {"guard": ["--dangerously-skip-permissions"], "bypass": ["--dangerously-skip-permissions"], "manual": []},
    "pi": {"guard": ["--no-extensions", "-e", "{client}"], "bypass": []},
    "kimi": {"native": ["--yolo"], "bypass": ["--auto"], "manual": []},
}


def autonomy_flags(runtime, lead, client_dir):
    if lead.get("autonomy_source", "legacy") == "legacy":
        return []
    flags = AUTONOMY_FLAGS.get(runtime, {}).get(lead.get("autonomy"), [])
    if "{client}" in " ".join(flags) and not client_dir:
        raise ValueError("guarded pi lead needs a client dir")
    return [f.replace("{client}", os.path.join(client_dir or "", "pi_guard.ts")) for f in flags]


def _pi_read_grants(lead, client_dir):
    if lead.get("autonomy_source", "legacy") != "legacy" and lead.get("autonomy") == "guard":
        if not client_dir:
            raise ValueError("guarded pi lead needs a client dir")
        return [client_dir]
    return []


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def _with_prompt(base, prompt):
    return base + ([prompt] if prompt else [])


def _add_dirs(writable):
    """Extra writable dirs (the checkpoint dir) for runtimes whose own sandbox stops at the worktree."""
    return [a for d in writable for a in ("--add-dir", d)]


# Inside a runtime's writable state dir, what loads as code or config in the user's NEXT,
# unsandboxed session stays read-only — a contained lead must not plant a hook there. Sessions,
# caches, logs, and the vendor's own credential refresh stay writable.
KIMI_CODE = ["bin", "config.toml", "tui.toml", "mcp.json", "AGENTS.md", "skills", "plugins", "hooks", "agents"]
PI_CODE = ["extensions", "settings.json", "models.json", "skills", "prompts", "themes", "AGENTS.md", "SYSTEM.md"]

ADAPTERS = {
    "claude-code": Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: _with_prompt(
            ["claude", "--model", lead["model"], *autonomy_flags("claude-code", lead, client_dir), *lead.get("args", [])], prompt),
        contained_by="self", grants=lambda lead, client_dir=None: RuntimeGrants([], [], {}, False), prompt_mode="arg",
        version_argv=["claude", "--version"], verify_argv=None, verify_flags=lambda lead: []),
    "codex": Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: _with_prompt(
            ["codex", "-m", lead["model"], "--sandbox", "workspace-write", *_add_dirs(writable),
             *autonomy_flags("codex", lead, client_dir), *lead.get("args", [])], prompt),
        contained_by="self", grants=lambda lead, client_dir=None: RuntimeGrants([], [], {}, False), prompt_mode="arg",
        version_argv=["codex", "--version"], verify_argv=None, verify_flags=lambda lead: []),
    "agy": Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: (
            ["agy", "--model", lead["model"], "--sandbox", *_add_dirs(writable),
             *autonomy_flags("agy", lead, client_dir), *lead.get("args", [])] + (["-i", prompt] if prompt else [])),
        contained_by="self", grants=lambda lead, client_dir=None: RuntimeGrants([], [], {}, False), prompt_mode="arg",
        version_argv=["agy", "--version"], verify_argv=None, verify_flags=lambda lead: [],
        guard_argv=lambda lead, client_dir: ["agy", "-p", "--model", lead["model"], "--sandbox", "--dangerously-skip-permissions"],
        guard_hook_files=lambda: [_guard_install.hooks_path(), os.path.expanduser("~/.gemini/settings.json")]),
    "pi": Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: _with_prompt(
            ["pi", "--model", lead["model"], *autonomy_flags("pi", lead, client_dir), *lead.get("args", [])], prompt),
        contained_by="bw",
        grants=lambda lead, client_dir=None: RuntimeGrants(
            read=["~/.volta"] + _pi_read_grants(lead, client_dir),
            write=[_real("~/.pi/agent")], env={}, private_home=False,
            deny_write=[_real(f"~/.pi/agent/{p}") for p in PI_CODE]),
        prompt_mode="arg", version_argv=["pi", "--version"],
        verify_argv=lambda lead: ["pi", "-p", "--model", lead["model"], "--no-session", "--no-extensions"],
        verify_flags=lambda lead: ["--read", _real("~/.volta"), "--write", _real("~/.pi/agent")]
                                  + [f for p in PI_CODE for f in ("--deny-write", _real(f"~/.pi/agent/{p}"))],
        guard_argv=lambda lead, client_dir: ["pi", "-p", "--mode", "json", "--model", lead["model"], "--no-extensions",
                                                      "-e", os.path.join(client_dir, "pi_guard.ts")],
        guard_hook_files=lambda: [os.path.join(_guard_install.pi_agent_dir(), "settings.json"),
                                  os.path.join(_guard_install.pi_agent_dir(), "extensions")]),
    "kimi": Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: ["kimi", "-m", lead["model"],
                                                                *autonomy_flags("kimi", lead, client_dir), *lead.get("args", [])],
        contained_by="bw",
        grants=lambda lead, client_dir=None: RuntimeGrants(read=["~/.kimi-code/bin", "~/.volta"], write=[_real("~/.kimi-code")],
                                          env={"KIMI_CODE_HOME": _real("~/.kimi-code")}, private_home=True,
                                          deny_write=[_real(f"~/.kimi-code/{p}") for p in KIMI_CODE]),
        prompt_mode="file", version_argv=["kimi", "--version"],
        verify_argv=lambda lead: ["kimi", "-m", lead["model"], "--output-format", "stream-json", "-p"],
        verify_flags=lambda lead: ["--private-home", "--read", _real("~/.kimi-code/bin"), "--read", _real("~/.volta"),
                                   "--write", _real("~/.kimi-code"), "--env", f"KIMI_CODE_HOME={_real('~/.kimi-code')}"]
                                  + [f for p in KIMI_CODE for f in ("--deny-write", _real(f"~/.kimi-code/{p}"))]),
}
