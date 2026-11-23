#!/usr/bin/env python3
"""Check the hand-written stories (stories-data.js) against the usage map.

Every place, person, team, story and map box a story names must exist, so the Who tab never
draws an arrow to nowhere or a "Show in architecture" link to a missing box. generate.py runs
this after it writes data.generated.js; run it alone with:

    python3 docs/usage-map/check_stories.py
"""
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
MOVE_KINDS = {"share", "store", "read", "sign"}
STATUSES = {"", "planned"}
PLACE_KINDS = {"machine", "repo", "home", "service", "agent"}
STORY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load_js_json(text, var):
    """Return the strict-JSON object assigned as `window.<var> = {...};`."""
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("//"))
    m = re.search(r"window\." + re.escape(var) + r"\s*=\s*", body)
    if not m:
        raise ValueError(f"no window.{var} assignment")
    body = body[m.end():].rstrip().rstrip(";")
    return json.loads(body)


def map_ids(generated, infra_text):
    """Every box id on the map: generated ids, hand-written ids in infra.js, and the root."""
    ids = {"root"}

    def walk(v):
        if isinstance(v, dict):
            if isinstance(v.get("id"), str):
                ids.add(v["id"])
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(generated)
    ids.update(re.findall(r"\bid:\s*'([^']+)'", infra_text))
    return ids


def infra_marks(infra_text):
    """(persona ids, planned ids) from the hand-written map: lines that set an id together
    with kind: 'persona' or status: 'planned'."""
    personas, planned = set(), set()
    for line in infra_text.splitlines():
        m = re.search(r"\bid:\s*'([^']+)'", line)
        if not m:
            continue
        if re.search(r"\bkind:\s*'persona'", line):
            personas.add(m.group(1))
        if re.search(r"\bstatus:\s*'planned'", line):
            planned.add(m.group(1))
    return personas, planned


def check(stories, ids, personas=None, planned=frozenset()):
    """Return one message per broken reference, missing field or unmarked claim; empty when
    all is well. Each bad entry gets its own message; one bad entry never hides the rest.

    personas: map ids a person may name as their persona (None = any map id).
    planned: map ids that are planned; a step on one, or in a planned place, must be planned.
    """
    errs = []

    def objs(value, where):
        """The dict entries of a list; one message for a non-list or a non-dict entry."""
        if value is None:
            return []
        if not isinstance(value, list):
            errs.append(f"{where}: must be a list")
            return []
        out = []
        for n, v in enumerate(value, 1):
            if isinstance(v, dict):
                out.append(v)
            else:
                errs.append(f"{where} entry {n}: must be an object")
        return out

    def need(obj, where, *fields):
        for f in fields:
            if not isinstance(obj.get(f), str) or not obj.get(f):
                errs.append(f"{where}: needs a {f}")

    def ids_of(entries, kind):
        seen = []
        for e in entries:
            need(e, f"{kind} {e.get('id', '?')}", "id")
            seen.append(e.get("id"))
        for d in sorted({x for x in seen if isinstance(x, str) and seen.count(x) > 1}):
            errs.append(f"{kind} {d}: duplicate {kind} id")
        return {x for x in seen if isinstance(x, str)}

    def status_ok(obj, where):
        if obj.get("status", "") not in STATUSES:
            errs.append(f"{where}: unknown status {obj.get('status')!r}")

    org = stories.get("org") if isinstance(stories.get("org"), dict) else {}
    why = stories.get("why") if isinstance(stories.get("why"), dict) else {}
    team_list = objs(org.get("teams"), "org.teams")
    people_list = objs(org.get("people"), "org.people")
    place_list = objs(stories.get("places"), "places")
    story_list = objs(stories.get("stories"), "stories")
    teams = ids_of(team_list, "team")
    people = ids_of(people_list, "person")
    places = ids_of(place_list, "place")
    known = ids_of(story_list, "story")
    planned_places = {p.get("id") for p in place_list if p.get("status") == "planned"}
    listed = set()

    for t in team_list:
        need(t, f"team {t.get('id')}", "name")
    for p in people_list:
        who = f"person {p.get('id')}"
        need(p, who, "name")
        if p.get("team") not in teams:
            errs.append(f"{who}: unknown team {p.get('team')!r}")
        for pid in p.get("personas", []):
            if pid not in (ids if personas is None else personas):
                errs.append(f"{who}: unknown map persona {pid!r}")
        for sid in p.get("stories", []):
            listed.add((p.get("id"), sid))
            if sid not in known:
                errs.append(f"{who}: unknown story {sid!r}")
    for c in objs(why.get("cards"), "why.cards"):
        need(c, f"why card {c.get('id')}", "id", "title", "text")
        for sid in c.get("stories", []):
            if sid not in known:
                errs.append(f"why card {c.get('id')}: unknown story {sid!r}")
    for p in place_list:
        where = f"place {p.get('id')}"
        need(p, where, "title")
        if p.get("kind") not in PLACE_KINDS:
            errs.append(f"{where}: unknown kind {p.get('kind')!r}")
        status_ok(p, where)
        if p.get("owner") and p["owner"] not in teams | people:
            errs.append(f"{where}: unknown owner {p['owner']!r}")
        if p.get("node") and p["node"] not in ids:
            errs.append(f"{where}: unknown map box {p['node']!r}")
    for s in story_list:
        sid = s.get("id")
        need(s, f"story {sid}", "title")
        if isinstance(sid, str) and not STORY_ID.match(sid):
            errs.append(f"story {sid}: id must be lowercase letters, digits and dashes")
        if s.get("person") not in people:
            errs.append(f"story {sid}: unknown person {s.get('person')!r}")
        elif (s["person"], sid) not in listed:
            errs.append(f"story {sid}: its person {s['person']!r} does not list it")
        status_ok(s, f"story {sid}")
        steps = objs(s.get("steps"), f"story {sid} steps")
        if not steps:
            errs.append(f"story {sid}: has no steps")
        for i, st in enumerate(steps, 1):
            where = f"story {sid} step {i}"
            need(st, where, "label", "text")
            status_ok(st, where)
            is_planned = st.get("status") == "planned" or s.get("status") == "planned"
            if not is_planned and st.get("node") in planned:
                errs.append(f"{where}: map box {st['node']!r} is planned, so the step must be planned")
            if not is_planned and st.get("place") in planned_places:
                errs.append(f"{where}: place {st['place']!r} is planned, so the step must be planned")
            if st.get("place") not in places:
                errs.append(f"{where}: unknown place {st.get('place')!r}")
            if st.get("truth") and st["truth"] not in places:
                errs.append(f"{where}: unknown truth place {st['truth']!r}")
            if st.get("node") and st["node"] not in ids:
                errs.append(f"{where}: unknown map box {st['node']!r}")
            for mv in objs(st.get("moves"), f"{where} moves"):
                need(mv, f"{where} move", "what")
                for end in ("from", "to"):
                    if mv.get(end) not in places:
                        errs.append(f"{where}: move {end} unknown place {mv.get(end)!r}")
                if mv.get("kind") not in MOVE_KINDS:
                    errs.append(f"{where}: unknown move kind {mv.get('kind')!r}")
    return errs


def main(quiet=False):
    try:
        return _main(quiet)
    except (OSError, ValueError) as e:  # missing file, or not strict JSON
        print(f"stories check: {e}", file=sys.stderr)
        return 1


def _main(quiet):
    stories = load_js_json((HERE / "stories-data.js").read_text(encoding="utf-8"), "USAGE_MAP_STORIES")
    generated = load_js_json((HERE / "data.generated.js").read_text(encoding="utf-8"), "USAGE_MAP_DATA")
    infra = (HERE / "infra.js").read_text(encoding="utf-8")
    personas, planned = infra_marks(infra)
    errs = check(stories, map_ids(generated, infra), personas, planned)
    if not quiet:
        for e in errs:
            print(f"stories-data.js: {e}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
