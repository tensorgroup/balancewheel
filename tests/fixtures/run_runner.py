"""Drive lead.runner.run with fake adapters (tests only).

argv: run_runner.py <config.json> <worktree> [--use LEAD] [--auto] [--contained-by-bw RUNTIME]
                    [--guard RUNTIME [--no-hook] [--no-spike]] [--fail-after-launch]
Every runtime maps to tests/fixtures/fake_lead.py; --contained-by-bw marks one as bw-contained
(without a containment record), to test the refusal path. --guard RUNTIME makes every lead of that
runtime guarded (autonomy "guard", source "config"), points BW_AGY_HOOKS at a temp hooks file with
the entry installed (--no-hook: an empty one) and marks agy's hook spike as passed (--no-spike: not).
--fail-after-launch makes the first give_terminal (called right after the lead is forked) raise.
"""
import atexit, os, shutil, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from lead import runner  # noqa: E402
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
        grants=lambda lead, client_dir=None: RuntimeGrants([], [], {}, False), prompt_mode="arg",
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
sys.exit(runner.run(config, config["state_dir"], worktree, use=use, auto=auto, rating=False,
                    adapters=adapters, backend="sandbox-exec"))
