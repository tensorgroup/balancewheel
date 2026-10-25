"""containment.py — which lead runtimes have passed the containment attack, on which key.

A pass is valid only for the exact verification key it was recorded against: the runtime's
version, the sandbox backend and its version, and hashes of the profile generator and the
runtime adapters. Any change re-requires `bw sandbox verify <runtime>`. Only that command
writes `state_dir/containment.json`; a contained lead cannot (its writes stop at its worktree).
"""
import hashlib
import os
import platform
import shutil
import subprocess
import time

from .paths import read_json, write_json_atomic

HERE = os.path.dirname(os.path.realpath(__file__))


def _sha(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return "absent"


def _first_line(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return ((r.stdout or r.stderr).strip().splitlines() or ["unknown"])[0]
    except (OSError, subprocess.SubprocessError):
        return "unreadable"


def verification_key(runtime, version_argv, backend):
    if backend == "nono" and shutil.which("nono"):
        backend_version = _first_line(["nono", "--version"])
    else:
        backend_version = "macos-" + (platform.mac_ver()[0] or "unknown")
    return {"runtime": runtime, "runtime_version": _first_line(version_argv), "backend": backend,
            "backend_version": backend_version, "sandbox_sha": _sha(os.path.join(HERE, "sandbox.py")),
            "adapter_sha": _sha(os.path.join(HERE, "runtimes.py"))}


def _path(state_dir):
    return os.path.join(state_dir, "containment.json")


def record_pass(state_dir, runtime, key, evidence_dir):
    data = read_json(_path(state_dir), {}) or {}
    data[runtime] = {"key": key, "evidence": evidence_dir, "ts": time.time()}
    write_json_atomic(_path(state_dir), data)


def is_verified(state_dir, runtime, key):
    entry = (read_json(_path(state_dir), {}) or {}).get(runtime)
    return bool(entry) and entry.get("key") == key
