"""Tiny stdlib client for the LabDog API (session cookie + CSRF header)."""
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


class Api:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar(policy=http.cookiejar.DefaultCookiePolicy(
            allowed_domains=None, strict_ns_set_initial_dollar=False))
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def wait_ready(self, timeout=240):
        end = time.time() + timeout
        while time.time() < end:
            try:
                with urllib.request.urlopen(BASE + "/health/ready", timeout=5) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(3)
        raise SystemExit("LabDog never became ready")

    def _csrf(self):
        return next((c.value for c in self.jar if c.name == "labdog_csrf"), "")

    def _req(self, method, path, data=None, form=False, ok=(200, 201, 202, 204)):
        hdrs = {"X-CSRF-Token": self._csrf()}
        body = None
        if form:
            body = urllib.parse.urlencode(data).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif data is not None:
            body = json.dumps(data).encode()
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(BASE + path, data=body, headers=hdrs, method=method)
        try:
            with self.op.open(req, timeout=120) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        if status not in ok:
            raise RuntimeError(f"{method} {path}: {status} {raw[:400]!r}")
        return json.loads(raw) if raw else None

    def login(self, email, password):
        self._req("POST", "/api/auth/jwt/login", {"username": email, "password": password},
                  form=True)

    def get(self, path, **kw):
        return self._req("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self._req("POST", path, data if data is not None else {}, **kw)

    def put(self, path, data, **kw):
        return self._req("PUT", path, data, **kw)

    def patch(self, path, data, **kw):
        return self._req("PATCH", path, data, **kw)
