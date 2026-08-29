from selenium.common.exceptions import WebDriverException, JavascriptException
from selenium import webdriver

GHOST_TAB_URLS = (
	"https://ntp.msn.com/edge/ntp?locale=en-US&title=New%20tab&fre=1&dsp=1&sp=Bing&feed_dis=always&en_widget_reg=false&prerender=1&PC=U531", # has fre
	"https://ntp.msn.com/edge/ntp?locale=en-US&title=New%20tab&dsp=1&sp=Bing&feed_dis=always&en_widget_reg=false&prerender=1&PC=U531" # no fre
)

class TabUtils:
	def __init__(self, driver: webdriver.Edge):
		self.driver = driver
		self.problematic_tabs = set()

	def ensure_focus(self):
		try:
			self.driver.execute_script("""
Object.defineProperty(document, 'hidden', { get: () => false });
Object.defineProperty(document, 'visibilityState', { get: () => 'visible' });
Document.prototype.hasFocus = function() { return true; };
window.hasFocus = function() { return true; };
document.dispatchEvent(new Event('visibilitychange'));
""")
		except JavascriptException: pass # it's probably a property redef exc

	def switch_to_other_tab(self):
		current_window = self.driver.current_window_handle

		for handle in self.driver.window_handles:
			if handle != current_window and handle not in self.problematic_tabs:
				self.driver.switch_to.window(handle)

				if self.driver.current_url in GHOST_TAB_URLS:
					print(f"[INFO] Found ghost tab with handle {handle} and URL {self.driver.current_url}.")
					continue

				self.ensure_focus()
				return

	def close_all_other_tabs(self, exceptions: list[str] = None):
		if exceptions is None:
			try:
				exceptions = [self.driver.current_window_handle]
			except WebDriverException:
				handles = self.driver.window_handles
				exceptions = [handles[0]] if handles else []

		switch_back_to = exceptions[0] if exceptions else None

		for handle in list(self.driver.window_handles):
			if handle not in exceptions and handle not in self.problematic_tabs:
				try:
					self.driver.switch_to.window(handle)

					if self.driver.current_url in GHOST_TAB_URLS:
						print(f"[INFO] Found ghost tab with handle {handle} and URL {self.driver.current_url}, not closing.")
						continue

					tab_url = self.driver.current_url

					self.driver.close()
					print(f"[INFO] Closed tab with handle {handle} and URL {tab_url}.")

				except WebDriverException:
					print(f"[WARNING] Could not close tab with handle {handle}.")
					self.problematic_tabs.add(handle)
					pass

		handles = self.driver.window_handles
		if switch_back_to and switch_back_to in handles:
			try:
				self.driver.switch_to.window(switch_back_to)
			except WebDriverException:
				if handles:
					self.driver.switch_to.window(handles[0])
		elif handles:
			self.driver.switch_to.window(handles[0])

		self.ensure_focus()