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
#
# These are compared against a URL's host, never bound to — the unspecified
# address is here because a provider URL may legitimately carry it, and a
# request to it goes to the local machine. nosec silences bandit's B104,
# which cannot tell a comparison from a bind.
_LOCAL_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",  # nosec B104 - compared against, not bound
}


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


#: Backends driven by the Claude Agent SDK rather than by
#: :class:`~app.ai.loop.AgentLoop`.
#:
#: These are deliberately absent from :data:`_PROVIDER_CLASSES`: the SDK
#: owns the agent loop, so there is no single turn for them to stream and
#: nothing that could implement ``LLMProvider``. Capability questions still
#: have to be answerable, which is what this set is for. Named here rather
#: than imported from ``app.ai.agent_sdk`` so that asking "can this
#: provider run tools?" does not drag the optional SDK — or the tool
#: registry — into a process that only wanted to render a settings page.
SDK_BACKED_TYPES = frozenset({"claude_agent"})


def runs_on_agent_sdk(provider: AIProvider) -> bool:
    """Whether this provider's sessions are driven by the Agent SDK."""
    return provider.provider_type in SDK_BACKED_TYPES


def sends_data_offsite(provider: AIProvider) -> bool:
    """True when using this provider transmits host data off the network."""
    if provider.provider_type == "claude_cli" or runs_on_agent_sdk(provider):
        # Claude Code runs locally, but it is an authenticated client that
        # talks to Anthropic — the data still leaves.
        return True
    return not is_local_endpoint(provider.base_url)


#: Backend class per stored ``provider_type``. Keeps capability questions
#: answerable without decrypting a credential just to read a class
#: attribute, and keeps the classes the single source of truth for them.
_PROVIDER_CLASSES: dict[str, type] = {
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    "claude_cli": ClaudeCLIProvider,
}


def supports_tools(provider: AIProvider) -> bool:
    """Whether this backend can execute tool calls.

    A backend that cannot is unusable for investigation: it has no way to
    look anything up, so anything it says about a host is invention. An
    unknown type is treated as incapable, which fails toward refusing a
    session rather than running one whose output cannot be trusted.
    """
    if runs_on_agent_sdk(provider):
        # The SDK runs LabDog's tools as an in-process MCP server. This is
        # the capability the single-shot CLI backend never had.
        return True
    cls = _PROVIDER_CLASSES.get(provider.provider_type)
    return bool(cls is not None and getattr(cls, "supports_tools", False))


def build_provider(provider: AIProvider) -> LLMProvider:
    """Instantiate the backend described by a stored provider row.

    Raises for SDK-backed types. They have no per-turn interface to
    return, and reaching here with one means a caller routed a session to
    ``AgentLoop`` that :func:`runs_on_agent_sdk` should have sent to
    ``AgentSDKRunner``.
    """
    if runs_on_agent_sdk(provider):
        raise LLMProviderError(
            f"Provider {provider.name!r} is driven by the Claude Agent SDK, which owns "
            "the agent loop itself. Sessions for it run through AgentSDKRunner, not "
            "AgentLoop; this call is a routing bug."
        )

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
        # The encrypted secret column carries a subscription OAuth token here
        # (from `claude setup-token`) rather than an API key, so it reuses the
        # same AES-256-GCM storage, redaction, and key rotation as every other
        # LabDog credential. Blank means "use whatever the host is already
        # authenticated with".
        return ClaudeCLIProvider(model=provider.model or None, oauth_token=api_key)

    raise LLMProviderError(f"Unknown provider type {provider.provider_type!r}")
