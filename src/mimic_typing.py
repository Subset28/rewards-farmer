import random
from typing import Iterable
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains

# Measured from recordpress.py + analyze_keypresses.py against this user's
# own typing (267 keypress intervals, keypress_times.txt).
FIRST_INTERVAL = (0.0, 0.1)
SECOND_INTERVAL = (0.1, 0.2)
THIRD_INTERVAL = (0.2, 0.7)

FIRST_INTERVAL_PROBABILITY = 0.5019
SECOND_INTERVAL_PROBABILITY = 0.4195
THIRD_INTERVAL_PROBABILITY = 1 - (FIRST_INTERVAL_PROBABILITY + SECOND_INTERVAL_PROBABILITY)

class KeyboardUtils:
	def __init__(self, driver: webdriver.Edge, behavior=None):
		self.driver = driver
		# The account's own typing rhythm (behavior.py). Without one, the
		# original measured constants above.
		self.weights = list(behavior.typing_weights) if behavior else [
			FIRST_INTERVAL_PROBABILITY, SECOND_INTERVAL_PROBABILITY, THIRD_INTERVAL_PROBABILITY
		]

	def send_keys(self, keys: Iterable[str]):
		actions = ActionChains(self.driver, duration=0)

		for key in keys:
			actions.send_keys(key)

			interval = random.choices(
				[FIRST_INTERVAL, SECOND_INTERVAL, THIRD_INTERVAL],
				weights=self.weights
			)[0]

			actions.pause(random.uniform(interval[0], interval[1]))

		actions.perform()