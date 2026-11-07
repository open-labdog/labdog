#!/bin/sh
# Replace Docker's bind mounts with plain files so Ansible can rename over them.
for f in /etc/hosts /etc/resolv.conf; do
    cp "$f" "/tmp/$(basename "$f").orig"
    umount "$f" && cp "/tmp/$(basename "$f").orig" "$f"
done
if [ "${NETEM_DELAY_MS:-0}" -gt 0 ]; then
    tc qdisc add dev eth0 root netem delay "${NETEM_DELAY_MS}ms"
fi
ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key
cron
exec /usr/sbin/sshd -D -e
