"""inbox.py — leads ask the user through files; bw alone owns the record and the answers.

Two directions, two owners (Design decision 10):
  asks     a lead publishes each question as one complete file in its asks dir
           (`handoffs/<worktree-key>/asks/`, lead-writable): write `.tmp-<random>`, rename to
           `<random>.ask`. bw ingests and unlinks them. Everything there is hostile input: only
           `*.ask` names that lstat as regular files are opened, without following links and
           without blocking, at most READ_LIMIT bytes; ingest never raises.
  answers  `state_dir/inbox-view/<worktree-key>.md`, written only by bw, read-only to the lead,
           holding only answers (ask id + the user's words), never ask text.
`state_dir/inbox.jsonl` (0600) holds every ask, notice, and answer; this module is its only
writer. Metrics get `ask`/`answer` events without text (the runner and `bw answer` log them).
"""
import hashlib
import os
import secrets
import stat
import sys
import time
from typing import Optional

from .guard.review import safe
from .handoff import checkpoint_dir

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "metrics"))
import store  # noqa: E402

FILE = "inbox.jsonl"
VIEW_DIR = "inbox-view"
NOTICE_KINDS = frozenset({"all-capped", "pinned-capped", "router-shadow", "guard-unreachable", "inbox"})
READ_LIMIT = 64 * 1024
TEXT_LIMIT = 2000
MAX_ASKS_PER_SESSION = 50
ASK_HEADER = "@human ask"


def path(state):
    return os.path.join(state, FILE)


def asks_dir(state, worktree_key):
    return os.path.join(checkpoint_dir(state, worktree_key), "asks")


def view_path(state, worktree_key):
    return os.path.join(state, VIEW_DIR, f"{worktree_key}.md")


def _append(state, event):
    """Created 0600 before the first append (store.append alone would create it at the umask)."""
    p = path(state)
    os.makedirs(state, exist_ok=True)
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    store.append(p, event)


def load(state):
    """{"items": asks and notices in order, each with `status` ("open"/"answered") and, once
    answered, `answer`; "warnings"}."""
    events, warnings = store.read_events(path(state))
    items, by_id = [], {}
    for e in events:
        if e["type"] in ("ask", "notice"):
            item = dict(e, status="open")
            items.append(item)
            by_id.setdefault(item.get("id"), item)
    for e in events:
        if e["type"] != "answer":
            continue
        item = by_id.get(e.get("ask_id"))
        if item is None:
            warnings.append(f"answer {safe(e.get('id'))} is for unknown item {safe(e.get('ask_id'))}")
        elif item["status"] == "open":  # the first answer stands
            item.update(status="answered", answer={"id": e.get("id"), "ts": e.get("ts"), "text": e.get("text")})
    return {"items": items, "warnings": warnings}


def open_items(state, project=None):
    return [i for i in load(state)["items"]
            if i["status"] == "open" and (project is None or i.get("project") == project)]


def first_line(text, limit=160):
    """An item's first line, escaped and short, for one-line listings."""
    line = safe(str(text).strip().split("\n", 1)[0])
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _new_id(items):
    ids = {i.get("id") for i in items}
    while True:
        i = secrets.token_hex(4)
        if i not in ids:
            return i


def ask(state, project, worktree, frm, session_id, text, item_id, now=None) -> Optional[dict]:
    """Record an ask; None when an item with this id is already recorded (a re-ingest)."""
    with store.locked(path(state)):
        if any(i.get("id") == item_id for i in load(state)["items"]):
            return None
        rec = {"type": "ask", "id": item_id, "ts": time.time() if now is None else now, "project": project,
               "worktree": worktree, "from": frm, "session_id": session_id, "text": text, "status": "open"}
        _append(state, rec)
        return rec


def notice(state, project, kind, text, now=None) -> Optional[dict]:
    """Record a notice from bw; None when one of this kind is already open for the project."""
    if kind not in NOTICE_KINDS:
        raise ValueError(f"unknown notice kind {kind!r}")
    with store.locked(path(state)):
        items = load(state)["items"]
        if any(i["type"] == "notice" and i.get("kind") == kind and i.get("project") == project
               and i["status"] == "open" for i in items):
            return None
        rec = {"type": "notice", "id": _new_id(items), "ts": time.time() if now is None else now,
               "project": project, "kind": kind, "from": "bw", "text": text, "status": "open"}
        _append(state, rec)
        return rec


def answer(state, item_id, text, now=None) -> dict:
    """Answer an ask or close a notice. KeyError: no such item; ValueError: already answered."""
    with store.locked(path(state)):
        items = load(state)["items"]
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None:
            raise KeyError(item_id)
        if item["status"] != "open":
            raise ValueError(f"{item_id} is already answered")
        rec = {"type": "answer", "id": _new_id(items), "ask_id": item_id,
               "ts": time.time() if now is None else now, "from": "user", "text": text}
        _append(state, rec)
        return rec


def write_view(state, worktree_key):
    """The lead's answers file: one block per answered ask of this worktree, answers only."""
    blocks = [f"@lead answer [id:{i['id']}]\n{safe(i['answer']['text'])}\n"
              for i in load(state)["items"]
              if i["type"] == "ask" and i.get("project") == worktree_key and i["status"] == "answered"]
    dest = view_path(state, worktree_key)
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    tmp = os.path.join(os.path.dirname(dest), f".{worktree_key}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "w") as f:
            f.write("\n".join(blocks))
        os.replace(tmp, dest)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def _read(dfd, name):
    """At most READ_LIMIT bytes of a regular file, never following a link or blocking on a FIFO."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        chunks, n = [], 0
        while n < READ_LIMIT:
            b = os.read(fd, READ_LIMIT - n)
            if not b:
                break
            chunks.append(b)
            n += len(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _text(data):
    lines = data.decode("utf-8", errors="replace").split("\n")
    if lines and lines[0].strip() == ASK_HEADER:
        lines = lines[1:]
    text = "\n".join(lines).strip()
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + " [truncated]"


def _open_dir(state, worktree_key):
    """The asks dir as a descriptor, opened without following a link at either of its last two
    levels, so a swapped-in symlink can never point bw's unlinks somewhere else."""
    cp = os.open(checkpoint_dir(state, worktree_key), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir("asks", 0o700, dir_fd=cp)
        except FileExistsError:
            pass
        return os.open("asks", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cp)
    finally:
        os.close(cp)


def ingest(state, worktree_key, worktree, frm, session_id):
    """Record and unlink every published ask. Returns (new ask records, notice text or None).
    Never raises: a filesystem error becomes the notice text (names in it escaped)."""
    records, problems = [], []
    try:
        dfd = _open_dir(state, worktree_key)
    except OSError as e:
        return [], f"the asks dir for {worktree} cannot be read ({type(e).__name__}); asks are not reaching you"
    try:
        names = sorted(n for n in os.listdir(dfd) if n.endswith(".ask") and not n.startswith(".tmp-"))
        if not names:
            return [], None
        used = sum(1 for i in load(state)["items"] if i["type"] == "ask" and i.get("session_id") == session_id)
        dropped, dirs = 0, []
        for name in names:
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    dirs.append(name)
                    continue
                if not stat.S_ISREG(st.st_mode):
                    os.unlink(name, dir_fd=dfd)
                    continue
                if used >= MAX_ASKS_PER_SESSION:
                    os.unlink(name, dir_fd=dfd)
                    dropped += 1
                    continue
                data = _read(dfd, name)
                text = _text(data) if data is not None else ""
                if text:
                    item_id = hashlib.sha1(name.encode("utf-8", errors="surrogateescape") + data).hexdigest()[:8]
                    rec = ask(state, worktree_key, worktree, frm, session_id, text, item_id)
                    if rec is not None:
                        records.append(rec)
                        used += 1
                os.unlink(name, dir_fd=dfd)
            except OSError as e:
                problems.append(f"{safe(name)}: {type(e).__name__}")
        if dropped:
            problems.append(f"{dropped} ask(s) over this session's limit of {MAX_ASKS_PER_SESSION} were deleted unread")
        if dirs:
            problems.append(f"directories named like asks were left in place: {', '.join(safe(d) for d in dirs)}")
    except OSError as e:
        problems.append(f"the asks dir could not be scanned ({type(e).__name__})")
    finally:
        os.close(dfd)
    return records, ("inbox: " + "; ".join(problems)) if problems else None
