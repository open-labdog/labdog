"""Async Proxmox API client using httpx.

All I/O is performed with httpx.AsyncClient. Secrets are never logged.
"""

import asyncio
import ssl
from typing import Any

import httpx


class ProxmoxError(Exception):
    """Raised when a Proxmox API request fails.

    Attributes:
        message: Human-readable error description.
        status_code: HTTP status code, if available.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code

    def __str__(self) -> str:
        return self.message


class ProxmoxClient:
    """Async client for the Proxmox VE REST API.

    Usage::

        async with ProxmoxClient(url, token_id, token_secret) as client:
            nodes = await client.list_nodes()

    Args:
        api_url: Base URL of the Proxmox API, e.g. ``https://pve:8006``.
        token_id: API token identifier in ``user@realm!tokenname`` form.
        token_secret: Secret UUID for the API token.
        verify_ssl: Whether to verify TLS certificates (default ``True``).
        ca_cert_pem: Optional PEM-encoded CA certificate(s) to trust when
            verifying the node's TLS certificate. When ``verify_ssl`` is
            ``True`` and this is set, an in-memory :class:`ssl.SSLContext`
            built from the PEM is used for verification instead of the
            system trust store. Ignored when ``verify_ssl`` is ``False``.
    """

    def __init__(
        self,
        api_url: str,
        token_id: str,
        token_secret: str,
        verify_ssl: bool = True,
        ca_cert_pem: str | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.token_id = token_id
        self.token_secret = token_secret
        self.verify_ssl = verify_ssl
        self.ca_cert_pem = ca_cert_pem
        self._ssl_context: ssl.SSLContext | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_ssl_context(self) -> ssl.SSLContext:
        """Lazily build and cache an :class:`ssl.SSLContext` from the CA PEM.

        The context trusts only the uploaded CA certificate(s); hostname
        checking remains enabled (the node cert's CN/SAN must match the
        ``api_url`` host). Malformed PEM — e.g. a manually-edited DB row that
        bypassed API validation — surfaces as :class:`ProxmoxError` rather
        than a raw :class:`ssl.SSLError`.
        """
        if self._ssl_context is None:
            try:
                self._ssl_context = ssl.create_default_context(cadata=self.ca_cert_pem)
            except Exception as exc:
                raise ProxmoxError(f"Invalid CA certificate: {exc}") from exc
        return self._ssl_context

    def _get_client(self) -> httpx.AsyncClient:
        """Return a configured :class:`httpx.AsyncClient`.

        The Authorization header uses the Proxmox API token scheme.
        ``token_secret`` is embedded in the header value and never logged.

        ``verify`` is selected per the BUG-52 matrix:

        * ``verify_ssl=False`` → ``verify=False`` (no verification; CA ignored).
        * ``verify_ssl=True`` and ``ca_cert_pem`` set → an
          :class:`ssl.SSLContext` built from the PEM.
        * ``verify_ssl=True`` and ``ca_cert_pem`` unset → ``verify=True``
          (system trust store).
        """
        headers = {
            "Authorization": f"PVEAPIToken={self.token_id}={self.token_secret}",
        }
        if not self.verify_ssl:
            verify: bool | ssl.SSLContext = False
        elif self.ca_cert_pem:
            verify = self._get_ssl_context()
        else:
            verify = True
        return httpx.AsyncClient(headers=headers, verify=verify)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute an HTTP request and return the ``data`` field of the response.

        Args:
            method: HTTP method (``GET``, ``POST``, ``DELETE``, …).
            path: API path, e.g. ``/api2/json/nodes``.
            **kwargs: Additional keyword arguments forwarded to
                :meth:`httpx.AsyncClient.request`.

        Returns:
            The value of ``response.json()["data"]``.

        Raises:
            ProxmoxError: If the response status code is not 2xx.
        """
        url = f"{self.api_url}{path}"
        async with self._get_client() as client:
            response = await client.request(method, url, **kwargs)

        if not response.is_success:
            raise ProxmoxError(response.text, response.status_code)

        return response.json()["data"]

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "ProxmoxClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def test_connection(self) -> dict:
        """Return Proxmox version information.

        GET /api2/json/version
        """
        return await self._request("GET", "/api2/json/version")

    async def list_nodes(self) -> list[dict]:
        """List all nodes in the cluster.

        GET /api2/json/nodes
        """
        return await self._request("GET", "/api2/json/nodes")

    async def list_vms(self, pve_node: str) -> list[dict]:
        """List all QEMU VMs on a node.

        Args:
            pve_node: Proxmox node name.

        GET /api2/json/nodes/{pve_node}/qemu
        """
        return await self._request("GET", f"/api2/json/nodes/{pve_node}/qemu")

    async def list_containers(self, pve_node: str) -> list[dict]:
        """List all LXC containers on a node.

        Args:
            pve_node: Proxmox node name.

        GET /api2/json/nodes/{pve_node}/lxc
        """
        return await self._request("GET", f"/api2/json/nodes/{pve_node}/lxc")

    async def get_vm_agent_interfaces(self, pve_node: str, vmid: int) -> list[dict]:
        """Return network interfaces reported by the QEMU guest agent.

        Args:
            pve_node: Proxmox node name.
            vmid: VM identifier.

        GET /api2/json/nodes/{pve_node}/qemu/{vmid}/agent/network-get-interfaces

        The Proxmox API wraps the result in ``{"data": {"result": [...]}}``.
        This method unwraps the ``result`` key if present.

        Raises:
            ProxmoxError: If the agent is not running (status 500) or any
                other API error occurs.
        """
        try:
            data = await self._request(
                "GET",
                f"/api2/json/nodes/{pve_node}/qemu/{vmid}/agent/network-get-interfaces",
            )
            # Proxmox wraps guest-agent responses in {"result": [...]}
            if isinstance(data, dict) and "result" in data:
                return data["result"]
            return data
        except ProxmoxError as exc:
            if exc.status_code == 500:
                raise ProxmoxError(f"Agent not responding on vmid {vmid}", 500) from exc
            raise

    async def get_container_interfaces(self, pve_node: str, vmid: int) -> list[dict]:
        """Return network interfaces for an LXC container.

        Args:
            pve_node: Proxmox node name.
            vmid: Container identifier.

        GET /api2/json/nodes/{pve_node}/lxc/{vmid}/interfaces

        Raises:
            ProxmoxError: If the container is stopped or the API fails.
        """
        return await self._request(
            "GET",
            f"/api2/json/nodes/{pve_node}/lxc/{vmid}/interfaces",
        )

    async def create_snapshot(
        self,
        pve_node: str,
        vmid: int,
        name: str,
        description: str = "",
        *,
        vm_type: str = "qemu",
    ) -> str:
        """Create a VM/container snapshot and return the task UPID.

        Args:
            pve_node: Proxmox node name.
            vmid: VM/container identifier.
            name: Snapshot name.
            description: Optional human-readable description.
            vm_type: ``"qemu"`` for VMs, ``"lxc"`` for containers.

        POST /api2/json/nodes/{pve_node}/{vm_type}/{vmid}/snapshot
        """
        guest = "lxc" if vm_type == "lxc" else "qemu"
        return await self._request(
            "POST",
            f"/api2/json/nodes/{pve_node}/{guest}/{vmid}/snapshot",
            json={"snapname": name, "description": description},
        )

    async def delete_snapshot(
        self, pve_node: str, vmid: int, name: str, *, vm_type: str = "qemu"
    ) -> str:
        """Delete a VM/container snapshot and return the task UPID.

        Args:
            pve_node: Proxmox node name.
            vmid: VM/container identifier.
            name: Snapshot name to delete.
            vm_type: ``"qemu"`` for VMs, ``"lxc"`` for containers.

        DELETE /api2/json/nodes/{pve_node}/{vm_type}/{vmid}/snapshot/{name}
        """
        guest = "lxc" if vm_type == "lxc" else "qemu"
        return await self._request(
            "DELETE",
            f"/api2/json/nodes/{pve_node}/{guest}/{vmid}/snapshot/{name}",
        )

    async def rollback_snapshot(
        self, pve_node: str, vmid: int, name: str, *, vm_type: str = "qemu"
    ) -> str:
        """Roll a VM/container back to a snapshot and return the task UPID.

        Args:
            pve_node: Proxmox node name.
            vmid: VM/container identifier.
            name: Snapshot name to roll back to.
            vm_type: ``"qemu"`` for VMs, ``"lxc"`` for containers.

        POST /api2/json/nodes/{pve_node}/{vm_type}/{vmid}/snapshot/{name}/rollback
        """
        guest = "lxc" if vm_type == "lxc" else "qemu"
        return await self._request(
            "POST",
            f"/api2/json/nodes/{pve_node}/{guest}/{vmid}/snapshot/{name}/rollback",
        )

    async def get_task_status(self, pve_node: str, upid: str) -> dict:
        """Return the current status of a Proxmox task.

        Args:
            pve_node: Proxmox node name.
            upid: Unique task identifier string.

        GET /api2/json/nodes/{pve_node}/tasks/{upid}/status
        """
        return await self._request(
            "GET",
            f"/api2/json/nodes/{pve_node}/tasks/{upid}/status",
        )

    async def wait_for_task(
        self,
        pve_node: str,
        upid: str,
        timeout: int = 120,
        poll_interval: int = 2,
    ) -> None:
        """Poll a task until it stops or the timeout is reached.

        Args:
            pve_node: Proxmox node name.
            upid: Unique task identifier string.
            timeout: Maximum seconds to wait before raising.
            poll_interval: Seconds between status polls.

        Raises:
            ProxmoxError: If the task exits with a non-OK status or the
                timeout is exceeded.
        """
        elapsed = 0
        while elapsed < timeout:
            data = await self.get_task_status(pve_node, upid)
            if data.get("status") == "stopped":
                exitstatus = data.get("exitstatus", "")
                if exitstatus != "OK":
                    raise ProxmoxError(f"Task failed: {exitstatus}")
                return
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise ProxmoxError(f"Task timed out after {timeout}s")

    async def get_vm_status(self, pve_node: str, vmid: int, *, vm_type: str = "qemu") -> dict:
        """Return the current runtime status of a VM or container.

        Args:
            pve_node: Proxmox node name.
            vmid: VM/container identifier.
            vm_type: ``"qemu"`` for VMs, ``"lxc"`` for containers.

        GET /api2/json/nodes/{pve_node}/{vm_type}/{vmid}/status/current
        """
        guest = "lxc" if vm_type == "lxc" else "qemu"
        return await self._request(
            "GET",
            f"/api2/json/nodes/{pve_node}/{guest}/{vmid}/status/current",
        )

    async def start_vm(self, pve_node: str, vmid: int, *, vm_type: str = "qemu") -> str:
        """Start a VM or container and return the task UPID.

        Args:
            pve_node: Proxmox node name.
            vmid: VM/container identifier.
            vm_type: ``"qemu"`` for VMs, ``"lxc"`` for containers.

        POST /api2/json/nodes/{pve_node}/{vm_type}/{vmid}/status/start
        """
        guest = "lxc" if vm_type == "lxc" else "qemu"
        return await self._request(
            "POST",
            f"/api2/json/nodes/{pve_node}/{guest}/{vmid}/status/start",
        )
