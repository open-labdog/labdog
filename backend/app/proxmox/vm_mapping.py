from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class VMMapping(Base):
    """Maps a LabDog host to its Proxmox VM identity.

    Discovered via qemu-guest-agent network interface inspection.
    One mapping per host (unique constraint on host_id).
    """

    __tablename__ = "vm_mappings"

    id: Mapped[int] = mapped_column(primary_key=True)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    proxmox_node_id: Mapped[int] = mapped_column(
        ForeignKey("proxmox_nodes.id", ondelete="CASCADE"),
        index=True,
    )
    pve_node_name: Mapped[str] = mapped_column(String(100))
    vmid: Mapped[int] = mapped_column(Integer)
    vm_name: Mapped[str] = mapped_column(String(200))
    vm_type: Mapped[str] = mapped_column(String(10), nullable=False, server_default="qemu")
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
    )
