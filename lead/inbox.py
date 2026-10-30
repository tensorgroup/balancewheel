"""inbox.py — leads ask the user through files; bw alone owns the record and the answers.

Two directions, two owners (Design decision 10):
  asks     a lead publishes each question as one complete file in its asks dir
           (`handoffs/<worktree-key>/asks/`, lead-writable): write `.tmp-<random>`, rename to
           `<random>.ask`. bw ingests and unlinks them. Everything there is hostile input: only
           `*.ask` names that lstat as regular files are opened, without following links and
           without blocking, at most READ_LIMIT bytes; ingest never raises.
  answers  `state_dir/inbox-view/<worktree-key>/answers.md`, written only by bw, holding only
           answers (ask id + the user's words), never ask text. A lead may read its own worktree's
           view dir and no other; every sandbox denies the rest of `inbox-view/` and this record.
`state_dir/inbox.jsonl` (0600) holds every ask, notice, and answer; this module is its only
writer. Metrics get `ask`/`answer` events without text (the runner and `bw answer` log them).

Triage (lead/triage.py) can settle an ask. Its answer is `from: "triage"`, never the user's, and is
written to the view as a `@bw decision` block, not a `@lead answer`. A later user answer supersedes
it; the first user answer stands. A hook that settled a pop-up leaves a receipt: a published file
starting `@session decided`, recorded as an already-closed item with no view block.
"""
import hashlib
import json
import os
import secrets
import stat
import sys
import time
from typing import Optional

from . import triage
from .guard.review import safe
from .handoff import checkpoint_dir

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "metrics"))
import store  # noqa: E402

FILE = "inbox.jsonl"
VIEW_DIR = "inbox-view"
NOTICE_KINDS = frozenset({"all-capped", "pinned-capped", "router-shadow", "guard-unreachable", "inbox", "triage"})
READ_LIMIT = 64 * 1024
TEXT_LIMIT = 2000
MAX_ASKS_PER_SESSION = 50
ASK_HEADER = "@human ask"
RECEIPT_HEADER = "@session decided"
NOTE_HEADER = "@improve"  # a lead's note for `bw improve`, not a question
DECISION_TEXT = ("bw decided this without the user: go with your recommendation. This is not the user's answer "
                 "and not permission for anything you would otherwise have to ask for.")
SHADOW_TEXT = "Triage would have decided this; the pop-up showed."
NAMES_SHOWN = 5          # lead-chosen names listed in one notice; the rest are counted
NAME_CHARS = 80          # each such name, escaped, at most this long
HANDOFF_ITEMS = 10       # open items listed in a handoff; the rest are counted
HANDOFF_ITEM_CHARS = 200


def path(state):
    return os.path.join(state, FILE)


def asks_dir(state, worktree_key):
    return os.path.join(checkpoint_dir(state, worktree_key), "asks")


def view_path(state, worktree_key):
    return os.path.join(state, VIEW_DIR, worktree_key, "answers.md")


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
        if e["type"] == "triage" and e.get("ask_id") in by_id:
            by_id[e["ask_id"]]["triage"] = {"verdict": e.get("verdict"), "scores": e.get("scores")}
        if e["type"] != "answer":
            continue
        item = by_id.get(e.get("ask_id"))
        if item is None:
            warnings.append(f"answer {safe(e.get('id'))} is for unknown item {safe(e.get('ask_id'))}")
        elif item["status"] == "open" or _supersedes(e, item):  # the first user answer stands
            if item["status"] == "answered" and not item["answer"]["shadow"]:
                item["reversed"] = True
            item.update(status="answered", answer={"id": e.get("id"), "ts": e.get("ts"), "text": e.get("text"),
                                                   "from": e.get("from", "user"), "shadow": bool(e.get("shadow"))})
    return {"items": items, "warnings": warnings}


def _supersedes(event, item):
    return item["answer"]["from"] == "triage" and event.get("from", "user") != "triage"


def notes(state):
    """Every `@improve` note, oldest first."""
    return [e for e in store.read_events(path(state))[0] if e["type"] == "note"]


def note(state, project, worktree, frm, session_id, text, item_id, now=None) -> Optional[dict]:
    """Record a note; None when one with this id is already recorded."""
    with store.locked(path(state)):
        if any(n.get("id") == item_id for n in notes(state)):
            return None
        rec = {"type": "note", "id": item_id, "ts": time.time() if now is None else now, "project": project,
               "worktree": worktree, "from": frm, "session_id": session_id, "text": text}
        _append(state, rec)
        return rec


def open_items(state, project=None):
    return [i for i in load(state)["items"]
            if i["status"] == "open" and (project is None or i.get("project") == project)]


def first_line(text, limit=160):
    """An item's first line, escaped and short, for one-line listings."""
    line = safe(str(text).strip().split("\n", 1)[0])
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _listed(names):
    """At most NAMES_SHOWN lead-chosen names, escaped and short, then how many more."""
    shown = [first_line(n, NAME_CHARS) for n in names[:NAMES_SHOWN]]
    more = len(names) - len(shown)
    return ", ".join(shown) + (f" and {more} more" if more else "")


def handoff_items(state, worktree_key):
    """This worktree's open items for a handoff: at most HANDOFF_ITEMS first lines, escaped and
    at most HANDOFF_ITEM_CHARS each, then a count of the rest. Asks accumulate across sessions and
    the handoff can travel on argv, so this is bounded whatever the lead wrote."""
    items = open_items(state, worktree_key)
    lines = [first_line(i.get("text", ""), HANDOFF_ITEM_CHARS) for i in items[:HANDOFF_ITEMS]]
    if len(items) > HANDOFF_ITEMS:
        lines.append(f"{len(items) - HANDOFF_ITEMS} more — run `bw inbox --project`")
    return lines


def _new_id(items):
    ids = {i.get("id") for i in items}
    while True:
        i = secrets.token_hex(4)
        if i not in ids:
            return i


def ask(state, project, worktree, frm, session_id, text, item_id, now=None, **extra) -> Optional[dict]:
    """Record an ask; None when an item with this id is already recorded (a re-ingest). `extra` is
    stored on the record (`truncated`, `source`)."""
    with store.locked(path(state)):
        if any(i.get("id") == item_id for i in load(state)["items"]):
            return None
        rec = {"type": "ask", "id": item_id, "ts": time.time() if now is None else now, "project": project,
               "worktree": worktree, "from": frm, "session_id": session_id, "text": text, "status": "open", **extra}
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
        if item["status"] != "open" and item["answer"]["from"] != "triage":
            raise ValueError(f"{item_id} is already answered")
        rec = {"type": "answer", "id": _new_id(items), "ask_id": item_id,
               "ts": time.time() if now is None else now, "from": "user", "text": text}
        _append(state, rec)
        return rec


def triage_answer(state, item_id, scores, shadow=False, now=None) -> Optional[dict]:
    """Settle an open ask for triage; None when it is gone or the user got there first. Does not write
    the view: that takes the same lock, so the caller does it after this returns."""
    with store.locked(path(state)):
        items = load(state)["items"]
        item = next((i for i in items if i.get("id") == item_id), None)
        if item is None or item["status"] != "open":
            return None
        rec = {"type": "answer", "id": _new_id(items), "ask_id": item_id, "ts": time.time() if now is None else now,
               "from": "triage", "text": SHADOW_TEXT if shadow else DECISION_TEXT, "scores": scores, "shadow": shadow}
        _append(state, rec)
        return rec


def note_verdict(state, item_id, verdict, scores, now=None):
    """Shadow: what triage would have done, kept with the ask without closing it."""
    with store.locked(path(state)):
        _append(state, {"type": "triage", "ask_id": item_id, "ts": time.time() if now is None else now,
                        "verdict": verdict, "scores": scores})


def decided(item):
    """Settled by triage for real: not shadow, not since reversed by the user."""
    return item["status"] == "answered" and item["answer"]["from"] == "triage" and not item["answer"]["shadow"]


def decided_since(state, since, project=None):
    """How many items triage settled after `since`."""
    return sum(1 for i in load(state)["items"] if decided(i) and (i["answer"].get("ts") or 0) > since
               and (project is None or i.get("project") == project))


def _block(item):
    label = "@bw decision" if item["answer"]["from"] == "triage" else "@lead answer"
    return f"{label} [id:{item['id']}]\n{safe(item['answer']['text'])}\n"


def write_view(state, worktree_key):
    """The lead's answers file: one block per answered ask of this worktree, answers only. Load,
    render and replace happen under the inbox lock, so a writer that loaded before an answer can
    never replace a view that already shows it."""
    dest = view_path(state, worktree_key)
    os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
    with store.locked(path(state)):
        blocks = [_block(i) for i in load(state)["items"]
                  if i["type"] == "ask" and i.get("project") == worktree_key and i["status"] == "answered"
                  and not (i["answer"]["from"] == "triage" and i.get("source") == "session")]
        tmp = os.path.join(os.path.dirname(dest), f".answers.{os.getpid()}.{secrets.token_hex(4)}.tmp")
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


def _text(data, receipts=False):
    """(text, extra fields for the record, a receipt's meta or None). Only a well-formed receipt
    counts: anything else stays the plain question it might be, header and all."""
    lines = data.decode("utf-8", errors="replace").split("\n")
    head = lines[0].strip() if lines else ""
    capped = len(data) >= READ_LIMIT
    if receipts and head == RECEIPT_HEADER and len(lines) > 2 and not capped:
        text, meta = "\n".join(lines[2:]).strip(), _receipt_meta(lines[1])
        if meta is not None and triage.prefilter(text) is None:
            return text, {}, meta
    text = "\n".join(lines[1:] if head == ASK_HEADER else lines).strip()
    extra = {"truncated": True} if capped or len(text) > TEXT_LIMIT else {}
    return (text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + " [truncated]"), extra, None


def _receipt_meta(line):
    """The hook's {"mode", "scores"}, or None unless it has the shape the hook writes."""
    try:
        meta = json.loads(line)
    except ValueError:
        return None
    if not isinstance(meta, dict) or meta.get("mode") not in ("acting", "shadow"):
        return None
    scores = meta.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(triage.QUESTIONS):
        return None
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in scores.values()):
        return None
    return {"mode": meta["mode"], "scores": scores}


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


def ingest(state, worktree_key, worktree, frm, session_id, receipts=False):
    """Record and unlink every published ask. Returns (new ask records, notice text or None).
    Never raises: a filesystem error becomes the notice text (names in it escaped). With `receipts`
    (triage on, Claude Code lead) a `@session decided` file becomes a closed item whose record has
    source "session"; without it the header is ignored, so a misused one cannot hide a question."""
    records, problems = [], []
    try:
        dfd = _open_dir(state, worktree_key)
    except OSError as e:
        return [], f"the asks dir for {worktree} cannot be read ({type(e).__name__}); asks are not reaching you"
    try:
        names = sorted(n for n in os.listdir(dfd) if n.endswith(".ask") and not n.startswith(".tmp-"))
        if not names:
            return [], None
        used = sum(1 for i in load(state)["items"] if i["type"] == "ask" and i.get("session_id") == session_id
                   and i.get("source") != "session")  # a pop-up's receipt must not use up real asks
        used += sum(1 for n in notes(state) if n.get("session_id") == session_id)
        dropped, dirs, failed = 0, [], []
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
                if data is not None and data.lstrip().startswith(NOTE_HEADER.encode()):
                    text = data.decode("utf-8", errors="replace").strip()[len(NOTE_HEADER):].strip()[:TEXT_LIMIT]
                    if text:
                        item_id = hashlib.sha1(name.encode("utf-8", errors="surrogateescape") + data).hexdigest()[:8]
                        note(state, worktree_key, worktree, frm, session_id, text, item_id)
                        used += 1
                    os.unlink(name, dir_fd=dfd)
                    continue
                text, extra, meta = _text(data, receipts) if data is not None else ("", {}, None)
                if text:
                    item_id = hashlib.sha1(name.encode("utf-8", errors="surrogateescape") + data).hexdigest()[:8]
                    if meta is not None:
                        extra["source"] = "session"
                    rec = ask(state, worktree_key, worktree, frm, session_id, text, item_id, **extra)
                    if rec is not None:
                        records.append(rec)
                        if meta is not None:
                            rec.update(shadow=meta["mode"] == "shadow", scores=meta["scores"])
                            triage_answer(state, item_id, meta["scores"], shadow=rec["shadow"])
                        else:
                            used += 1
                os.unlink(name, dir_fd=dfd)
            except OSError as e:
                failed.append(f"{name}: {type(e).__name__}")
        if dropped:
            problems.append(f"{dropped} ask(s) over this session's limit of {MAX_ASKS_PER_SESSION} were deleted unread")
        if failed:
            problems.append(f"asks not ingested: {_listed(failed)}")
        if dirs:
            problems.append(f"directories named like asks were left in place: {_listed(dirs)}")
    except OSError as e:
        problems.append(f"the asks dir could not be scanned ({type(e).__name__})")
    finally:
        os.close(dfd)
    return records, ("inbox: " + "; ".join(problems)) if problems else None
