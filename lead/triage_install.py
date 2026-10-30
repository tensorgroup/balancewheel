"""triage_install.py — `bw triage install claude`: one PreToolUse entry in Claude Code's user settings.

The command is inert unless bw set BW_TRIAGE_HOOK for this lead, so a hand-run session answers "no
opinion" without starting Python. `TIMEOUT` sits above the hook's own alarm; a killed hook lets the
pop-up show. Every other key and entry is left as it was. The entry is found by its command.
"""
import json
import os
import stat

from .guard.install import _load

MATCHER = "AskUserQuestion"
TIMEOUT = 6
COMMAND = "/bin/sh -c '[ -n \"$BW_TRIAGE_HOOK\" ] || exit 0; exec /usr/bin/python3 -I \"$BW_TRIAGE_HOOK\"'"


def settings_path():
    return os.environ.get("BW_CLAUDE_SETTINGS") or os.path.expanduser("~/.claude/settings.json")


def _write(path, data):
    """Through any symlink, keeping the file's mode: settings can hold private values."""
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o600
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), mode)
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def _ours(hook):
    return isinstance(hook, dict) and "BW_TRIAGE_HOOK" in str(hook.get("command"))


def _without_ours(entries):
    """Entries minus our hook objects. A hook sharing an entry with ours stays; an entry left empty goes."""
    kept = []
    for e in entries:
        hooks = e.get("hooks") if isinstance(e, dict) else None
        if not isinstance(hooks, list) or not any(_ours(h) for h in hooks):
            kept.append(e)
        elif any(not _ours(h) for h in hooks):
            kept.append({**e, "hooks": [h for h in hooks if not _ours(h)]})
    return kept


def _entries(data):
    hooks = data.get("hooks")
    if hooks is None:
        return []
    entries = hooks.get("PreToolUse", []) if isinstance(hooks, dict) else None
    if not isinstance(entries, list):
        raise ValueError("the hooks in Claude Code's settings are not in the expected shape; bw wrote nothing")
    return entries


def install(path):
    data = _load(path)
    entries = _without_ours(_entries(data))
    entries.append({"matcher": MATCHER, "hooks": [{"type": "command", "command": COMMAND, "timeout": TIMEOUT}]})
    if data.get("hooks") is None:
        data["hooks"] = {}
    data["hooks"]["PreToolUse"] = entries
    _write(path, data)
    return (f"bw: installed the triage hook in {path}. It does nothing unless triage.mode is shadow or acting for the "
            "lead's profile. A running lead picks it up at its next start.")


def uninstall(path):
    if not os.path.exists(path):
        return "bw: nothing to remove"
    data = _load(path)
    entries = _entries(data)
    kept = _without_ours(entries)
    if kept == entries:
        return "bw: nothing to remove"
    data["hooks"]["PreToolUse"] = kept
    _write(path, data)
    return f"bw: removed the triage hook from {path}"
