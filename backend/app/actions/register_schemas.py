"""Map canonical module name -> (Pydantic Create schema, SQLAlchemy model)
for the post_run_register feature on action manifests.

Lives in its own module so the manifest validator and the dispatch
helper can share one source of truth, and so the imports of all seven
modules' schemas/models don't fire at every-app-startup -- the manifest
loader imports this lazily inside its validator.

Keys must match the canonical module names from
``app.ansible_runtime.composer.CANONICAL_ORDER``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.cron.models import CronJob
from app.cron.schemas import CronJobCreate
from app.hosts_mgmt.models import HostsEntry
from app.hosts_mgmt.schemas import HostsEntryCreate
from app.models.firewall_rule import FirewallRule
from app.packages.models import PackageRule
from app.packages.schemas import PackageRuleCreate
from app.resolver.models import ResolverConfig
from app.resolver.schemas import ResolverConfigCreate
from app.schemas.rules import RuleCreate
from app.services.models import ServiceRule
from app.services.schemas import ServiceRuleCreate
from app.user_mgmt.models import LinuxUser
from app.user_mgmt.schemas import LinuxUserCreate

if TYPE_CHECKING:
    from pydantic import BaseModel

    from app.models.base import Base


# Maps canonical module name -> Pydantic Create schema. Used by the
# manifest validator to type-check post_run_register items and by the
# dispatch helper to re-validate at insert time.
CREATE_SCHEMAS: dict[str, type[BaseModel]] = {
    "packages": PackageRuleCreate,
    "resolver": ResolverConfigCreate,
    "services": ServiceRuleCreate,
    "hosts-file": HostsEntryCreate,
    "cron": CronJobCreate,
    "linux-users": LinuxUserCreate,
    "firewall": RuleCreate,
}

# Maps canonical module name -> SQLAlchemy model class. The dispatch
# helper constructs rows via ``Model(**validated_dict, host_id=...)``.
MODELS: dict[str, type[Base]] = {
    "packages": PackageRule,
    "resolver": ResolverConfig,
    "services": ServiceRule,
    "hosts-file": HostsEntry,
    "cron": CronJob,
    "linux-users": LinuxUser,
    "firewall": FirewallRule,
}

# Convenience -- the set of names accepted in post_run_register keys.
# Kept in sync with the two dicts above by construction.
VALID_MODULE_NAMES: frozenset[str] = frozenset(CREATE_SCHEMAS)
assert set(CREATE_SCHEMAS) == set(MODELS), (
    "CREATE_SCHEMAS and MODELS must declare the same module names"
)
