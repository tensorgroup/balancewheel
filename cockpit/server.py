"""server.py — the cockpit's HTTP server: loopback only, read-only, one browser.

Who may read: the browser `bw cockpit` opened. It arrives once with a one-time token
(`/?t=<token>`), which is exchanged, under a lock and at most once, for a cookie holding a
separate secret (HttpOnly, SameSite=Strict). Every other request must carry that cookie and a
Host header naming this loopback port (DNS rebinding), and use GET. Every response, errors
included, carries a nonce CSP and no CORS header, so no other origin can read a byte.
"""
import hmac
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from lead import proc

COOKIE = "bw_cockpit"
REFUSED = "open the cockpit with `bw cockpit`\n"
PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
CSP = ("default-src 'none'; script-src 'nonce-{n}'; style-src 'nonce-{n}'; connect-src 'self'; "
       "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _same(a, b):
    """Constant-time compare that never raises on non-ASCII input."""
    return hmac.compare_digest(a.encode("utf-8", "surrogateescape"), b.encode("utf-8", "surrogateescape"))


def load_page():
    with open(PAGE_PATH, encoding="utf-8") as f:
        return f.read()


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    allow_reuse_address = False  # a port another listener holds is "in use", never a shared bind

    def __init__(self, port, views, page):
        self.views = dict(views)
        self.page = page
        self.token = secrets.token_urlsafe(32)
        self.secret = secrets.token_urlsafe(32)  # the cookie; never the token
        self.token_used = False
        self.token_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), _Handler)

    def exchange(self, offered):
        with self.token_lock:
            if self.token_used or not _same(offered, self.token):
                return False
            self.token_used = True
            return True

    def handle_error(self, request, client_address):
        """A browser that hangs up mid-response is routine; anything else keeps the default report."""
        if isinstance(sys.exc_info()[1], ConnectionError):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    server_version = "bw-cockpit"
    sys_version = ""
    timeout = 10  # seconds; an idle or silent connection cannot pin a thread forever
    default_request_version = "HTTP/1.0"  # so a refusal of a malformed request still carries its headers

    def log_message(self, fmt, *args):  # no per-request noise on the user's terminal
        pass

    def __getattr__(self, name):  # every method but GET, including ones http.server has never heard of
        if name.startswith("do_"):
            return self._not_allowed
        raise AttributeError(name)

    def _send(self, status, body=b"", ctype="text/plain; charset=utf-8", headers=(), nonce=None):
        nonce = nonce or secrets.token_urlsafe(16)
        self.send_response(status)
        self.send_header("Content-Security-Policy", CSP.format(n=nonce))
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):  # http.server's own refusals (400, 414, 431, 505)
        self._send(code, f"{code} {message or ''}\n".encode("utf-8", "replace"))

    def _not_allowed(self):
        self._send(405, b"405 method not allowed\n", headers=[("Allow", "GET")])

    def _host_ok(self):
        hosts = self.headers.get_all("Host") or []
        port = self.server.server_address[1]
        return len(hosts) == 1 and hosts[0] in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _cookie_ok(self):
        values = []
        for header in self.headers.get_all("Cookie") or []:
            for part in header.split(";"):
                name, sep, value = part.strip().partition("=")
                if sep and name == COOKIE:
                    values.append(value)
        return len(values) == 1 and _same(values[0], self.server.secret)

    def do_GET(self):
        if not self._host_ok():
            return self._send(421, b"421 misdirected request\n")
        url = urlsplit(self.path)
        t = parse_qs(url.query, keep_blank_values=True).get("t") if url.path == "/" else None
        if t is not None:
            if len(t) == 1 and self.server.exchange(t[0]):
                cookie = f"{COOKIE}={self.server.secret}; HttpOnly; SameSite=Strict; Path=/"
                return self._send(303, headers=[("Location", "/"), ("Set-Cookie", cookie)])
            return self._send(403, REFUSED.encode())
        if not self._cookie_ok():
            return self._send(403, REFUSED.encode())
        if url.path == "/":
            nonce = secrets.token_urlsafe(16)
            body = self.server.page.replace("{{NONCE}}", nonce).encode("utf-8")
            return self._send(200, body, "text/html; charset=utf-8", nonce=nonce)
        view = self.server.views.get(url.path) if url.path.startswith("/api/") else None
        if view is None:
            return self._send(404, b"404 not found\n")
        try:
            body = json.dumps(view(), allow_nan=False)
        except Exception as e:  # one broken view must not take the page down, nor leak its message
            body = json.dumps({"available": False, "reason": type(e).__name__})
        self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")


def make_server(port, views, page):
    """Bind 127.0.0.1:<port> (0 picks one); `views` maps "/api/<name>" to a zero-argument callable
    returning JSON-able data. Returns (server, token); the caller runs `serve_forever`."""
    srv = _Server(port, views, page)
    return srv, srv.token


def open_url(url, run=proc.run):
    """Open the token URL in the default browser; False when `open` failed."""
    return run(["open", url], timeout=10).rc == 0
