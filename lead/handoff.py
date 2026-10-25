"""handoff.py — what the next lead is told when it takes over a worktree.

Built from facts bw can read, not from transcripts: branch, status, commits since the outgoing
session began, diff stat, the lead's last checkpoint, and open inbox items. bw reads git
OUTSIDE the sandbox here, and the outgoing lead could write the repo, so git runs with hooks,
fsmonitor, external diff drivers, textconv, and optional locks all off, and with GIT_*
variables cleared: nothing a lead planted in the repo runs at handoff time.
"""
import os
import subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "--no-pager"]


def _git(worktree, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    r = subprocess.run(["git", "-C", worktree, *SAFE, *args], capture_output=True, text=True, env=env)
    return r.stdout.strip() if r.returncode == 0 else f"(git {args[0]} failed: {r.stderr.strip()[:200]})"


def checkpoint_dir(state_dir, worktree_key):
    return os.path.join(state_dir, "handoffs", worktree_key)


def read_checkpoint(state_dir, worktree_key):
    try:
        with open(os.path.join(checkpoint_dir(state_dir, worktree_key), "checkpoint.md")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def build_handoff(worktree, since_head, state_dir, worktree_key, inbox_items):
    lines = ["@next handoff",
             f"branch: {_git(worktree, 'branch', '--show-current') or '(detached)'}",
             "status:", _git(worktree, "status", "--short") or "(clean)"]
    if since_head:
        lines += ["commits since the last lead started:",
                  _git(worktree, "log", "--no-ext-diff", "--no-textconv", "--oneline", f"{since_head}..HEAD") or "(none)",
                  "diff stat:",
                  _git(worktree, "diff", "--no-ext-diff", "--no-textconv", "--stat", since_head) or "(none)"]
    cp = read_checkpoint(state_dir, worktree_key)
    lines += ["checkpoint:", cp] if cp else ["checkpoint: none"]
    if inbox_items:
        lines += ["open questions for the user:"] + [f"- {i}" for i in inbox_items]
    return "\n".join(lines)


def protocol_text():
    with open(os.path.join(HERE, "protocol", "checkpoint.md")) as f:
        return f.read().strip()


def first_prompt(handoff, protocol):
    return f"{protocol}\n\n{handoff}" if handoff else protocol
