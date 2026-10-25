"""install.py — the one guard file bw writes outside its own state dir: agy's user-level hooks file.

`bw guard install agy` adds a single entry, "bw-guard", and keeps every other key as it was.
The command is inert unless bw set BW_GUARD_MODE (a hand-run agy answers "no opinion" without
starting Python or touching bw's checkout); a guarded session runs its own copy of hook.py
from BW_GUARD_DIR. pi needs no install: bw passes its client with `-e` per session.
"""
import json
import os

from .hook import INERT_ANSWER

ENTRY = "bw-guard"
EVENT = "PreToolUse"
MATCHER = "*"
TIMEOUT = 5


def hooks_path():
    return os.environ.get("BW_AGY_HOOKS") or os.path.expanduser("~/.gemini/config/hooks.json")


def pi_agent_dir():
    return os.environ.get("BW_PI_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def _command():
    return ("/bin/sh -c '[ -n \"$BW_GUARD_MODE\" ] || { printf \"%s\\n\" \"" + INERT_ANSWER + "\"; cat >/dev/null; exit 0; }; "
            "exec /usr/bin/python3 -I \"$BW_GUARD_DIR/hook.py\" agy'")


def _entry():
    return {EVENT: [{"matcher": MATCHER, "hooks": [{"type": "command", "command": _command(), "timeout": TIMEOUT}]}]}


def _load(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"{path} is not valid JSON ({e}); fix or move it, bw wrote nothing") from e
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object; bw wrote nothing")
    return data


def _write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def install_agy(path, state_dir):
    data = _load(path)
    data[ENTRY] = _entry()
    _write(path, data)
    return (f"bw: installed the agy guard hook in {path} (listed in setup/paths.txt). It does nothing for a hand-run agy. "
            "Every agy-based seat must be re-verified: run `bw guard verify` again.")


def uninstall_agy(path):
    if not os.path.exists(path):
        return
    data = _load(path)
    if ENTRY in data:
        del data[ENTRY]
        _write(path, data)


def agy_installed(path):
    try:
        entry = _load(path).get(ENTRY)
    except ValueError:
        return False
    return entry == _entry()


def other_pretooluse_entries(path):
    try:
        data = _load(path)
    except ValueError:
        return []
    return sorted(k for k, v in data.items() if k != ENTRY and isinstance(v, dict) and v.get(EVENT))
