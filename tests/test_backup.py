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


class FakeResponse:
	def __init__(self, status, body=None):
		self.status_code = status
		self._body = body or {}

	def json(self):
		return self._body


class FakeSession:
	"""Stands in for the GitHub API: remembers what was put and where."""

	def __init__(self, existing_sha=None, put_status=201, get_status=None):
		self.existing_sha = existing_sha
		self.put_status = put_status
		self.get_status = get_status
		self.puts = []
		self.gets = []

	def get(self, url, headers=None, timeout=None):
		self.gets.append((url, headers))

		if self.get_status:
			return FakeResponse(self.get_status)

		return FakeResponse(200, {"sha": self.existing_sha}) if self.existing_sha else FakeResponse(404)

	def put(self, url, headers=None, json=None, timeout=None):
		self.puts.append((url, headers, json))

		return FakeResponse(self.put_status)


class TestOffNas(BackupCase):
	TOKEN = "github_pat_SECRETVALUE"

	def setUp(self):
		super().setUp()
		patcher = mock.patch.dict(os.environ, {"BACKUP_REPO": "owner/rewards-backups", "BACKUP_GITHUB_TOKEN": self.TOKEN})
		patcher.start()
		self.addCleanup(patcher.stop)

	def archive_names(self):
		with tarfile.open(fileobj=__import__("io").BytesIO(backup.shareable_archive(str(self.data))), mode="r:gz") as archive:
			return sorted(m.name.replace("\\", "/") for m in archive.getmembers())

	def test_the_off_nas_copy_has_the_state_and_no_secret(self):
		names = self.archive_names()

		for expected in ("pacing.json", "points.jsonl", "features.json", "behavior/mom.json", "vpn-servers.json"):
			self.assertIn(expected, names)

		for secret in ("second/openvpn/auth.txt", "second/openvpn/config.ovpn", "Default/Cookies", "Local State", "second/Default/Cookies"):
			self.assertNotIn(secret, names)

		self.assertFalse([n for n in names if "openvpn" in n or "Cookies" in n or "auth" in n])

	def test_the_vpn_summary_names_the_server_but_not_the_login_or_keys(self):
		(self.data / "second" / "openvpn" / "timezone").write_text("America/New_York\n")
		(self.data / "second" / "openvpn" / "config.ovpn").write_text("remote 1.2.3.4 1194\n<key>\nSECRETKEY\n</key>\n")
		summary = backup.vpn_summary(str(self.data))

		self.assertEqual(summary, {"second": {"remote": "1.2.3.4 1194", "timezone": "America/New_York"}})
		self.assertNotIn("SECRETKEY", str(summary))
		self.assertNotIn("pass", str(summary))

	def test_a_first_upload_creates_the_file(self):
		session = FakeSession()

		self.assertTrue(backup.offsite_if_due(str(self.data), str(self.out), NOW, session))
		url, headers, body = session.puts[0]

		self.assertTrue(url.endswith("/repos/owner/rewards-backups/contents/state/rewards-state.tar.gz"))
		self.assertNotIn("sha", body)
		self.assertEqual(headers["Authorization"], f"Bearer {self.TOKEN}")

	def test_a_later_upload_replaces_it_using_the_old_sha(self):
		session = FakeSession(existing_sha="abc123")
		backup.offsite_if_due(str(self.data), str(self.out), NOW, session)

		self.assertEqual(session.puts[0][2]["sha"], "abc123")

	def test_once_a_day_only(self):
		session = FakeSession()

		self.assertTrue(backup.offsite_if_due(str(self.data), str(self.out), NOW, session))
		self.assertFalse(backup.offsite_if_due(str(self.data), str(self.out), NOW + timedelta(hours=2), session))
		self.assertEqual(len(session.puts), 1)

	def test_a_refused_upload_is_tried_again_next_time(self):
		bad = FakeSession(put_status=403)

		self.assertFalse(backup.offsite_if_due(str(self.data), str(self.out), NOW, bad))
		self.assertTrue(backup.offsite_if_due(str(self.data), str(self.out), NOW + timedelta(hours=1), FakeSession()))

	def test_a_refusal_is_remembered_from_the_first_time_and_forgotten_on_success(self):
		self.out.mkdir(exist_ok=True)

		backup.offsite_if_due(str(self.data), str(self.out), NOW, FakeSession(put_status=401))
		backup.offsite_if_due(str(self.data), str(self.out), NOW + timedelta(hours=5), FakeSession(put_status=401))

		self.assertEqual(backup.offsite_failing_since(str(self.out)), NOW.isoformat(timespec="seconds"))

		backup.offsite_if_due(str(self.data), str(self.out), NOW + timedelta(hours=6), FakeSession())

		self.assertIsNone(backup.offsite_failing_since(str(self.out)))

	def test_unset_means_nothing_is_sent(self):
		session = FakeSession()

		with mock.patch.dict(os.environ, {"BACKUP_GITHUB_TOKEN": ""}):
			self.assertFalse(backup.offsite_if_due(str(self.data), str(self.out), NOW, session))

		self.assertEqual(session.puts, [])
		self.assertEqual(session.gets, [])

	def test_a_network_error_does_not_raise_and_the_token_is_not_logged(self):
		class Down:
			def get(self, *a, **k):
				raise OSError(f"cannot reach https://x with {TestOffNas.TOKEN}")

		with self.assertLogs(backup.logger, level="DEBUG") as logs:
			self.assertFalse(backup.offsite_if_due(str(self.data), str(self.out), NOW, Down()))

		self.assertNotIn(self.TOKEN, "\n".join(logs.output))

	def test_a_conflict_is_looked_at_again_once(self):
		class Racy(FakeSession):
			def put(self, *a, **k):
				result = super().put(*a, **k)
				self.put_status = 201

				return FakeResponse(409) if len(self.puts) == 1 else result

		session = Racy()

		self.assertTrue(backup.upload(b"x", "owner/r", "t", session=session))
		self.assertEqual(len(session.puts), 2)

	def test_run_if_due_sends_it_even_when_the_local_backup_already_exists(self):
		session = FakeSession()

		with mock.patch.object(backup, "BACKUP_DIR", str(self.out)), mock.patch.object(backup, "USER_DATA_DIR", str(self.data)), 			mock.patch.object(backup.requests, "get", session.get), mock.patch.object(backup.requests, "put", session.put):
			backup.create(str(self.data), str(self.out), NOW)
			backup.run_if_due()

		self.assertEqual(len(session.puts), 1)


if __name__ == "__main__":
	unittest.main()
