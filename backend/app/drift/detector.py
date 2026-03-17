from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from app.models.host import SyncStatus
from app.sync.diff import RulesetDiff, compute_diff, fetch_current_state


@dataclass
class DriftResult:
    host_id: int
    status: SyncStatus
    diff: Optional[RulesetDiff] = None
    checked_at: datetime = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.checked_at is None:
            self.checked_at = datetime.now(timezone.utc)


async def check_drift(host_id: int, desired_rules: list, db=None) -> DriftResult:
    """Check if host firewall matches desired state."""
    try:
        current = await fetch_current_state(host_id, db)
        diff = compute_diff(current, desired_rules)
        status = SyncStatus.in_sync if not diff.has_changes else SyncStatus.out_of_sync
        return DriftResult(host_id=host_id, status=status, diff=diff)
    except Exception as e:
        return DriftResult(host_id=host_id, status=SyncStatus.error, error_message=str(e))
