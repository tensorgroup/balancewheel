import http.client, json, os, re, signal, socket, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "metrics"))
sys.path.insert(0, str(HERE))
from cockpit import server  # noqa: E402
from lead import proc, sandbox  # noqa: E402
import config as cfg  # noqa: E402
from test_config_v2 import load_obj  # noqa: E402

PAGE = '<!doctype html><title>t</title><style nonce="{{NONCE}}"></style><script nonce="{{NONCE}}">1</script>'
CSP = re.compile(r"^default-src 'none'; script-src 'nonce-([A-Za-z0-9_-]+)'; style-src 'nonce-\1'; "
                 r"connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'; "
                 r"frame-ancestors 'none'$")


def boom():
    raise ValueError("secret detail")


class CockpitServer(unittest.TestCase):
    def setUp(self):
        self.srv, self.token = server.make_server(0, {"/api/ok": lambda: {"a": 1}, "/api/boom": boom}, PAGE)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, args=(0.05,), daemon=True)
        self.thread.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.thread.join(5)

    def req(self, method="GET", path="/", host=None, cookie=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            c.putheader("Host", host or f"127.0.0.1:{self.port}")
            if cookie is not None:
                c.putheader("Cookie", cookie)
            c.endheaders()
            r = c.getresponse()
            return r, r.read()
        finally:
            c.close()

    def assert_hardened(self, r):
        self.assertRegex(r.getheader("Content-Security-Policy") or "", CSP)
        self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        self.assertEqual([h for h, _ in r.getheaders() if h.lower().startswith("access-control-")], [])

    def login(self):
        r, _ = self.req(path=f"/?t={self.token}")
        self.assertEqual(r.status, 303)
        return r.getheader("Set-Cookie").split(";")[0]

    def test_binds_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_token_exchanged_for_httponly_strict_cookie(self):
        r, _ = self.req(path=f"/?t={self.token}")
        self.assert_hardened(r)
        self.assertEqual(r.status, 303)
        self.assertEqual(r.getheader("Location"), "/")
        sc = r.getheader("Set-Cookie")
        parts = [p.strip() for p in sc.split(";")]
        name, value = parts[0].split("=", 1)
        self.assertEqual(name, "bw_cockpit")
        self.assertNotEqual(value, self.token)
        self.assertGreaterEqual(len(value), 32)
        self.assertIn("HttpOnly", parts)
        self.assertIn("SameSite=Strict", parts)
        self.assertIn("Path=/", parts)

    def test_token_reuse_refused(self):
        self.login()
        r, _ = self.req(path=f"/?t={self.token}")
        self.assertEqual(r.status, 403)
        self.assert_hardened(r)

    def test_wrong_token_refused_and_does_not_burn_the_real_one(self):
        for path in ("/?t=nope", "/?t=", f"/?t={self.token}&t={self.token}", "/?t=%C3%A9"):
            r, _ = self.req(path=path)
            self.assertEqual(r.status, 403, path)
        self.login()

    def test_ten_concurrent_token_requests_one_cookie(self):
        barrier = threading.Barrier(10)
        codes = []

        def go():
            barrier.wait()
            r, _ = self.req(path=f"/?t={self.token}")
            codes.append(r.status)

        ts = [threading.Thread(target=go) for _ in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        self.assertEqual(sorted(codes), [303] + [403] * 9)

    def test_no_cookie_refused(self):
        for path in ("/", "/api/ok"):
            r, body = self.req(path=path)
            self.assertEqual(r.status, 403, path)
            self.assertIn(b"open the cockpit with `bw cockpit`", body)
            self.assert_hardened(r)

    def test_wrong_cookie_refused(self):
        self.login()
        for cookie in ("bw_cockpit=wrong", f"bw_cockpit={self.token}", "other=1", "bw_cockpit=é"):
            r, _ = self.req(path="/api/ok", cookie=cookie)
            self.assertEqual(r.status, 403, cookie)

    def test_foreign_host_refused_even_with_cookie(self):
        cookie = self.login()
        for host in (f"evil.example:{self.port}", "127.0.0.1", f"127.0.0.1:{self.port + 1}", f"localhost.evil:{self.port}"):
            r, _ = self.req(path="/api/ok", host=host, cookie=cookie)
            self.assertEqual(r.status, 421, host)
            self.assert_hardened(r)

    def test_foreign_host_cannot_exchange_the_token(self):
        r, _ = self.req(path=f"/?t={self.token}", host=f"evil.example:{self.port}")
        self.assertEqual(r.status, 421)
        self.login()  # still unused

    def test_localhost_host_accepted(self):
        cookie = self.login()
        r, body = self.req(path="/api/ok", host=f"localhost:{self.port}", cookie=cookie)
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body), {"a": 1})

    def test_methods_other_than_get_refused(self):
        cookie = self.login()
        for m in ("OPTIONS", "POST", "PUT", "DELETE", "HEAD", "PATCH", "BREW"):
            r, _ = self.req(method=m, path="/api/ok", cookie=cookie)
            self.assertEqual(r.status, 405, m)
            self.assertEqual(r.getheader("Allow"), "GET", m)
            self.assert_hardened(r)

    def test_page_gets_a_fresh_nonce_each_load(self):
        cookie = self.login()
        nonces = []
        for _ in range(2):
            r, body = self.req(path="/", cookie=cookie)
            self.assertEqual(r.status, 200)
            self.assert_hardened(r)
            self.assertTrue(r.getheader("Content-Type").startswith("text/html"))
            n = CSP.match(r.getheader("Content-Security-Policy")).group(1)
            self.assertIn(f'<script nonce="{n}">'.encode(), body)
            self.assertNotIn(b"{{NONCE}}", body)
            nonces.append(n)
        self.assertNotEqual(nonces[0], nonces[1])

    def test_view_raising_is_unavailable_not_500(self):
        cookie = self.login()
        r, body = self.req(path="/api/boom", cookie=cookie)
        self.assertEqual(r.status, 200)
        self.assertEqual(json.loads(body), {"available": False, "reason": "ValueError"})
        self.assertNotIn(b"secret detail", body)

    def test_unknown_path_404(self):
        cookie = self.login()
        for path in ("/api/nope", "/nope", "/api/"):
            r, _ = self.req(path=path, cookie=cookie)
            self.assertEqual(r.status, 404, path)
            self.assert_hardened(r)

    def test_malformed_request_still_hardened(self):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            s.sendall(b"GET / HTTP/9.9\r\n\r\n")
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        finally:
            s.close()
        head = data.split(b"\r\n\r\n")[0].decode("latin-1")
        self.assertIn("Content-Security-Policy: default-src 'none'", head)
        self.assertNotIn("Access-Control-", head)


class OpenUrl(unittest.TestCase):
    def test_opens_with_open_through_run(self):
        calls = []

        def run(argv, timeout, **kw):
            calls.append(argv)
            return proc.Result(0, "", "", False)

        self.assertTrue(server.open_url("http://127.0.0.1:1/?t=x", run=run))
        self.assertEqual(calls, [["open", "http://127.0.0.1:1/?t=x"]])

    def test_open_failure_reported(self):
        self.assertFalse(server.open_url("u", run=lambda argv, timeout, **kw: proc.Result(127, "", "open: not found", False)))


class CockpitConfig(unittest.TestCase):
    def base(self, **cockpit):
        return {"schema_version": 2, "seats": {"s": {"runtime": "codex", "model": "m"}}, "cockpit": cockpit}

    def test_default_port(self):
        c = load_obj(self.base())
        self.assertEqual(c["cockpit"], {"port": cfg.DEFAULT_COCKPIT_PORT})
        self.assertEqual(cfg.DEFAULT_COCKPIT_PORT, sandbox.DEFAULT_COCKPIT_PORT)

    def test_port_bounds(self):
        for bad in (80, 1023, 65536, "8765", 8765.0, True, None):
            with self.assertRaises(cfg.ConfigError, msg=repr(bad)) as e:
                load_obj(self.base(port=bad))
            self.assertIn("cockpit.port", str(e.exception))
        for ok in (1024, 65535):
            self.assertEqual(load_obj(self.base(port=ok))["cockpit"]["port"], ok)

    def test_unknown_key_warns(self):
        c = load_obj(self.base(port=9100, theme="dark"))
        self.assertEqual(c["cockpit"], {"port": 9100})
        self.assertEqual(c["warnings"], ["cockpit.theme: unknown key (ignored)"])

    def test_configured_port_reaches_sandbox(self):
        c = load_obj(self.base(port=9100))
        self.assertIn(9100, sandbox.denied_ports(c))
        self.assertIn(sandbox.DEFAULT_COCKPIT_PORT, sandbox.denied_ports(c))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class BwCockpit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        c = {"schema_version": 2, "seats": {"s": {"runtime": "codex", "model": "m"}},
             "state_dir": os.path.join(root, "state"), "metrics_log": os.path.join(root, "m.jsonl")}
        path = os.path.join(root, "c.json")
        with open(path, "w") as f:
            json.dump(c, f)
        self.env = dict(os.environ, BALANCEWHEEL_CONFIG=path, BW_STATE_DIR=os.path.join(root, "state"))
        self.bw = str(ROOT / "bin" / "bw")

    def tearDown(self):
        self.tmp.cleanup()

    def test_serves_and_prints_url_with_no_open(self):
        port = free_port()
        p = subprocess.Popen([sys.executable, self.bw, "cockpit", "--port", str(port), "--no-open"], env=self.env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = p.stdout.readline()
            m = re.search(rf"http://127\.0\.0\.1:{port}/\?t=([A-Za-z0-9_-]+)", line)
            self.assertTrue(m, line)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", f"/?t={m.group(1)}")
            self.assertEqual(c.getresponse().status, 303)
            c.close()
        finally:
            p.send_signal(signal.SIGINT)
            out, err = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0, err)
        self.assertNotIn("Traceback", err)

    def test_port_in_use_one_line_exit_1(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        try:
            r = subprocess.run([sys.executable, self.bw, "cockpit", "--port", str(port), "--no-open"], env=self.env,
                               capture_output=True, text=True, timeout=10)
        finally:
            s.close()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(r.stderr.strip().splitlines()), 1, r.stderr)
        self.assertIn(f"port {port} is in use", r.stderr)

    def test_port_flag_out_of_range(self):
        r = subprocess.run([sys.executable, self.bw, "cockpit", "--port", "80", "--no-open"], env=self.env,
                           capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
