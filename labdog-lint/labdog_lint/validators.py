import ipaddress


def validate_cidr(cidr: str) -> str | None:
    try:
        ipaddress.ip_network(cidr, strict=False)
        return None
    except ValueError as e:
        return f"Invalid CIDR: {e}"


def validate_port(port) -> str | None:
    if isinstance(port, int):
        if port < 1 or port > 65535:
            return f"Port {port} out of range (1-65535)"
        return None
    if isinstance(port, str) and "-" in port:
        parts = port.split("-", 1)
        try:
            start, end = int(parts[0]), int(parts[1])
            if start < 1 or end > 65535 or end < start:
                return f"Invalid port range: {port}"
            return None
        except ValueError:
            return f"Invalid port range: {port}"
    try:
        p = int(port)
        if p < 1 or p > 65535:
            return f"Port {p} out of range"
        return None
    except (ValueError, TypeError):
        return f"Invalid port value: {port}"
