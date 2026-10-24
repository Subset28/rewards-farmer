"""Unit tests for stealth and anti-bot hardening.

Tests for:
1. Browser preload stealth script (WebGL spoofing, driver markers, chrome runtime APIs).
2. Natural search navigation (SERP box vs homepage navigation).
3. Graceful degradation when driver registration fails.
4. Binary driver string patch logic (length preservation).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
if SRC not in sys.path:
	sys.path.insert(0, SRC)

import accounts  # noqa: E402
import browser  # noqa: E402
import rewards_tasks  # noqa: E402
import safety  # noqa: E402
import search_scheduler  # noqa: E402
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

	def test_preload_script_contains_canvas_audio_and_font_spoofing(self):
		script = browser.HIDE_DRIVER_MARKERS_JS
		self.assertIn("CanvasRenderingContext2D.prototype.getImageData", script)
		self.assertIn("HTMLCanvasElement.prototype.toDataURL", script)
		self.assertIn("AudioBuffer.prototype.getChannelData", script)
		self.assertIn("document.fonts.check", script)

	def test_make_stealth_script_generates_distinct_per_account_seeds(self):
		s_default = browser.make_stealth_script("default")
		s_second = browser.make_stealth_script("second")
		s_third = browser.make_stealth_script("third")
		self.assertNotEqual(s_default, s_second)
		self.assertNotEqual(s_second, s_third)
		self.assertEqual(s_default, browser.make_stealth_script("default"))

	def test_hide_driver_markers_registers_both_cdp_and_bidi(self):
		mock_driver = mock.Mock()
		browser.hide_driver_markers(mock_driver, "test_account")
		expected_script = browser.make_stealth_script("test_account")

		# Verify BiDi pin was called
		mock_driver.script.pin.assert_called_once_with(expected_script)
		# Verify CDP addScriptToEvaluateOnNewDocument was called
		mock_driver.execute_cdp_cmd.assert_called_once_with(
			"Page.addScriptToEvaluateOnNewDocument",
			{"source": expected_script}
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
		self.ORGANIC_DWELL_RATE = 0.20
		self.dwell_organic_result = mock.Mock()

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
		self.assertIn("--force-webrtc-ip-handling-policy=default_public_interface_only", opts.arguments)
		self.assertIn("--enforce-webrtc-ip-permission-check", opts.arguments)

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

	def test_dwell_organic_result_delegates_to_open_a_result(self):
		page = SearchCadenceTestPage()
		page._open_a_result = mock.Mock()
		page.dwell_organic_result = rewards_tasks.RewardsTaskUtils.dwell_organic_result.__get__(page)

		page.dwell_organic_result(speed=1.5)
		page._open_a_result.assert_called_once_with(1.5)

	def test_dwell_organic_result_handles_driver_exception(self):
		page = SearchCadenceTestPage()
		page._open_a_result = mock.Mock(side_effect=WebDriverException("Timeout"))
		page.dwell_organic_result = rewards_tasks.RewardsTaskUtils.dwell_organic_result.__get__(page)

		with self.assertLogs(rewards_tasks.logger, level="DEBUG") as logs:
			page.dwell_organic_result(speed=1.0)

		self.assertTrue(any("Skipped organic dwell" in line for line in logs.output))


class TestTlsHardening(unittest.TestCase):
	def test_build_options_includes_tls_and_kyber_flags(self):
		account = accounts.Account(name="test_acc", user_data_dir="/tmp/test", profile_name="Profile 1")
		opts = browser.build_options(account)
		args = opts.arguments
		self.assertIn("--ssl-version-min=tls1.2", args)
		self.assertIn("--enable-features=PostQuantumKyber", args)


class TestRoadblockTriageAndSnapshots(unittest.TestCase):
	def setUp(self):
		self.temp_dir = tempfile.TemporaryDirectory()
		self.addCleanup(self.temp_dir.cleanup)
		patcher1 = mock.patch.object(safety, "PAUSE_FILE", os.path.join(self.temp_dir.name, "PAUSED"))
		patcher1.start()
		self.addCleanup(patcher1.stop)
		patcher2 = mock.patch.object(safety, "USER_DATA_DIR", self.temp_dir.name)
		patcher2.start()
		self.addCleanup(patcher2.stop)
		self.mock_notify = mock.patch.object(safety.notify, "send").start()
		self.addCleanup(mock.patch.stopall)

	def test_triage_roadblock_account_locked(self):
		risk = safety.Risk(safety.RESTRICTED, "account is suspended")
		res = safety.triage_roadblock(risk, url="https://account.live.com/abuse", text="Your account is suspended")
		self.assertEqual(res["category"], safety.CATEGORY_ACCOUNT_LOCKED)
		self.assertEqual(res["severity"], "critical")

	def test_triage_roadblock_captcha(self):
		risk = safety.Risk(safety.CHALLENGE, "solve the puzzle")
		res = safety.triage_roadblock(risk, url="https://www.bing.com/turing/captcha", text="Solve the puzzle to continue")
		self.assertEqual(res["category"], safety.CATEGORY_CAPTCHA_PUZZLE)
		self.assertEqual(res["severity"], "high")

	def test_triage_roadblock_phone_verification(self):
		risk = safety.Risk(safety.CHALLENGE, "phone verification required")
		res = safety.triage_roadblock(risk, url="https://account.live.com/proofs/Verify", text="Help us protect your account")
		self.assertEqual(res["category"], safety.CATEGORY_PHONE_VERIFICATION)
		self.assertEqual(res["severity"], "high")

	def test_triage_roadblock_session_lapsed(self):
		risk = safety.Risk(safety.SIGNED_OUT, "redirected to login")
		res = safety.triage_roadblock(risk, url="https://login.live.com/login.srf", text="")
		self.assertEqual(res["category"], safety.CATEGORY_SESSION_LAPSED)
		self.assertEqual(res["severity"], "medium")

	def test_trip_captures_screenshot_and_includes_in_pause_record(self):
		mock_driver = mock.Mock()
		mock_driver.current_url = "https://www.bing.com/turing/captcha"
		mock_driver.execute_script.return_value = "solve the puzzle"
		mock_driver.save_screenshot.return_value = True

		risk = safety.Risk(safety.CHALLENGE, "solve the puzzle")
		safety.trip(risk, "account_a", driver=mock_driver)

		record = safety.paused()
		self.assertIsNotNone(record)
		self.assertEqual(record["account"], "account_a")
		self.assertEqual(record["triage"]["category"], safety.CATEGORY_CAPTCHA_PUZZLE)
		self.assertIn("snapshot", record)
		self.assertTrue(record["snapshot"].endswith(".png"))
		mock_driver.save_screenshot.assert_called_once()

		self.mock_notify.assert_called_once()
		self.assertIn("captcha_challenge", self.mock_notify.call_args.args[0])
		self.assertIn("Snapshot:", self.mock_notify.call_args.args[1])


class TestCircadianScheduling(unittest.TestCase):
	def test_circadian_drift_is_bounded_and_deterministic(self):
		d1 = search_scheduler.circadian_drift_minutes("acc1", "2026-10-24")
		d2 = search_scheduler.circadian_drift_minutes("acc1", "2026-10-24")
		self.assertEqual(d1, d2)
		self.assertTrue(-10.0 <= d1 <= 10.0)

	def test_circadian_drift_differs_across_dates_and_owners(self):
		d_day1 = search_scheduler.circadian_drift_minutes("acc1", "2026-10-24")
		d_day2 = search_scheduler.circadian_drift_minutes("acc1", "2026-10-25")
		d_other = search_scheduler.circadian_drift_minutes("acc2", "2026-10-24")
		self.assertNotEqual(d_day1, d_day2)
		self.assertNotEqual(d_day1, d_other)

	def test_habit_windows_integrates_circadian_drift(self):
		w_static = search_scheduler.habit_windows("acc1", False)
		w_drift = search_scheduler.habit_windows("acc1", False, date_iso="2026-10-24")
		drift = search_scheduler.circadian_drift_minutes("acc1", "2026-10-24")

		self.assertEqual(len(w_static), len(w_drift))
		for (s_a, s_b), (d_a, d_b) in zip(w_static, w_drift):
			self.assertAlmostEqual(d_a - s_a, drift, places=4)
			self.assertAlmostEqual(d_b - s_b, drift, places=4)


if __name__ == "__main__":
	unittest.main()
