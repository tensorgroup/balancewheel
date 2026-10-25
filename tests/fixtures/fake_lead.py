"""A stand-in lead runtime for runner tests. Synthetic; never talks to a model.

argv: fake_lead.py <name> [first prompt]
env:  FAKE_OUT (dir for evidence), FAKE_MODE_<name> = sleep | ignore_term | exit | guard_probe
      guard_probe: sends one allowed pi-shaped call to the guard broker over BW_GUARD_SOCKET and
      records the reply, then exits like `exit`. FAKE_GUARD_RUNTIME names the runtime (default pi).
Writes <FAKE_OUT>/<name>.json: pid, pgid, argv, the BW_GUARD_* variable names, the probe reply, whether every pid listed in FAKE_OUT/*.json
from earlier leads is gone, and whether it holds the terminal's foreground.
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
    req = {"v": 1, "token": os.environ["BW_GUARD_TOKEN"], "runtime": os.environ.get("FAKE_GUARD_RUNTIME", "pi"),
           "cwd": os.getcwd(), "payload": {"toolName": "bash", "input": {"command": "ls"}}}
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
with open(os.path.join(out, f"{name}.json"), "w") as fh:
    json.dump({"pid": os.getpid(), "pgid": os.getpgrp(), "argv": sys.argv, "foreground": fg, "probe": probe,
               "guard_env": sorted(k for k in os.environ if k.startswith("BW_GUARD_")),
               "earlier_alive": [p for p in earlier if alive(p)]}, fh)
print(f"FAKE {name} started", flush=True)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.environ.get(f"FAKE_SPAWN_{name}"):
    import subprocess
    kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    with open(os.path.join(out, f"{name}-child.json"), "w") as fh:
        json.dump({"pid": kid.pid}, fh)
if mode == "exit":
    time.sleep(0.3)
    sys.exit(0)
while True:
    time.sleep(0.1)
