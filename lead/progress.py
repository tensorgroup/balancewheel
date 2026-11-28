"""progress.py — the "still waiting on" line that repeats while a slow part of a step runs.

Each slow part is marked with `begin` and `end`. While any part runs, a line names each one with
its elapsed time, every HEARTBEAT_S seconds. Safe to call from worker threads."""
import threading
import time

HEARTBEAT_S = 15


def duration(seconds) -> str:
    s = int(seconds)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


class Progress:
    def __init__(self, say, interval=None, clock=time.monotonic):
        self._say, self._clock = say, clock
        self._interval = HEARTBEAT_S if interval is None else interval
        self._lock = threading.Lock()
        self._active = {}  # key -> (short name, started at)
        self._stop = threading.Event()
        self._thread = None

    def begin(self, key, name):
        """Mark `key` as running; the waiting line calls it `name`."""
        with self._lock:
            self._active[key] = (name, self._clock())

    def end(self, key):
        with self._lock:
            self._active.pop(key, None)

    def waiting_line(self):
        with self._lock:
            now = self._clock()
            # A part younger than one interval was just announced by its own line: not yet "still" waiting.
            parts = [f"{name} ({duration(now - t0)})" for name, t0 in self._active.values()
                     if now - t0 >= self._interval]
        return f"still waiting on: {', '.join(parts)}" if parts else None

    def _loop(self):
        while not self._stop.wait(self._interval):
            line = self.waiting_line()
            if line:
                self._say(line)

    def __enter__(self):
        self._thread = threading.Thread(target=self._loop, name="bw-progress", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        return False
