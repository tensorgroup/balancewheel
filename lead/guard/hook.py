#!/usr/bin/env python3
"""hook.py — agy's guard client: forward the hook payload to the broker, answer what it says.

Thin on purpose. Normalisation and every decision live in the broker. This process has mode
logic only for failure: an unreachable broker, an error or malformed reply, or no reply within
the deadline is "allow plus a marker line" in shadow and "deny" in acting. It is inert (no
opinion) only when BW_GUARD_MODE is unset; BW_GUARD_MODE set with the socket or token missing
is a failure. Python 3.9+ standard library only. bw copies this file to a dir no lead can write.
"""
import json
import os
import socket
import sys
import time

# agy's real no-opinion answer is confirmed by the agy spike; "{}" is provisional until then.
INERT_ANSWER = "{}"
DEADLINE = 2.0
MAX_REQUEST = 1024 * 1024  # a request line over this is replaced by a stub
RUNTIME = "agy"
TO_AGY = {"allow": "allow", "ask": "ask", "block": "deny"}  # the answer shape is provisional the same way


def drain():
    """Read all of stdin so the writer never sees a closed pipe, whatever happens next."""
    chunks = []
    try:
        while True:
            c = os.read(0, 1 << 20)
            if not c:
                break
            chunks.append(c)
    except OSError:
        pass
    return b"".join(chunks)


def write_marker(path, mode, why):
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError:
        return
    try:
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "runtime": RUNTIME,
                           "mode": mode, "why": why}) + "\n"
        os.write(fd, line.encode())
    except OSError:
        pass
    finally:
        os.close(fd)


def tool_name(payload):
    if isinstance(payload, dict):
        t = payload.get("toolName") or payload.get("tool_name")
        if isinstance(t, str):
            return t
    return "unknown"


def ask_broker(sock_path, token, payload, cwd):
    """Returns (decision, reason); raises on any failure."""
    req = {"v": 1, "token": token, "runtime": RUNTIME, "cwd": cwd, "payload": payload}
    data = json.dumps(req).encode() + b"\n"
    if len(data) > MAX_REQUEST:
        req["payload"] = {"oversized": True, "tool": tool_name(payload)}
        data = json.dumps(req).encode() + b"\n"
    end = time.monotonic() + DEADLINE
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.settimeout(DEADLINE)
        s.connect(sock_path)
        s.settimeout(max(0.05, end - time.monotonic()))
        s.sendall(data)
        buf = b""
        while b"\n" not in buf:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("no reply within %.1fs" % DEADLINE)
            s.settimeout(left)
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    if not buf.strip():
        raise ValueError("empty reply")
    try:
        rep = json.loads(buf.split(b"\n", 1)[0])
    except ValueError:
        raise ValueError("malformed reply")
    if not isinstance(rep, dict) or rep.get("v") != 1:
        raise ValueError("malformed reply")
    if "error" in rep:
        raise ValueError("broker error: %s" % str(rep["error"])[:100])
    decision = rep.get("decision")
    if decision not in TO_AGY:
        raise ValueError("unknown decision")
    reason = rep.get("reason")
    return decision, reason if isinstance(reason, str) else ""


def answer(decision, reason):
    return json.dumps({"decision": TO_AGY[decision], "reason": reason})


def run():
    raw = drain()
    mode = os.environ.get("BW_GUARD_MODE")
    if not mode:
        return INERT_ANSWER
    markers = os.environ.get("BW_GUARD_MARKERS")
    try:
        sock_path, token = os.environ.get("BW_GUARD_SOCKET"), os.environ.get("BW_GUARD_TOKEN")
        if not sock_path:
            raise ValueError("BW_GUARD_SOCKET is not set")
        if not token:
            raise ValueError("BW_GUARD_TOKEN is not set")
        if mode not in ("shadow", "acting"):
            raise ValueError("BW_GUARD_MODE is neither shadow nor acting")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ValueError("unparsable hook input")
        if len(raw) > MAX_REQUEST:  # not parsed into the request: the stub carries only the tool
            payload = {"oversized": True, "tool": tool_name(payload)}
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        decision, reason = ask_broker(sock_path, token, payload, cwd if isinstance(cwd, str) and cwd else os.getcwd())
        return answer(decision, reason)
    except Exception as exc:
        why = "%s: %s" % (type(exc).__name__, exc) if not isinstance(exc, ValueError) else str(exc)
        if mode == "shadow":
            write_marker(markers, mode, why)
            return answer("allow", "guard unavailable: " + why)
        return answer("block", "guard unavailable: " + why)


def main():
    try:
        out = run()
    except BaseException as exc:  # last resort: a hook that dies silently has no opinion, so fail closed
        shadow = os.environ.get("BW_GUARD_MODE") == "shadow"
        out = answer("allow" if shadow else "block", "guard unavailable: %s" % type(exc).__name__)
    sys.stdout.write(out + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
