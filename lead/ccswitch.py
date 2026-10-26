"""cc-switch's settings contract.

bw reads cc-switch's usage database and nothing else of it, so everything that would make
it route, fail over, sync, or update on its own stays off. `write_settings` pre-writes that
contract before cc-switch's first launch and never overwrites a file that differs;
`check_settings` reports drift.
"""
import json
import os

from lead.checks import Check

REQUIRED_SETTINGS = {
    "enableLocalProxy": False,
    "enableStackMode": False,
    "enableFailoverToggle": False,
    "unifyCodexSessionHistory": False,
    "launchOnStartup": False,
    "checkToolUpdatesOnStartup": False,
    "sessionAutoSyncEnabled": True,
}


def data_dir():
    return os.environ.get("BW_CCSWITCH_DIR") or os.path.expanduser("~/.cc-switch")


def _settings_path(d):
    return os.path.join(d, "settings.json")


def _differences(current):
    """[(key, current, required)] for each required key not at its value (absent: None)."""
    return [(k, current.get(k), v) for k, v in REQUIRED_SETTINGS.items()
            if k not in current or current[k] is not v]


def _read(d):
    """(settings dict, None) or (None, reason)."""
    try:
        with open(_settings_path(d)) as f:
            cur = json.load(f)
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as e:
        return None, f"unreadable ({type(e).__name__})"
    if not isinstance(cur, dict):
        return None, "unreadable (not a JSON object)"
    return cur, None


def write_settings(d=None):
    d = d or data_dir()
    path = _settings_path(d)
    if os.path.lexists(path):
        cur, why = _read(d)
        if cur is None:
            return 1, f"refusing to overwrite {path}; {why}"
        diff = _differences(cur)
        if not diff:
            return 0, "already set"
        shown = ", ".join(f"{k}: {json.dumps(c) if c is None else c} → {r}" for k, c, r in diff)
        return 1, f"refusing to overwrite {path}; differs: {shown}"
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(REQUIRED_SETTINGS, f, indent=2)
        f.write("\n")
    return 0, f"wrote {path}"


def check_settings(d=None):
    d = d or data_dir()
    if not os.path.isdir(d):
        return [Check("cc-switch", "unknown", "not installed (Usage view off)")]
    cur, why = _read(d)
    if cur is None:
        return [Check("cc-switch settings", "unknown", f"settings.json {why}")]
    warns = [Check(k, "warn", f"{k} is {json.dumps(c)}, should be {json.dumps(r)}") for k, c, r in _differences(cur)]
    return warns or [Check("cc-switch settings", "ok", "")]
