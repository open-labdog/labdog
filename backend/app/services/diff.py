"""Service drift diff engine — compare current vs desired service states."""

from dataclasses import dataclass, field


NORMALIZE_TO_RUNNING = {"running", "restarted", "reloaded"}


@dataclass
class ServiceDiffItem:
    service_name: str
    desired_state: str
    desired_enabled: bool
    actual_state: str
    actual_enabled: bool
    reason: str  # "state_mismatch", "enabled_mismatch", "both_mismatch", "error"


@dataclass
class ServiceDiff:
    services_to_update: list[ServiceDiffItem] = field(default_factory=list)
    services_in_sync: list[str] = field(default_factory=list)
    services_with_errors: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.services_to_update)


def _normalize_state(state: str) -> str:
    """Normalize restarted/reloaded to running for comparison."""
    return "running" if state in NORMALIZE_TO_RUNNING else state


def compute_service_diff(
    current: list,  # list[ServiceCurrentState]
    desired: list,  # list[EffectiveServiceResponse]
) -> ServiceDiff:
    """
    Compare current service states against desired config.

    CRITICAL: Normalize desired state — restarted/reloaded are treated as "running"
    for comparison.
    """
    diff = ServiceDiff()

    # Index current states by service_name
    current_map = {s.service_name: s for s in current}

    for desired_svc in desired:
        name = desired_svc.service_name
        desired_state = _normalize_state(
            desired_svc.state.value
            if hasattr(desired_svc.state, "value")
            else str(desired_svc.state)
        )
        desired_enabled = desired_svc.enabled

        current_svc = current_map.get(name)

        if current_svc is None or current_svc.active_state == "error":
            diff.services_with_errors.append(name)
            continue

        actual_state = current_svc.active_state  # already "running" or "stopped"
        actual_enabled = current_svc.enabled

        state_mismatch = desired_state != actual_state
        enabled_mismatch = desired_enabled != actual_enabled

        if state_mismatch or enabled_mismatch:
            if state_mismatch and enabled_mismatch:
                reason = "both_mismatch"
            elif state_mismatch:
                reason = "state_mismatch"
            else:
                reason = "enabled_mismatch"

            diff.services_to_update.append(
                ServiceDiffItem(
                    service_name=name,
                    desired_state=desired_state,
                    desired_enabled=desired_enabled,
                    actual_state=actual_state,
                    actual_enabled=actual_enabled,
                    reason=reason,
                )
            )
        else:
            diff.services_in_sync.append(name)

    return diff
