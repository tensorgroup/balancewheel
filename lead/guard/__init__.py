"""lead.guard — judging a sandboxed lead's tool calls: lexer, normaliser, rules, redaction.

rules_version() names what decides (so a log row can say which rules judged it); guard_version()
names everything that runs. AGY_SPIKE_PASSED stays False until agy's hook payloads are captured
and the normaliser can read them; until then every agy call is gray.
"""
import hashlib
import os

AGY_SPIKE_PASSED = False
DECIDING = ("lexer.py", "rules.py", "normalise.py", "redact.py")
HERE = os.path.dirname(os.path.realpath(__file__))


def _hash(directory, names):
    h = hashlib.sha256()
    for n in names:
        h.update(n.encode() + b"\0")
        with open(os.path.join(directory, n), "rb") as f:
            h.update(f.read() + b"\0")
    return h.hexdigest()[:12]


def rules_version(directory=None):
    return _hash(directory or HERE, DECIDING)


def guard_version(directory=None):
    d = directory or HERE
    with os.scandir(d) as it:
        names = sorted(e.name for e in it if e.is_file() and not e.name.endswith(".pyc"))
    return _hash(d, names)
