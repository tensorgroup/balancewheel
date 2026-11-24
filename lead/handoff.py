"""handoff.py — what the next lead is told when it takes over a worktree.

Built from facts bw can read, not from transcripts: branch, status, commits since the outgoing
session began, diff stat, the lead's last checkpoint, and open inbox items. bw reads git
OUTSIDE the sandbox here, and the outgoing lead could write the repo, so git runs with hooks,
fsmonitor, external diff drivers, textconv, filter drivers, and optional locks all off, never
enters an embedded repo (a gitlink, whose own config and attributes would run code), and has its
GIT_* variables cleared: nothing a lead planted in the repo runs at handoff time.
"""
import os
import shlex
import string
import subprocess
from typing import Optional

HERE = os.path.dirname(os.path.realpath(__file__))
OPEN_PANELS = "open panels (finish and log them):"
# The checkpoint roles in the order the protocol lists them (metrics/config.py ROLES is a set).
CHECKPOINT_ORDER = ("design", "plan", "review", "debug", "decision")
DEEP_LINE = '- Set "stakes" to "high" for a risky change: bw then runs a deep panel.'
SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
        "-c", "core.attributesFile=/dev/null", "-c", "diff.ignoreSubmodules=all", "-c", "submodule.recurse=false",
        "-c", "status.submoduleSummary=false", "-c", "diff.submodule=short", "--no-pager"]
# Commands that look inside a gitlink unless told not to: each gets --ignore-submodules=all.
SUBMODULE_COMMANDS = ("diff", "status")
CONFIG_UNREADABLE = 125  # _git_rc's status when the repo's filter config cannot be read
GIT_TIMEOUT = 5  # seconds: a hung git call must not hold a broker worker or bw
INBOX_LIMIT = 16 * 1024  # characters of open inbox items in one handoff
INBOX_CUT = "\n[cut — run `bw inbox --project`]"
SECTION_LINES = 200       # git status / log / diff --stat lines kept per section
SECTION_BYTES = 16 * 1024  # and bytes: the handoff can travel on argv (E2BIG mid-switch otherwise)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # attributes read from here: no filters run


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_ATTR_SOURCE"] = EMPTY_TREE  # git >= 2.42: ignore the worktree's .gitattributes (clean/smudge filters)
    return env


def _filter_off(worktree, env):
    """Env entries (GIT_CONFIG_COUNT, GIT_CONFIG_KEY_n, GIT_CONFIG_VALUE_n) that blank every filter
    driver the repo config names, or None when the config cannot be read (a timeout, or a status
    other than 0, or 1 for no match): the caller then refuses to run git.

    .git/info/attributes is still read, and a worktree diff or status runs the clean filter of a
    driver it names. Reading config runs nothing. An empty value disables the driver. The env form
    keeps a name with '=' or a space whole; `-c` splits at the first '='."""
    try:
        r = subprocess.run(["git", "-C", worktree, *SAFE, "config", "-z", "--get-regexp", r"^filter\."],
                           capture_output=True, env=env, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode not in (0, 1):
        return None
    names = set()
    for record in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
        key = record.split("\n", 1)[0]  # -z: the key, a newline, the value
        if key.startswith("filter.") and key.count(".") >= 2:
            names.add(key[len("filter."):].rsplit(".", 1)[0])
    pairs = [(f"filter.{n}.{part}", val) for n in sorted(names)
             for part, val in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false"))]
    out = {"GIT_CONFIG_COUNT": str(len(pairs))}
    for i, (k, v) in enumerate(pairs):
        out[f"GIT_CONFIG_KEY_{i}"], out[f"GIT_CONFIG_VALUE_{i}"] = k, v
    return out


def _git_rc(worktree, *args):
    """(returncode, stdout, stderr), run the safe way: nothing the lead planted in the repo runs.
    CONFIG_UNREADABLE, and no git call, when the filter config cannot be read."""
    env = _env()
    off = _filter_off(worktree, env)
    if off is None:
        return CONFIG_UNREADABLE, "", "git config could not be read; git not run"
    env.update(off)
    args = list(args)
    if args and args[0] in SUBMODULE_COMMANDS:
        args.insert(1, "--ignore-submodules=all")
    try:
        r = subprocess.run(["git", "-C", worktree, *SAFE, *args],
                           capture_output=True, env=env, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "", f"git {args[0]} timed out after {GIT_TIMEOUT}s"
    # not text=True: a non-UTF-8 diff must not raise
    return (r.returncode, r.stdout.decode("utf-8", "replace").strip(),
            r.stderr.decode("utf-8", "replace").strip())


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


def build_handoff(worktree, since_head, state_dir, worktree_key, inbox_items, open_panels=()):
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
    panels = open_panels_section(open_panels)
    if panels:
        lines.append(panels)
    return "\n".join(lines)


def open_panels_section(lines) -> Optional[str]:
    """The handoff's open-panels section (lines from `panel.service.open_lines`); None for no lines.
    A fresh `bw lead` uses it alone as the handoff, so every new lead learns of open panels."""
    if not lines:
        return None
    return "\n".join([OPEN_PANELS] + [f"- {ln}" for ln in lines])


def _checkpoint_line(name, spec):
    seats = ", ".join(spec["seats"])
    least = spec.get("min_seats", min(2, len(spec["seats"])))
    required = spec.get("required") or []
    rule = f"required: {', '.join(required)}; at least {least}" if required else f"at least {least}"
    return f"- {name}: {seats} ({rule})"


def _read_protocol(name):
    with open(os.path.join(HERE, "protocol", name)) as f:
        return f.read().strip()


def protocol_text(config=None, profile=None, request_dir=None, view_root=None, wait_path=None):
    """checkpoint.md, then, when `profile` has checkpoints, panel.md with literal paths (Design
    decision 7). Paths in shell commands are shell-quoted; the file-tool paths stay plain."""
    base = _read_protocol("checkpoint.md")
    prof = ((config or {}).get("profiles") or {}).get(profile) or {}
    cps = prof.get("checkpoints") or {}
    if not cps:
        return base
    if request_dir is None or view_root is None:
        raise ValueError("the panel protocol needs the request dir and the view root")
    wait_path = wait_path or os.path.join(view_root, "bw-panel-wait")
    deep = DEEP_LINE if prof.get("depth") == "deep-on-stakes" else ""
    # A $deep line with nothing to say is dropped, not left empty.
    tpl = "\n".join(ln for ln in _read_protocol("panel.md").split("\n") if ln != "$deep" or deep)
    panel = string.Template(tpl).substitute(
        checkpoints="\n".join(_checkpoint_line(c, cps[c]) for c in CHECKPOINT_ORDER if c in cps),
        deep=deep, request_dir=request_dir, view_root=view_root,
        request_dir_sh=shlex.quote(request_dir), wait_sh=shlex.quote(wait_path))
    return f"{base}\n\n{panel}"


def first_prompt(handoff, protocol):
    return f"{protocol}\n\n{handoff}" if handoff else protocol
