"""paths.py — where lead state lives, and how lead code finds the config loader."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "metrics"))
import config as _config  # noqa: E402  (metrics/config.py; stdlib-only loader)

load_config = _config.load_config
ConfigError = _config.ConfigError


def state_dir(config=None):
    """BW_STATE_DIR wins (tests, statusline hooks); otherwise the config's state_dir."""
    env = os.environ.get("BW_STATE_DIR")
    if env:
        return os.path.realpath(env)
    return (config or load_config())["state_dir"]


def write_json_atomic(path, obj):
    """Temp file in the same directory, then rename: readers never see a partial file."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


class locked_dir:
    """An exclusive flock on <dir>/.lock for a read-modify-write across processes."""

    def __init__(self, d):
        os.makedirs(d, exist_ok=True)
        self.path = os.path.join(d, ".lock")

    def __enter__(self):
        import fcntl
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default
