from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PackSourceType(enum.StrEnum):
    """Where the pack's manifests live.

    ``git`` — the pack references a configured ``GitRepository``. LabDog
    clones that repo and reads the pack from a subpath within it.
    ``local`` — the pack is a pre-existing directory on the LabDog host;
    nothing is cloned. Useful for BYO playbooks you maintain outside a
    git workflow, or for local dev against a working copy.
    """

    GIT = "git"
    LOCAL = "local"


class PackRole(enum.StrEnum):
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
    """A pack of playbooks LabDog treats as a source of action
    definitions.

    For git packs: ``git_repository_id`` references a configured
    ``GitRepository`` row (managed on the Git Repos page) and ``path``
    is the subpath within that repo where the pack lives. URL, branch,
    and credentials are owned by the repository row — never duplicated
    here.

    For local packs: ``local_path`` is the absolute filesystem path on
    the LabDog host where the pack directory lives. No clone, no auth.
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

    #: For source_type='git': FK into git_repositories. For 'local':
    #: always NULL.
    git_repository_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("git_repositories.id", ondelete="RESTRICT"),
        nullable=True,
    )

    #: For source_type='git': subpath within the repo where the pack
    #: lives; empty string = repo root. For 'local': ignored.
    path: Mapped[str] = mapped_column(String(500), default="", server_default="")

    #: For source_type='local': absolute filesystem path on the LabDog
    #: host. For 'git': NULL.
    local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

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

    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
