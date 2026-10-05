"""The human-check detector used by the VPN browser check."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import vpn_browser_check as c


class TestChallengeMarkers(unittest.TestCase):
	def test_an_ordinary_page_has_no_markers(self):
		self.assertEqual(c.challenge_markers("Weather tomorrow: sunny, 72. Sign in to Bing", "https://www.bing.com/search?q=weather"), [])

	def test_the_usual_human_check_wordings_are_found(self):
		for text in (
			"Please complete the CAPTCHA to continue", "We've detected unusual traffic from your network",
			"Verify you are a human", "Press & Hold to confirm you are not a bot", "Are you a robot?",
			"Prove you're human", "Suspicious activity detected", "Access denied",
		):
			self.assertTrue(c.challenge_markers(text), text)

	def test_it_is_not_case_sensitive(self):
		self.assertEqual(c.challenge_markers("CAPTCHA required"), ["captcha"])

	def test_a_challenge_in_the_address_counts(self):
		self.assertEqual(c.challenge_markers("hello", "https://example.com/captcha/verify"), ["captcha"])

	def test_several_markers_are_all_reported(self):
		self.assertEqual(sorted(c.challenge_markers("unusual traffic. complete the captcha")), ["captcha", "unusual traffic"])


class TestCheckPage(unittest.TestCase):
	class Driver:
		def __init__(self, text, url="https://www.bing.com/x", title="Bing"):
			self._text, self.current_url, self.title = text, url, title

		def get(self, url):
			pass

		def find_element(self, by, selector):
			return type("Body", (), {"text": self._text})()

	def run_check(self, text, **kwargs):
		from unittest import mock

		with mock.patch.object(c.time, "sleep"):
			return c.check_page(self.Driver(text, **kwargs), "bing search", "https://www.bing.com/")

	def test_a_clean_page_reports_no_challenge(self):
		result = self.run_check("Weather tomorrow\nSunny")

		self.assertEqual(result["verdict"], "no challenge seen")
		self.assertEqual(result["challenge_markers"], [])

	def test_a_challenged_page_is_reported_as_such(self):
		result = self.run_check("Verify you are a human\nPress & Hold")

		self.assertEqual(result["verdict"], "CHALLENGED")
		self.assertIn("verify you are a human", result["challenge_markers"])


if __name__ == "__main__":
	unittest.main()
