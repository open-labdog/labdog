"""Static-export serving: dynamic-route resolution and the placeholder rewrite.

The rewrite exists because Next's static export bakes the literal string
"placeholder" into the RSC flight data of every ``generateStaticParams``
route, so ``/hosts/7/`` is served from ``hosts/placeholder/index.html`` with
the id patched back in at request time.

That patch interpolated a raw, percent-decoded URL segment into a
``<script>`` block. ``GET /hosts/x"</script><script>alert(1)</script>/``
therefore closed the flight-data string and the script tag, and executed —
reflected XSS on every dynamic route. It mattered more than a typical
reflected XSS here: the CSP allows ``script-src 'unsafe-inline'``, and the
CSRF cookie is deliberately JavaScript-readable, so injected script could
drive any authenticated mutation same-origin.

These tests do not need a database — they exercise the pure resolver and
rewrite helpers directly.
"""

import pytest

from app.main import _resolve_dynamic_route, _rewrite_placeholder

# A miniature stand-in for what `next build` emits: the route param appears
# once plain (as in the .txt flight payload) and once backslash-escaped (as
# in the HTML), and a same-named *prop key* is present to prove the negative
# lookahead still protects it.
_FLIGHT_HTML = (
    "<!DOCTYPE html><html><body><script>self.__next_f.push("
    '[1,"{\\"params\\":{\\"id\\":\\"placeholder\\"},'
    '\\"form\\":{\\"placeholder\\":\\"Search hosts\\"}}"])</script>'
    '<script>window.__DATA={"id":"placeholder","placeholder":"keep me"}</script>'
    "</body></html>"
)


@pytest.fixture
def export_dir(tmp_path):
    """A static export with one dynamic route and one static route."""
    (tmp_path / "index.html").write_text("<html>shell</html>", encoding="utf-8")
    hosts = tmp_path / "hosts"
    (hosts / "placeholder").mkdir(parents=True)
    (hosts / "placeholder" / "index.html").write_text(_FLIGHT_HTML, encoding="utf-8")
    (hosts / "new").mkdir()
    (hosts / "new" / "index.html").write_text("<html>new host</html>", encoding="utf-8")
    return tmp_path


class TestLegitimateRoutes:
    def test_integer_id_resolves_to_the_placeholder_export(self, export_dir):
        result = _resolve_dynamic_route(export_dir, "hosts/7")
        assert result is not None
        resolved, value = result
        assert resolved == export_dir / "hosts" / "placeholder" / "index.html"
        assert value == "7"

    def test_the_id_is_patched_into_the_flight_data(self, export_dir):
        target = export_dir / "hosts" / "placeholder" / "index.html"
        content = _rewrite_placeholder(str(target), target.stat().st_mtime_ns, "7")
        assert content is not None
        assert '\\"id\\":\\"7\\"' in content
        assert '"id":"7"' in content
        assert "placeholder" not in content.replace('\\"placeholder\\":', "").replace(
            '"placeholder":', ""
        )

    def test_a_prop_key_named_placeholder_is_left_alone(self, export_dir):
        """The negative lookahead must not rewrite `{"placeholder": ...}`."""
        target = export_dir / "hosts" / "placeholder" / "index.html"
        content = _rewrite_placeholder(str(target), target.stat().st_mtime_ns, "7")
        assert '\\"placeholder\\":\\"Search hosts\\"' in content
        assert '"placeholder":"keep me"' in content

    def test_a_real_directory_still_wins_over_the_placeholder(self, export_dir):
        """/hosts/new/ is a static route and must not be treated as an id."""
        result = _resolve_dynamic_route(export_dir, "hosts/new")
        assert result is not None
        resolved, value = result
        assert resolved == export_dir / "hosts" / "new" / "index.html"
        assert value is None


class TestInjectionIsRefused:
    """The segment is an allow-list, so these fall through to the SPA shell."""

    @pytest.mark.parametrize(
        "segment",
        [
            # The original proof of concept: close the string, close the
            # script tag, open a new one.
            'x"</script><script>alert(1)</script>',
            '7"</script><script>alert(1)</script>',
            # Break out of the flight-data string only.
            '7\\","evil":"',
            # A lone backslash would previously be re-emitted into the
            # replacement and could escape the closing quote.
            "7\\",
            # Non-numeric ids of any shape are simply not routes this app
            # produces.
            "abc",
            "7abc",
            "../etc/passwd",
            "<img src=x onerror=alert(1)>",
            "",
            # Longer than int64.
            "1" * 20,
        ],
    )
    def test_resolver_refuses_the_segment(self, export_dir, segment):
        assert _resolve_dynamic_route(export_dir, f"hosts/{segment}") is None

    @pytest.mark.parametrize(
        "segment",
        ['x"</script><script>alert(1)</script>', "7\\", "abc", "1" * 20],
    )
    def test_rewrite_refuses_the_segment_independently(self, export_dir, segment):
        """The rewrite re-validates rather than trusting its caller, so the
        two guards fail independently."""
        target = export_dir / "hosts" / "placeholder" / "index.html"
        assert _rewrite_placeholder(str(target), target.stat().st_mtime_ns, segment) is None

    def test_no_script_tag_can_be_injected_end_to_end(self, export_dir):
        """The property that actually matters, stated directly."""
        payload = 'x"</script><script>alert(1)</script>'
        assert _resolve_dynamic_route(export_dir, f"hosts/{payload}") is None

        target = export_dir / "hosts" / "placeholder" / "index.html"
        rendered = _rewrite_placeholder(str(target), target.stat().st_mtime_ns, payload)
        assert rendered is None or "<script>alert(1)</script>" not in rendered


class TestCaching:
    def test_a_redeployed_export_is_not_served_stale(self, export_dir):
        """The cache key includes mtime, so rewriting a changed file re-reads."""
        target = export_dir / "hosts" / "placeholder" / "index.html"
        first = _rewrite_placeholder(str(target), target.stat().st_mtime_ns, "7")
        assert '\\"id\\":\\"7\\"' in first

        target.write_text(_FLIGHT_HTML.replace("body", "main"), encoding="utf-8")
        second = _rewrite_placeholder(str(target), target.stat().st_mtime_ns, "7")
        assert "<main>" in second

    def test_repeated_requests_hit_the_cache(self, export_dir):
        target = export_dir / "hosts" / "placeholder" / "index.html"
        mtime = target.stat().st_mtime_ns
        before = _rewrite_placeholder.cache_info().hits
        _rewrite_placeholder(str(target), mtime, "12345")
        _rewrite_placeholder(str(target), mtime, "12345")
        assert _rewrite_placeholder.cache_info().hits > before


class TestTheAllowListIsTheSecurityBoundary:
    """Pins the two design decisions the fix rests on.

    Both live in docstrings today. A docstring does not fail a build, and
    each of these is the kind of change that looks like a small
    improvement while removing the property the fix exists for.
    """

    def test_the_permitted_segment_is_digits_only(self):
        """Deliberately strict: every dynamic route in the app router is an
        integer primary key ([id] / [runId]), so nothing else needs to
        resolve server-side.

        This is a tripwire, not a behaviour test. If a slug-shaped route is
        added and someone widens the pattern to admit letters or hyphens,
        this fails — and the docstring on ``_rewrite_placeholder`` explains
        why widening it *also* requires adding context-correct escaping,
        because the placeholder sits in two different quoting contexts that
        need different escapes.
        """
        from app.main import _SAFE_DYNAMIC_SEGMENT

        assert _SAFE_DYNAMIC_SEGMENT.pattern == r"[0-9]{1,19}"

        for allowed in ("1", "42", "9" * 19):
            assert _SAFE_DYNAMIC_SEGMENT.fullmatch(allowed)
        for refused in ("a", "1a", "-1", "1.0", "1 2", "9" * 20, ""):
            assert not _SAFE_DYNAMIC_SEGMENT.fullmatch(refused)

    def test_the_rewrite_refuses_rather_than_escaping(self, export_dir):
        """The chosen failure mode is "serve the SPA shell", not "escape
        and serve anyway".

        Escaping was considered and rejected: the placeholder appears
        plain in the .txt payloads and backslash-escaped in the HTML, so a
        single escape helper would be wrong in one of them. Returning None
        keeps one code path instead of two subtly different ones.
        """
        target = export_dir / "hosts" / "placeholder" / "index.html"
        assert _rewrite_placeholder(str(target), target.stat().st_mtime_ns, 'x"') is None
