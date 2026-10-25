"""projects.py — resolve a working directory to its worktree, repository, and profile.

The profile belongs to the repository (every worktree of a repo gets it); the lock and the
handoff directory belong to the worktree (two worktrees are two independent tasks).
"""
import hashlib
import os
import subprocess
from collections import namedtuple

Project = namedtuple("Project", "worktree worktree_key common_dir main_worktree profile")


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(f"not a git repository: {cwd} ({r.stderr.strip() or 'git failed'})")
    return r.stdout


def worktree_key(worktree):
    return hashlib.sha1(os.path.realpath(worktree).encode()).hexdigest()[:12]


def _components(path):
    return [p for p in os.path.realpath(os.path.expanduser(path)).split(os.sep) if p]


def _match_profile(main_worktree, projects, default_profile):
    target = _components(main_worktree)
    best, best_len = default_profile, -1
    for key, profile in projects.items():
        parts = _components(key)
        if len(parts) > best_len and target[:len(parts)] == parts:
            best, best_len = profile, len(parts)
    return best


def resolve_project(cwd, config):
    cwd = os.path.realpath(cwd)
    if _git(cwd, "rev-parse", "--is-bare-repository").strip() == "true":
        raise ValueError(f"bare repository not supported as a lead workspace: {cwd}")
    out = _git(cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir").splitlines()
    if len(out) < 2:
        raise ValueError(f"not a git repository: {cwd}")
    worktree, common_dir = os.path.realpath(out[0]), os.path.realpath(out[1])
    main_worktree = None
    for line in _git(cwd, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            main_worktree = os.path.realpath(line[len("worktree "):])
            break
    if not main_worktree or main_worktree == common_dir:
        raise ValueError(f"unsupported repository layout (no main worktree) for {cwd}")
    profile = _match_profile(main_worktree, config.get("projects", {}), config.get("default_profile"))
    return Project(worktree, worktree_key(worktree), common_dir, main_worktree, profile)
