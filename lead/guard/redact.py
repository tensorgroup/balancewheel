"""redact.py — best-effort scrubbing of a call before it is written to the local guard log.

Not a secrets scanner: it catches the common shapes (bearer tokens, KEY=/TOKEN=/SECRET=/PASSWORD=
assignments, --password/--token flag values, user:pass@ in URLs, Authorization headers). digest()
hashes the unredacted call so two log rows for the same call can be matched without storing it.
"""
import hashlib
import json
import re

_VAL = r"""("[^"]*"|'[^']*'|\S+)"""
_PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*)[^\r\n'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)=)" + _VAL), r"\1[REDACTED]"),
    (re.compile(r"(?i)(--(?:password|passwd|token|secret|api-key|apikey)(?:=|\s+))" + _VAL), r"\1[REDACTED]"),
    (re.compile(r"(://[^/\s:@]+):[^@\s/]+@"), r"\1:[REDACTED]@"),
]


def redact(text):
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def digest(call):
    d = call._asdict() if hasattr(call, "_asdict") else call
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
