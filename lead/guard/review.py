"""review.py — audit of the guard's decisions: stops and sampled allows, per evidence key.

`--remark <id>` marks a marked decision again: the latest mark counts, so a wrong mark can be corrected.
Evidence is only ever counted within one evidence key (profile, runtime, rules version, policy digest,
judge model, prompt hash); marks made under another key count nowhere. Review reads the final
decision: a judge allow is sampled as an allow. Self-test decisions are
never evidence. Pure functions plus run(), which reads verdicts line by line from a stream.
"""
import calendar
import json
import os
import random
import sys
import textwrap
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
from lead.guard import record  # noqa: E402
from lead.paths import read_json  # noqa: E402

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


OFFER_ALLOWS = 2  # sampled allows in each offer before promotion: 20 sampled allows in about 10 offers


def allowed(d):
    """True if the call ran: an allow, except an acting allow whose client gave up before the reply
    (outcome client-timeout), which stopped the call."""
    return d.get("decision") == "allow" and not (d.get("mode") == "acting" and d.get("outcome") == "client-timeout")


def _pools(decisions, key):
    """(stops, judge allows, other allows) among the unmarked decisions under `key`."""
    mine = [d for d in of_key(decisions, key) if "mark" not in d]
    return ([d for d in mine if not allowed(d)],
            [d for d in mine if allowed(d) and d.get("source") == "judge"],
            [d for d in mine if allowed(d) and d.get("source") != "judge"])


def _sample_allows(judged, others, k, rng):
    """`k` allows, stratified: at least half are judge allows while there are some, so the gate's
    judge-allow condition can be met; the rest are the other allows, then judge allows again."""
    n_judge = min(len(judged), max((k + 1) // 2, k - len(others)))
    n_other = min(len(others), k - n_judge)
    return [dict(d, sampled=True) for d in rng.sample(judged, n_judge) + rng.sample(others, n_other)]


def pick(decisions, key, recent, sample, rng):
    stops, judged, others = _pools(decisions, key)
    out = [dict(d) for d in (stops[-recent:] if recent > 0 else [])]
    return out + _sample_allows(judged, others, sample, rng)


def offer_pick(decisions, key, n, acting, rng):
    """`n` unmarked decisions for `bw start`'s short review. Before promotion OFFER_ALLOWS are sampled
    allows and the rest are the most recent stops. After promotion at least two are stops (the guard
    now acts, so what it blocked matters most) and the rest are sampled allows. The allows are
    stratified as pick() does them. A pool too short on one side is filled from the other."""
    stops, judged, others = _pools(decisions, key)
    n_allows = len(judged) + len(others)
    want_allows = n - min(n, 2) if acting else min(n, OFFER_ALLOWS)
    take_allows = min(n_allows, want_allows)
    take_stops = min(len(stops), n - take_allows)
    take_allows = min(n_allows, n - take_stops)  # a short stop pool is filled with more allows
    out = [dict(d) for d in (stops[-take_stops:] if take_stops else [])]
    return out + _sample_allows(judged, others, take_allows, rng)


def _split(items):
    by = {}
    for d in items:
        s = d.get("source") or "unknown"
        by[s] = by.get(s, 0) + 1
    return by


def epoch(ts):
    """A row's ts in epoch seconds: store.append writes `YYYY-MM-DDTHH:MM:SSZ`; a number passes through.
    Anything else is 0 (never after a demotion)."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return float(ts)
    try:
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0.0


def false_allows(decisions, key):
    """The sampled allows under `key` whose latest mark is `wrong`."""
    return [d for d in of_key(decisions, key) if "mark" in d and d["mark"].get("sampled")
            and allowed(d) and d["mark"].get("verdict") == "wrong"]


def summary(decisions, key, since=None):
    """Counts under one evidence key. `since` (epoch seconds) adds `marked_since`: decisions marked after it."""
    ds = of_key(decisions, key)
    marked = [d for d in ds if "mark" in d]
    stops = [d for d in marked if not allowed(d)]
    sampled = [d for d in marked if d["mark"].get("sampled") and allowed(d)]
    false_stops = [d for d in stops if d["mark"].get("verdict") == "wrong"]
    false_allows = [d for d in sampled if d["mark"].get("verdict") == "wrong"]
    gray = sum(1 for d in ds if record.is_gray(d))
    groups = {"reviewed": marked, "sampled_allows": sampled, "false_stops": false_stops,
              "stops_reviewed": stops, "false_allows": false_allows}
    out = {k: len(v) for k, v in groups.items()}
    out["by_source"] = {k: _split(v) for k, v in groups.items()}
    out["judge_sampled_allows"] = sum(1 for d in sampled if d.get("source") == "judge")
    out["marked_since"] = sum(1 for d in marked if epoch(d["mark"].get("ts")) > since) if since is not None else 0
    out["decisions"] = len(ds)
    out["gray_share"] = gray / len(ds) if ds else 0.0
    return out


def promotion_line(s, conditions=None):
    """What acting needs. Given the gate's conditions, it names each one and whether it holds."""
    if conditions is not None:
        word = {True: "ok", False: "open", None: "unknown"}
        return "acting needs " + "; ".join(f"{c.name} {word[c.ok]} ({c.detail})" for c in conditions)
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
    return "profile={} runtime={} rules={} policy={} judge={} prompt={}".format(*key)


def show(d, out, n, total):
    tag = "allow (sampled)" if d.get("sampled") else d.get("decision")
    print(f"--- {n}/{total}  id={d['id']}", file=out)
    print(safe(f"runtime={d.get('runtime')} tool={d.get('tool')} decision={tag} "
               f"source={d.get('source')} rule={d.get('rule')} outcome={d.get('outcome')}"), file=out)
    print("call:", file=out)
    for line in str(d.get("call", "")).split("\n") or [""]:
        print(textwrap.fill(safe(line), 100, initial_indent="  ", subsequent_indent="  ",
                            break_long_words=True, replace_whitespace=False) or "  ", file=out)
    print(safe(f"reason: {d.get('reason')}"), file=out)
    if d.get("judge_reason"):
        print(safe(f"judge: {d.get('judge_reason')}"), file=out)


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


def _ask(stdin, out):
    """One verdict letter from the user; EOF is `q`, and an unknown answer asks again."""
    while True:
        print("[r]ight [w]rong [s]kip [q]uit> ", end="", file=out, flush=True)
        line = stdin.readline()
        if line == "":  # EOF behaves like q; a blank line is just a line
            return "q"
        ans = VERDICTS.get(line.strip().lower())
        if ans:
            return ans
        print("", file=out)


def remark(state_dir, decision_id, stdin=None, out=None):
    """`bw guard review --remark <id>`: mark an already marked decision again, to correct a wrong mark.
    The new mark replaces the old one (load() folds the latest), keeps its `sampled` flag, and counts
    as a new mark. The gate reads it at the next lead start."""
    stdin, out = stdin or sys.stdin, out or sys.stdout
    d = next((d for d in real(record.load(state_dir)["decisions"]) if d.get("id") == decision_id), None)
    if d is None:
        print(safe(f"bw: no guard decision {decision_id}"), file=sys.stderr)
        return 2
    if "mark" not in d:
        print(safe(f"bw: decision {decision_id} has no mark yet; run `bw guard review`"), file=sys.stderr)
        return 2
    key = record.evidence_key(d)
    print(safe(f"re-marking a decision under {key_label(key)}; its mark is {d['mark'].get('verdict')}"), file=out)
    sampled = bool(d["mark"].get("sampled"))
    show(dict(d, sampled=sampled), out, 1, 1)
    ans = _ask(stdin, out)
    print("", file=out)
    if ans in ("r", "w"):
        record.append(state_dir, {"type": "mark", "decision_id": d["id"], "verdict": "right" if ans == "r" else "wrong",
                                  "sampled": sampled, "remark": True})
    else:
        print("not re-marked", file=out)
    print_summary(record.load(state_dir)["decisions"], key, out)
    return 0


def run(state_dir, profile=None, recent=20, sample=5, list_only=False, stdin=None, out=None, rng=None,
        items=None, key=None):
    """`items` (with `key`) reviews exactly those decisions under that evidence key: the start offer."""
    stdin, out = stdin or sys.stdin, out or sys.stdout
    rng = rng or random.SystemRandom()
    loaded = record.load(state_dir)
    for m in loaded["orphan_marks"]:
        print(f"bw: ignoring an orphan mark (no decision {m.get('decision_id')})", file=sys.stderr)
    for w in loaded["warnings"]:
        print(f"bw: guard log: {w}", file=sys.stderr)
    if items is None:
        key = select_key(loaded["decisions"], profile)
    if key is None:
        print("bw: no guard decisions to review" + (f" for profile {profile}" if profile else ""), file=out)
        return 0
    print(safe(f"reviewing {key_label(key)}"), file=out)
    if items is None:
        items = pick(loaded["decisions"], key, recent, sample, rng)
    for i, d in enumerate(items, 1):
        show(d, out, i, len(items))
        if list_only:
            continue
        ans = _ask(stdin, out)
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


def status_lines(config, state_dir, now):
    """`bw status`'s guard lines: each evidence key with the gate's conditions, each stored mode with
    the last demotion's reason and what re-promotion needs, and a note when no judge is set."""
    from lead.guard import gate  # lazy: gate imports this module
    decisions = record.load(state_dir)["decisions"]
    profiles = config.get("profiles") or {}
    lines = []
    for key in keys(decisions):
        ds = of_key(decisions, key)
        s = summary(decisions, key)
        conds = None
        if key[0] in profiles and tuple(key) == gate.current_key(config, key[0], key[1]):
            conds = gate.conditions(config, state_dir, key[0], key[1], None, now, decisions=decisions)
        lines.append(safe(f"  {key[1]} (profile {key[0]}) · {ds[-1].get('mode')} · {s['decisions']} decisions · "
                          f"gray {s['gray_share'] * 100:.0f}% · {promotion_line(s, conds)}"))
    modes = read_json(gate.path(state_dir), {})
    for pname, by_rt in sorted((modes if isinstance(modes, dict) else {}).items()):
        for rt, entry in sorted((by_rt if isinstance(by_rt, dict) else {}).items()):
            if not isinstance(entry, dict) or entry.get("mode") not in ("acting", "shadow"):
                continue
            head = f"  guard {entry['mode']} for profile {pname} ({rt}) since {gate.day(entry.get('since') or 0)}"
            dem = (entry.get("demotions") or [None])[-1]
            if entry["mode"] == "shadow" and dem and dem.get("ts") == entry.get("since"):
                need = gate.requirement(dem)
                head += f": {dem.get('reason')}" + (f"; {need}" if need else "")
            elif entry.get("reason"):
                head += f": {entry['reason']}"
            lines.append(safe(head))
    pinned = any(m in ("auto", "acting") for m in ((config.get("guard") or {}).get("mode") or {}).values())
    if (lines or pinned) and not (config.get("guard") or {}).get("model"):
        lines.append("  no judge: gray calls are recorded as asks, nothing is stopped; the guard stays shadow "
                     "(set guard.model to let it act)")
    return lines
