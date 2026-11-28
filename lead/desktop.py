"""desktop.py — a desktop notification, for what must reach the user while a lead owns the terminal.

macOS uses `osascript` (the text goes in as arguments, never into the script); elsewhere
`notify-send` when it is installed. Best effort: it never raises and never blocks. It starts no
thread (bw forks leads, and no thread of bw's may be alive then): the caller reaps the process it
returns, with `reap`.
"""
import shutil
import subprocess
import sys

MAX_TEXT = 240

# The title and the text are `argv` items, so no quote in them can change the script.
OSASCRIPT = ["osascript", "-e", "on run argv", "-e",
             "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]


def argv_for(title, text, platform=None, which=shutil.which):
    platform = platform or sys.platform
    text = text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…"
    if platform == "darwin" and which("osascript"):
        return OSASCRIPT + [title, text]
    if which("notify-send"):
        return ["notify-send", "--", title, text]
    return None


def notify(title, text, platform=None, which=shutil.which, popen=subprocess.Popen):
    """The started notifier process, or None."""
    argv = argv_for(title, text, platform, which)
    if argv is None:
        return None
    try:
        return popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)  # never the lead's terminal or its process group
    except Exception:  # noqa: BLE001 — a notification never stops a lead
        return None


def reap(procs):
    """The notifier processes still running; the finished ones are reaped (no zombie)."""
    alive = []
    for p in procs:
        try:
            if p.poll() is None:
                alive.append(p)
        except Exception:  # noqa: BLE001
            pass
    return alive
