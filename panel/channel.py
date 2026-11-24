"""channel.py — a lead's panel requests arrive as files; bw answers each with a response file.

A contained lead publishes one request as one file in `$BW_PANEL` (the request dir, inside its write
grant): it writes `.tmp-<name>`, then renames it to `<id>.request`. bw ingests and unlinks every
request, and writes the answer to `$BW_PANEL_VIEW/responses/<id>.json` (bw's own, read-only to the lead).

Every request file is hostile input:
- the request dir is opened without following a link at either of its last two levels, so a
  swapped-in symlink never points bw's unlinks or reads somewhere else;
- only `<id>.request` names whose id matches REQ_ID are read; each is opened without following a
  link and without blocking, and at most READ_LIMIT bytes are read;
- a panel id must match `state.PANEL_ID` before it reaches a path, and the claim of an `ask` is one
  claim of at most CLAIM_LIMIT characters with no attachment;
- a request id that already has a response is refused under a new name, and the old response stays;
- every lead-chosen name in a problem line is escaped and cut. `ingest` never raises.
"""
import json
import os
import re
import secrets
import stat
import time
from typing import Optional

from lead import handoff, inbox, paths
from panel import state as pstate

READ_LIMIT = 64 * 1024
REQ_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
CLAIM_LIMIT = 1500
TARGET_LIMIT = 300
FOCUS_LIMIT = 500
PATHS_LIMIT = 20
NONCE_LIMIT = 32
DISPUTE_ID = re.compile(r"[A-Za-z0-9_-]{1,16}")
TYPES = ("panel", "ask", "log", "amend")
ENVELOPE = ("v", "type", "panel", "nonce")
SUFFIX = ".request"
STAKES = ("normal", "high")
NAME_CHARS = 80


def check_ask(seat, claim, dispute) -> None:
    """The claim-only checks for an `ask`: one ValueError each. The CLI path calls this too."""
    if not isinstance(seat, str):
        raise ValueError("seat must be a string")
    if not isinstance(claim, str) or not claim:
        raise ValueError("claim must be a non-empty string")
    if len(claim) > CLAIM_LIMIT:
        raise ValueError(f"claim is over {CLAIM_LIMIT} characters; relay one claim")
    if not isinstance(dispute, str) or not DISPUTE_ID.fullmatch(dispute):
        raise ValueError("dispute must be a dispute id such as d1")


def _repo_relative(p) -> bool:
    if not isinstance(p, str) or not p or "\0" in p or os.path.isabs(p):
        return False
    return os.pardir not in p.replace("\\", "/").split("/")


def _panel_request(req: dict) -> None:
    if not isinstance(req.get("checkpoint"), str):
        raise ValueError("checkpoint must be a string")
    target = req.get("target")
    if not isinstance(target, str) or not 1 <= len(target) <= TARGET_LIMIT:
        raise ValueError(f"target must be a string of 1 to {TARGET_LIMIT} characters")
    focus = req.setdefault("focus", "")
    if not isinstance(focus, str) or len(focus) > FOCUS_LIMIT:
        raise ValueError(f"focus must be a string of at most {FOCUS_LIMIT} characters")
    if req.setdefault("stakes", "normal") not in STAKES:
        raise ValueError("stakes must be normal or high")
    ps = req.setdefault("paths", [])
    if not isinstance(ps, list) or len(ps) > PATHS_LIMIT or not all(_repo_relative(p) for p in ps):
        raise ValueError(f"paths must be a list of at most {PATHS_LIMIT} repo-relative paths")
    if not isinstance(req.setdefault("deep", False), bool):
        raise ValueError("deep must be true or false")


def parse(data: bytes) -> dict:
    """The request as a dict, defaults filled in. ValueError with one message the lead can read."""
    try:
        req = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("request is not UTF-8 JSON") from None
    if not isinstance(req, dict):
        raise ValueError("request must be a JSON object")
    v = req.get("v")
    if not isinstance(v, int) or isinstance(v, bool) or v != 1:
        raise ValueError('request must have "v": 1')
    nonce = req.get("nonce")
    if nonce is not None and (not isinstance(nonce, str) or len(nonce) > NONCE_LIMIT):
        raise ValueError(f"nonce must be a string of at most {NONCE_LIMIT} characters")
    kind = req.get("type")
    if kind not in TYPES:
        raise ValueError("type must be one of " + ", ".join(TYPES))
    if kind == "panel":
        _panel_request(req)
        return req
    pid = req.get("panel")
    if not isinstance(pid, str) or not pstate.PANEL_ID.fullmatch(pid):
        raise ValueError("panel must be a panel id such as p3f9a1c2")
    if kind == "ask":
        check_ask(req.get("seat"), req.get("claim"), req.get("dispute"))
    elif kind == "log":
        if not isinstance(req.get("verdicts"), dict):
            raise ValueError("verdicts must be an object")
    elif not isinstance(req.get("patch"), dict):
        raise ValueError("patch must be an object")
    return req


def _nonce(data: bytes) -> Optional[str]:
    """The request's nonce when it has a valid one, so a refusal can still echo it."""
    try:
        req = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    n = req.get("nonce") if isinstance(req, dict) else None
    return n if isinstance(n, str) and len(n) <= NONCE_LIMIT else None


def open_request_dir(state, key) -> int:
    """The request dir as a descriptor. The checkpoint dir is made with no link followed; the
    `panel` dir in it is made 0700 when absent. A symlink at either level raises OSError."""
    cp_path = handoff.checkpoint_dir(state, pstate._key(key))
    pstate._ensure(state, cp_path)
    cp = os.open(cp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.mkdir("panel", 0o700, dir_fd=cp)
        except FileExistsError:
            pass
        return os.open("panel", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cp)
    finally:
        os.close(cp)


def ensure_request_dir(state, key) -> Optional[str]:
    """Make the request dir at session start. None on success, else one line for a notice."""
    try:
        os.close(open_request_dir(state, key))
        return None
    except (OSError, ValueError) as e:
        return f"the panel request dir cannot be made ({type(e).__name__}); panel requests will not reach bw"


def _responses(state, key) -> str:
    return os.path.join(pstate.view_root(state, key), "responses")


def _error(msg, nonce=None) -> ValueError:
    err = ValueError(msg)
    err.nonce = nonce
    return err


def _refusal(stem) -> ValueError:
    err = ValueError(f"request id {stem} was used before; send the request again with a new id")
    err.reused = True
    return err


def ingest(state, key) -> tuple:
    """([(req_id, request dict or ValueError)], problems). Every taken file is unlinked; a dir is left.
    A ValueError carries `nonce` (the request's, when it had a valid one) and, for a reused id,
    `reused=True`. Never raises: a filesystem error is one problem line."""
    out, problems = [], []
    try:
        dfd = open_request_dir(state, key)
    except (OSError, ValueError) as e:
        return [], [f"the panel request dir cannot be read ({type(e).__name__}); panel requests are not reaching bw"]
    try:
        try:
            names = sorted(n for n in os.listdir(dfd) if n.endswith(SUFFIX) and not n.startswith(".tmp-"))
            responses = _responses(state, key)
        except OSError as e:
            return [], [f"the panel request dir cannot be listed ({type(e).__name__})"]
        for name in names:
            shown = inbox.first_line(name, NAME_CHARS)
            stem = name[:-len(SUFFIX)]
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    problems.append(f"a directory named like a request was left in place: {shown}")
                    continue
                if not REQ_ID.fullmatch(stem):
                    os.unlink(name, dir_fd=dfd)
                    problems.append(f"a request with a bad name was deleted unread: {shown}")
                    continue
                if not stat.S_ISREG(st.st_mode):
                    os.unlink(name, dir_fd=dfd)
                    out.append((stem, _error("not a regular file")))
                    continue
                try:
                    data = inbox.read_at(dfd, name, READ_LIMIT)
                except OSError:
                    data = None  # swapped for a link or a FIFO after the stat
                os.unlink(name, dir_fd=dfd)
                if data is None:
                    out.append((stem, _error("not a regular file")))
                    continue
                if os.path.lexists(os.path.join(responses, f"{stem}.json")):
                    err = _refusal(stem)
                elif len(data) >= READ_LIMIT:
                    err = ValueError("request is over 64 KiB")
                else:
                    try:
                        out.append((stem, parse(data)))
                        continue
                    except ValueError as e:
                        err = e
                err.nonce = _nonce(data) if len(data) < READ_LIMIT else None
                out.append((stem, err))
            except OSError as e:
                problems.append(f"a request was not ingested: {shown} ({type(e).__name__})")
    finally:
        os.close(dfd)
    return out, problems


def respond(state, key, req_id, obj: dict, nonce=None, reused=False) -> str:
    """Write the response atomically to `responses/<req_id>.json`, or, when `reused`, to the first free
    `responses/<req_id>.reused-<n>.json` (the old response stays). Returns the path."""
    if not isinstance(req_id, str) or not REQ_ID.fullmatch(req_id):
        raise ValueError(f"bad request id: {req_id!r}")
    d = pstate._ensure(state, _responses(state, key))
    body = {**obj, "nonce": nonce, "received": time.time()}
    if not reused:
        dest = os.path.join(d, f"{req_id}.json")
        paths.write_json_atomic(dest, body)
        return dest
    tmp = os.path.join(d, f".{req_id}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "x") as f:
            json.dump(body, f)
        n = 1
        while True:
            dest = os.path.join(d, f"{req_id}.reused-{n}.json")
            try:
                os.link(tmp, dest)  # fails when the name is taken: never replaces a response
                return dest
            except FileExistsError:
                n += 1
    finally:
        os.unlink(tmp)

