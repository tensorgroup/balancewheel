"""Test fixture for `bw run`: write the file argv[1], then sleep up to 30 s.
On SIGTERM it writes argv[1] + ".term" and exits 0."""
import signal
import sys
import time

flag = sys.argv[1]


def _term(signum, frame):
    with open(flag + ".term", "w") as f:
        f.write("term\n")
    sys.exit(0)


signal.signal(signal.SIGTERM, _term)  # before the flag, so a SIGTERM sent after the flag is always caught
with open(flag, "w") as f:
    f.write("up\n")
time.sleep(30)
