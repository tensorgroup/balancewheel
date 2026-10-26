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
        "-c", "core.attributesFile=/dev/null", "--no-pager"]
GIT_TIMEOUT = 5  # seconds: a hung git call must not hold a broker worker or bw
INBOX_LIMIT = 16 * 1024  # characters of open inbox items in one handoff
INBOX_CUT = "\n[cut — run `bw inbox --project`]"
SECTION_LINES = 200       # git status / log / diff --stat lines kept per section
SECTION_BYTES = 16 * 1024  # and bytes: the handoff can travel on argv (E2BIG mid-switch otherwise)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # attributes read from here: no filters run


def _git_rc(worktree, *args):
    """(returncode, stdout, stderr), run the safe way: nothing the lead planted in the repo runs."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_ATTR_SOURCE"] = EMPTY_TREE  # git >= 2.42: ignore the worktree's .gitattributes (clean/smudge filters)
    try:
        r = subprocess.run(["git", "-C", worktree, *SAFE, *args], capture_output=True, text=True, env=env,
                           timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "", f"git {args[0]} timed out after {GIT_TIMEOUT}s"
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _git(worktree, *args):
    rc, out, err = _git_rc(worktree, *args)
    return out if rc == 0 else f"(git {args[0]} failed: {err[:200]})"


def _bounded(text):
    """At most SECTION_LINES lines and SECTION_BYTES bytes of `text`, then "… K more" lines."""
    lines = text.split("\n")
    kept, size = [], 0
    for ln in lines:
        n = len(ln.encode("utf-8", errors="surrogateescape")) + 1
        if len(kept) >= SECTION_LINES or size + n > SECTION_BYTES:
            break
        kept.append(ln)
        size += n
    more = len(lines) - len(kept)
    return "\n".join(kept + ([f"… {more} more"] if more else []))


def checkpoint_dir(state_dir, worktree_key):
    return os.path.join(state_dir, "handoffs", worktree_key)


def prompt_path(state_dir, worktree_key):
    """Where a file-mode runtime's first prompt goes: a bw-owned dir outside every write grant
    (never the lead-writable checkpoint dir, where a planted link would aim bw's write)."""
    return os.path.join(state_dir, "prompts", worktree_key, "first-prompt.md")


def write_first_prompt(state_dir, worktree_key, prompt):
    """Written to a fresh temp name (O_EXCL, no link followed), then renamed over the old file:
    a link standing at the path is replaced, never written through."""
    dest = prompt_path(state_dir, worktree_key)
    d = os.path.dirname(dest)
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = os.path.join(d, f".first-prompt.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(prompt)
        os.replace(tmp, dest)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    return dest


def read_checkpoint(state_dir, worktree_key, limit=64 * 1024):
    """The lead writes this file, so it is opened without following symlinks (a link to a
    secret would otherwise be read by bw and handed to the next model), regular files only."""
    path = os.path.join(checkpoint_dir(state_dir, worktree_key), "checkpoint.md")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # a FIFO must not hang bw
    except OSError:
        return None
    try:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        return os.read(fd, limit).decode("utf-8", errors="replace").strip() or None
    finally:
        os.close(fd)


def build_handoff(worktree, since_head, state_dir, worktree_key, inbox_items):
    lines = ["@next handoff",
             f"branch: {_git(worktree, 'branch', '--show-current') or '(detached)'}",
             "status:", _bounded(_git(worktree, "status", "--short")) or "(clean)"]
    if since_head:
        lines += ["commits since the last lead started:",
                  _bounded(_git(worktree, "log", "--no-ext-diff", "--no-textconv", "--oneline",
                                f"{since_head}..HEAD")) or "(none)",
                  "diff stat:",
                  _bounded(_git(worktree, "diff", "--no-ext-diff", "--no-textconv", "--stat", since_head)) or "(none)"]
    cp = read_checkpoint(state_dir, worktree_key)
    lines += ["checkpoint:", cp] if cp else ["checkpoint: none"]
    if inbox_items:
        section = "\n".join(f"- {i}" for i in inbox_items)
        if len(section) > INBOX_LIMIT:  # the handoff can travel on argv: never unbounded
            section = section[:INBOX_LIMIT - len(INBOX_CUT)] + INBOX_CUT
        lines += ["open questions for the user:", section]
    return "\n".join(lines)


def protocol_text():
    with open(os.path.join(HERE, "protocol", "checkpoint.md")) as f:
        return f.read().strip()


def first_prompt(handoff, protocol):
    return f"{protocol}\n\n{handoff}" if handoff else protocol
