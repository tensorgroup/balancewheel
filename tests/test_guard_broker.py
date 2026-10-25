import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from lead.guard import broker as brk
from lead.guard import record
from lead.guard.rules import RuleContext

TOKEN = "t0k3n"
SESSION = {"session_id": "s1", "project": "wt", "profile": "dev", "lead": "pi-lead",
           "runtime": "pi", "runtime_version": "1.2.3"}


def bash(cmd):
    return {"toolName": "bash", "input": {"command": cmd}}


class BrokerBase(unittest.TestCase):
    mode = "acting"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.wt)
        self.state = os.path.join(self.tmp, "state")
        self.gdir = os.path.join(self.tmp, "g")
        self.base_threads = threading.active_count()
        self.b = self.start()

    def ctx(self):
        return RuleContext(worktree=self.wt, git_dir=os.path.join(self.wt, ".git"),
                           common_dir=os.path.join(self.wt, ".git"), home=self.tmp,
                           protected_branches=["main", "master"], deploy_commands=["make deploy"],
                           push_dest=lambda r, s: None, is_pristine=lambda p: False)

    def start(self, mode=None, session=None):
        b = brk.Broker(self.state, self.gdir, TOKEN, mode or self.mode, session or dict(SESSION), self.ctx())
        b.bind()
        b.serve()
        self.addCleanup(self.stop, b)
        return b

    def stop(self, b):
        b.stop()

    def tearDown(self):
        self.b.stop()
        self.assertEqual(threading.active_count(), self.base_threads)

    def raw(self, data, read=True, timeout=3):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        try:
            s.connect(os.path.join(self.gdir, "sock"))
            try:
                s.sendall(data)
            except OSError:
                pass
            if not read:
                return None
            buf = b""
            try:
                while not buf.endswith(b"\n"):
                    c = s.recv(65536)
                    if not c:
                        break
                    buf += c
            except OSError:
                pass
            return json.loads(buf) if buf.strip() else None
        finally:
            s.close()

    def ask(self, payload, runtime="pi", token=TOKEN, v=1):
        req = {"v": v, "token": token, "runtime": runtime, "cwd": self.wt, "payload": payload}
        return self.raw(json.dumps(req).encode() + b"\n")

    def decisions(self):
        return record.load(self.state)["decisions"]


class TestBroker(BrokerBase):
    def test_block_in_acting_logged(self):
        r = self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.assertEqual(r["v"], 1)
        self.assertEqual(r["decision"], "block")
        self.assertTrue(r["reason"])
        d = self.decisions()
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["decision"], "block")
        self.assertEqual(d[0]["outcome"], "blocked")
        self.assertEqual(d[0]["id"], r["id"])
        self.assertEqual(d[0]["mode"], "acting")
        for k in ("type", "ts", "session_id", "project", "profile", "lead", "runtime", "runtime_version",
                  "tool", "kind", "call", "digest", "source", "rule", "reason", "latency_ms",
                  "rules_version", "guard_version", "policy_digest"):
            self.assertIn(k, d[0])
        self.assertIsNone(d[0]["judge_model"])
        self.assertIsNone(d[0]["prompt_hash"])
        self.assertEqual(len(d[0]["id"]), 12)
        self.assertEqual(len(d[0]["policy_digest"]), 12)
        self.assertFalse(d[0]["self_test"])

    def test_allow_outcome_in_acting(self):
        r = self.ask(bash("ls"))
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(self.decisions()[0]["outcome"], "allowed")

    def test_ask_outcome_in_acting(self):
        r = self.ask({"toolName": "mystery", "input": {}})
        self.assertEqual(r["decision"], "ask")
        self.assertEqual(self.decisions()[0]["outcome"], "asked")

    def test_bad_requests_get_errors_and_no_record(self):
        for r in (self.ask(bash("ls"), token="nope"),
                  self.raw(b"{not json\n"),
                  self.ask(bash("ls"), v=2),
                  self.ask(bash("ls"), runtime="agy")):
            self.assertIn("error", r)
            self.assertEqual(r["v"], 1)
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.b.counts()["calls"], 0)

    def test_300kib_payload_is_oversized_other(self):
        r = self.ask({"toolName": "bash", "input": {"command": "x" * (300 * 1024)}})
        self.assertIn("decision", r)
        d = self.decisions()[0]
        self.assertEqual((d["kind"], d["tool"]), ("other", "oversized"))

    def test_2mib_request_refused(self):
        big = b'{"v":1,"token":"%s","payload":"' % TOKEN.encode() + b"x" * (2 * 1024 * 1024) + b'"}\n'
        r = self.raw(big)
        if r is not None:
            self.assertIn("error", r)
        self.assertEqual(self.decisions(), [])

    def test_long_socket_path_raises_before_binding(self):
        long_dir = os.path.join(self.tmp, "d" * 100)
        b = brk.Broker(self.state, long_dir, TOKEN, "acting", dict(SESSION), self.ctx())
        with self.assertRaises(ValueError) as cm:
            b.bind()
        self.assertIn("sock", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(long_dir, "sock")))

    def test_stale_socket_replaced(self):
        self.b.stop()
        os.makedirs(self.gdir, exist_ok=True)
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(os.path.join(self.gdir, "sock"))
        stale.close()
        self.b = self.start()
        self.assertEqual(self.ask(bash("ls"))["decision"], "allow")

    def test_modes_0600(self):
        self.ask(bash("ls"))
        for p in (os.path.join(self.gdir, "sock"), record.path(self.state)):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)

    def test_stalled_clients_do_not_starve(self):
        socks = []
        for _ in range(2):
            s = socket.socket(socket.AF_UNIX)
            s.connect(os.path.join(self.gdir, "sock"))
            socks.append(s)
        try:
            t = time.time()
            r = self.ask(bash("ls"))
            self.assertLess(time.time() - t, 1.0)
            self.assertEqual(r["decision"], "allow")
        finally:
            for s in socks:
                s.close()

    def test_twenty_concurrent(self):
        out = []
        lock = threading.Lock()

        def go(i):
            r = self.ask(bash("echo %d" % i))
            with lock:
                out.append(r)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(out), 20)
        self.assertTrue(all(r and r.get("decision") == "allow" for r in out))
        loaded = record.load(self.state)
        self.assertEqual(len(loaded["decisions"]), 20)
        self.assertEqual(loaded["warnings"], [])

    def test_stop_during_inflight_request(self):
        real = brk.decide
        started = threading.Event()

        def slow(call, ctx):
            started.set()
            time.sleep(0.4)
            return real(call, ctx)
        out = []
        with mock.patch.object(brk, "decide", slow):
            t = threading.Thread(target=lambda: out.append(self.ask(bash("ls"))))
            t.start()
            self.assertTrue(started.wait(2))
            self.b.stop()
            t.join()
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(self.b.counts()["calls"], 1)
        self.assertEqual(out[0]["decision"], "allow")
        self.assertFalse(os.path.exists(os.path.join(self.gdir, "sock")))

    def test_redaction_and_digest(self):
        self.ask(bash("echo TOKEN=abc123"))
        d = self.decisions()[0]
        self.assertNotIn("abc123", json.dumps(d))
        from lead.guard.normalise import normalise
        from lead.guard.redact import digest
        self.assertEqual(d["digest"], digest(normalise("pi", bash("echo TOKEN=abc123"), self.wt, self.wt)))
        self.assertNotEqual(d["digest"], digest(normalise("pi", bash(d["call"]), self.wt, self.wt)))

    def test_counts(self):
        self.ask(bash("ls"))
        self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.ask({"toolName": "mystery", "input": {}})
        self.b.stop()
        c = self.b.counts()
        self.assertEqual(c["calls"], 3)
        self.assertEqual(c["decisions"], {"allow": 1, "block": 1, "ask": 1})
        self.assertEqual(c["gray"], 1)
        self.assertLessEqual(c["latency_ms"]["p50"], c["latency_ms"]["p95"])

    def test_self_test_marker(self):
        self.b.arm_self_test("MARKER-9f2")
        self.ask(bash("echo MARKER-9f2"))
        self.ask({"toolName": "write", "input": {"path": os.path.join(self.wt, "MARKER-9f2.txt")}})
        self.ask({"toolName": "mystery", "input": {"x": "MARKER-9f2"}})
        self.ask(bash("ls"))
        self.b.stop()
        d = self.decisions()
        self.assertEqual([x["self_test"] for x in d], [True, True, True, False])
        self.assertEqual(self.b.counts()["calls"], 1)
        self.assertEqual(self.b.counts()["decisions"], {"allow": 1})


class TestShadow(BrokerBase):
    mode = "shadow"

    def test_shadow_allows_but_logs_real_decision(self):
        r = self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.assertEqual(r["decision"], "allow")
        d = self.decisions()[0]
        self.assertEqual(d["decision"], "block")
        self.assertEqual(d["outcome"], "shadow-allowed")
        self.assertEqual(d["mode"], "shadow")


class TestRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_load_skips_truncated_folds_marks_orphans(self):
        record.append(self.tmp, {"type": "decision", "id": "aaaaaaaaaaaa", "decision": "allow"})
        record.append(self.tmp, {"type": "mark", "decision_id": "aaaaaaaaaaaa", "verdict": "wrong", "sampled": True})
        record.append(self.tmp, {"type": "mark", "decision_id": "zzzzzzzzzzzz", "verdict": "right", "sampled": False})
        with open(record.path(self.tmp), "ab") as f:
            f.write(b'{"type": "decision", "id": "bbb')
        r = record.load(self.tmp)
        self.assertEqual(len(r["decisions"]), 1)
        self.assertEqual(r["decisions"][0]["mark"], {"verdict": "wrong", "sampled": True})
        self.assertEqual(len(r["marks"]), 2)
        self.assertEqual([m["decision_id"] for m in r["orphan_marks"]], ["zzzzzzzzzzzz"])
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("truncated", r["warnings"][0])

    def test_load_missing_file(self):
        r = record.load(os.path.join(self.tmp, "none"))
        self.assertEqual((r["decisions"], r["marks"], r["orphan_marks"], r["warnings"]), ([], [], [], []))

    def test_evidence_key(self):
        base = {"profile": "p", "runtime": "pi", "runtime_version": "1", "rules_version": "r", "policy_digest": "d"}
        k = record.evidence_key(base)
        self.assertEqual(k, ("p", "pi", "1", "r", "d"))
        self.assertNotEqual(k, record.evidence_key(dict(base, runtime_version="2")))
        self.assertNotEqual(k, record.evidence_key(dict(base, policy_digest="e")))

    def test_policy_digest(self):
        a = record.policy_digest(["main"], ["make deploy"])
        self.assertEqual(len(a), 12)
        self.assertEqual(a, record.policy_digest(["main"], ["make deploy"]))
        self.assertNotEqual(a, record.policy_digest(["main", "dev"], ["make deploy"]))
        self.assertNotEqual(a, record.policy_digest(["main"], []))


if __name__ == "__main__":
    unittest.main()
