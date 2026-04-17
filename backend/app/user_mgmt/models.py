import enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class UserState(enum.StrEnum):
    present = "present"
    absent = "absent"


class LinuxUser(Base):
    __tablename__ = "linux_users"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_linux_users_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shell: Mapped[str] = mapped_column(String(100), nullable=False, default="/bin/bash")
    home_dir: Mapped[str | None] = mapped_column(String(200), nullable=True)
    state: Mapped[UserState] = mapped_column(
        SAEnum(UserState, name="userstate"),
        nullable=False,
        default=UserState.present,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    sudo_rule: Mapped[str | None] = mapped_column(Text, nullable=True)
    authorized_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    supplementary_groups: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LinuxGroup(Base):
    __tablename__ = "linux_groups"
    __table_args__ = (
        CheckConstraint(
            "(group_id IS NOT NULL AND host_id IS NULL)"
            " OR (group_id IS NULL AND host_id IS NOT NULL)",
            name="ck_linux_groups_scope",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"), nullable=True
    )
    host_id: Mapped[int | None] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=True
    )
    groupname: Mapped[str] = mapped_column(String(32), nullable=False)
    gid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[UserState] = mapped_column(
        SAEnum(UserState, name="userstate"),
        nullable=False,
        default=UserState.present,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
