"""redact.py — best-effort scrubbing of a call before it is written to the local guard log.

Not a secrets scanner: it catches the common shapes (bearer tokens, KEY=/TOKEN=/SECRET=/PASSWORD=
assignments, --password/--token flag values, user:pass@ in URLs, Authorization headers, Basic
credentials, and JSON or dict keys such as "password": "..." or 'api_key': '...'). digest()
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
    # Basic <base64>: a capital B and a mixed-case token of 8+ characters, so "basic tests" stays
    (re.compile(r"\b(Basic)\s+(?=[A-Za-z0-9+/]*[A-Z])(?=[A-Za-z0-9+/]*[a-z])[A-Za-z0-9+/]{8,}={0,2}"), r"\1 [REDACTED]"),
    # "password": "x" and 'api_key': 'x' in tool and MCP args (also as \"token\": \"x\" inside a JSON string)
    (re.compile(r"""(?i)((\\?["'])[A-Za-z0-9_.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key"""
                r"""|private[_-]?key|authorization|credentials?)\2\s*:\s*)(\\?["'])(?:(?!\3).)+\3"""),
     r"\1\3[REDACTED]\3"),
]


def redact(text):
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def digest(call):
    d = call._asdict() if hasattr(call, "_asdict") else call
    blob = json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
