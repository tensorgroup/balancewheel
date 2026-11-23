"""The shared brief a seat reads at the start of a new session.

bw builds it outside the sandbox, from a worktree that a lead could write. So every git call
runs the safe way (`lead.handoff._git_rc`) and adds --no-ext-diff --no-textconv, and every file
opens with O_NOFOLLOW as a regular file. The brief is cached by a key of everything it shows.
"""
import hashlib
import os
import stat

from lead.handoff import _git_rc

HEAD_LINES = 80
DIFF_LINES = 3000
DIFF_BYTES = 256 * 1024
CALLERS_PER_FILE = 5
MIN_STEM = 4
GENERIC_STEMS = {"index", "utils", "util", "types", "init", "__init__", "main",
                 "config", "common", "helpers", "setup", "test", "tests"}
FILE_BYTES = 64 * 1024
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
READ_NEXT = ("docs/architecture.md", "ARCHITECTURE.md", "README.md")
NO_BASE = "base unknown; diff against HEAD"
DIFF_FLAGS = ("--no-ext-diff", "--no-textconv")


def _out(target, *args):
    rc, out, _ = _git_rc(target, *args)
    return out if rc == 0 else None


def _run(target, *args):
    """(ok, text): stdout when git succeeds, else its error line."""
    rc, out, err = _git_rc(target, *args)
    return (True, out) if rc == 0 else (False, f"(git {args[0]} failed: {err[:200]})")


def resolve_base(target):
    """merge-base(first of origin/HEAD's target, main, master; HEAD), or None."""
    candidates = []
    ref = _out(target, "symbolic-ref", "-q", "refs/remotes/origin/HEAD")
    if ref:
        candidates.append(ref)
    candidates += ["refs/heads/main", "refs/heads/master"]
    for cand in candidates:
        if _out(target, "rev-parse", "--verify", "-q", cand + "^{commit}") is None:
            continue
        base = _out(target, "merge-base", cand, "HEAD")
        if base:
            return base
    return None


def safe_paths(target, paths):
    """Each path relative to `target`; ValueError names the first that resolves outside it."""
    root = os.path.realpath(target)
    result = []
    for p in paths:
        if any(ord(c) < 32 or ord(c) == 127 for c in p):
            raise ValueError(f"control character in path: {p!r}")
        real = os.path.realpath(os.path.join(root, p))
        if real != root and not real.startswith(root + os.sep):
            raise ValueError(f"path outside the target: {p}")
        rel = os.path.relpath(real, root)
        if rel == ".":
            raise ValueError(f"path is the target itself: {p}")
        result.append(rel)
    return result


def _read_file(path):
    """Text of a regular file (no symlink), up to FILE_BYTES; None otherwise."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)  # NONBLOCK: a FIFO must not hang the open
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read(FILE_BYTES).decode("utf-8", "replace")
    finally:
        os.close(fd)


def _first_existing(target, names):
    for name in names:
        if _read_file(os.path.join(target, name)) is not None:
            return name
    return None


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _cap_diff(diff):
    lines = diff.split("\n")
    cut = None
    if len(lines) > DIFF_LINES:
        lines, cut = lines[:DIFF_LINES], DIFF_LINES
    text = "\n".join(lines)
    raw = text.encode("utf-8", "replace")
    if len(raw) > DIFF_BYTES:
        text = raw[:DIFF_BYTES].decode("utf-8", "replace")
        cut = len(text.split("\n"))
    if cut is not None:
        text += f"\n[cut at {cut} lines — open the rest from the stat]"
    return text


def _callers(target, changed):
    lines, seen = [], set()
    for name in changed:
        stem = os.path.splitext(os.path.basename(name))[0]
        if len(stem) < MIN_STEM or stem.lower() in GENERIC_STEMS or stem in seen:
            continue
        seen.add(stem)
        hits = (_out(target, "grep", "-l", "-z", "-w", "-e", stem) or "").split("\0")
        hits = [h for h in hits if h and h != name][:CALLERS_PER_FILE]
        if hits:
            lines.append(f"- {stem}: " + ", ".join(hits))
    return lines


def build(state, target, paths=()):
    """Path of the cached brief for this target, built if absent.

    A brief with a failed git call is written beside the cache, not in it, so the next call retries."""
    paths = sorted(safe_paths(target, paths))
    base = resolve_base(target)
    head = _out(target, "rev-parse", "--verify", "-q", "HEAD^{commit}")
    ref = base or head
    failed = []

    def section(empty, *args):
        if not ref and args[0] == "diff":
            return empty
        ok, text = _run(target, *args)
        if not ok:
            failed.append(text)
            return text
        return text or empty

    diff = section("", "diff", *DIFF_FLAGS, *([ref] if ref else []))
    untracked = section("", "ls-files", "-z", "--others", "--exclude-standard").replace("\0", "\n").strip()
    key = hashlib.sha256("\n".join([
        os.path.realpath(target), base or "none", head or "unborn", _sha(diff), _sha(untracked),
        "\0".join(paths)]).encode()).hexdigest()[:16]
    out_dir = os.path.join(state, "seats", "briefs")
    path = os.path.join(out_dir, key + ".md")
    if os.path.isfile(path):
        return path

    parts = []
    name = _first_existing(target, INSTRUCTION_FILES)
    if name:
        body = _read_file(os.path.join(target, name)) or ""
        parts.append(f"## Instructions (head of {name})\n\n" + "\n".join(body.split("\n")[:HEAD_LINES]))
    pointer = _first_existing(target, READ_NEXT)
    if pointer:
        parts.append(f"## Read next\n\n- {pointer}")
    if paths:
        parts.append("## Start with\n\n" + "\n".join(f"- {p}" for p in paths))
    parts.append("## Base\n\n" + (base or NO_BASE))
    parts.append("## Diff stat\n\n" + (section("(no changes)", "diff", *DIFF_FLAGS, "--stat", ref) if ref else "(no changes)"))
    parts.append("## Diff\n\n" + (_cap_diff(diff) if diff and not diff.startswith("(git ") else (diff or "(no changes)")))
    parts.append("## Untracked files\n\n" + (untracked or "(none)"))
    changed = section("", "diff", *DIFF_FLAGS, "-z", "--name-only", ref) if ref else ""
    names = [c for c in changed.split("\0") if c] if not changed.startswith("(git ") else []
    callers = _callers(target, names)
    parts.append("## Direct callers (a starting point from git grep, not a call graph)\n\n"
                 + ("\n".join(callers) if callers else "(none)"))

    if failed:
        path = os.path.join(out_dir, key + ".failed.md")  # not a cache hit: the next call retries
    os.makedirs(out_dir, mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    os.replace(tmp, path)
    return path
