import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import orca, probes, quota  # noqa: E402
from lead.proc import Result  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
CANARY = "sk-or-SYNTHETIC-CANARY"
NOW = datetime(2027, 1, 15, 8, 0, tzinfo=timezone.utc).timestamp()  # mid-month
T = 1793000060.0  # a minute after the Orca fixtures' updatedAt
MONTH_START = datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp()
NEXT_MONTH = datetime(2027, 2, 1, tzinfo=timezone.utc).timestamp()


def key_doc(name):
    return json.loads((FIX / "openrouter" / name).read_text())


def or_plan(budget=20, key_env="OPENROUTER_API_KEY"):
    return {"vendor": "openrouter", "billing": "metered", "quota": "probe", "budget_usd_month": budget,
            "orca_provider": None, "orca_windows": {}, "probe": "openrouter", "key_env": key_env}


def fake_fetch(status=200, body=None, reason="", raises=None):
    calls = []

    def fetch(url, key, timeout):
        calls.append((url, key, timeout))
        if raises is not None:
            raise raises
        return status, body if body is not None else b"", reason
    fetch.calls = calls
    return fetch


def no_run(argv, timeout, **kw):
    raise AssertionError(f"ran {argv}")


class AgyStubTest(unittest.TestCase):
    def test_agy_is_unknown_awaiting_task_g_and_runs_nothing(self):
        calls = []
        s = probes.probe_agy({"probe": "agy"}, "/nonexistent", NOW, run=lambda *a, **k: calls.append(a))
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "agy /usage: awaiting Task G (shape not yet observed)")
        self.assertEqual(calls, [])


class ParseKeyTest(unittest.TestCase):
    def test_budget_window_from_usage_monthly(self):
        s = probes.parse_openrouter_key(key_doc("key_ok.json"), 20, None, NOW)
        self.assertNotIn("unknown", s)
        self.assertEqual(s["windows"], [{"name": "budget", "used_pct": 25.0, "resets_at": NEXT_MONTH}])
        self.assertEqual((s["spend_usd"], s["budget_usd_month"], s["is_free_tier"]), (5.0, 20, False))
        self.assertEqual(s["observed_at"], NOW)

    def test_no_budget_window_at_budget_zero_or_none(self):
        for budget in (0, None):
            s = probes.parse_openrouter_key(key_doc("key_ok.json"), budget, None, NOW)
            self.assertEqual(s["windows"], [], budget)
            self.assertEqual(s["spend_usd"], 5.0)

    def test_used_pct_display_capped_at_100(self):
        doc = key_doc("key_ok.json"); doc["data"]["usage_monthly"] = 50.0
        s = probes.parse_openrouter_key(doc, 20, None, NOW)
        self.assertEqual(s["windows"][0]["used_pct"], 100.0)

    def test_without_usage_monthly_a_mid_month_baseline_leaves_spend_unknown(self):
        at = MONTH_START + 3 * 86400
        s = probes.parse_openrouter_key(key_doc("key_no_limit.json"), 20, {"usage": 10.0, "at": at}, NOW)
        self.assertEqual(s["windows"], [])
        self.assertIsNone(s["spend_usd"])
        self.assertEqual(s["note"], f"monthly spend unknown before {time.ctime(at)}")

    def test_without_usage_monthly_a_baseline_from_before_the_first_counts_spend(self):
        s = probes.parse_openrouter_key(key_doc("key_no_limit.json"), 20, {"usage": 25.0, "at": MONTH_START - 60}, NOW)
        self.assertEqual(s["spend_usd"], 5.0)
        self.assertEqual(s["windows"], [{"name": "budget", "used_pct": 25.0, "resets_at": NEXT_MONTH}])
        self.assertNotIn("note", s)

    def test_baseline_exactly_at_the_first_counts(self):
        s = probes.parse_openrouter_key(key_doc("key_no_limit.json"), 20, {"usage": 26.0, "at": MONTH_START}, NOW)
        self.assertEqual(s["spend_usd"], 4.0)

    def test_null_limit_has_no_limits(self):
        self.assertNotIn("limits", probes.parse_openrouter_key(key_doc("key_free_tier.json"), 20, None, NOW))
        self.assertTrue(probes.parse_openrouter_key(key_doc("key_free_tier.json"), 20, None, NOW)["is_free_tier"])

    def test_positive_limit_is_shown_never_a_window(self):
        s = probes.parse_openrouter_key(key_doc("key_ok.json"), 0, None, NOW)
        self.assertEqual(s["limits"], {"key_limit": {"limit": 100.0, "limit_remaining": 57.5}})
        self.assertEqual(s["windows"], [])

    def test_only_the_documented_keys_are_kept(self):
        s = probes.parse_openrouter_key(key_doc("key_ok.json"), 20, None, NOW)
        self.assertEqual(set(s), {"source", "windows", "observed_at", "spend_usd", "budget_usd_month",
                                  "is_free_tier", "limits"})

    def test_malformed_documents_are_unknown(self):
        for doc in (None, [], {"data": []}, {"data": {"usage": "lots"}}, {"data": {"usage": float("nan")}}):
            s = probes.parse_openrouter_key(doc, 20, None, NOW)
            self.assertTrue(s["unknown"], doc)

    def test_non_finite_usage_monthly_is_ignored(self):
        doc = key_doc("key_ok.json"); doc["data"]["usage_monthly"] = float("inf")
        s = probes.parse_openrouter_key(doc, 20, None, NOW)
        self.assertIsNone(s["spend_usd"])
        self.assertEqual(s["windows"], [])


class ProbeOpenRouterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def probe(self, fetch, env=None, plan=None, name="or"):
        env = {"OPENROUTER_API_KEY": CANARY} if env is None else env
        return probes.probe_openrouter(plan or or_plan(), self.state, NOW, fetch=fetch, env=env, plan=name)

    def test_ok(self):
        f = fake_fetch(body=json.dumps(key_doc("key_ok.json")).encode())
        s = self.probe(f)
        self.assertEqual(s["windows"][0]["used_pct"], 25.0)
        self.assertEqual(f.calls, [(probes.KEY_URL, CANARY, 10)])

    def test_unset_env_is_unknown_without_a_fetch(self):
        f = fake_fetch()
        s = self.probe(f, env={})
        self.assertEqual(s["reason"], "key env OPENROUTER_API_KEY unset")
        self.assertEqual(f.calls, [])
        s = self.probe(f, env={"MY_KEY": ""}, plan=or_plan(key_env="MY_KEY"))
        self.assertEqual(s["reason"], "key env MY_KEY unset")
        self.assertEqual(f.calls, [])

    def test_http_401(self):
        s = self.probe(fake_fetch(status=401))
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "HTTP 401")

    def test_fetch_raising_urlerror_is_unknown(self):
        s = self.probe(fake_fetch(raises=urllib.error.URLError(f"boom {CANARY}")))
        self.assertTrue(s["unknown"])
        self.assertEqual(s["reason"], "URLError")

    def test_not_json(self):
        self.assertEqual(self.probe(fake_fetch(body=b"<html>")).get("reason"), "openrouter: response is not JSON")

    def test_baseline_first_reading_of_the_month_kept(self):
        doc = key_doc("key_no_limit.json")
        self.probe(fake_fetch(body=json.dumps(doc).encode()))
        path = os.path.join(self.state, "probes", "openrouter-or-2027-01.json")
        self.assertEqual(json.loads(Path(path).read_text()), {"usage": 30.0, "at": NOW})
        doc["data"]["usage"] = 31.0
        s = self.probe(fake_fetch(body=json.dumps(doc).encode()))
        self.assertEqual(json.loads(Path(path).read_text()), {"usage": 30.0, "at": NOW})
        self.assertEqual(s["note"], f"monthly spend unknown before {time.ctime(NOW)}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.server.seen.append(dict(self.headers))
        if self.server.location:
            self.send_response(302)
            self.send_header("Location", self.server.location)
            self.end_headers()
        else:
            body = json.dumps(key_doc("key_ok.json")).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)


def serve(location=None):
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.seen, srv.location = [], location
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.servers = []
        for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
            p = mock.patch.dict(os.environ); p.start(); self.addCleanup(p.stop)
            os.environ.pop(k, None)

    def tearDown(self):
        for srv, t in self.servers:
            srv.shutdown(); srv.server_close(); t.join()

    def server(self, location=None):
        srv, t = serve(location)
        self.servers.append((srv, t))
        return srv, f"http://127.0.0.1:{srv.server_address[1]}/"

    def test_redirect_refused_and_the_key_never_reaches_the_second_host(self):
        second, second_url = self.server()
        first, first_url = self.server(location=second_url)
        status, body, reason = probes._fetch(first_url, CANARY, 5, _test_any_url=True)
        self.assertEqual((status, body, reason), (302, b"", "redirect refused"))
        self.assertEqual(first.seen[0].get("Authorization"), "Bearer " + CANARY)
        self.assertTrue(all("Authorization" not in h for h in second.seen))
        self.assertEqual(second.seen, [])

    def test_ok_through_the_seam(self):
        srv, url = self.server()
        status, body, reason = probes._fetch(url, CANARY, 5, _test_any_url=True)
        self.assertEqual((status, reason), (200, ""))
        self.assertEqual(json.loads(body)["data"]["usage_monthly"], 5.0)

    def test_any_other_url_refused_without_a_request(self):
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("requested")):
            self.assertEqual(probes._fetch("https://example.invalid/", CANARY, 5), (0, b"", "url refused"))
        srv, url = self.server()
        self.assertEqual(probes._fetch(url, CANARY, 5), (0, b"", "url refused"))
        self.assertEqual(srv.seen, [])

    def test_exception_named_never_described(self):
        srv, url = self.server()
        port = srv.server_address[1]
        srv.shutdown(); srv.server_close(); self.servers[-1][1].join(); self.servers.pop()
        status, body, reason = probes._fetch(f"http://127.0.0.1:{port}/", CANARY, 5, _test_any_url=True)
        self.assertEqual((status, body), (0, b""))
        self.assertEqual(reason, "ConnectionRefusedError")
        self.assertNotIn(CANARY, reason)


def load_bw():
    loader = importlib.machinery.SourceFileLoader("bw_cli", str(ROOT / "bin" / "bw"))
    spec = importlib.util.spec_from_loader("bw_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class KeySecrecyTest(unittest.TestCase):
    """The key is in no file under the state dir, no output of `bw probe`, and no reason string."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.state = root / "state"
        cfg = {"schema_version": 2,
               "plans": {"or": {"vendor": "openrouter", "billing": "metered", "quota": "probe",
                                "budget_usd_month": 20}},
               "leads": {"lo": {"runtime": "pi", "model": "m", "plan": "or"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}}, "profiles": {"p": {"lead_order": ["lo"]}}, "default_profile": "p",
               "state_dir": str(self.state), "metrics_log": str(root / "metrics.jsonl")}
        (root / "config.json").write_text(json.dumps(cfg))
        p = mock.patch.dict(os.environ, {"BALANCEWHEEL_CONFIG": str(root / "config.json"),
                                         "OPENROUTER_API_KEY": CANARY, "BW_ORCA_SUPPORT": str(root / "no-orca")})
        p.start(); self.addCleanup(p.stop)
        os.environ.pop("BW_STATE_DIR", None)
        self.bw = load_bw()
        self.servers = []

    def tearDown(self):
        for srv, t in self.servers:
            srv.shutdown(); srv.server_close(); t.join()
        self.tmp.cleanup()

    def bw_probe(self, fetch):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(probes, "_fetch", fetch), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.bw.main(["probe"])
        return code, out.getvalue(), err.getvalue()

    def assert_secret(self, out, err):
        self.assertNotIn(CANARY, out + err)
        for dirpath, _, files in os.walk(self.state):
            for f in files:
                self.assertNotIn(CANARY.encode(), Path(dirpath, f).read_bytes(), f)

    def redirect_fetch(self):
        second, t2 = serve(); self.servers.append((second, t2))
        first, t1 = serve(location=f"http://127.0.0.1:{second.server_address[1]}/"); self.servers.append((first, t1))
        url = f"http://127.0.0.1:{first.server_address[1]}/"
        real = probes._fetch  # bound now: bw_probe patches the module attribute with this lambda
        return lambda u, key, timeout: real(url, key, timeout, _test_any_url=True)

    def test_success_401_redirect_and_exception(self):
        ok = fake_fetch(body=json.dumps(key_doc("key_ok.json")).encode())

        def boom(url, key, timeout):
            raise OSError(f"failed for {key}")
        cases = [(ok, "or: budget 75%"), (fake_fetch(status=401), "HTTP 401"),
                 (self.redirect_fetch(), "redirect refused"), (boom, "OSError")]
        for fetch, want in cases:
            code, out, err = self.bw_probe(fetch)
            self.assertEqual(code, 0, err)
            self.assertIn(want, out)
            self.assert_secret(out, err)
            snap = quota.read_sources(str(self.state))["or"]["probe"]
            self.assertNotIn(CANARY, json.dumps(snap))

    def test_unknown_plan_refused(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.bw.main(["probe", "nope"])


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        p = mock.patch.object(orca, "app_version", return_value="1.0.0")
        p.start(); self.addCleanup(p.stop)
        self.payload = json.loads((FIX / "orca" / "account_list_ok.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def config(self):
        def oplan(provider):
            return {"vendor": "", "billing": "plan", "quota": "orca", "budget_usd_month": None,
                    "orca_provider": provider, "orca_windows": {}, "probe": None, "key_env": "OPENROUTER_API_KEY"}
        return {"plans": {"chatgpt": oplan("codex"), "kimi-plan": oplan("kimi"), "or": or_plan(),
                          "gp": {"vendor": "google", "billing": "plan", "quota": "probe", "budget_usd_month": None,
                                 "orca_provider": None, "orca_windows": {}, "probe": "agy",
                                 "key_env": "OPENROUTER_API_KEY"}},
                "rotation": {"probe_every_min": 15, "cap_expiry_hours": 5}}

    def run_fake(self):
        calls = []

        def run(argv, timeout, **kw):
            calls.append(list(argv))
            if argv[:1] == ["orca"]:
                return Result(0, json.dumps(self.payload), "", False)
            raise AssertionError(f"ran {argv}")
        run.calls = calls
        return run

    def test_one_orca_read_for_two_orca_plans_and_every_probe_written(self):
        run = self.run_fake()
        fetch = fake_fetch(body=json.dumps(key_doc("key_ok.json")).encode())
        before = threading.active_count()
        res = probes.refresh(self.config(), self.state, T, run=run, fetch=fetch, env={"OPENROUTER_API_KEY": CANARY})
        self.assertEqual(threading.active_count(), before)
        self.assertEqual(run.calls, [["orca", "account", "list", "--json"]])
        self.assertEqual(len(fetch.calls), 1)
        src = quota.read_sources(self.state)
        self.assertEqual(set(src), {"chatgpt", "kimi-plan", "or", "gp"})
        self.assertNotIn("unknown", src["chatgpt"]["orca"])
        self.assertNotIn("unknown", src["kimi-plan"]["orca"])
        self.assertEqual(src["or"]["probe"]["windows"][0]["used_pct"], 25.0)
        self.assertIn("awaiting Task G", src["gp"]["probe"]["reason"])
        self.assertEqual(set(res["snapshots"]), {"or", "gp"})  # probe snapshots; Orca's are on disk

    def test_named_plans_only(self):
        run = self.run_fake()
        fetch = fake_fetch(body=json.dumps(key_doc("key_ok.json")).encode())
        probes.refresh(self.config(), self.state, T, plans=["or"], run=run, fetch=fetch,
                       env={"OPENROUTER_API_KEY": CANARY})
        self.assertEqual(run.calls, [])
        self.assertEqual(set(quota.read_sources(self.state)), {"or"})

    def test_a_probe_plan_without_a_probe_is_unknown(self):
        cfg = self.config()
        cfg["plans"] = {"x": dict(or_plan(), probe=None, vendor="")}
        probes.refresh(cfg, self.state, T, run=no_run, fetch=fake_fetch(), env={})
        self.assertEqual(quota.read_sources(self.state)["x"]["probe"]["reason"], "no probe for this plan")

    def test_a_probe_that_raises_is_unknown_named(self):
        with mock.patch.object(probes, "parse_openrouter_key", side_effect=KeyError(CANARY)):
            res = probes.refresh(self.config(), self.state, T, plans=["or"], run=no_run,
                                 fetch=fake_fetch(body=b"{}"), env={"OPENROUTER_API_KEY": CANARY})
        self.assertEqual(res["snapshots"]["or"]["reason"], "probe failed (KeyError)")

    def test_expired_caps_cleared_after_the_snapshots(self):
        quota.record_cap(self.state, "or", T - 3600, resets_at=T - 60)
        fetch = fake_fetch(body=json.dumps(key_doc("key_ok.json")).encode())
        probes.refresh(self.config(), self.state, T, plans=["or"], run=no_run, fetch=fetch,
                       env={"OPENROUTER_API_KEY": CANARY})
        self.assertEqual(quota.read_caps(self.state, T, include_expired=True), {})

    def test_orca_version_change_is_returned(self):
        run = self.run_fake()
        probes.refresh(self.config(), self.state, T, plans=["chatgpt"], run=run, fetch=fake_fetch(), env={})
        with mock.patch.object(orca, "app_version", return_value="1.1.0"):
            res = probes.refresh(self.config(), self.state, T, plans=["chatgpt"], run=run, fetch=fake_fetch(), env={})
        self.assertEqual(res["notes"], ["Orca 1.0.0 → 1.1.0: shape re-checked: codex ok"])


if __name__ == "__main__":
    unittest.main()
