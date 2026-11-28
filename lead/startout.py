"""startout.py — what `bw start` prints, and how.

On a terminal the output has a header and sections (Updates, Checks, Quota, Lead, Also). Each line
starts with a mark: ✓ passed or ok, ✗ failed, · information, … in progress. Paths under the home
directory print with `~`, and long lines wrap. Anywhere else (a pipe, a file, tests) every line is
plain text with the prefix `bw start: `, with no header, no section names, and no marks.

A section that comes earlier holds the lines of a later one while it is open (`begin`/`end`), so the
parallel checks never print under the wrong heading; the held lines print, in order, when it ends.
One lock covers every write, because the checks run on threads.
"""
import os
import shutil
import textwrap
import threading

SECTIONS = ("Updates", "Checks", "Quota", "Lead", "Also")
OK, BAD, INFO, WAIT = "✓", "✗", "·", "…"
PREFIX = "bw start: "


class Report:
    def __init__(self, out, tty=False, home=None, width=None):
        self.out, self.tty = out, tty
        self.home = (home if home is not None else os.path.expanduser("~")).rstrip(os.sep)
        cols = width or shutil.get_terminal_size((88, 24)).columns
        self.width = max(60, min(cols if cols >= 20 else 88, 100))  # a terminal that reports no size gets 88
        self._lock = threading.RLock()
        self._open, self._held, self._current = set(), [], None

    # --- plain text, for both modes ---------------------------------------------------------

    def say(self, text):
        """A line outside any section: errors and refusals. Always `bw start: <text>`."""
        with self._lock:
            print(f"{PREFIX}{text}", file=self.out, flush=True)

    # --- sections -------------------------------------------------------------------------------

    def header(self, worktree, dry_profile=None):
        """The terminal's title line; `dry_profile` (a dry run) adds the line that says nothing is run."""
        with self._lock:
            if self.tty:
                print(f"bw start · {self.tilde(worktree)}", file=self.out, flush=True)
                if dry_profile:
                    print(f"  dry run (profile {dry_profile}): nothing is run or written", file=self.out, flush=True)
            elif dry_profile:
                self.say(f"dry run for {worktree} (profile {dry_profile}); nothing is run or written")

    def begin(self, section):
        with self._lock:
            self._open.add(section)

    def end(self, section):
        with self._lock:
            self._open.discard(section)
            held, self._held = self._held, []
            for item in held:
                self._route(*item)

    def line(self, section, text, mark=INFO, label=None):
        """One line. `label` prefixes the plain-text line only (a terminal shows it as the section)."""
        with self._lock:
            if self.tty:
                self._route(section, text, mark)
            else:
                print(f"{PREFIX}{label + ': ' if label else ''}{text}", file=self.out, flush=True)

    def ok(self, section, text, label=None):
        self.line(section, text, OK, label)

    def bad(self, section, text, label=None):
        self.line(section, text, BAD, label)

    def wait(self, section, text):
        """A progress line. It is never held: when an earlier section is still open, it prints under
        the section that is on screen, because the wait it announces has already started."""
        with self._lock:
            if self.tty and self._blocked(section):
                self._write(self._current or section, text, WAIT)
            else:
                self.line(section, text, WAIT)

    def detail(self, section, text):
        """A line under the one before it: a file's content, say. No mark, and no wrapping."""
        with self._lock:
            if self.tty:
                self._route(section, text, None)
            else:
                print(f"{PREFIX}  {text}", file=self.out, flush=True)

    def rows(self, section, rows, label):
        """Aligned `(name, text)` rows. The plain form is `<label> <name>: <text>`."""
        with self._lock:
            if not self.tty:
                for name, text in rows:
                    print(f"{PREFIX}{label} {name}: {text}", file=self.out, flush=True)
                return
            col = max((len(n) for n, _ in rows), default=0) + 2
            for name, text in rows:
                self._route(section, f"{name:<{col}}{text}", "", indent=2 + col)

    # --- the terminal layout ------------------------------------------------------------------

    def tilde(self, text):
        if not self.home:
            return text
        out, i = [], 0
        while True:
            j = text.find(self.home, i)
            if j < 0:
                out.append(text[i:])
                return "".join(out)
            end = j + len(self.home)
            if (j == 0 or text[j - 1] in " (=:") and (end == len(text) or text[end] in os.sep + " ),;:'\""):
                out.append(text[i:j] + "~")
            else:
                out.append(text[i:end])
            i = end

    def _blocked(self, section):
        rank = SECTIONS.index(section)
        return any(SECTIONS.index(o) < rank for o in self._open)

    def _route(self, section, text, mark, indent=None):
        item = (section, text, mark, indent)
        if self._blocked(section) or (self._held and section in {h[0] for h in self._held}):
            self._held.append(item)
            return
        self._write(*item)

    def _write(self, section, text, mark, indent=None):
        if section != self._current:
            print(f"\n{section}", file=self.out)
            self._current = section
        text = self.tilde(text)
        if mark is None:  # a detail line: kept as written, not wrapped
            print(f"      {text}", file=self.out, flush=True)
            return
        lead = f"  {mark} " if mark else "  "
        hang = " " * (indent if indent is not None else 4)
        print(textwrap.fill(text, width=self.width, initial_indent=lead, subsequent_indent=hang,
                            break_long_words=False, break_on_hyphens=False), file=self.out, flush=True)
