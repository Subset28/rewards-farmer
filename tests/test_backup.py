"""The daily backup keeps what is painful to lose, once a day, and never gets in the way of a run."""

import os
import sys
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import backup

NOW = datetime(2026, 10, 8, 12, 30)


class BackupCase(unittest.TestCase):
	def setUp(self):
		directory = tempfile.TemporaryDirectory()
		self.addCleanup(directory.cleanup)
		self.data = Path(directory.name) / "data-dir"
		self.out = self.data / "backups"
		self.data.mkdir()

		for name, text in (("pacing.json", "{}"), ("features.json", "{}"), ("points.jsonl", "x\n"), ("journal.jsonl", "y\n")):
			(self.data / name).write_text(text)

		(self.data / "behavior").mkdir()
		(self.data / "behavior" / "mom.json").write_text("{}")
		(self.data / "second" / "openvpn").mkdir(parents=True)
		(self.data / "second" / "openvpn" / "config.ovpn").write_text("remote x")
		(self.data / "second" / "openvpn" / "auth.txt").write_text("user\npass")

		# Two Edge profiles: the unnamed one at the root of data-dir, and a named one.
		(self.data / "Local State").write_text("{}")
		(self.data / "Default").mkdir()
		(self.data / "Default" / "Cookies").write_text("cookies")
		(self.data / "Default" / "Cache").mkdir()
		(self.data / "Default" / "Cache" / "junk").write_text("x" * 1000)
		(self.data / "second" / "Local State").write_text("{}")
		(self.data / "second" / "Default").mkdir()
		(self.data / "second" / "Default" / "Cookies").write_text("cookies2")
		(self.data / "logs").mkdir()
		(self.data / "logs" / "big.log").write_text("log")

	def members(self, path):
		with tarfile.open(path) as archive:
			return sorted(m.name.replace("\\", "/") for m in archive.getmembers())

	def make(self, now=NOW, keep=14):
		return backup.create(str(self.data), str(self.out), now, keep)


class TestWhatIsKept(BackupCase):
	def test_state_vpn_configs_behavior_and_sessions_are_kept(self):
		names = self.members(self.make())

		for expected in (
			"pacing.json", "features.json", "points.jsonl", "journal.jsonl", "behavior/mom.json",
			"second/openvpn/config.ovpn", "second/openvpn/auth.txt",
			"Local State", "Default/Cookies", "second/Local State", "second/Default/Cookies",
		):
			self.assertIn(expected, names)

	def test_caches_and_logs_are_not(self):
		names = self.members(self.make())

		self.assertFalse([n for n in names if "Cache/junk" in n])
		self.assertFalse([n for n in names if n.startswith("logs")])

	def test_a_missing_file_is_simply_left_out(self):
		(self.data / "pacing.json").unlink()

		self.assertNotIn("pacing.json", self.members(self.make()))

	def test_a_pause_record_is_kept(self):
		(self.data / "PAUSED.second").write_text("{}")

		self.assertIn("PAUSED.second", self.members(self.make()))

	def test_the_archive_is_private(self):
		path = self.make()

		if os.name != "nt":
			self.assertEqual(os.stat(path).st_mode & 0o077, 0)


class TestOncePerDay(BackupCase):
	def test_a_second_ask_the_same_day_does_nothing(self):
		self.assertIsNotNone(self.make(NOW))
		self.assertIsNone(self.make(NOW + timedelta(hours=3)))
		self.assertEqual(len(backup.existing(str(self.out))), 1)

	def test_the_next_day_makes_a_new_one(self):
		self.make(NOW)
		self.make(NOW + timedelta(days=1))

		self.assertEqual(len(backup.existing(str(self.out))), 2)

	def test_only_the_newest_few_are_kept(self):
		for day in range(6):
			self.make(NOW + timedelta(days=day), keep=3)

		kept = backup.existing(str(self.out))

		self.assertEqual(len(kept), 3)
		self.assertIn("20261013", kept[-1])

	def test_a_failed_backup_lets_the_next_try_the_same_day_succeed(self):
		with mock.patch.object(backup.tarfile, "open", side_effect=OSError("disk full")):
			with self.assertRaises(OSError):
				self.make()

		self.assertIsNotNone(self.make())


class TestNeverBreaksARun(BackupCase):
	def test_run_if_due_swallows_errors(self):
		with mock.patch.object(backup, "create", side_effect=OSError("no space")):
			self.assertIsNone(backup.run_if_due())

	def test_a_backup_restores_to_the_same_contents(self):
		path = self.make()
		restored = Path(tempfile.mkdtemp())

		with tarfile.open(path) as archive:
			archive.extractall(restored)

		self.assertEqual((restored / "second" / "openvpn" / "config.ovpn").read_text(), "remote x")
		self.assertEqual((restored / "Default" / "Cookies").read_text(), "cookies")


if __name__ == "__main__":
	unittest.main()
