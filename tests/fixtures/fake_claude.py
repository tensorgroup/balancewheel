"""A stand-in `claude` for the live pane check (tests only; synthetic). Never touches a real Claude config.

argv: fake_claude.py <claude args>...
Appends its args as one JSON line to $FAKE_CLAUDE_LOG (when set). Answers `--version`, `plugin list
--json`, and `plugin marketplace list --json` from $FAKE_CLAUDE_STATE, a JSON file
{"version": "<text>", "plugins": [...], "markets": [...]}; without that file the state is the plugin
installed, current, and enabled from a `balancewheel` marketplace at this checkout. Every other call
changes nothing and exits 0. The list shapes are the ones Claude Code 2.1.332 prints.
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
sys.path.insert(0, ROOT)
from lead.version import VERSION  # noqa: E402

args = sys.argv[1:]
log = os.environ.get("FAKE_CLAUDE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\n")
state = {"version": "2.1.332 (Claude Code)",
         "plugins": [{"id": "live-pane@balancewheel", "version": VERSION, "scope": "user", "enabled": True}],
         "markets": [{"name": "balancewheel", "source": "directory", "path": ROOT, "installLocation": ROOT}]}
path = os.environ.get("FAKE_CLAUDE_STATE")
if path:
    with open(path) as f:
        state.update(json.load(f))
if args == ["--version"]:
    print(state["version"])
elif args == ["plugin", "list", "--json"]:
    print(json.dumps(state["plugins"], indent=2))
elif args == ["plugin", "marketplace", "list", "--json"]:
    print(json.dumps(state["markets"], indent=2))
sys.exit(0)
