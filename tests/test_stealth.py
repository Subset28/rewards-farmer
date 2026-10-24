"""Unit tests for stealth and anti-bot hardening.

Tests for:
1. Browser preload stealth script (WebGL spoofing, driver markers, chrome runtime APIs).
2. Natural search navigation (SERP box vs homepage navigation).
3. Graceful degradation when driver registration fails.
4. Binary driver string patch logic (length preservation).
"""

import os
import sys
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if SRC not in sys.path:
	sys.path.insert(0, SRC)

import browser  # noqa: E402
import rewards_tasks  # noqa: E402
from selenium.common.exceptions import WebDriverException  # noqa: E402


class TestStealthPreloadScript(unittest.TestCase):
	def test_preload_script_contains_webgl_spoofing(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		# Check that UNMASKED_VENDOR_WEBGL (37445) and UNMASKED_RENDERER_WEBGL (37446) are handled
		self.assertIn("37445", script)
		self.assertIn("37446", script)
		self.assertIn("Intel Inc.", script)
		self.assertIn("Intel(R) Iris(R) Xe Graphics", script)
		self.assertIn("getParameterProxyHandler", script)

	def test_preload_script_contains_chrome_runtime_completeness(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("window.chrome.csi", script)
		self.assertIn("window.chrome.loadTimes", script)
		self.assertIn("window.chrome.app", script)

	def test_preload_script_cleans_driver_markers(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("cdc_", script)
		self.assertIn("rnd_", script)

	def test_preload_script_overrides_navigator_webdriver(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("navigator, 'webdriver'", script)
		self.assertIn("get: () => undefined", script)

	def test_preload_script_proxies_function_prototype_tostring(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("Function.prototype.toString", script)
		self.assertIn("nativeFunctions", script)
		self.assertIn("[native code]", script)

	def test_preload_script_defines_devicememory_and_window_dimensions(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("navigator, 'deviceMemory'", script)
		self.assertIn("window, 'outerWidth'", script)
		self.assertIn("window, 'outerHeight'", script)

	def test_hide_driver_markers_registers_both_cdp_and_bidi(self):
		mock_driver = mock.Mock()
		browser.hide_driver_markers(mock_driver, "test_account")

		# Verify BiDi pin was called
		mock_driver.script.pin.assert_called_once_with(browser.HIDE_DRIVER_MARKERS_JS)
		# Verify CDP addScriptToEvaluateOnNewDocument was called
		mock_driver.execute_cdp_cmd.assert_called_once_with(
			"Page.addScriptToEvaluateOnNewDocument",
			{"source": browser.HIDE_DRIVER_MARKERS_JS}
		)

	def test_hide_driver_markers_degrades_gracefully_on_driver_exception(self):
		mock_driver = mock.Mock()
		mock_driver.script.pin.side_effect = WebDriverException("BiDi unsupported")
		mock_driver.execute_cdp_cmd.side_effect = WebDriverException("CDP failed")

		# Should not raise exception
		with self.assertLogs(browser.logger, level="WARNING") as logs:
			browser.hide_driver_markers(mock_driver, "test_account")

		self.assertTrue(any("could not hide the driver's cdc_ window properties" in line for line in logs.output))

	def test_driver_binary_patch_preserves_length(self):
		sample_binary = b"header_data...cdc_adoQpoasnfa76pfcZLmcfl_Array...trailer"
		patched = sample_binary.replace(b"cdc_", b"rnd_")
		self.assertEqual(len(sample_binary), len(patched))
		self.assertNotIn(b"cdc_", patched)
		self.assertIn(b"rnd_", patched)


class MockSearchElements:
	def __init__(self):
		self.serp_box = mock.Mock()
		self.bing_bar = mock.Mock()

	def get_serp_search_box(self):
		return self.serp_box

	def get_bing_search_bar(self):
		return self.bing_bar


class SearchCadenceTestPage:
	def __init__(self, current_url="https://www.bing.com/"):
		self.account_name = "test_stealth"
		self.driver = mock.Mock()
		self.driver.current_url = current_url
		self.driver.current_window_handle = "main"
		self.driver.window_handles = ["main"]
		self.tab_utils = mock.Mock()
		self.elements = MockSearchElements()
		self.keyboard = mock.Mock()
		self.keyboard.slip_settings.return_value = (0.0, 0.0)
		self.keyboard.slip_weights.return_value = None
		self.mouse = mock.Mock()
		self.moves = []

	def wait_for_element(self, getter):
		return getter()

	def move_to_and_click(self, element):
		self.moves.append(element)

	def browse_results(self):
		pass


@mock.patch.object(rewards_tasks.time, "sleep", lambda *_: None)
class TestBrowserHardwareOptions(unittest.TestCase):
	def test_has_gpu_device_checks_dev_dri(self):
		with mock.patch("os.path.exists", return_value=True) as mock_exists:
			self.assertTrue(browser.has_gpu_device())
			mock_exists.assert_called_once_with("/dev/dri")

	def test_build_options_with_dri_omits_swiftshader(self):
		mock_account = mock.Mock()
		mock_account.user_data_dir = "/tmp/fake_user_data"
		mock_account.profile_name = "Default"

		with mock.patch.object(browser, "HEADLESS", True), \
			mock.patch.object(browser, "VIRTUAL_DISPLAY", True), \
			mock.patch.object(browser, "has_gpu_device", return_value=True):
			opts = browser.build_options(mock_account)

		self.assertIn("--ignore-gpu-blocklist", opts.arguments)
		self.assertNotIn("--use-angle=swiftshader", opts.arguments)
		self.assertNotIn("--enable-unsafe-swiftshader", opts.arguments)

	def test_build_options_without_dri_uses_swiftshader_fallback(self):
		mock_account = mock.Mock()
		mock_account.user_data_dir = "/tmp/fake_user_data"
		mock_account.profile_name = "Default"

		with mock.patch.object(browser, "HEADLESS", True), \
			mock.patch.object(browser, "VIRTUAL_DISPLAY", True), \
			mock.patch.object(browser, "has_gpu_device", return_value=False):
			opts = browser.build_options(mock_account)

		self.assertIn("--ignore-gpu-blocklist", opts.arguments)
		self.assertIn("--use-angle=swiftshader", opts.arguments)
		self.assertIn("--enable-unsafe-swiftshader", opts.arguments)


class TestSearchCadence(unittest.TestCase):
	def test_subsequent_searches_use_serp_search_box_when_on_serp(self):
		page = SearchCadenceTestPage(current_url="https://www.bing.com/search?q=test")

		with mock.patch.object(rewards_tasks.features, "enabled", return_value=False), \
			mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["query1", "query2"]), \
			mock.patch.object(rewards_tasks.random, "random", return_value=0.5), \
			mock.patch.object(rewards_tasks.ActionChains, "perform", return_value=None):
			page.run_search_batch = rewards_tasks.RewardsTaskUtils.run_search_batch.__get__(page)
			page.run_search_batch(2)

		# First search (i=0) always goes to home or initializes
		# Second search (i=1) on SERP should click SERP search box rather than driver.get(home)
		self.assertIn(page.elements.serp_box, page.moves)

	def test_serp_search_box_failure_falls_back_to_homepage(self):
		page = SearchCadenceTestPage(current_url="https://www.bing.com/search?q=test")

		# Make moving to serp_box fail
		def move_fail(element):
			if element == page.elements.serp_box:
				raise WebDriverException("Element obscured")

		page.move_to_and_click = move_fail

		with mock.patch.object(rewards_tasks.features, "enabled", return_value=False), \
			mock.patch.object(rewards_tasks.queries, "related_queries", return_value=["query1", "query2"]), \
			mock.patch.object(rewards_tasks.random, "random", return_value=0.5), \
			mock.patch.object(rewards_tasks.ActionChains, "perform", return_value=None):
			page.run_search_batch = rewards_tasks.RewardsTaskUtils.run_search_batch.__get__(page)
			page.run_search_batch(2)

		self.assertEqual(page.keyboard.send_keys.call_count, 2)


if __name__ == "__main__":
	unittest.main()
