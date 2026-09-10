"""Every response carries the security headers, and the CSP says what it means.

Nothing asserted these before, which is how ``x-xss-protection`` survived
long past its deprecation and how four CSP directives that do *not* inherit
from ``default-src`` stayed unset. A header set is a contract with the
browser; this file is where it is written down.
"""

import pytest

from app.main import _CSP


def _directives(csp: str) -> dict[str, str]:
    """``"a 'self'; b 'none'"`` -> ``{"a": "'self'", "b": "'none'"}``."""
    out = {}
    for part in csp.split(";"):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(" ")
        out[name] = value.strip()
    return out


class TestTheCspItself:
    """Parsed from the constant, so these hold without a request."""

    def test_it_parses_and_has_no_duplicate_directives(self):
        text = _CSP.decode()
        names = [p.strip().split(" ")[0] for p in text.split(";") if p.strip()]
        assert len(names) == len(set(names)), f"duplicate directive in {text}"

    @pytest.mark.parametrize(
        ("directive", "value"),
        [
            # The four that do not fall back to default-src. Without them
            # they are unrestricted, not inherited.
            ("object-src", "'none'"),
            ("base-uri", "'self'"),
            ("form-action", "'self'"),
            ("frame-ancestors", "'none'"),
            # The pre-existing floor, asserted so a future edit cannot
            # widen it by accident.
            ("default-src", "'self'"),
        ],
    )
    def test_directive(self, directive, value):
        assert _directives(_CSP.decode())[directive] == value

    def test_base_uri_is_present_because_it_is_the_load_bearing_one(self):
        """An injected <base> retargets every relative script URL on the page.

        Called out on its own because it is the directive whose absence
        turns a same-origin ``script-src`` into a loader for someone
        else's host, and it is the least obvious of the four.
        """
        assert "base-uri" in _directives(_CSP.decode())

    def test_script_src_still_allows_inline_and_that_is_known(self):
        """Not an oversight — see the comment on ``_CSP`` and TODO.md.

        Next's static export inlines the RSC flight data as ``<script>``
        blocks. This test exists so that removing 'unsafe-inline' is a
        deliberate act that updates a test, rather than something a
        future reader assumes was never tried.
        """
        assert "'unsafe-inline'" in _directives(_CSP.decode())["script-src"]


class TestTheHeadersOnARealResponse:
    async def test_csp_is_sent(self, client):
        resp = await client.get("/health")
        assert resp.headers["content-security-policy"] == _CSP.decode()

    async def test_xss_protection_is_gone(self, client):
        """Deprecated, ignored by current browsers, and on some old ones the
        filter it enables is itself an XSS vector."""
        resp = await client.get("/health")
        assert "x-xss-protection" not in resp.headers

    @pytest.mark.parametrize(
        ("header", "value"),
        [
            ("x-content-type-options", "nosniff"),
            # Redundant with frame-ancestors for any CSP-aware browser,
            # kept for proxies and older clients that read only this.
            ("x-frame-options", "DENY"),
            ("referrer-policy", "strict-origin-when-cross-origin"),
            ("permissions-policy", "camera=(), microphone=(), geolocation=(), payment=()"),
        ],
    )
    async def test_header(self, client, header, value):
        resp = await client.get("/health")
        assert resp.headers[header] == value

    async def test_headers_ride_on_error_responses_too(self, client):
        """A 404 is still a document a browser will parse."""
        resp = await client.get("/api/hosts/999999999")
        assert resp.status_code >= 400
        assert resp.headers["content-security-policy"] == _CSP.decode()
