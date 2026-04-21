from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ANSIBLE_DIR = Path(__file__).parent.parent / "ansible"


@dataclass(frozen=True)
class ActionParameter:
    key: str
    label: str
    type: Literal["string", "int", "bool", "choice"]
    default: Any = None
    required: bool = False
    choices: tuple[str, ...] | None = None
    help_text: str | None = None


@dataclass(frozen=True)
class ActionDefinition:
    key: str
    name: str
    description: str
    icon: str
    playbook_path: Path
    version: str
    estimated_duration: str
    destructive: bool = False
    supports_group: bool = True
    supports_host: bool = True
    parameters: tuple[ActionParameter, ...] = field(default_factory=tuple)


ACTION_REGISTRY: dict[str, ActionDefinition] = {}


def register(defn: ActionDefinition) -> None:
    ACTION_REGISTRY[defn.key] = defn


register(
    ActionDefinition(
        key="linux-upgrade",
        name="Upgrade Linux packages",
        description=(
            "Upgrades all system packages to the latest version. "
            "Reboots if /var/run/reboot-required is set."
        ),
        icon="ArrowUpFromLine",
        playbook_path=ANSIBLE_DIR / "actions" / "linux-upgrade.yml",
        version="1.0",
        estimated_duration="5\u201315 min",
        destructive=True,
        parameters=(
            ActionParameter(
                key="auto_reboot",
                label="Reboot if required",
                type="bool",
                default=True,
                help_text="Reboot the host after upgrade if /var/run/reboot-required exists.",
            ),
            ActionParameter(
                key="reboot_timeout",
                label="Reboot timeout (seconds)",
                type="int",
                default=300,
                help_text="Maximum seconds to wait for host to come back after reboot.",
            ),
            ActionParameter(
                key="cleanup",
                label="Remove unused packages",
                type="bool",
                default=True,
            ),
        ),
    )
)

register(
    ActionDefinition(
        key="linux-os-upgrade",
        name="Upgrade OS release",
        description=(
            "Upgrades to a new major OS release "
            "(e.g. Debian 12\u219213, Ubuntu 22.04\u219224.04). "
            "Includes NIC rename fix."
        ),
        icon="Layers",
        playbook_path=ANSIBLE_DIR / "actions" / "linux-os-upgrade.yml",
        version="1.0",
        estimated_duration="20\u201345 min",
        destructive=True,
        parameters=(
            ActionParameter(
                key="current_version",
                label="Current codename",
                type="string",
                required=True,
                help_text="e.g. bookworm (Debian 12) or jammy (Ubuntu 22.04)",
            ),
            ActionParameter(
                key="next_version",
                label="Target codename",
                type="string",
                required=True,
                help_text="e.g. trixie (Debian 13) or noble (Ubuntu 24.04)",
            ),
            ActionParameter(
                key="cleanup",
                label="Remove unused packages",
                type="bool",
                default=True,
            ),
        ),
    )
)

register(
    ActionDefinition(
        key="k8s-upgrade",
        name="Upgrade Kubernetes cluster",
        description="Drains, upgrades, and re-admits each node in the cluster.",
        icon="Network",
        playbook_path=ANSIBLE_DIR / "actions" / "k8s-upgrade.yml",
        version="1.0",
        estimated_duration="5\u201320 min per node",
        destructive=True,
        parameters=(
            ActionParameter(
                key="target_version",
                label="Target Kubernetes version",
                type="string",
                required=True,
                help_text="e.g. 1.29.3",
            ),
            ActionParameter(
                key="skip_preflight",
                label="Skip preflight checks",
                type="bool",
                default=False,
            ),
        ),
    )
)
