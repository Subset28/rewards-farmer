"""Run the tangent runner in a real browser and watch memory, to see whether a long run fits the container's limit.

    with-xvfb python src/chain_probe.py 24 0.5

Starts Edge on a throwaway profile (never an account's, not signed in, so nothing is earned), builds the real
task code around it, and runs `count` searches as tangents with result pages opened `open_share` of the time
(more than a person would, to push memory). Memory is sampled every few seconds from the container's own
figures. Prints what was searched, how long it took, and the peak memory against the limit.
"""

import json
import logging
import os
import random
import sys
import tempfile
import threading
import time

import accounts
import behavior
import browser
import element_selectors
import memory_guard
import mimic_typing
import mouse_trajectory
import pace
import rewards_tasks
import tab_utils


def sampler(samples: list, stop: threading.Event):
	while not stop.is_set():
		figures = memory_guard.reading()

		if figures:
			samples.append((time.time(), figures[0] / 2**20, figures[1] / 2**20))
			print(f"MEM {figures[0] / 2**20:.0f} of {figures[1] / 2**20:.0f} MB", flush=True)

		stop.wait(10.0)


def run(count: int, open_share: float) -> dict:
	os.environ["REWARDS_FEATURES"] = "typing,mouse,chains"

	with tempfile.TemporaryDirectory(prefix="chain-probe-") as profile:
		account = accounts.Account(name="probe-chain", user_data_dir=profile, profile_name="Default")
		driver = browser.start_driver(account)

		if driver is None:
			return {"error": "driver did not start"}

		samples: list = []
		stop = threading.Event()
		thread = threading.Thread(target=sampler, args=(samples, stop), daemon=True)
		thread.start()

		try:
			person = behavior.provisional("probe-chain")
			page = object.__new__(rewards_tasks.RewardsTaskUtils)
			page.driver = driver
			page.account_name = "probe-chain"
			page.behavior = person
			page.main_window = driver.current_window_handle
			page.mouse = mouse_trajectory.MouseUtils(driver, person)
			page.keyboard = mimic_typing.KeyboardUtils(driver, person, account="probe-chain")
			page.elements = element_selectors.ElementSelectionUtils(driver)
			page.tab_utils = tab_utils.TabUtils(driver)
			page.pace = pace.Pace("probe-chain", {})
			searched = []
			original = rewards_tasks.query_history.record
			rewards_tasks.query_history.record = lambda account, query: searched.append(query)

			# Open results far more often than a person would, to find the memory ceiling.
			rewards_tasks.chains.OPEN_RESULT_CHANCE = open_share
			started = time.time()
			error = None

			try:
				page.run_chain_batch(count)
			except Exception as exc:  # reported, not hidden: this is a measurement
				error = f"{type(exc).__name__}: {str(exc)[:200]}"
			finally:
				rewards_tasks.query_history.record = original

			peak = max(samples, key=lambda s: s[1]) if samples else None

			return {
				"asked_for": count, "searched": len(searched), "seconds": round(time.time() - started),
				"queries": searched, "error": error,
				"peak_mb": round(peak[1]) if peak else None, "limit_mb": round(peak[2]) if peak else None,
				"share_of_limit": round(peak[1] / peak[2], 2) if peak else None,
				"memory_over_time_mb": [round(s[1]) for s in samples[::8]],
				"stopped_for_memory": len(searched) < count and bool(peak and peak[1] / peak[2] >= memory_guard.STOP_ABOVE),
			}
		finally:
			stop.set()
			driver.quit()


if __name__ == "__main__":
	logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", stream=sys.stdout)
	random.seed(int(os.environ.get("PROBE_SEED", "1")))
	print("RESULT " + json.dumps(run(int(sys.argv[1]) if len(sys.argv) > 1 else 20, float(sys.argv[2]) if len(sys.argv) > 2 else 0.5)))
	sys.exit(0)
