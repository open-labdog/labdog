"""Async query client for the Grafana-stack backends, using httpx.

Two consumers with different needs share it. The host page renders single
current values, which is what :meth:`PrometheusClient.query` is for. The
AI tools additionally ask "how did this change" and "what do the logs
say", which need the range APIs — :meth:`query_range` for Mimir and
:meth:`query_loki_range` for Loki.

Secrets (bearer token) are never logged. TLS handling mirrors
:class:`app.proxmox.client.ProxmoxClient`.
"""

from __future__ import annotations

import ssl
from datetime import datetime
from typing import Any

import httpx


class PrometheusError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class PrometheusClient:
    """Minimal async client for the Prometheus/Mimir HTTP query API.

    Args:
        query_url: Prometheus-compatible base URL, e.g.
            ``http://mimir:9009/prometheus``. ``/api/v1/query`` is appended.
        org_id: Optional ``X-Scope-OrgID`` (Mimir tenant) header value.
        token: Optional bearer token (sent as ``Authorization: Bearer``).
        verify_ssl / ca_cert_pem: same semantics as ProxmoxClient.
    """

    def __init__(
        self,
        query_url: str,
        org_id: str | None = None,
        token: str | None = None,
        verify_ssl: bool = True,
        ca_cert_pem: str | None = None,
        auth_type: str = "none",
        username: str | None = None,
    ) -> None:
        self.query_url = query_url.rstrip("/")
        self.org_id = org_id
        self.token = token  # bearer token, or basic-auth password
        self.verify_ssl = verify_ssl
        self.ca_cert_pem = ca_cert_pem
        self.auth_type = auth_type
        self.username = username
        self._ssl_context: ssl.SSLContext | None = None

    def _auth_header(self) -> str | None:
        if self.auth_type == "bearer" and self.token:
            return f"Bearer {self.token}"
        if self.auth_type == "basic" and self.username is not None:
            import base64

            raw = f"{self.username}:{self.token or ''}".encode()
            return f"Basic {base64.b64encode(raw).decode()}"
        return None

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            try:
                self._ssl_context = ssl.create_default_context(cadata=self.ca_cert_pem)
            except Exception as exc:
                raise PrometheusError(f"Invalid CA certificate: {exc}") from exc
        return self._ssl_context

    def _get_client(self) -> httpx.AsyncClient:
        # Mimir/Loki with multitenancy enabled (their default) reject any
        # request lacking X-Scope-OrgID with HTTP 401 ("no org id"). Default
        # the tenant to "anonymous" — the conventional single-tenant value,
        # and the same default the Alloy install action ships with — so an
        # unauthenticated, no-tenant backend works out of the box.
        headers: dict[str, str] = {"X-Scope-OrgID": self.org_id or "anonymous"}
        auth = self._auth_header()
        if auth:
            headers["Authorization"] = auth
        if not self.verify_ssl:
            verify: bool | ssl.SSLContext = False
        elif self.ca_cert_pem:
            verify = self._get_ssl_context()
        else:
            verify = True
        return httpx.AsyncClient(headers=headers, verify=verify, timeout=10.0)

    async def query(self, promql: str) -> list[dict[str, Any]]:
        """Run an instant query, returning the ``result`` array.

        Each element is ``{"metric": {...}, "value": [<ts>, "<val>"]}``.
        Raises :class:`PrometheusError` on transport/HTTP/API errors.
        """
        url = f"{self.query_url}/api/v1/query"
        try:
            async with self._get_client() as client:
                resp = await client.get(url, params={"query": promql})
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Request failed: {exc}") from exc
        if resp.status_code == 401 or resp.status_code == 403:
            raise PrometheusError("Authentication failed", resp.status_code)
        if resp.status_code >= 400:
            raise PrometheusError(f"Query API returned HTTP {resp.status_code}", resp.status_code)
        try:
            body = resp.json()
        except Exception as exc:
            raise PrometheusError(f"Invalid JSON from query API: {exc}") from exc
        if body.get("status") != "success":
            raise PrometheusError(str(body.get("error", "query failed")))
        return body.get("data", {}).get("result", [])

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET a JSON endpoint under ``query_url`` with the usual error mapping."""
        url = f"{self.query_url}{path}"
        try:
            async with self._get_client() as client:
                resp = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise PrometheusError("Authentication failed", resp.status_code)
        if resp.status_code >= 400:
            raise PrometheusError(f"Query API returned HTTP {resp.status_code}", resp.status_code)
        try:
            body = resp.json()
        except Exception as exc:
            raise PrometheusError(f"Invalid JSON from query API: {exc}") from exc
        if body.get("status") not in (None, "success"):
            raise PrometheusError(str(body.get("error", "query failed")))
        return body

    async def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "60s",
    ) -> list[dict[str, Any]]:
        """Run a range query, returning the ``result`` array.

        Each element is ``{"metric": {...}, "values": [[<ts>, "<val>"], ...]}``.
        Used for "how did this change over time" questions, which an instant
        query cannot answer.
        """
        body = await self._get_json(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step,
            },
        )
        return body.get("data", {}).get("result", [])

    async def query_loki_range(
        self,
        logql: str,
        start: datetime,
        end: datetime,
        limit: int = 100,
        direction: str = "backward",
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run a LogQL query against Loki.

        Returns ``(result_type, result)``. Loki answers two shapes from the
        same endpoint and the caller has to branch on which it got:

        * ``streams`` — a log-selector query. ``result`` entries carry
          ``stream`` labels and a ``values`` list of ``[ns_timestamp, line]``.
        * ``matrix`` — a metric query (``count_over_time``, ``rate``, …).
          ``result`` looks like Prometheus range output.

        The distinction matters for cost as much as parsing: a ``streams``
        query ships raw log lines, while a ``matrix`` query has Loki do the
        counting server-side and returns a handful of numbers.

        ``direction`` defaults to ``backward`` so that a truncated result is
        the *most recent* ``limit`` lines rather than the oldest, which is
        almost always what an investigation wants.
        """
        body = await self._get_json(
            "/api/v1/query_range",
            {
                "query": logql,
                "start": int(start.timestamp() * 1_000_000_000),
                "end": int(end.timestamp() * 1_000_000_000),
                "limit": limit,
                "direction": direction,
            },
        )
        data = body.get("data", {})
        return data.get("resultType", "streams"), data.get("result", [])

    async def get_ok(self, path: str) -> None:
        """GET ``{query_url}{path}`` and raise :class:`PrometheusError` unless
        the response is 2xx. Used by connectivity tests (e.g. Loki labels)."""
        url = f"{self.query_url}{path}"
        try:
            async with self._get_client() as client:
                resp = await client.get(url)
        except httpx.HTTPError as exc:
            raise PrometheusError(f"Request failed: {exc}") from exc
        if resp.status_code in (401, 403):
            raise PrometheusError("Authentication failed", resp.status_code)
        if resp.status_code >= 400:
            raise PrometheusError(f"HTTP {resp.status_code}", resp.status_code)

    async def query_scalar(self, promql: str) -> tuple[float, float] | None:
        """Run ``query`` and return ``(value, timestamp)`` of the first
        result, or ``None`` when the query returned no series."""
        result = await self.query(promql)
        if not result:
            return None
        ts, val = result[0]["value"]
        try:
            return float(val), float(ts)
        except (TypeError, ValueError):
            return None
