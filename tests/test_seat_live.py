import json, os, shutil, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from seats import live  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "live"
OLD, NEW = 1_700_000_000, 1_700_000_100


def _codex_lines(cwd, ctx):
    return [{"type": "session_meta", "payload": {"cwd": cwd}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": ctx}, "model_context_window": 258000,
                "total_token_usage": {"input_tokens": ctx, "output_tokens": 1}}}}]


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".250Z"


def _claude_lines(began, ctx):
    return [{"type": "user", "timestamp": _iso(began), "message": {"role": "user"}},
            {"type": "assistant", "timestamp": _iso(began + 5), "message": {"id": "m", "usage": {
                "input_tokens": ctx, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                "output_tokens": 1}}}]


def _pi_lines(cwd, ctx):
    return [{"type": "session", "cwd": cwd},
            {"type": "message", "message": {"role": "assistant", "usage": {
                "input": ctx, "output": 1, "cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0.5}}}}]


def _kimi_lines(ctx):
    return [{"type": "token_counting.measured", "tokens": ctx}]


class SeatLive(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.home)

    def tearDown(self):
        self.tmp.cleanup()

    # -- helpers --------------------------------------------------------------------------------
    def write(self, rel, lines, mtime=None):
        p = Path(self.home, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(x) + "\n" for x in lines))
        if mtime is not None:
            os.utime(p, (mtime, mtime))
        return str(p)

    def copy_to_home(self, src, dest):
        p = Path(self.home, dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIX / src, p)
        return self.home

    def kimi_index(self, entries):
        self.write("session_index.jsonl", entries)

    def two_worktree_home(self, rt):
        """/w1 has the older session (ctx 1000); /w2 the newer (ctx 2000), next to it where the layout allows."""
        if rt == "codex":
            self.write("sessions/2026/11/27/rollout-a.jsonl", _codex_lines("/w1", 1000), OLD)
            self.write("sessions/2026/11/27/rollout-b.jsonl", _codex_lines("/w2", 2000), NEW)
        elif rt == "pi":
            self.write("sessions/--w1--/a.jsonl", _pi_lines("/w1", 1000), OLD)
            # a newer file for /w2 inside /w1's folder: only the header's cwd tells them apart
            self.write("sessions/--w1--/b.jsonl", _pi_lines("/w2", 2000), NEW)
            self.write("sessions/--w2--/b.jsonl", _pi_lines("/w2", 2000), NEW)
        elif rt == "kimi":
            self.write("sessions/h1/session_a/agents/main/wire.jsonl", _kimi_lines(1000), OLD)
            self.write("sessions/h2/session_b/agents/main/wire.jsonl", _kimi_lines(2000), NEW)
            self.kimi_index([
                {"sessionId": "a", "sessionDir": os.path.join(self.home, "sessions/h1/session_a"), "workDir": "/w1"},
                {"sessionId": "b", "sessionDir": "h2/session_b", "workDir": "/w2"}])
        return self.home

    # -- the four runtimes ---------------------------------------------------------------------
    def test_codex_from_the_seat_home(self):
        u = live.live_usage("codex", str(FIX / "codex"), None, "/w", 0.0)
        self.assertEqual((u["ctx"], u["window"], u["tokens_in"], u["tokens_out"], u["ctx_peak"]),
                         (41000, 258000, 90000, 1200, 41000))
        self.assertIsNone(u["cost_usd"])

    def test_claude_slug_and_sum(self):
        home = self.copy_to_home("claude-code/session.jsonl", ".claude/projects/-w-a-b/s1.jsonl")
        u = live.live_usage("claude-code", home, None, "/w/a.b", 0.0)
        self.assertEqual((u["ctx"], u["tokens_out"], u["window"]), (113000, 900, None))
        self.assertEqual(u["ctx_peak"], 113000)

    def test_claude_counts_each_message_id_once(self):
        # msg_a is split over two lines (300 once, not 600); msg_b keeps its last line (60);
        # the two lines with no id count one by one (5 + 5)
        home = self.copy_to_home("claude-code/split-message.jsonl", ".claude/projects/-w/s1.jsonl")
        u = live.live_usage("claude-code", home, None, "/w", 0.0)
        self.assertEqual((u["tokens_out"], u["ctx"], u["ctx_peak"]), (370, 20010, 90100))

    def test_pi_cost_and_window_from_config(self):
        home = self.copy_to_home("pi/session.jsonl", "sessions/--w--/x.jsonl")
        u = live.live_usage("pi", home, None, "/w", 0.0, {"provider": {"contextWindow": 1310720}})
        self.assertEqual((u["ctx"], u["cost_usd"], u["window"]), (5000, 0.02, 1310720))
        self.assertEqual(u["ctx_peak"], 5000)
        u = live.live_usage("pi", home, None, "/w", 0.0, {"provider": {"contextWindow": True}})
        self.assertIsNone(u["window"])

    def test_kimi_from_the_index(self):
        self.copy_to_home("kimi/session.jsonl", "sessions/wd/session_1/agents/main/wire.jsonl")
        self.kimi_index([{"sessionId": "s1", "sessionDir": "wd/session_1", "workDir": "/w"}])
        u = live.live_usage("kimi", self.home, None, "/w", 0.0)
        self.assertEqual((u["ctx"], u["ctx_peak"], u["window"]), (7000, 7000, None))
        self.assertEqual(live.live_usage("kimi", self.home, None, "/other", 0.0), live.EMPTY)

    def test_kimi_session_dir_outside_the_root_is_refused(self):
        self.write("elsewhere/session_1/agents/main/wire.jsonl", _kimi_lines(5))
        self.kimi_index([{"sessionId": "s1", "sessionDir": "../elsewhere/session_1", "workDir": "/w"},
                         {"sessionId": "s2", "sessionDir": os.path.join(self.home, "elsewhere/session_1"),
                          "workDir": "/w"}])
        self.assertEqual(live.live_usage("kimi", self.home, None, "/w", 0.0), live.EMPTY)

    def test_two_worktrees_never_mix(self):
        for rt in ("codex", "pi", "kimi"):
            home = self.two_worktree_home(rt)
            self.assertEqual(live.live_usage(rt, home, None, "/w1", 0.0)["ctx"], 1000, rt)
            self.assertEqual(live.live_usage(rt, home, None, "/w2", 0.0)["ctx"], 2000, rt)

    # -- choosing the file ---------------------------------------------------------------------
    def test_session_id_picks_its_file_else_newest_since_start(self):
        self.write("sessions/2026/11/27/rollout-2026-11-27T10-00-00-abc123.jsonl", _codex_lines("/w", 1000), OLD)
        self.write("sessions/2026/11/27/rollout-2026-11-27T11-00-00-def456.jsonl", _codex_lines("/w", 2000), NEW)
        self.assertEqual(live.live_usage("codex", self.home, "abc123", "/w", float(NEW))["ctx"], 1000)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", 0.0)["ctx"], 2000)
        self.assertEqual(live.live_usage("codex", self.home, "zzz999", "/w", 0.0)["ctx"], 2000)
        # an id that is not a valid session id is ignored, never matched as a pattern
        self.assertEqual(live.live_usage("codex", self.home, "../abc123", "/w", 0.0)["ctx"], 2000)
        # kimi matches on the index's sessionId
        self.two_worktree_home("kimi")
        self.write("sessions/h1/session_c/agents/main/wire.jsonl", _kimi_lines(3000), NEW + 50)
        self.kimi_index([
            {"sessionId": "a", "sessionDir": "h1/session_a", "workDir": "/w1"},
            {"sessionId": "c", "sessionDir": "h1/session_c", "workDir": "/w1"}])
        self.assertEqual(live.live_usage("kimi", self.home, "a", "/w1", 0.0)["ctx"], 1000)
        self.assertEqual(live.live_usage("kimi", self.home, None, "/w1", 0.0)["ctx"], 3000)

    def test_file_older_than_start_is_ignored(self):
        self.write("sessions/2026/11/27/rollout-a.jsonl", _codex_lines("/w", 1000), OLD)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", OLD + 2.0), live.EMPTY)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", OLD + 0.5)["ctx"], 1000)

    def test_agy_missing_link_and_changed_format_give_empty(self):
        self.assertEqual(live.live_usage("agy", self.home, None, "/w", 0.0), live.EMPTY)
        self.assertEqual(live.live_usage("codex", "/nonexistent", None, "/w", 0.0), live.EMPTY)
        self.assertEqual(live.live_usage("nope", self.home, None, "/w", 0.0), live.EMPTY)
        # a symlinked session file is not followed
        real = self.write("outside/real.jsonl", _codex_lines("/w", 1000))
        link = Path(self.home, "sessions/2026/11/27/rollout-l.jsonl")
        link.parent.mkdir(parents=True)
        os.symlink(real, link)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", 0.0), live.EMPTY)
        claude_link = Path(self.home, ".claude/projects/-w/s.jsonl")
        claude_link.parent.mkdir(parents=True)
        os.symlink(self.copy_to_home("claude-code/session.jsonl", "outside/c.jsonl") + "/outside/c.jsonl",
                   claude_link)
        self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", 0.0), live.EMPTY)
        # a file of other JSON gives EMPTY
        os.unlink(link)
        self.copy_to_home("changed.jsonl", "sessions/2026/11/27/rollout-c.jsonl")
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", 0.0), live.EMPTY)
        self.copy_to_home("changed.jsonl", ".claude/projects/-v/s.jsonl")
        self.assertEqual(live.live_usage("claude-code", self.home, None, "/v", 0.0), live.EMPTY)
        self.copy_to_home("changed.jsonl", "sessions/--v--/s.jsonl")
        self.assertEqual(live.live_usage("pi", self.home, None, "/v", 0.0), live.EMPTY)
        # a non-text, non-JSON file gives EMPTY too
        Path(self.home, "sessions/--v--/s.jsonl").write_bytes(b"\xff\xfe\x00garbage\n")
        self.assertEqual(live.live_usage("pi", self.home, None, "/v", 0.0), live.EMPTY)

    def test_result_is_a_fresh_dict(self):
        u = live.live_usage("agy", self.home, None, "/w", 0.0)
        u["ctx"] = 1
        self.assertIsNone(live.EMPTY["ctx"])

    def test_reads_only_the_tail(self):
        line = {"type": "assistant", "message": {"usage": {
            "input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "output_tokens": 1, "pad": "x" * 200}}}
        n = (3 * 1024 * 1024) // len(json.dumps(line)) + 1
        last = {"type": "assistant", "message": {"usage": {
            "input_tokens": 113000, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "output_tokens": 1}}}
        self.write(".claude/projects/-w/s.jsonl", [line] * n + [last])
        u = live.live_usage("claude-code", self.home, None, "/w", 0.0)
        self.assertEqual((u["ctx"], u["ctx_peak"]), (113000, 113000))
        self.assertLess(u["tokens_out"], n // 2)  # only about a third of the lines were read
        # codex: the cwd comes from the first line, which is far outside the tail
        tok = _codex_lines("/w", 41000)[1]
        tok["payload"]["pad"] = "x" * 200
        self.write("sessions/2026/11/27/rollout-big.jsonl",
                   [{"type": "session_meta", "payload": {"cwd": "/w"}}] + [tok] * n)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", 0.0)["ctx"], 41000)

    def test_first_line_over_the_bound_is_no_match(self):
        meta = {"type": "session", "cwd": "/w", "pad": "x" * (70 * 1024)}
        self.write("sessions/--w--/x.jsonl", [meta] + _pi_lines("/w", 5)[1:])
        self.assertEqual(live.live_usage("pi", self.home, None, "/w", 0.0), live.EMPTY)

    def test_candidates_are_capped(self):
        for i in range(live.MAX_CANDIDATES + 5):
            self.write(f"sessions/--w--/{i:04d}.jsonl", _pi_lines("/other", 9), NEW + i)
        self.write("sessions/--w--/old.jsonl", _pi_lines("/w", 5), OLD)
        self.assertEqual(live.live_usage("pi", self.home, None, "/w", 0.0), live.EMPTY)

    def test_an_older_session_in_the_same_folder_is_not_used(self):
        # the lead's own claude-code session for this worktree: begun before start, written to just now
        start = NEW - 50
        self.write(".claude/projects/-w/lead.jsonl", _claude_lines(start - 3600, 1000), NEW)
        self.write(".claude/projects/-w/seat.jsonl", _claude_lines(start + 10, 2000), NEW - 20)
        self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start)["ctx"], 2000)
        os.unlink(os.path.join(self.home, ".claude/projects/-w/seat.jsonl"))
        self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start), live.EMPTY)
        # codex: the session_meta timestamp says when the session began
        meta = _codex_lines("/w", 1000)
        meta[0]["payload"]["timestamp"] = _iso(start - 3600)
        self.write("sessions/2026/11/27/rollout-old.jsonl", meta, NEW)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", start), live.EMPTY)
        meta[0]["payload"]["timestamp"] = _iso(start + 1)
        self.write("sessions/2026/11/27/rollout-new.jsonl", meta, NEW - 10)
        self.assertEqual(live.live_usage("codex", self.home, None, "/w", start)["ctx"], 1000)

    def test_the_first_timestamp_in_the_head_says_when_a_session_began(self):
        # the real claude-code shape: the first line has no timestamp, a later one has
        start = NEW - 50
        lead = [{"type": "summary", "summary": "x"}] + _claude_lines(start - 3600, 1000)
        seat = [{"type": "summary", "summary": "x"}] + _claude_lines(start + 10, 2000)
        self.write(".claude/projects/-w/lead.jsonl", lead, NEW)
        self.write(".claude/projects/-w/seat.jsonl", seat, NEW - 20)
        self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start)["ctx"], 2000)
        os.unlink(os.path.join(self.home, ".claude/projects/-w/seat.jsonl"))
        with mock.patch.object(live, "_birth", lambda st: NEW):  # a birth time after start does not win
            self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start), live.EMPTY)

    def test_no_timestamp_and_no_birth_time_is_not_new(self):
        start = NEW - 50
        lines = [{"type": "summary"}, {"type": "assistant", "message": {"usage": {
            "input_tokens": 7, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "output_tokens": 1}}}]
        self.write(".claude/projects/-w/s.jsonl", lines, NEW)
        with mock.patch.object(live, "_birth", lambda st: None):  # Linux: no st_birthtime
            self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start), live.EMPTY)
            # a session id still names its file
            self.assertEqual(live.live_usage("claude-code", self.home, "s", "/w", start)["ctx"], 7)
        with mock.patch.object(live, "_birth", lambda st: NEW):
            self.assertEqual(live.live_usage("claude-code", self.home, None, "/w", start)["ctx"], 7)

    def test_old_files_are_never_opened(self):
        start = time.time() - 60
        day = time.strftime("%Y/%m/%d", time.gmtime(start))
        new = self.write(f"sessions/{day}/rollout-new.jsonl", _codex_lines("/w", 2000), start + 30)
        old_here = self.write(f"sessions/{day}/rollout-stale.jsonl", _codex_lines("/w", 1), start - 3600)
        old_day = self.write("sessions/2020/01/01/rollout-2020-01-01-oldid.jsonl", _codex_lines("/w", 3), start + 40)
        opened, real = [], live._read
        with mock.patch.object(live, "_read", lambda p, tail: opened.append(p) or real(p, tail)):
            self.assertEqual(live.live_usage("codex", self.home, None, "/w", start)["ctx"], 2000)
            self.assertNotIn(old_here, opened)
            self.assertNotIn(old_day, opened)  # its date folder is older than start: not even listed
            self.assertEqual(set(opened), {new})
            # a session id still finds its file in an old folder, by name
            self.assertEqual(live.live_usage("codex", self.home, "oldid", "/w", start)["ctx"], 3)

    def test_a_short_session_id_does_not_match_inside_a_name(self):
        self.write("sessions/2026/11/27/rollout-2026-11-27T10-00-00-abc.jsonl", _codex_lines("/w", 1000), OLD)
        self.assertEqual(live.live_usage("codex", self.home, "2026", "/w", float(NEW)), live.EMPTY)
        self.assertEqual(live.live_usage("codex", self.home, "abc", "/w", float(NEW))["ctx"], 1000)

    def test_timestamps(self):
        self.assertEqual(live._time("2026-11-27T10:00:00Z"), 1795773600.0)
        self.assertEqual(live._time("2026-11-27T11:00:00.5+01:00"), 1795773600.5)
        self.assertEqual(live._time(1795773600000), 1795773600.0)
        self.assertIsNone(live._time("yesterday"))
        self.assertIsNone(live._time(True))

    # -- paths ---------------------------------------------------------------------------------
    def test_session_root_with_a_seat_home(self):
        h = "/h"
        self.assertEqual(live.session_root("codex", h, "/w"), "/h/sessions")
        self.assertEqual(live.session_root("claude-code", h, "/w/a.b_c"), "/h/.claude/projects/-w-a-b-c")
        self.assertEqual(live.session_root("pi", h, "/Users/x/repo"), "/h/sessions/--Users-x-repo--")
        self.assertEqual(live.session_root("kimi", h, "/w"), "/h/sessions")
        self.assertIsNone(live.session_root("agy", h, "/w"))

    def test_session_root_without_a_seat_home(self):
        fake = os.path.join(self.tmp.name, "realhome")
        env = {"HOME": fake}
        with mock.patch.dict(os.environ, env):
            for k in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "KIMI_CODE_HOME"):
                os.environ.pop(k, None)
            self.assertEqual(live.session_root("codex", None, "/w"), os.path.join(fake, ".codex", "sessions"))
            self.assertEqual(live.session_root("claude-code", None, "/w"),
                             os.path.join(fake, ".claude", "projects", "-w"))
            self.assertEqual(live.session_root("pi", None, "/w"), os.path.join(fake, ".pi", "agent", "sessions", "--w--"))
            self.assertEqual(live.session_root("kimi", None, "/w"), os.path.join(fake, ".kimi-code", "sessions"))
        with mock.patch.dict(os.environ, {"HOME": fake, "CODEX_HOME": "/c", "CLAUDE_CONFIG_DIR": "/cc",
                                          "KIMI_CODE_HOME": "/k"}):
            self.assertEqual(live.session_root("codex", None, "/w"), "/c/sessions")
            self.assertEqual(live.session_root("claude-code", None, "/w"), "/cc/projects/-w")
            self.assertEqual(live.session_root("kimi", None, "/w"), "/k/sessions")

    def test_local_seat_reads_the_real_home_layout(self):
        fake = os.path.join(self.tmp.name, "realhome")
        Path(fake, ".claude/projects/-w").mkdir(parents=True)
        shutil.copyfile(FIX / "claude-code/session.jsonl", Path(fake, ".claude/projects/-w/s.jsonl"))
        with mock.patch.dict(os.environ, {"HOME": fake}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(live.live_usage("claude-code", None, None, "/w", 0.0)["ctx"], 113000)

    def test_seat_home_path_makes_nothing(self):
        p = live.seat_home_path(self.state, "astra")
        self.assertEqual(p, os.path.join(self.state, "seats", "homes", "astra"))
        self.assertFalse(os.path.exists(self.state))


if __name__ == "__main__":
    unittest.main()
