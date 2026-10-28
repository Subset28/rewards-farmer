"""Starting Edge for an account, and saying why it did not start.

Shared by main.py and check_selectors.py so both launch the same way and both
explain a failure instead of dumping a traceback.
"""

import logging
import os

from selenium import webdriver
from selenium.common.exceptions import NoSuchDriverException, WebDriverException

import accounts
import log_utils

HEADLESS = os.environ.get("REWARDS_HEADLESS", "").strip().lower() in ("1", "true", "yes")

# Unattended, but with a real (virtual) display for Edge to draw on instead of
# --headless=new. Microsoft does not credit the "Explore on Bing" cards to a
# headless browser: the same click-and-search that completed them in a headed
# Edge left them on "Activated" every time in headless one. Only takes effect
# together with HEADLESS, which keeps meaning "nothing is watching" everywhere
# else (no prompt at exit, no cursor overlay), and expects DISPLAY to be set,
# e.g. by wrapping the process in with-xvfb.sh.
VIRTUAL_DISPLAY = os.environ.get("REWARDS_VIRTUAL_DISPLAY", "").strip().lower() in ("1", "true", "yes")

logger = logging.getLogger(__name__)

HIDE_DRIVER_MARKERS_JS = (
	"(() => {"
	" for (const k of Object.getOwnPropertyNames(window)) {"
	"  if (/^(cdc_|rnd_)/.test(k)) { try { delete window[k]; } catch (e) {} }"
	" }"
	" const nativeFunctions = new Set();"
	" try {"
	"  const origToString = Function.prototype.toString;"
	"  const toStringProxy = new Proxy(origToString, {"
	"   apply: function(target, ctx, args) {"
	"    if (ctx && (nativeFunctions.has(ctx) || ctx === toStringProxy)) {"
	"     return 'function ' + (ctx.name || '') + '() { [native code] }';"
	"    }"
	"    return Reflect.apply(target, ctx, args);"
	"   }"
	"  });"
	"  Function.prototype.toString = toStringProxy;"
	"  nativeFunctions.add(origToString);"
	" } catch (e) {}"
	" const SEED = /*SEED*/0;"
	" const FAKE_VENDOR = 'Intel Inc.';"
	" const FAKE_RENDERER = 'Intel(R) Iris(R) Xe Graphics';"
	" const getParameterProxyHandler = {"
	"  apply: function(target, ctx, args) {"
	"   const param = args[0];"
	"   if (param === 37445) return FAKE_VENDOR;"
	"   if (param === 37446) return FAKE_RENDERER;"
	"   if (param === 7936) return 'WebKit';"
	"   if (param === 7937) return 'WebKit WebGL';"
	"   return Reflect.apply(target, ctx, args);"
	"  }"
	" };"
	" try {"
	"  if (typeof WebGLRenderingContext !== 'undefined') {"
	"   WebGLRenderingContext.prototype.getParameter = new Proxy(WebGLRenderingContext.prototype.getParameter, getParameterProxyHandler);"
	"  }"
	"  if (typeof WebGL2RenderingContext !== 'undefined') {"
	"   WebGL2RenderingContext.prototype.getParameter = new Proxy(WebGL2RenderingContext.prototype.getParameter, getParameterProxyHandler);"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (typeof CanvasRenderingContext2D !== 'undefined') {"
	"   const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;"
	"   CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {"
	"    const data = origGetImageData.apply(this, arguments);"
	"    if (SEED && data && data.data && data.data.length > 32) {"
	"     for (let i = 0; i < 4; i++) {"
	"      const idx = ((SEED * (i + 1) * 31 + w * 7 + h) % (data.data.length / 4)) * 4;"
	"      data.data[idx] = (data.data[idx] ^ (1 + (SEED % 3))) & 255;"
	"     }"
	"    }"
	"    return data;"
	"   };"
	"   nativeFunctions.add(CanvasRenderingContext2D.prototype.getImageData);"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (typeof HTMLCanvasElement !== 'undefined') {"
	"   const origToDataURL = HTMLCanvasElement.prototype.toDataURL;"
	"   HTMLCanvasElement.prototype.toDataURL = function() {"
	"    if (SEED && this.width > 0 && this.height > 0) {"
	"     try {"
	"      const ctx = this.getContext('2d');"
	"      if (ctx) {"
	"       ctx.fillStyle = 'rgba(' + (SEED % 5) + ',1,1,0.01)';"
	"       ctx.fillRect(SEED % this.width, (SEED * 7) % this.height, 1, 1);"
	"      }"
	"     } catch (err) {}"
	"    }"
	"    return origToDataURL.apply(this, arguments);"
	"   };"
	"   nativeFunctions.add(HTMLCanvasElement.prototype.toDataURL);"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (typeof AudioBuffer !== 'undefined') {"
	"   const origGetChannelData = AudioBuffer.prototype.getChannelData;"
	"   AudioBuffer.prototype.getChannelData = function(channel) {"
	"    const array = origGetChannelData.apply(this, arguments);"
	"    if (SEED && array && array.length > 64) {"
	"     const offset = (SEED % 10) * 1e-7;"
	"     const step = Math.floor(array.length / 8);"
	"     for (let i = 0; i < array.length; i += step) {"
	"      array[i] += offset;"
	"     }"
	"    }"
	"    return array;"
	"   };"
	"   nativeFunctions.add(AudioBuffer.prototype.getChannelData);"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (typeof document !== 'undefined' && document.fonts && document.fonts.check) {"
	"   const origCheck = document.fonts.check;"
	"   document.fonts.check = function(font, text) {"
	"    if (/(Segoe UI|Calibri|Arial|Consolas|Tahoma|Verdana)/i.test(font)) return true;"
	"    return origCheck.apply(this, arguments);"
	"   };"
	"   nativeFunctions.add(document.fonts.check);"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (typeof window.chrome === 'undefined') window.chrome = {};"
	"  if (!window.chrome.csi) {"
	"   const csi = function csi() { return { startE: Date.now(), onloadT: Date.now(), pageT: 0, tran: 15 }; };"
	"   nativeFunctions.add(csi);"
	"   window.chrome.csi = csi;"
	"  }"
	"  if (!window.chrome.loadTimes) {"
	"   const lt = function loadTimes() { return { requestTime: Date.now() / 1000, startLoadTime: Date.now() / 1000, commitLoadTime: Date.now() / 1000, finishDocumentLoadTime: Date.now() / 1000, finishLoadTime: Date.now() / 1000, firstPaintTime: Date.now() / 1000, firstPaintAfterLoadTime: 0, navigationType: 'Other', wasFetchedViaSpdy: false, wasNpnNegotiated: false, npnNegotiatedProtocol: '', wasAlternateProtocolAvailable: false, connectionInfo: 'h2' }; };"
	"   nativeFunctions.add(lt);"
	"   window.chrome.loadTimes = lt;"
	"  }"
	"  if (!window.chrome.app) {"
	"   window.chrome.app = { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } };"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (navigator.permissions && navigator.permissions.query) {"
	"   const origQuery = navigator.permissions.query;"
	"   const patchedQuery = function query(p) {"
	"    if (p && p.name === 'notifications') return Promise.resolve({ state: Notification.permission });"
	"    return origQuery.apply(this, arguments);"
	"   };"
	"   nativeFunctions.add(patchedQuery);"
	"   navigator.permissions.query = patchedQuery;"
	"  }"
	" } catch (e) {}"
	" try {"
	"  delete Object.getPrototypeOf(navigator).webdriver;"
	"  delete navigator.webdriver;"
	"  Object.defineProperty(navigator, 'webdriver', { get: () => undefined, enumerable: false, configurable: true });"
	"  delete navigator.webdriver;"
	" } catch (e) {}"
	" try {"
	"  if (!navigator.deviceMemory) {"
	"   Object.defineProperty(navigator, 'deviceMemory', { get: () => 8, configurable: true });"
	"  }"
	" } catch (e) {}"
	" try {"
	"  if (window.outerWidth === 0) {"
	"   Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth, configurable: true });"
	"  }"
	"  if (window.outerHeight === 0) {"
	"   Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight + 85, configurable: true });"
	"  }"
	" } catch (e) {}"
	"})();"
)

# Matched in order against the driver's message, lowercased. selenium reports
# nearly every one of these as SessionNotCreatedException, so the class alone
# says nothing about which it was.
EXPLANATIONS = [
	("chrome instance exited", [
		"Edge exited during startup, before the driver could connect to it.",
		"Common causes: this profile is open in another Edge window, a lock left",
		"behind by a browser that was killed, or a profile directory Edge cannot",
		"write to. Set REWARDS_DRIVER_LOG=msedgedriver.log and run again; that",
		"log has Edge's own reason.",
	]),
	("cannot create default profile directory", [
		"Edge could not create the profile directory. Check that the current user",
		"can write to it without administrator rights.",
	]),
	("still attached to a running", [
		"This profile is already open in another Edge window, including one left",
		"over from a previous run or held by a container. Close it and try again.",
	]),
	("only supports microsoft edge version", [
		"msedgedriver and Edge versions do not match. Update msedgedriver to your",
		"Edge version, or remove the old one from PATH / MSEDGEDRIVER_PATH.",
	]),
]


DISK_CACHE_BYTES = 64 * 1024 * 1024


def has_gpu_device() -> bool:
	return os.path.exists("/dev/dri")


def build_options(account: accounts.Account) -> webdriver.EdgeOptions:
	options = webdriver.EdgeOptions()

	options.add_experimental_option("excludeSwitches", ["enable-automation"])
	options.add_experimental_option("useAutomationExtension", False)
	options.add_argument("--disable-blink-features=AutomationControlled")
	options.add_argument(f"--user-data-dir={account.user_data_dir}")
	options.add_argument(f"--profile-directory={account.profile_name}")

	# Edge's own cache otherwise grows to hundreds of MB per profile (and sits in the container's
	# memory as page cache). A cap is invisible to the pages: it only changes how much Edge keeps.
	options.add_argument(f"--disk-cache-size={DISK_CACHE_BYTES}")
	options.add_argument(f"--media-cache-size={DISK_CACHE_BYTES}")

	# WebRTC IP leak protection: prevent internal Docker or LAN IP leakage
	options.add_argument("--force-webrtc-ip-handling-policy=default_public_interface_only")
	options.add_argument("--enforce-webrtc-ip-permission-check")

	# TLS & BoringSSL cryptographic alignment with retail desktop Edge
	options.add_argument("--ssl-version-min=tls1.2")
	options.add_argument("--enable-features=PostQuantumKyber")

	# Prevent background throttling and extra crashpad processes
	options.add_argument("--disable-renderer-backgrounding")
	options.add_argument("--disable-breakpad")

	# Needed to register a script that runs in every tab, including the ones a
	# card opens; see hide_driver_markers.
	options.enable_bidi = True

	if os.environ.get("EDGE_BINARY"):
		options.binary_location = os.environ["EDGE_BINARY"]

	if HEADLESS:
		# A container has no display. The window size is set explicitly because
		# the pointer code works in viewport coordinates, and the default
		# headless window is small enough to put cards out of reach.
		if VIRTUAL_DISPLAY:
			# If a GPU device is available (e.g. Intel iGPU passed through from Synology DS1019+),
			# allow Edge to use true hardware acceleration. Otherwise fall back to SwiftShader.
			options.add_argument("--ignore-gpu-blocklist")
			if not has_gpu_device():
				options.add_argument("--use-angle=swiftshader")
				options.add_argument("--enable-unsafe-swiftshader")
		else:
			options.add_argument("--headless=new")

		options.add_argument("--window-size=1920,1080")
		options.add_argument("--no-sandbox")
		options.add_argument("--disable-dev-shm-usage")

	return options


def build_service() -> webdriver.EdgeService:
	driver_log = os.environ.get("REWARDS_DRIVER_LOG")

	# An explicit driver path skips Selenium Manager entirely, which is also
	# what fails in #79 when it cannot locate the Edge install.
	return webdriver.EdgeService(
		executable_path=os.environ.get("MSEDGEDRIVER_PATH") or None,
		service_args=["--verbose"] if driver_log else None,
		log_output=driver_log or None,
	)


def explain(exc: Exception) -> list[str]:
	if isinstance(exc, NoSuchDriverException):
		return [
			"selenium could not find msedgedriver or Edge on this machine.",
			"Set MSEDGEDRIVER_PATH to the full path of msedgedriver, and EDGE_BINARY",
			"to msedge if Edge is installed somewhere non-standard.",
		]

	message = str(exc).lower()

	for needle, lines in EXPLANATIONS:
		if needle in message:
			return lines

	return ["The driver's message is below; it did not match a known cause."]


def make_stealth_script(account_name: str | None = None) -> str:
	if not account_name:
		return HIDE_DRIVER_MARKERS_JS
	seed = sum((i + 1) * ord(c) for i, c in enumerate(account_name)) % 100000 + 1
	return HIDE_DRIVER_MARKERS_JS.replace("/*SEED*/0", f"/*SEED*/{seed}")


def hide_driver_markers(driver, account_name: str) -> None:
	"""Remove the cdc_ properties msedgedriver puts on every page's window.

	They are the classic Selenium tell: BrowserScan's Webdriver check reads them
	and calls the browser a robot, while navigator.webdriver, which
	AutomationControlled already masks, was never what it looked at. A preload
	script registered over BiDi runs before a page's own scripts in every tab,
	unlike a per-tab CDP script, which the tabs a card opens would not have.
	Needs both halves, found by testing on a throwaway profile: the BiDi script
	only reaches tabs opened after it is registered, so the tab the browser
	starts with, where the bot begins and reaches the Rewards pages, stayed
	"Robot" until the CDP script below was added for it. A new tab reads Normal
	with the first half alone.

	Best effort on purpose, and loud: a run that cannot hide them is still
	worth doing, but must not look as though it did.
	"""
	script = make_stealth_script(account_name)
	for label, register in (
		("every new tab", lambda: driver.script.pin(script)),
		("the starting tab", lambda: driver.execute_cdp_cmd(
			"Page.addScriptToEvaluateOnNewDocument", {"source": script}
		)),
	):
		try:
			register()
		except Exception as exc:
			logger.warning(
				"%s: could not hide the driver's cdc_ window properties for %s, so "
				"this run is easier to spot as automated: %s",
				account_name,
				label,
				log_utils.exception_summary(exc),
			)


def close_driver(driver, account_name: str = "", user_data_dir: str | None = None) -> None:
	"""Safely shut down the driver and terminate any lingering browser or driver processes."""
	if driver is None:
		return

	try:
		driver.quit()
	except Exception as exc:
		logger.warning(
			"%s: the driver did not shut down cleanly: %s",
			account_name,
			log_utils.exception_summary(exc),
		)
	finally:
		try:
			if hasattr(driver, "service") and getattr(driver.service, "process", None):
				proc = driver.service.process
				if proc and proc.poll() is None:
					proc.kill()
		except Exception:
			pass

		if user_data_dir:
			try:
				import run_lock
				run_lock.kill_orphaned_browsers(user_data_dir)
				run_lock.clear_stale_browser_locks(user_data_dir)
			except Exception:
				pass


def start_driver(account: accounts.Account):
	"""An Edge driver for the account, or None after logging why it failed."""
	try:
		import run_lock
		run_lock.clear_stale_browser_locks(account.user_data_dir)
		run_lock.kill_orphaned_browsers(account.user_data_dir)
	except Exception:
		pass

	try:
		driver = webdriver.Edge(options=build_options(account), service=build_service())
		hide_driver_markers(driver, account.name)

		return driver
	except WebDriverException as exc:
		logger.error("[FAIL] %s: could not start Edge with this profile.", account.name)
		logger.error("       profile directory: %s", account.user_data_dir)

		for line in explain(exc):
			logger.error("       %s", line)

		logger.error("       driver said: %s", log_utils.exception_summary(exc))

		return None
