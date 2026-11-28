"""The `run` rows: one seat or local run as a start row, beat rows, and one end row.

`bw seat` (seats/run.py) writes a start row just before the seat CLI starts and exactly one end row
after it. The rows go through `store.append`, the only appender. Row shapes are in metrics/store.py.
"""
import re
import secrets
import time

from lead.guard.redact import redact
from lead.guard.review import safe
from metrics import store  # noqa: F401  (the appender the callers use; tests patch it here)

RUN_ID = re.compile(r"r[0-9a-f]{12}")
LEAD_SESSION = re.compile(r"[0-9a-f]{12}")  # a lead Session.id (lead/runner.py)
LABEL_CHARS = 80
END_LOOKBACK_S = 7 * 86400


def new_run_id() -> str:
    return "r" + secrets.token_hex(6)


def label(text: str) -> str:
    """The first line of `text`, redacted, every control character escaped, cut to LABEL_CHARS."""
    lines = str(text or "").splitlines()
    first = lines[0].strip() if lines else ""
    return safe(redact(first))[:LABEL_CHARS]


def start_row(seat, model, dir, label, source, *, pid=None, host=None, panel=None, session_id=None,
              stand_in_for=None, t=None) -> dict:
    """A start row with a new run id. An optional field that is None is left out."""
    row = {"type": "run", "phase": "start", "run_id": new_run_id(), "t": time.time() if t is None else t,
           "seat": seat, "model": model, "dir": dir, "label": label, "source": source}
    for k, v in (("pid", pid), ("host", host), ("panel", panel), ("session_id", session_id),
                 ("stand_in_for", stand_in_for)):
        if v is not None:
            row[k] = v
    return row


def end_row(run_id, seat, dir, ok, reason, duration_s, *, tokens_in=None, tokens_out=None, cost_usd=None,
            ctx_peak=None, t=None) -> dict:
    """An end row. It always holds every field; an unknown one is null."""
    return {"type": "run", "phase": "end", "run_id": run_id, "t": time.time() if t is None else t,
            "seat": seat, "dir": dir, "ok": ok, "reason": reason, "duration_s": duration_s,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd, "ctx_peak": ctx_peak}
