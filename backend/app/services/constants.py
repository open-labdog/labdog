PROTECTED_SERVICES: frozenset[str] = frozenset(
    {
        "sshd",
        "ssh",
        "networking",
        "NetworkManager",
        "systemd-journald",
        "systemd-logind",
        "systemd-udevd",
        "systemd-resolved",
        "dbus",
    }
)

# Prefixes for services considered system/internal (not typically user-managed).
SYSTEM_SERVICE_PREFIXES: tuple[str, ...] = (
    "systemd-",
    "getty@",
    "serial-getty@",
    "user@",
    "user-runtime-dir@",
    "modprobe@",
    "initrd-",
    "dracut-",
    "plymouth",
)

# Exact names for services considered system/internal.
SYSTEM_SERVICE_NAMES: frozenset[str] = frozenset(
    {
        "dbus",
        "dbus-broker",
        "emergency",
        "rescue",
        "console-setup",
        "keyboard-setup",
        "kmod-static-nodes",
        "ldconfig",
        "proc-sys-fs-binfmt_misc",
        "sys-fs-fuse-connections",
        "sys-kernel-config",
        "sys-kernel-debug",
        "sys-kernel-tracing",
        "tmp",
        "rc-local",
        "lvm2-monitor",
        "dm-event",
        "multipathd",
        "blk-availability",
        "finalrd",
        "apparmor",
        "snapd",
        "snap",
        "polkit",
        "rtkit-daemon",
        "udisks2",
        "accounts-daemon",
        "switcheroo-control",
        "power-profiles-daemon",
        "thermald",
        "bolt",
        "fwupd",
        "packagekit",
        "colord",
    }
)


def is_system_service(unit: str) -> bool:
    """Check if a service unit name is a system/internal service."""
    return (
        unit in SYSTEM_SERVICE_NAMES
        or unit in PROTECTED_SERVICES
        or any(unit.startswith(p) for p in SYSTEM_SERVICE_PREFIXES)
    )
