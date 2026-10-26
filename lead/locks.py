"""Runtime locks: one flock per runtime, shared by every lead session on it, exclusive for its update.

`bw lead` takes the shared lock on its lead's runtime before checking that runtime's verification
and holds it through the launch and the session; the descriptor is passed to the lead, so the
kernel keeps the lock until bw AND the lead are gone (the worktree lock's pattern). The passed
descriptor's lock is shared with the lead, which could drop it with LOCK_UN, so bw also holds the
shared lock on a second, non-inheritable open of the file for as long as its session lasts. An updater
takes it exclusively without blocking and skips the tool when it is held, so an update can land
neither between a verification check and the launch it allowed, nor under a running lead.
"""
import fcntl
import os
from typing import Optional

DEFAULT_TOOL_RUNTIME = {"claude": "claude-code", "codex": "codex", "agy": "agy", "pi": "pi", "kimi": "kimi"}
NON_RUNTIME_TOOLS = frozenset({"orca", "cc-switch"})


def tool_runtime(name, tool_cfg) -> Optional[str]:
    """The runtime a tool's update could change: `tools.<t>.runtime`, else the default map.
    None for tools that update with no lock and for tools no runtime is known for."""
    if name in NON_RUNTIME_TOOLS:
        return None
    rt = (tool_cfg or {}).get("runtime")
    return rt if rt else DEFAULT_TOOL_RUNTIME.get(name)


def runtime_tools(config) -> dict:
    """runtime -> the configured tool that updates it."""
    out = {}
    for name, t in (config.get("tools") or {}).items():
        rt = tool_runtime(name, t)
        if rt:
            out[rt] = name
    return out


class RuntimeLock:
    def __init__(self, state, runtime):
        os.makedirs(os.path.join(state, "locks"), exist_ok=True)
        self.path = os.path.join(state, "locks", f"runtime-{runtime}.lock")
        # Read-only: flock needs no write access, and nothing should write to the lock file
        # through the descriptor a lead inherits.
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o600)
        self.session_fd = None  # bw's own open file description; never passed to a lead

    def _open_session(self):
        if self.session_fd is None:
            self.session_fd = os.open(self.path, os.O_RDONLY | os.O_CLOEXEC)
            os.set_inheritable(self.session_fd, False)

    def _close_session(self):
        if self.session_fd is not None:
            os.close(self.session_fd)
            self.session_fd = None

    def shared(self) -> bool:
        """Blocks while an update holds the lock exclusively."""
        self._open_session()
        fcntl.flock(self.session_fd, fcntl.LOCK_SH)
        fcntl.flock(self.fd, fcntl.LOCK_SH)
        return True

    def try_shared(self) -> bool:
        """False while an update holds the lock exclusively (never blocks)."""
        self._open_session()
        try:
            fcntl.flock(self.session_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            fcntl.flock(self.fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            self._close_session()
            return False

    def try_exclusive(self) -> bool:
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def release(self):
        """Close, never LOCK_UN: a lead that inherited the descriptor shares the lock, and an
        unlock would drop it for the lead too. Closing ours leaves the lead's copy holding it."""
        self._close_session()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
