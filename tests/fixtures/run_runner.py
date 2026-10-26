"""Drive lead.runner.run with fake adapters (tests only).

argv: run_runner.py <config.json> <worktree> [--use LEAD] [--auto] [--contained-by-bw RUNTIME]
                    [--guard RUNTIME [--no-hook] [--no-spike]] [--fail-after-launch] [--no-client] [--fail-in-poll]
                    [--verified] [--fail-in-caps] [--probe-lock-in-verify] [--orca-payload FILE] [--orca-raise]
                    [--count-refresh]
Every runtime maps to tests/fixtures/fake_lead.py; --contained-by-bw marks one as bw-contained
(without a containment record), to test the refusal path. --guard RUNTIME makes every lead of that
runtime guarded (autonomy "guard", source "config"), points BW_AGY_HOOKS at a temp hooks file with
the entry installed (--no-hook: an empty one) and marks agy's hook spike as passed (--no-spike: not).
--no-client points the session at an empty client source dir; --fail-in-poll makes the first poll_guard raise.
--fail-after-launch makes the first give_terminal (called right after the lead is forked) raise.
--verified counts every runtime's containment as verified (the bw-contained one gets FAKE_OUT as a write
grant, so the fake can leave its evidence); each per-session scratch dir is printed as `SCRATCH <path>`.
--fail-in-caps makes every read_caps after the lead started wait for the lead to exit, then raise.
--probe-lock-in-verify makes each _verified call try the lead's runtime lock exclusively from a second
process, append {"runtime", "exclusive"} to FAKE_OUT/verify-lock.jsonl, and count the lead as verified.
--orca-payload FILE answers every `orca ...` shell-out with FILE's content (each argv appended to
FAKE_OUT/orca-calls.jsonl) and reports Orca's version as 9.9.9-test; without it any `orca` call fails.
--orca-raise makes every `orca ...` shell-out (still appended to orca-calls.jsonl) raise OSError.
--count-refresh appends one line to FAKE_OUT/refresh-calls.jsonl per probes.refresh call (which still runs).
"""
import atexit, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from lead import quota, runner  # noqa: E402
import lead.guard  # noqa: E402
from lead.guard import install  # noqa: E402
from lead.paths import load_config  # noqa: E402
from lead.runtimes import Adapter  # noqa: E402
from lead.sandbox import RuntimeGrants  # noqa: E402

args = sys.argv[1:]
cfg_path, worktree = args[0], args[1]
use = args[args.index("--use") + 1] if "--use" in args else None
auto = "--auto" in args
bw_rt = args[args.index("--contained-by-bw") + 1] if "--contained-by-bw" in args else None
guard_rt = args[args.index("--guard") + 1] if "--guard" in args else None


def fake(rt):
    return Adapter(
        argv=lambda lead, prompt, writable=(), client_dir=None: [sys.executable, str(HERE / "fake_lead.py"), lead["model"]] + ([prompt] if prompt else []),
        contained_by="bw" if rt == bw_rt else "self",
        grants=lambda lead, client_dir=None: RuntimeGrants([], [os.environ["FAKE_OUT"]] if rt == bw_rt else [], {}, False),
        prompt_mode="arg",
        version_argv=[sys.executable, "--version"], verify_argv=None, verify_flags=lambda lead: [])


adapters = {rt: fake(rt) for rt in ("claude-code", "codex", "agy", "pi", "kimi")}
config = load_config(cfg_path)
if guard_rt:
    for ld in config["leads"].values():
        if ld["runtime"] == guard_rt:
            ld["autonomy"], ld["autonomy_source"] = "guard", "config"
    hooks_dir = tempfile.mkdtemp(prefix="bw-hooks-")
    atexit.register(shutil.rmtree, hooks_dir, ignore_errors=True)
    os.environ["BW_AGY_HOOKS"] = os.path.join(hooks_dir, "hooks.json")
    if "--no-hook" not in args:
        install.install_agy(os.environ["BW_AGY_HOOKS"], config["state_dir"])
    lead.guard.AGY_SPIKE_PASSED = "--no-spike" not in args
if "--fail-after-launch" in args:
    def _boom(self, pgid):
        with open(os.path.join(os.environ["FAKE_OUT"], "launched.pgid"), "w") as f:
            f.write(str(pgid))
        raise RuntimeError("injected failure after launch")
    runner.Context.give_terminal = _boom
if "--no-client" in args:
    from lead.guard import launch
    launch.HERE = tempfile.mkdtemp(prefix="bw-noclient-")
    atexit.register(shutil.rmtree, launch.HERE, ignore_errors=True)
if "--fail-in-poll" in args:
    def _poll(self, ctx):
        raise RuntimeError("injected failure in the poll loop")
    runner.Session.poll_guard = _poll
if "--verified" in args:
    runner._verified = lambda ctx, lead_name: True
    tempfile.tempdir = None  # honour TMPDIR from the environment
    _mkdtemp = tempfile.mkdtemp

    def _logged_mkdtemp(*a, **k):
        d = _mkdtemp(*a, **k)
        if k.get("prefix") == "bw-lead-":
            print(f"SCRATCH {d}", flush=True)
        return d
    tempfile.mkdtemp = _logged_mkdtemp
if "--probe-lock-in-verify" in args:
    import json, subprocess

    def _probe_verified(ctx, lead_name):
        rt = ctx.config["leads"][lead_name]["runtime"]
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from lead.locks import RuntimeLock; "
                "print(RuntimeLock(sys.argv[2], sys.argv[3]).try_exclusive())")
        r = subprocess.run([sys.executable, "-c", code, str(ROOT), ctx.state, rt], capture_output=True, text=True)
        with open(os.path.join(os.environ["FAKE_OUT"], "verify-lock.jsonl"), "a") as f:
            f.write(json.dumps({"runtime": rt, "exclusive": r.stdout.strip()}) + "\n")
        return True
    runner._verified = _probe_verified
if "--fail-in-caps" in args:
    sessions = []
    _init, _read_caps = runner.Session.__init__, quota.read_caps

    def _session_init(self, *a, **k):
        sessions.append(self)
        _init(self, *a, **k)

    def _caps(*a, **k):
        if not sessions:
            return _read_caps(*a, **k)
        sessions[-1].proc.wait()  # the lead has exited and its group is gone
        raise RuntimeError("injected failure after the lead exited")
    runner.Session.__init__ = _session_init
    quota.read_caps = _caps
from lead import orca  # noqa: E402
from lead.proc import Result, run as real_run  # noqa: E402

orca.app_version = lambda *a, **k: "9.9.9-test"  # never the real app bundle
orca_payload = args[args.index("--orca-payload") + 1] if "--orca-payload" in args else None
orca_raise = "--orca-raise" in args

if "--count-refresh" in args:
    import json
    from lead import probes
    _refresh = probes.refresh

    def _counted(*a, **k):
        with open(os.path.join(os.environ["FAKE_OUT"], "refresh-calls.jsonl"), "a") as f:
            f.write(json.dumps({"plans": k.get("plans")}) + "\n")
        return _refresh(*a, **k)
    probes.refresh = _counted


def fake_run(argv, timeout, **kw):
    if argv[:1] != ["orca"]:
        return real_run(argv, timeout, **kw)
    if orca_payload is None and not orca_raise:
        return Result(127, "", "orca: not found", False)
    import json
    with open(os.path.join(os.environ["FAKE_OUT"], "orca-calls.jsonl"), "a") as f:
        f.write(json.dumps({"argv": argv, "timeout": timeout}) + "\n")
    if orca_raise:
        raise OSError("injected failure reading Orca")
    with open(orca_payload) as f:
        return Result(0, f.read(), "", False)


sys.exit(runner.run(config, config["state_dir"], worktree, use=use, auto=auto, rating=False,
                    adapters=adapters, backend="sandbox-exec", run=fake_run))
