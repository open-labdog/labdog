"""Async Prometheus-compatible (Mimir) query client using httpx.

Only the instant-query API is needed — LabDog renders single current
values, never time series. Secrets (bearer token) are never logged.
TLS handling mirrors :class:`app.proxmox.client.ProxmoxClient`.
"""

from __future__ import annotations

import ssl
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
    ) -> None:
        self.query_url = query_url.rstrip("/")
        self.org_id = org_id
        self.token = token
        self.verify_ssl = verify_ssl
        self.ca_cert_pem = ca_cert_pem
        self._ssl_context: ssl.SSLContext | None = None

    def _get_ssl_context(self) -> ssl.SSLContext:
        if self._ssl_context is None:
            try:
                self._ssl_context = ssl.create_default_context(cadata=self.ca_cert_pem)
            except Exception as exc:
                raise PrometheusError(f"Invalid CA certificate: {exc}") from exc
        return self._ssl_context

    def _get_client(self) -> httpx.AsyncClient:
        headers: dict[str, str] = {}
        if self.org_id:
            headers["X-Scope-OrgID"] = self.org_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
