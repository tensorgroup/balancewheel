"""bw's version (PD-10). `claude/live-pane/.claude-plugin/plugin.json` and the `live-pane` entry of
`.claude-plugin/marketplace.json` carry the same string (tested). Bump the prerelease number in the same
commit as any change under `claude/live-pane/`: the bump changes the version that bw compares (it runs
`claude plugin update` when the installed one differs) and that Claude Code shows. It does not control
which files load: Claude Code reads the pane in place from bw's checkout at each session start."""
VERSION = "0.2.0-dev.5"
