"""review.py — audit of the guard's decisions: stops and sampled allows, per evidence key.

Evidence is only ever counted within one evidence key (profile, runtime, runtime version, rules
version, policy digest); marks made under another key count nowhere. Self-test decisions are
never evidence. Pure functions plus run(), which reads verdicts line by line from a stream.
"""
import json
import os
import random
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
from lead.guard import record  # noqa: E402

MIN_REVIEWED, MIN_SAMPLED, MAX_FALSE_STOP = 50, 20, 0.10
# whole words only: "really not sure" must not become "right", nor "what?" "wrong"
VERDICTS = {"r": "r", "right": "r", "w": "w", "wrong": "w", "s": "s", "skip": "s", "q": "q", "quit": "q"}


def safe(value):
    """A lead-originated string with every control character shown as an escape (a lead must not
    be able to repaint the terminal the user marks decisions on). A backslash is doubled, so an escape
    the lead typed never prints like a real control character. Other printable text is unchanged."""
    out = []
    for ch in str(value):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")  # a typed backslash-x-1-b must never print like a real ESC
        elif o < 0x20 or o == 0x7f or 0x80 <= o < 0xa0 or ch in "\u2028\u2029":
            out.append({9: "\\t", 13: "\\r"}.get(o) or (f"\\x{o:02x}" if o < 0x100 else f"\\u{o:04x}"))
        else:
            out.append(ch)
    return "".join(out)


def real(decisions):
    return [d for d in decisions if not d.get("self_test")]


def of_key(decisions, key):
    return [d for d in real(decisions) if record.evidence_key(d) == key]


def pick(decisions, key, recent, sample, rng):
    mine = [d for d in of_key(decisions, key) if "mark" not in d]
    stops = [d for d in mine if d.get("decision") != "allow"]
    allows = [d for d in mine if d.get("decision") == "allow"]
    out = [dict(d) for d in (stops[-recent:] if recent > 0 else [])]
    out += [dict(d, sampled=True) for d in rng.sample(allows, min(sample, len(allows)))]
    return out


def _split(items):
    by = {}
    for d in items:
        s = d.get("source") or "unknown"
        by[s] = by.get(s, 0) + 1
    return by


def summary(decisions, key):
    ds = of_key(decisions, key)
    marked = [d for d in ds if "mark" in d]
    stops = [d for d in marked if d.get("decision") != "allow"]
    sampled = [d for d in marked if d["mark"].get("sampled") and d.get("decision") == "allow"]
    false_stops = [d for d in stops if d["mark"].get("verdict") == "wrong"]
    false_allows = [d for d in sampled if d["mark"].get("verdict") == "wrong"]
    gray = sum(1 for d in ds if d.get("source") == "gray")
    groups = {"reviewed": marked, "sampled_allows": sampled, "false_stops": false_stops,
              "stops_reviewed": stops, "false_allows": false_allows}
    out = {k: len(v) for k, v in groups.items()}
    out["by_source"] = {k: _split(v) for k, v in groups.items()}
    out["decisions"] = len(ds)
    out["gray_share"] = gray / len(ds) if ds else 0.0
    return out


def promotion_line(s):
    rate = s["false_stops"] / s["stops_reviewed"] * 100 if s["stops_reviewed"] else 0.0
    return (f"acting needs ≥{MIN_REVIEWED} reviewed ({s['reviewed']}), ≥{MIN_SAMPLED} sampled allows "
            f"({s['sampled_allows']}), <{int(MAX_FALSE_STOP * 100)}% false stops ({rate:.0f}%), "
            f"and a clean `bw guard eval` (v1)")


def keys(decisions):
    """Evidence keys with real decisions, most recent first."""
    seen = []
    for d in reversed(real(decisions)):
        k = record.evidence_key(d)
        if k not in seen:
            seen.append(k)
    return seen


def select_key(decisions, profile=None):
    for k in keys(decisions):
        if profile is None or k[0] == profile:
            return k
    return None


def key_label(key):
    return "profile={} runtime={} version={} rules={} policy={}".format(*key)


def show(d, out, n, total):
    tag = "allow (sampled)" if d.get("sampled") else d.get("decision")
    print(f"--- {n}/{total}  id={d['id']}", file=out)
    print(safe(f"runtime={d.get('runtime')} tool={d.get('tool')} decision={tag} "
               f"source={d.get('source')} rule={d.get('rule')}"), file=out)
    print("call:", file=out)
    for line in str(d.get("call", "")).split("\n") or [""]:
        print(textwrap.fill(safe(line), 100, initial_indent="  ", subsequent_indent="  ",
                            break_long_words=True, replace_whitespace=False) or "  ", file=out)
    print(safe(f"reason: {d.get('reason')}"), file=out)


def print_summary(decisions, key, out):
    s = summary(decisions, key)
    print(safe(f"summary for {key_label(key)}"), file=out)
    print(f"  reviewed {s['reviewed']} · sampled allows {s['sampled_allows']} · stops reviewed "
          f"{s['stops_reviewed']} · false stops {s['false_stops']} · false allows {s['false_allows']} "
          f"· gray share {s['gray_share'] * 100:.0f}%", file=out)
    for k, by in s["by_source"].items():
        if by:
            print(f"  {k} by source: " + ", ".join(f"{a}={b}" for a, b in sorted(by.items())), file=out)
    print(promotion_line(s), file=out)


def run(state_dir, profile=None, recent=20, sample=5, list_only=False, stdin=None, out=None, rng=None):
    stdin, out = stdin or sys.stdin, out or sys.stdout
    rng = rng or random.SystemRandom()
    loaded = record.load(state_dir)
    for m in loaded["orphan_marks"]:
        print(f"bw: ignoring an orphan mark (no decision {m.get('decision_id')})", file=sys.stderr)
    for w in loaded["warnings"]:
        print(f"bw: guard log: {w}", file=sys.stderr)
    key = select_key(loaded["decisions"], profile)
    if key is None:
        print("bw: no guard decisions to review" + (f" for profile {profile}" if profile else ""), file=out)
        return 0
    print(safe(f"reviewing {key_label(key)}"), file=out)
    items = pick(loaded["decisions"], key, recent, sample, rng)
    for i, d in enumerate(items, 1):
        show(d, out, i, len(items))
        if list_only:
            continue
        while True:
            print("[r]ight [w]rong [s]kip [q]uit> ", end="", file=out, flush=True)
            line = stdin.readline()
            if line == "":  # EOF behaves like q; a blank line is just a line
                ans = "q"
                break
            ans = VERDICTS.get(line.strip().lower())
            if ans:
                break
            print("", file=out)
        print("", file=out)
        if ans == "q":
            break
        if ans in ("r", "w"):
            record.append(state_dir, {"type": "mark", "decision_id": d["id"],
                                      "verdict": "right" if ans == "r" else "wrong",
                                      "sampled": bool(d.get("sampled"))})
    if not items:
        print("nothing unmarked to review", file=out)
    print_summary(record.load(state_dir)["decisions"], key, out)
    return 0


def status_lines(state_dir):
    decisions = record.load(state_dir)["decisions"]
    lines = []
    for key in keys(decisions):
        ds = of_key(decisions, key)
        s = summary(decisions, key)
        lines.append(safe(f"  {key[1]} {key[2]} (profile {key[0]}) · {ds[-1].get('mode')} · {s['decisions']} decisions · "
                          f"gray {s['gray_share'] * 100:.0f}% · {promotion_line(s)}"))
    return lines
