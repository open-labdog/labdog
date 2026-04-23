def render_resolv_conf(nameservers: list[str], search_domains: list[str], options: dict) -> str:
    """Render /etc/resolv.conf content."""
    lines = ["# Managed by LabDog — do not edit manually"]
    for ns in nameservers:
        lines.append(f"nameserver {ns}")
    if search_domains:
        lines.append(f"search {' '.join(search_domains)}")
    if options:
        opts_parts = []
        for key, val in options.items():
            if key in ("rotate", "edns0"):
                opts_parts.append(key)  # boolean options — just the key name
            else:
                opts_parts.append(f"{key}:{val}")
        lines.append(f"options {' '.join(opts_parts)}")
    lines.append("")
    return "\n".join(lines)


def render_systemd_resolved(
    nameservers: list[str], search_domains: list[str], dns_over_tls: bool
) -> str:
    """Render /etc/systemd/resolved.conf content."""
    lines = [
        "# Managed by LabDog — do not edit manually",
        "[Resolve]",
        f"DNS={' '.join(nameservers)}",
    ]
    if search_domains:
        lines.append(f"Domains={' '.join(search_domains)}")
    if dns_over_tls:
        lines.append("DNSOverTLS=yes")
    lines.append("")
    return "\n".join(lines)


def render_networkmanager_conf(nameservers: list[str], search_domains: list[str]) -> str:
    """Render /etc/NetworkManager/conf.d/90-labdog-dns.conf content."""
    lines = [
        "# Managed by LabDog — do not edit manually",
        "[global-dns-domain-*]",
        f"servers={','.join(nameservers)}",
    ]
    if search_domains:
        lines.append(f"options=search {' '.join(search_domains)}")
    lines.append("")
    return "\n".join(lines)


def render_config(effective_config) -> str:
    rt = effective_config.resolver_type
    ns = effective_config.nameservers
    sd = effective_config.search_domains
    opts = effective_config.options
    dot = getattr(effective_config, "dns_over_tls", False)

    if rt == "resolv_conf":
        return render_resolv_conf(ns, sd, opts)
    elif rt == "systemd_resolved":
        return render_systemd_resolved(ns, sd, dot)
    elif rt == "networkmanager":
        return render_networkmanager_conf(ns, sd)
    else:
        raise ValueError(f"Unknown resolver type: {rt}")
