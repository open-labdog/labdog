import enum

from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class GroupRole(str, enum.Enum):
    admin = "admin"
    editor = "editor"
    viewer = "viewer"


class UserGroupPermission(Base):
    __tablename__ = "user_group_permissions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("host_groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role: Mapped[GroupRole] = mapped_column(
        Enum(GroupRole, name="grouprole"),
    )
