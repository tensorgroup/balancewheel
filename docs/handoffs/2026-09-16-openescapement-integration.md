# Handoff — balancewheel and OpenEscapement, independently or together

*Session record, 2026-09-16. Point-in-time: where this disagrees with README.md or an
owning doc, the owning doc wins.*

## What changed and why

**The question.** How do balancewheel and OpenEscapement (`esc`) fit, given that both
seemed to write `CLAUDE.md`? Answer, now stated in both READMEs: they never write the
same file. Balancewheel's setup writes only under the home directory (the user-level
instruction file, wrappers, config, state; `setup/paths.txt` plus the state dir and
backups). `esc` writes agent-readable files only inside a repo, as a managed block in
that repo's instruction file plus its skills and MCP config. The overlap is semantic,
not on disk: an agent reads both files, so a pack rule and a moderator rule can disagree
on the same subject. Rule adopted: in a governed repo the pack wins; reconcile by
changing your own rule or opening a pack PR, never by editing the block (that reports as
drift, fails `esc status --check`, and sync refuses to overwrite it without `--force`).

**Independently or together.** Both READMEs now carry the same three paths: balancewheel
only (a panel for your own agent, no org policy), esc only (org rules in every repo, no
panel needed), both (the panel's log is the evidence, the pack is the delivery). Install
order balancewheel-then-esc is a recommendation, not a dependency; neither reads the
other's state.

**Stale claim removed.** Three balancewheel docs said esc ships a `model-seats` example
pack. It does not. A draft sits on esc's `feat/model-seats-pack` branch (2026-08-25) with
a catalog naming since-retired stealth model ids, which this repo's rules forbid in
interfaces. Docs said planned, not shipped, for most of the day; that evening the pack was written clean on esc's main (see Where to pick up, item 3), and the docs now say shipped.

**Model packs in esc.** `anthropic-models` refreshed to 0.2.0: Fable 5.1 preferred, Opus
4.8 the Opus-tier choice, Sonnet 5 at its now-permanent $2/$10, Opus 5 moved to
review-required with the reason and a measurable reversal condition (the balancewheel
rule that a policy departure must cite evidence and say what would reverse it, applied to
a vendor pack), Fable 5 to review-required as superseded.
New `openai-models` (GPT-6 Astra preferred for planning and review; GPT-5.6 Terra, Luna,
Sol allowed; GPT-5.2 banned) and `zai-models` (GLM 5.3 Flash via OpenRouter as the
near-free seat; its retired stealth id banned by name). Each pack was validated by
syncing it into a temporary repo with `esc sync` and `esc status --check`.

**Setup prompts now review and merge, not just back up.** The machine-setup prompt gained
a step: read every existing file on the paths list, produce a section-by-section merge
plan (keep, merge, drop, with reasons), recommend against anything that contradicts the
panel rules, wait for approval, then append balancewheel's rules as one delimited block
and apply only approved merges. The new-project prompt does the same for a directory
that already has instruction files, and leans on `esc init`'s own preservation of
everything outside the managed block.

**New-project prompt.** A second paste-in under Getting started, for a new repo:
machine-setup check, git init, `AGENTS.md` with `CLAUDE.md` importing it, `esc init
--yes` with `targets` set to `agents, skills, mcp`, a local-path starter pack with
`trust: unsigned` until a signed tag exists, the seat attack test fresh and resumed, one
panel at the design checkpoint, branch and PR. Mechanical steps are candidates for a
`setup/new-project.sh` once a real run shows which ones are actually mechanical.

## What cost time

- **"esc never writes under the home directory" was false.** Its pack cache lives in
  the OS cache directory and `esc serve` keeps data under `~/.escapement/server`. Three
  seats caught it; the wording now says esc never writes agent configuration.
- **The first draft of the new-project prompt had four steps that would not work.**
  `esc init` never prompts on a non-TTY or for uncommitted files; init pre-fills
  `targets` with only the detected files, so skills and MCP servers would never render;
  the GitHub Action runs in a bare checkout where a local sibling pack does not exist;
  a local pack needs `trust: unsigned`, a tag does nothing for it. All verified against
  esc's code and fixed.
- **YAML colons.** A catalog `notes` value with `: ` in it fails the pack parse. Quote
  it or rephrase.

## Deliberately left alone

- esc's embedded portal guidance (`internal/guidance/models/*.md`) still describes Opus
  5 as preferred and GPT-5.6 Sol as the OpenAI frontier, has no entry for Fable 5.1,
  Opus 4.8, or GPT-6 Astra, and prices Sonnet 5 at $3/$15 with an introductory rate it
  says expired 2026-08-31 (Anthropic made $2/$10 permanent in August). It now disagrees
  with the example packs on all of that and needs the same refresh, with sources; it
  has its own tests.
- esc's `feat/model-seats-pack` branch: superseded by the clean copy on main; not deleted here. Delete it when convenient.
- esc's README still says the repo is private; that paragraph goes when it opens.
- `CONTINUE.md` at esc's root: an untracked session note from an old cycle, not
  committed here.

## Where to pick up

1. Dogfood the new-project prompt on the next real project; step 8 collects what the
   docs missed. Decide from that run what becomes `setup/new-project.sh`.
2. ~~Refresh esc's portal guidance to match the packs.~~ Done later the same day (esc
   commit `025d624`): Fable 5.1, Opus 4.8, Astra added; Sonnet 5 price corrected.
3. ~~Re-pin and land `model-seats`; then flip "planned" to "shipped" in both repos.~~
   Done later the same day: written clean on esc's main with the catalog keyed by
   seat and the models as a dated roster; the branch can be deleted. What `packs/`
   still owes is the export step that regenerates the pack from the log.
