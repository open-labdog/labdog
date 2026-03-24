import asyncio
import socket

from app.tasks import celery_app


@celery_app.task(bind=True, track_started=True, name="discovery.scan_network")
def scan_network_task(self, cidr: str, port: int, timeout: float, exclude_ips: list[str]) -> dict:
    """
    Scan network for SSH hosts, excluding IPs already in the DB.
    Runs asyncio scanner in a Celery worker (sync task, bridges to async via asyncio.run).
    """
    from app.config import settings
    from app.discovery.scanner import check_port, validate_cidr

    # Validate and enumerate hosts
    network = validate_cidr(cidr, settings.discovery.min_prefix)
    all_hosts = [str(ip) for ip in network.hosts()]
    # Subtract already-known hosts
    exclude_set = set(exclude_ips)
    hosts_to_scan = [h for h in all_hosts if h not in exclude_set]
    total = len(hosts_to_scan)

    # Scan in batches, reporting progress every ~50 hosts
    async def _scan_with_progress():
        semaphore = asyncio.Semaphore(settings.discovery.max_concurrent)

        found = []
        completed = 0
        tasks = [check_port(h, port, semaphore, timeout) for h in hosts_to_scan]

        # Use asyncio.as_completed for progress updates
        for coro in asyncio.as_completed(tasks):
            result = await coro
            completed += 1
            if result:
                found.append(result)
            # Update progress every 50 hosts
            if completed % 50 == 0 or completed == total:
                self.update_state(
                    state='PROGRESS',
                    meta={'progress': completed, 'total': total, 'found': len(found)}
                )
        return found

    reachable = asyncio.run(_scan_with_progress())

    # Attempt reverse DNS for each discovered IP
    hosts_found = []
    for ip, port_status in reachable:
        try:
            fqdn = socket.getfqdn(ip)
            hostname = None if fqdn == ip else fqdn
        except Exception:
            hostname = None
        hosts_found.append({"ip": ip, "hostname": hostname, "ssh_status": port_status})

    return {
        "hosts_found": hosts_found,
        "total_scanned": total,
    }
