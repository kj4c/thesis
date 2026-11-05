# the addresses shown to the user for pointing the phone at this mac. a wrong
# "tailscale" address (e.g. the wifi one) would send them chasing a dead link.

import pytest

import network


class FakeSocket:
    routes: dict[str, str | None] = {}

    def __init__(self, *_):
        self._dest = None

    def connect(self, addr):
        if self.routes.get(addr[0]) is None:
            raise OSError("network unreachable")
        self._dest = addr[0]

    def getsockname(self):
        return (self.routes[self._dest], 54321)

    def close(self):
        pass


@pytest.fixture
def routes(monkeypatch):
    monkeypatch.setattr(network.socket, "socket", FakeSocket)
    FakeSocket.routes = {}
    return FakeSocket.routes


def test_tailscale_address_when_tunnel_is_up(routes):
    routes.update({"100.100.100.100": "100.101.7.42", "8.8.8.8": "192.168.0.138"})
    assert network.tailscale_ip() == "100.101.7.42"
    assert network.lan_ip() == "192.168.0.138"


def test_no_tailscale_address_when_traffic_goes_out_over_wifi(routes):
    # tailscale off: the os routes its service ip via the normal gateway
    routes.update({"100.100.100.100": "192.168.0.138", "8.8.8.8": "192.168.0.138"})
    assert network.tailscale_ip() is None


@pytest.mark.parametrize("ip,expected", [
    ("100.64.0.1", "100.64.0.1"),       # bottom of the tailnet range
    ("100.127.255.254", "100.127.255.254"),
    ("100.128.0.1", None),              # just outside it
    ("10.4.165.59", None),
    ("172.20.10.4", None),
])
def test_only_tailnet_range_counts(routes, ip, expected):
    routes["100.100.100.100"] = ip
    assert network.tailscale_ip() == expected


def test_offline_machine(routes):
    assert network.tailscale_ip() is None
    assert network.lan_ip() == "127.0.0.1"


def test_real_machine_does_not_crash():
    ts = network.tailscale_ip()
    assert ts is None or ts.startswith("100.")
    assert network.lan_ip()
