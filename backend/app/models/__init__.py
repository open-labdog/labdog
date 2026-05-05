"""Models package — imports all models so Alembic autogenerate can discover them.

External module models (services, hosts_mgmt, etc.) are imported via
``__getattr__`` so that importing ``app.models.base`` does NOT trigger
these imports (avoiding the circular-import cycle).  Alembic's env.py
calls ``import_all_models()`` explicitly after the package is ready.
"""

from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.firewall_rule import FirewallRule, RuleAction, RuleDirection, RuleProtocol
from app.models.git_repository import GitAuthType, GitOpsStatus, GitRepository
from app.models.host import FirewallBackend, Host, HostGroupMembership, SyncStatus
from app.models.host_group import HostGroup
from app.models.host_module_status import HostModuleStatus
from app.models.scan_config import PendingHost, ScanConfig
from app.models.scheduled_action import ScheduledAction
from app.models.ssh_key import SSHKey
from app.models.sync_job import JobStatus, SyncJob
from app.models.user import User

# Lazy-loaded external model names
_EXTERNAL_MODELS = {
    "ServiceRule": "app.services.models",
    "HostsEntry": "app.hosts_mgmt.models",
    "CronJob": "app.cron.models",
    "LinuxUser": "app.user_mgmt.models",
    "LinuxGroup": "app.user_mgmt.models",
    "PackageRule": "app.packages.models",
    "PackageRepository": "app.packages.models",
    "CACertRule": "app.ca_certs.models",
    "ResolverConfig": "app.resolver.models",
    "ResolverType": "app.resolver.models",
    "AppSetting": "app.models.app_setting",
    "ProxmoxNode": "app.proxmox.models",
    "VMMapping": "app.proxmox.vm_mapping",
    "ActionPack": "app.packs.models",
    "PackSourceType": "app.packs.models",
    "PackRole": "app.packs.models",
}


def import_all_models():
    """Import all external models so they register on Base.metadata.

    Call this from Alembic env.py or app startup — NOT during __init__.py.
    """
    import importlib

    seen = set()
    for module_path in _EXTERNAL_MODELS.values():
        if module_path not in seen:
            importlib.import_module(module_path)
            seen.add(module_path)


def __getattr__(name):
    """Lazy-load external model classes on first access."""
    if name in _EXTERNAL_MODELS:
        import importlib

        mod = importlib.import_module(_EXTERNAL_MODELS[name])
        val = getattr(mod, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    "ScanConfig",
    "PendingHost",
    "GitRepository",
    "GitAuthType",
    "GitOpsStatus",
    "HostModuleStatus",
    "ScheduledAction",
    "ServiceRule",
    "HostsEntry",
    "CronJob",
    "LinuxUser",
    "LinuxGroup",
    "PackageRule",
    "PackageRepository",
    "CACertRule",
    "ResolverConfig",
    "ResolverType",
    "ProxmoxNode",
    "VMMapping",
    "ActionPack",
    "PackSourceType",
    "PackRole",
]
