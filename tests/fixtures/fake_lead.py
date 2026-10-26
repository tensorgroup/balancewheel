"""A stand-in lead runtime for runner tests. Synthetic; never talks to a model.

argv: fake_lead.py <name> [first prompt]
env:  FAKE_OUT (dir for evidence), FAKE_MODE_<name> = sleep | ignore_term | exit | guard_probe | ask | ask_exit
      ask: sleeps; on SIGTERM publishes one ask in BW_ASKS (.tmp-<n>, renamed to <n>.ask), then exits.
      ask_exit: publishes one ask and exits at once. The ask's text is FAKE_ASK_TEXT (default "fake question?").
      guard_probe: sends one allowed pi-shaped call to the guard broker over BW_GUARD_SOCKET and
      records the reply, then exits like `exit`. FAKE_STATE (the bw state dir) makes it replay the
      logged self-test call instead of `ls`. FAKE_GUARD_RUNTIME names the runtime (default pi).
      FAKE_UNLOCK_<name> set: flock(LOCK_UN) every inherited descriptor before writing the evidence.
Writes <FAKE_OUT>/<name>.json: pid, pgid, argv, the BW_GUARD_* variable names, BW_ASKS and BW_INBOX, the probe reply, whether every pid listed in FAKE_OUT/*.json
from earlier leads is gone, whether it holds the terminal's foreground, and each inherited descriptor
above 2 as [inode, access mode].
"""
import json, os, signal, sys, time

name = sys.argv[1]
out = os.environ["FAKE_OUT"]
mode = os.environ.get(f"FAKE_MODE_{name}", "sleep")
earlier = []
for f in os.listdir(out):
    if f.endswith(".json"):
        with open(os.path.join(out, f)) as fh:
            earlier.append(json.load(fh)["pid"])


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
with open(os.path.join(out, f"{name}.json"), "w") as fh:
    json.dump({"pid": os.getpid(), "pgid": os.getpgrp(), "argv": sys.argv, "foreground": fg, "probe": probe,
               "guard_env": sorted(k for k in os.environ if k.startswith("BW_GUARD_")),
               "inbox_env": {k: os.environ.get(k) for k in ("BW_ASKS", "BW_INBOX")},
               "earlier_alive": [p for p in earlier if alive(p)], "fds": inherited_fds()}, fh)
print(f"FAKE {name} started", flush=True)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.environ.get(f"FAKE_SPAWN_{name}"):
    import subprocess
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.path.join(out, f"{name}-child.json"), "w") as fh:
        json.dump({"pid": kid.pid}, fh)


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
while True:
    time.sleep(0.1)
