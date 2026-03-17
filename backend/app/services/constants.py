PROTECTED_SERVICES: frozenset[str] = frozenset({
    "sshd",
    "ssh",
    "networking",
    "NetworkManager",
    "systemd-journald",
    "systemd-logind",
    "systemd-udevd",
    "systemd-resolved",
    "dbus",
})
