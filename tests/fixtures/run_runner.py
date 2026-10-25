"""Drive lead.runner.run with fake adapters (tests only).

argv: run_runner.py <config.json> <worktree> [--use LEAD] [--auto] [--contained-by-bw RUNTIME]
Every runtime maps to tests/fixtures/fake_lead.py; --contained-by-bw marks one as bw-contained
(without a containment record), to test the refusal path.
"""
import os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from lead import runner  # noqa: E402
from lead.paths import load_config  # noqa: E402
from lead.runtimes import Adapter  # noqa: E402
from lead.sandbox import RuntimeGrants  # noqa: E402

args = sys.argv[1:]
cfg_path, worktree = args[0], args[1]
use = args[args.index("--use") + 1] if "--use" in args else None
auto = "--auto" in args
bw_rt = args[args.index("--contained-by-bw") + 1] if "--contained-by-bw" in args else None


def fake(rt):
    return Adapter(
        argv=lambda lead, prompt, writable=(): [sys.executable, str(HERE / "fake_lead.py"), lead["model"]] + ([prompt] if prompt else []),
        contained_by="bw" if rt == bw_rt else "self",
        grants=lambda lead: RuntimeGrants([], [], {}, False), prompt_mode="arg",
        version_argv=[sys.executable, "--version"], verify_argv=None, verify_flags=lambda lead: [])


adapters = {rt: fake(rt) for rt in ("claude-code", "codex", "agy", "pi", "kimi")}
config = load_config(cfg_path)
sys.exit(runner.run(config, config["state_dir"], worktree, use=use, auto=auto, rating=False,
                    adapters=adapters, backend="sandbox-exec"))
