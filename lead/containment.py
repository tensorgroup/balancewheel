"""containment.py — which lead runtimes have passed the containment attack, on which key.

A pass is valid only for the exact verification key it was recorded against: the runtime's
version, the sandbox backend and its version, and hashes of the profile generator and the
runtime adapters. Any change re-requires `bw sandbox verify <runtime>`. Only that command
writes `state_dir/containment.json`; a contained lead cannot (its writes stop at its worktree).
"""
import hashlib
import os
import json
import platform
import re
import shutil
import subprocess
import time

from .guard import guard_version
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


def _manifest(path, seen=()):
    """Content manifest for one hook file or directory: shas, sorted relative paths, links recorded as
    links and followed (a linked directory is hashed too; a cycle is recorded, not followed)."""
    real = os.path.realpath(path)
    if os.path.islink(path):
        return {"link": os.readlink(path), "target": _manifest(real, seen)}
    if os.path.isdir(path):
        if real in seen:
            return {"cycle": True}
        seen = seen + (real,)
        out = {}
        for root, dirs, files in os.walk(path):
            dirs.sort()
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
            linked = [d for d in sorted(os.listdir(root)) if os.path.islink(os.path.join(root, d)) and os.path.isdir(os.path.join(root, d))]
            for n in sorted(files) + linked:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, path)
                out[rel] = _manifest(full, seen) if os.path.islink(full) else _sha(full)
        return {"dir": out}
    return {"sha": _sha(path), "scripts": _named_scripts(path)}


def _commands(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "command" and isinstance(v, str):
                yield v
            else:
                yield from _commands(v)
    elif isinstance(node, list):
        for v in node:
            yield from _commands(v)


def _named_scripts(path):
    """sha of every existing absolute file a hook command in this (JSON) file names."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for cmd in _commands(data):
        for tok in re.findall(r"/[^\s'\"();|&<>]+", cmd):
            if os.path.isfile(tok):
                out[tok] = _sha(tok)
    return out


def guard_key(runtime, version_argv, backend, hook_files):
    key = verification_key(runtime, version_argv, backend)
    key["guard_version"] = guard_version()
    key["hook_files"] = {p: _manifest(p) for p in sorted(hook_files)}
    return key


def _path(state_dir, store="containment.json"):
    return os.path.join(state_dir, store)


def record_pass(state_dir, runtime, key, evidence_dir, store="containment.json"):
    data = read_json(_path(state_dir, store), {}) or {}
    data[runtime] = {"key": key, "evidence": evidence_dir, "ts": time.time()}
    write_json_atomic(_path(state_dir, store), data)


def is_verified(state_dir, runtime, key, store="containment.json"):
    entry = (read_json(_path(state_dir, store), {}) or {}).get(runtime)
    return bool(entry) and entry.get("key") == key
