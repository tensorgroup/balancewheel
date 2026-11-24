import copy, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from lead import paths  # noqa: E402,F401
import config as cfg  # noqa: E402
import store  # noqa: E402
from packs import export  # noqa: E402

BASE = {
    "schema_version": 3,
    "plans": {"p1": {"vendor": "v1", "billing": "plan", "quota": "statusline"},
              "p2": {"vendor": "v2", "billing": "plan", "quota": "statusline"}},
    "leads": {"l1": {"runtime": "claude-code", "model": "m-lead", "plan": "p1"}},
    "seats": {
        "alpha": {"runtime": "codex", "model": "model-a", "roles": ["plan", "review"], "policy": "Alpha policy."},
        "beta": {"runtime": "claude-code", "model": "model-b", "roles": ["review"], "policy": "Beta policy."},
        "gamma": {"runtime": "claude-code", "model": "model-g", "roles": ["debug"], "tier": "reserved",
                  "policy": "Gamma policy."},
        "eps": {"runtime": "pi", "model": "model-e", "roles": ["review"], "policy": "Eps policy."},
        "off": {"runtime": "pi", "model": "model-o", "roles": ["review"], "enabled": False, "policy": ""},
        "idle": {"runtime": "pi", "model": "model-i", "roles": ["review"], "policy": "Idle policy."},
    },
    "profiles": {"d": {"lead_order": ["l1"], "checkpoints": {"review": {"seats": ["alpha", "beta", "eps"]}}}},
    "default_profile": "d",
}


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("BALANCEWHEEL_METRICS_LOG", None)
        os.environ.pop("BW_STATE_DIR", None)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        self.n = 0

    def make_config(self, top=None, **seat_overrides):
        obj = copy.deepcopy(BASE)
        obj.update(top or {})
        for seat, over in seat_overrides.items():
            obj["seats"][seat].update(over)
        self.n += 1
        path = os.path.join(self.tmp, f"c{self.n}.json")
        with open(path, "w") as f:
            json.dump(obj, f)
        return cfg.load_config(path)

    def load(self, events):
        """Events go through `store.append`, which writes them as given (metrics/log.py would recompute counts)."""
        self.n += 1
        path = os.path.join(self.tmp, f"m{self.n}.jsonl")
        for e in events:
            store.append(path, e)
        return store.load(path)


def panel(pid, ts, seats, counts=None, disputes=(), **over):
    """A panel event; `findings` has an entry for every seat, with the given counts (confirmed, partial, refuted, unique)."""
    counts = counts or {}
    findings = {}
    for s in seats:
        c, p, r, u = counts.get(s, (0, 0, 0, 0))
        findings[s] = {"confirmed": c, "partial": p, "refuted": r, "unique": u, "total": c + p + r}
    e = {"type": "panel", "id": pid, "ts": ts, "target": "t", "seats": dict(seats), "findings": findings,
         "disputes": list(disputes)}
    e.update(over)
    return e


class Entries(Base):
    def test_exported_seats_are_enabled_and_reachable(self):
        self.assertEqual(export.exported_seats(self.make_config()), ["alpha", "beta", "gamma", "eps"])

    def test_fallback_only_seat_is_exported(self):
        c = self.make_config(alpha={"fallback": "idle"})
        self.assertEqual(export.exported_seats(c), ["alpha", "beta", "gamma", "eps", "idle"])

    def test_exported_seats_is_seat_items(self):
        c = self.make_config(alpha={"fallback": "idle"})
        with mock.patch("seats.verify.seat_items", return_value=["beta"]) as si:
            self.assertEqual(export.exported_seats(c), ["beta"])
        si.assert_called_once_with(c)

    def test_role_names(self):
        c = self.make_config()
        self.assertEqual([export.seat_role(c, s) for s in ("alpha", "gamma", "beta", "eps")],
                         ["Planning seat", "Reserved escalation seat", "Blind same-vendor seat", "Peer seat"])

    def test_title_overrides_role(self):
        c = self.make_config(eps={"title": "Fast metered seat"})
        self.assertEqual(export.seat_role(c, "eps"), "Fast metered seat")

    def test_numbers_use_every_panel_under_the_current_model(self):
        data = self.load([
            panel("p1", "2026-09-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (9, 0, 0, 1)}),
            panel("p2", "2026-10-01T00:00:00Z", {"alpha": "old-a"}, {"alpha": (7, 0, 0, 1)}),
            panel("p3", "2026-11-02T00:00:00Z", {"alpha": "model-a"}, {"alpha": (2, 1, 0, 1)}),
            panel("p4", "2026-11-09T00:00:00Z", {"alpha": "model-a"}, {"alpha": (3, 0, 1, 0)})])
        e = export.seat_entry(self.make_config(), data, "alpha")
        self.assertEqual(e["name"], "Planning seat (reference roster, 2026-09, model-a via codex)")
        self.assertIn("to 2026-11-09: 3 panels, 14 confirmed, 1 partial, 1 refuted; 2 unique in the last 3 panels",
                      e["notes"])

    def test_legacy_panel_without_model_not_counted(self):
        legacy = panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"})
        legacy["seats"] = ["alpha"]
        e = export.seat_entry(self.make_config(), self.load([legacy]), "alpha")
        self.assertTrue(e["notes"].endswith("No panels under this model yet."))
        self.assertIn("not yet run", e["name"])

    def test_month_is_stable_from_usage_to_first_panel(self):
        use = {"type": "usage", "seat": "alpha", "model": "model-a", "ok": True, "ts": "2026-10-20T00:00:00Z"}
        before = export.seat_entry(self.make_config(), self.load([use]), "alpha")["name"]
        after = export.seat_entry(
            self.make_config(), self.load([use, panel("p1", "2026-11-02T00:00:00Z", {"alpha": "model-a"})]),
            "alpha")["name"]
        self.assertEqual(before, after)
        self.assertIn("2026-10", after)

    def test_unparsed_ts_does_not_crash(self):
        data = self.load([panel("p1", "garbage", {"alpha": "model-a"})])
        e = export.seat_entry(self.make_config(), data, "alpha")
        self.assertIn("not yet run", e["name"])

    def test_record_date_is_last_counted_panel(self):
        data = self.load([panel("p1", "2026-11-03T10:00:00Z", {"alpha": "model-a"})])
        self.assertIn("to 2026-11-03: 1 panels,", export.seat_entry(self.make_config(), data, "alpha")["notes"])

    def test_status_rules(self):
        both = {"alpha": (1, 0, 0, 1), "beta": (1, 0, 0, 1)}
        five = [panel(f"p{i}", f"2026-11-0{i}T00:00:00Z", {"alpha": "model-a", "beta": "model-b"}, both)
                for i in range(1, 6)]
        c = self.make_config()
        self.assertEqual(export.seat_entry(c, self.load(five), "alpha")["status"], "preferred")
        self.assertEqual(export.seat_entry(c, self.load(five[:4]), "alpha")["status"], "review-required")
        self.assertEqual(export.seat_entry(c, self.load(five), "gamma")["status"], "reserved")
        w = {"type": "watch", "id": "w1", "kind": "redundant-seat", "target": "alpha", "status": "open"}
        self.assertEqual(export.seat_entry(c, self.load(five + [w]), "alpha")["status"], "review-required")

    def test_disputes_counted_under_current_model(self):
        d = {"summary": "s", "challenger": "beta", "proposals": {"alpha": "x", "beta": "y"}, "winner": "alpha"}
        data = self.load([panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a", "beta": "model-b"}, disputes=[d])])
        notes = export.seat_entry(self.make_config(), data, "alpha")["notes"]
        self.assertIn(": 1 panels,", notes)
        self.assertIn("1 of 1 disputes won", notes)

    def test_empty_policy_refused(self):
        with self.assertRaises(export.PackError) as e:
            export.entries(self.make_config(eps={"policy": ""}), self.load([]), self.state)
        self.assertIn("eps", str(e.exception))

    def test_placeholder_policy_refused(self):
        for text in ("<why this seat holds its duties>", "TODO: write this"):
            with self.subTest(text), self.assertRaises(export.PackError) as e:
                export.entries(self.make_config(eps={"policy": text}), self.load([]), self.state)
            self.assertIn("placeholder", str(e.exception))

    def test_colliding_names_get_a_distinguisher(self):
        c = self.make_config(eps={"runtime": "codex", "model": "model-a", "roles": ["plan"], "effort": "high"},
                             alpha={"effort": "low"})
        names = [e["name"] for e in export.entries(c, self.load([]), self.state)]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(any(", effort high)" in n for n in names))

    def test_collision_that_remains_is_refused(self):
        c = self.make_config(eps={"runtime": "codex", "model": "model-a", "roles": ["plan"]})
        with self.assertRaises(export.PackError) as e:
            export.entries(c, self.load([]), self.state)
        self.assertIn("title", str(e.exception))

    def test_entries_are_seats_then_leads(self):
        names = [e["name"] for e in export.entries(self.make_config(), self.load([]), self.state)]
        self.assertEqual(len(names), 5)
        self.assertTrue(names[-1].startswith("Lead ("))


class Leads(Base):
    def test_lead_entry(self):
        s = {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
             "model": "m-lead", "ts": "2026-11-05T09:00:00Z", "project": "secret-project"}
        leads = export.lead_entries(self.make_config(), self.load([s]), self.state)
        self.assertEqual(leads, [{"name": "Lead (reference roster, 2026-11, m-lead via claude-code)",
                                  "status": "review-required", "notes": leads[0]["notes"]}])
        self.assertIn("to 2026-11-05: 1 sessions", leads[0]["notes"])
        self.assertNotIn("secret-project", leads[0]["notes"])

    def test_lead_without_sessions(self):
        (lead,) = export.lead_entries(self.make_config(), self.load([]), self.state)
        self.assertIn("not yet run", lead["name"])
        self.assertTrue(lead["notes"].endswith("No sessions under this model yet."))

    def test_lead_alias_resolves_to_logged_model(self):
        os.makedirs(os.path.join(self.state, "models"))
        with open(os.path.join(self.state, "models", "p1.json"), "w") as f:
            json.dump({"model": "model-opus-9"}, f)
        s = {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
             "model": "model-opus-9", "ts": "2026-11-05T09:00:00Z"}
        c = self.make_config(top={"leads": {"l1": {"runtime": "claude-code", "model": "opus", "plan": "p1"}}})
        (lead,) = export.lead_entries(c, self.load([s]), self.state)
        self.assertIn("model-opus-9 via claude-code", lead["name"])
        self.assertIn("1 sessions", lead["notes"])

    def test_two_leads_on_one_model_named_by_plan(self):
        top = {"leads": {"l1": {"runtime": "claude-code", "model": "m-lead", "plan": "p1"},
                         "l2": {"runtime": "claude-code", "model": "m-lead", "plan": "p2"}},
               "profiles": {"d": {"lead_order": ["l1", "l2"],
                                  "checkpoints": {"review": {"seats": ["alpha", "beta", "eps"]}}}}}
        c = self.make_config(top=top)
        names = [e["name"] for e in export.entries(c, self.load([]), self.state)][-2:]
        self.assertEqual(names, ["Lead (reference roster, not yet run, m-lead via claude-code, p1 plan)",
                                 "Lead (reference roster, not yet run, m-lead via claude-code, p2 plan)"])


FX = HERE / "fixtures" / "packs" / "model-seats" / "pack.yaml"
E1 = {"name": "Planning seat (reference roster, 2026-11, model-a via codex)", "status": "preferred", "notes": "P. Record ..."}


class PackTextTests(unittest.TestCase):
    def test_rewrite_keeps_hand_entries_byte_for_byte(self):
        text = FX.read_text()
        new, old_v, new_v, _ = export.rewrite(text, [E1])
        b0, e0, _ = export.find_block(text)
        b1, e1, _ = export.find_block(new)
        self.assertEqual(text[:b0].replace("version: 0.1.0", ""), new[:b1].replace("version: 0.2.0", ""))
        self.assertEqual(text[e0:], new[e1:])
        self.assertEqual((old_v, new_v), ("0.1.0", "0.2.0"))
        self.assertEqual(export.parse_block(new[b1:e1]), [E1])

    def test_render_values_are_single_line_json(self):
        hostile = 'Quote " hash # colon: ok\nnew line, café, sep   end'
        block = export.render([dict(E1, notes=hostile)], "  ", "\n")
        lines = block.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(l.isascii() for l in lines))
        self.assertEqual(json.loads(lines[3].split("notes: ", 1)[1]), hostile)

    def test_crlf_and_no_final_newline_preserved(self):
        text = FX.read_text().replace("\n", "\r\n").rstrip("\r\n")
        new, *_ = export.rewrite(text, [E1])
        self.assertFalse(new.endswith("\n"))
        self.assertNotIn("\n", new.replace("\r\n", ""))

    def test_bad_markers_refused(self):
        base = FX.read_text()
        begin, end = "  # bw:catalog:begin\n", "  # bw:catalog:end\n"
        cases = {"missing": base.replace(begin, ""), "twice": base.replace(begin, begin * 2),
                 "order": base.replace(begin, "@@").replace(end, begin).replace("@@", end),
                 "above catalog": begin + base.replace(begin, "")}
        for label, text in cases.items():
            with self.subTest(label), self.assertRaises(export.PackError):
                export.find_block(text)

    def test_missing_markers_message_is_the_instruction(self):
        with self.assertRaises(export.PackError) as e:
            export.find_block(FX.read_text().replace("  # bw:catalog:begin\n", ""))
        self.assertIn("# bw:catalog:begin", str(e.exception))

    def test_bad_version_refused(self):
        base = FX.read_text()
        for label, text in {"not semver": base.replace("0.1.0", "0.1"),
                            "twice": base.replace("version: 0.1.0", "version: 0.1.0\nversion: 0.1.0"),
                            "missing": base.replace("version: 0.1.0\n", "")}.items():
            with self.subTest(label), self.assertRaises(export.PackError):
                export.find_version(text)

    def test_version_inside_block_refused(self):
        text = FX.read_text().replace("version: 0.1.0\n", "").replace(
            "  # bw:catalog:end\n", "version: 0.1.0\n  # bw:catalog:end\n")
        with self.assertRaises(export.PackError):
            export.rewrite(text, [E1])

    def test_multiline_value_inside_block_refused(self):
        for bad in ('  - name: "a\n    b"\n', "  - name: x\n    notes: |\n      two\n      lines\n",
                    '  - name: "unterminated\n'):
            with self.subTest(bad), self.assertRaises(export.PackError) as e:
                export.parse_block(bad)
            self.assertIn("inside the bw:catalog markers", str(e.exception))

    def test_one_line_hand_entry_inside_block_is_replaced_and_named(self):
        text = FX.read_text().replace(
            "  # bw:catalog:end\n", "  - name: Hand entry\n    status: reserved\n    notes: x\n  # bw:catalog:end\n")
        new, _, _, old = export.rewrite(text, [E1])
        self.assertEqual([e["name"] for e in old], ["Hand entry"])
        self.assertNotIn("Hand entry", new)
        self.assertIn("removed", export.bullet("0.1.0", "0.2.0", old, [E1], "2026-11-22"))

    def test_bump_rule(self):
        num = dict(E1, notes="P. Record ... 3 panels")
        self.assertIsNone(export.bump_kind([E1], [E1]))
        self.assertEqual(export.bump_kind([E1], [num]), "patch")
        self.assertEqual(export.bump_kind([E1], [dict(E1, status="review-required")]), "minor")
        self.assertEqual(export.bump_kind([E1], [dict(E1, name=E1["name"].replace("model-a", "model-a2"))]), "minor")
        self.assertEqual(export.bump_kind([E1], []), "minor")
        self.assertEqual([export.next_version((0, 5, 3), k) for k in ("minor", "patch")], ["0.6.0", "0.5.4"])

    def test_no_change_keeps_text_and_version(self):
        once, *_ = export.rewrite(FX.read_text(), [E1])
        again, old_v, new_v, _ = export.rewrite(once, [E1])
        self.assertEqual((again, new_v), (once, None))

    def test_bullet_names_changes(self):
        b = export.bullet("0.1.0", "0.2.0", [], [E1], "2026-11-22")
        self.assertTrue(b.startswith("- `model-seats` 0.2.0 (2026-11-22,"))
        self.assertIn("added", b)
        self.assertIn(E1["name"], b)


BW = str(HERE.parent / "bin" / "bw")
GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def commit_all(repo):
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(["git", "-C", repo, *GIT_ID, "commit", "-qm", "init"], check=True)


def pack_repo(path, text=None, add=True):
    os.makedirs(path)
    Path(path, "pack.yaml").write_text(FX.read_text() if text is None else text)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)
    if add:
        commit_all(str(path))
    return str(path)


class ExportRun(Base):
    def setUp(self):
        super().setUp()
        self.repo = pack_repo(Path(self.tmp) / "esc")
        self.log = os.path.join(self.tmp, "m.jsonl")
        self.cfg = self.make_config(top={"state_dir": self.state, "metrics_log": self.log})
        self.data = self.load([panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)})])

    def pack(self):
        return Path(self.repo, "pack.yaml")

    def test_export_writes_block_version_bullet_and_event(self):
        r = export.export(self.cfg, self.data, self.repo, self.state, today="2026-11-22")
        text = self.pack().read_text()
        self.assertTrue(r["changed"])
        self.assertIn("version: 0.2.0", text)
        self.assertTrue(Path(self.state, "packs", "model-seats-0.2.0.md").read_text().startswith("- `model-seats` 0.2.0"))
        self.assertEqual([e["type"] for e in store.load(self.log)["events"]][-1], "pack_export")

    def test_dry_run_writes_nothing(self):
        before = self.pack().read_bytes()
        r = export.export(self.cfg, self.data, self.repo, self.state, dry_run=True)
        self.assertTrue(r["diff"])
        self.assertTrue(r["bullet"])
        self.assertEqual(self.pack().read_bytes(), before)
        self.assertFalse(Path(self.state, "packs").exists())
        self.assertFalse(Path(self.log).exists())

    def test_second_export_is_a_no_op(self):
        export.export(self.cfg, self.data, self.repo, self.state)
        commit_all(self.repo)
        r = export.export(self.cfg, self.data, self.repo, self.state)
        self.assertFalse(r["changed"])
        self.assertIsNone(r["new_version"])

    def refused(self, repo, part, **kw):
        with self.assertRaises(export.PackError) as e:
            export.export(self.cfg, self.data, repo, self.state, **kw)
        self.assertIn(part, str(e.exception))

    def test_wrong_name_refused(self):
        repo = pack_repo(Path(self.tmp) / "o", FX.read_text().replace("name: model-seats", "name: other-pack"))
        self.refused(repo, "not model-seats")

    def test_dirty_file_refused(self):
        with open(self.pack(), "a") as f:
            f.write("# more\n")
        self.refused(self.repo, "uncommitted changes")

    def test_untracked_file_refused(self):
        self.refused(pack_repo(Path(self.tmp) / "u", add=False), "not tracked by git")

    def test_ignored_untracked_file_refused(self):
        repo = pack_repo(Path(self.tmp) / "i", add=False)
        Path(repo, ".gitignore").write_text("pack.yaml\n")
        self.refused(repo, "not tracked by git")

    def test_git_timeout_reported(self):
        self.refused(self.repo, "git timed out", git_rc=lambda d, *a: (124, "", "x"))

    def test_git_config_unreadable_reported(self):
        self.refused(self.repo, "could not be read", git_rc=lambda d, *a: (125, "", "x"))

    def test_not_a_git_checkout_refused(self):
        d = Path(self.tmp) / "plain"
        d.mkdir()
        (d / "pack.yaml").write_text(FX.read_text())
        self.refused(str(d), "not a git checkout")

    def test_missing_file_refused(self):
        d = Path(self.tmp) / "empty"
        d.mkdir()
        self.refused(str(d), "pack.yaml")

    def test_symlink_refused(self):
        d = Path(self.tmp) / "sl"
        d.mkdir()
        (d / "real.yaml").write_text(FX.read_text())
        os.symlink("real.yaml", d / "pack.yaml")
        self.refused(str(d), "symlink")

    def test_bullet_write_failure_leaves_pack_unchanged(self):
        Path(self.state, "packs").write_text("a file")
        before = self.pack().read_bytes()
        with self.assertRaises(OSError):
            export.export(self.cfg, self.data, self.repo, self.state)
        self.assertEqual(self.pack().read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.repo)), [".git", "pack.yaml"])

    def test_changed_during_export_refused(self):
        def git_rc(d, *a):
            Path(d, "pack.yaml").write_text("changed\n")
            return 0, "", ""
        self.refused(self.repo, "changed during the export", git_rc=git_rc)

    def test_mode_is_kept(self):
        os.chmod(self.pack(), 0o600)
        export.export(self.cfg, self.data, self.repo, self.state, git_rc=lambda d, *a: (0, "", ""))
        self.assertEqual(self.pack().stat().st_mode & 0o7777, 0o600)

    def test_event_failure_does_not_undo_the_write(self):
        with mock.patch("store.append", side_effect=OSError("disk")):
            r = export.export(self.cfg, self.data, self.repo, self.state)
        self.assertTrue(r["changed"])
        self.assertEqual(r["event_error"], "OSError")
        self.assertIn("version: 0.2.0", self.pack().read_text())

    def test_privacy_no_target_title_path_or_project_leaks(self):
        canary = "CANARY-7f3a"
        dispute = {"summary": f"{canary}-dsummary", "challenger": "alpha", "winner": "alpha",
                   "proposals": {"alpha": f"{canary}-proposal"}, "reason": f"{canary}-dreason"}
        events = [panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)},
                        target=f"{canary}-target", notes=f"{canary}-note", disputes=[dispute],
                        absent={"beta": f"{canary}-absent"},
                        findings_detail=[{"seat": "alpha", "title": f"{canary}-title", "where": f"/{canary}/x.py:1",
                                          "claim": f"{canary}-claim", "evidence": f"{canary}-evidence",
                                          "ref": f"{canary}-ref", "notes": f"{canary}-fnote",
                                          "verdict": "confirmed", "severity": "major"}]),
                  {"type": "watch", "id": "w1", "kind": "redundant-seat", "target": "alpha", "status": "open",
                   "detail": f"{canary}-wdetail"},
                  {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
                   "model": "m-lead", "project": f"{canary}-project", "worktree": f"/{canary}",
                   "ts": "2026-11-01T00:00:00Z"},
                  {"type": "usage", "seat": "alpha", "model": "model-a", "dir": f"/{canary}/repo", "ok": True}]
        r = export.export(self.cfg, self.load(events), self.repo, self.state, today="2026-11-22")
        for out in (r["diff"], r["bullet"], self.pack().read_text(), Path(r["bullet_path"]).read_text(),
                    Path(self.log).read_text().splitlines()[-1]):
            self.assertNotIn(canary, out)


class Cli(Base):
    def setUp(self):
        super().setUp()
        self.repo = pack_repo(Path(self.tmp) / "esc")
        self.log = os.path.join(self.tmp, "m.jsonl")
        self.log2 = os.path.join(self.tmp, "m2.jsonl")
        obj = copy.deepcopy(BASE)
        obj.update({"state_dir": self.state, "metrics_log": self.log, "improve": {"panel_logs": [self.log2]}})
        self.config = os.path.join(self.tmp, "cli.json")
        Path(self.config).write_text(json.dumps(obj))
        self.env = {k: v for k, v in os.environ.items() if k != "BALANCEWHEEL_METRICS_LOG"}
        self.env.update(BALANCEWHEEL_CONFIG=self.config, BW_STATE_DIR=self.state)

    def bw(self, *args):
        return subprocess.run([sys.executable, BW, "pack", "export", "--to", self.repo, *args],
                              capture_output=True, text=True, env=self.env)

    def test_cli_dry_run(self):
        before = Path(self.repo, "pack.yaml").read_bytes()
        r = self.bw("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("bw: dry run; nothing written", r.stdout)
        self.assertEqual(Path(self.repo, "pack.yaml").read_bytes(), before)

    def test_cli_refusal(self):
        with open(Path(self.repo, "pack.yaml"), "a") as f:
            f.write("# more\n")
        r = self.bw()
        self.assertEqual(r.returncode, 1)
        self.assertIn("bw: pack export:", r.stderr)

    def test_cli_success_says_paste_and_resign(self):
        store.append(self.log, panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)}))
        r = self.bw()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("paste the bullet", r.stdout)
        self.assertIn("re-sign the pack with esc", r.stdout)

    def test_cli_unchanged(self):
        self.assertEqual(self.bw().returncode, 0)
        commit_all(self.repo)
        r = self.bw()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pack is current at", r.stdout)

    def test_cli_reads_every_log(self):
        store.append(self.log, panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)}))
        for e in (panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)}),
                  panel("p2", "2026-11-05T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 0)}),
                  {"type": "usage", "seat": "beta", "model": "model-b", "ts": "2026-10-07T00:00:00Z", "ok": True},
                  {"type": "watch", "id": "w1", "kind": "redundant-seat", "target": "alpha", "status": "open"}):
            store.append(self.log2, e)
        r = self.bw()
        self.assertEqual(r.returncode, 0, r.stderr)
        text = Path(self.repo, "pack.yaml").read_text()
        self.assertIn("2 panels", text)
        self.assertIn("review-required", text)
        self.assertIn("2026-10", text)


if __name__ == "__main__":
    unittest.main()
