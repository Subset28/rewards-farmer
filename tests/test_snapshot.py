import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import snapshot


class TestWhatIsKept(unittest.TestCase):
	def test_emails_numbers_and_greetings_do_not_survive(self):
		self.assertEqual(snapshot.clean_label("Signed in as someone@example.com"), "Signed in as [email]")
		self.assertEqual(snapshot.clean_label("Call 555-123-4567 now"), "Call [number] now")
		self.assertIsNone(snapshot.clean_label("Good morning, Alexandra"))
		self.assertIsNone(snapshot.clean_label("Welcome back Sam"))
		self.assertIsNone(snapshot.clean_label("   "))

	def test_ordinary_labels_are_kept_and_shortened(self):
		self.assertEqual(snapshot.clean_label("Daily set streak 7 days"), "Daily set streak 7 days")
		self.assertEqual(len(snapshot.clean_label("x" * 500)), snapshot.MAX_LABEL_CHARS)

	def test_addresses_lose_their_query_fragment_and_credentials(self):
		self.assertEqual(snapshot.clean_url("https://user:pw@rewards.bing.com/earn?token=abc&x=1#frag"), "https://rewards.bing.com/earn")
		self.assertEqual(snapshot.clean_url("/earn?id=5#x"), "/earn")
		self.assertEqual(snapshot.clean_url(""), "")

	def test_a_whole_page_is_reduced_and_deduplicated(self):
		page = snapshot.clean({
			"url": "https://rewards.bing.com/?code=SECRET", "title": "Rewards for me@x.com",
			"buttons": ["Visual search", "Visual search", "Good evening, Pat", "Claim"],
			"links": [["Earn", "https://rewards.bing.com/earn?id=77"], ["Earn", "/dup"]],
			"headings": ["Streaks"], "ids": ["streaks", "has space", "<script>"],
		})
		dump = json.dumps(page)

		self.assertNotIn("SECRET", dump)
		self.assertNotIn("me@x.com", dump)
		self.assertNotIn("Pat", dump)
		self.assertEqual(page["buttons"], ["Visual search", "Claim"])
		self.assertEqual(page["links"], [["Earn", "rewards.bing.com/earn"]])
		self.assertEqual(page["ids"], ["streaks"])


class TestSaving(unittest.TestCase):
	def setUp(self):
		self.folder = tempfile.TemporaryDirectory()
		self.addCleanup(self.folder.cleanup)
		patch = mock.patch.object(snapshot, "SNAPSHOT_DIR", self.folder.name)
		patch.start()
		self.addCleanup(patch.stop)

	def driver(self, page):
		driver = mock.Mock()
		driver.execute_script.return_value = page

		return driver

	def test_a_snapshot_is_saved_and_can_be_read_back(self):
		name = snapshot.capture(self.driver({"url": "https://rewards.bing.com/", "buttons": ["Visual search"]}), "default", "Visual search", "not available")

		self.assertIn(name, snapshot.names())
		self.assertEqual(snapshot.read(name)["buttons"], ["Visual search"])
		self.assertEqual(snapshot.read(name)["task"], "Visual search")

	def test_only_the_newest_few_per_task_are_kept(self):
		for i in range(snapshot.KEEP_PER_TASK + 3):
			with mock.patch.object(snapshot.clock, "now") as now:
				from datetime import datetime
				now.return_value = datetime(2026, 10, 8, 10, 0, i)
				snapshot.capture(self.driver({}), "default", "Quests")

		self.assertEqual(len(snapshot.names()), snapshot.KEEP_PER_TASK)

	def test_a_page_that_cannot_be_read_is_not_an_error(self):
		driver = mock.Mock()
		driver.execute_script.side_effect = RuntimeError("gone")

		self.assertIsNone(snapshot.capture(driver, "default", "Quests"))

	def test_only_files_in_the_folder_can_be_read(self):
		self.assertIsNone(snapshot.read("../../etc/passwd"))
		self.assertIsNone(snapshot.read("missing.json"))
