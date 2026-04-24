from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, LargeBinary, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PackAuthType(str, enum.Enum):
    """How LabDog authenticates to the pack's git remote.

    ``none`` is for public repos. ``ssh`` uses a stored private key with
    an accompanying (optional) per-pack known_hosts file. ``https_token``
    uses a Personal Access Token delivered via ``http.extraHeader`` so it
    never lands in ``remote.origin.url``.
    """

    NONE = "none"
    SSH = "ssh"
    HTTPS_TOKEN = "https_token"


class PackSourceType(str, enum.Enum):
    """Where the pack's manifests live.

    ``git`` — LabDog clones / fetches the configured remote into
    ``<packs_root>/<id>`` and loads manifests from the checkout.
    ``local`` — the pack is a pre-existing directory on disk (BYO
    playbooks); LabDog reads manifests from ``repo_url`` directly and
    never clones. Useful for ops who maintain playbooks outside a
    git workflow, or for local dev against a working copy.
    """

    GIT = "git"
    LOCAL = "local"


class PackRole(str, enum.Enum):
    """Semantic position of a pack in the override hierarchy.

    ``default`` — a canonical/baseline pack that downstream packs can
    override. Typically one per install (e.g. ``labdog-default``).
    ``override`` — customisations or extensions layered on top of the
    default. Multiple allowed.

    Role is only meaningful for git-backed packs. Local packs are
    always placed above both tiers (they're the fastest way to
    iterate on a playbook you're actively editing). The integer
    priority shown to the loader is derived from ``(source_type,
    role)`` — admins never see raw numbers.
    """

    DEFAULT = "default"
    OVERRIDE = "override"


class ActionPack(Base):
    """A git-backed action pack configured from the admin UI.

    Credentials (SSH private key, HTTPS token) are encrypted at rest with
    the same AES-256-GCM pipeline the ``ProxmoxNode`` table uses; handling
    happens in the API / service layers, never here.
    """

    __tablename__ = "action_packs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    source_type: Mapped[PackSourceType] = mapped_column(
        SAEnum(
            PackSourceType,
            name="packsourcetype",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=PackSourceType.GIT,
    )
    # For ``git`` source this is the clone URL; for ``local`` it's a
    # filesystem path (the UI labels it accordingly).
    repo_url: Mapped[str] = mapped_column(String(500))
    # Only meaningful when source_type == git; ignored for local.
    ref: Mapped[str] = mapped_column(String(200), default="main")
    # Role tiers the pack in the override hierarchy. Only applied for
    # git packs; local packs are always placed above all git tiers
    # regardless of this value.
    role: Mapped[PackRole] = mapped_column(
        SAEnum(
            PackRole,
            name="packrole",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=PackRole.OVERRIDE,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    auth_type: Mapped[PackAuthType] = mapped_column(
        SAEnum(
            PackAuthType,
            name="packauthtype",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=PackAuthType.NONE,
    )
    encrypted_ssh_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    ssh_known_hosts: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_token: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
