"""One tunnel and one kill switch per account: nothing may leak, and one account may not touch another's."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import vpn_config as v

ALPHA = "client\ndev tun\nproto udp\nremote alpha.example.net 1194\nremote-cert-tls server\n"
BETA = "client\ndev tun\nproto tcp\nremote beta.example.net 443\nremote-cert-tls server\n"

# The shape of a Surfshark config: a login prompt, and ping-restart 0.
SURFSHARK_LIKE = """client
dev tun
proto udp
remote alpha.example.net 1194
resolv-retry infinite
remote-random
nobind
persist-key
persist-tun
ping 15
ping-restart 0
ping-timer-rem
reneg-sec 0
remote-cert-tls server
auth-user-pass
verb 3
pull
fast-io
cipher AES-256-CBC
auth SHA512
"""

# Surfshark-shaped, with the certificate material inline, and what it must still be allowed to do.
SURFSHARK_INLINE = """client
dev tun
proto udp
remote alpha.example.net 1194
nobind
ping 15
ping-restart 0
reneg-sec 0
remote-cert-tls server
auth-user-pass
verb 3
fast-io
cipher AES-256-CBC
auth SHA512
key-direction 1
<ca>
-----BEGIN CERTIFICATE-----
up /bin/evil
-----END CERTIFICATE-----
</ca>
<tls-auth>
-----BEGIN OpenVPN Static key V1-----
down /bin/evil
-----END OpenVPN Static key V1-----
</tls-auth>
"""

ADDRESSES = {"alpha.example.net": ["198.51.100.4", "198.51.100.5"], "beta.example.net": ["203.0.113.9"], "203.0.113.9": ["203.0.113.9"]}


def fake_getaddrinfo(host, port, *args, **kwargs):
	if host not in ADDRESSES:
		import socket

		raise socket.gaierror("no such host")

	return [(2, 1, 6, "", (ip, port or 0)) for ip in ADDRESSES[host]]


class FakeProcess:
	"""Stands in for a child process."""

	pids = iter(range(1000, 9999))

	def __init__(self, args, exit_after=None, code=7):
		self.args = list(args)
		self.pid = next(self.pids)
		self.exit_after = exit_after
		self.code = code
		self.returncode = None
		self.polls = 0
		self.terminated = False

	def poll(self):
		self.polls += 1

		if self.returncode is None and self.exit_after is not None and self.polls > self.exit_after:
			self.returncode = self.code

		return self.returncode

	def terminate(self):
		self.terminated = True
		self.returncode = -15

	def kill(self):
		self.returncode = -9

	def wait(self, timeout=None):
		return self.returncode


class FakeSystem:
	"""Records every command and answers the ones the supervisor asks."""

	def __init__(self):
		self.commands = []
		self.tun_up = True
		self.exits = {}  # netns path -> exit address
		self.processes = []
		self.command_exit_after = None
		self.events = []  # commands and processes in the order they happened
		self.ipv6_policy = "DROP"

	def run(self, command, **kwargs):
		self.commands.append(list(command))
		self.events.append(("run", list(command)))
		code, text = 0, ""

		if command[0] == "nsenter":
			inner = command[2:]

			if inner[:3] == ["ip", "-o", "link"]:
				code, text = (0, "4: tun0: <UP> state UNKNOWN") if self.tun_up else (1, "")
			elif inner[:2] == ["ip6tables", "-S"]:
				text = "".join(f"-P {chain} {self.ipv6_policy}\n" for chain in ("INPUT", "FORWARD", "OUTPUT"))
			elif inner and inner[0] == sys.executable:
				address = self.exits.get(command[1].split("=", 1)[1])
				code, text = (0, address) if address else (1, "")

		return SimpleNamespace(returncode=code, stdout=text, stderr="")

	def popen(self, args, **kwargs):
		is_command = args[0] not in ("nsenter", "unshare")
		process = FakeProcess(args, exit_after=self.command_exit_after if is_command else None)
		self.processes.append(process)
		self.events.append(("popen", list(args)))

		return process

	def of(self, first):
		return [p for p in self.processes if p.args[0] == first]

	def openvpn(self):
		return [p for p in self.processes if "openvpn" in p.args]

	def ran(self, *prefix):
		return [c for c in self.commands if c[: len(prefix)] == list(prefix)]


class VpnTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.root = Path(directory.name)
		self.system = FakeSystem()

		for patcher in (
			mock.patch.object(v, "DATA_DIR", self.root),
			mock.patch.object(v, "LOG_DIR", self.root / "logs"),
			mock.patch.object(v, "STATE_FILE", self.root / "state" / "namespaces.json"),
			mock.patch.object(v, "RESOLV_CONF", self.root / "resolv.conf"),
			mock.patch.object(v.socket, "getaddrinfo", side_effect=fake_getaddrinfo),
			mock.patch.object(v.tempfile, "gettempdir", return_value=str(self.root)),
			mock.patch.object(v.subprocess, "run", side_effect=self.system.run),
			mock.patch.object(v, "namespace_ready", return_value=True),
			mock.patch.dict(os.environ, {"HOME_IP_BLACKLIST": "", "VPN_OPTIONS": ""}),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

		alert = mock.patch.object(v.notify, "send")
		self.alert = alert.start()
		self.addCleanup(alert.stop)
		os.environ.pop("NAME_SERVERS", None)

		for number, account in enumerate(("alpha", "beta"), start=1):
			self.put_config(account, ALPHA if account == "alpha" else BETA)

	def put_config(self, account, text, auth=None, timezone=None):
		folder = self.root / account / "openvpn"
		folder.mkdir(parents=True, exist_ok=True)
		(folder / "config.ovpn").write_text(text)

		if auth is not None:
			(folder / "auth.txt").write_text(auth)

		if timezone is not None:
			(folder / "timezone").write_text(timezone + "\n")

	def tunnels(self, *accounts):
		return v.plan(list(accounts or ("alpha", "beta")))

	def ready(self, tunnels):
		"""Tunnels as they are once their namespaces exist, with the fake exit addresses set."""
		for tunnel in tunnels:
			tunnel.netns = f"/proc/{5000 + tunnel.number}/ns/net"
			self.system.exits.setdefault(tunnel.netns, f"192.0.2.{tunnel.number}")

		return tunnels


class TestReadingTheConfig(VpnTestCase):
	def test_every_server_and_address_is_found_with_its_port_and_protocol(self):
		self.assertEqual(
			v.parse_endpoints(ALPHA),
			[v.Endpoint("198.51.100.4", "1194", "udp"), v.Endpoint("198.51.100.5", "1194", "udp")],
		)

	def test_the_configs_own_port_and_protocol_are_the_defaults(self):
		self.assertEqual(v.parse_endpoints("proto tcp\nport 8443\nremote 203.0.113.9\n"), [v.Endpoint("203.0.113.9", "8443", "tcp")])

	def test_a_config_with_no_server_is_an_error(self):
		with self.assertRaises(RuntimeError):
			v.parse_endpoints("client\ndev tun\n")

	def test_a_name_that_does_not_resolve_is_an_error_not_a_hole_in_the_firewall(self):
		with self.assertRaises(RuntimeError):
			v.parse_endpoints("remote nowhere.invalid 1194\n")

	def test_names_become_addresses_and_nothing_else_changes(self):
		pinned = v.pin_remotes(ALPHA)

		self.assertIn("remote 198.51.100.4 1194", pinned)
		self.assertNotIn("alpha.example.net", pinned)
		self.assertIn("remote-cert-tls server", pinned)

	def test_the_resolvers_default_and_can_be_set(self):
		path = self.root / "r.conf"
		v.write_resolv_conf(path)
		self.assertEqual(path.read_text(), "nameserver 1.1.1.1\nnameserver 1.0.0.1\n")

		with mock.patch.dict(os.environ, {"NAME_SERVERS": "9.9.9.9, 149.112.112.112"}):
			v.write_resolv_conf(path)

		self.assertEqual(path.read_text(), "nameserver 9.9.9.9\nnameserver 149.112.112.112\n")

	def test_a_resolver_that_is_not_an_address_is_refused(self):
		with mock.patch.dict(os.environ, {"NAME_SERVERS": "1.1.1.1; rm -rf /"}), self.assertRaises(ValueError):
			v.write_resolv_conf(self.root / "r.conf")


class TestPlan(VpnTestCase):
	def test_each_account_gets_its_own_number_and_subnet(self):
		alpha, beta = self.tunnels()

		self.assertEqual((alpha.number, beta.number), (1, 2))
		self.assertEqual((alpha.subnet, beta.subnet), ("10.200.1.0/30", "10.200.2.0/30"))
		self.assertEqual((alpha.host_if, alpha.ns_if), ("vh1", "vn1"))
		self.assertNotEqual(alpha.host_ip, beta.host_ip)

	def test_an_account_without_a_config_stops_everything_rather_than_running_unprotected(self):
		with self.assertRaisesRegex(RuntimeError, "gamma"):
			v.plan(["alpha", "gamma"])

	def test_a_config_that_asks_for_a_login_needs_an_auth_file_rather_than_hanging(self):
		self.put_config("alpha", SURFSHARK_LIKE)

		with self.assertRaisesRegex(RuntimeError, "auth.txt"):
			v.plan(["alpha"])

	def test_a_config_that_names_its_own_login_file_needs_no_auth_file(self):
		self.put_config("alpha", SURFSHARK_LIKE.replace("auth-user-pass\n", "auth-user-pass /somewhere/creds\n"))

		self.assertEqual(len(v.plan(["alpha"])), 1)

	def test_a_name_that_could_walk_out_of_the_data_folder_is_refused(self):
		for name in ("../escape", "a/b", "..", "x.", "a b", ""):
			with self.assertRaises(RuntimeError, msg=name):
				v.plan([name])

	def test_the_supervisor_makes_the_namespaces_mandatory_for_what_it_starts(self):
		os.environ.pop("REWARDS_VPN_REQUIRED", None)
		self.addCleanup(os.environ.pop, "REWARDS_VPN_REQUIRED", None)
		self.system.command_exit_after = 0
		TestRun.run_it(self)

		self.assertEqual(os.environ.get("REWARDS_VPN_REQUIRED"), "1")

	def test_no_accounts_is_an_error(self):
		with self.assertRaises(RuntimeError):
			v.plan([])

	def test_the_pinned_copy_is_private_and_not_left_in_the_data_folder(self):
		alpha, _ = self.tunnels()

		if os.name != "nt":
			self.assertEqual(alpha.pinned.stat().st_mode & 0o777, 0o600)
			self.assertEqual(alpha.workdir.stat().st_mode & 0o777, 0o700)

		self.assertEqual(alpha.pinned.parent, alpha.workdir)
		self.assertTrue(alpha.workdir.name.startswith("vpn-"))
		self.assertIn("remote 198.51.100.4", alpha.pinned.read_text())
		self.assertEqual(sorted(p.name for p in alpha.folder.iterdir()), ["config.ovpn"])


class TestPinnedCopy(VpnTestCase):
	def test_each_account_gets_its_own_private_directory_and_it_is_removed_at_teardown(self):
		alpha, beta = self.tunnels()

		self.assertNotEqual(alpha.workdir, beta.workdir)
		self.assertTrue(alpha.pinned.is_file())

		v.teardown([alpha, beta], [])

		self.assertFalse(alpha.workdir.exists())
		self.assertFalse(beta.workdir.exists())

	def test_the_file_is_created_exclusively_and_privately(self):
		calls = []
		real = os.open

		def spy(path, flags, mode=0o777, **kw):
			calls.append((str(path), flags, mode))
			return real(path, flags, mode, **kw)

		with mock.patch.object(v.os, "open", side_effect=spy):
			alpha, _ = self.tunnels()

		path, flags, mode = next(c for c in calls if c[0] == str(alpha.pinned))

		self.assertTrue(flags & os.O_EXCL)
		self.assertTrue(flags & os.O_CREAT)
		self.assertEqual(mode, 0o600)

	def test_a_failed_write_leaves_no_directory_behind(self):
		with mock.patch.object(v, "pin_remotes", side_effect=OSError("boom")), self.assertRaises(OSError):
			self.tunnels("alpha")

		self.assertEqual([p.name for p in self.root.iterdir() if p.name.startswith("vpn-")], [])


class TestForbiddenDirectives(VpnTestCase):
	def test_every_listed_directive_is_refused_with_its_name(self):
		for directive in sorted(v.FORBIDDEN_DIRECTIVES):
			for text in (f"{directive} /bin/x", f"  {directive.upper()}\t/bin/x"):
				self.put_config("alpha", ALPHA + text + "\n")

				with self.subTest(text), self.assertRaisesRegex(RuntimeError, repr(directive)):
					v.plan(["alpha"])

	def test_the_list_is_the_one_the_review_asked_for(self):
		self.assertEqual(v.FORBIDDEN_DIRECTIVES, {
			"up", "down", "route-up", "route-pre-down", "ipchange", "plugin", "management", "tls-verify",
			"client-connect", "client-disconnect", "learn-address", "auth-user-pass-verify", "up-restart",
			"log", "log-append", "status", "writepid", "chroot", "daemon", "cd", "script-security",
		})

	def test_comments_are_ignored(self):
		self.put_config("alpha", ALPHA + "# up /bin/x\n; script-security 2\n")

		self.assertEqual(len(v.plan(["alpha"])), 1)

	def test_a_directive_that_only_starts_with_a_listed_word_is_fine(self):
		self.put_config("alpha", ALPHA + "up-delay\nlogin\nstatus-version 2\n")

		self.assertEqual(len(v.plan(["alpha"])), 1)

	def test_a_directive_inside_an_inline_key_block_is_not_one(self):
		self.put_config("alpha", ALPHA + "<key>\nup /bin/x\n</key>\n")

		self.assertEqual(len(v.plan(["alpha"])), 1)

	def test_a_directive_after_an_inline_block_is_still_caught(self):
		self.put_config("alpha", ALPHA + "<ca>\nabc\n</ca>\nscript-security 2\n")

		with self.assertRaisesRegex(RuntimeError, "script-security"):
			v.plan(["alpha"])

	def test_a_connection_blocks_inner_lines_are_scanned(self):
		self.put_config("alpha", ALPHA + "<connection>\nremote alpha.example.net 1194\nup /bin/x\n</connection>\n")

		with self.assertRaisesRegex(RuntimeError, "'up'"):
			v.plan(["alpha"])

	def test_a_surfshark_shaped_config_is_still_accepted(self):
		self.put_config("alpha", SURFSHARK_INLINE, auth="user\npass\n")
		(alpha,) = v.plan(["alpha"])

		self.assertEqual(alpha.endpoints[0].port, "1194")
		self.assertIn("<tls-auth>", alpha.pinned.read_text())


class TestEndpointAddresses(VpnTestCase):
	def refused(self, ip):
		with mock.patch.dict(ADDRESSES, {"x.example.net": [ip]}), self.assertRaisesRegex(RuntimeError, "not a public address"):
			v.parse_endpoints("remote x.example.net 1194\n")

	def test_loopback_private_shared_and_special_addresses_are_refused(self):
		for ip in (
			"127.0.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0", "10.0.0.1", "10.200.1.2",
			"172.16.0.1", "172.31.255.254", "192.168.1.1", "100.64.0.1", "100.127.255.254",
		):
			with self.subTest(ip=ip):
				self.refused(ip)

	def test_a_literal_address_is_checked_too(self):
		with mock.patch.dict(ADDRESSES, {"10.0.0.5": ["10.0.0.5"]}), self.assertRaises(RuntimeError):
			v.parse_endpoints("remote 10.0.0.5 1194\n")

	def test_documentation_ranges_and_ordinary_addresses_are_accepted(self):
		for ip in ("198.51.100.4", "203.0.113.9", "172.32.0.1", "100.128.0.1", "8.8.8.8"):
			with self.subTest(ip=ip), mock.patch.dict(ADDRESSES, {"x.example.net": [ip]}):
				self.assertEqual(v.parse_endpoints("remote x.example.net 1194\n")[0].ip, ip)


class TestAccountNames(VpnTestCase):
	def test_a_name_with_a_trailing_newline_is_refused(self):
		for name in ("alpha\n", "second\n"):
			with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, "not usable"):
				v.plan([name])


class TestNamespace(VpnTestCase):
	def test_a_namespace_is_built_and_joined_to_the_container(self):
		alpha, _ = self.tunnels()
		v.create_namespace(alpha, self.system.popen, lambda s: None)
		pid = alpha.holder.pid

		self.assertEqual(alpha.holder.args, ["unshare", "-n", "sleep", "infinity"])
		self.assertEqual(alpha.netns, f"/proc/{pid}/ns/net")
		self.assertIn(["ip", "link", "add", "vh1", "type", "veth", "peer", "name", "vn1"], self.system.commands)
		self.assertIn(["ip", "link", "set", "vn1", "netns", str(pid)], self.system.commands)
		self.assertIn(["nsenter", f"--net={alpha.netns}", "ip", "route", "add", "default", "via", "10.200.1.1"], self.system.commands)

	def test_nothing_is_configured_until_the_namespace_really_is_separate(self):
		alpha, _ = self.tunnels()

		with mock.patch.object(v, "namespace_ready", return_value=False), self.assertRaises(OSError):
			v.create_namespace(alpha, self.system.popen, lambda s: None)

		self.assertEqual(self.system.ran("ip", "link", "add"), [])

	def test_ipv6_is_closed_in_the_namespace(self):
		alpha, _ = self.tunnels()
		v.create_namespace(alpha, self.system.popen, lambda s: None)
		inside = [c[2:] for c in self.system.commands if c[:1] == ["nsenter"] and c[2] == "ip6tables"]

		for chain in ("INPUT", "OUTPUT", "FORWARD"):
			self.assertIn(["ip6tables", "-P", chain, "DROP"], inside)

	def test_a_namespace_whose_ipv6_is_still_open_is_refused(self):
		alpha, _ = self.tunnels()

		for policy in ("ACCEPT", "DROPX"):
			self.system.ipv6_policy = policy

			with self.subTest(policy=policy), self.assertRaisesRegex(RuntimeError, "IPv6 is not closed"):
				v.create_namespace(alpha, self.system.popen, lambda s: None)

	def test_one_open_chain_is_enough_to_refuse(self):
		alpha, _ = self.tunnels()
		real = self.system.run

		def run(command, **kw):
			result = real(command, **kw)

			if command[2:4] == ["ip6tables", "-S"]:
				result.stdout = result.stdout.replace("-P FORWARD DROP", "-P FORWARD ACCEPT")

			return result

		with mock.patch.object(v.subprocess, "run", side_effect=run), self.assertRaisesRegex(RuntimeError, "IPv6 is not closed"):
			v.create_namespace(alpha, self.system.popen, lambda s: None)

	def test_a_failing_sysctl_is_still_tolerated_when_the_chains_are_closed(self):
		alpha, _ = self.tunnels()
		real = self.system.run

		def run(command, **kw):
			if command[2:3] == ["sysctl"]:
				return SimpleNamespace(returncode=255, stdout="", stderr="no ipv6")

			return real(command, **kw)

		with mock.patch.object(v.subprocess, "run", side_effect=run):
			v.create_namespace(alpha, self.system.popen, lambda s: None)

	def test_the_supervisor_stops_cleanly_when_ipv6_cannot_be_closed(self):
		self.system.ipv6_policy = "ACCEPT"

		self.assertEqual(TestRun.run_it(self), 1)
		self.assertEqual(self.system.of("daily"), [])


class TestKillSwitch(VpnTestCase):
	def rules(self, tunnel):
		self.system.commands.clear()
		v.namespace_kill_switch(tunnel)

		return [c for c in self.system.commands if c[0] == "nsenter" and c[2] == "iptables"]

	def test_every_rule_is_applied_inside_the_accounts_own_namespace(self):
		alpha, beta = self.ready(self.tunnels())

		for tunnel in (alpha, beta):
			for command in self.rules(tunnel):
				self.assertEqual(command[1], f"--net={tunnel.netns}")

	def test_everything_is_dropped_by_default(self):
		(alpha, _) = self.ready(self.tunnels())
		rules = [c[3:] for c in self.rules(alpha)]

		for chain in ("INPUT", "OUTPUT", "FORWARD"):
			self.assertIn(["-P", chain, "DROP"], rules)

	def test_the_rules_are_flushed_before_anything_is_allowed(self):
		(alpha, _) = self.ready(self.tunnels())
		rules = [c[3:] for c in self.rules(alpha)]

		self.assertEqual(rules[0], ["-F"])
		self.assertLess(rules.index(["-P", "OUTPUT", "DROP"]), rules.index(["-A", "OUTPUT", "-o", "tun0", "-j", "ACCEPT"]))

	def test_only_loopback_and_the_tunnel_are_open_to_everything(self):
		(alpha, _) = self.ready(self.tunnels())
		open_to_all = [c[3:] for c in self.rules(alpha) if c[3] == "-A" and "-d" not in c and "-s" not in c]

		self.assertCountEqual(
			[(r[1], r[2], r[3]) for r in open_to_all],
			[("INPUT", "-i", "lo"), ("OUTPUT", "-o", "lo"), ("INPUT", "-i", "tun0"), ("OUTPUT", "-o", "tun0")],
		)

	def test_the_real_side_may_only_reach_this_accounts_vpn_servers(self):
		alpha, beta = self.ready(self.tunnels())
		out = [c for c in self.rules(alpha) if "OUTPUT" in c and "vn1" in c]

		self.assertEqual(
			sorted((c[c.index("-d") + 1], c[c.index("--dport") + 1], c[c.index("-p") + 1]) for c in out),
			[("198.51.100.4", "1194", "udp"), ("198.51.100.5", "1194", "udp")],
		)

		other = [c for c in self.rules(beta) if "OUTPUT" in c and "vn2" in c]

		self.assertEqual([(c[c.index("-d") + 1], c[c.index("-p") + 1]) for c in other], [("203.0.113.9", "tcp")])

	def test_one_accounts_servers_are_not_reachable_from_anothers_namespace(self):
		alpha, beta = self.ready(self.tunnels())
		beta_rules = " ".join(" ".join(c) for c in self.rules(beta))

		self.assertNotIn("198.51.100", beta_rules)
		self.assertNotIn("vn1", beta_rules)


class TestHostRules(VpnTestCase):
	def rules(self):
		self.system.commands.clear()
		v.host_rules(self.ready(self.tunnels()))

		return [c[1:] for c in self.system.commands if c[0] == "iptables"]

	def test_forwarding_is_dropped_by_default_and_flushed_first(self):
		rules = self.rules()

		self.assertEqual(rules[0], ["-F", "FORWARD"])
		self.assertIn(["-P", "FORWARD", "DROP"], rules)

	def test_each_namespace_is_masqueraded_on_its_own_subnet(self):
		rules = self.rules()

		self.assertIn(["-t", "nat", "-A", "POSTROUTING", "-s", "10.200.1.0/30", "!", "-o", "vh1", "-j", "MASQUERADE"], rules)
		self.assertIn(["-t", "nat", "-A", "POSTROUTING", "-s", "10.200.2.0/30", "!", "-o", "vh2", "-j", "MASQUERADE"], rules)

	def test_a_namespace_may_forward_only_to_its_own_servers(self):
		rules = self.rules()
		forward = [r for r in rules if r[:2] == ["-A", "FORWARD"] and "-i" in r]
		by_interface = {}

		for r in forward:
			by_interface.setdefault(r[r.index("-i") + 1], []).append(r[r.index("-d") + 1])

		self.assertEqual(sorted(by_interface["vh1"]), ["198.51.100.4", "198.51.100.5"])
		self.assertEqual(by_interface["vh2"], ["203.0.113.9"])

	def test_nothing_is_forwarded_back_in_unless_it_is_a_reply_from_a_server(self):
		rules = self.rules()
		back = [r for r in rules if r[:2] == ["-A", "FORWARD"] and "-o" in r]

		self.assertTrue(back)

		for r in back:
			self.assertIn("ESTABLISHED", r)


class TestExit(VpnTestCase):
	def test_an_exit_that_cannot_be_read_is_refused(self):
		alpha, _ = self.tunnels()
		alpha.netns = "/proc/1/ns/net"

		self.assertIn("cannot read", v.check_exit(alpha, {}))

	def test_a_good_exit_is_remembered(self):
		(alpha, _) = self.ready(self.tunnels())

		self.assertEqual(v.check_exit(alpha, {}), "")
		self.assertEqual(alpha.exit, "192.0.2.1")

	def test_the_home_address_is_refused(self):
		(alpha, _) = self.ready(self.tunnels())

		with mock.patch.dict(os.environ, {"HOME_IP_BLACKLIST": "10.0.0.1, 192.0.2.1"}):
			self.assertIn("must never be used", v.check_exit(alpha, {}))

	def test_two_accounts_may_not_share_an_exit(self):
		alpha, beta = self.ready(self.tunnels())
		self.system.exits[beta.netns] = "192.0.2.1"

		self.assertIn("already used by alpha", v.check_exit(beta, {"192.0.2.1": "alpha"}))

	def test_an_account_may_keep_its_own_exit_when_it_restarts(self):
		(alpha, _) = self.ready(self.tunnels())

		self.assertEqual(v.check_exit(alpha, {"192.0.2.1": "alpha"}), "")


class TestIpCheck(unittest.TestCase):
	def test_the_service_asked_can_be_replaced(self):
		with mock.patch.dict(os.environ, {"VPN_IP_CHECK_URL": "https://ifconfig.me/ip"}):
			self.assertIn("'https://ifconfig.me/ip'", v.ip_check_snippet())

		with mock.patch.dict(os.environ, {}, clear=False) as env:
			env.pop("VPN_IP_CHECK_URL", None)
			self.assertIn("api.ipify.org", v.ip_check_snippet())

	def test_the_snippet_is_valid_python(self):
		compile(v.ip_check_snippet(), "snippet", "exec")


class TestOpenvpn(VpnTestCase):
	def command(self, tunnel):
		return v.start_openvpn(tunnel, self.system.popen).args

	def test_it_runs_inside_the_accounts_namespace_on_its_own_log(self):
		(alpha, _) = self.ready(self.tunnels())
		command = self.command(alpha)

		self.assertEqual(command[:2], ["nsenter", f"--net={alpha.netns}"])
		self.assertEqual(command[2], "openvpn")
		self.assertEqual(command[command.index("--dev") + 1], "tun0")
		self.assertIn("--auth-nocache", command)
		self.assertTrue(command[command.index("--log-append") + 1].endswith("openvpn-alpha.log"))

	def test_user_scripts_are_disabled(self):
		(alpha, _) = self.ready(self.tunnels())
		command = self.command(alpha)

		self.assertEqual(command[command.index("--script-security") + 1], "1")

	def test_it_is_always_told_to_give_up_on_a_dead_tunnel(self):
		(alpha, _) = self.ready(self.tunnels())
		command = self.command(alpha)

		self.assertEqual(command[command.index("--ping") + 1], "15")
		self.assertEqual(command[command.index("--ping-exit") + 1], "90")

	def test_a_providers_ping_restart_zero_is_removed_so_a_dead_tunnel_is_still_noticed(self):
		self.put_config("alpha", SURFSHARK_LIKE, auth="user\npass\n")
		(alpha,) = self.ready(self.tunnels("alpha"))
		kept = alpha.pinned.read_text()

		self.assertNotIn("ping-restart", kept)
		self.assertNotIn("ping-exit", kept)
		self.assertIn("remote-cert-tls server", kept)
		self.assertIn("--ping-exit", self.command(alpha))

	def test_a_login_file_and_extra_options_are_passed_on(self):
		self.put_config("alpha", ALPHA, auth="user\npass\n")
		(alpha, _) = self.ready(self.tunnels())

		with mock.patch.dict(os.environ, {"VPN_OPTIONS": "--verb 4 --mute 20"}):
			command = self.command(alpha)

		self.assertEqual(command[command.index("--auth-user-pass") + 1], str(alpha.folder / "auth.txt"))
		self.assertEqual(command[-4:], ["--verb", "4", "--mute", "20"])


class TestBringUp(VpnTestCase):
	def test_a_tunnel_that_comes_up_with_an_acceptable_exit_is_up(self):
		(alpha, _) = self.ready(self.tunnels())
		taken = {}

		self.assertTrue(v.bring_up(alpha, taken, self.system.popen, lambda s: None))
		self.assertTrue(alpha.up)
		self.assertEqual(taken, {"192.0.2.1": "alpha"})

	def test_a_tunnel_that_never_comes_up_is_not_up(self):
		(alpha, _) = self.ready(self.tunnels())
		self.system.tun_up = False

		with mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 0):
			self.assertFalse(v.bring_up(alpha, {}, self.system.popen, lambda s: None))

		self.assertFalse(alpha.up)
		self.assertIn("did not come up", alpha.reason)

	def test_a_tunnel_with_a_leaking_exit_is_stopped_not_used(self):
		(alpha, _) = self.ready(self.tunnels())

		with mock.patch.dict(os.environ, {"HOME_IP_BLACKLIST": "192.0.2.1"}):
			self.assertFalse(v.bring_up(alpha, {}, self.system.popen, lambda s: None))

		self.assertFalse(alpha.up)
		self.assertTrue(alpha.vpn.terminated)


class TestState(VpnTestCase):
	def test_the_state_says_where_each_account_is_and_whether_it_may_run(self):
		self.put_config("beta", BETA, timezone="America/Chicago")
		alpha, beta = self.ready(self.tunnels())
		alpha.up, alpha.exit = True, "192.0.2.1"
		beta.reason = "the tunnel did not come up"

		v.write_state([alpha, beta])
		state = json.loads(v.STATE_FILE.read_text())

		self.assertEqual(state["alpha"]["netns"], alpha.netns)
		self.assertTrue(state["alpha"]["up"])
		self.assertFalse(state["beta"]["up"])
		self.assertEqual(state["beta"]["timezone"], "America/Chicago")
		self.assertEqual(state["alpha"]["timezone"], "")

	def test_it_is_replaced_whole_never_half_written(self):
		alpha, beta = self.ready(self.tunnels())
		v.write_state([alpha, beta])
		v.write_state([alpha])

		self.assertEqual(list(json.loads(v.STATE_FILE.read_text())), ["alpha"])
		self.assertEqual([p.name for p in v.STATE_FILE.parent.iterdir()], ["namespaces.json"])


class TestSupervising(VpnTestCase):
	def alive(self):
		tunnels = self.ready(self.tunnels())
		taken = {}

		for tunnel in tunnels:
			v.bring_up(tunnel, taken, self.system.popen, lambda s: None)

		self.alert.reset_mock()

		return tunnels, taken

	def test_healthy_tunnels_are_left_alone(self):
		tunnels, taken = self.alive()
		before = len(self.system.processes)

		self.assertTrue(v.supervise_tunnels(tunnels, taken, 1000.0, self.system.popen, lambda s: None))
		self.assertEqual(len(self.system.processes), before)
		self.alert.assert_not_called()

	def test_a_dead_tunnel_is_noticed_alerted_and_restarted(self):
		tunnels, taken = self.alive()
		alpha, beta = tunnels
		alpha.vpn.returncode = 1

		self.assertTrue(v.supervise_tunnels(tunnels, taken, 1000.0, self.system.popen, lambda s: None))

		titles = [c.args[0] for c in self.alert.call_args_list]

		self.assertEqual(titles, ["VPN tunnel down", "VPN tunnel restored"])
		self.assertEqual({c.kwargs["account"] for c in self.alert.call_args_list}, {"alpha"})
		self.assertTrue(alpha.up)
		self.assertTrue(beta.up)
		self.assertEqual(len(self.system.openvpn()), 3)

	def test_the_state_says_the_account_is_down_before_the_restart_not_after(self):
		tunnels, taken = self.alive()
		alpha, _ = tunnels
		v.write_state(tunnels)
		self.assertTrue(json.loads(v.STATE_FILE.read_text())["alpha"]["up"])
		alpha.vpn.returncode = 1
		seen = []

		def restart(tunnel, taken, popen, sleep):
			seen.append(json.loads(v.STATE_FILE.read_text())["alpha"]["up"])

			return False

		with mock.patch.object(v, "bring_up", side_effect=restart):
			v.supervise_tunnels(tunnels, taken, 1000.0, self.system.popen, lambda s: None)

		self.assertEqual(seen, [False])

	def test_restarts_are_not_hammered(self):
		tunnels, taken = self.alive()
		alpha, _ = tunnels
		alpha.vpn.returncode = 1
		self.system.tun_up = False

		with mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 0):
			v.supervise_tunnels(tunnels, taken, 1000.0, self.system.popen, lambda s: None)
			count = len(self.system.openvpn())
			v.supervise_tunnels(tunnels, taken, 1005.0, self.system.popen, lambda s: None)

		self.assertEqual(len(self.system.openvpn()), count)

	def test_an_account_that_will_not_come_back_ends_the_supervisor_so_docker_restarts_it(self):
		tunnels, taken = self.alive()
		alpha, _ = tunnels
		alpha.vpn.returncode = 1
		self.system.tun_up = False
		result = True

		with mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 0):
			for attempt in range(v.MAX_FAILED_RESTARTS):
				result = v.supervise_tunnels(tunnels, taken, 1000.0 + attempt * 100, self.system.popen, lambda s: None)

		self.assertFalse(result)
		self.assertIn("VPN tunnel will not come back", [c.args[0] for c in self.alert.call_args_list])

	def test_a_down_tunnel_gives_up_its_exit_address(self):
		tunnels, taken = self.alive()
		alpha, _ = tunnels
		alpha.vpn.returncode = 1
		self.system.tun_up = False

		with mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 0):
			v.supervise_tunnels(tunnels, taken, 1000.0, self.system.popen, lambda s: None)

		self.assertNotIn("192.0.2.1", taken)
		self.assertFalse(alpha.up)


class TestRun(VpnTestCase):
	def run_it(self, accounts=("alpha", "beta"), commands=(["daily"], ["search"]), **kwargs):
		def exits(command, **kw):
			return self.system.popen(command, **kw)

		# Each namespace gets the address its number implies, as soon as it exists.
		original = self.system.popen

		def popen(args, **kw):
			process = original(args, **kw)

			if args[:1] == ["unshare"]:
				number = len([p for p in self.system.processes if p.args[:1] == ["unshare"]])
				self.system.exits.setdefault(f"/proc/{process.pid}/ns/net", f"192.0.2.{number}")

			return process

		with mock.patch.object(v, "CHECK_INTERVAL_SECONDS", 0):
			return v.run(list(accounts), [list(c) for c in commands], popen=popen, sleep=lambda s: None, handle_signals=False, clock=iter(range(0, 10000, 100)).__next__, **kwargs)

	def test_a_missing_config_stops_before_anything_is_touched(self):
		(self.root / "beta" / "openvpn" / "config.ovpn").unlink()

		self.assertEqual(self.run_it(), 1)
		self.assertEqual(self.system.commands, [])

	def test_each_firewall_closes_before_its_openvpn_starts(self):
		self.system.command_exit_after = 1
		self.run_it()
		events = self.system.events

		for number in (1, 2):
			closed = next(i for i, (kind, c) in enumerate(events) if kind == "run" and c[:1] == ["nsenter"] and c[2] == "iptables" and c[3:5] == ["-P", "OUTPUT"] and self._ns_number(c[1]) == number)
			started = next(i for i, (kind, c) in enumerate(events) if kind == "popen" and "openvpn" in c and self._ns_number(c[1]) == number)

			self.assertLess(closed, started, f"account {number}: OpenVPN started before its firewall was closed")

	def _ns_number(self, option):
		"""Which account a --net=/proc/<pid>/ns/net option belongs to, by the order the holders were started."""
		pid = int(option.split("/")[2])
		holders = [p.pid for p in self.system.processes if p.args[:1] == ["unshare"]]

		return holders.index(pid) + 1

	def test_the_commands_start_after_the_tunnels_and_the_state_is_written(self):
		self.system.command_exit_after = 0
		code = self.run_it()

		order = [p.args[0] for p in self.system.processes]

		self.assertEqual(code, 7)
		self.assertLess(max(i for i, p in enumerate(self.system.processes) if "openvpn" in p.args), order.index("daily"))
		self.assertEqual(order.count("unshare"), 2)

	def test_the_exit_code_of_a_command_is_the_containers_and_everything_is_torn_down(self):
		self.system.command_exit_after = 1
		code = self.run_it()

		self.assertEqual(code, 7)
		self.assertTrue(all(p.returncode is not None for p in self.system.processes))
		self.assertFalse(v.STATE_FILE.exists())

	def test_one_account_that_cannot_connect_is_held_and_the_rest_carry_on(self):
		self.system.command_exit_after = 1

		with mock.patch.object(v, "check_exit", side_effect=lambda t, taken: "" if t.account == "alpha" else "cannot read the exit address, so cannot rule out a leak"):
			def run_with_exit(*a, **k):
				return None

			code = self.run_it()

		self.assertEqual(code, 7)
		titles = [c.args[0] for c in self.alert.call_args_list]
		self.assertIn("VPN tunnel not up", titles)
		self.assertEqual({c.kwargs["account"] for c in self.alert.call_args_list if c.args[0] == "VPN tunnel not up"}, {"beta"})

	def test_no_tunnel_at_all_stops_the_container(self):
		self.system.tun_up = False

		with mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 0):
			code = self.run_it()

		self.assertEqual(code, 1)
		self.assertEqual(self.system.of("daily"), [])

	def test_a_setup_failure_tears_down_and_stops(self):
		def broken(command, **kwargs):
			if command[:3] == ["ip", "link", "add"]:
				raise v.subprocess.CalledProcessError(2, command, stderr="RTNETLINK answers: Operation not permitted")

			return self.system.run(command, **kwargs)

		with mock.patch.object(v.subprocess, "run", side_effect=broken):
			code = self.run_it()

		self.assertEqual(code, 1)
		self.assertEqual(self.system.of("daily"), [])
		self.assertTrue(all(p.returncode is not None for p in self.system.of("unshare")))

	def test_a_stop_signal_stops_the_commands_and_the_tunnels(self):
		self.system.command_exit_after = None
		handlers = {}

		with mock.patch.object(v.signal, "signal", side_effect=lambda n, h: handlers.__setitem__(n, h)):
			sleeps = iter([lambda: None, lambda: handlers[v.signal.SIGTERM](v.signal.SIGTERM, None)])

			def sleep(seconds):
				next(sleeps, lambda: None)()

			original = self.system.popen

			def popen(args, **kw):
				process = original(args, **kw)

				if args[:1] == ["unshare"]:
					self.system.exits.setdefault(f"/proc/{process.pid}/ns/net", f"192.0.2.{len(self.system.of('unshare'))}")

				return process

			with mock.patch.object(v, "CHECK_INTERVAL_SECONDS", 0):
				code = v.run(["alpha", "beta"], [["daily"]], popen=popen, sleep=sleep, handle_signals=True, clock=iter(range(0, 10000, 100)).__next__)

		self.assertEqual(code, 128 + v.signal.SIGTERM)
		self.assertTrue(all(p.returncode is not None for p in self.system.processes))


class TestSplitCommands(unittest.TestCase):
	def test_groups_are_split_on_double_dash(self):
		self.assertEqual(v.split_commands(["a", "b", "--", "c", "d"]), [["a", "b"], ["c", "d"]])

	def test_empty_groups_are_dropped(self):
		self.assertEqual(v.split_commands(["--", "a", "--", "--"]), [["a"]])
		self.assertEqual(v.split_commands([]), [])


if __name__ == "__main__":
	unittest.main()
