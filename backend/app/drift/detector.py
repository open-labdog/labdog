from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from app.sync.diff import RulesetDiff, compute_diff, fetch_current_state_stub


@dataclass
class DriftResult:
    host_id: int
    status: str  # "in_sync" | "out_of_sync" | "error" | "unknown"
    diff: Optional[RulesetDiff] = None
    checked_at: datetime = None
    error_message: Optional[str] = None

    def __post_init__(self):
        if self.checked_at is None:
            self.checked_at = datetime.now(timezone.utc)


async def check_drift(host_id: int, desired_rules: list) -> DriftResult:
    """Check if host firewall matches desired state."""
    try:
        current = await fetch_current_state_stub(host_id)
        diff = compute_diff(current, desired_rules)
        status = "in_sync" if not diff.has_changes else "out_of_sync"
        return DriftResult(host_id=host_id, status=status, diff=diff)
    except Exception as e:
        return DriftResult(host_id=host_id, status="error", error_message=str(e))
