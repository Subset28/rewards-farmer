"""The original tool that recorded keypress timings; calibrate.py replaces it."""

import os
import tempfile
import time
import keyboard as kb
import pygetwindow as pygw
from matplotlib import pyplot as plt
from selenium import webdriver
from constants import USER_DATA_DIR, PROFILE_NAME

keypress_times: list[float] = []

def key_event_handler(event: kb.KeyboardEvent):
	if event.event_type == kb.KEY_DOWN:
		timestamp = event.time

		window = pygw.getActiveWindow()

		if window and "Edge" in window.title:
			keypress_times.append(timestamp)

kb.hook(key_event_handler)

options = webdriver.EdgeOptions()

options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument(f"--user-data-dir={USER_DATA_DIR}")
options.add_argument(f"--profile-directory={PROFILE_NAME}")

driver = webdriver.Edge(options=options)

driver.get("https://rewards.bing.com/")

input("Press Enter to exit...")

# Releasing the global hook here, rather than leaving it to interpreter
# shutdown, matters because antivirus treats a live low-level keyboard hook
# (this is how `keyboard` works) plus a process writing a timestamped keypress
# file as a keylogger signature and can transiently lock the new file for a
# scan; unhooking first shrinks that window instead of just retrying blind.
kb.unhook_all()

press_time_differences = [t2 - t1 for t1, t2 in zip(keypress_times[:-1], keypress_times[1:])]
lines = [str(diff)+'\n' for diff in press_time_differences]

def try_write(path: str) -> bool:
	for attempt in range(3):
		try:
			with open(path, "w") as f:
				f.writelines(lines)
			return True
		except PermissionError:
			if attempt < 2:
				time.sleep(1)
	return False

# A process holding a live low-level keyboard hook (this is how `keyboard`
# works) plus a script writing a timestamped keypress file matches a
# keylogger signature closely enough that some AV/EDR filter drivers deny the
# write outright -- not a race, so retrying the same path is pointless once
# it happens. The data only exists in memory at this point, so it falls back
# to a location outside the repo, and as a last resort prints it: it must
# never be silently lost just because one destination is blocked.
saved_path = None

if try_write("keypress_times.txt"):
	saved_path = os.path.abspath("keypress_times.txt")
else:
	fallback = os.path.join(tempfile.gettempdir(), "keypress_times.txt")
	print(f"keypress_times.txt was blocked in the repo dir, trying {fallback}")
	if try_write(fallback):
		saved_path = fallback

if saved_path:
	print(f"Saved {len(lines)} intervals to {saved_path}")
else:
	print("Could not write keypress_times.txt anywhere. Raw intervals (copy these into a file yourself):")
	print("".join(lines))

try:
	plt.hist(press_time_differences)
	plt.savefig("keypress_times.png")
except OSError as exc:
	print(f"Histogram PNG failed (not needed for analysis): {exc}")

driver.quit()