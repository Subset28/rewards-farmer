"""trawl as a fallback for refused public feeds, and never for an account."""

import io
import json
import os
import sys
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import query_sources
import trawl_client


class Reply:
	def __init__(self, body):
		self.body = body if isinstance(body, bytes) else body.encode()

	def read(self):
		return self.body

	def __enter__(self):
		return self

	def __exit__(self, *args):
		return False


def solved(html, status=200, answer="ok"):
	return Reply(json.dumps({"status": answer, "message": "", "solution": {"url": "x", "status": status, "response": html}}))


def http_error(code):
	return urllib.error.HTTPError("https://example.com", code, "refused", {}, io.BytesIO(b""))


class TrawlTestCase(unittest.TestCase):
	def setUp(self):
		patcher = mock.patch.dict(os.environ, {"TRAWL_URL": "http://trawl.local:8191"})
		patcher.start()
		self.addCleanup(patcher.stop)


class TestFetch(TrawlTestCase):
	def test_without_a_url_nothing_is_asked(self):
		with mock.patch.dict(os.environ, {"TRAWL_URL": ""}), mock.patch.object(trawl_client.urllib.request, "urlopen") as post:
			self.assertIsNone(trawl_client.fetch("https://example.com/feed"))

		post.assert_not_called()

	def test_a_url_that_is_not_http_is_refused(self):
		with mock.patch.dict(os.environ, {"TRAWL_URL": "file:///etc/passwd"}), mock.patch.object(trawl_client.urllib.request, "urlopen") as post:
			self.assertIsNone(trawl_client.fetch("https://example.com/feed"))

		post.assert_not_called()

	def test_the_page_is_asked_for_in_the_flaresolverr_form_and_its_html_returned(self):
		with mock.patch.object(trawl_client.urllib.request, "urlopen", return_value=solved("<html>feed</html>")) as post:
			self.assertEqual(trawl_client.fetch("https://example.com/feed"), "<html>feed</html>")

		request = post.call_args.args[0]

		self.assertEqual(request.full_url, "http://trawl.local:8191/v1")
		self.assertEqual(json.loads(request.data), {"cmd": "request.get", "url": "https://example.com/feed", "maxTimeout": 45000})

	def test_a_trailing_slash_on_the_service_address_is_fine(self):
		with mock.patch.dict(os.environ, {"TRAWL_URL": "http://trawl.local:8191/"}), \
			mock.patch.object(trawl_client.urllib.request, "urlopen", return_value=solved("ok")) as post:
			trawl_client.fetch("https://example.com/feed")

		self.assertEqual(post.call_args.args[0].full_url, "http://trawl.local:8191/v1")

	def test_a_bare_answer_a_browser_wrapped_in_pre_is_unwrapped(self):
		page = '<html><head></head><body><pre style="x">{"a": 1, "b": "&lt;x&gt;"}</pre></body></html>'

		with mock.patch.object(trawl_client.urllib.request, "urlopen", return_value=solved(page)):
			self.assertEqual(trawl_client.fetch("https://example.com/feed.json"), '{"a": 1, "b": "<x>"}')

	def test_a_failed_solve_a_bad_status_or_a_challenge_page_gives_nothing(self):
		for reply in (solved("x", answer="error"), solved("x", status=403), solved("<title>Just a moment...</title>")):
			with mock.patch.object(trawl_client.urllib.request, "urlopen", return_value=reply):
				self.assertIsNone(trawl_client.fetch("https://example.com/feed"))

	def test_a_service_that_is_down_never_raises(self):
		with mock.patch.object(trawl_client.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")):
			self.assertIsNone(trawl_client.fetch("https://example.com/feed"))

	def test_nonsense_back_never_raises(self):
		for body in (b"not json", b"[]", b'{"status": "ok"}'):
			with mock.patch.object(trawl_client.urllib.request, "urlopen", return_value=Reply(body)):
				self.assertIsNone(trawl_client.fetch("https://example.com/feed"))


class TestAccountSideIsRefused(TrawlTestCase):
	ADDRESSES = (
		"https://login.live.com/ppsecure/post.srf", "https://rewards.bing.com/", "https://www.bing.com/search?q=x",
		"https://account.microsoft.com/", "https://login.microsoftonline.com/", "https://www.msn.com/",
		"https://outlook.live.com/mail", "https://BING.COM/", "https://deep.sub.rewards.bing.com/x",
	)

	def test_none_of_microsofts_account_pages_ever_go_through_trawl(self):
		with mock.patch.object(trawl_client.urllib.request, "urlopen") as post:
			for address in self.ADDRESSES:
				self.assertIsNone(trawl_client.fetch(address), address)

		post.assert_not_called()

	def test_an_unrelated_site_that_only_looks_similar_is_not_refused(self):
		for address in ("https://notbing.com/", "https://bing.com.example.net/", "https://example.com/?u=bing.com"):
			self.assertFalse(trawl_client.refused(address), address)

	def test_the_feeds_we_use_are_not_refused(self):
		for address in ("https://trends.google.com/trending/rss?geo=US", "https://suggestqueries.google.com/complete/search?q=a", "https://en.wikipedia.org/api/rest_v1/feed"):
			self.assertFalse(trawl_client.refused(address), address)


class TestFeedFallback(TrawlTestCase):
	"""query_sources._fetch tries trawl only when a plain request is turned away."""

	def test_a_page_that_loads_normally_never_touches_trawl(self):
		with mock.patch.object(query_sources.urllib.request, "urlopen", return_value=Reply("plain feed")), \
			mock.patch.object(trawl_client, "fetch") as through:
			self.assertEqual(query_sources._fetch("https://example.com/feed"), "plain feed")

		through.assert_not_called()

	def test_a_refusal_is_retried_through_trawl(self):
		for code in (403, 429, 503):
			with mock.patch.object(query_sources.urllib.request, "urlopen", side_effect=http_error(code)), \
				mock.patch.object(trawl_client, "fetch", return_value="solved feed") as through:
				self.assertEqual(query_sources._fetch("https://example.com/feed"), "solved feed")

			through.assert_called_once_with("https://example.com/feed")

	def test_a_challenge_page_served_with_a_200_is_retried_through_trawl(self):
		with mock.patch.object(query_sources.urllib.request, "urlopen", return_value=Reply("<title>Just a moment...</title>")), \
			mock.patch.object(trawl_client, "fetch", return_value="solved feed"):
			self.assertEqual(query_sources._fetch("https://example.com/feed"), "solved feed")

	def test_an_ordinary_error_is_not_retried(self):
		with mock.patch.object(query_sources.urllib.request, "urlopen", side_effect=http_error(404)), \
			mock.patch.object(trawl_client, "fetch") as through:
			self.assertIsNone(query_sources._fetch("https://example.com/feed"))

		through.assert_not_called()

	def test_a_refusal_with_no_trawl_is_just_nothing_as_before(self):
		with mock.patch.dict(os.environ, {"TRAWL_URL": ""}), \
			mock.patch.object(query_sources.urllib.request, "urlopen", side_effect=http_error(403)):
			self.assertIsNone(query_sources._fetch("https://example.com/feed"))

	def test_a_network_failure_is_not_retried(self):
		with mock.patch.object(query_sources.urllib.request, "urlopen", side_effect=urllib.error.URLError("down")), \
			mock.patch.object(trawl_client, "fetch") as through:
			self.assertIsNone(query_sources._fetch("https://example.com/feed"))

		through.assert_not_called()


if __name__ == "__main__":
	unittest.main()
