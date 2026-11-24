import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from lead import handoff, inbox  # noqa: E402
from panel import channel, state  # noqa: E402


def enc(obj) -> bytes:
    return json.dumps(obj).encode()


class ChannelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.tmp.name))
        self.S = str(self.root / "state")
        self.assertIsNone(channel.ensure_request_dir(self.S, "k"))
        self.rd = Path(state.request_dir(self.S, "k"))

    def tearDown(self):
        self.tmp.cleanup()

    def publish(self, name, body):
        data = body if isinstance(body, bytes) else enc(body)
        tmp = self.rd / f".tmp-{name}"
        tmp.write_bytes(data)
        os.rename(tmp, self.rd / f"{name}.request")

    def read(self, rid):
        return json.loads(Path(state.view_root(self.S, "k"), "responses", f"{rid}.json").read_text())


class Ingest(ChannelCase):
    def test_valid_panel_request(self):
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        (req,), problems = channel.ingest(self.S, "k")
        self.assertEqual((req[0], req[1]["stakes"], req[1]["paths"], problems), ("r1", "normal", [], []))
        self.assertNotIn("deep", req[1])
        self.assertFalse(any(self.rd.iterdir()))

    def test_tmp_ignored(self):
        (self.rd / ".tmp-x").write_bytes(b"{}")
        (self.rd / ".tmp-y.request").write_bytes(b"{}")
        self.assertEqual(channel.ingest(self.S, "k"), ([], []))
        self.assertTrue((self.rd / ".tmp-x").exists())
        self.assertTrue((self.rd / ".tmp-y.request").exists())

    def test_bad_json_and_size(self):
        self.publish("bad", b"{not json")
        self.publish("big", b" " * channel.READ_LIMIT)
        self.publish("utf", b"\xff\xfe")
        got = dict(channel.ingest(self.S, "k")[0])
        self.assertIsInstance(got["bad"], ValueError)
        self.assertEqual(str(got["big"]), "request is over 64 KiB")
        self.assertIsInstance(got["utf"], ValueError)
        self.assertFalse(any(self.rd.iterdir()))

    def test_symlink_and_fifo_refused(self):
        os.symlink("/etc/hosts", self.rd / "s.request")
        os.mkfifo(self.rd / "f.request")
        got = dict(channel.ingest(self.S, "k")[0])
        self.assertEqual({k: str(v) for k, v in got.items()}, {"s": "not a regular file", "f": "not a regular file"})
        self.assertEqual([v.nonce for v in got.values()], [None, None])
        self.assertFalse(any(self.rd.iterdir()))
        self.assertTrue(os.path.exists("/etc/hosts"))

    def test_dir_named_like_request_left(self):
        (self.rd / "d.request").mkdir()
        reqs, problems = channel.ingest(self.S, "k")
        self.assertEqual(reqs, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("d.request", problems[0])
        self.assertTrue((self.rd / "d.request").is_dir())

    def test_many_dirs_make_bounded_problems(self):
        for i in range(200):
            (self.rd / f"d{i}.request").mkdir()
        _, problems = channel.ingest(self.S, "k")
        self.assertEqual(len(problems), inbox.NAMES_SHOWN + 1)
        self.assertEqual(problems[-1], f"and {200 - inbox.NAMES_SHOWN} more")

    def test_long_name_cut(self):
        self.publish("x" * 70 + ".y" + "z" * 100, b"{}")
        _, (problem,) = channel.ingest(self.S, "k")
        self.assertNotIn("z" * 100, problem)

    def test_hard_link_refused(self):
        outside = self.root / "outside.json"
        outside.write_bytes(enc({"v": 1, "type": "panel", "checkpoint": "design", "target": "t"}))
        os.link(outside, self.rd / "h.request")
        ((rid, err),), _ = channel.ingest(self.S, "k")
        self.assertEqual((rid, str(err)), ("h", channel.HARD_LINK))
        self.assertFalse(any(self.rd.iterdir()))
        self.assertTrue(outside.exists())

    def test_bad_stem_dropped(self):
        self.publish("a.b", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        reqs, problems = channel.ingest(self.S, "k")
        self.assertEqual(reqs, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("a.b.request", problems[0])
        self.assertFalse(any(self.rd.iterdir()))
        self.assertFalse(Path(state.view_root(self.S, "k"), "responses").exists())

    def test_problem_names_escaped(self):
        self.publish("\x1b[2Jx.y", b"{}")
        _, problems = channel.ingest(self.S, "k")
        self.assertEqual(len(problems), 1)
        self.assertNotIn("\x1b", problems[0])
        self.assertIn("\\x1b", problems[0])

    def test_reused_id_refused_under_new_name(self):
        channel.respond(self.S, "k", "r1", {"ok": True, "panel": "p1111111"}, nonce="old")
        self.publish("r1", {"v": 1, "type": "panel", "checkpoint": "design", "target": "t", "nonce": "new"})
        ((rid, err),), _ = channel.ingest(self.S, "k")
        self.assertTrue(getattr(err, "reused", False))
        self.assertIn("used before", str(err))
        self.assertEqual(err.nonce, "new")
        path = channel.respond(self.S, "k", rid, {"ok": False, "error": str(err)}, nonce="new", reused=True)
        self.assertTrue(path.endswith("r1.reused-1.json"))
        self.assertEqual(self.read("r1")["nonce"], "old")
        path2 = channel.respond(self.S, "k", rid, {"ok": False}, reused=True)
        self.assertTrue(path2.endswith("r1.reused-2.json"))
        self.assertFalse(any(self.rd.iterdir()))

    def test_a_used_id_is_refused_before_any_other_check(self):  # panel g12
        channel.respond(self.S, "k", "r1", {"ok": True, "panel": "p1111111"}, nonce="old")
        channel.respond(self.S, "k", "r2", {"ok": True, "panel": "p2222222"}, nonce="old")
        outside = self.root / "outside.json"
        outside.write_bytes(b"{}")
        os.link(outside, self.rd / "r1.request")  # a hard link: a malformed request
        os.symlink(outside, self.rd / "r2.request")  # not a regular file
        self.publish("r3", b"[" * 5000)  # a fresh id: its own error
        got = dict(channel.ingest(self.S, "k")[0])
        self.assertTrue(getattr(got["r1"], "reused", False))
        self.assertTrue(getattr(got["r2"], "reused", False))
        self.assertFalse(getattr(got["r3"], "reused", False))
        self.assertEqual(str(got["r3"]), "request is not UTF-8 JSON")
        self.assertEqual(self.read("r1")["nonce"], "old")  # the old responses stay
        self.assertEqual(self.read("r2")["nonce"], "old")

    def test_bad_request_keeps_its_nonce(self):
        self.publish("r2", {"v": 1, "type": "panel", "nonce": "n1"})
        ((rid, err),), _ = channel.ingest(self.S, "k")
        self.assertIsInstance(err, ValueError)
        self.assertEqual(err.nonce, "n1")

    def test_request_dir_is_a_symlink(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "x.request").write_bytes(enc({"v": 1}))
        self.rd.rmdir()
        os.symlink(elsewhere, self.rd)
        reqs, problems = channel.ingest(self.S, "k")
        self.assertEqual(reqs, [])
        self.assertEqual(len(problems), 1)
        self.assertTrue((elsewhere / "x.request").exists())

    def test_checkpoint_dir_is_a_symlink(self):
        cp = Path(handoff.checkpoint_dir(self.S, "j"))
        target = self.root / "target"
        target.mkdir()
        cp.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, cp)
        self.assertIsNotNone(channel.ensure_request_dir(self.S, "j"))
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(len(channel.ingest(self.S, "j")[1]), 1)

    def test_ensure_request_dir(self):
        self.assertIsNone(channel.ensure_request_dir(self.S, "k"))
        self.assertEqual(stat.S_IMODE(os.stat(self.rd).st_mode), 0o700)
        self.rd.rmdir()
        gone = self.root / "gone"
        os.symlink(gone, self.rd)
        problem = channel.ensure_request_dir(self.S, "k")
        self.assertIsInstance(problem, str)
        self.assertNotIn("\n", problem)
        self.assertFalse(os.path.lexists(gone))

    def test_bad_key_is_a_problem(self):
        self.assertEqual(channel.ingest(self.S, "../x")[0], [])
        self.assertEqual(len(channel.ingest(self.S, "../x")[1]), 1)
        self.assertIsInstance(channel.ensure_request_dir(self.S, "../x"), str)


class Respond(ChannelCase):
    def test_response_echoes_nonce_and_time(self):
        p = channel.respond(self.S, "k", "r1", {"ok": True}, nonce="n1")
        got = self.read("r1")
        self.assertEqual(p, os.path.join(state.view_root(self.S, "k"), "responses", "r1.json"))
        self.assertEqual((got["ok"], got["nonce"]), (True, "n1"))
        self.assertIsInstance(got["received"], (int, float))
        channel.respond(self.S, "k", "r2", {"ok": True})
        self.assertIsNone(self.read("r2")["nonce"])

    def test_respond_atomic(self):
        channel.respond(self.S, "k", "r1", {"ok": True, "n": 1})
        channel.respond(self.S, "k", "r1", {"ok": True, "n": 2})
        d = Path(state.view_root(self.S, "k"), "responses")
        self.assertEqual(sorted(x.name for x in d.iterdir()), ["r1.json"])
        self.assertEqual(self.read("r1")["n"], 2)

    def test_respond_refuses_a_bad_id(self):
        for bad in ("../x", "a.b", "", "x" * 65, None):
            self.assertRaises(ValueError, channel.respond, self.S, "k", bad, {"ok": True})


class Parse(unittest.TestCase):
    def test_claim_cap(self):
        self.assertRaisesRegex(ValueError, "over 1500", channel.parse,
                               enc({"v": 1, "type": "ask", "panel": "p1234567", "seat": "s", "claim": "x" * 1501,
                                    "dispute": "d1"}))
        self.assertRaisesRegex(ValueError, "over 1500", channel.check_ask, "s", "x" * 1501, "d1")
        self.assertRaises(ValueError, channel.check_ask, "s", "c", "../../x")
        self.assertRaises(ValueError, channel.check_ask, "s", "", "d1")
        self.assertRaises(ValueError, channel.check_ask, 3, "c", "d1")
        self.assertIsNone(channel.check_ask("s", "x" * 1500, "d1"))

    def test_valid_ask(self):
        req = channel.parse(enc({"v": 1, "type": "ask", "panel": "p1234567", "seat": "s", "claim": "c",
                                 "dispute": "d1"}))
        self.assertEqual((req["type"], req["panel"]), ("ask", "p1234567"))

    def test_panel_field_must_be_an_id(self):
        for bad in ("../other/p1234567", "p123", "P1234567", "p1234567/..", 7, None):
            self.assertRaisesRegex(ValueError, "panel must be a panel id", channel.parse,
                                   enc({"v": 1, "type": "log", "panel": bad, "verdicts": {}}))

    def test_envelope(self):
        for bad in ([], "x", {"type": "panel"}, {"v": 2, "type": "panel"}, {"v": True, "type": "log"},
                    {"v": 1, "type": "other"}, {"v": 1, "type": "log", "panel": "p1234567", "verdicts": {},
                                                "nonce": "n" * 33},
                    {"v": 1, "type": "log", "panel": "p1234567", "verdicts": {}, "nonce": 5}):
            self.assertRaises(ValueError, channel.parse, enc(bad))

    def test_panel_fields(self):
        ok = {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"}
        req = channel.parse(enc({**ok, "focus": "f", "stakes": "high", "paths": ["a/b.py"]}))
        self.assertEqual((req["focus"], req["stakes"], req["paths"]), ("f", "high", ["a/b.py"]))
        for bad in ({"target": ""}, {"target": "x" * 301}, {"checkpoint": 3}, {"focus": "x" * 501},
                    {"stakes": "low"}, {"paths": "a"}, {"paths": ["a"] * 21}, {"paths": ["/abs"]},
                    {"paths": ["../up"]}, {"paths": ["a/../../b"]}, {"paths": [3]}, {"paths": [""]},
                    {"deep": "yes"}):
            self.assertRaises(ValueError, channel.parse, enc({**ok, **bad}))

    def test_a_lead_cannot_set_the_deep_tier(self):  # panel g04: only `bw panel --deep` sets it
        ok = {"v": 1, "type": "panel", "checkpoint": "design", "target": "t"}
        for deep in (True, False, "yes"):
            with self.assertRaises(ValueError) as cm:
                channel.parse(enc({**ok, "deep": deep}))
            self.assertEqual(str(cm.exception), channel.NO_DEEP)

    def test_nul_in_a_claim_is_refused(self):  # panel g26
        with self.assertRaises(ValueError) as cm:
            channel.check_ask("astra", "a\x00b", "d1")
        self.assertIn("NUL", str(cm.exception))

    def test_deep_nesting_is_a_refusal(self):  # panel g13
        with self.assertRaises(ValueError) as cm:
            channel.parse(b"[" * 100000)
        self.assertEqual(str(cm.exception), "request is not UTF-8 JSON")
        self.assertIsNone(channel._nonce(b"[" * 100000))

    def test_panel_request_drops_unknown_keys(self):
        req = channel.parse(enc({"v": 1, "type": "panel", "checkpoint": "design", "target": "t", "nonce": "n",
                                 "transcript": "x", "roster": ["a"]}))
        self.assertEqual(set(req), {"v", "type", "nonce", "checkpoint", "target", "focus", "stakes", "paths"})

    def test_seat_and_checkpoint_capped(self):
        self.assertRaises(ValueError, channel.check_ask, "s" * 65, "c", "d1")
        self.assertRaises(ValueError, channel.check_ask, "", "c", "d1")
        self.assertIsNone(channel.check_ask("s" * 64, "c", "d1"))
        ok = {"v": 1, "type": "panel", "target": "t"}
        for bad in ("c" * 65, ""):
            with self.assertRaises(ValueError) as cm:
                channel.parse(enc({**ok, "checkpoint": bad}))
            self.assertNotIn("c" * 65, str(cm.exception))
        self.assertEqual(channel.parse(enc({**ok, "checkpoint": "c" * 64}))["checkpoint"], "c" * 64)

    def test_duplicate_keys_refused(self):
        body = b'{"v": 1, "type": "log", "panel": "p1234567", "verdicts": {}, "panel": "p7654321"}'
        self.assertRaisesRegex(ValueError, "duplicate key", channel.parse, body)
        nested = b'{"v": 1, "type": "log", "panel": "p1234567", "verdicts": {"f1": "a", "f1": "b"}}'
        self.assertRaisesRegex(ValueError, "duplicate key", channel.parse, nested)

    def test_fenced_request_accepted(self):
        body = json.dumps({"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        for text in (f"```json\n{body}\n```", f"\n  ```\n{body}\n```\n\n"):
            self.assertEqual(channel.parse(text.encode())["target"], "t")

    def test_bad_fences_refused(self):
        body = json.dumps({"v": 1, "type": "panel", "checkpoint": "design", "target": "t"})
        for text in (f"```json\n```json\n{body}\n```\n```", f"```json\n{body}\n```\ntrailing",
                     f"Here it is:\n```json\n{body}\n```", f"```python\n{body}\n```", f"```json\n{body}",
                     "```json\n```"):
            self.assertRaises(ValueError, channel.parse, text.encode())

    def test_log_and_amend(self):
        base = {"v": 1, "panel": "p1234567"}
        self.assertEqual(channel.parse(enc({**base, "type": "log", "verdicts": {}, "x": 1}))["x"], 1)
        self.assertRaises(ValueError, channel.parse, enc({**base, "type": "log", "verdicts": []}))
        self.assertRaises(ValueError, channel.parse, enc({**base, "type": "log"}))
        self.assertEqual(channel.parse(enc({**base, "type": "amend", "patch": {}}))["patch"], {})
        self.assertRaises(ValueError, channel.parse, enc({**base, "type": "amend", "patch": None}))

    def test_constants(self):
        self.assertEqual((channel.READ_LIMIT, channel.CLAIM_LIMIT, channel.TARGET_LIMIT, channel.FOCUS_LIMIT,
                          channel.PATHS_LIMIT, channel.NONCE_LIMIT), (65536, 1500, 300, 500, 20, 32))
        self.assertEqual(channel.TYPES, ("panel", "ask", "log", "amend"))
        self.assertEqual(channel.ENVELOPE, ("v", "type", "panel", "nonce"))

    def test_notice_kind(self):
        self.assertIn("panel", inbox.NOTICE_KINDS)


if __name__ == "__main__":
    unittest.main()
