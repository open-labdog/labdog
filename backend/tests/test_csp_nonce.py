"""Per-response CSP nonces on every HTML document.

``script-src`` used to carry ``'unsafe-inline'`` because Next's static
export inlines the RSC flight data as ``<script>`` blocks. Build-time
hashes were the obvious alternative and do not work here: the export's 147
inline scripts include 19 that ``_rewrite_placeholder`` mutates *per
request*, so a hash computed at build time never matches what is served.

So each HTML response gets a fresh nonce stamped into its inline tags and
a CSP naming that nonce. Two properties carry the whole design:

* the nonce differs per response, or it is not a nonce;
* the cached document is shared across responses, or the rewrite's
  ``lru_cache`` was pointless.

Both are only true together because the *sentinel* is cached and the
substitution happens per response.
"""

import re

import pytest

from app.main import (
    _CSP,
    _DOCS_PATHS,
    _NONCE_SLOT,
    SecurityHeadersMiddleware,
    _csp,
    _html_response,
    _inject_nonce_slots,
    _new_nonce,
)

_NONCE_IN_CSP = re.compile(r"'nonce-([A-Za-z0-9+/=]+)'")
_NONCE_ATTR = re.compile(rb' nonce="[A-Za-z0-9+/=]+"')


class TestTheStamping:
    def test_an_inline_script_gets_a_slot(self):
        out = _inject_nonce_slots("<script>alert(1)</script>")
        assert f'<script nonce="{_NONCE_SLOT}">' in out

    def test_existing_attributes_survive(self):
        out = _inject_nonce_slots('<script type="module">x</script>')
        assert f'<script type="module" nonce="{_NONCE_SLOT}">' in out

    def test_external_scripts_are_left_alone(self):
        """They are already governed by 'self', and the fewer tags a regex
        over markup touches, the smaller its blast radius."""
        html = '<script src="/_next/static/chunk.js"></script>'
        assert _inject_nonce_slots(html) == html

    def test_every_inline_script_in_a_document_is_stamped(self):
        """The export puts three inline scripts in every page. One missed
        tag is a page that renders without its flight data."""
        html = "<script>a</script><script src='x.js'></script><script>b</script>"
        assert _inject_nonce_slots(html).count(_NONCE_SLOT) == 2

    def test_the_slot_is_not_something_a_document_could_contain(self):
        assert len(_NONCE_SLOT) >= 32


class TestTheNonceItself:
    def test_it_is_128_bits(self):
        import base64

        assert len(base64.b64decode(_new_nonce())) == 16

    def test_two_calls_differ(self):
        assert _new_nonce() != _new_nonce()


class TestTheHeader:
    def test_the_fallback_forbids_inline_script(self):
        """The fallback is strict, not permissive, on purpose: a future path
        that returns HTML without going through the nonce machinery should
        break visibly rather than quietly serve inline script."""
        assert b"'unsafe-inline'" not in _CSP.split(b";")[1]

    def test_a_nonce_csp_names_that_nonce(self):
        header = _csp("script-src 'self' 'nonce-abc123'").decode()
        assert "script-src 'self' 'nonce-abc123'" in header

    def test_the_other_directives_are_unchanged(self):
        header = _csp("script-src 'self'").decode()
        for directive in (
            "default-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ):
            assert directive in header


class TestOnRenderedDocuments:
    """Exercised through ``_html_response`` rather than over HTTP.

    ``app.main.app`` is built at import with whatever static directory
    exists then — none, in a test container — so there is no ``spa_fallback``
    route to drive. The function is where all the behaviour lives anyway;
    the middleware half is covered separately below.
    """

    def test_a_rendered_page_carries_a_nonce_matching_its_scripts(self, export):
        resp = _html_response(export / "dashboard" / "index.html")

        nonce = _NONCE_IN_CSP.search(resp.headers["content-security-policy"])
        assert nonce, "an HTML response must declare a nonce"
        assert f'nonce="{nonce.group(1)}"'.encode() in resp.body, (
            "the nonce in the header must be the one stamped into the document — "
            "a mismatch blocks every script on the page"
        )

    def test_the_sentinel_never_reaches_the_client(self, export):
        resp = _html_response(export / "dashboard" / "index.html")
        assert _NONCE_SLOT.encode() not in resp.body, (
            "an unsubstituted slot means the cached document was served raw"
        )

    def test_two_responses_get_different_nonces(self, export):
        """The point of a nonce. If the cache returned a fully-rendered
        document these would match, and reading one page would predict the
        token for every later one."""
        page = export / "dashboard" / "index.html"
        a = _NONCE_IN_CSP.search(_html_response(page).headers["content-security-policy"])
        b = _NONCE_IN_CSP.search(_html_response(page).headers["content-security-policy"])
        assert a.group(1) != b.group(1)

    def test_the_document_body_is_cached_across_responses(self, export):
        """Both properties at once: shared cache, per-response nonce.

        Strip the nonces and the two bodies must be byte-identical — that is
        what proves the expensive part was reused rather than redone.
        """
        page = export / "dashboard" / "index.html"
        first, second = _html_response(page), _html_response(page)
        strip = lambda body: _NONCE_ATTR.sub(b"", body)  # noqa: E731
        assert strip(first.body) == strip(second.body)

    def test_a_dynamic_route_is_stamped_and_still_rewritten(self, export):
        """The 19 rewritten scripts are exactly the ones hashes could not
        cover, so they are the ones worth pinning."""
        resp = _html_response(export / "hosts" / "placeholder" / "index.html", ("42",))

        nonce = _NONCE_IN_CSP.search(resp.headers["content-security-policy"])
        assert nonce
        assert f'nonce="{nonce.group(1)}"'.encode() in resp.body
        assert b"42" in resp.body, "the placeholder rewrite must still have run"
        assert b"placeholder" not in resp.body

    def test_a_refused_value_returns_none_for_the_caller_to_fall_back(self, export):
        """The digits-only allow-list still decides; this must not render."""
        assert _html_response(export / "hosts" / "placeholder" / "index.html", ("../etc",)) is None


class TestTheMiddlewareDefers:
    async def test_a_json_response_gets_the_strict_fallback(self, client):
        resp = await client.get("/api/version")
        assert resp.headers["content-security-policy"] == _CSP.decode()

    async def test_only_one_csp_header_survives(self):
        """Browsers *intersect* multiple CSP headers rather than letting the
        last win. A second, nonce-less header would forbid every nonce the
        document just declared and the page would render with no script.
        """
        sent = []

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-security-policy", b"script-src 'nonce-abc'")],
                }
            )
            await send({"type": "http.response.body", "body": b""})

        async def capture(message):
            if message["type"] == "http.response.start":
                sent.extend(message["headers"])

        await SecurityHeadersMiddleware(app)({"type": "http", "path": "/dashboard/"}, None, capture)

        csp = [v for k, v in sent if k.lower() == b"content-security-policy"]
        assert csp == [b"script-src 'nonce-abc'"]

    async def test_a_response_without_one_gets_the_fallback(self):
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def capture(message):
            if message["type"] == "http.response.start":
                sent.extend(message["headers"])

        await SecurityHeadersMiddleware(app)({"type": "http", "path": "/api/hosts"}, None, capture)

        csp = [v for k, v in sent if k.lower() == b"content-security-policy"]
        assert csp == [_CSP]


class TestTheDocsException:
    def test_the_docs_paths_are_the_only_ones_allowed_inline_script(self):
        """FastAPI generates Swagger UI with inline bootstrap we do not
        control and cannot stamp. Off unless `server.expose_docs`."""
        assert _DOCS_PATHS == {"/docs", "/redoc", "/docs/oauth2-redirect"}

    async def test_docs_are_off_by_default(self, client):
        resp = await client.get("/docs")
        assert resp.status_code != 200


class TestTheSingleServingPath:
    def test_no_staticfiles_mount_exists(self):
        """The nonce approach depends on one function serving every HTML
        document. A ``StaticFiles`` mount added later would bypass it and
        hand HTML the strict fallback CSP — a page with no script at all.
        """
        import pathlib

        source = (pathlib.Path(__file__).parent.parent / "app" / "main.py").read_text()
        # Matches the import and the mount, not the prose above them — the
        # comment in `_serve` explains this constraint and says the name.
        offenders = [
            line.strip()
            for line in source.splitlines()
            if ("staticfiles" in line.lower() and "import" in line) or ".mount(" in line
        ]
        assert not offenders, (
            "a StaticFiles mount bypasses the nonce stamping in spa_fallback:\n  "
            + "\n  ".join(offenders)
        )


@pytest.fixture
def export(tmp_path):
    """A miniature static export: one plain page and one dynamic route."""
    (tmp_path / "index.html").write_text("<html><script>shell</script></html>", encoding="utf-8")
    dash = tmp_path / "dashboard"
    dash.mkdir()
    (dash / "index.html").write_text(
        '<html><script>self.__next_f.push([1,"a"])</script><script src="/x.js"></script></html>',
        encoding="utf-8",
    )
    hosts = tmp_path / "hosts" / "placeholder"
    hosts.mkdir(parents=True)
    (hosts / "index.html").write_text(
        '<html><script>self.__next_f.push([1,"{\\"id\\":\\"placeholder\\"}"])</script></html>',
        encoding="utf-8",
    )
    return tmp_path
