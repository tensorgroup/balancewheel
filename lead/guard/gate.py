"""gate.py — the guard's mode at lead start: shadow or acting, decided by bw from recorded evidence.

The profile's pin (`guard.mode`) says what may happen. `shadow` never promotes. `auto` promotes to
acting when every condition holds. `acting` promotes the same way, and refuses to start a lead whose
guard key has no attack pass. Once acting, bw demotes at a later start when a trigger holds.

The evidence is the user's marks (`bw guard review`), the eval (`bw guard eval`), the attack pass
(`bw guard verify`), and the interactive check. Nothing here calls a model or a runtime: the start
path stays fast. Only bw writes `state_dir/guard-mode.json`, under an flock.
"""
import fcntl
import hashlib
import json
import os
import time
from collections import namedtuple

from .. import containment
from ..paths import read_json, write_json_atomic
from ..runtimes import ADAPTERS
from . import evaluate, interactive, judge, record, review, rules_version

FILE = "guard-mode.json"
VERIFY_STORE = "guard-verify.json"
MIN_JUDGE_ALLOWS = 5  # reviewed sampled allows that the judge decided, when a judge model is set
COOLDOWN_S = 7 * 86400
MIN_NEW_MARKS = 5
KEEP_DEMOTIONS = 10
NO_JUDGE = "no judge: stays shadow"

Condition = namedtuple("Condition", "name ok detail")
Outcome = namedtuple("Outcome", "mode refuse line event")


def path(state):
    return os.path.join(state, FILE)


def pin(config, profile):
    return ((config.get("guard") or {}).get("mode") or {}).get(profile, "shadow")


def current_key(config, profile, runtime):
    """The evidence key a session of this profile and runtime writes now (record.EVIDENCE_KEY order)."""
    prof = (config.get("profiles") or {}).get(profile) or {}
    model = (config.get("guard") or {}).get("model")
    return (profile, runtime, rules_version(),
            record.policy_digest(prof.get("protected_branches"), prof.get("deploy_commands")),
            model, judge.prompt_hash() if model else None)


def eval_key(config, runtime):
    return evaluate.eval_key(runtime, config.get("guard") or {}, evaluate.load_set()[1])


def verify_sha(verify_key):
    return hashlib.sha256(json.dumps(verify_key, sort_keys=True).encode()).hexdigest()


def _verified(state, runtime, verify_key):
    return containment.is_verified(state, runtime, verify_key, store=VERIFY_STORE)


def read(state, profile, runtime):
    data = read_json(path(state), {})
    entry = ((data if isinstance(data, dict) else {}).get(profile) or {})
    entry = entry.get(runtime) if isinstance(entry, dict) else None
    return entry if isinstance(entry, dict) else None


def _write(state, profile, runtime, entry):
    data = read_json(path(state), {})
    data = data if isinstance(data, dict) else {}
    prof = data.get(profile) if isinstance(data.get(profile), dict) else {}
    prof[runtime] = entry
    data[profile] = prof
    write_json_atomic(path(state), data)


def _lock(state):
    os.makedirs(state, exist_ok=True)
    fd = os.open(path(state) + ".lock", os.O_RDONLY | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _rate(s):
    return s["false_stops"] / s["stops_reviewed"] if s["stops_reviewed"] else 0.0


def last_evidence_demotion(entry, key):
    for d in reversed((entry or {}).get("demotions") or []):
        if d.get("kind") == "evidence" and tuple(d.get("key") or ()) == tuple(key):
            return d
    return None


def day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def conditions(config, state, profile, runtime, verify_key, now):
    """[Condition], in gate order. A verify_key of None leaves `attack` unknown (ok None): status
    cannot compute the guard key without running the runtime, and None never promotes."""
    ekey = current_key(config, profile, runtime)
    model = (config.get("guard") or {}).get("model")
    decisions = record.load(state)["decisions"]
    entry = read(state, profile, runtime)
    dem = last_evidence_demotion(entry, ekey)
    s = review.summary(decisions, ekey, since=dem["ts"] if dem else None)
    p = pin(config, profile)
    out = [Condition("mode", p in ("auto", "acting"), f"pinned {p}")]
    rate = _rate(s)
    marks_ok = (s["reviewed"] >= review.MIN_REVIEWED and s["sampled_allows"] >= review.MIN_SAMPLED
                and rate < review.MAX_FALSE_STOP and s["false_allows"] == 0
                and (not model or s["judge_sampled_allows"] >= MIN_JUDGE_ALLOWS))
    detail = (f"{s['reviewed']}/{review.MIN_REVIEWED} reviewed, {s['sampled_allows']}/{review.MIN_SAMPLED} sampled "
              f"allows, {rate * 100:.0f}% false stops, {s['false_allows']} false allows")
    if model:
        detail += f", {s['judge_sampled_allows']}/{MIN_JUDGE_ALLOWS} judge allows"
    out.append(Condition("marks", marks_ok, detail))
    out.append(Condition("judge", bool(model), "judge model set" if model else NO_JUDGE))
    res = evaluate.current(state, runtime, eval_key(config, runtime))
    status = res.get("status") if isinstance(res, dict) else None
    out.append(Condition("eval", status == "pass", status or "not run on this key"))
    if verify_key is None:
        out.append(Condition("attack", None, "not checked here; see `bw guard verify`"))
    else:
        ok = _verified(state, runtime, verify_key)
        out.append(Condition("attack", ok, "pass on this guard key" if ok else "no pass on this guard key"))
    ok = runtime in ADAPTERS and interactive.is_checked(state, runtime, interactive.check_key(runtime, ADAPTERS[runtime]))
    out.append(Condition("interactive", ok, "checked" if ok else "no interactive check on this key"))
    if dem is None:
        out.append(Condition("cooldown", True, "no evidence demotion"))
    else:
        waited = now - dem["ts"] >= COOLDOWN_S
        new = s["marked_since"] >= MIN_NEW_MARKS
        out.append(Condition("cooldown", waited and new,
                             f"after {day(dem['ts'] + COOLDOWN_S)} and {MIN_NEW_MARKS} new marks "
                             f"({s['marked_since']} so far)"))
    return out


def _evidence(s):
    return {"reviewed": s["reviewed"], "sampled_allows": s["sampled_allows"],
            "false_stop_pct": round(_rate(s) * 100, 1), "false_allows": s["false_allows"]}


def _event(profile, runtime, frm, to, reason, s):
    return {"type": "guard_mode", "profile": profile, "runtime": runtime, "from": frm, "to": to,
            "reason": reason, "evidence": _evidence(s)}


def _open(conds):
    return ", ".join(f"{c.name} ({c.detail})" for c in conds if not c.ok)


def refuses(config, state, profile, runtime, verify_key):
    """The exit-7 reason for a profile pinned `acting` whose guard key has no attack pass, else None."""
    if pin(config, profile) != "acting" or _verified(state, runtime, verify_key):
        return None
    return (f"guard mode is pinned acting for profile {profile}, but the {runtime} guard key is unverified; "
            f"run `bw guard verify {runtime}`")


def _trigger(config, state, profile, runtime, verify_key, entry, ekey, s):
    """(reason, kind) of the first demotion trigger that holds for an acting entry, else None."""
    if tuple((entry.get("key") or {}).get("evidence") or ()) != ekey:
        return "the evidence key changed", "key"
    if not _verified(state, runtime, verify_key):
        return "guard key unverified", "key"
    if s["false_allows"]:
        return "a sampled allow was marked wrong", "evidence"
    ekey_eval = eval_key(config, runtime)
    res = evaluate.current(state, runtime, ekey_eval)
    status = res.get("status") if isinstance(res, dict) else None
    if status == "fail" or (status == "incomplete" and not evaluate.passed_on(state, runtime, ekey_eval)):
        return f"the eval is {status} on its current key", "evidence"
    if s["stops_reviewed"] and _rate(s) >= review.MAX_FALSE_STOP:
        return f"false stops reached {_rate(s) * 100:.0f}% of reviewed stops", "evidence"
    return None


def decide(config, state, profile, runtime, verify_key, now):
    """Outcome(mode, refuse, line, event) for a lead starting now. Writes guard-mode.json on a change."""
    p = pin(config, profile)
    fd = _lock(state)
    try:
        entry = read(state, profile, runtime)
        stored = entry.get("mode") if entry else "shadow"
        ekey = current_key(config, profile, runtime)
        s = review.summary(record.load(state)["decisions"], ekey)
        if p not in ("auto", "acting"):  # shadow (or anything else): never acting
            if stored != "acting":
                return Outcome("shadow", False, None, None)
            entry = dict(entry, mode="shadow", since=now, reason="pinned shadow")
            _write(state, profile, runtime, entry)
            return Outcome("shadow", False, f"bw: guard shadow for profile {profile} ({runtime}): pinned shadow",
                           _event(profile, runtime, "acting", "shadow", "pinned shadow", s))
        why = refuses(config, state, profile, runtime, verify_key)
        if why:
            return Outcome("shadow", True, why, None)
        vsha = verify_sha(verify_key)
        if stored == "acting":
            hit = _trigger(config, state, profile, runtime, verify_key, entry, ekey, s)
            if hit is None:
                if (entry.get("key") or {}).get("verify") != vsha:  # a verified runtime upgrade: keep acting
                    _write(state, profile, runtime, dict(entry, key={"evidence": list(ekey), "verify": vsha}))
                return Outcome("acting", False, None, None)
            reason, kind = hit
            dems = list(entry.get("demotions") or [])
            dems.append({"key": list((entry.get("key") or {}).get("evidence") or ekey), "ts": now,
                         "reason": reason, "kind": kind})
            _write(state, profile, runtime, dict(entry, mode="shadow", since=now, reason=reason,
                                                  demotions=dems[-KEEP_DEMOTIONS:]))
            line = f"bw: guard shadow for profile {profile} ({runtime}): {reason}"
            return Outcome("shadow", False, line, _event(profile, runtime, "acting", "shadow", reason, s))
        conds = conditions(config, state, profile, runtime, verify_key, now)
        if all(c.ok for c in conds):
            reason = "the gate held"
            base = entry or {"demotions": []}
            _write(state, profile, runtime, dict(base, mode="acting", key={"evidence": list(ekey), "verify": vsha},
                                                  since=now, reason=reason))
            line = (f"bw: guard acting for profile {profile} ({runtime}): {s['reviewed']} reviewed, "
                    f"{s['false_allows']} false allows, {_rate(s) * 100:.0f}% false stops")
            return Outcome("acting", False, line, _event(profile, runtime, stored or "shadow", "acting", reason, s))
        if not _verified(state, runtime, verify_key):
            return Outcome("shadow", False, f"bw: guard key unverified: shadow for profile {profile} ({runtime}); "
                                            f"run `bw guard verify {runtime}`", None)
        if p == "acting":
            return Outcome("shadow", False, f"bw: guard shadow for profile {profile} ({runtime}): acting is pinned, "
                                            f"but the gate is open: {_open(conds)}", None)
        return Outcome("shadow", False, None, None)
    finally:
        os.close(fd)
