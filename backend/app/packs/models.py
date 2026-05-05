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

    ``position`` defines a single linear ordering across all packs.
    Higher position wins on action-key collisions. The bundled pack is
    implicit at position 0 (no DB row); every DB pack starts above it.
    Operators reorder packs via drag-to-reorder on ``/action-packs``.
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

    #: Linear precedence ordering. Higher wins on collisions. New packs
    #: default to ``MAX(position) + 1`` (server-assigned at insert).
    position: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", index=True
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


class ActionResolution(Base):
    """Explicit per-key "this pack wins" decision.

    Two write paths feed this table:

    1. **Wizard onboarding** — the operator picks per-key winners when
       adding a pack that conflicts with existing packs. The wizard's
       per-key radio writes a row here.
    2. **Sync-time freeze** — when ``reload_registry`` detects that a
       previously-uncontested key just became contested (a pack's
       upstream pushed a new manifest that conflicts with another
       pack), it auto-writes a row pinning the **previous winner**.
       Behaviour does not silently flip; the operator resolves via the
       conflict UI on ``/action-packs``.

    A row's presence overrides position-based default. ``pack_id NULL``
    means "use bundled" — supported because position-based default
    always favours DB packs over bundled, so the only way to make
    bundled win for a contested key is an explicit resolution.

    Deletion of the chosen pack drops the resolution row (CASCADE);
    position-based default takes over for the next rebuild.
    """

    __tablename__ = "action_resolution"

    action_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("action_packs.id", ondelete="CASCADE"),
        nullable=True,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
    decided_by_user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


class ActionRegistrySnapshot(Base):
    """Last-known winner per action key.

    Bookkeeping only — the registry rebuild reads this before computing
    the new state, then writes the new winners atomically. Used to
    detect "previously uncontested key just became contested" so the
    freeze logic in :class:`ActionResolution` can pin the previous
    winner without surprise flips.

    ``pack_id NULL`` means bundled was the winner (it has no DB row).
    """

    __tablename__ = "action_registry_snapshot"

    action_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    pack_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("action_packs.id", ondelete="CASCADE"),
        nullable=True,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
