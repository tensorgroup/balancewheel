"""`bw seat <id>` and `bw seat list`: flags, output channels, exit statuses, SIGTERM, and the wiring."""
import importlib.machinery
import importlib.util
import io
import json
import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
import test_seat_run as tsr  # noqa: E402
from lead import sandbox  # noqa: E402
from lead.proc import Result  # noqa: E402
from seats import caps, cli, run  # noqa: E402

RESULT = run.SeatResult(seat="astra", requested="astra", stand_in_for=None, runtime="codex", model="m", ran=True,
                        ok=True, reason="ok", exit_code=0, session_id="s1", mode="new", dir="/r", duration_s=1.0,
                        tokens_in=1, tokens_out=2, cost_usd=None, rate_limits=None, answer="the answer")


def load_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class Cli(unittest.TestCase):
    """Reuses the seat-run fixture (verified seats, temp state) without inheriting its tests."""
    setUp = tsr.SeatRun.setUp
    _pass = tsr.SeatRun._pass
    usage = tsr.SeatRun.usage
    scratch_dirs = tsr.SeatRun.scratch_dirs

    def main(self, argv, **kw):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(argv, self.config, self.state, out=out, err=err, **kw)
        return code, out.getvalue(), err.getvalue()

    def test_flags_map_to_run_seat(self):
        with mock.patch("seats.run.run_seat", return_value=RESULT) as rs:
            code = cli.main(["astra", "-b", "-d", "/r", "--deep", "--no-fallback", "--out", "/o", "p"],
                            self.config, self.state, out=io.StringIO(), err=io.StringIO())
        kw = rs.call_args.kwargs
        self.assertEqual((kw["brief"], kw["target"], kw["deep"], kw["fallback"], kw["out"]),
                         (True, "/r", True, False, "/o"))
        self.assertEqual((kw["resume"], kw["session_id"], kw["attack"]), (False, None, False))
        self.assertEqual(rs.call_args.args[2:4], ("astra", "p"))
        self.assertEqual(code, RESULT.exit_code)

    def test_resume_and_session_flags(self):
        with mock.patch("seats.run.run_seat", return_value=RESULT) as rs:
            cli.main(["astra", "-r", "p"], self.config, self.state, out=io.StringIO(), err=io.StringIO())
            self.assertTrue(rs.call_args.kwargs["resume"])
            cli.main(["astra", "--session", "abc", "--brief-file", "f", "p"], self.config, self.state,
                     out=io.StringIO(), err=io.StringIO())
        self.assertEqual((rs.call_args.kwargs["session_id"], rs.call_args.kwargs["brief_file"]), ("abc", "f"))

    def test_stdout_is_answer_only(self):
        failed = RESULT._replace(ok=False, reason="timeout", exit_code=124)
        with mock.patch("seats.run.run_seat", return_value=failed):
            code, out, err = self.main(["astra", "p"])
        self.assertEqual((code, out), (124, "the answer"))
        self.assertEqual(err, "bw seat: astra failed: timeout\n")
        with mock.patch("seats.run.run_seat", return_value=RESULT):
            code, out, err = self.main(["astra", "p"])
        self.assertEqual((code, out, err), (0, "the answer", ""))

    def test_exclusive_flags(self):
        for argv in (["astra", "-b", "--brief-file", "f", "p"], ["astra", "-r", "--session", "s", "p"],
                     ["astra", ""], ["astra", "   "], ["astra"]):
            with mock.patch("seats.run.run_seat") as rs:
                code, out, err = self.main(argv)
            self.assertEqual((code, out), (2, ""), argv)
            self.assertTrue(err.startswith("bw seat: usage error"), err)
            rs.assert_not_called()

    def test_unknown_seat(self):
        code, out, err = self.main(["nope", "p"])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("bw seat: unknown seat 'nope'; configured: ", err)
        for name in self.config["seats"]:
            self.assertIn(name, err)

    def test_result_file(self):
        path = os.path.join(self.home, "result.json")
        with mock.patch("seats.run.run_seat", return_value=RESULT):
            self.main(["astra", "--result", path, "p"])
        data = json.loads(Path(path).read_text())
        self.assertEqual((data["v"], data["reason"], data["ok"]), (1, "ok", True))
        self.assertNotIn("answer", data)

    def test_result_file_on_refusal(self):  # build ruling, 2026-11-23
        path = os.path.join(self.home, "result.json")
        code, out, err = self.main(["astra", "--result", path, "-d", self.wt, "p"], run=self.fake_unverified())
        data = json.loads(Path(path).read_text())
        self.assertEqual((code, data["ok"], data["ran"], data["exit_code"]), (75, False, False, 75))
        self.assertTrue(data["reason"].startswith("unverified"), data["reason"])
        self.assertIn("refused: unverified", err)

    def fake_unverified(self):
        os.remove(os.path.join(self.state, "seat-verify.json"))
        return self.fake

    def test_free_form_refusal_reason_printed_as_given(self):
        refused = RESULT._replace(ran=False, ok=False, reason="login needed: run `x login`", exit_code=75)
        with mock.patch("seats.run.run_seat", return_value=refused):
            code, out, err = self.main(["astra", "p"])
        self.assertEqual((code, err), (75, "bw seat: astra refused: login needed: run `x login`\n"))

    def test_attack_flag_hidden(self):
        with mock.patch("seats.run.run_seat", return_value=RESULT) as rs:
            self.main(["astra", "--attack", "p"])
            self.assertTrue(rs.call_args.kwargs["attack"])
            code, _, _ = self.main(["astra", "--attack", "x", "p"])
        self.assertEqual(code, 2)
        out = io.StringIO()
        with self.assertRaises(SystemExit), mock.patch("sys.stdout", out):
            cli._parser().parse_args(["-h"])
        self.assertNotIn("--attack", out.getvalue())
        self.assertIn("--no-fallback", out.getvalue())

    def test_sigterm_cleans_up(self):  # R8
        before = signal.getsignal(signal.SIGTERM)
        started, saw = threading.Event(), []
        inner = self.fake

        def blocking(argv, timeout, **kw):
            if argv[0] == "git" or len(argv) == 2:
                return inner(argv, timeout, **kw)
            started.set()
            try:
                time.sleep(20)
            except SystemExit:
                saw.append("interrupt")  # proc.run's group-kill path runs here
                raise

        def killer():
            started.wait(10)
            os.kill(os.getpid(), signal.SIGTERM)

        t = threading.Thread(target=killer, daemon=True)
        t.start()
        path = os.path.join(self.home, "result.json")
        code, out, err = self.main(["astra", "-d", self.wt, "--result", path, "review x"], run=blocking)
        t.join(5)
        self.assertEqual(code, 143)
        self.assertEqual(saw, ["interrupt"])
        row = self.usage()[-1]
        self.assertEqual((row["reason"], row["ok"], row["ran"]), ("interrupted", False, True))
        self.assertEqual(self.scratch_dirs(), set())
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        self.assertFalse(os.path.exists(path))  # SIGTERM writes no result file

    def test_list_text_and_json(self):
        caps.arm(self.state, "gem", time.time(), 6)
        code, out, err = self.main(["list", "--json"], run=self.fake)
        self.assertEqual(code, 0)
        rows = json.loads(out)
        keys = "id runtime model plan fallback tier enabled reachable verified failed_probe capped".split()
        self.assertEqual([list(r) for r in rows], [keys] * len(rows))
        self.assertEqual([r["id"] for r in rows], list(self.config["seats"]))
        by = {r["id"]: r for r in rows}
        self.assertTrue(by["astra"]["verified"])
        self.assertIsNone(by["astra"]["capped"])
        self.assertIn("capped until", by["gem"]["capped"])
        self.assertTrue((Path(self.state) / "seats.json").exists())
        code, text, _ = self.main(["list"], run=self.fake)
        lines = text.splitlines()
        self.assertEqual(len(lines), len(rows))
        astra = next(x for x in lines if x.startswith("astra  "))
        self.assertIn("  verified=yes  capped=no", astra)
        self.assertIn("tier=", astra)
        self.assertIn("capped=seat gem capped until", next(x for x in lines if x.startswith("gem  ")))

    def test_list_unverified_shows_probe(self):
        os.remove(os.path.join(self.state, "seat-verify.json"))
        _, text, _ = self.main(["list"], run=self.fake)
        self.assertIn("verified=no (never verified)", text)

    def test_bw_dispatch(self):
        bw = load_module("bw_seat_cli", ROOT / "bin" / "bw")
        self.assertIn("seat", bw.COMMANDS)
        self.assertIn("  bw seat <id>", bw.__doc__)
        self.assertIn("  bw seat list", bw.__doc__)
        self.assertEqual(bw.default_argv(["seat", "x", "p"], "/")[0], ["seat", "x", "p"])
        with mock.patch.object(bw, "load_config", return_value=self.config), \
                mock.patch.object(bw.seat_cli, "main", return_value=7) as m:
            self.assertEqual(bw.main(["seat", "astra", "-b", "p"]), 7)
        self.assertEqual(m.call_args.args[0], ["astra", "-b", "p"])

    def test_usage_map_lists_seat_subcommands(self):  # build ruling, 2026-11-23
        gen = load_module("usage_map_generate", ROOT / "docs" / "usage-map" / "generate.py")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "data.js"
            with mock.patch.object(gen, "OUT", out), mock.patch.object(gen, "rel", str), mock.patch("sys.stdout", io.StringIO()):
                gen.main()
            data = out.read_text()
        for cid in ("cmd:seat", "cmd:seat.list"):
            self.assertIn(f'"{cid}"', data)
        # Task 13 adds "cmd:seat.verify" to this test when it adds the docstring line.


if __name__ == "__main__":
    unittest.main()
