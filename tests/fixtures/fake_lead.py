"""A stand-in lead runtime for runner tests. Synthetic; never talks to a model.

argv: fake_lead.py <name> [first prompt]
env:  FAKE_OUT (dir for evidence), FAKE_MODE_<name> = sleep | ignore_term | exit
Writes <FAKE_OUT>/<name>.json: pid, pgid, argv, whether every pid listed in FAKE_OUT/*.json
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
with open(os.path.join(out, f"{name}.json"), "w") as fh:
    json.dump({"pid": os.getpid(), "pgid": os.getpgrp(), "argv": sys.argv, "foreground": fg,
               "earlier_alive": [p for p in earlier if alive(p)]}, fh)
print(f"FAKE {name} started", flush=True)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "exit":
    time.sleep(0.3)
    sys.exit(0)
while True:
    time.sleep(0.1)
