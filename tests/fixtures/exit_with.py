"""Test fixture for `bw run`: exit with argv[1], or kill itself with the signal number in argv[2]."""
import os
import sys

if len(sys.argv) > 2:
    os.kill(os.getpid(), int(sys.argv[2]))
sys.exit(int(sys.argv[1]))
