"""grants.py — the review-seat grants: what a seat may write and read, on top of lead.sandbox.

The seat-only rules live here, not in lead/sandbox.py, so a seat change never resets a lead key
(the lead key hashes lead/sandbox.py; the seat key hashes this file). The profile builders and the
shared helpers (SECRET_READS, git_config_reads, check_path) stay in lead/sandbox.py.
"""
import os
import shutil
import stat
import sys

from lead.sandbox import (Grants, SECRET_READS, _inside, _real, check_path, git_config_reads,
                          refuse_overlap)

# Language toolchains under the home that a seat reads, so it can run the repo's tools: a version
# manager's shims load their interpreters from here, and nono denies reads under the home by default.
# A toolchain dir holds code, not secrets. Rules: read only (no write rule covers one); only dirs that
# exist, at their real paths, plus the link node of a symlinked one; a secret path inside one stays
# denied on both backends (SECRET_READS wins). A name that resolves into a secret path, holds the home,
# or overlaps bw's paths, the seat's writes, or a lead session store is not granted.
SEAT_TOOLCHAIN_READS = [".pyenv", ".volta", ".nvm", ".rbenv", ".cargo", ".rustup", ".local/bin", ".local/lib"]
# Session stores of the lead runtimes (home-relative): a seat never reads a lead's transcripts.
LEAD_SESSION_STORES = [".claude/projects", ".codex/sessions", ".pi/agent/sessions", ".kimi-code/sessions",
                       ".gemini/tmp", ".gemini/antigravity-cli/conversations",
                       ".gemini/antigravity-cli/conversation_summaries.db", ".gemini/antigravity-cli/history.jsonl"]
# The env a seat gets is an allowlist: these names, every LC_* name, and the adapter's env_keys.
SEAT_ENV_ALLOW = ("PATH", "HOME", "TMPDIR", "LANG", "TERM")
SEAT_ENV_PREFIX = "LC_"
_CS_DARWIN_USER_TEMP_DIR = 65537  # <unistd.h>; some Pythons do not name it in os.confstr_names


def seat_env(environ, keep=()):
    """Only the allowed keys, the LC_* keys, and the names in `keep` (the adapter's env_keys).
    Every other key is dropped: ORCA_*, BW_*, and GIT_* included."""
    keep = set(keep)
    return {k: v for k, v in environ.items()
            if k in SEAT_ENV_ALLOW or k.startswith(SEAT_ENV_PREFIX) or k in keep}


def _system_temp_root():
    """The per-user temp dir on macOS, else /tmp. TMPDIR is never read: the caller may have set it."""
    name = os.confstr_names.get("CS_DARWIN_USER_TEMP_DIR",
                                _CS_DARWIN_USER_TEMP_DIR if sys.platform == "darwin" else None)
    if name is not None:
        try:
            root = os.confstr(name)
        except (ValueError, OSError):
            root = None
        if root:
            return root
    return "/tmp"


def seat_scratch_root():
    """<system temp root>/bw-seats, made 0700 and owned by this user, as a real path. Every seat
    scratch dir lies under it, and every seat profile denies reads of it (each re-reads its own)."""
    path = os.path.join(os.path.realpath(_system_temp_root()), "bw-seats")
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise ValueError(f"refusing a seat scratch root that is not this user's own directory: {path}")
    os.chmod(path, 0o700)
    return check_path(path)


def _tool_read_ok(real, user_home, forbidden):
    """False when a tool read's real path lies inside a secret path, holds the home, or overlaps one
    of `forbidden` (bw's paths, the seat's writes, the lead session stores)."""
    secrets = [os.path.realpath(os.path.join(user_home, s)) for s in SECRET_READS]
    if _inside(os.path.realpath(user_home), [real]) or _inside(real, secrets):
        return False
    return not any(_inside(real, [f]) or _inside(f, [real]) for f in forbidden)


def _toolchain_reads(user_home, forbidden):
    """The SEAT_TOOLCHAIN_READS dirs that exist, as real paths, plus each symlinked one's link node.
    A dir is dropped when `_tool_read_ok` refuses its real path."""
    out = []
    for rel in SEAT_TOOLCHAIN_READS:
        given = os.path.join(user_home, rel)
        if not os.path.isdir(given):
            continue
        real = _real(given)
        if not _tool_read_ok(real, user_home, forbidden):
            continue
        out.append(real)
        if os.path.islink(given):
            out.append(check_path(os.path.abspath(given)))
    return out


def _link_nodes(path):
    """Each symlink met while resolving `path` (a link in a middle component included), as absolute
    link-node paths, in order."""
    nodes, todo, seen = [], os.path.abspath(path), set()
    while todo not in seen:
        seen.add(todo)
        parts, cur = todo.split("/")[1:], "/"
        for i, part in enumerate(parts):
            cur = os.path.join(cur, part)
            if os.path.islink(cur):
                nodes.append(cur)
                target = os.path.join(os.path.dirname(cur), os.readlink(cur))
                todo = os.path.normpath(os.path.join(target, *parts[i + 1:]))
                break
        else:
            return nodes
    return nodes


def _install_reads(cmds, user_home, forbidden):
    """For each command on PATH that is a symlink: its install root, as a real path, plus each link
    node on the way. The root is the real binary's dir, or that dir's parent when it is named `bin`
    (a package keeps its resources next to `bin`). A plain file on PATH adds nothing. A root that
    `_tool_read_ok` refuses is dropped, with its link nodes."""
    out = []
    for cmd in cmds:
        found = shutil.which(cmd)
        if not found:
            continue
        nodes = _link_nodes(found)
        if not nodes:
            continue
        root = os.path.dirname(os.path.realpath(found))
        if os.path.basename(root) == "bin":
            root = os.path.dirname(root)
        root = _real(root)
        if not _tool_read_ok(root, user_home, forbidden):
            continue
        out += [root] + [check_path(n) for n in nodes]
    return out


def _regular_file(f):
    given = os.path.abspath(os.path.expanduser(f))
    if os.path.islink(given) or not os.path.isfile(given):
        raise ValueError(f"refusing a seat write file that is not a regular file: {given}")
    return _real(given)


def _state_dir(d, user_home, bw_paths, code):
    """The real path of a declared state dir, or ValueError. It must be a real dir strictly under the
    user's home, overlap none of `bw_paths` (bw state, seat scratch, the target, the seat home), lie
    under no code path, and hold or lie under no secret path."""
    given = os.path.abspath(os.path.expanduser(d))
    if os.path.islink(given) or not os.path.isdir(given):
        raise ValueError(f"refusing a seat state dir that is not a real directory: {given}")
    real, home = _real(given), os.path.realpath(user_home)
    if real == home or not _inside(real, [home]):
        raise ValueError(f"refusing a seat state dir that is not strictly under the home {home}: {real}")
    refuse_overlap(real, bw_paths)
    if _inside(real, code):
        raise ValueError(f"refusing a seat state dir under a code path: {real}")
    for s in SECRET_READS:
        secret = os.path.realpath(os.path.join(user_home, s))
        if _inside(secret, [real]) or _inside(real, [secret]):
            raise ValueError(f"refusing a seat state dir that overlaps the secret path {secret}: {real}")
    return real


def seat_grants(target, seat_home, runtime_grants, state_dir, scratch, write_files=(), credentials=(), state_dirs=(),
                installs=()):
    """Ordered write rules and read grants for a review seat on `target` (a lead.projects.Project).
    The seat writes only its own home (minus the runtime's code-loading names) and the credential
    files it refreshes. It reads the target, its git dir, and the user's global git config, and nothing
    of bw's state, of other seats' scratch dirs, or of the lead runtimes' session stores.
    `credentials`: the real login files the adapter declares as refreshing (ruling C-cred). Each one
    is readable and writable at its real path as one literal file, even under a secret read deny.
    Pass only the adapter's declared credentials here, never a generic write file.
    `state_dirs`: the CLI's own state dirs under the user's home, for a runtime that cannot run on a
    private home (agy: ruling S-agy). Each one is writable as a subpath; the code names in
    `runtime_grants.deny_write` stay read-only inside it. Pass only the adapter's declared dirs.
    `installs`: the commands whose install a seat reads, read only, as tool reads (`_install_reads`):
    a CLI linked into PATH from a package dir under the home cannot start when nono denies that dir."""
    state, home, own_scratch = _real(state_dir), _real(seat_home), _real(scratch)
    scratch_root = seat_scratch_root()
    if not own_scratch.startswith(scratch_root + "/"):
        raise ValueError(f"refusing a seat scratch dir outside {scratch_root}: {own_scratch}")
    writes = [_real(p) for p in runtime_grants.write]
    for w in writes:
        if not _inside(w, [home]):
            raise ValueError(f"refusing: seat write grant {w} lies outside the seat home {home}")
    # The target is read-only: it overlaps neither bw's state nor anything the seat writes.
    for t in (target.worktree, target.common_dir):
        refuse_overlap(t, [state, home, own_scratch] + writes)
    # A secret under the target would be readable: the profile skips secret denies inside read grants.
    user_home = os.path.expanduser("~")
    for s in SECRET_READS:
        secret = os.path.realpath(os.path.join(user_home, s))
        if _inside(secret, [_real(target.worktree), _real(target.common_dir)]):
            raise ValueError(f"refusing a seat target that contains the secret path {secret}")
    code = [_real(p) for p in runtime_grants.deny_write]
    bw_paths = [state, scratch_root, _real(target.worktree), _real(target.common_dir)]
    dirs = [_state_dir(d, user_home, bw_paths + [home], code) for d in state_dirs]
    rules = [("allow", "subpath", p) for p in [home] + writes + dirs]
    rules += [("deny", "subpath", p) for p in code]  # after the allows: a later rule wins
    for f in write_files:
        real = _regular_file(f)
        if not _inside(real, [home]) or _inside(real, code):
            raise ValueError(f"refusing a seat write file outside the seat home or under a code path: {real}")
        rules.append(("allow", "literal", real))
    creds = []
    forbidden = bw_paths + code
    for f in credentials:
        real = _regular_file(f)
        if _inside(real, forbidden):
            raise ValueError(f"refusing a seat credential in bw state, the target, a code path, or seat scratch: {real}")
        rules.append(("allow", "literal", real))
        creds.append(real)
    reads = ([_real(target.worktree), _real(target.common_dir)] + [_real(p) for p in runtime_grants.read]
             + git_config_reads())  # a review seat runs git log and git diff
    # Denied whether or not a store exists yet: a lead may create it while the seat runs.
    stores = [os.path.realpath(os.path.join(user_home, s)) for s in LEAD_SESSION_STORES]
    stores = [check_path(p) for p in stores if not _inside(p, [home] + writes + dirs)]
    denied = [state, scratch_root] + stores
    forbidden_reads = bw_paths + [home] + writes + dirs + stores
    tools = _toolchain_reads(user_home, forbidden_reads)
    tools += [p for p in _install_reads(installs, user_home, forbidden_reads) if p not in tools]
    # A credential inside a secret dir is read again after the secret deny.
    return Grants(rules, reads, denied, [home, own_scratch] + creds, tools)
