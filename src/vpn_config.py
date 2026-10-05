"""Per-account OpenVPN tunnel with an iptables kill switch.

One container is one account, so the tunnel is simply the container's own
network: lock the firewall down so the only way out is tun0 (plus the VPN
endpoint itself, to build the tunnel), start OpenVPN, wait for tun0, then hand
over to the real command. If the tunnel drops, nothing else is allowed out.

Layout per account, in data-dir (mounted into the container):

    data-dir/<account>/openvpn/config.ovpn    required
    data-dir/<account>/openvpn/auth.txt       optional, username then password

    python src/vpn_config.py up <account>     used by with-vpn
"""

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("REWARDS_DATA_DIR", "/app/data-dir"))
TUN = "tun0"
CONNECT_TIMEOUT_SECONDS = 60

# The container's own resolver (Docker's 127.0.0.11) forwards out of the real
# interface, which the kill switch closes, so every lookup would fail once the
# tunnel is up. Lookups are sent through the tunnel to these instead; VPN_DNS
# overrides them, for example with the provider's own resolver.
RESOLV_CONF = Path("/etc/resolv.conf")
DEFAULT_DNS = "1.1.1.1 9.9.9.9"

REMOTE_HOST = re.compile(r"^(\s*remote\s+)(\S+)", re.I | re.M)
REMOTE = re.compile(r"^\s*remote\s+(\S+)(?:\s+(\d+))?(?:\s+(udp|tcp)\S*)?", re.I | re.M)
DEFAULT_PORT = re.compile(r"^\s*port\s+(\d+)", re.I | re.M)
DEFAULT_PROTO = re.compile(r"^\s*proto\s+(udp|tcp)\S*", re.I | re.M)


@dataclass(frozen=True)
class Endpoint:
	ip: str
	port: str
	proto: str


def vpn_dir(account: str) -> Path:
	return DATA_DIR / account / "openvpn"


def pin_remotes(ovpn_text: str, endpoints: list[Endpoint]) -> str:
	"""The config with every `remote` hostname replaced by its address.

	Once the firewall is closed OpenVPN cannot look a name up, so it would never
	reach a server given by name. The addresses are the ones just resolved, and
	the first one for a name is used.
	"""
	by_name: dict[str, str] = {}

	for host in {m[1] for m in REMOTE_HOST.findall(ovpn_text)}:
		infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
		by_name[host] = sorted({info[4][0] for info in infos})[0]

	return REMOTE_HOST.sub(lambda m: m.group(1) + by_name[m.group(2)], ovpn_text)


def write_resolv_conf(path: Path | None = None) -> None:
	servers = os.environ.get("VPN_DNS", DEFAULT_DNS).replace(",", " ").split()
	text = "".join(f"nameserver {server}\n" for server in servers)
	(path or RESOLV_CONF).write_text(text)


def parse_endpoints(ovpn_text: str) -> list[Endpoint]:
	"""Every server the config may dial, resolved to addresses.

	Resolved before the firewall closes, because afterwards there is no way to
	look a name up. Only these addresses are allowed out of the real interface.
	"""
	port_match = DEFAULT_PORT.search(ovpn_text)
	proto_match = DEFAULT_PROTO.search(ovpn_text)
	default_port = port_match.group(1) if port_match else "1194"
	default_proto = proto_match.group(1).lower() if proto_match else "udp"

	endpoints: list[Endpoint] = []
	for host, port, proto in REMOTE.findall(ovpn_text):
		port = port or default_port
		proto = (proto or default_proto).lower()
		try:
			infos = socket.getaddrinfo(host, int(port), socket.AF_INET, socket.SOCK_STREAM)
		except socket.gaierror as err:
			raise RuntimeError(f"cannot resolve VPN server {host!r}: {err}") from err
		for ip in sorted({info[4][0] for info in infos}):
			endpoints.append(Endpoint(ip, port, proto))

	if not endpoints:
		raise RuntimeError("config.ovpn has no 'remote' line")
	return endpoints


def _ipt(*args: str) -> None:
	subprocess.run(["iptables", *args], check=True, capture_output=True)


def apply_kill_switch(endpoints: list[Endpoint]) -> None:
	"""Default-drop everything; allow loopback, tun0 and the VPN servers only."""
	_ipt("-F")
	_ipt("-X")
	for chain in ("INPUT", "OUTPUT", "FORWARD"):
		_ipt("-P", chain, "DROP")

	_ipt("-A", "INPUT", "-i", "lo", "-j", "ACCEPT")
	_ipt("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")

	_ipt("-A", "INPUT", "-i", TUN, "-j", "ACCEPT")
	_ipt("-A", "OUTPUT", "-o", TUN, "-j", "ACCEPT")

	for ep in endpoints:
		_ipt("-A", "OUTPUT", "!", "-o", TUN, "-d", ep.ip, "-p", ep.proto,
			"--dport", ep.port, "-j", "ACCEPT")
		_ipt("-A", "INPUT", "!", "-i", TUN, "-s", ep.ip, "-p", ep.proto,
			"--sport", ep.port, "-m", "conntrack", "--ctstate", "ESTABLISHED",
			"-j", "ACCEPT")

	# IPv6 has no tunnel here, so close it outright rather than leave a side door.
	# Failure is tolerated: some kernels ship without ip6tables.
	for args in (("-F",), ("-P", "INPUT", "DROP"), ("-P", "OUTPUT", "DROP"),
		("-P", "FORWARD", "DROP")):
		subprocess.run(["ip6tables", *args], capture_output=True)


def wait_for_tunnel() -> bool:
	deadline = time.monotonic() + CONNECT_TIMEOUT_SECONDS
	while time.monotonic() < deadline:
		result = subprocess.run(["ip", "-o", "link", "show", TUN], capture_output=True, text=True)
		if result.returncode == 0 and ("UP" in result.stdout or "UNKNOWN" in result.stdout):
			return True
		time.sleep(1)
	return False


def up(account: str) -> int:
	folder = vpn_dir(account)
	config = folder / "config.ovpn"
	if not config.is_file():
		print(f"vpn: {config} not found; refusing to run without the tunnel", file=sys.stderr)
		return 1

	text = config.read_text(errors="replace")
	endpoints = parse_endpoints(text)

	# A copy with the servers as addresses, kept out of data-dir because its
	# inline certificates and keys are secrets and would be left lying around.
	pinned = Path(tempfile.gettempdir()) / f"vpn-{account}.ovpn"
	pinned.write_text(pin_remotes(text, endpoints))
	pinned.chmod(0o600)

	apply_kill_switch(endpoints)

	command = [
		"openvpn", "--config", str(pinned), "--cd", str(folder),
		"--dev", TUN, "--daemon", "--log", "/var/log/openvpn.log",
		"--persist-tun", "--resolv-retry", "infinite",
		"--script-security", "2",
	]
	auth = folder / "auth.txt"
	if auth.is_file():
		command += ["--auth-user-pass", str(auth)]
	subprocess.run(command, check=True)

	if not wait_for_tunnel():
		print("vpn: tunnel did not come up; see /var/log/openvpn.log", file=sys.stderr)
		return 1

	try:
		write_resolv_conf()
	except OSError as err:
		print(f"vpn: cannot set the resolver: {err}", file=sys.stderr)
		return 1

	print(f"vpn: {account} tunnel up via {', '.join(sorted({e.ip for e in endpoints}))}")
	return 0


if __name__ == "__main__":
	if len(sys.argv) != 3 or sys.argv[1] != "up":
		print("usage: vpn_config.py up <account>", file=sys.stderr)
		sys.exit(2)
	sys.exit(up(sys.argv[2]))
