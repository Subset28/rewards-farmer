"""One OpenVPN tunnel and one iptables kill switch per account, in one container.

Modelled on the binhex qbittorrentvpn images (default-drop firewall, a tunnel that
must come up before anything runs, and a supervisor that notices a dead tunnel),
but with a separate tunnel for each account so each one sits behind a different
VPN location.

The NAS kernel has no iptables owner match and ignores per-user routing rules, so
accounts cannot be told apart by user. They are told apart by network namespace
instead: every account gets its own, which has its own interfaces, its own routes,
its own firewall and its own tun0. Nothing in it can reach another account's
tunnel, and the only way out of it to the real network is a veth pair that the
host side allows to the VPN servers and nothing else.

    account namespace                         container (host side)
    -----------------                         ---------------------
    tun0  <- OpenVPN, the only way out        vh<N>  allowed to this account's
    vn<N> --------- veth pair --------------        VPN servers only (FORWARD)
    firewall: drop all, allow lo, tun0,       NAT out of eth0
    and the VPN servers on vn<N>

Starting up, for each account: build the namespace, close its firewall, start
OpenVPN inside it, wait for tun0, and check the exit address (it must not be one
of HOME_IP_BLACKLIST, and no two accounts may share one). Then the schedulers
start. They run each account's browser inside that account's namespace
(isolation.py), and an account whose tunnel is down is skipped, never run on the
real connection. A dead tunnel is restarted on its own; if it will not come back
the supervisor exits and Docker's restart policy builds the container afresh.

Needs NET_ADMIN and SYS_ADMIN (namespaces) and /dev/net/tun. Layout per account,
in data-dir:

    data-dir/<account>/openvpn/config.ovpn    required
    data-dir/<account>/openvpn/auth.txt       optional, username then password
    data-dir/<account>/openvpn/timezone       optional, such as America/Chicago

Settings (environment):

    REWARDS_ACCOUNTS    the accounts to give a tunnel, comma separated
    NAME_SERVERS        resolvers used through the tunnels, default 1.1.1.1,1.0.0.1.
                        Avoid Google and OpenDNS: they pass on the client subnet.
    VPN_OPTIONS         extra OpenVPN command-line options
    VPN_IP_CHECK_URL    where to ask for the exit address, default https://api.ipify.org
    HOME_IP_BLACKLIST   comma-separated addresses that must never be the exit

    python src/vpn_config.py run -- <command> [args...] [-- <command> ...]
"""

import ipaddress
import json
import logging
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import accounts as account_names
import isolation
import notify

logger = logging.getLogger("vpn")

DATA_DIR = Path(os.environ.get("REWARDS_DATA_DIR", "/app/data-dir"))
STATE_FILE = Path(os.environ.get("VPN_STATE_FILE", "/run/vpn/namespaces.json"))
LOG_DIR = DATA_DIR / "logs"

TUN = "tun0"
CONNECT_TIMEOUT_SECONDS = 60
CHECK_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 10

# A dead tunnel is restarted no more often than this, and the supervisor gives up
# (so Docker rebuilds the container) after this many restarts in a row fail.
RESTART_BACKOFF_SECONDS = 30
MAX_FAILED_RESTARTS = 10

# Each account's namespace is joined to the container by a /30 of its own.
SUBNET_BASE = "10.200"
MAX_ACCOUNTS = 200

# Every namespace shares the container's /etc/resolv.conf, and a lookup made in
# one goes out through that namespace's own tunnel.
RESOLV_CONF = Path("/etc/resolv.conf")
DEFAULT_NAME_SERVERS = "1.1.1.1,1.0.0.1"

DEFAULT_IP_CHECK_URL = "https://api.ipify.org"

# Private and shared ranges, which include the veth subnets (10.200.0.0/16).
NON_PUBLIC = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")]

REMOTE_HOST = re.compile(r"^(\s*remote\s+)(\S+)", re.I | re.M)
REMOTE = re.compile(r"^\s*remote\s+(\S+)(?:\s+(\d+))?(?:\s+(udp|tcp)\S*)?", re.I | re.M)
DEFAULT_PORT = re.compile(r"^\s*port\s+(\d+)", re.I | re.M)
DEFAULT_PROTO = re.compile(r"^\s*proto\s+(udp|tcp)\S*", re.I | re.M)
# A provider's own dead-tunnel setting is removed: some (Surfshark's, with
# `ping-restart 0`) turn it off, and then OpenVPN never exits on a dead tunnel and
# the supervisor never finds out. ping-restart and ping-exit also cannot both be set.
STRIP_PING = re.compile(r"^[ \t]*(ping-restart|ping-exit)\b[^\n]*\n?", re.I | re.M)

# `auth-user-pass` with no file after it makes OpenVPN ask on the terminal, and there
# is none, so a config like that needs an auth.txt.
ASKS_FOR_LOGIN = re.compile(r"^[ \t]*auth-user-pass[ \t]*(?:#[^\n]*)?$", re.I | re.M)

# Directives that run code, open a control channel or write outside the sandbox. A
# provider config has no need of them and a tampered one could use any to break out
# of the namespace, so a config that has one is refused.
FORBIDDEN_DIRECTIVES = frozenset((
	"up", "down", "route-up", "route-pre-down", "ipchange", "plugin", "management", "tls-verify",
	"client-connect", "client-disconnect", "learn-address", "auth-user-pass-verify", "up-restart",
	"log", "log-append", "status", "writepid", "chroot", "daemon", "cd", "script-security",
	"engine", "providers", "inetd", "down-pre",
))
BLOCK_OPEN = re.compile(r"^<([A-Za-z0-9_-]+)>$")
BLOCK_CLOSE = re.compile(r"^</([A-Za-z0-9_-]+)>$")


def check_directives(ovpn_text: str) -> None:
	"""Raise RuntimeError if the config uses a directive from FORBIDDEN_DIRECTIVES."""
	block = None

	for line in ovpn_text.splitlines():
		line = line.strip()

		if block:
			closing = BLOCK_CLOSE.match(line)

			if closing and closing.group(1).lower() == block:
				block = None
				continue

			# Keys and certificates are not directives, but a <connection> holds real ones.
			if block != "connection":
				continue

		if not line or line[0] in "#;":
			continue

		opening = BLOCK_OPEN.match(line)

		if opening:
			block = opening.group(1).lower()
			continue

		# A config line may write an option with its command-line dashes (`--up script`).
		directive = line.split(None, 1)[0].lower().lstrip("-")

		if directive in FORBIDDEN_DIRECTIVES:
			raise RuntimeError(f"config.ovpn uses the directive {directive!r}, which is not allowed (it can run code or escape the namespace)")


def check_endpoint_address(host: str, ip: str) -> None:
	"""Raise RuntimeError for a server address that is not a public one.

	An address inside the container's own or a private network would let a config
	point the namespace's one hole in the firewall at something local.
	"""
	address = ipaddress.ip_address(ip)

	if (
		address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified
		or any(address in network for network in NON_PUBLIC)
	):
		raise RuntimeError(f"VPN server {host!r} resolves to {ip}, which is not a public address; refusing it")


@dataclass(frozen=True)
class Endpoint:
	ip: str
	port: str
	proto: str


@dataclass
class Tunnel:
	"""One account's namespace, tunnel and what is known about them."""

	account: str
	number: int
	endpoints: list[Endpoint]
	pinned: Path
	folder: Path
	workdir: Path | None = None  # private directory that holds `pinned`
	holder: object = None  # the process that keeps the namespace alive
	vpn: object = None  # OpenVPN, running inside it
	netns: str = ""  # /proc/<pid>/ns/net of the holder
	up: bool = False
	exit: str = ""
	reason: str = ""
	failed_restarts: int = 0
	last_restart: float = 0.0

	@property
	def host_if(self) -> str:
		return f"vh{self.number}"

	@property
	def ns_if(self) -> str:
		return f"vn{self.number}"

	@property
	def subnet(self) -> str:
		return f"{SUBNET_BASE}.{self.number}.0/30"

	@property
	def host_ip(self) -> str:
		return f"{SUBNET_BASE}.{self.number}.1"

	@property
	def ns_ip(self) -> str:
		return f"{SUBNET_BASE}.{self.number}.2"


def vpn_dir(account: str) -> Path:
	return DATA_DIR / account / "openvpn"


def ns_cmd(netns: str, *args: str) -> list[str]:
	"""A command to run inside a namespace."""
	return ["nsenter", f"--net={netns}", *args]


def _run(command, check=False, **kwargs):
	return subprocess.run(command, check=check, capture_output=True, text=True, **kwargs)


# --- reading the provider's config ---------------------------------------------------


def pin_remotes(ovpn_text: str) -> str:
	"""The config with every `remote` hostname replaced by its address.

	Once the firewall is closed OpenVPN cannot look a name up, so it would never
	reach a server given by name. The first address for a name is used.
	"""
	by_name: dict[str, str] = {}

	for host in {m[1] for m in REMOTE_HOST.findall(ovpn_text)}:
		infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
		by_name[host] = sorted({info[4][0] for info in infos})[0]

	return REMOTE_HOST.sub(lambda m: m.group(1) + by_name[m.group(2)], ovpn_text)


def parse_endpoints(ovpn_text: str) -> list[Endpoint]:
	"""Every server the config may dial, resolved to addresses.

	Resolved before any firewall closes, because afterwards there is no way to
	look a name up. Only these addresses are allowed out towards the real network.
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
			check_endpoint_address(host, ip)
			endpoints.append(Endpoint(ip, port, proto))

	if not endpoints:
		raise RuntimeError("config.ovpn has no 'remote' line")
	return endpoints


def write_resolv_conf(path: Path | None = None) -> None:
	servers = os.environ.get("NAME_SERVERS", DEFAULT_NAME_SERVERS).replace(",", " ").split()

	for server in servers:
		ipaddress.ip_address(server)

	(path or RESOLV_CONF).write_text("".join(f"nameserver {server}\n" for server in servers))


def plan(accounts: list[str]) -> list[Tunnel]:
	"""A Tunnel for each account, read from its config. Raises RuntimeError if any cannot be.

	Each tunnel gets a private directory holding its key-bearing config; if planning fails
	part way, the ones already made are removed rather than left in /tmp."""
	tunnels: list[Tunnel] = []

	try:
		return _plan(accounts, tunnels)
	except BaseException:
		for tunnel in tunnels:
			if tunnel.workdir:
				shutil.rmtree(tunnel.workdir, ignore_errors=True)

		raise


def _plan(accounts: list[str], tunnels: list[Tunnel]) -> list[Tunnel]:
	if not accounts:
		raise RuntimeError("no accounts to give a tunnel")

	if len(accounts) > MAX_ACCOUNTS:
		raise RuntimeError(f"at most {MAX_ACCOUNTS} accounts")

	for number, account in enumerate(accounts, start=1):
		# The name goes into paths, so it is held to what an account name may be.
		if not account_names.SAFE_NAME.fullmatch(account) or account in account_names.RESERVED_NAMES or account.endswith("."):
			raise RuntimeError(f"{account!r} is not usable as an account name")

		folder = vpn_dir(account)
		config = folder / "config.ovpn"

		if not config.is_file():
			raise RuntimeError(f"{config} not found; refusing to run {account} without its tunnel")

		text = config.read_text(errors="replace")

		if ASKS_FOR_LOGIN.search(text) and not (folder / "auth.txt").is_file():
			raise RuntimeError(
				f"{account}: the config asks for a login (auth-user-pass) but {folder / 'auth.txt'} is missing; "
				"put the provider's service username on the first line and its password on the second"
			)

		check_directives(text)
		endpoints = parse_endpoints(text)

		# A copy with the servers as addresses, kept out of data-dir because its
		# inline certificates and keys are secrets and would be left lying around.
		# The directory is 0700 and the file is created 0600 and exclusively, so the
		# keys are never readable by anyone else, not even briefly, and no one can
		# plant a link at a predictable path first.
		workdir = Path(tempfile.mkdtemp(prefix="vpn-", dir=tempfile.gettempdir()))
		pinned = workdir / "config.ovpn"

		try:
			descriptor = os.open(pinned, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

			with os.fdopen(descriptor, "w") as handle:
				handle.write(STRIP_PING.sub("", pin_remotes(text)))
		except BaseException:
			shutil.rmtree(workdir, ignore_errors=True)
			raise

		tunnels.append(Tunnel(account, number, endpoints, pinned, folder, workdir))

	return tunnels


# --- the namespace and its firewall --------------------------------------------------


def namespace_ready(pid: int) -> bool:
	"""Whether this process has moved into a namespace of its own yet."""
	try:
		return os.readlink(f"/proc/{pid}/ns/net") != os.readlink("/proc/self/ns/net")
	except OSError:
		return False


def create_namespace(tunnel: Tunnel, popen=subprocess.Popen, sleep=time.sleep) -> None:
	"""A namespace of its own, joined to the container by a veth pair."""
	# The holder only has to stay alive: a namespace lives as long as a process is in it.
	tunnel.holder = popen(["unshare", "-n", "sleep", "infinity"])
	tunnel.netns = f"/proc/{tunnel.holder.pid}/ns/net"

	# unshare moves into the new namespace a moment after it starts; joining or
	# configuring it before then would be doing it to the container's own network.
	for _ in range(50):
		if namespace_ready(tunnel.holder.pid):
			break

		sleep(0.1)
	else:
		raise OSError(f"{tunnel.account}: the network namespace did not appear")

	for command in (
		["ip", "link", "add", tunnel.host_if, "type", "veth", "peer", "name", tunnel.ns_if],
		["ip", "link", "set", tunnel.ns_if, "netns", str(tunnel.holder.pid)],
		["ip", "addr", "add", f"{tunnel.host_ip}/30", "dev", tunnel.host_if],
		["ip", "link", "set", tunnel.host_if, "up"],
		ns_cmd(tunnel.netns, "ip", "link", "set", "lo", "up"),
		ns_cmd(tunnel.netns, "ip", "addr", "add", f"{tunnel.ns_ip}/30", "dev", tunnel.ns_if),
		ns_cmd(tunnel.netns, "ip", "link", "set", tunnel.ns_if, "up"),
		ns_cmd(tunnel.netns, "ip", "route", "add", "default", "via", tunnel.host_ip),
	):
		_run(command, check=True)

	# IPv6 has no tunnel here. Closed outright, tolerating a kernel without it.
	_run(ns_cmd(tunnel.netns, "sysctl", "-w", "net.ipv6.conf.all.disable_ipv6=1"))

	for args in (("-F",), ("-P", "INPUT", "DROP"), ("-P", "OUTPUT", "DROP"), ("-P", "FORWARD", "DROP")):
		_run(ns_cmd(tunnel.netns, "ip6tables", *args))

	# Loopback alone stays open over IPv6. `localhost` resolves to ::1 as well as 127.0.0.1, and
	# the browser driver dials it for the websocket it hides its automation markers through; with
	# everything dropped that connection timed out ("socket is already closed"), the markers
	# stayed on every new tab, and the browser was easier to spot as automated. Nothing here
	# can leave the namespace: the loopback interface goes nowhere else.
	for args in (("-A", "INPUT", "-i", "lo", "-j", "ACCEPT"), ("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")):
		_run(ns_cmd(tunnel.netns, "ip6tables", *args))

	# Checked rather than assumed: a failed policy command above is tolerated, but
	# an open IPv6 chain would leak around the IPv4-only kill switch.
	rules = _run(ns_cmd(tunnel.netns, "ip6tables", "-S")).stdout
	dropped = set(re.findall(r"^-P (INPUT|OUTPUT|FORWARD) DROP[ \t]*$", rules, re.M))

	if dropped != {"INPUT", "OUTPUT", "FORWARD"}:
		raise RuntimeError(f"IPv6 is not closed in the namespace of {tunnel.account}; refusing to continue")


def namespace_kill_switch(tunnel: Tunnel) -> None:
	"""Inside the namespace: drop everything, allow loopback, tun0 and this account's VPN servers."""
	def ipt(*args: str) -> None:
		_run(ns_cmd(tunnel.netns, "iptables", *args), check=True)

	ipt("-F")
	ipt("-X")

	for chain in ("INPUT", "OUTPUT", "FORWARD"):
		ipt("-P", chain, "DROP")

	ipt("-A", "INPUT", "-i", "lo", "-j", "ACCEPT")
	ipt("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT")

	ipt("-A", "INPUT", "-i", TUN, "-j", "ACCEPT")
	ipt("-A", "OUTPUT", "-o", TUN, "-j", "ACCEPT")

	for ep in tunnel.endpoints:
		ipt("-A", "OUTPUT", "-o", tunnel.ns_if, "-d", ep.ip, "-p", ep.proto, "--dport", ep.port, "-j", "ACCEPT")
		ipt("-A", "INPUT", "-i", tunnel.ns_if, "-s", ep.ip, "-p", ep.proto, "--sport", ep.port,
			"-m", "conntrack", "--ctstate", "ESTABLISHED", "-j", "ACCEPT")


def host_rules(tunnels: list[Tunnel]) -> None:
	"""On the container side: each namespace may reach its own VPN servers, and nothing else, via NAT."""
	def ipt(*args: str) -> None:
		_run(["iptables", *args], check=True)

	ipt("-F", "FORWARD")
	ipt("-t", "nat", "-F", "POSTROUTING")
	ipt("-P", "FORWARD", "DROP")

	for tunnel in tunnels:
		ipt("-t", "nat", "-A", "POSTROUTING", "-s", tunnel.subnet, "!", "-o", tunnel.host_if, "-j", "MASQUERADE")

		for ep in tunnel.endpoints:
			ipt("-A", "FORWARD", "-i", tunnel.host_if, "-s", tunnel.ns_ip, "-d", ep.ip, "-p", ep.proto,
				"--dport", ep.port, "-j", "ACCEPT")
			ipt("-A", "FORWARD", "-o", tunnel.host_if, "-s", ep.ip, "-d", tunnel.ns_ip, "-p", ep.proto,
				"--sport", ep.port, "-m", "conntrack", "--ctstate", "ESTABLISHED", "-j", "ACCEPT")


# --- the tunnel ----------------------------------------------------------------------


def start_openvpn(tunnel: Tunnel, popen=subprocess.Popen):
	"""OpenVPN inside the account's namespace, as a child of this process so its death is noticed."""
	LOG_DIR.mkdir(parents=True, exist_ok=True)

	command = ns_cmd(
		tunnel.netns, "openvpn", "--config", str(tunnel.pinned), "--cd", str(tunnel.folder),
		"--dev", TUN, "--auth-nocache", "--resolv-retry", "infinite", "--script-security", "1",
		"--log-append", str(LOG_DIR / f"openvpn-{tunnel.account}.log"),
	)

	# OpenVPN has to give up on a dead tunnel for the supervisor to see it.
	command += ["--ping", "15", "--ping-exit", "90"]

	auth = tunnel.folder / "auth.txt"
	if auth.is_file():
		command += ["--auth-user-pass", str(auth)]

	command += shlex.split(os.environ.get("VPN_OPTIONS", ""))

	return popen(command)


def tunnel_is_up(tunnel: Tunnel) -> bool:
	result = _run(ns_cmd(tunnel.netns, "ip", "-o", "link", "show", TUN))
	return result.returncode == 0 and ("UP" in result.stdout or "UNKNOWN" in result.stdout)


def wait_for_tunnel(tunnel: Tunnel, sleep=time.sleep, timeout: float | None = None) -> bool:
	deadline = time.monotonic() + (CONNECT_TIMEOUT_SECONDS if timeout is None else timeout)

	while True:
		if tunnel_is_up(tunnel):
			return True

		if time.monotonic() >= deadline:
			return False

		sleep(1)


def ip_check_snippet() -> str:
	"""Python that prints the address a server sees us from. VPN_IP_CHECK_URL replaces the service asked."""
	url = os.environ.get("VPN_IP_CHECK_URL", DEFAULT_IP_CHECK_URL)

	return f"import urllib.request;print(urllib.request.urlopen({url!r}, timeout=10).read().decode())"


def exit_address(tunnel: Tunnel) -> str | None:
	"""The public address the world sees this account from, asked through its tunnel."""
	result = _run(ns_cmd(tunnel.netns, sys.executable, "-c", ip_check_snippet()))
	address = result.stdout.strip()

	try:
		return str(ipaddress.ip_address(address)) if result.returncode == 0 else None
	except ValueError:
		return None


def blacklist() -> set[str]:
	return {a.strip() for a in os.environ.get("HOME_IP_BLACKLIST", "").split(",") if a.strip()}


def check_exit(tunnel: Tunnel, taken: dict[str, str]) -> str:
	"""An empty string if this tunnel's exit is acceptable, otherwise why not.

	`taken` maps exit addresses already in use to the account using them. Two
	accounts on one exit address defeat the point of separate tunnels, and an exit
	that is the real home address is a leak, so both are refused. An exit that
	cannot be read is refused too.
	"""
	address = exit_address(tunnel)

	if address is None:
		return "cannot read the exit address, so cannot rule out a leak"

	if address in blacklist():
		return f"the exit address {address} is one that must never be used"

	other = taken.get(address)

	if other and other != tunnel.account:
		return f"the exit address {address} is already used by {other}; each account needs its own location"

	tunnel.exit = address
	return ""


def stop(process) -> None:
	if process is None or process.poll() is not None:
		return

	process.terminate()

	try:
		process.wait(timeout=STOP_TIMEOUT_SECONDS)
	except subprocess.TimeoutExpired:
		process.kill()


def bring_up(tunnel: Tunnel, taken: dict[str, str], popen=subprocess.Popen, sleep=time.sleep) -> bool:
	"""Start (or restart) one account's OpenVPN and judge it. Sets tunnel.up and tunnel.reason."""
	stop(tunnel.vpn)
	tunnel.vpn = start_openvpn(tunnel, popen)
	tunnel.up = False

	if not wait_for_tunnel(tunnel, sleep):
		tunnel.reason = "the tunnel did not come up"
		return False

	tunnel.reason = check_exit(tunnel, taken)

	if tunnel.reason:
		stop(tunnel.vpn)
		return False

	taken[tunnel.exit] = tunnel.account
	tunnel.up = True
	return True


# --- what the schedulers read --------------------------------------------------------


def write_state(tunnels: list[Tunnel], path: Path | None = None) -> None:
	"""Which namespace each account lives in and whether its tunnel is up, for isolation.py."""
	path = path or STATE_FILE
	state = {
		t.account: {"netns": t.netns, "up": t.up, "exit": t.exit, "reason": t.reason, "timezone": timezone_for(t)}
		for t in tunnels
	}

	path.parent.mkdir(parents=True, exist_ok=True)
	temporary = path.with_suffix(".tmp")
	temporary.write_text(json.dumps(state))
	os.replace(temporary, path)


def timezone_for(tunnel: Tunnel) -> str:
	"""The timezone to give this account's browser, from its optional `timezone` file."""
	try:
		return (tunnel.folder / "timezone").read_text().strip()
	except OSError:
		return ""


def tell(tunnel: Tunnel, title: str, message: str, priority: str = "default") -> None:
	notify.send(title, f"{tunnel.account}: {message}", priority=priority, account=tunnel.account)


# --- the supervisor ------------------------------------------------------------------


def supervise_tunnels(tunnels: list[Tunnel], taken: dict[str, str], now: float, popen=subprocess.Popen, sleep=time.sleep) -> bool:
	"""One pass over the tunnels: notice a dead one and restart it. Returns False when one will not come back."""
	changed = False

	for tunnel in tunnels:
		alive = tunnel.vpn is not None and tunnel.vpn.poll() is None and tunnel_is_up(tunnel)

		if alive:
			tunnel.failed_restarts = 0
			continue

		if tunnel.up:
			taken.pop(tunnel.exit, None)
			tunnel.up = False
			changed = True
			logger.error("[VPN] %s: the tunnel died. Its runs are held until it is back.", tunnel.account)
			tell(tunnel, "VPN tunnel down", "the tunnel dropped. Runs for this account are held and nothing leaves on the real connection.", "high")

			# Said now, not after the restart: a restart can take a minute, and until
			# the state says the account is down the schedulers would still run it.
			write_state(tunnels)

		if now - tunnel.last_restart < RESTART_BACKOFF_SECONDS:
			continue

		tunnel.last_restart = now
		was = tunnel.reason

		if bring_up(tunnel, taken, popen, sleep):
			tunnel.failed_restarts = 0
			changed = True
			logger.info("[VPN] %s: tunnel up again via %s", tunnel.account, tunnel.exit)
			tell(tunnel, "VPN tunnel restored", f"back up, exit {tunnel.exit}.")
		else:
			tunnel.failed_restarts += 1
			changed = changed or tunnel.reason != was
			logger.error("[VPN] %s: restart %d failed: %s", tunnel.account, tunnel.failed_restarts, tunnel.reason)

			if tunnel.failed_restarts >= MAX_FAILED_RESTARTS:
				tell(tunnel, "VPN tunnel will not come back", f"{tunnel.failed_restarts} restarts failed ({tunnel.reason}). Restarting the container.", "high")
				return False

	return True


def teardown(tunnels: list[Tunnel], commands: list) -> None:
	for command in commands:
		stop(command)

	for tunnel in tunnels:
		stop(tunnel.vpn)
		stop(tunnel.holder)

		if tunnel.workdir:
			shutil.rmtree(tunnel.workdir, ignore_errors=True)

	try:
		STATE_FILE.unlink()
	except OSError:
		pass


def run(accounts: list[str], commands: list[list[str]], popen=subprocess.Popen, sleep=time.sleep, handle_signals: bool = True, clock=time.monotonic) -> int:
	"""Build every account's tunnel, run the commands, and stop when any of them or a tunnel gives up. Returns the exit code."""
	try:
		tunnels = plan(accounts)
	except (RuntimeError, ValueError, OSError) as err:
		print(f"vpn: {err}", file=sys.stderr)
		return 1

	try:
		write_resolv_conf()
	except (ValueError, OSError) as err:
		print(f"vpn: {err}", file=sys.stderr)
		teardown(tunnels, [])
		return 1

	running: list = []

	try:
		for tunnel in tunnels:
			create_namespace(tunnel, popen, sleep)
			namespace_kill_switch(tunnel)

		host_rules(tunnels)

		taken: dict[str, str] = {}

		for tunnel in tunnels:
			if bring_up(tunnel, taken, popen, sleep):
				print(f"vpn: {tunnel.account} tunnel up, exit {tunnel.exit}")
			else:
				# Not fatal: the account is held and retried, the others carry on.
				print(f"vpn: {tunnel.account} is held: {tunnel.reason}", file=sys.stderr)
				tell(tunnel, "VPN tunnel not up", f"{tunnel.reason}. Runs for this account are held.", "high")

		if not any(t.up for t in tunnels):
			print("vpn: no tunnel came up; stopping", file=sys.stderr)
			teardown(tunnels, running)
			return 1

		write_state(tunnels)

		# From here on the schedulers must find the namespaces or run nothing.
		os.environ[isolation.REQUIRED_ENV] = "1"

		# The container's own namespace, for the children to compare theirs against: they
		# have no rights to read it themselves (isolation.py).
		try:
			os.environ[isolation.ROOT_NETNS_ENV] = os.readlink(isolation.OWN_NETNS)
		except OSError:
			pass

		running = [popen(command) for command in commands]
	except (subprocess.CalledProcessError, OSError, RuntimeError) as err:
		detail = (getattr(err, "stderr", "") or "").strip() or str(err)
		print(f"vpn: setup failed: {detail}", file=sys.stderr)
		teardown(tunnels, running)
		return 1

	stopping: list[int] = []

	if handle_signals:
		def on_signal(signum, frame):
			stopping.append(signum)

		signal.signal(signal.SIGTERM, on_signal)
		signal.signal(signal.SIGINT, on_signal)

	while True:
		for command in running:
			code = command.poll()

			if code is not None:
				teardown(tunnels, running)
				return code

		if stopping:
			teardown(tunnels, running)
			return 128 + stopping[0]

		if not supervise_tunnels(tunnels, taken, clock(), popen, sleep):
			teardown(tunnels, running)
			return 1

		write_state(tunnels)
		sleep(CHECK_INTERVAL_SECONDS)


def split_commands(args: list[str]) -> list[list[str]]:
	"""`a b -- c d` into [[a, b], [c, d]]."""
	groups: list[list[str]] = [[]]

	for arg in args:
		if arg == "--":
			groups.append([])
		else:
			groups[-1].append(arg)

	return [g for g in groups if g]


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%H:%M:%S")
	names = [a.strip() for a in os.environ.get("REWARDS_ACCOUNTS", "").split(",") if a.strip()] or ["default"]
	groups = split_commands(sys.argv[2:] if sys.argv[1:2] == ["run"] else [])

	if not groups:
		print("usage: vpn_config.py run -- <command> [args...] [-- <command> ...]", file=sys.stderr)
		sys.exit(2)

	sys.exit(run(names, groups))
