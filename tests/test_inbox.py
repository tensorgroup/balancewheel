import hashlib, json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "metrics"))
from lead import inbox  # noqa: E402
from lead.projects import resolve_project  # noqa: E402

try:
    import test_sandbox as _harness  # unittest discover -s tests
except ImportError:
    from tests import test_sandbox as _harness  # python3 -m unittest tests.test_inbox

KEY = "0123456789ab"
CANARY = "SECRET-c4n4ry"


class InboxCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.state = str(self.root / "state")
        self.asks = Path(inbox.asks_dir(self.state, KEY))
        self.asks.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, name, text):
        tmp = self.asks / f".tmp-{name}"
        tmp.write_bytes(text if isinstance(text, bytes) else text.encode())
        os.rename(tmp, self.asks / f"{name}.ask")

    def ingest(self, session="s1"):
        return inbox.ingest(self.state, KEY, "/w", "la", session)

    def items(self):
        return inbox.load(self.state)["items"]


class Ingest(InboxCase):
    def test_two_asks_become_two_records_and_the_files_go(self):
        self.publish("a", "@human ask\nwhich db?\nmore")
        self.publish("b", "deploy now?")
        recs, note = self.ingest()
        self.assertIsNone(note)
        self.assertEqual([r["text"] for r in recs], ["which db?\nmore", "deploy now?"])
        self.assertEqual(list(self.asks.iterdir()), [])
        r = recs[0]
        self.assertEqual((r["type"], r["project"], r["worktree"], r["from"], r["session_id"], r["status"]),
                         ("ask", KEY, "/w", "la", "s1", "open"))
        self.assertEqual(r["id"], hashlib.sha1(b"a.ask" + b"@human ask\nwhich db?\nmore").hexdigest()[:8])
        self.assertEqual(len(self.items()), 2)
        self.assertEqual(os.stat(inbox.path(self.state)).st_mode & 0o777, 0o600)

    def test_half_written_tmp_file_ignored_and_left(self):
        (self.asks / ".tmp-x").write_text("half")
        (self.asks / ".tmp-y.ask").write_text("half")
        (self.asks / "notes.txt").write_text("not an ask")
        self.assertEqual(self.ingest(), ([], None))
        self.assertEqual(sorted(p.name for p in self.asks.iterdir()), [".tmp-x", ".tmp-y.ask", "notes.txt"])

    def test_reingest_after_a_crash_before_unlink_is_one_record(self):
        self.publish("a", "q?")
        data = (self.asks / "a.ask").read_bytes()
        self.ingest()
        (self.asks / "a.ask").write_bytes(data)  # as if bw died after recording, before unlinking
        recs, _ = self.ingest()
        self.assertEqual(recs, [])
        self.assertEqual(len(self.items()), 1)
        self.assertFalse((self.asks / "a.ask").exists())

    def test_symlink_to_a_canary_is_not_read_and_removed(self):
        canary = self.root / "canary"; canary.write_text(CANARY)
        os.symlink(canary, self.asks / "s.ask")
        recs, _ = self.ingest()
        self.assertEqual(recs, [])
        self.assertFalse(os.path.lexists(self.asks / "s.ask"))
        self.assertEqual(canary.read_text(), CANARY)
        self.assertNotIn(CANARY, Path(inbox.path(self.state)).read_text() if os.path.exists(inbox.path(self.state)) else "")

    def test_fifo_returns_quickly(self):
        os.mkfifo(self.asks / "x.ask")
        t0 = time.time()
        recs, _ = self.ingest()
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(recs, [])
        self.assertFalse(os.path.lexists(self.asks / "x.ask"))

    def test_directory_skipped_with_one_notice(self):
        (self.asks / "y.ask").mkdir()
        recs, note = self.ingest()
        self.assertEqual(recs, [])
        self.assertIn("y.ask", note)
        self.assertTrue((self.asks / "y.ask").is_dir())

    def test_thousands_of_ask_directories_make_a_bounded_notice(self):
        for i in range(5000):
            (self.asks / (f"{i:04d}" + "\x1b" * 200 + "d" * 47 + ".ask")).mkdir()
        recs, note = self.ingest()
        self.assertEqual(recs, [])
        self.assertLess(len(note), 2000)
        self.assertIn("and 4995 more", note)

    def test_big_file_reads_64_kib(self):
        data = b"q" * (10 * 1024 * 1024)
        self.publish("big", data)
        recs, _ = self.ingest()
        self.assertEqual(recs[0]["id"], hashlib.sha1(b"big.ask" + data[:64 * 1024]).hexdigest()[:8])
        self.assertEqual(recs[0]["text"], "q" * 2000 + " [truncated]")

    def test_500_asks_make_50_records_and_one_notice(self):
        for i in range(500):
            self.publish(f"a{i:03d}", f"question {i}")
        recs, note = self.ingest()
        self.assertEqual(len(recs), 50)
        self.assertIn("450", note)
        self.assertEqual(list(self.asks.iterdir()), [])
        self.publish("late", "one more")
        recs, note = self.ingest()  # the same session is still over its limit
        self.assertEqual(recs, [])
        self.assertIn("1 ", note)
        self.assertEqual(len(self.ingest(session="s2")[0]), 0)

    def test_asks_dir_replaced_by_a_file_is_a_notice_not_an_exception(self):
        shutil.rmtree(self.asks); self.asks.write_text("x")
        recs, note = self.ingest()
        self.assertEqual(recs, [])
        self.assertIn("asks", note)

    def test_asks_dir_symlinked_elsewhere_is_not_followed(self):
        other = self.root / "other"; other.mkdir(); (other / "v.ask").write_text("victim")
        shutil.rmtree(self.asks); os.symlink(other, self.asks)
        recs, note = self.ingest()
        self.assertEqual(recs, [])
        self.assertIsNotNone(note)
        self.assertTrue((other / "v.ask").exists(), "bw unlinked a file through a symlinked asks dir")

    def test_an_unwritable_inbox_is_a_notice_and_the_ask_stays(self):
        self.publish("a", "q?")
        with mock.patch.object(inbox.store, "append", side_effect=OSError("disk full")):
            recs, note = self.ingest()
        self.assertEqual(recs, [])
        self.assertIn("OSError", note)
        self.assertTrue((self.asks / "a.ask").exists())


class Records(InboxCase):
    def test_answer_unknown_and_twice(self):
        with self.assertRaises(KeyError):
            inbox.answer(self.state, "nope", "x")
        self.publish("a", "q?")
        (r,), _ = self.ingest()
        a = inbox.answer(self.state, r["id"], "yes")
        self.assertEqual((a["type"], a["ask_id"], a["from"], a["text"]), ("answer", r["id"], "user", "yes"))
        with self.assertRaises(ValueError):
            inbox.answer(self.state, r["id"], "again")
        item = self.items()[0]
        self.assertEqual(item["status"], "answered")
        self.assertEqual(item["answer"]["text"], "yes")
        self.assertEqual(inbox.open_items(self.state), [])

    def test_duplicate_notices_collapse_and_an_answered_one_closes(self):
        n = inbox.notice(self.state, KEY, "all-capped", "all leads capped")
        self.assertIsNotNone(n)
        self.assertIsNone(inbox.notice(self.state, KEY, "all-capped", "all leads capped again"))
        self.assertIsNotNone(inbox.notice(self.state, "otherkey00000", "all-capped", "x"))
        self.assertIsNotNone(inbox.notice(self.state, KEY, "inbox", "x"))
        self.assertEqual(len(inbox.open_items(self.state, KEY)), 2)
        inbox.answer(self.state, n["id"], "ok")
        self.assertEqual([i["kind"] for i in inbox.open_items(self.state, KEY)], ["inbox"])
        self.assertIsNotNone(inbox.notice(self.state, KEY, "all-capped", "capped again"))
        with self.assertRaises(ValueError):
            inbox.notice(self.state, KEY, "made-up", "x")
        self.assertEqual(inbox.NOTICE_KINDS,
                         {"all-capped", "pinned-capped", "router-shadow", "guard-unreachable", "inbox"})

    def test_view_holds_answers_and_ask_ids_never_ask_text(self):
        self.publish("a", "ASK-TEXT-secret question")
        self.publish("b", "unanswered one")
        (r, _r2), _ = self.ingest()
        inbox.answer(self.state, r["id"], "use \x1b[2J postgres")
        inbox.write_view(self.state, KEY)
        view = Path(inbox.view_path(self.state, KEY))
        self.assertEqual(view.parent, Path(self.state) / "inbox-view" / KEY)
        body = view.read_text()
        self.assertIn(f"@lead answer [id:{r['id']}]", body)
        self.assertIn("use \\x1b[2J postgres", body)
        self.assertNotIn("\x1b", body)
        self.assertNotIn("ASK-TEXT", body)
        self.assertNotIn("unanswered", body)
        self.assertEqual([p.name for p in view.parent.iterdir()], ["answers.md"])  # no temp left behind

    def test_handoff_items_are_bounded(self):
        for i in range(200):
            inbox.ask(self.state, KEY, "/w", "la", "s1", f"q{i} " + "\x1b" * 1995 + "\nmore", f"{i:08x}")
        lines = inbox.handoff_items(self.state, KEY)
        self.assertEqual(len(lines), inbox.HANDOFF_ITEMS + 1)
        self.assertTrue(all(len(l) <= inbox.HANDOFF_ITEM_CHARS for l in lines))
        self.assertEqual(lines[-1], "190 more — run `bw inbox --project`")
        self.assertNotIn("\x1b", "".join(lines))
        from lead.handoff import build_handoff, INBOX_LIMIT
        h = build_handoff(str(self.root), None, self.state, KEY, ["x" * 5000] * 10)
        self.assertLessEqual(len(h.split("open questions for the user:\n", 1)[1]), INBOX_LIMIT)

    def test_load_reports_an_answer_to_nothing(self):
        inbox._append(self.state, {"type": "answer", "id": "x", "ask_id": "gone", "from": "user", "text": "t"})
        self.assertTrue(any("gone" in w for w in inbox.load(self.state)["warnings"]))


@unittest.skipUnless(shutil.which("sandbox-exec"), "macOS sandbox-exec required")
class ViewUnwritableSeatbelt(unittest.TestCase):
    """A contained lead cannot write the answers view (Task 0a's escape harness)."""
    backend = "sandbox-exec"
    setUp, tearDown, sh = _harness.EscapesUnderSeatbelt.setUp, _harness.EscapesUnderSeatbelt.tearDown, \
        _harness.EscapesUnderSeatbelt.sh

    def test_contained_lead_cannot_append_to_the_view(self):
        key = resolve_project(str(self.wt), {"projects": {}, "default_profile": "d"}).worktree_key
        a = Path(inbox.asks_dir(str(self.state), key)); a.mkdir(parents=True)
        (a / "q.ask").write_text("q?")
        (r,), _ = inbox.ingest(str(self.state), key, str(self.wt), "la", "s")
        inbox.answer(str(self.state), r["id"], "ANSWER")
        inbox.write_view(str(self.state), key)
        view = Path(inbox.view_path(str(self.state), key))
        before = view.read_bytes()
        out = self.sh(self.wt, f'echo x >> "{view}"; cat "{view}"')
        self.assertIn("ANSWER", out.stdout)  # readable
        self.assertEqual(view.read_bytes(), before)


@unittest.skipUnless(shutil.which("nono"), "nono not installed")
class ViewUnwritableNono(ViewUnwritableSeatbelt):
    backend = "nono"


class Cli(InboxCase):
    BW = str(ROOT / "bin" / "bw")

    def setUp(self):
        super().setUp()
        self.repo = self.root / "app"; self.repo.mkdir()
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        self.log = self.root / "metrics.jsonl"
        cfg = {"schema_version": 2, "plans": {"pa": {"billing": "plan", "quota": "none"}},
               "leads": {"la": {"runtime": "codex", "model": "a", "plan": "pa"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}},
               "profiles": {"p": {"lead_order": ["la"]}}, "default_profile": "p",
               "state_dir": self.state, "metrics_log": str(self.log)}
        self.cfg = self.root / "config.json"; self.cfg.write_text(json.dumps(cfg))
        self.key = resolve_project(str(self.repo), {"projects": {}, "default_profile": "p"}).worktree_key
        self.asks = Path(inbox.asks_dir(self.state, self.key)); self.asks.mkdir(parents=True)

    def bw(self, *args):
        env = {**os.environ, "BALANCEWHEEL_CONFIG": str(self.cfg), "BW_STATE_DIR": self.state,
               "BW_ORCA_SUPPORT": str(self.root / "no-orca"), "BW_CCSWITCH_DIR": str(self.root / "no-cc")}
        return subprocess.run([sys.executable, self.BW, *args], cwd=str(self.repo), env=env,
                              capture_output=True, text=True)

    def test_inbox_lists_escaped_and_answer_closes(self):
        self.publish("a", "wipe? \x1b[2J now")
        (r,), _ = inbox.ingest(self.state, self.key, str(self.repo), "la", "s1")
        inbox.notice(self.state, "elsewhere000", "all-capped", "all leads capped")
        out = self.bw("inbox").stdout
        self.assertIn(r["id"], out)
        self.assertIn("wipe? \\x1b[2J now", out)
        self.assertNotIn("\x1b", out)
        self.assertIn("all-capped", out)
        self.assertNotIn("all-capped", self.bw("inbox", "--project").stdout)
        res = self.bw("answer", r["id"], "no, keep it")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("the lead sees it in its inbox file; tell it to look", res.stdout)
        self.assertIn("no, keep it", Path(inbox.view_path(self.state, self.key)).read_text())
        self.assertNotIn(r["id"], self.bw("inbox").stdout)
        self.assertIn(r["id"], self.bw("inbox", "--all").stdout)
        again = self.bw("answer", r["id"], "twice")
        self.assertEqual(again.returncode, 1)
        self.assertIn("already answered", again.stderr)
        events = [json.loads(l) for l in self.log.read_text().splitlines()]
        ans = [e for e in events if e["type"] == "answer"]
        self.assertEqual(len(ans), 1)
        self.assertEqual(ans[0]["ask_id"], r["id"])
        self.assertNotIn("text", ans[0])

    def test_answer_survives_a_failed_view_refresh(self):
        self.publish("a", "q?")
        (r,), _ = inbox.ingest(self.state, self.key, str(self.repo), "la", "s1")
        view_dir = Path(self.state) / "inbox-view"
        view_dir.write_text("not a dir")  # write_view cannot create its dir
        res = self.bw("answer", r["id"], "yes")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("Traceback", res.stderr)
        self.assertIn("recorded", res.stdout + res.stderr)
        self.assertIn("next session start", res.stdout + res.stderr)
        self.assertEqual(inbox.load(self.state)["items"][0]["status"], "answered")

    def test_answer_unknown_id_exits_1_naming_it(self):
        res = self.bw("answer", "deadbeef", "x")
        self.assertEqual(res.returncode, 1)
        self.assertIn("deadbeef", res.stderr)

    def test_status_lists_open_asks(self):
        self.publish("a", "which \x1b[2J branch?")
        (r,), _ = inbox.ingest(self.state, self.key, str(self.repo), "la", "s1")
        out = self.bw("status").stdout
        self.assertIn("inbox:", out)
        self.assertIn(r["id"], out)
        self.assertIn("which \\x1b[2J branch?", out)
        self.assertNotIn("\x1b", out)


if __name__ == "__main__":
    unittest.main()
