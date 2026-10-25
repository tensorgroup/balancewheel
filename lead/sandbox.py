"""sandbox.py — bw sandbox: OS-level containment for leads that have none of their own (pi, Kimi).

A lead may write its worktree, the git data it needs to commit, its runtime's own state, and
its checkpoint dir — nothing else. Git control files stay write-denied, because a hook or
`core.hooksPath` planted there runs later outside any sandbox. Rules are ordered: Seatbelt
lets a later rule override an earlier one, which is how a linked worktree gets its own admin
dir back after the shared `worktrees/` dir is denied. Both backends also deny asking other
processes to act (LaunchServices opens, Apple Events, preference writes, launchctl): a payload
written to scratch could otherwise run unsandboxed.

    python3 -m lead.sandbox run --worktree DIR --state-dir DIR [--read P]... [--write P]...
        [--env K=V]... [--private-home] [--backend nono|sandbox-exec] -- argv...
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple

Grants = namedtuple("Grants", "rules read")              # rules: [(allow|deny, subpath|literal, path)]
RuntimeGrants = namedtuple("RuntimeGrants", "read write env private_home deny_write")
RuntimeGrants.__new__.__defaults__ = ((),)  # deny_write: paths inside `write` that stay read-only

IPC_DENIES = ["(deny lsopen)", "(deny appleevent-send)", "(deny user-preference-write)",
              '(deny process-exec (literal "/usr/bin/open") (literal "/usr/bin/osascript") '
              '(literal "/bin/launchctl") (literal "/usr/bin/defaults"))',
              # launchctl has no operation-level deny like lsopen; a copy of the binary would run
              # from a writable dir, so it is unreadable too.
              '(deny file-read* (literal "/bin/launchctl"))']
SECRET_READS = [".ssh", ".aws", ".gnupg", ".netrc", ".config/gh", ".docker/config.json",
                ".codex/auth.json", ".claude/.credentials.json", ".kimi-code/credentials", ".pi/agent/auth.json",
                ".gemini/oauth_creds.json", ".config/gcloud"]


def check_path(p):
    if '"' in p or "\\" in p:
        raise ValueError(f"refusing a sandbox path containing a quote or backslash: {p!r}")
    return p


def _real(p):
    return check_path(os.path.realpath(os.path.expanduser(p)))


def refuse_overlap(target, writable):
    t = _real(target)
    for w in writable:
        w = _real(w)
        if t == w or t.startswith(w.rstrip("/") + "/") or w.startswith(t.rstrip("/") + "/"):
            raise ValueError(f"refusing: {t} overlaps writable grant {w}")


def _git_dir(worktree):
    r = subprocess.run(["git", "-C", worktree, "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, check=True)
    return os.path.realpath(r.stdout.strip())


def lead_grants(project, runtime, state_dir):
    """Ordered write rules and read grants for a lead working in `project.worktree`."""
    common, wt = project.common_dir, project.worktree
    state = _real(state_dir)
    # bw's own control files (locks, pins, quota, containment) must stay out of every write grant.
    refuse_overlap(state, [wt])
    homes = os.path.join(state, "homes")
    for p in runtime.write:
        rp = _real(p)
        if not rp.startswith(homes.rstrip("/") + "/"):
            refuse_overlap(rp, [state])
    handoffs = os.path.join(state, "handoffs", project.worktree_key)
    os.makedirs(handoffs, exist_ok=True)
    rules = [("allow", "subpath", wt), ("allow", "subpath", common), ("allow", "subpath", handoffs)]
    rules += [("allow", "subpath", _real(p)) for p in runtime.write]
    rules += [("deny", "subpath", _real(p)) for p in runtime.deny_write]
    # Anything git reads as configuration or runs as code stays read-only: hooks, config,
    # attributes (info/), submodule git dirs (modules/), other worktrees' admin dirs, and the
    # pointers that say where the git dir is — rewriting those aims git at a planted config.
    rules += [("deny", "subpath", os.path.join(common, "hooks")),
              ("deny", "literal", os.path.join(common, "config")),
              ("deny", "literal", os.path.join(common, "config.worktree")),
              ("deny", "subpath", os.path.join(common, "info")),
              ("deny", "subpath", os.path.join(common, "modules")),
              ("deny", "subpath", os.path.join(common, "worktrees")),
              ("deny", "literal", os.path.join(wt, ".git"))]  # main: the dir node (no rename); linked: the gitdir file
    own_admin = _git_dir(wt)
    if own_admin != common:  # linked worktree: give back its own admin dir, minus its pointers and config
        rules += [("allow", "subpath", own_admin),
                  ("deny", "literal", os.path.join(own_admin, "config.worktree")),
                  ("deny", "literal", os.path.join(own_admin, "commondir")),
                  ("deny", "literal", os.path.join(own_admin, "gitdir"))]
    for _, _, p in rules:
        check_path(p)
    # git needs the user's global config (identity, includes) to commit; read-only.
    home = os.path.expanduser("~")
    git_cfg = [os.path.join(home, ".gitconfig"), os.path.join(home, ".config", "git")]
    reads = [_real(p) for p in runtime.read] + [_real(p) for p in git_cfg if os.path.exists(p)]
    # A symlinked path (dotfiles) is read through its link: grant the link node as well.
    reads += [check_path(os.path.abspath(os.path.expanduser(p))) for p in list(runtime.read) + git_cfg
              if os.path.islink(os.path.expanduser(p))]
    return Grants(rules, reads)


def _rule(action, kind, path):
    return f'({action} file-write* ({kind} "{path}"))'


def seatbelt_profile(grants, scratch):
    home = os.path.expanduser("~")
    parts = ["(version 1)", "(allow default)", "(deny file-write*)",
             f'(allow file-write* (subpath "/dev") (subpath "{check_path(os.path.realpath(scratch))}"))']
    parts += [_rule(*r) for r in grants.rules]
    own = [p for _, _, p in grants.rules] + list(grants.read)  # a runtime still reads its own login
    paths = [os.path.realpath(os.path.join(home, s)) for s in SECRET_READS]
    paths = [p for p in paths if not any(p == o or p.startswith(o.rstrip("/") + "/") for o in own)]
    secrets = " ".join(f'(subpath "{check_path(p)}")' for p in paths)
    parts.append(f"(deny file-read* {secrets})")
    parts += IPC_DENIES
    return "".join(parts)


def nono_profile(grants, scratch):
    first_allows, ordered = [], []
    seen_deny = False
    for r in grants.rules:
        if r[0] == "deny":
            seen_deny = True
        if r[0] == "allow" and r[1] == "subpath" and not seen_deny:
            first_allows.append(r[2])
        else:
            ordered.append(_rule(*r))
    return {
        "extends": "default",
        "meta": {"name": "bw-lead"},
        "groups": {"exclude": ["system_write_macos"]},
        "filesystem": {"allow": [check_path(os.path.realpath(scratch))] + first_allows,
                       "write": ["/dev"],
                       "read": [p for p in grants.read if not os.path.islink(p) and not os.path.isfile(p)],
                       "read_file": [p for p in grants.read if not os.path.islink(p) and os.path.isfile(p)]},
        # nono resolves grants to real paths; the link node itself needs a raw read rule.
        "unsafe_macos_seatbelt_rules": [f'(allow file-read* (literal "{p}"))' for p in grants.read if os.path.islink(p)]
                                       + ordered + IPC_DENIES,
    }


def backend():
    return "nono" if shutil.which("nono") else "sandbox-exec"


def wrap(argv, grants, which, home, scratch, env):
    """The argv that runs `argv` contained, and a cleanup for any profile file written."""
    envs = [f"HOME={home}", f"TMPDIR={scratch}"] + [f"{k}={v}" for k, v in env.items()]
    if which == "nono":
        fd, path = tempfile.mkstemp(prefix="bw-lead-", suffix=".json", dir=scratch)
        with os.fdopen(fd, "w") as f:
            json.dump(nono_profile(grants, scratch), f)
        return (["nono", "run", "--silent", "--allow-cwd", "--profile", path, "--", "/usr/bin/env", *envs, *argv],
                lambda: os.path.exists(path) and os.remove(path))
    return (["sandbox-exec", "-p", seatbelt_profile(grants, scratch), "/usr/bin/env", *envs, *argv], lambda: None)


def _cli(args):
    import argparse
    from .projects import resolve_project
    ap = argparse.ArgumentParser(prog="python3 -m lead.sandbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run")
    run.add_argument("--worktree", required=True)
    run.add_argument("--state-dir", required=True)
    run.add_argument("--read", action="append", default=[])
    run.add_argument("--write", action="append", default=[])
    run.add_argument("--deny-write", action="append", default=[])
    run.add_argument("--env", action="append", default=[])
    run.add_argument("--private-home", action="store_true")
    run.add_argument("--backend", choices=["nono", "sandbox-exec"])
    run.add_argument("argv", nargs=argparse.REMAINDER)
    a = ap.parse_args(args)
    argv = a.argv[1:] if a.argv and a.argv[0] == "--" else a.argv
    project = resolve_project(a.worktree, {"projects": {}, "default_profile": "none"})
    env = dict(e.split("=", 1) for e in a.env)
    scratch = tempfile.mkdtemp(prefix="bw-lead-scratch-")
    scratch = os.path.realpath(scratch)
    writes = list(a.write)
    home = os.path.expanduser("~")
    if a.private_home:
        from .runner import _link_git_identity
        home = os.path.join(os.path.realpath(a.state_dir), "homes", project.worktree_key)
        os.makedirs(home, exist_ok=True)
        _link_git_identity(home)
        writes.append(home)
    grants = lead_grants(project, RuntimeGrants(a.read, writes, env, a.private_home, a.deny_write), a.state_dir)
    full, _ = wrap(argv, grants, a.backend or backend(), home, scratch, env)
    os.chdir(project.worktree)
    os.execvp(full[0], full)


if __name__ == "__main__":
    _cli(sys.argv[1:])
