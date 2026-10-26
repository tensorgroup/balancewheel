"""A check is a named reading of one thing: ok, warn (something is off), or unknown."""
from collections import namedtuple

Check = namedtuple("Check", "name state detail")  # state in {"ok", "warn", "unknown"}


def lines(title, checks):
    bad = [c for c in checks if c.state != "ok"]
    if not bad:
        return [f"{title}: ok"]
    return [f"  {c.name}: {c.state} — {c.detail}" for c in bad]
