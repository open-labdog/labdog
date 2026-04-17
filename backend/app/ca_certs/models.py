import enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class CertState(enum.StrEnum):
    present = "present"
    absent = "absent"


class CACertRule(Base):
    __tablename__ = "ca_cert_rules"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_ca_cert_rules_scope",
        ),
        UniqueConstraint("group_id", "fingerprint_sha256", name="uq_ca_cert_rules_group_fp"),
        UniqueConstraint("host_id", "fingerprint_sha256", name="uq_ca_cert_rules_host_fp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    pem_content: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(95), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    not_before: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    not_after: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[CertState] = mapped_column(
        SAEnum(CertState, name="certstate"),
        nullable=False,
        default=CertState.present,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
