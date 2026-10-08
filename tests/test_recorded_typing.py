"""Typing the way a recorded person does: their rhythm, and keys that really go down and come up."""

import math
import os
import random
import statistics
import sys
import unittest
from datetime import date
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import behavior
import human_model
import mimic_typing
from mimic_typing import Keys

DETAIL = {
	"log_gap_mu": math.log(0.16), "within_sigma": 0.34, "sigma_sd": 0.05, "tempo_sd": 0.12, "tempo_phi": 0.3, "gap_phi": 0.3,
	"offset_alternate": -0.1, "offset_same_hand": 0.05, "offset_same_finger": 0.3, "offset_other": 0.1, "offset_common_pair": -0.2,
	"day_sd": 0.08, "hold_mu": math.log(85), "hold_sigma": 0.25, "rollover_rate": 0.35,
	"hesitation_rate": 0.0, "start_mu": math.log(0.9), "start_sigma": 0.3,
}


class FakeChains:
	"""Stands in for ActionChains and writes down what it was asked to do."""

	last = None

	def __init__(self, driver, duration=0):
		self.calls = []
		FakeChains.last = self

	def pause(self, seconds):
		self.calls.append(("pause", seconds))

		return self

	def send_keys(self, key):
		self.calls.append(("send", key))

		return self

	def key_down(self, key):
		self.calls.append(("down", key))

		return self

	def key_up(self, key):
		self.calls.append(("up", key))

		return self

	def perform(self):
		self.calls.append(("perform", None))

	def timeline(self):
		"""[(time, kind, key)] with the pauses added up."""
		now, out = 0.0, []

		for kind, value in self.calls:
			if kind == "pause":
				now += value
			elif kind in ("down", "up", "send"):
				out.append((now, kind, value))

		return out


class Case(unittest.TestCase):
	def setUp(self):
		for patcher in (
			mock.patch.object(mimic_typing, "ActionChains", FakeChains),
			mock.patch.dict(os.environ, {"REWARDS_FEATURES": "typing"}),
			mock.patch.object(mimic_typing.clock, "today", return_value=date(2026, 10, 8)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def keyboard(self, detail=None, name="mom"):
		profile = behavior.Behavior(name, "recorded", 0.4, 0.4, 0.2, 0.13, typing_detail=DETAIL if detail is None else detail)

		return mimic_typing.KeyboardUtils(mock.Mock(), profile, account=name)

	def type_text(self, keyboard, text, seed=1):
		keyboard.send_keys(text + Keys.ENTER, intended=text, rng=random.Random(seed))

		return FakeChains.last.timeline()


class TestEveryKeyGoesDownAndUp(Case):
	def test_each_key_is_pressed_and_released_once_in_order(self):
		timeline = self.type_text(self.keyboard(), "hello world")
		downs = [key for _, kind, key in timeline if kind == "down"]
		ups = [key for _, kind, key in timeline if kind == "up"]

		self.assertEqual("".join(k for k in downs[:-1]), "hello world")
		self.assertEqual(downs[-1], Keys.ENTER)
		self.assertEqual(sorted(map(str, downs)), sorted(map(str, ups)))

	def test_a_key_is_never_released_before_it_is_pressed(self):
		timeline = self.type_text(self.keyboard(), "the quick brown fox jumps over")
		down_at = {}

		for when, kind, key in timeline:
			if kind == "down":
				down_at[key] = when
			else:
				self.assertGreaterEqual(when, down_at[key])

	def test_the_first_key_waits_for_the_persons_own_beginning(self):
		first = self.type_text(self.keyboard(), "hello")[0][0]

		self.assertTrue(0.2 <= first <= 6.0)

	def test_times_only_move_forward(self):
		timeline = self.type_text(self.keyboard(), "weather in lisbon tomorrow")
		times = [when for when, _, _ in timeline]

		self.assertEqual(times, sorted(times))


class TestHoldsAndOverlap(unittest.TestCase):
	def events(self, rollover, count=600, seed=3, text=None):
		rng = random.Random(seed)
		timeline, now = [], 0.0
		letters = text or "abcdefghijklmnopqrstuvwxyz"

		for n in range(count):
			now += rng.uniform(0.08, 0.25)
			timeline.append((now, letters[n % len(letters)]))

		return timeline, mimic_typing.KeyboardUtils.key_events(timeline, {**DETAIL, "rollover_rate": rollover}, rng)

	def measure(self, timeline, events):
		down = {i: t for i, (t, _) in enumerate(timeline)}
		ups = [e for e in events if not e[1]]
		holds = []

		for index, (when, _) in enumerate(timeline):
			matching = [t for t, d, k in events if not d and k == timeline[index][1] and t >= when]
			holds.append(min(matching) - when)

		overlaps = sum(1 for i in range(len(timeline) - 1) if min(t for t, d, k in events if not d and k == timeline[i][1] and t >= down[i]) > down[i + 1])

		return holds, overlaps / (len(timeline) - 1), len(ups)

	def test_the_hold_times_follow_the_persons_own(self):
		timeline, events = self.events(0.0)
		holds, _, _ = self.measure(timeline, events)

		# Releases are capped when the next key follows soon, so allow the median to sit a little under the person's 85 ms.
		self.assertTrue(0.05 <= statistics.median(holds) <= 0.095)
		self.assertTrue(all(0.02 <= h <= 0.34 for h in holds))

	def test_the_overlap_rate_follows_the_persons_own(self):
		for rate in (0.0, 0.35, 0.8):
			timeline, events = self.events(rate)
			_, overlap, _ = self.measure(timeline, events)

			self.assertAlmostEqual(overlap, rate, delta=0.08, msg=f"rollover {rate}")

	def test_no_key_is_held_through_a_pause(self):
		rng = random.Random(1)
		timeline = [(0.0, "a"), (0.2, "b"), (1.4, "c"), (1.55, "d"), (3.0, "e")]
		events = mimic_typing.KeyboardUtils.key_events(timeline, {**DETAIL, "rollover_rate": 1.0}, rng)
		up = {key: when for when, down, key in events if not down}

		for when, key in timeline:
			self.assertLessEqual(up[key] - when, mimic_typing.MAX_HOLD + 1e-9, key)

	def test_a_quick_follow_up_can_overlap_but_a_slow_one_cannot(self):
		rng = random.Random(2)
		quick = mimic_typing.KeyboardUtils.key_events([(0.0, "a"), (0.15, "b")], {**DETAIL, "rollover_rate": 1.0}, rng)
		slow = mimic_typing.KeyboardUtils.key_events([(0.0, "a"), (0.9, "b")], {**DETAIL, "rollover_rate": 1.0}, rng)

		self.assertGreater(next(w for w, d, k in quick if not d and k == "a"), 0.15)
		self.assertLess(next(w for w, d, k in slow if not d and k == "a"), 0.9)

	def test_the_same_key_twice_never_overlaps_itself(self):
		timeline, events = self.events(1.0, count=200, text="aabbccdd")
		order = []

		for when, down, key in events:
			order.append((down, key))

		pressed = set()

		for down, key in order:
			if down:
				self.assertNotIn(key, pressed, "a key went down again while still held (a repeat)")
				pressed.add(key)
			else:
				pressed.discard(key)

	def test_events_are_in_time_order_and_a_press_comes_before_a_release_at_a_tie(self):
		_, events = self.events(0.5)

		self.assertEqual([e[0] for e in events], sorted(e[0] for e in events))


class TestItIsInconsistentInTheWayThePersonIs(Case):
	def gaps(self, keyboard, text, seed):
		timeline = self.type_text(keyboard, text, seed)
		downs = [when for when, kind, _ in timeline if kind == "down"]

		return [b - a for a, b in zip(downs, downs[1:])]

	def test_two_searches_are_not_typed_alike(self):
		keyboard = self.keyboard()
		first = self.gaps(keyboard, "best pizza near me tonight", 1)
		second = self.gaps(keyboard, "best pizza near me tonight", 2)

		self.assertNotEqual([round(g, 3) for g in first], [round(g, 3) for g in second])

	def test_the_tempo_differs_from_search_to_search_by_about_what_the_person_does(self):
		keyboard = self.keyboard({**DETAIL, "tempo_sd": 0.2, "day_sd": 0.0})
		means = [statistics.mean(math.log(g) for g in self.gaps(keyboard, "best pizza near me tonight", n)[1:-1]) for n in range(120)]

		self.assertGreater(statistics.pstdev(means), 0.10)

	def test_one_person_is_slower_than_another(self):
		quick = self.keyboard({**DETAIL, "log_gap_mu": math.log(0.10)}, "quick")
		slow = self.keyboard({**DETAIL, "log_gap_mu": math.log(0.30)}, "slow")
		text = "movies coming out next month"

		self.assertLess(statistics.median(self.gaps(quick, text, 1)), statistics.median(self.gaps(slow, text, 1)))

	def test_a_day_is_the_same_all_day_and_different_the_next(self):
		with mock.patch.object(mimic_typing.clock, "today", return_value=date(2026, 10, 8)):
			a = human_model.normal_for("day|mom|2026-10-08")
			b = human_model.normal_for("day|mom|2026-10-08")

		self.assertEqual(a, b)
		self.assertNotEqual(a, human_model.normal_for("day|mom|2026-10-09"))


class TestTheDriversOwnDelayIsTakenOff(Case):
	"""Asking the driver for a 150 ms gap and an 80 ms hold gives about 157 and 85; schedule less so the page sees the drawn figures."""

	def test_the_default_figures_are_the_measured_ones(self):
		gap, hold = mimic_typing._driver_latency()

		self.assertAlmostEqual(gap, 0.0074, places=4)
		self.assertAlmostEqual(hold, 0.0052, places=4)

	def test_it_can_be_overridden_and_a_bad_value_falls_back(self):
		with mock.patch.dict(os.environ, {"REWARDS_DRIVER_LATENCY_MS": "10,6"}):
			self.assertEqual(mimic_typing._driver_latency(), (0.010, 0.006))

		with mock.patch.dict(os.environ, {"REWARDS_DRIVER_LATENCY_MS": "fast"}):
			self.assertEqual(mimic_typing._driver_latency(), (0.0074, 0.0052))

	def test_scheduled_holds_are_shorter_by_the_holds_delay(self):
		timeline = [(0.0, "a"), (0.5, "b")]
		detail = {**DETAIL, "hold_alpha": math.log(80), "hold_beta": 0.0, "hold_gap_ref": math.log(0.5), "hold_resid": 1e-6}
		with_delay = mimic_typing.KeyboardUtils.key_events(timeline, detail, random.Random(1))

		with mock.patch.dict(os.environ, {"REWARDS_DRIVER_LATENCY_MS": "0,0"}):
			without = mimic_typing.KeyboardUtils.key_events(timeline, detail, random.Random(1))

		up = lambda events: next(w for w, d, k in events if not d and k == "a")

		self.assertAlmostEqual(up(without) - up(with_delay), 0.0052, places=4)

	def test_scheduled_gaps_are_shorter_by_the_gaps_delay(self):
		def total(env):
			with mock.patch.dict(os.environ, {"REWARDS_DRIVER_LATENCY_MS": env}):
				keyboard = self.keyboard({**DETAIL, "tempo_sd": 1e-6, "sigma_sd": 1e-6, "within_sigma": 0.0001, "day_sd": 0.0, "start_mu": math.log(1.0), "start_sigma": 1e-6})
				timeline = self.type_text(keyboard, "abcdefghijklmnopqrstuvwxy", seed=3)

				return [w for w, kind, key in timeline if kind == "down"][-1]

		self.assertAlmostEqual(total("0,0") - total("7.4,5.2"), 0.0074 * 25, delta=0.03)


class SamplingDriver:
	"""A browser that carries out what it is asked, a little late: each gap runs GAP long and each hold HOLD long."""

	def __init__(self, gap_extra=0.006, hold_extra=0.011, fail=False):
		self.gap_extra, self.hold_extra, self.fail = gap_extra, hold_extra, fail
		self.current_window_handle = "main"
		self.closed = 0
		self.restored = []
		self.opened = []
		self.switch_to = types.SimpleNamespace(
			new_window=lambda kind: self.opened.append(kind),
			window=lambda handle: self.restored.append(handle),
		)

	def get(self, url):
		self.url = url

	def find_element(self, *args):
		return types.SimpleNamespace(click=lambda: None)

	def close(self):
		self.closed += 1

	def execute_script(self, script):
		if self.fail:
			return []

		timeline = FakeChains.last.timeline()
		downs = [(w, k) for w, kind, k in timeline if kind == "down"]
		ups = [(w, k) for w, kind, k in timeline if kind == "up"]
		log = []

		for index, (when, key) in enumerate(downs):
			down_at = (when + index * self.gap_extra) * 1000
			held = next(w for w, k in ups if k == key and w >= when) - when
			log.append(["keydown", key, down_at])
			log.append(["keyup", key, down_at + (held + self.hold_extra) * 1000])
			ups.remove(next((w, k) for w, k in ups if k == key and w >= when))

		return log


import types


class TestMeasuringTheDriversDelay(Case):
	def keyboard_on(self, driver):
		profile = behavior.Behavior("mom", "recorded", 0.4, 0.4, 0.2, 0.13, typing_detail={**DETAIL, "hold_alpha": math.log(85), "hold_beta": 0.0, "hold_gap_ref": math.log(0.15), "hold_resid": 0.2})

		return mimic_typing.KeyboardUtils(driver, profile, account="mom")

	def test_the_delay_the_page_saw_is_what_gets_measured(self):
		driver = SamplingDriver(gap_extra=0.006, hold_extra=0.011)
		keyboard = self.keyboard_on(driver)
		gap, hold = keyboard.measured_latency(keyboard.recorded, random.Random(1))

		self.assertAlmostEqual(gap, 0.006, delta=0.0015)
		self.assertAlmostEqual(hold, 0.011, delta=0.0015)

	def test_it_is_measured_in_a_scratch_tab_and_the_original_is_restored(self):
		driver = SamplingDriver()
		keyboard = self.keyboard_on(driver)
		keyboard.measured_latency(keyboard.recorded, random.Random(1))

		self.assertEqual(driver.opened, ["tab"])
		self.assertEqual(driver.closed, 1)
		self.assertEqual(driver.restored, ["main"])

	def test_it_is_measured_once_per_run(self):
		driver = SamplingDriver()
		keyboard = self.keyboard_on(driver)
		first = keyboard.measured_latency(keyboard.recorded, random.Random(1))
		second = keyboard.measured_latency(keyboard.recorded, random.Random(2))

		self.assertEqual(first, second)
		self.assertEqual(driver.opened, ["tab"])

	def test_if_the_page_shows_nothing_the_measured_figures_from_the_nas_are_used(self):
		driver = SamplingDriver(fail=True)
		keyboard = self.keyboard_on(driver)

		self.assertEqual(keyboard.measured_latency(keyboard.recorded, random.Random(1)), mimic_typing._driver_latency())
		self.assertEqual(driver.restored, ["main"])

	def test_the_override_skips_the_measurement(self):
		driver = SamplingDriver()
		keyboard = self.keyboard_on(driver)

		with mock.patch.dict(os.environ, {"REWARDS_DRIVER_LATENCY_MS": "3,4"}):
			self.assertEqual(keyboard.measured_latency(keyboard.recorded, random.Random(1)), (0.003, 0.004))

		self.assertEqual(driver.opened, [])

	def test_a_huge_measurement_is_capped(self):
		driver = SamplingDriver(gap_extra=0.5, hold_extra=0.5)
		keyboard = self.keyboard_on(driver)

		self.assertEqual(keyboard.measured_latency(keyboard.recorded, random.Random(1)), (0.04, 0.04))

	def test_typing_uses_the_measured_delay(self):
		keyboard = self.keyboard_on(SamplingDriver(gap_extra=0.0, hold_extra=0.0))
		keyboard._latency = (0.020, 0.030)
		timeline = self.type_text(keyboard, "hello")
		holds = [u - d for (d, k1, key1), (u, k2, key2) in zip([t for t in timeline if t[1] == "down"], [t for t in timeline if t[1] == "up"]) if key1 == key2]

		with mock.patch.object(mimic_typing.KeyboardUtils, "measured_latency", return_value=(0.0, 0.0)):
			keyboard2 = self.keyboard_on(SamplingDriver())
			timeline2 = self.type_text(keyboard2, "hello")
			holds2 = [u - d for (d, k1, key1), (u, k2, key2) in zip([t for t in timeline2 if t[1] == "down"], [t for t in timeline2 if t[1] == "up"]) if key1 == key2]

		self.assertTrue(holds and holds2)
		self.assertLess(statistics.mean(holds), statistics.mean(holds2))


class TestNothingChangesForEveryoneElse(Case):
	def test_without_a_recording_the_original_typing_runs(self):
		keyboard = self.keyboard({})
		timeline = self.type_text(keyboard, "hello")

		self.assertTrue(all(kind == "send" for _, kind, _ in timeline))

	def test_a_recorded_rhythm_without_key_holds_sends_plain_key_presses(self):
		detail = {k: v for k, v in DETAIL.items() if k not in ("hold_mu", "hold_sigma", "rollover_rate")}
		timeline = self.type_text(self.keyboard(detail), "hello")

		self.assertTrue(timeline)
		self.assertTrue(all(kind == "send" for _, kind, _ in timeline))

	def test_with_the_feature_off_the_original_classic_typing_runs(self):
		with mock.patch.dict(os.environ, {"REWARDS_FEATURES": ""}):
			keyboard = self.keyboard()
			keyboard.send_keys("hello" + Keys.ENTER, intended="hello", rng=random.Random(1))

		self.assertTrue(all(kind == "send" for _, kind, _ in FakeChains.last.timeline()))


class TestACorrectionStillFinishesWithTheRightText(Case):
	def test_a_slip_is_backspaced_and_retyped(self):
		keyboard = self.keyboard({**DETAIL, "correction_rate": 1.0, "noticed_weights": [0, 1, 0, 0]})
		keyboard.send_keys("hexlo world" + Keys.ENTER, intended="hello world", rng=random.Random(2))
		downs = [key for _, kind, key in FakeChains.last.timeline() if kind == "down"]
		text = ""

		for key in downs:
			if key == Keys.BACKSPACE:
				text = text[:-1]
			elif key != Keys.ENTER:
				text += key

		self.assertEqual(text, "hello world")


if __name__ == "__main__":
	unittest.main()
