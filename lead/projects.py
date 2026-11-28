"""projects.py — resolve a working directory to its worktree, repository, and profile.

The profile belongs to the repository (every worktree of a repo gets it); the lock and the
handoff directory belong to the worktree (two worktrees are two independent tasks).
"""
import hashlib
import os
from collections import namedtuple
from typing import Optional

from . import proc

Project = namedtuple("Project", "worktree worktree_key common_dir main_worktree profile")


class NotARepository(ValueError):
    """`path` is not inside a git repository. `repos` are the repositories directly under it,
    most recently used first, so a caller can say which path to give instead."""

    def __init__(self, path):
        self.path = path
        super().__init__(f"not a git repository: {path}")

    @property
    def repos(self):  # read only by the help that prints them; most callers only need the message
        return child_repositories(self.path)


def child_repositories(path):
    """The git repositories one level under `path` (a `.git` file or dir), newest `.git` first."""
    try:
        names = os.listdir(path)
    except OSError:
        return []
    found = []
    for name in names:
        if name.startswith("."):
            continue
        git_entry = os.path.join(path, name, ".git")
        try:
            found.append((os.stat(git_entry).st_mtime, os.path.join(path, name)))
        except OSError:
            continue
    return [p for _, p in sorted(found, key=lambda t: (-t[0], t[1]))]


def _git(run, cwd, *args):
    r = run(["git", "-C", cwd, *args], 30)
    if r.rc != 0:
        if not os.path.exists(cwd):
            raise ValueError(f"no such directory: {cwd}")
        if not os.path.isdir(cwd):
            raise ValueError(f"not a directory: {cwd}")
        err = r.err.strip()
        if not err or "not a git repository" in err:
            raise NotARepository(cwd)
        # Another reason (for example "dubious ownership") tells the user what to fix: keep git's words.
        raise ValueError(f"git failed in {cwd}: {err}")
    return r.out


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


def resolve_project(cwd, config, run=proc.run):
    cwd = os.path.realpath(cwd)
    if _git(run, cwd, "rev-parse", "--is-bare-repository").strip() == "true":
        raise ValueError(f"bare repository not supported as a lead workspace: {cwd}")
    out = _git(run, cwd, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir").splitlines()
    if len(out) < 2:
        raise NotARepository(cwd)
    worktree, common_dir = os.path.realpath(out[0]), os.path.realpath(out[1])
    main_worktree = None
    for line in _git(run, cwd, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            main_worktree = os.path.realpath(line[len("worktree "):])
            break
    if not main_worktree or main_worktree == common_dir:
        raise ValueError(f"unsupported repository layout (no main worktree) for {cwd}")
    profile = _match_profile(main_worktree, config.get("projects", {}), config.get("default_profile"))
    return Project(worktree, worktree_key(worktree), common_dir, main_worktree, profile)


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def refuse_inside(project, bw_path, config_path=None) -> Optional[str]:
    """Why bw must not run against `project`, or None. bw's own code and its config (whose
    `tools.*.update`/`version` argv bw runs unsandboxed) must lie outside the target worktree and
    its common git dir: a lead there could edit the next `bw lead` or what it runs."""
    for what, p, why in (("bw", bw_path, "a lead there could edit the next `bw lead` — run bw from a "
                                          "checkout outside the target repository"),
                         ("the config", config_path, "a lead there could edit what bw runs — keep the "
                                                     "config outside the target repository")):
        if not p:
            continue
        real = os.path.realpath(p)
        for root in (project.worktree, project.common_dir):
            if _inside(real, root):
                return f"refusing: {what} ({real}) lies inside {root}; {why}"
    return None
