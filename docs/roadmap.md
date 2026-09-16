# Roadmap — from private tooling to shareable project

## Now (dogfooding)

The reference implementation runs as private tooling (shell wrappers, Claude Code
commands, launchd sync, local dashboard). Goals of this phase:

- Accumulate real panel records on actual work (target: ≥10 logged disputes, ≥20
  panels) so the scoreboard means something.
- Let the seat-policy design settle (the substance bar and role scoping changed on
  day two — extraction before stability means maintaining an API for unfinished
  decisions).
- Watch vendor-CLI churn (Codex profile format, the OpenCode→pi runtime swap, a
  subscription route that exists only from a given pi version) to learn where the
  abstraction seams actually are.

## Extraction criteria (all must hold before code lands here)

1. Design unchanged for ~a month of real use.
2. The primary stealth model has graduated or been replaced — no interface may
   reference a preview model id. *(Met 2026-09-02: the stealth seat model was retired
   by the provider and re-pinned to its graduated catalog id in one line per component.
   See reference-implementation.md.)*
3. Clean-room rewrite: no personal paths, secrets layout, private sources, or
   employer/project references. Migration of existing files is forbidden (privacy
   firewall).
4. A second user (not the author) has run the setup from README alone.

## Planned shape

- `seats/` — wrapper generator: given a vendor CLI + model + read-only profile,
  emit the uniform `seat.sh` interface (new/resume, per-dir sessions, usage logging,
  scratch-dir hygiene).
- `panel/` — the protocol as instruction-file templates (moderator rules, substance
  bar, logging schema) rather than code: the moderator is whatever primary agent the
  user already runs.
- `metrics/` — JSONL schema, validator, dashboard generator.
- `setup/` — *(landed 2026-09-16, ahead of the gate: it is install hygiene, not the
  seat implementation)* the list of home-directory paths a setup touches, and the
  backup/restore scripts around it.
- `packs/` — seat policies exported as OpenEscapement rule packs (the governance
  hand-off: balancewheel generates and justifies policy; esc distributes it). The first
  pack, `model-seats`, shipped in esc's `examples/packs/` on 2026-09-16, written by hand
  from this repo's seat policy: duties per seat, the substance bar, wall-clock
  caps, mandatory logging, and a re-promotion criterion for any seat demoted on evidence, with the models behind
  the seats a dated catalog roster (the 2026-08-25 draft that named stealth ids was
  superseded). What `packs/` still owes is the export step: regenerate that pack's
  catalog and changelog from the log and the dashboard numbers instead of by hand. A pack that also carries the
  panel protocol as a skill makes the user-level copy redundant in governed repos; esc
  already reports that overlap as an informational duplicate.
- Knowledge layer stays out of scope for v1 — it's separable, and the panel is the
  novel part.

## Non-goals

- Not a router/proxy: no request interception, no prompt translation.
- Not a harness replacement: the user's primary agent stays primary. Driver mode
  (running a peer vendor's CLI, or the primary vendor's cheaper tier, as the working
  agent when the moderator's budget is exhausted) is failover, not promotion — see
  "Seat mode and driver mode" in `architecture.md`.
- Not a hosted service: everything local, everything inspectable.
