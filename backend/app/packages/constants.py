import fnmatch

PROTECTED_PACKAGES: frozenset[str] = frozenset({
    "openssh-server", "openssh-client", "sshd",
    "systemd", "systemd-sysv",
    "linux-image*", "linux-headers*",
    "kernel", "kernel-core", "kernel-devel",
    "glibc", "libc6", "libc-bin", "libc6-dev",
    "coreutils", "bash",
    "init", "sysvinit-core",
    "grub", "grub2", "grub2-common", "grub-common",
})


def is_protected(name: str) -> bool:
    """Return True if package name matches any protected package pattern."""
    return any(
        fnmatch.fnmatch(name.lower(), pattern.lower())
        for pattern in PROTECTED_PACKAGES
    )
