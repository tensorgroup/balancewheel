"""verify.py — `bw sandbox verify` and `bw guard verify`: run the attack, record a pass on the key attacked.

Each command computes the runtime's verification key, runs its attack script, and recomputes the
key: a pass is recorded only when the two keys are identical, so a tool that updated while the
attack ran never gets a pass for a version nobody attacked. Key → attack → key → record runs under
a shared lock on the runtime (`lead.locks`), so no update of any tool mapped to it lands meanwhile
(an updater finds it held and skips); `reverify` holds it from its first key read. Every shell-out goes through `run`
(default `lead.proc.run`). Each returns (ok, message); the message is the script's output, then
one `bw:` line saying what was recorded or why not.

`reverify` re-checks one runtime's keys after an update phase (or at once with `--no-update`):
an item whose key still matches is `verified`; one that cannot be tested now is `pending` with
its reason; anything else runs the attack and is `passed` or `failed`.
"""
import os
import time
from collections import namedtuple

from . import containment, guard as guardpkg, proc, quota, sandbox
from .runner import _guarded
from .guard import verify as guard_judge
from .locks import RuntimeLock
from .paths import ROOT
from .runtimes import ADAPTERS

ATTACK_TIMEOUT = 900
SANDBOX_FAIL_NAMES = ("too-few-os-denials", "inside-write-or-commit-failed", "git-control-changed", "outside-written",
                      "tmp-written", "preference-written", "canvas-port", "ccswitch-db-read")
REFUSAL_ONLY = {"too-few-os-denials", "inside-write-or-commit-failed"}  # what a model that refused leaves behind
KEY_CHANGED = "failed: key changed during the attack"


def sandbox_failure_message(stdout):
    """What `bw sandbox verify` says when lead_containment.sh did not print PASS."""
    names = set()
    for line in stdout.splitlines():
        if line.startswith("FAIL:"):
            body = line[len("FAIL:"):].split(" — evidence in")[0]
            names = {t.split(":", 1)[0] for t in body.split()} & set(SANDBOX_FAIL_NAMES)
            break
    if names and names <= REFUSAL_ONLY:
        return "not recorded — a refusal by the model is inconclusive; re-run"
    return "not recorded — the containment attack failed; see the evidence"


def _lead(config, runtime, lead_name):
    return next((l for n, l in config["leads"].items() if l["runtime"] == runtime and (not lead_name or n == lead_name)),
                None)


def _attack(state, run, argv, key_fn, record, failure_message, what, runtime, which):
    lock = RuntimeLock(state, runtime)
    try:
        lock.shared()  # waits for an update in progress; holds off any other until the record
        return _attack_locked(run, argv, key_fn, record, failure_message, what, runtime, which)
    finally:
        lock.release()


def _attack_locked(run, argv, key_fn, record, failure_message, what, runtime, which):
    before = key_fn()
    r = run(argv, ATTACK_TIMEOUT)
    out = r.out.strip()
    head = out + "\n" if out else ""
    if r.timed_out:
        return False, head + f"bw: not recorded — the {what} attack timed out after {ATTACK_TIMEOUT} s"
    if r.rc != 0 or not r.out.startswith("PASS"):
        return False, head + "bw: " + failure_message(r.out)
    if key_fn() != before:
        return False, head + "bw: " + KEY_CHANGED
    record(before, r.out.split("evidence in ")[-1].strip())
    return True, head + f"bw: recorded a {what} pass for {runtime} on {which}"


def sandbox_verify(config, state, runtime, lead_name=None, run=proc.run):
    ad = ADAPTERS.get(runtime)
    if not ad or ad.contained_by != "bw":
        return False, f"bw: {runtime} is not contained by bw (contained_by={ad.contained_by if ad else 'unknown'})"
    lead = _lead(config, runtime, lead_name)
    if not lead:
        return False, f"bw: no lead in the config uses runtime {runtime}"
    which = sandbox.backend()
    ports = sandbox.denied_ports(config)
    script = os.path.join(ROOT, "tests", "attack", "lead_containment.sh")
    deny = [a for p in ports for a in ("--deny-port", str(p))]
    argv = [script, "--backend", which, *deny, *ad.verify_flags(lead), "--", *ad.verify_argv(lead)]
    return _attack(state, run, argv, lambda: containment.verification_key(runtime, ad.version_argv, which, run=run,
                                                                         ports=ports),
                   lambda key, ev: containment.record_pass(state, runtime, key, ev),
                   sandbox_failure_message, "containment", runtime, which)


def guard_verify(config, state, runtime, lead_name=None, run=proc.run):
    ad = ADAPTERS.get(runtime)
    if ad is None or ad.guard_argv is None:
        return False, f"bw: no guard attack for {runtime}"
    if runtime == "agy" and not guardpkg.AGY_SPIKE_PASSED:
        return False, f"bw: {guardpkg.AGY_AWAITS}"
    lead = _lead(config, runtime, lead_name)
    if not lead:
        return False, f"bw: no lead in the config uses runtime {runtime}"
    which = sandbox.backend()
    argv = guard_judge.script_argv(ROOT, runtime, lead, which, "{client}")
    ports = sandbox.denied_ports(config)
    return _attack(state, run, argv, lambda: containment.guard_key(runtime, ad.version_argv, which, ad.guard_hook_files(),
                                                                  run=run, ports=ports),
                   lambda key, ev: containment.record_pass(state, runtime, key, ev, store="guard-verify.json"),
                   guard_judge.failure_message, "guard", runtime, which)


Item = namedtuple("Item", "runtime kind state reason")  # kind: containment | guard
ITEM_STATES = ("verified", "passed", "failed", "pending")


def _preferred_leads(config):
    """Lead names, those in any profile's lead_order first (in that order), then the rest."""
    order = []
    for profile in (config.get("profiles") or {}).values():
        for name in profile.get("lead_order", []):
            if name in config["leads"] and name not in order:
                order.append(name)
    return order + [n for n in config["leads"] if n not in order]


def lead_runtimes(config) -> list:
    out = []
    for lead in config["leads"].values():
        if lead["runtime"] not in out:
            out.append(lead["runtime"])
    return out


def items_for_runtime(config, runtime) -> list:
    """(kind, lead_name) for each key this runtime has to keep verified."""
    ad = ADAPTERS.get(runtime)
    names = [n for n in _preferred_leads(config) if config["leads"][n]["runtime"] == runtime]
    if ad is None or not names:
        return []
    out = []
    if ad.contained_by == "bw":
        out.append(("containment", names[0]))
    guarded = [n for n in names if _guarded(config["leads"][n])]
    if guarded and ad.guard_argv is not None:
        out.append(("guard", guarded[0]))
    return out


def _pending_reason(config, state, runtime, kind, lead_name, now, allow_run, version_ok):
    if not allow_run:
        return "--no-verify"
    if not version_ok:
        return "version unreadable"
    plan = config["leads"][lead_name].get("plan")
    cap = quota.read_caps(state, now).get(plan) if plan else None
    if cap:
        return f"plan {plan} capped until {time.ctime(cap['resets_at'])}"
    if kind == "guard" and runtime == "agy" and not guardpkg.AGY_SPIKE_PASSED:
        return guardpkg.AGY_AWAITS
    return None


def reverify(config, state, runtime, now, run=proc.run, allow_run=True, version_ok=True) -> list:
    if not items_for_runtime(config, runtime):
        return []
    lock = RuntimeLock(state, runtime)
    try:
        lock.shared()  # the key read here is the key attacked and recorded (the attack's own is nested)
        return _reverify(config, state, runtime, now, run, allow_run, version_ok)
    finally:
        lock.release()


def _reverify(config, state, runtime, now, run, allow_run, version_ok):
    ad = ADAPTERS.get(runtime)
    items = []
    for kind, lead_name in items_for_runtime(config, runtime):
        which = sandbox.backend()
        if kind == "containment":
            key, store, attack = containment.verification_key(runtime, ad.version_argv, which, run=run,
                                                              ports=sandbox.denied_ports(config)), \
                "containment.json", sandbox_verify
        else:
            key, store, attack = containment.guard_key(runtime, ad.version_argv, which, ad.guard_hook_files(), run=run,
                                                       ports=sandbox.denied_ports(config)), \
                "guard-verify.json", guard_verify
        if containment.is_verified(state, runtime, key, store=store):
            items.append(Item(runtime, kind, "verified", "key matches the recorded pass"))
            continue
        why = _pending_reason(config, state, runtime, kind, lead_name, now, allow_run, version_ok)
        if why:
            items.append(Item(runtime, kind, "pending", why))
            continue
        ok, msg = attack(config, state, runtime, lead_name=lead_name, run=run)
        items.append(Item(runtime, kind, "passed" if ok else "failed", msg))
    return items
