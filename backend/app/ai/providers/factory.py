"""Build a live provider from a stored :class:`app.ai.models.AIProvider` row.

This is the only place the API key is decrypted, mirroring how the
Grafana and Proxmox integrations keep crypto in the API/task layer rather
than in the model.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from app.ai.models import AIProvider
from app.ai.providers.anthropic import AnthropicProvider
from app.ai.providers.base import LLMProvider, LLMProviderError
from app.ai.providers.claude_cli import ClaudeCLIProvider
from app.ai.providers.openai_compat import OpenAICompatProvider
from app.crypto import decrypt_ssh_key, get_master_key

logger = logging.getLogger(__name__)

# Hosts that never leave the operator's own network. Anything else is
# treated as off-site for the purposes of the egress policy.
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def decrypt_api_key(provider: AIProvider) -> str | None:
    """Decrypt the stored API key.

    No AAD, matching every other encrypted column in LabDog. This is
    deliberate rather than an oversight: ``scripts/rotate_encryption_key.py``
    re-encrypts blobs without a context id, so a column that bound its
    ciphertext to a row id would survive normal use and then fail to
    decrypt after the first key rotation.
    """
    if not provider.encrypted_api_key:
        return None
    return decrypt_ssh_key(provider.encrypted_api_key, get_master_key())


def is_local_endpoint(base_url: str | None) -> bool:
    """Whether requests to this URL stay on the operator's own network.

    Used by the egress policy: with cloud providers disallowed, only
    local endpoints (and the local CLI) may run. Errs toward calling an
    endpoint remote — an unparseable or missing host is not proof of
    locality.
    """
    if not base_url:
        return False
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES or host.endswith(".local") or host.endswith(".lan"):
        return True
    # RFC1918 and the link-local / CGNAT ranges a homelab actually uses.
    if host.startswith(("10.", "192.168.", "169.254.")):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) > 1 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    return False


def sends_data_offsite(provider: AIProvider) -> bool:
    """True when using this provider transmits host data off the network."""
    if provider.provider_type == "claude_cli":
        # The CLI runs locally, but it is an authenticated client that talks
        # to Anthropic — the data still leaves.
        return True
    return not is_local_endpoint(provider.base_url)


def build_provider(provider: AIProvider) -> LLMProvider:
    """Instantiate the backend described by a stored provider row."""
    api_key = decrypt_api_key(provider)

    if provider.provider_type == "openai_compat":
        if not provider.base_url:
            raise LLMProviderError(
                f"Provider {provider.name!r} has no base URL; an OpenAI-compatible "
                "endpoint needs one (e.g. http://localhost:11434/v1)"
            )
        return OpenAICompatProvider(
            base_url=provider.base_url,
            model=provider.model,
            api_key=api_key,
            verify_ssl=provider.verify_ssl,
            ca_cert_pem=provider.ca_cert_pem,
        )

    if provider.provider_type == "anthropic":
        return AnthropicProvider(
            model=provider.model,
            api_key=api_key,
            base_url=provider.base_url,
            verify_ssl=provider.verify_ssl,
            ca_cert_pem=provider.ca_cert_pem,
        )

    if provider.provider_type == "claude_cli":
        return ClaudeCLIProvider(model=provider.model or None)

    raise LLMProviderError(f"Unknown provider type {provider.provider_type!r}")
