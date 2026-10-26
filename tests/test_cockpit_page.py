import http.client
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(HERE))
from cockpit import server, views  # noqa: E402
from lead.proc import Result  # noqa: E402
from test_config_v2 import load_obj  # noqa: E402

PAGE = (ROOT / "cockpit" / "page.html").read_text(encoding="utf-8")
SVG_NS = "http://www.w3.org/2000/svg"
HOSTILE = "<img src=x onerror=alert(1)>"


class PageStatic(unittest.TestCase):
    def test_no_html_sinks_or_dynamic_code(self):
        for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
                    "<link", "src=", "srcdoc", "javascript:"):
            self.assertNotIn(bad, PAGE, bad)

    def test_no_inline_event_handler_attributes(self):
        self.assertIsNone(re.search(r"\bon[a-z]+\s*=", PAGE, re.I))

    def test_the_only_url_is_the_svg_namespace(self):
        urls = re.findall(r"https?:[^\s\"'`)<>]*", PAGE, re.I)
        self.assertEqual(set(urls), {SVG_NS})
        self.assertNotIn("//", PAGE.replace(SVG_NS, ""))

    def test_one_script_and_one_style_both_nonced(self):
        self.assertEqual(len(re.findall(r"<script\b", PAGE, re.I)), 1)
        self.assertEqual(len(re.findall(r"<style\b", PAGE, re.I)), 1)
        self.assertIn('<script nonce="{{NONCE}}">', PAGE)
        self.assertIn('<style nonce="{{NONCE}}">', PAGE)
        self.assertEqual(len(re.findall(r"\bstyle\s*=", PAGE)), 0)  # no inline style attributes either

    def test_tabs_in_the_required_order(self):
        m = re.search(r"const TABS = \[(.*?)\];", PAGE, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(re.findall(r'"([A-Za-z]+)"', m.group(1)), ["Models", "Usage", "History", "Now", "Graph"])

    def test_both_themes(self):
        self.assertIn("prefers-color-scheme: dark", PAGE)

    def test_null_renders_unavailable(self):
        self.assertIn('"unavailable"', PAGE)


class HostileThroughServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.env = {k: os.environ.get(k) for k in ("BALANCEWHEEL_METRICS_LOG", "BW_ORCA_SUPPORT", "BW_CCSWITCH_DIR")}
        os.environ["BALANCEWHEEL_METRICS_LOG"] = str(root / "metrics.jsonl")
        os.environ["BW_ORCA_SUPPORT"] = str(root / "orca")
        os.environ["BW_CCSWITCH_DIR"] = str(root / "cc-switch")
        (root / "metrics.jsonl").write_text(json.dumps(
            {"type": "panel", "id": "p1", "ts": "2026-10-01T00:00:00Z", "target": "t", "kind": "plan",
             "seats": {HOSTILE: "m"}, "findings": {HOSTILE: {"total": 1, "confirmed": 1}}}) + "\n")
        obj = json.loads((ROOT / "tests" / "fixtures" / "config_v2.json").read_text())
        obj["state_dir"] = str(root / "state")
        config = load_obj(obj)
        b = views.build(config, str(root / "state"), run=lambda argv, timeout, **kw: Result(127, "", "", False))
        self.srv, self.token = server.make_server(0, {f"/api/{k}": v for k, v in b.items()}, PAGE)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(5)
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def get(self, path, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", f"127.0.0.1:{self.port}")
            if cookie:
                c.putheader("Cookie", cookie)
            c.endheaders()
            r = c.getresponse()
            return r, r.read()
        finally:
            c.close()

    def test_models_returns_a_hostile_seat_name_as_a_json_string(self):
        r, _ = self.get(f"/?t={self.token}")
        cookie = r.getheader("Set-Cookie").split(";")[0]
        r, body = self.get("/api/models", cookie)
        self.assertEqual(r.status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("application/json"))
        seats = [s["seat"] for s in json.loads(body)["seats"]]
        self.assertIn(HOSTILE, seats)
        r, page = self.get("/", cookie)
        self.assertNotIn(HOSTILE.encode(), page)  # it reaches the DOM only through the JSON and textContent


if __name__ == "__main__":
    unittest.main()
