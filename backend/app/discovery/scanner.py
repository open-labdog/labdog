import asyncio
import ipaddress

BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / cloud metadata (169.254.169.254!)
    ipaddress.ip_network("224.0.0.0/4"),  # multicast
    ipaddress.ip_network("240.0.0.0/4"),  # reserved
]


async def check_port(
    host: str,
    port: int,
    semaphore: asyncio.Semaphore,
    timeout: float = 1.0,
) -> tuple[str, str] | None:
    """
    Check if a host is reachable on the given TCP port.

    Returns (ip, status) where status is "open" or "refused", or None if
    the host is unreachable (timeout / network-unreachable).
    """
    async with semaphore:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()  # CRITICAL: prevents fd leaks under load
            return (host, "open")
        except ConnectionResetError:
            # Port was open; service reset connection immediately (treat as open)
            return (host, "open")
        except ConnectionRefusedError:
            # Host is alive but port is closed — exclude from discovery results
            return None
        except (TimeoutError, OSError):
            return None


async def scan_network(
    cidr: str,
    port: int = 22,
    timeout: float = 1.0,
    max_concurrent: int = 100,
) -> list[tuple[str, str]]:
    """
    Scan all hosts in CIDR range for open port.
    Returns list of (ip, status) tuples for reachable hosts.
    """
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [check_port(h, port, semaphore, timeout) for h in hosts]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


def validate_cidr(cidr: str, min_prefix: int = 20) -> ipaddress.IPv4Network:
    """
    Validate a CIDR range for scanning.
    Raises ValueError with clear message if invalid, too large, or blocked.
    Returns the parsed IPv4Network if valid.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        raise ValueError(f"Invalid CIDR: {e}") from e

    if not isinstance(network, ipaddress.IPv4Network):
        raise ValueError("Only IPv4 CIDR ranges are supported")

    if network.prefixlen < min_prefix:
        raise ValueError(
            f"CIDR range too large. Maximum scan range is /{min_prefix} "
            f"({2 ** (32 - min_prefix) - 2} hosts). Got /{network.prefixlen} "
            f"({network.num_addresses} addresses)."
        )

    for blocked in BLOCKED_NETWORKS:
        if network.overlaps(blocked):
            raise ValueError(
                f"Scanning {network} is not permitted (overlaps blocked range {blocked})"
            )

    return network
