import re

PROTECTED_USERS: frozenset[str] = frozenset(
    {
        "root",
        "daemon",
        "bin",
        "sys",
        "sync",
        "games",
        "man",
        "lp",
        "mail",
        "news",
        "uucp",
        "proxy",
        "www-data",
        "backup",
        "list",
        "irc",
        "gnats",
        "nobody",
        "sshd",
        "systemd-network",
        "systemd-resolve",
        "messagebus",
        "polkitd",
    }
)

PROTECTED_GROUPS: frozenset[str] = frozenset(
    {
        "root",
        "daemon",
        "bin",
        "sys",
        "adm",
        "tty",
        "disk",
        "lp",
        "mail",
        "news",
        "uucp",
        "man",
        "proxy",
        "kmem",
        "dialout",
        "fax",
        "voice",
        "cdrom",
        "floppy",
        "tape",
        "sudo",
        "audio",
        "dip",
        "www-data",
        "backup",
        "operator",
        "list",
        "irc",
        "src",
        "gnats",
        "shadow",
        "utmp",
        "video",
        "sasl",
        "plugdev",
        "staff",
        "games",
        "users",
        "nogroup",
        "wheel",
        "sshd",
    }
)

# Characters forbidden in sudo_rule.
#
# The generator writes ``f"{username} {sudo_rule}\n"`` into
# ``/etc/sudoers.d/{username}`` (user_mgmt/generator.py). The shell
# metacharacters were blocked from the start; ``\n`` was not — so a rule of
#
#     ALL=(ALL) NOPASSWD: /bin/true\nsomeone ALL=(ALL) NOPASSWD: ALL
#
# wrote a *two-line* drop-in granting passwordless root to an account
# LabDog does not manage. ``visudo -cf`` accepts it: it is perfectly valid
# sudoers syntax, which is exactly why the validate step never caught it
# (SEC-25).
#
# \r is included because sudoers treats a bare CR as line-ending in
# enough contexts to be worth refusing, and NUL because it truncates.
SUDO_FORBIDDEN_PATTERN = re.compile(r"[`$();|&<>\r\n\x00]")

# Valid SSH public key type prefixes
VALID_KEY_TYPES = (
    "ssh-rsa",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "ssh-dss",
)
