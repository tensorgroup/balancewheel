"""export.py — owns the `model-seats` catalog block: the entries bw writes between the pack's markers.

Pure functions over the resolved config and `store.load()` output. An entry is `{name, status, notes}`.
Numbers count only the panels where the seat ran its current model. Entries carry no target, finding
title, path, project, profile name, lead id, or conversation.
"""
import difflib
import json
import os
import re

from lead import handoff
from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead import quota
from seats import verify
import store  # noqa: E402
import summary  # noqa: E402

NOT_YET = "not yet run"


class PackError(Exception):
    """Every refusal. The message is the one line the user sees."""


def exported_seats(config):
    return verify.seat_items(config)


def model_panels(panels, seat, model):
    """Every panel, sorted by `ts`, where the seat ran `model`. A model that returns keeps its earlier panels."""
    return sorted((p for p in panels if p["seats"].get(seat) == model), key=lambda p: p["ts"] or "")


def _first_lead_runtime(config):
    profile = (config.get("profiles") or {}).get(config.get("default_profile")) or {}
    order = profile.get("lead_order") or []
    lead = (config.get("leads") or {}).get(order[0]) if order else None
    return lead.get("runtime") if lead else None


def seat_role(config, seat):
    spec = config["seats"][seat]
    if spec.get("title"):
        return spec["title"]
    roles = spec.get("roles") or []
    if roles and roles[0] in ("plan", "decision"):
        return "Planning seat"
    if spec.get("tier") == "reserved":
        return "Reserved escalation seat"
    if spec["runtime"] == _first_lead_runtime(config):
        return "Blind same-vendor seat"
    return "Peer seat"


def _dates(stamps):
    return [t for t in (summary._parse(s) for s in stamps) if t is not None]


def _month(stamps):
    parsed = _dates(stamps)
    return min(parsed).strftime("%Y-%m") if parsed else NOT_YET


def _last_date(stamps):
    parsed = _dates(stamps)
    return max(parsed).strftime("%Y-%m-%d") if parsed else "an unknown date"


def seat_entry(config, data, seat):
    spec = config["seats"][seat]
    model = spec["model"]
    panels = model_panels(data["panels"], seat, model)
    stamps = [p["ts"] for p in panels] + [u.get("ts") for u in data["usage"]
                                          if u.get("seat") == seat and u.get("model") == model]
    name = f"{seat_role(config, seat)} (reference roster, {_month(stamps)}, {model} via {spec['runtime']})"
    row = next(r for r in summary.seat_rows(dict(data, panels=panels), config) if r["seat"] == seat)
    if spec.get("tier") == "reserved":
        status = "reserved"
    elif row["panels"] < config["watches"]["redundant_min_panels"] or any(
            w["kind"] == "redundant-seat" and w["target"] == seat and w["status"] == "open" for w in data["watches"]):
        status = "review-required"
    else:
        status = "preferred"
    if panels:
        record = (f"Record under this model to {_last_date([p['ts'] for p in panels])}: {row['panels']} panels, "
                  f"{row['confirmed']} confirmed, {row['partial']} partial, {row['refuted']} refuted; "
                  f"{row['unique_recent']} unique in the last {row['recent_panels']} panels; "
                  f"{row['wins']} of {row['involved']} disputes won.")
    else:
        record = "No panels under this model yet."
    return {"name": name, "status": status, "notes": spec["policy"].strip() + " " + record}


def lead_model(config, state, lead):
    return quota.lead_model(state, config["leads"][lead])[0]


def _lead_ids(config):
    profiles = config.get("profiles") or {}
    names = [n for n in [config.get("default_profile")] if n in profiles]
    names += sorted(n for n in profiles if n not in names)
    ids = []
    for n in names:
        ids += [lead for lead in profiles[n].get("lead_order") or [] if lead not in ids]
    return ids


def lead_entries(config, data, state):
    out = []
    rows = summary.lead_rows(data, [])
    for lead in _lead_ids(config):
        runtime = config["leads"][lead]["runtime"]
        model = lead_model(config, state, lead)
        starts = [s.get("start") for s in data["sessions"] if s.get("runtime") == runtime and s.get("model") == model]
        name = f"Lead (reference roster, {_month(starts)}, {model} via {runtime})"
        row = next((r for r in rows if r["runtime"] == runtime and r["model"] == model), None)
        head = "A lead in a lead order; bw does not score lead quality yet. "
        if row:
            tail = (f"Record under this model to {_last_date(starts)}: {row['sessions']} sessions, {row['minutes']} lead minutes, "
                    f"{row['good']} good and {row['bad']} bad ratings, {row['cap_switches']} switches on a cap.")
        else:
            tail = "No sessions under this model yet."
        out.append({"name": name, "status": "review-required", "notes": head + tail})
    return out


def _check_policies(config, seats):
    policy = {s: config["seats"][s]["policy"] for s in seats}
    empty = [s for s, p in policy.items() if not p.strip()]
    if empty:
        raise PackError("seats with no policy (the pack must say why a seat holds its duties): " + ", ".join(empty))
    stub = [s for s, p in policy.items() if "TODO" in p or ("<" in p and ">" in p)]
    if stub:
        raise PackError("seats whose policy is still a placeholder: " + ", ".join(stub))


def _with(name, extra):
    return name[:-1] + extra + ")"


def entries(config, data, state):
    """Seat entries, then lead entries. A name two entries share gets a distinguisher; one that still
    collides is refused, because `bump_kind` and `bullet` key by name."""
    seats = exported_seats(config)
    _check_policies(config, seats)
    items = []
    for s in seats:
        effort = config["seats"][s].get("effort")
        items.append((seat_entry(config, data, s), s, f", effort {effort}" if effort else None))
    for e, lead in zip(lead_entries(config, data, state), _lead_ids(config)):
        plan = config["leads"][lead].get("plan")
        items.append((e, None, f", {plan} plan" if plan else None))
    counts = {}
    for e, _, _ in items:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    out, owner = [], {}
    for e, seat, extra in items:
        if counts[e["name"]] > 1 and extra:
            e = dict(e, name=_with(e["name"], extra))
        if e["name"] in owner:
            first = seat or owner[e["name"]]
            hint = f"set seats.{first}.title on one" if first else "give the leads different plans or models"
            raise PackError(f"two catalog entries would be named {e['name']}; {hint}")
        owner[e["name"]] = seat
        out.append(e)
    return out


# ---- the pack text: markers, render, version bump, bullet -------------------------------------

MARK_BEGIN = "# bw:catalog:begin"
MARK_END = "# bw:catalog:end"
PACK_NAME = "model-seats"
MARKER_HELP = (f"The pack has no bw catalog block. Put `{MARK_BEGIN}` and `{MARK_END}` "
               "around the catalog entries bw will own.")

_BEGIN = re.compile(r"^([ \t]*)" + re.escape(MARK_BEGIN) + r"[ \t]*\r?$", re.M)
_END = re.compile(r"^[ \t]*" + re.escape(MARK_END) + r"[ \t]*\r?$", re.M)
_CATALOG = re.compile(r"^catalog:", re.M)
_NAME = re.compile(r"^name:[ \t]*(.*?)[ \t]*\r?$", re.M)
_VERSION_LINE = re.compile(r"^version:", re.M)
_VERSION = re.compile(r"^version:[ \t]*([\"']?)(\d+)\.(\d+)\.(\d+)\1[ \t]*\r?$", re.M)
_ITEM = re.compile(r"^- name:[ \t]*(.*)$")
_FIELD = re.compile(r"^(category|status|notes):[ \t]*(.*)$")


def pack_name(text):
    m = _NAME.search(text)
    if not m:
        return None
    value = m.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def find_block(text):
    """(start, end, indent): the offsets strictly between the begin line and the end line."""
    begins, ends = list(_BEGIN.finditer(text)), list(_END.finditer(text))
    if not begins or not ends:
        raise PackError(MARKER_HELP)
    if len(begins) > 1 or len(ends) > 1:
        raise PackError(f"The pack has more than one `{MARK_BEGIN}` or `{MARK_END}` line. Keep one of each.")
    begin, end = begins[0], ends[0]
    if end.start() < begin.start():
        raise PackError(f"`{MARK_END}` comes before `{MARK_BEGIN}`. Swap the two lines.")
    catalog = _CATALOG.search(text)
    if not catalog or catalog.start() > begin.start():
        raise PackError(f"No top-level `catalog:` line comes before `{MARK_BEGIN}`. Put the markers inside `catalog:`.")
    start = begin.end() + (1 if text[begin.end():begin.end() + 1] == "\n" else 0)
    return start, end.start(), begin.group(1)


def find_version(text):
    """(start, end, (X, Y, Z)): the offsets of the version number only."""
    if len(_VERSION_LINE.findall(text)) != 1:
        raise PackError("The pack needs exactly one top-level `version:` line.")
    m = _VERSION.search(text)
    if not m:
        raise PackError("The pack `version:` line is not in the form X.Y.Z.")
    return m.start(2), m.end(4), tuple(int(m.group(i)) for i in (2, 3, 4))


def _newline(text, block_start):
    return "\r\n" if text[max(block_start - 2, 0):block_start] == "\r\n" else "\n"


def render(entries, indent, newline):
    out = []
    for e in entries:
        out.append(f"{indent}- name: {json.dumps(e['name'], ensure_ascii=True)}")
        out.append(f"{indent}  category: model-seat")
        out.append(f"{indent}  status: {e['status']}")
        out.append(f"{indent}  notes: {json.dumps(e['notes'], ensure_ascii=True)}")
    return "".join(line + newline for line in out)


def _bad_line(n):
    return PackError(f"line {n} inside the bw:catalog markers is not a one-line catalog field; "
                     "bw owns this block — move hand-written entries outside the markers")


def _value(raw, n):
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise _bad_line(n) from None
    if raw[:1] in ("|", ">"):
        raise _bad_line(n)
    return raw.strip()


def parse_block(block, first_line=1):
    """The entries in the current block. `first_line` is the file line number of the block's first line."""
    found, current = [], None
    for i, line in enumerate(block.split("\n")):
        n = first_line + i
        s = line.rstrip("\r").strip()
        if not s or s.startswith("#"):
            continue
        item, field = _ITEM.match(s), _FIELD.match(s)
        if item:
            current = {"name": _value(item.group(1), n), "status": "", "notes": ""}
            found.append(current)
        elif field and current is not None:
            if field.group(1) != "category":
                current[field.group(1)] = _value(field.group(2), n)
        else:
            raise _bad_line(n)
    return found


def bump_kind(old, new):
    if old == new:
        return None
    if {e["name"] for e in old} != {e["name"] for e in new}:
        return "minor"
    status = {e["name"]: e["status"] for e in old}
    return "minor" if any(status[e["name"]] != e["status"] for e in new) else "patch"


def next_version(version, kind):
    major, minor, patch = version
    return f"{major}.{minor + 1}.0" if kind == "minor" else f"{major}.{minor}.{patch + 1}"


def rewrite(text, entries):
    """(new_text, old_version, new_version, old_entries). `new_version` is None when nothing changed."""
    start, end, indent = find_block(text)
    vstart, vend, version = find_version(text)
    old_version = ".".join(str(p) for p in version)
    if start <= vstart < end:
        raise PackError("The `version:` line lies inside the bw:catalog markers. Move it outside.")
    old = parse_block(text[start:end], first_line=text.count("\n", 0, start) + 1)
    kind = bump_kind(old, entries)
    if kind is None:
        return text, old_version, None, old
    new_version = next_version(version, kind)
    spans = [(start, end, render(entries, indent, _newline(text, start))), (vstart, vend, new_version)]
    for a, b, replacement in sorted(spans, reverse=True):
        text = text[:a] + replacement + text[b:]
    return text, old_version, new_version, old


def bullet(old_version, new_version, old, new, today):
    before, after = {e["name"]: e for e in old}, {e["name"]: e for e in new}
    quote = lambda names: ", ".join(f'"{n}"' for n in names)  # noqa: E731
    parts = []
    added = [n for n in after if n not in before]
    removed = [n for n in before if n not in after]
    if added:
        parts.append(f"added {quote(added)}")
    if removed:
        parts.append(f"removed {quote(removed)}")
    both = [n for n in after if n in before]
    for n in both:
        if before[n]["status"] != after[n]["status"]:
            parts.append(f'"{n}": {before[n]["status"]} to {after[n]["status"]}')
    refreshed = sum(1 for n in both if before[n]["notes"] != after[n]["notes"])
    if refreshed:
        parts.append(f"record lines refreshed for {refreshed} entries")
    head = (f"- `{PACK_NAME}` {new_version} ({today}, exported by `bw pack export` from the balancewheel log; "
            f"was {old_version}): ")
    return head + "; ".join(parts)


def _write_text_atomic(path, data, mode=0o644):
    """A temp file in the same directory, then `os.replace`. A failure leaves no temp file behind."""
    tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            os.fchmod(f.fileno(), mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_pack(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def _check_git(pack_dir, git_rc):
    timeout = f"git timed out in {pack_dir}; try again"
    unreadable = f"git config in {pack_dir} could not be read; git not run"
    rc, _, _ = git_rc(pack_dir, "ls-files", "--error-unmatch", "--", "pack.yaml")
    if rc == 124:
        raise PackError(timeout)
    if rc == 125:
        raise PackError(unreadable)
    if rc != 0:
        rc2, _, _ = git_rc(pack_dir, "rev-parse", "--git-dir")
        if rc2 != 0:
            raise PackError(f"{pack_dir} is not a git checkout; bw writes pack.yaml only where git can show the change")
        raise PackError("pack.yaml is not tracked by git (untracked or ignored); commit it first")
    rc, out, _ = git_rc(pack_dir, "status", "--porcelain", "--untracked-files=all", "--ignored", "--", "pack.yaml")
    if rc == 124:
        raise PackError(timeout)
    if rc == 125:
        raise PackError(unreadable)
    if rc != 0:
        raise PackError(f"git status failed in {pack_dir}; pack.yaml not written")
    if out.strip():
        raise PackError("pack.yaml has uncommitted changes; commit or discard them first")


def export(config, data, pack_dir, state, dry_run=False, today=None, git_rc=None):
    """Rewrite the marked catalog block and the version of `<pack_dir>/pack.yaml`. See the plan for the order."""
    git_rc = git_rc or handoff._git_rc
    today = today or store.now_ts()[:10]
    path = os.path.join(pack_dir, "pack.yaml")
    try:
        st = os.lstat(path)
    except OSError:
        raise PackError(f"{path} does not exist") from None
    if os.path.islink(path):
        raise PackError(f"{path} is a symlink; bw does not write through symlinks")
    if not os.path.isfile(path):
        raise PackError(f"{path} is not a regular file")
    try:
        raw = _read_pack(path)
    except OSError as e:
        raise PackError(f"could not read {path}: {e.strerror}") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PackError(f"{path} is not valid UTF-8") from None
    name = pack_name(text)
    if name != PACK_NAME:
        raise PackError(f"{path}: name is {name}, not {PACK_NAME}")
    _check_git(pack_dir, git_rc)
    new_entries = entries(config, data, state)
    new_text, old_version, new_version, old = rewrite(text, new_entries)
    result = {"path": path, "changed": False, "old_version": old_version, "new_version": None,
              "diff": "", "bullet": None, "bullet_path": None}
    if new_version is None:
        return result
    diff = "".join(difflib.unified_diff(text.splitlines(True), new_text.splitlines(True),
                                        "a/pack.yaml", "b/pack.yaml"))
    text_bullet = bullet(old_version, new_version, old, new_entries, today)
    result.update(changed=True, new_version=new_version, diff=diff, bullet=text_bullet)
    if dry_run:
        return result
    bullet_path = os.path.join(state, "packs", f"{PACK_NAME}-{new_version}.md")
    os.makedirs(os.path.dirname(bullet_path), exist_ok=True)
    _write_text_atomic(bullet_path, (text_bullet + "\n").encode("utf-8"))
    result["bullet_path"] = bullet_path
    try:
        again = _read_pack(path)
    except OSError:
        raise PackError("pack.yaml changed during the export; run it again") from None
    if again != raw:
        raise PackError("pack.yaml changed during the export; run it again")
    _write_text_atomic(path, new_text.encode("utf-8"), mode=st.st_mode & 0o7777)
    kind = bump_kind(old, new_entries)
    try:
        store.append(store.log_path(config), {"type": "pack_export", "pack": PACK_NAME, "from": old_version,
                                              "to": new_version, "bump": kind, "entries": len(new_entries)})
    except OSError as e:
        result["event_error"] = type(e).__name__
    return result
