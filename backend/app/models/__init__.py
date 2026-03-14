"""Models package — imports all models so Alembic autogenerate can discover them."""

from app.models.base import Base
from app.models.user import User
from app.models.host_group import HostGroup
from app.models.ssh_key import SSHKey
from app.models.host import Host, HostGroupMembership, FirewallBackend, SyncStatus
from app.models.firewall_rule import FirewallRule, RuleAction, RuleProtocol, RuleDirection
from app.models.sync_job import SyncJob, JobStatus
from app.models.audit_log import AuditLog
from app.models.user_group_permission import UserGroupPermission, GroupRole

__all__ = [
    "Base",
    "User",
    "HostGroup",
    "SSHKey",
    "Host",
    "HostGroupMembership",
    "FirewallBackend",
    "SyncStatus",
    "FirewallRule",
    "RuleAction",
    "RuleProtocol",
    "RuleDirection",
    "SyncJob",
    "JobStatus",
    "AuditLog",
    "UserGroupPermission",
    "GroupRole",
]
