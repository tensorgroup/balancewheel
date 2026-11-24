"""A stand-in lead runtime for runner tests. Synthetic; never talks to a model.

argv: fake_lead.py <name> [first prompt]
env:  FAKE_OUT (dir for evidence), FAKE_MODE_<name> = sleep | ignore_term | exit | guard_probe | ask | ask_exit
      ask: sleeps; on SIGTERM publishes one ask in BW_ASKS (.tmp-<n>, renamed to <n>.ask), then exits.
      ask_exit: publishes one ask and exits at once. The ask's text is FAKE_ASK_TEXT (default "fake question?").
      guard_probe: sends one allowed pi-shaped call to the guard broker over BW_GUARD_SOCKET and
      records the reply, then exits like `exit`. FAKE_STATE (the bw state dir) makes it replay the
      logged self-test call instead of `ls`. FAKE_GUARD_RUNTIME names the runtime (default pi).
      FAKE_UNLOCK_<name> set: flock(LOCK_UN) every inherited descriptor before writing the evidence.
      panel: writes a design panel request with the literal path under BW_PANEL, polls its response and
      status.json until `ready`, logs it with every finding `confirmed` (the ids from next.md), polls
      until `logged`, writes the log response to <FAKE_OUT>/<name>-panel.json, and exits 0.
      panel_nolog: the same up to `ready`, writes {"panel", "state"} to <name>-panel.json, and exits 0
      (FAKE_PANEL_STAY_<name> set: sleeps instead of exiting). panel_exit: exits once bw answered the
      request ({"panel"} in <name>-panel.json). log_open: logs the first panel listed in the first
      prompt's "open panels" section, as `panel` does, and exits 0. A panel mode that fails writes
      {"error"} to <name>-panel.json and exits 1.
Writes <FAKE_OUT>/<name>.json: pid, pgid, argv, the BW_GUARD_* variable names, BW_ASKS and BW_INBOX,
BW_PANEL and BW_PANEL_VIEW, the probe reply, whether every pid listed in FAKE_OUT/*.json
from earlier leads is gone, whether it holds the terminal's foreground, and each inherited descriptor
above 2 as [inode, access mode].
"""
import json, os, re, signal, sys, time

name = sys.argv[1]
out = os.environ["FAKE_OUT"]
mode = os.environ.get(f"FAKE_MODE_{name}", "sleep")
earlier = []
for f in os.listdir(out):
    if f.endswith(".json"):
        try:
            with open(os.path.join(out, f)) as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("pid"), int):  # fake seats and panel results share FAKE_OUT
            earlier.append(doc["pid"])


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def inherited_fds():
    import fcntl
    fds = []
    for fd in range(3, 256):
        try:
            fds.append([os.fstat(fd).st_ino, fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE])
        except OSError:
            pass
    return fds


fg = None
try:
    fg = os.tcgetpgrp(sys.stdin.fileno()) == os.getpgrp()
except OSError:
    pass
probe = None
if mode == "guard_probe":
    import socket
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(10)
    s.connect(os.environ["BW_GUARD_SOCKET"])
    cmd = "ls"
    if os.environ.get("FAKE_STATE"):  # replay the logged self-test call, as a lead that can read guard.jsonl could
        with open(os.path.join(os.environ["FAKE_STATE"], "guard.jsonl")) as gf:
            cmd = [json.loads(l) for l in gf if '"self_test": true' in l][0]["call"]
    req = {"v": 1, "token": os.environ["BW_GUARD_TOKEN"], "runtime": os.environ.get("FAKE_GUARD_RUNTIME", "pi"),
           "cwd": os.getcwd(), "payload": {"toolName": "bash", "input": {"command": cmd}}}
    s.sendall(json.dumps(req).encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    probe = json.loads(buf)
    s.close()
    mode = "exit"
if os.environ.get(f"FAKE_UNLOCK_{name}"):  # a lead dropping the locks it inherited
    import fcntl
    for fd in range(3, 256):
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
# Write to a temp name and rename: the tests poll for this file, and a reader must never see it
# half-written (an empty read raised JSONDecodeError in test_switch_check_does_not_block_on_an_update).
with open(os.path.join(out, f"{name}.json.tmp"), "w") as fh:
    json.dump({"pid": os.getpid(), "pgid": os.getpgrp(), "argv": sys.argv, "foreground": fg, "probe": probe,
               "guard_env": sorted(k for k in os.environ if k.startswith("BW_GUARD_")),
               "inbox_env": {k: os.environ.get(k) for k in ("BW_ASKS", "BW_INBOX")},
               "panel_env": {k: os.environ.get(k) for k in ("BW_PANEL", "BW_PANEL_VIEW")},
               "triage_env": {k: os.environ.get(k) for k in ("BW_TRIAGE_HOOK", "BW_TRIAGE_CONFIG")},
               "earlier_alive": [p for p in earlier if alive(p)], "fds": inherited_fds()}, fh)
os.replace(os.path.join(out, f"{name}.json.tmp"), os.path.join(out, f"{name}.json"))
print(f"FAKE {name} started", flush=True)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.environ.get(f"FAKE_SPAWN_{name}"):
    import subprocess
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.path.join(out, f"{name}-child.json.tmp"), "w") as fh:
        json.dump({"pid": kid.pid}, fh)
    os.replace(os.path.join(out, f"{name}-child.json.tmp"), os.path.join(out, f"{name}-child.json"))


def publish_ask():
    d = os.environ["BW_ASKS"]
    tmp = os.path.join(d, f".tmp-{os.getpid()}")
    with open(tmp, "w") as fh:
        fh.write("@human ask\n" + os.environ.get("FAKE_ASK_TEXT", "fake question?"))
    os.rename(tmp, os.path.join(d, f"{os.getpid()}.ask"))


if mode == "ask":
    def _on_term(*_):
        publish_ask()
        os._exit(0)
    signal.signal(signal.SIGTERM, _on_term)
if mode == "ask_exit":
    publish_ask()
    sys.exit(0)
if mode == "exit":
    time.sleep(0.3)
    sys.exit(0)


# --- panel modes: the paths come from the first prompt, as a real lead reads them ---

def panel_result(obj, code=None):
    with open(os.path.join(out, f"{name}-panel.json.tmp"), "w") as fh:
        json.dump(obj, fh)
    os.replace(os.path.join(out, f"{name}-panel.json.tmp"), os.path.join(out, f"{name}-panel.json"))
    if code is not None:
        sys.exit(code)


def prompt_path(pattern):
    m = re.search(pattern, sys.argv[2] if len(sys.argv) > 2 else "")
    if not m:
        panel_result({"error": f"no match in the first prompt: {pattern}"}, 1)
    return m.group(1)


def read_json_file(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def poll(fn, what, timeout=50):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v is not None:
            return v
        time.sleep(0.1)
    panel_result({"error": f"timed out waiting for {what}"}, 1)


def send(request_dir, view_root, obj):
    rid = "r" + os.urandom(4).hex()
    tmp = os.path.join(request_dir, f".tmp-{rid}")
    with open(tmp, "w") as fh:
        json.dump(obj, fh)
    os.rename(tmp, os.path.join(request_dir, f"{rid}.request"))
    resp = poll(lambda: read_json_file(os.path.join(view_root, "responses", f"{rid}.json")), f"response {rid}")
    if not resp.get("ok"):
        panel_result({"error": resp}, 1)
    return resp


def status_in(view_root, pid, states):
    def check():
        doc = read_json_file(os.path.join(view_root, pid, "status.json"))
        return doc if doc and doc.get("state") in states else None
    return poll(check, f"panel {pid} in {states}")


def log_panel(request_dir, view_root, pid):
    with open(os.path.join(view_root, pid, "next.md")) as fh:
        m = re.search(r"needs a verdict: (.*)", fh.read())
    ids = [i.strip() for i in m.group(1).split(",") if i.strip()] if m else []
    resp = send(request_dir, view_root, {"v": 1, "type": "log", "panel": pid, "nonce": "n-log",
                                         "verdicts": {i: "confirmed" for i in ids}})
    status_in(view_root, pid, ("logged",))
    panel_result(resp, 0)


if mode in ("panel", "panel_nolog", "panel_exit", "log_open"):
    request_dir = prompt_path(r"file-write tool to (.+?)/\.tmp-<id>\.")
    view_root = prompt_path(r"bw answers in (.+?)/responses/<id>\.json")
    if mode == "log_open":
        log_panel(request_dir, view_root, prompt_path(r"open panels \(finish and log them\):\n- (p[0-9a-f]{7}) "))
    pid = send(request_dir, view_root, {"v": 1, "type": "panel", "nonce": "n-panel", "checkpoint": "design",
                                        "target": "the widget module", "focus": "error paths", "stakes": "normal",
                                        "paths": []})["panel"]
    if mode == "panel_exit":
        panel_result({"panel": pid}, 0)
    st = status_in(view_root, pid, ("ready", "failed"))
    if mode == "panel_nolog":
        panel_result({"panel": pid, "state": st["state"]}, None if os.environ.get(f"FAKE_PANEL_STAY_{name}") else 0)
    else:
        log_panel(request_dir, view_root, pid)
while True:
    time.sleep(0.1)
