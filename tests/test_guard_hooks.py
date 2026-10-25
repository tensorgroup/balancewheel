"""The thin guard clients (agy hook.py, pi pi_guard.ts): inert, fail-closed, markers, and the
sandbox attack that shows a contained lead can neither skip nor replace the guard. No model calls."""
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from lead import sandbox
from lead.guard import broker as brk
from lead.guard import record
from lead.guard.rules import RuleContext
from lead.projects import resolve_project
from lead.runtimes import ADAPTERS

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
GUARD = os.path.join(ROOT, "lead", "guard")
HOOK = os.path.join(GUARD, "hook.py")
PI_TS = os.path.join(GUARD, "pi_guard.ts")
HARNESS = os.path.join(GUARD, "pi_harness.mjs")
TOKEN = "t0k3n"
HERDR = "herdr pane send x"  # a canvas-control call: the rules block it


def _node_ok():
    node = shutil.which("node")
    if not node:
        return False
    try:
        v = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        major, minor = (int(x) for x in v.lstrip("v").split(".")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return (major, minor) >= (22, 6)


NODE_OK = _node_ok()
NODE_ARGV = ["node", "--no-warnings", "--experimental-strip-types"]


class FakeBroker:
    """A socket that answers with a canned reply, or never (reply=None), and keeps what it was sent."""

    def __init__(self, path, reply):
        self.path, self.reply, self.requests = path, reply, []
        self._stop = threading.Event()
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(path)
        self.s.listen(8)
        self.s.settimeout(0.1)
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()

    def _loop(self):
        conns = []
        while not self._stop.is_set():
            try:
                c, _ = self.s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conns.append(c)
            c.settimeout(1)
            buf = b""
            try:
                while b"\n" not in buf:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
            except OSError:
                pass
            if buf.strip():
                self.requests.append(json.loads(buf.split(b"\n")[0]))
            if self.reply is not None:
                try:
                    c.sendall(self.reply)
                except OSError:
                    pass
                c.close()
        for c in conns:
            c.close()

    def close(self):
        self._stop.set()
        self.t.join()
        self.s.close()


def reply(decision, reason="because"):
    return json.dumps({"v": 1, "id": "abc", "decision": decision, "reason": reason}).encode() + b"\n"


class Env(unittest.TestCase):
    runtime = None

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.wt)
        self.state = os.path.join(self.tmp, "state")
        self.gdir = os.path.join(self.tmp, "g")
        os.makedirs(self.gdir)
        self.sock = os.path.join(self.gdir, "sock")
        self.markers = os.path.join(self.tmp, "markers.jsonl")
        self.threads = threading.active_count()
        self.addCleanup(self._no_threads)

    def _no_threads(self):
        self.assertEqual(threading.active_count(), self.threads)

    def env(self, mode="acting", socket_path=True, token=True, markers=True, **extra):
        e = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
        e["BW_SESSION"] = "sess"
        if mode:
            e["BW_GUARD_MODE"] = mode
        if socket_path:
            e["BW_GUARD_SOCKET"] = self.sock
        if token:
            e["BW_GUARD_TOKEN"] = TOKEN
        if markers:
            e["BW_GUARD_MARKERS"] = self.markers
        e.update(extra)
        return e

    def fake(self, canned):
        fb = FakeBroker(self.sock, canned)
        self.addCleanup(fb.close)
        return fb

    def ctx(self):
        return RuleContext(worktree=self.wt, git_dir=os.path.join(self.wt, ".git"),
                           common_dir=os.path.join(self.wt, ".git"), home=self.tmp,
                           protected_branches=["main", "master"], deploy_commands=[],
                           push_dest=lambda r, s: None, is_pristine=lambda p: False)

    def real(self, mode, runtime=None, **kw):
        session = {"session_id": "s1", "project": "wt", "profile": "dev", "lead": "l",
                   "runtime": runtime or self.runtime, "runtime_version": "1"}
        b = brk.Broker(self.state, self.gdir, TOKEN, mode, session, self.ctx(), **kw)
        b.bind()
        b.serve()
        self.addCleanup(b.stop)
        return b

    def marker_lines(self):
        if not os.path.exists(self.markers):
            return []
        with open(self.markers) as f:
            return [json.loads(x) for x in f if x.strip()]


class ClientTests:
    """Behaviour both clients share. Subclasses define call() -> (kind, reason) where kind is one of
    inert | allow | ask | block, and blocking_cmd for the real broker."""

    def call(self, env, cmd="ls", timeout=15):
        raise NotImplementedError

    def test_inert_without_mode_even_with_session(self):
        fb = self.fake(reply("block"))
        kind, _ = self.call(self.env(mode=None))
        self.assertIn(kind, ("inert", "allow"))
        self.assertEqual(fb.requests, [])
        self.assertEqual(self.marker_lines(), [])

    def test_mode_without_socket_is_failure_acting(self):
        kind, why = self.call(self.env("acting", socket_path=False))
        self.assertEqual(kind, "block")
        self.assertTrue(why.startswith("guard unavailable: "), why)
        self.assertEqual(self.marker_lines(), [])

    def test_mode_without_socket_is_failure_shadow(self):
        kind, _ = self.call(self.env("shadow", socket_path=False))
        self.assertEqual(kind, "allow")
        m = self.marker_lines()
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0]["runtime"], m[0]["mode"]), (self.runtime, "shadow"))
        self.assertTrue(m[0]["why"] and m[0]["ts"])

    def test_mode_without_token_is_failure(self):
        self.fake(reply("allow"))
        kind, why = self.call(self.env("acting", token=False))
        self.assertEqual(kind, "block")
        self.assertIn("guard unavailable", why)

    def test_acting_block_reply_blocks_with_reason(self):
        fb = self.fake(reply("block", "no canvas"))
        kind, why = self.call(self.env("acting"), cmd="ls")
        self.assertEqual((kind, why), ("block", "no canvas"))
        req = fb.requests[0]
        self.assertEqual((req["v"], req["token"], req["runtime"]), (1, TOKEN, self.runtime))
        self.assertTrue(os.path.isabs(req["cwd"]))
        self.assertIsInstance(req["payload"], dict)

    def test_acting_allow_reply_allows(self):
        self.fake(reply("allow"))
        self.assertEqual(self.call(self.env("acting"))[0], "allow")

    def test_acting_broker_absent_blocks_without_marker(self):
        kind, why = self.call(self.env("acting"))
        self.assertEqual(kind, "block")
        self.assertTrue(why.startswith("guard unavailable: "), why)
        self.assertEqual(self.marker_lines(), [])

    def test_shadow_broker_absent_allows_with_one_marker(self):
        kind, _ = self.call(self.env("shadow"))
        self.assertEqual(kind, "allow")
        m = self.marker_lines()
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0]["mode"], "shadow")
        self.assertEqual(os.stat(self.markers).st_mode & 0o777, 0o600)

    def test_shadow_with_real_broker_allows_and_logs_the_decision(self):
        self.real("shadow")
        kind, _ = self.call(self.env("shadow"), cmd=self.blocking_cmd)
        self.assertEqual(kind, "allow")
        d = record.load(self.state)["decisions"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["decision"], self.logged_decision)
        self.assertEqual(self.marker_lines(), [])

    def test_acting_with_real_broker_enforces(self):
        self.real("acting")
        kind, why = self.call(self.env("acting"), cmd=self.blocking_cmd)
        self.assertEqual(kind, self.acting_kind)
        self.assertTrue(why)

    def test_acting_garbage_reply_blocks(self):
        self.real("acting", reply_garbage=True)
        kind, why = self.call(self.env("acting"))
        self.assertEqual(kind, "block")
        self.assertIn("guard unavailable", why)

    def test_shadow_garbage_reply_allows_with_marker(self):
        self.real("shadow", reply_garbage=True)
        self.assertEqual(self.call(self.env("shadow"))[0], "allow")
        self.assertEqual(len(self.marker_lines()), 1)

    def test_error_reply_blocks_in_acting(self):
        self.fake(json.dumps({"v": 1, "error": "bad token"}).encode() + b"\n")
        kind, why = self.call(self.env("acting"))
        self.assertEqual(kind, "block")
        self.assertIn("guard unavailable", why)

    def test_unknown_decision_blocks_in_acting(self):
        self.fake(reply("maybe"))
        self.assertEqual(self.call(self.env("acting"))[0], "block")

    def test_silent_broker_blocks_within_the_deadline(self):
        self.fake(None)
        kind, why = self.call(self.env("acting"))
        took = self.last_seconds
        self.assertEqual(kind, "block")
        self.assertIn("guard unavailable", why)
        self.assertLess(took, 2.5)

    def test_silent_broker_shadow_allows_with_marker(self):
        self.fake(None)
        self.assertEqual(self.call(self.env("shadow"))[0], "allow")
        self.assertEqual(len(self.marker_lines()), 1)

    def test_oversized_payload_is_sent_as_a_stub_and_logged(self):
        self.real("shadow")
        kind, _ = self.call(self.env("shadow"), cmd="echo " + "x" * (2 * 1024 * 1024))
        self.assertEqual(kind, "allow")
        d = record.load(self.state)["decisions"]
        self.assertEqual([x["tool"] for x in d], ["oversized"])
        self.assertEqual(self.marker_lines(), [])

    def test_marker_symlink_is_not_followed(self):
        target = os.path.join(self.tmp, "victim")
        with open(target, "w") as f:
            f.write("keep")
        os.symlink(target, self.markers)
        self.assertEqual(self.call(self.env("shadow"))[0], "allow")
        with open(target) as f:
            self.assertEqual(f.read(), "keep")

    def test_missing_markers_path_still_allows_in_shadow(self):
        self.assertEqual(self.call(self.env("shadow", markers=False))[0], "allow")


class AgyHookTests(ClientTests, Env):
    runtime = "agy"
    blocking_cmd = HERDR
    logged_decision = "ask"  # every agy call is gray until the agy spike
    acting_kind = "ask"

    def payload(self, cmd):
        return {"toolName": "run_command", "input": {"command": cmd}, "cwd": self.wt}

    def run_hook(self, env, stdin, timeout=15):
        return subprocess.run(["python3", HOOK], input=stdin, capture_output=True, env=env, timeout=timeout,
                              cwd=self.wt)

    def call(self, env, cmd="ls", timeout=15):
        t0 = time.monotonic()
        p = self.run_hook(env, json.dumps(self.payload(cmd)).encode(), timeout)
        self.last_seconds = time.monotonic() - t0
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout.decode().strip()
        if out == "{}":
            return "inert", ""
        a = json.loads(out)
        return {"allow": "allow", "ask": "ask", "deny": "block"}[a["decision"]], a.get("reason", "")

    def test_inert_prints_the_inert_answer_and_drains_stdin(self):
        from lead.guard import hook
        p = self.run_hook(self.env(mode=None), b"x" * (3 * 1024 * 1024))
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.decode().strip(), hook.INERT_ANSWER)
        self.assertEqual(p.stderr, b"")

    def test_unparsable_stdin_follows_the_failure_path(self):
        p = self.run_hook(self.env("acting"), b"not json")
        self.assertEqual(json.loads(p.stdout)["decision"], "deny")
        p = self.run_hook(self.env("shadow"), b"not json")
        self.assertEqual(json.loads(p.stdout)["decision"], "allow")
        self.assertEqual(len(self.marker_lines()), 1)

    def test_oversized_stdin_drains_and_exits_cleanly(self):
        p = self.run_hook(self.env("acting"), b'{"toolName":"t","x":"' + b"y" * (3 * 1024 * 1024) + b'"}')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["decision"], "deny")

    def test_ask_reply_is_ask(self):
        self.fake(reply("ask", "gray"))
        self.assertEqual(self.call(self.env("acting")), ("ask", "gray"))


@unittest.skipUnless(NODE_OK, "pi client tests need node >= 22.6 (erasable-syntax TypeScript)")
class PiClientTests(ClientTests, Env):
    runtime = "pi"
    blocking_cmd = HERDR
    logged_decision = "block"
    acting_kind = "block"

    def scenario(self, cmd, ui=None, has_ui=False, run=None):
        sc = {"event": {"type": "tool_call", "toolName": "bash", "toolCallId": "c1", "input": {"command": cmd}},
              "ctx": {"hasUI": has_ui, "cwd": self.wt}}
        if ui is not None:
            sc["ui"] = {"select": ui}
        if run:
            sc["run"] = run
        p = os.path.join(self.tmp, "scenario.json")
        with open(p, "w") as f:
            json.dump(sc, f)
        return p

    def run_ext(self, env, scenario, ext=PI_TS, timeout=15):
        p = subprocess.run(NODE_ARGV + [HARNESS, ext, scenario], capture_output=True, env=env,
                           timeout=timeout, cwd=self.wt)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def call(self, env, cmd="ls", timeout=15):
        out = self.run_ext(env, self.scenario(cmd), timeout=timeout)
        self.last_seconds = out["ms"] / 1000
        r = out["result"]
        if r is None:
            return "allow", ""
        self.assertIs(r["block"], True)
        return "block", r.get("reason", "")

    def test_ask_without_ui_blocks(self):
        self.fake(reply("ask", "gray"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", has_ui=False))
        self.assertEqual(out["result"], {"block": True, "reason": "gray"})
        self.assertIsNone(out["ui_prompt"])

    def test_ask_no_blocks_declined(self):
        self.fake(reply("ask", "gray"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", has_ui=True, ui="No"))
        self.assertEqual(out["result"], {"block": True, "reason": "declined by the user"})
        self.assertTrue(out["ui_prompt"].endswith("Allow?"))
        self.assertIn("gray", out["ui_prompt"])

    def test_ask_yes_allows(self):
        self.fake(reply("ask", "gray"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", has_ui=True, ui="Yes"))
        self.assertIsNone(out["result"])

    def test_ask_dismissed_blocks(self):
        self.fake(reply("ask", "gray"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", has_ui=True, ui=None))
        self.assertEqual(out["result"], {"block": True, "reason": "declined by the user"})

    def test_run_executes_when_allowed(self):
        mark = os.path.join(self.tmp, "ran")
        self.fake(reply("allow"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", run="touch " + mark))
        self.assertTrue(out["ran"])
        self.assertTrue(os.path.exists(mark))

    def test_run_skipped_when_blocked(self):
        mark = os.path.join(self.tmp, "ran")
        self.fake(reply("block", "no"))
        out = self.run_ext(self.env("acting"), self.scenario("ls", run="touch " + mark))
        self.assertFalse(out["ran"])
        self.assertFalse(os.path.exists(mark))


def sandbox_backends():
    out = ["sandbox-exec"] if shutil.which("sandbox-exec") else []
    if shutil.which("nono"):
        out.append("nono")
    return out


@unittest.skipUnless(NODE_OK, "pi sandbox tests need node >= 22.6 (erasable-syntax TypeScript)")
class SandboxedPiTests(Env):
    """The client runs inside the lead's sandbox: it can reach the socket without a write grant, a
    blocked canvas call never runs, and the socket and the client copy cannot be replaced."""
    runtime = "pi"

    def setUp(self):
        super().setUp()
        subprocess.run(["git", "init", "-q", self.wt], check=True)
        self.project = resolve_project(self.wt, {"projects": {}, "default_profile": "none"})
        self.home = os.path.expanduser("~")
        self.scratch = os.path.realpath(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        for src in (PI_TS, HARNESS):
            shutil.copy(src, self.gdir)
        self.ext, self.harness = os.path.join(self.gdir, "pi_guard.ts"), os.path.join(self.gdir, "pi_harness.mjs")
        os.makedirs(os.path.join(self.wt, "bin"))
        self.marker = os.path.join(self.wt, ".bw-block-marker")
        self.herdr = os.path.join(self.wt, "bin", "herdr")
        with open(self.herdr, "w") as f:
            f.write('#!/bin/sh\ntouch "%s"\n' % self.marker)
        os.chmod(self.herdr, 0o755)
        os.makedirs(self.state)
        self.cmd = "%s pane send x" % self.herdr

    def grants(self):
        rt = ADAPTERS["pi"].grants({"model": "m"})
        rt = rt._replace(read=list(rt.read) + [self.gdir], write=[], deny_write=())
        return sandbox.lead_grants(self.project, rt, self.state)

    def sandboxed(self, backend, argv, env):
        full, cleanup = sandbox.wrap(argv, self.grants(), backend, self.home, self.scratch, env)
        self.addCleanup(cleanup)
        e = {k: v for k, v in os.environ.items() if not k.startswith("BW_")}
        return subprocess.run(full, capture_output=True, env=e, cwd=self.wt, timeout=60)

    def harness_run(self, backend, ext, mode="acting"):
        sc = os.path.join(self.scratch, "sc.json")
        with open(sc, "w") as f:
            json.dump({"event": {"type": "tool_call", "toolName": "bash", "toolCallId": "c",
                                 "input": {"command": self.cmd}},
                       "ctx": {"hasUI": False, "cwd": self.wt}, "run": self.cmd}, f)
        env = {"BW_SESSION": "s", "BW_GUARD_MODE": mode, "BW_GUARD_SOCKET": self.sock,
               "BW_GUARD_TOKEN": TOKEN, "BW_GUARD_MARKERS": os.path.join(self.scratch, "m.jsonl")}
        p = self.sandboxed(backend, NODE_ARGV + [self.harness, ext, sc], env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def test_block_holds_inside_each_sandbox_backend(self):
        backends = sandbox_backends()
        if not backends:
            self.skipTest("no sandbox backend available")
        for backend in backends:
            with self.subTest(backend=backend):
                if os.path.exists(self.marker):
                    os.remove(self.marker)
                    shutil.rmtree(self.state)
                    os.makedirs(self.state)
                out = self.harness_run(backend, "--none")  # control: no extension, the call runs
                self.assertTrue(out["ran"])
                self.assertTrue(os.path.exists(self.marker), "control: the sandboxed call should run")
                os.remove(self.marker)
                b = self.real("acting")
                try:
                    out = self.harness_run(backend, self.ext)
                    self.assertFalse(out["ran"])
                    self.assertEqual(out["result"]["block"], True)
                    self.assertFalse(os.path.exists(self.marker))
                    rules = [d["rule"] for d in record.load(self.state)["decisions"]]
                    self.assertIn("block:canvas-control", rules)
                finally:
                    b.stop()
                    self.assertFalse(os.path.exists(self.sock))

    def test_socket_and_client_cannot_be_replaced_from_inside(self):
        backends = sandbox_backends()
        if not backends:
            self.skipTest("no sandbox backend available")
        b = self.real("acting")
        with open(self.ext) as f:
            original = f.read()
        for backend in backends:
            with self.subTest(backend=backend):
                attacks = [
                    ("rm", ["/bin/rm", self.sock]),
                    ("bind-over", NODE_ARGV[:1] + ["-e", "require('net').createServer().listen(%r)" % self.sock]),
                    ("bind-new", NODE_ARGV[:1] + ["-e", "require('net').createServer().listen(%r)"
                                                  % os.path.join(self.gdir, "sock2")]),
                    ("write-client", ["/bin/sh", "-c", "echo x >> '%s'" % self.ext]),
                    ("replace-client", ["/bin/sh", "-c", "echo x > '%s.new' && mv '%s.new' '%s'"
                                        % (self.ext, self.ext, self.ext)]),
                ]
                for name, argv in attacks:
                    p = self.sandboxed(backend, argv, {})
                    self.assertNotEqual(p.returncode, 0, "%s succeeded under %s" % (name, backend))
                self.assertTrue(os.path.exists(self.sock))
                self.assertFalse(os.path.exists(os.path.join(self.gdir, "sock2")))
                with open(self.ext) as f:
                    self.assertEqual(f.read(), original)
        self.assertEqual(b.counts()["calls"], 0)


class AgyMarkerPairTests(Env):
    """The agy client under the same control and guarded pair as a plain subprocess (agy's own sandbox is
    a later task). Every agy call is gray until the agy spike, so a guarded call is asked, not run."""
    runtime = "agy"

    def run_agy(self, env):
        p = subprocess.run(["python3", HOOK], input=json.dumps(
            {"toolName": "run_command", "input": {"command": "herdr pane send x"}, "cwd": self.wt}).encode(),
            capture_output=True, env=env, cwd=self.wt, timeout=15)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout.decode().strip()

    def test_marker_pair(self):
        marker = os.path.join(self.wt, ".bw-block-marker")

        def run_if_allowed(answer):
            if answer in ("{}",) or json.loads(answer)["decision"] == "allow":
                open(marker, "w").close()

        run_if_allowed(self.run_agy(self.env(mode=None)))  # control: inert, the call runs
        self.assertTrue(os.path.exists(marker))
        os.remove(marker)
        self.real("acting")
        run_if_allowed(self.run_agy(self.env("acting")))
        self.assertFalse(os.path.exists(marker))
        self.assertEqual([d["runtime"] for d in record.load(self.state)["decisions"]], ["agy"])


if __name__ == "__main__":
    unittest.main()
