# The stack — what we use, what we don't, and why

*Started 2026-10-25; revised 2026-10-25 for `bw guard` v0. One row per tool that has been used, planned, retired, or evaluated for
the reference setup, with the reason and what would change the call. Update it in the same
commit as any change of tool; date the row. Settings and boundaries for each adopted tool
live in the owning doc linked from its row (the 2026-10-24 spec's "Third-party tools"
table, `docs/orca.md`, the rebuild guide); this file owns only the decision and its why.*

**The OpenEscapement boundary applies to every row.** esc writes agent-readable files only
inside a repo (instructions, skills, rule packs); Balancewheel's setup writes only under the
home directory (`setup/paths.txt`). A tool is adopted only if it leaves both alone or is
configured to: Orca reads a repo's `.claude/`/`.codex/` config and, per its docs, leaves it and `CLAUDE.md`/`AGENTS.md` alone (its hooks
go to the home-level vendor files, which is why those are in `paths.txt`); cc-switch's sync
features would write both layers, so they stay off.

## In use

| Tool | Role | License | Since | Why this one |
|---|---|---|---|---|
| **Claude Code** | the primary agent: lead, panel moderator, and a blind fresh-context seat | proprietary | start | Strongest moderator in the logged panels; its statusline reports plan quota (the only documented first-party quota surface); its `auto` permission mode is a built-in classifier, so it needs no `bw guard`. |
| **Codex CLI** | the OpenAI seat (lead seat on plan/design targets) and a fallback lead | Apache-2.0 | start | Repo-aware, its own OS sandbox for read-only seats, flat-rate on the ChatGPT plan; `--approve-for-me` gives leads autonomy without prompts. |
| **pi** | runtime for metered seats (OpenRouter models) and a `bw sandbox` lead | MIT | 2026-09-03 | Headless, a tool allowlist plus an extension guard, JSON output with tokens and cost; replaced OpenCode (below). Must be installed with a manager that pins its own Node. |
| **agy** (Antigravity CLI) | the Gemini seat; a Claude lead billed to the Google plan | proprietary | 2026-10-24 | The only way to reach Gemini's top model and a non-Anthropic-quota Claude from a CLI. Its login is in the Keychain, so its seat uses the `sandbox-exec` backend. |
| **Kimi Code** | a review seat (not a lead) | proprietary | 2026-10-24 | A cheap fourth model family for review. Not a lead: hardest runtime to contain (ignores its own deny rules in `-p` mode) and its plan limits are unknown. |
| **OpenRouter** | metered models: the fast seat, the decision model, the planned `bw guard` judge | service | 2026-08 | One key for many open models at cents per call; a documented key endpoint for spend. |
| **nono** | default backend for `bw sandbox` and the OS-sandboxed seats | Apache-2.0 | 2026-10-24 | Kernel-enforced write *and* read allow-lists (blocks secret reads), passed every attack run; `sandbox-exec` (ships with macOS) is the zero-install fallback. |
| **Orca** | the recommended canvas: one pane per worktree running `bw lead`, notifications, a per-plan quota view whose snapshot feeds `quota: "orca"` | MIT | 2026-10-25 | Replaced nodeterm (below). Optional; plain terminal tabs work the same. Read `docs/orca.md` before installing: it edits vendor configs and its control channel must be denied in every sandbox (done in `lead/sandbox.py`). |
| **`bw guard`** (rules-only, shadow) | per-lead autonomy for runtimes with no native judge: a broker in `bw lead` with a thin pi extension or agy hook; logs what it would decide, blocks nothing | ours | 2026-10-25 | Built for pi and verified by attack on both sandbox backends. The agy side is built but refused until agy's hook behaviour is observed (a usage-capped spike). A model judge (v1) waits on a week of shadow data. Design in the 2026-10-25 spec. |
| git, `gh`, Python 3.9+ stdlib | everything else | — | start | No packages to install, by rule. |

## Planned, not installed yet

| Tool | Planned role | License | Why | Status |
|---|---|---|---|---|
| **cc-switch** | usage and cost for the user's own lead sessions, read from its SQLite (read-only) | MIT | It already scans the session logs of Claude Code, Codex, agy, Kimi Code, and pi into one table, with routing off — usage history we would otherwise have to parse per vendor. It is **not** the quota source: it keeps quota in memory only, with no CLI or API. Its config, prompt, MCP, and skill sync is **not** wanted: those files belong to OpenEscapement (repo level) and Balancewheel's setup (home level), and a third writer would break the rule that the two never write the same file. | Not installed; deferred to the cockpit's Usage view (build step 6), where it competes with Orca's own usage tracking (2026-10-25: Orca aggregates tokens and estimated cost for Claude Code, Codex, OpenCode, and Muse, but only in its UI, with no runtime RPC, and not for agy, Kimi, or pi) and with parsing session logs directly. If chosen, it runs read-only with routing, failover, aggregation, Codex provider management, and every sync **off**. |

## Retired

| Tool | Was | Until | Why we stopped |
|---|---|---|---|
| **nodeterm** | the live canvas `bw start` drove | 2026-10-25 | Orca does the same job and more under MIT: a worktree per task, a scriptable CLI, per-plan quota for most vendors, notifications, a mobile companion. nodeterm is BUSL-licensed (we could never depend on it) and covered less. It can still host `bw lead` in a terminal frame; its control CLI would need the same deny-and-attack treatment as Orca's before use. |
| **OpenCode** | runtime of the cheap OpenRouter seat | 2026-09-03 | 12–36-minute review runs; pi brought the median under five minutes, with a tool allowlist and an extension guard for read-only enforcement. Uninstalled. |
| **kimi-cli** (1.x) | an earlier Kimi runtime | 2026-09-03 | Superseded by Kimi Code 2.x. Uninstalled. |
| **tokscale** | usage tracking | 2026-10-24 | cc-switch's session-log usage covers it. |

## Evaluated, not adopted

| Tool | What it is | License | Why not (now) | Would revisit if |
|---|---|---|---|---|
| **herdr** | a terminal-native agent multiplexer (Rust): panes with working/blocked/idle status, detach and reattach, SSH hosts, a CLI and socket API | Apache-2.0 (earlier AGPL) | Orca already fills the canvas slot, and herdr has no quota view or mobile companion. Two canvases would mean two sets of vendor hooks and two control channels. Its strengths — terminal-only, SSH-native, no telemetry, hooks optional — matter for headless servers, which the reference setup doesn't use. Its pane socket (`~/.config/herdr/herdr.sock`) takes input and reads panes with no token, so `bw sandbox` denies it anyway, installed or not. | leads run on remote or headless machines; Orca's hook injection or telemetry becomes a problem; a user prefers a terminal over a desktop app. |
| **Orca orchestration, automations, `agent launch`** | Orca's own ways to start agents | MIT | They bypass bw's worktree lock and containment, and `agent launch` bypasses permissions by default. Only `orca terminal create --command "bw lead"` is used. | Orca grows a launch hook bw can own. |
| **AWS CLI Agent Orchestrator** | multi-agent message routing | Apache-2.0 | The lead relays claims itself, which keeps first rounds blind and relays claim-only. | relay becomes a bottleneck. |
| **ACP** (Agent Client Protocol) | a uniform way to drive any agent CLI | open spec | Each runtime's own headless mode is enough for now. | the number of runtime adapters makes per-vendor code the main cost. |
| **`codex sandbox` as the shared sandbox** | Codex's Rust Seatbelt wrapper | Apache-2.0 | Crashed Kimi Code's single-executable Node at startup. | — (nono replaced the need) |
| **A bw command judge on Claude Code and Codex** | `bw guard` stacked on their native modes | — | Both ship their own judge (`auto`, `--approve-for-me`); stacking adds latency for no new signal. `bw guard` is for agy and pi. | the native judges show misses in logged evidence. |
