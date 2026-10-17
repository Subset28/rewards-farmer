"""The in-browser recorder's server (tools/web_calibrate/serve.py) and the import of what it saves (calibrate.import_web)."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "web_calibrate"))

import calibrate
import serve


def sitting():
	return {"when": "2026-10-17T14:00", "typing": [{"target": "a b", "final": "a b", "shown_at": 1.0, "compose": False, "events": [], "holds": []}], "mouse": [{"start": [1, 2], "rect": [3, 4, 5, 6], "shown_at": 1.0, "samples": [], "early": [], "presses": [], "release_at": 2.0}]}


class TestServer(unittest.TestCase):
	def setUp(self):
		self.dir = tempfile.TemporaryDirectory()
		patcher = mock.patch.object(serve, "INBOX", self.dir.name)
		patcher.start()
		self.addCleanup(patcher.stop)
		self.addCleanup(self.dir.cleanup)
		self.server = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
		self.port = self.server.server_address[1]
		threading.Thread(target=self.server.serve_forever, daemon=True).start()
		self.addCleanup(self.server.server_close)
		self.addCleanup(self.server.shutdown)

	def post(self, path, body):
		request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body, method="POST", headers={"Content-Type": "application/json"})

		try:
			with urllib.request.urlopen(request) as response:
				return response.status
		except urllib.error.HTTPError as exc:
			return exc.code

	def test_the_page_is_served(self):
		with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/?for=third") as response:
			self.assertIn(b"Typing and mouse test", response.read())

	def test_a_complete_test_is_saved_under_the_account_name(self):
		self.assertEqual(self.post("/submit?for=third", json.dumps({"sessions": [sitting()]}).encode()), 200)
		names = os.listdir(self.dir.name)

		self.assertEqual(len(names), 1)
		self.assertTrue(names[0].startswith("third-"))

	def test_an_incomplete_test_is_refused(self):
		self.assertEqual(self.post("/submit?for=third", json.dumps({"sessions": [{"typing": [], "mouse": []}]}).encode()), 400)
		self.assertEqual(os.listdir(self.dir.name), [])

	def test_a_bad_account_name_is_refused(self):
		self.assertEqual(self.post("/submit?for=../etc", json.dumps({"sessions": [sitting()]}).encode()), 400)

	def test_other_paths_are_not_found(self):
		self.assertEqual(self.post("/elsewhere", b"{}"), 404)


class TestImport(unittest.TestCase):
	def setUp(self):
		self.dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.dir.cleanup)
		self.raw = os.path.join(self.dir.name, "third.raw.json")
		self.file = os.path.join(self.dir.name, "in.json")
		patcher = mock.patch.object(calibrate.App, "_raw_path", lambda app: self.raw)
		patcher.start()
		self.addCleanup(patcher.stop)

	def write_input(self, data):
		with open(self.file, "w", encoding="utf-8") as handle:
			json.dump(data, handle)

	def test_the_sitting_is_added_and_the_profile_rebuilt(self):
		self.write_input({"sessions": [sitting()]})

		with mock.patch.object(calibrate, "reanalyze", return_value=0) as rebuilt:
			self.assertEqual(calibrate.import_web("third", self.file), 0)

		rebuilt.assert_called_once_with("third")

		with open(self.raw, encoding="utf-8") as handle:
			self.assertEqual(len(json.load(handle)["sessions"]), 1)

	def test_the_same_file_twice_is_added_once(self):
		self.write_input({"sessions": [sitting()]})

		with mock.patch.object(calibrate, "reanalyze", return_value=0):
			calibrate.import_web("third", self.file)
			calibrate.import_web("third", self.file)

		with open(self.raw, encoding="utf-8") as handle:
			self.assertEqual(len(json.load(handle)["sessions"]), 1)

	def test_a_file_without_a_complete_sitting_is_refused(self):
		self.write_input({"sessions": [{"typing": [], "mouse": []}]})

		self.assertEqual(calibrate.import_web("third", self.file), 1)
		self.assertFalse(os.path.exists(self.raw))


if __name__ == "__main__":
	unittest.main()
