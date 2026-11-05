# the addresses the phone can use to reach this machine.

import ipaddress
import socket

# tailscale gives every device an address in the carrier-grade nat range
_TAILNET = ipaddress.ip_network("100.64.0.0/10")
# tailscale's own service address, routed through the tunnel when it's up
_TAILSCALE_SERVICE_IP = "100.100.100.100"


def _source_ip_for(dest: str) -> str | None:
    # the local address the os would send from to reach `dest` (udp connect
    # sends nothing, it only picks a route)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((dest, 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def lan_ip() -> str:
    return _source_ip_for("8.8.8.8") or "127.0.0.1"


def tailscale_ip() -> str | None:
    # reachable from the phone on any network once both are on the same
    # tailnet; None when tailscale isn't running on this machine
    ip = _source_ip_for(_TAILSCALE_SERVICE_IP)
    if ip and ipaddress.ip_address(ip) in _TAILNET:
        return ip
    return None
