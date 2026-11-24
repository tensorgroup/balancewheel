#!/usr/bin/env python3
"""Usage-map data: what the page in docs/usage-map/ reads from the code.

    python3 docs/usage-map/generate.py          write docs/usage-map/data.generated.js
    python3 docs/usage-map/generate.py --open   the same, then open the page and print where it is

The output is committed, so it is deterministic: the date is HEAD's commit date and every
list keeps a stable order. It reads repository files only, and never imports them: `bw` commands from the bin/bw
docstring and its cmd_* functions, module docstrings, the config keys in metrics/config.py,
the runtime adapters in lead/runtimes.py, setup/paths.txt, and the example roster in
balancewheel.example.json. It never reads a user's config or state directory (privacy
firewall). The output sets window.USAGE_MAP_DATA, so the page opens straight from disk.
"""
import ast
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "usage-map" / "data.generated.js"
MODULE_DIRS = ("lead", "lead/guard", "metrics", "cockpit", "seats", "panel", "packs")


def rel(p):
    return pathlib.Path(p).relative_to(ROOT).as_posix()


def parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def first_line(doc):
    if not doc:
        return ""
    line = doc.strip().splitlines()[0].strip()
    return line


# ── modules ─────────────────────────────────────────────────────────────────
def modules():
    groups = []
    for d in MODULE_DIRS:
        items = []
        for f in sorted((ROOT / d).iterdir()):
            if f.suffix not in (".py", ".ts", ".mjs", ".html") or f.name == "__init__.py":
                continue
            doc = ""
            if f.suffix == ".py":
                doc = ast.get_docstring(parse(f)) or ""
            else:
                head = f.read_text(encoding="utf-8").splitlines()[:6]
                comment = [re.sub(r"^\s*(//|/\*+|\*|<!--)\s?", "", l).strip() for l in head if re.match(r"^\s*(//|/\*|\*|<!--)", l)]
                doc = " ".join(c for c in comment if c)
            lines = len(f.read_text(encoding="utf-8").splitlines())
            items.append({
                "id": f"mod:{d}/{f.stem}",
                "name": f.stem,
                "title": f"{d}/{f.name}",
                "tone": "module",
                "detail": f"{lines} lines",
                "kindLabel": "Module",
                "desc": first_line(doc) or "(no module docstring)",
                "file": rel(f),
            })
        groups.append({"id": f"modgrp:{d}", "title": f"{d}/", "items": items})
    return groups


# ── bw commands ─────────────────────────────────────────────────────────────
def commands():
    path = ROOT / "bin" / "bw"
    tree = parse(path)
    # The raw docstring: get_docstring() would strip the indentation the parse below keys on.
    doc = ast.get_docstring(tree, clean=False) or ""
    entries = []
    for line in doc.splitlines():
        m = re.match(r"^  (bw(?: [^\s].*?)?)\s{2,}(\S.*)$", line)
        if m:
            entries.append({"usage": m.group(1).strip(), "desc": m.group(2).strip()})
        elif re.match(r"^  bw\b", line):
            entries.append({"usage": line.strip(), "desc": ""})
        elif entries and re.match(r"^\s{20,}\S", line):
            entries[-1]["desc"] = (entries[-1]["desc"] + " " + line.strip()).strip()
    # Which imported modules each cmd_* function calls: command → module edges.
    imported = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            base = node.module.replace(".", "/")
            for a in node.names:
                local = a.asname or a.name
                cand = f"{base}/{a.name}"
                # Only a whole module counts; a helper function imported by name is not a home.
                if (ROOT / f"{cand}.py").exists():
                    imported[local] = cand
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported[a.asname or a.name] = a.name
    uses = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            mods = sorted({imported[n] for n in names if n in imported})
            uses[node.name[4:]] = [m for m in mods if (ROOT / f"{m}.py").exists()]
    # store lives in metrics/ (bin/bw puts metrics/ on sys.path).
    for k, v in uses.items():
        uses[k] = [("metrics/store" if m == "store" else m) for m in v]

    groups = {}
    for e in entries:
        words = e["usage"].split()
        top = words[1] if len(words) > 1 and not words[1].startswith(("[", "-", "<")) else "bw"
        sub = words[2] if len(words) > 2 and top in ("guard", "triage", "sandbox", "improve", "setup", "seat", "panel", "pack") and not words[2].startswith(("[", "-", "<", '"')) else ""
        cid = f"cmd:{top}" + (f".{sub}" if sub else "")
        flags = [{"name": f} for f in re.findall(r"--[a-z][a-z-]*", e["usage"])]
        fn = {"sandbox": "verify", "guard": "guard_verify" if sub == "verify" else "guard"}.get(top, top)
        item = {
            "id": cid,
            "name": f"{top} {sub}".strip(),
            "title": e["usage"],
            "tone": "command",
            "kindLabel": "bw command",
            "desc": e["desc"],
            "usage": e["usage"],
            "flags": flags,
            "file": "bin/bw",
            "_uses": uses.get(fn, uses.get(top, [])),
        }
        if any(i["id"] == cid for i in groups.get(top, [])):
            continue
        groups.setdefault(top, []).append(item)
    return groups


# ── example roster ──────────────────────────────────────────────────────────
VENDOR_TONE = {"anthropic": "anthropic", "openai": "openai", "google": "google", "moonshot": "moonshot", "openrouter": "openrouter"}


def roster():
    cfg = json.loads((ROOT / "balancewheel.example.json").read_text(encoding="utf-8"))
    plans = cfg.get("plans", {})
    vendor_of = {name: p.get("vendor", "") for name, p in plans.items()}
    groups, edges = [], []

    def tone_for_plan(plan):
        return VENDOR_TONE.get(vendor_of.get(plan, ""), "data")

    items = []
    for name, p in plans.items():
        items.append({"id": f"plan:{name}", "name": name, "tone": VENDOR_TONE.get(p.get("vendor"), "data"), "detail": f"{p.get('billing', '')} · quota: {p.get('quota', '')}",
                      "kindLabel": "Plan (who pays)", "desc": f"Vendor {p.get('vendor', '?')}, billing {p.get('billing', '?')}, " + ("no quota source." if p.get('quota') == "none" else f"quota read from {p.get('quota', '?')}."),
                      "chips": [f"{k}: {json.dumps(v)}" for k, v in p.items()], "chipsLabel": "Example config", "file": "balancewheel.example.json"})
    groups.append({"id": "rgrp:plans", "title": "Plans", "items": items})

    items = []
    for name, t in cfg.get("tools", {}).items():
        items.append({"id": f"tool:{name}", "name": name, "tone": "tool", "detail": "self-updating" if t.get("self_updating") else " ".join(t.get("update", [])),
                      "kindLabel": "Tool bw keeps current", "desc": "bw start updates it (or checks it) before it opens a lead.",
                      "chips": [f"{k}: {json.dumps(v)}" for k, v in t.items()], "chipsLabel": "Example config", "file": "balancewheel.example.json"})
    groups.append({"id": "rgrp:tools", "title": "Tools", "items": items})

    items = []
    for name, ld in cfg.get("leads", {}).items():
        items.append({"id": f"lead:{name}", "name": name, "tone": tone_for_plan(ld.get("plan")), "detail": f"{ld.get('runtime')} · {ld.get('model')}",
                      "kindLabel": "Lead (writes code in a worktree)", "desc": f"Runs {ld.get('runtime')} on model {ld.get('model')}, paid by {ld.get('plan')}." + (f" Autonomy: {ld['autonomy']}." if ld.get("autonomy") else ""),
                      "chips": [f"{k}: {json.dumps(v)}" for k, v in ld.items()], "chipsLabel": "Example config", "file": "balancewheel.example.json"})
        if ld.get("plan") in plans:
            edges.append({"from": f"lead:{name}", "to": f"plan:{ld['plan']}", "label": "paid by"})
        edges.append({"from": f"lead:{name}", "to": f"rt:{ld.get('runtime')}", "label": "runs on"})
    groups.append({"id": "rgrp:leads", "title": "Leads", "items": items})

    items = []
    for name, s in cfg.get("seats", {}).items():
        off = s.get("enabled") is False
        items.append({"id": f"seat:{name}", "name": name, "tone": tone_for_plan(s.get("plan")), "detail": ", ".join(s.get("roles", [])) + (" · off" if off else ""),
                      "kindLabel": "Seat (read-only reviewer)", "desc": f"Runs {s.get('runtime')} on {s.get('model')}" + (f" at {s['effort']} effort" if s.get("effort") else "") + f". Roles: {', '.join(s.get('roles', []))}." + (f" Falls back to {s['fallback']} when its plan is capped." if s.get("fallback") else "") + (" Disabled in the example." if off else ""),
                      "chips": [f"{k}: {json.dumps(v)}" for k, v in s.items() if k != "provider"], "chipsLabel": "Example config", "file": "balancewheel.example.json"})
        if s.get("plan") in plans:
            edges.append({"from": f"seat:{name}", "to": f"plan:{s['plan']}", "label": "paid by"})
        if s.get("fallback"):
            edges.append({"from": f"seat:{name}", "to": f"seat:{s['fallback']}", "label": "falls back to"})
        edges.append({"from": f"seat:{name}", "to": f"rt:{s.get('runtime')}", "label": "runs on"})
    groups.append({"id": "rgrp:seats", "title": "Seats", "items": items})

    items = []
    for name, pr in cfg.get("profiles", {}).items():
        cps = pr.get("checkpoints", {})
        chips = [f"lead_order: {' → '.join(pr.get('lead_order', []))}"]
        for cp, spec in cps.items():
            chips.append(f"{cp}: seats {', '.join(spec.get('seats', []))}" + (f"; required {', '.join(spec['required'])}" if spec.get("required") else "") + (f"; min {spec['min_seats']}" if spec.get("min_seats") else ""))
        chips += [f"{k}: {json.dumps(v)}" for k, v in pr.items() if k not in ("lead_order", "checkpoints")]
        items.append({"id": f"profile:{name}", "name": name, "tone": "profile", "detail": f"checkpoints: {', '.join(cps)}",
                      "kindLabel": "Profile (how a project is run)", "desc": "Which leads to try in order, and which seats sit on each checkpoint's panel.",
                      "chips": chips, "chipsLabel": "Example config", "file": "balancewheel.example.json"})
        for i, ld in enumerate(pr.get("lead_order", [])):
            edges.append({"from": f"profile:{name}", "to": f"lead:{ld}", "label": f"lead #{i + 1}"})
        for spec in cps.values():
            for s in spec.get("seats", []):
                if not any(e["from"] == f"profile:{name}" and e["to"] == f"seat:{s}" for e in edges):
                    edges.append({"from": f"profile:{name}", "to": f"seat:{s}", "label": "panel seat"})
    groups.append({"id": "rgrp:profiles", "title": "Profiles", "items": items})
    return groups, edges, cfg


# ── runtimes ────────────────────────────────────────────────────────────────
def runtimes():
    tree = parse(ROOT / "lead" / "runtimes.py")
    names, autonomy = [], {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ADAPTERS" for t in node.targets) and isinstance(node.value, ast.Dict):
            names = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "AUTONOMY_FLAGS" for t in node.targets) and isinstance(node.value, ast.Dict):
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Dict):
                    autonomy[k.value] = [kk.value for kk in v.keys if isinstance(kk, ast.Constant)]
    vendor = {"claude-code": "Anthropic (Claude Code CLI)", "codex": "OpenAI (Codex CLI)", "agy": "Google (Antigravity CLI: Gemini or Claude)", "kimi": "Moonshot (Kimi Code CLI)", "pi": "OpenRouter (pi CLI)"}
    items = [{"id": f"rt:{n}", "name": n, "tone": "runtime", "detail": vendor.get(n, ""), "kindLabel": "Runtime adapter",
              "desc": f"How bw starts {n}: an argv built per lead or seat. Autonomy modes: {', '.join(autonomy.get(n, [])) or 'none'}.",
              "file": "lead/runtimes.py"} for n in names]
    return {"id": "rgrp:runtimes", "title": "Runtimes", "items": items}


# ── config keys and home paths ──────────────────────────────────────────────
def config_keys():
    tree = parse(ROOT / "metrics" / "config.py")
    groups = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id.endswith("_KEYS"):
            name = node.targets[0].id
            try:
                keys = sorted(ast.literal_eval(node.value))
            except ValueError:
                continue
            label = name.replace("_KEYS", "").replace("_", " ").lower()
            groups.append({"id": f"keygrp:{name}", "title": label, "items": [
                {"id": f"key:{name}.{k}", "name": k, "tone": "key", "kindLabel": "Config key", "detail": name,
                 "desc": f"Accepted in {label} config ({name} in metrics/config.py; unknown keys are refused).", "file": "metrics/config.py"} for k in keys if not k.startswith("_")]})
    return groups


def home_paths():
    items, section = [], "Paths"
    for line in (ROOT / "setup" / "paths.txt").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            section = s.lstrip("# ").strip()
            continue
        items.append({"id": f"path:{s}", "name": s.split("/")[-1] or s, "title": s, "tone": "path", "kindLabel": "Home path setup touches",
                      "detail": s, "desc": section, "file": "setup/paths.txt"})
    return items


def git_head():
    """The HEAD commit's short hash and date. Using the commit date, not the clock, keeps the
    output byte-identical when the code has not changed (the file is committed)."""
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h %cI"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        return out[0], out[1]
    except (OSError, subprocess.CalledProcessError, IndexError):
        return "unknown", "unknown"


def main():
    cmd_groups = commands()
    mod_groups = modules()
    roster_groups, roster_edges, cfg = roster()
    rt_group = runtimes()
    edges = list(roster_edges)
    cmd_tm_groups = []
    titles = {"bw": "bw (no command)", "guard": "guard", "triage": "triage", "sandbox": "sandbox", "improve": "improve", "setup": "setup"}
    for top, items in cmd_groups.items():
        for it in items:
            for m in sorted(it.pop("_uses")):
                edges.append({"from": it["id"], "to": f"mod:{m}", "label": "implemented in"})
        cmd_tm_groups.append({"id": f"cmdgrp:{top}", "title": titles.get(top, top), "items": items})
    tests = sum(len(re.findall(r"^\s+def test_", p.read_text(encoding="utf-8"), re.M)) for p in (ROOT / "tests").glob("test_*.py"))
    docs = sum(1 for _ in (ROOT / "docs").rglob("*.md"))
    n_cmds = sum(len(g["items"]) for g in cmd_tm_groups)
    n_mods = sum(len(g["items"]) for g in mod_groups)
    commit, committed_at = git_head()
    data = {
        "generatedAt": committed_at,
        "commit": commit,
        "stats": [
            {"n": n_cmds, "label": "bw commands", "target": "commands"},
            {"n": n_mods, "label": "modules", "target": "code"},
            {"n": tests, "label": "tests", "target": "code"},
            {"n": len(cfg.get("seats", {})), "label": "example seats", "target": "roster"},
            {"n": len(cfg.get("leads", {})), "label": "example leads", "target": "roster"},
            {"n": len(cfg.get("plans", {})), "label": "plans", "target": "roster"},
            {"n": docs, "label": "docs", "target": "root"},
        ],
        "treemaps": {
            "commands": {"groups": cmd_tm_groups},
            "modules": {"groups": mod_groups},
            "roster": {"groups": roster_groups + [rt_group]},
            "config": {"groups": config_keys() + [{"id": "keygrp:paths", "title": "home paths (setup/paths.txt)", "items": home_paths()}]},
        },
        "edges": edges,
    }
    OUT.write_text("// GENERATED by docs/usage-map/generate.py — do not edit by hand.\n"
                   "// Regenerate and open: python3 docs/usage-map/generate.py --open\n"
                   f"window.USAGE_MAP_DATA = {json.dumps(data, indent=1)};\n", encoding="utf-8")
    print(f"usage map: {n_cmds} commands, {n_mods} modules, {len(cfg.get('seats', {}))} seats, {tests} tests -> {rel(OUT)}")
    if "--open" in sys.argv[1:]:
        open_page()


def open_page():
    page = ROOT / "docs" / "usage-map" / "index.html"
    print(f"\nOpened the usage map:\n  {page}\n  {page.as_uri()}")
    if sys.platform == "darwin":
        cmd = ["open", str(page)]
    elif os.name == "nt":
        cmd = ["cmd", "/c", "start", "", str(page)]
    else:
        cmd = ["xdg-open", str(page)]
    try:
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        print("  (Could not open a browser here. Open the path above by hand.)")


if __name__ == "__main__":
    main()
