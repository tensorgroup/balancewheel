# Roadmap — from private tooling to shareable project

## Standing goal

Working with balancewheel gets easier, faster, and better the more it is used, for everyone working
together. Every change should either reduce what the user has to do or make the next improvement
easier to see. `bw improve` is the loop: what happened, the pattern, the change it points to.
Letting bw apply a low-risk suggestion by itself waits on evidence that its suggestions are right.

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

As of 2026-11-24 every directory below has landed (see the amendments). The list stays as the
record of what each one owns.

- `seats/` — a Python runner behind `bw seat`: headless argv per runtime, the brief,
  per-directory sessions, caps and stand-ins, the OS sandbox for every seat, usage records,
  and attack-based verification.
- `panel/` — templates **plus** the mechanics code behind `bw panel`. The templates cover the
  moderator rules, the substance bar, and the logging schema. The moderator stays whatever
  primary agent the user already runs.
- `metrics/` — JSONL schema, validator, dashboard generator.
- `setup/` — *(landed 2026-09-16, ahead of the gate: it is install hygiene, not the
  seat implementation)* the list of home-directory paths a setup touches, and the
  backup/restore scripts around it.
- `packs/` — the export of one marked catalog block in esc's `model-seats` pack (the governance
  hand-off: balancewheel generates and justifies policy; esc distributes it). The first
  pack, `model-seats`, shipped in esc's `examples/packs/` on 2026-09-16, written by hand
  from this repo's seat policy: duties per seat, the substance bar, wall-clock
  caps, mandatory logging, and a re-promotion criterion for any seat demoted on evidence, with the models behind
  the seats a dated catalog roster (the 2026-08-25 draft that named stealth ids was
  superseded). `packs/` is the export of one marked catalog block (`bw pack export`, landed 2026-11-22); the
  changelog bullet is printed for the user to paste, because esc owns its changelog. A pack that also carries the
  panel protocol as a skill makes the user-level copy redundant in governed repos; esc
  already reports that overlap as an informational duplicate.
- Knowledge layer stays out of scope for v1 — it's separable, and the panel is the
  novel part.

## Amendment (2026-10-24): `lead/` and `bin/bw` land before criterion 4

Leads and rotation (build-order step 2 of the 2026-10-24 design) are built here, clean-room,
before a second user has run the setup. Reason: they are what a second user needs most, and
the author's own setup becoming `bw`'s first consumer is the dogfood. The privacy firewall is
unchanged.

## Amendment (2026-11-22): `seats/`, `panel/`, and `packs/` land before criterion 4

The seat runner, the panel mechanics and templates, and the pack export are built here, clean-room,
before a second user has run the setup. Reason: they make the setup usable without the author's
private wrappers, so a second user can only run it once they exist. The author's own setup becomes
their first consumer, as it did for `lead/` and `bin/bw`. The privacy firewall is unchanged: no
wrapper, instruction file, or log record is copied from the private implementation, and every fixture
is synthetic. Criterion 4 still gates the first tagged release that calls the extraction complete.

The "Planned shape" bullets above changed in the same commit. `seats/` is a Python runner behind
`bw seat`, not a wrapper generator. `panel/` is templates plus the mechanics code. `packs/` is the
export of one marked catalog block.

## Non-goals

- Not a router/proxy: no request interception, no prompt translation.
- Not a harness replacement: the user works in a vendor's own agent; `bw lead` picks which
  one and never sits between the user and it. Driver mode
  (running a peer vendor's CLI, or the primary vendor's cheaper tier, as the working
  agent when the moderator's budget is exhausted) is failover, not promotion — see
  "Seat mode and driver mode" in `architecture.md`.
- Not a hosted service: everything local, everything inspectable.
