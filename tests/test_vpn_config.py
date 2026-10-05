"""The per-account VPN: the firewall must close before the tunnel opens, and nothing may leak."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import vpn_config as v

CONFIG = """client
dev tun
proto udp
remote vpn.example.net 1194
remote 203.0.113.9 443 tcp
remote-cert-tls server
"""

ADDRESSES = {"vpn.example.net": ["198.51.100.4", "198.51.100.5"], "203.0.113.9": ["203.0.113.9"]}


def fake_getaddrinfo(host, port, *args, **kwargs):
	if host not in ADDRESSES:
		import socket

		raise socket.gaierror("no such host")

	return [(2, 1, 6, "", (ip, port or 0)) for ip in ADDRESSES[host]]


class Recorder:
	"""Stands in for subprocess.run and keeps every command it was given."""

	def __init__(self, tun_up=True):
		self.commands = []
		self.tun_up = tun_up

	def __call__(self, command, **kwargs):
		self.commands.append(list(command))

		if command[:2] == ["ip", "-o"]:
			return mock.Mock(returncode=0 if self.tun_up else 1, stdout="3: tun0: <UP> state UNKNOWN" if self.tun_up else "")

		return mock.Mock(returncode=0, stdout="")

	def iptables(self):
		return [c[1:] for c in self.commands if c[0] == "iptables"]


class VpnTestCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.root = Path(directory.name)

		for patcher in (
			mock.patch.object(v, "DATA_DIR", self.root),
			mock.patch.object(v, "RESOLV_CONF", self.root / "resolv.conf"),
			mock.patch.object(v.socket, "getaddrinfo", side_effect=fake_getaddrinfo),
			mock.patch.object(v.tempfile, "gettempdir", return_value=str(self.root)),
			mock.patch.object(v.time, "sleep"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def put_config(self, account="second", text=CONFIG, auth=None):
		folder = self.root / account / "openvpn"
		folder.mkdir(parents=True)
		(folder / "config.ovpn").write_text(text)

		if auth is not None:
			(folder / "auth.txt").write_text(auth)


class TestEndpoints(VpnTestCase):
	def test_every_server_and_address_is_found_with_its_port_and_protocol(self):
		found = v.parse_endpoints(CONFIG)

		self.assertEqual(
			found,
			[v.Endpoint("198.51.100.4", "1194", "udp"), v.Endpoint("198.51.100.5", "1194", "udp"), v.Endpoint("203.0.113.9", "443", "tcp")],
		)

	def test_the_configs_own_port_and_protocol_are_the_defaults(self):
		found = v.parse_endpoints("proto tcp\nport 8443\nremote 203.0.113.9\n")

		self.assertEqual(found, [v.Endpoint("203.0.113.9", "8443", "tcp")])

	def test_a_config_with_no_server_is_an_error(self):
		with self.assertRaises(RuntimeError):
			v.parse_endpoints("client\ndev tun\n")

	def test_a_name_that_does_not_resolve_is_an_error_not_a_hole_in_the_firewall(self):
		with self.assertRaises(RuntimeError):
			v.parse_endpoints("remote nowhere.invalid 1194\n")


class TestPinning(VpnTestCase):
	def test_names_become_addresses_and_nothing_else_changes(self):
		pinned = v.pin_remotes(CONFIG, v.parse_endpoints(CONFIG))

		self.assertIn("remote 198.51.100.4 1194", pinned)
		self.assertIn("remote 203.0.113.9 443 tcp", pinned)
		self.assertNotIn("vpn.example.net", pinned)
		self.assertIn("remote-cert-tls server", pinned)

	def test_the_resolver_file_uses_the_default_or_the_override(self):
		path = self.root / "r.conf"

		with mock.patch.dict(os.environ, {}, clear=False) as env:
			env.pop("VPN_DNS", None)
			v.write_resolv_conf(path)
			self.assertEqual(path.read_text(), "nameserver 1.1.1.1\nnameserver 9.9.9.9\n")

		with mock.patch.dict(os.environ, {"VPN_DNS": "10.8.0.1,10.8.0.2"}):
			v.write_resolv_conf(path)
			self.assertEqual(path.read_text(), "nameserver 10.8.0.1\nnameserver 10.8.0.2\n")


class TestKillSwitch(VpnTestCase):
	def rules(self):
		run = Recorder()

		with mock.patch.object(v.subprocess, "run", run):
			v.apply_kill_switch(v.parse_endpoints(CONFIG))

		return run.iptables()

	def test_everything_is_dropped_by_default(self):
		rules = self.rules()

		for chain in ("INPUT", "OUTPUT", "FORWARD"):
			self.assertIn(["-P", chain, "DROP"], rules)

	def test_the_rules_are_flushed_before_anything_is_allowed(self):
		rules = self.rules()

		self.assertEqual(rules[0], ["-F"])
		self.assertLess(rules.index(["-P", "OUTPUT", "DROP"]), rules.index(["-A", "OUTPUT", "-o", "tun0", "-j", "ACCEPT"]))

	def test_only_the_tunnel_and_loopback_are_open_to_everything(self):
		open_to_all = [r for r in self.rules() if r[0] == "-A" and "-d" not in r and "-s" not in r]

		self.assertCountEqual(
			[(r[1], r[2], r[3]) for r in open_to_all],
			[("INPUT", "-i", "lo"), ("OUTPUT", "-o", "lo"), ("INPUT", "-i", "tun0"), ("OUTPUT", "-o", "tun0")],
		)

	def test_the_real_interface_may_only_reach_the_vpn_servers(self):
		out = [r for r in self.rules() if r[:2] == ["-A", "OUTPUT"] and "!" in r]

		self.assertEqual(
			sorted((r[r.index("-d") + 1], r[r.index("--dport") + 1], r[r.index("-p") + 1]) for r in out),
			[("198.51.100.4", "1194", "udp"), ("198.51.100.5", "1194", "udp"), ("203.0.113.9", "443", "tcp")],
		)

	def test_ipv6_is_closed_too(self):
		run = Recorder()

		with mock.patch.object(v.subprocess, "run", run):
			v.apply_kill_switch(v.parse_endpoints(CONFIG))

		six = [c[1:] for c in run.commands if c[0] == "ip6tables"]

		for chain in ("INPUT", "OUTPUT", "FORWARD"):
			self.assertIn(["-P", chain, "DROP"], six)


class TestUp(VpnTestCase):
	def run_up(self, account="second", tun_up=True):
		run = Recorder(tun_up)

		with mock.patch.object(v.subprocess, "run", run), mock.patch.object(v, "CONNECT_TIMEOUT_SECONDS", 5 if tun_up else 0):
			return v.up(account), run

	def test_without_a_config_nothing_runs_and_the_bot_does_not_start(self):
		code, run = self.run_up()

		self.assertEqual(code, 1)
		self.assertEqual(run.commands, [])

	def test_the_firewall_closes_before_openvpn_starts(self):
		self.put_config()
		code, run = self.run_up()
		names = [c[0] for c in run.commands]

		self.assertEqual(code, 0)
		self.assertLess(names.index("iptables"), names.index("openvpn"))

	def test_openvpn_is_given_the_pinned_copy_and_the_login_file(self):
		self.put_config(auth="user\npass\n")
		_, run = self.run_up()
		command = next(c for c in run.commands if c[0] == "openvpn")

		self.assertEqual(command[command.index("--config") + 1], str(self.root / "vpn-second.ovpn"))
		self.assertEqual(command[command.index("--auth-user-pass") + 1], str(self.root / "second" / "openvpn" / "auth.txt"))
		self.assertIn("remote 198.51.100.4", (self.root / "vpn-second.ovpn").read_text())

	def test_the_pinned_copy_is_not_left_in_the_data_folder(self):
		self.put_config()
		self.run_up()

		self.assertEqual(sorted(p.name for p in (self.root / "second" / "openvpn").iterdir()), ["config.ovpn"])

	def test_a_tunnel_that_never_comes_up_stops_the_bot_and_leaves_the_firewall_shut(self):
		self.put_config()
		code, run = self.run_up(tun_up=False)

		self.assertEqual(code, 1)
		self.assertIn(["-P", "OUTPUT", "DROP"], run.iptables())
		self.assertFalse((self.root / "resolv.conf").exists())

	def test_a_good_tunnel_gets_a_resolver_that_works_through_it(self):
		self.put_config()
		self.run_up()

		self.assertIn("nameserver", (self.root / "resolv.conf").read_text())


if __name__ == "__main__":
	unittest.main()
