import re

PROTECTED_USERS: frozenset[str] = frozenset({
    "root", "daemon", "bin", "sys", "sync", "games", "man", "lp", "mail",
    "news", "uucp", "proxy", "www-data", "backup", "list", "irc", "gnats",
    "nobody", "sshd", "systemd-network", "systemd-resolve", "messagebus", "polkitd",
})

PROTECTED_GROUPS: frozenset[str] = frozenset({
    "root", "daemon", "bin", "sys", "adm", "tty", "disk", "lp", "mail", "news",
    "uucp", "man", "proxy", "kmem", "dialout", "fax", "voice", "cdrom", "floppy",
    "tape", "sudo", "audio", "dip", "www-data", "backup", "operator", "list",
    "irc", "src", "gnats", "shadow", "utmp", "video", "sasl", "plugdev", "staff",
    "games", "users", "nogroup", "wheel", "sshd",
})

# Shell metacharacters forbidden in sudo_rule
SUDO_FORBIDDEN_PATTERN = re.compile(r"[`$();|&<>]")

# Valid SSH public key type prefixes
VALID_KEY_TYPES = (
    "ssh-rsa", "ssh-ed25519", "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-dss",
)
