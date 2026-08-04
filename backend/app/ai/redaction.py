"""Strip secrets from command output before it reaches an LLM.

The host inventory LabDog manages holds no plaintext secrets, but command
*output* routinely does — ``cat`` a config file, dump an environment,
read a unit file. Everything a tool returns passes through
:func:`redact` on its way into the transcript, so a secret is neither
sent to the provider nor persisted in ``ai_messages``.

This is a safety net, not a guarantee: patterns catch the common shapes,
and the tool layer's real defence is that a read-only session is the
default and the target-host allowlist bounds what can be read at all.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[redacted by labdog]"

# Ordered most- to least-specific: the first match wins for a given span.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private keys, including the whole body.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        f"-----BEGIN PRIVATE KEY-----\n{PLACEHOLDER}\n-----END PRIVATE KEY-----",
    ),
    # OpenSSH authorized_keys / known_hosts private material and certs.
    (re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----.*?-----END OPENSSH PRIVATE KEY-----",
                re.DOTALL),
     PLACEHOLDER),
    # Authorization headers of any scheme. Must precede the generic
    # KEY: VALUE rule below, which would otherwise match on "Authorization"
    # and redact only the scheme word, leaving the credential itself.
    (
        re.compile(r"(?i)\b(authorization\s*:\s*)(bearer|basic|token|digest)\s+\S+"),
        rf"\1\2 {PLACEHOLDER}",
    ),
    # KEY=VALUE and KEY: VALUE where the key names a secret.
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|api[_-]?key|"
            r"access[_-]?key|private[_-]?key|credential|auth)[A-Z0-9_]*)"
            r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
        ),
        rf"\1\2{PLACEHOLDER}",
    ),
    # Bare JWTs.
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
        PLACEHOLDER,
    ),
    # Well-known token prefixes (GitHub, Slack, Stripe, OpenAI, Anthropic).
    (
        re.compile(
            r"\b(gh[pousr]_[A-Za-z0-9]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|"
            r"sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|"
            r"AKIA[0-9A-Z]{16}|glpat-[A-Za-z0-9_-]{16,})"
        ),
        PLACEHOLDER,
    ),
    # Credentials embedded in a URL.
    (
        re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@"),
        rf"\1\2:{PLACEHOLDER}@",
    ),
    # /etc/shadow-style hashes.
    (
        re.compile(r"(?m)^([^:\s]+):(\$[0-9a-z]\$[^:\s]+)"),
        rf"\1:{PLACEHOLDER}",
    ),
)

# A long unbroken base64-ish run is almost always key material rather than
# something the model needs. The bound is high enough that hashes,
# fingerprints, and UUIDs pass through intact.
_LONG_BLOB = re.compile(r"\b[A-Za-z0-9+/]{120,}={0,2}\b")


def redact(text: str) -> str:
    """Replace credential-shaped spans in ``text`` with a placeholder."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _LONG_BLOB.sub(PLACEHOLDER, text)


def redact_mapping(data: dict[str, object]) -> dict[str, object]:
    """Redact every string value in a flat mapping (e.g. tool arguments)."""
    return {
        key: redact(value) if isinstance(value, str) else value for key, value in data.items()
    }
