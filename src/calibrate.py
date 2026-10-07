"""Record a person's own typing and mouse habits into a behavior profile, in one sitting.

    python src/calibrate.py <account>

One fullscreen window, a page after each page:

    1. an introduction
    2. typing: a practice phrase, then sixteen search-style phrases, typed the way you would into a
       search box (fix mistakes or leave them, press Enter when you are done)
    3. a short introduction to the mouse part
    4. mouse: click the dot, then the blue rectangle, eighteen times
    5. the result, and the profile written to data-dir/behavior/<account>.json

About five minutes. Do it on the machine and with the hands of the person the account belongs to.
Esc leaves at any point and writes nothing. What is measured is in calibration.py. The raw keys
and mouse movements go to data-dir/behavior/<account>.raw.json so a better analysis later does
not need the person back.

Then copy data-dir/behavior/<account>.json (and the .raw.json if you like) to the same place in
the NAS's data-dir. The profile holds only speeds and habits, nothing personal.

The window is App; every event it receives goes through a plain method (on_key_press and so on),
which is what the tests drive.
"""

import argparse
import json
import math
import os
import random
import sys
import time

import behavior
import calibration

TYPING_PHRASES = 16
MOUSE_TRIALS = 18
DOT_RADIUS = 18
DOT_CLICK_SLACK = 10

BG = "#f3f3f3"
INK = "#111111"
MUTED = "#555555"
GOOD = "#1b7f3a"
BAD = "#d32f2f"


class App:
	def __init__(self, account: str, root=None, clock=time.perf_counter, rng=random, phrases: int = TYPING_PHRASES, trials: int = MOUSE_TRIALS, save: bool = True):
		self.account = account
		self.root = root
		self.clock = clock
		self.rng = rng
		self.phrase_count = phrases
		self.trial_count = trials
		self.save = save
		self.page = "intro"
		self.typing: calibration.TypingRecorder | None = None
		self.trials: list[calibration.Trial] = []
		self.targets: list = []
		self.trial: calibration.Trial | None = None
		self.phase = "dot"
		self.message = ""
		self.outcome: dict | None = None
		self.abandoned = False
		self.size = (1920, 1080)
		self.widgets: dict = {}

		if root is not None:
			self._build_window()

		self.show_intro()

	# ------------------------------------------------------------------ the window

	def _build_window(self) -> None:
		import tkinter as tk

		self.tk = tk
		self.root.title("Calibration")
		self.root.attributes("-fullscreen", True)
		self.root.attributes("-topmost", True)
		self.root.configure(bg=BG)
		self.root.update_idletasks()
		self.size = (self.root.winfo_screenwidth(), self.root.winfo_screenheight())
		self.frame = tk.Frame(self.root, bg=BG)
		self.frame.pack(fill="both", expand=True)

		self.root.bind("<KeyPress>", lambda e: self._ask("on_key_press", e.keysym, e.char))
		self.root.bind("<KeyRelease>", lambda e: self._ask("on_key_release", e.keysym))
		self.root.bind("<Motion>", lambda e: self._ask("on_motion", e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty()))
		self.root.bind("<ButtonPress-1>", lambda e: self._ask("on_button_press", e.x_root - self.root.winfo_rootx(), e.y_root - self.root.winfo_rooty()))
		self.root.bind("<ButtonRelease-1>", lambda e: self._ask("on_button_release"))
		self.root.bind("<Escape>", lambda e: self.abandon())
		self.root.bind("<<Paste>>", lambda e: "break")
		self.root.focus_force()

	def _ask(self, method: str, *args):
		getattr(self, method)(*args)

		return "break"

	def _clear(self) -> None:
		if self.root is None:
			return

		for child in self.frame.winfo_children():
			child.destroy()

		self.widgets = {}

	def _label(self, text: str, size: int = 16, color: str = INK, pad=(10, 10), font: str = "Segoe UI", wrap: int = 1100):
		label = self.tk.Label(self.frame, text=text, bg=BG, fg=color, font=(font, size), wraplength=wrap, justify="center")
		label.pack(pady=pad)

		return label

	def abandon(self) -> None:
		self.abandoned = True

		if self.root is not None:
			self.root.destroy()

	def finish_window(self) -> None:
		if self.root is not None:
			self.root.destroy()

	# ------------------------------------------------------------------ 1. introduction

	def show_intro(self) -> None:
		self.page = "intro"
		self._clear()

		if self.root is None:
			return

		self._label("Calibration", 30, pad=(120, 20))
		self._label(f"This records how you type and move the mouse, for the account \"{self.account}\".", 18)
		self._label(
			"Part 1: type 16 short phrases, the way you would into a search box.\n"
			"Part 2: click a dot and then a blue rectangle, 18 times.\n\n"
			"It takes about five minutes. Type and click naturally; there is no right speed. Esc quits.",
			16, MUTED, (30, 40),
		)
		self._label("Press Enter to begin", 20, GOOD)

	# ------------------------------------------------------------------ 2. typing

	def start_typing(self) -> None:
		self.page = "typing"
		self.typing = calibration.TypingRecorder(count=self.phrase_count, rng=self.rng)
		self._clear()
		self._build_typing()
		self.typing.show(self.clock())
		self.refresh_typing()

	def _build_typing(self) -> None:
		if self.root is None:
			return

		tk = self.tk
		self.widgets["header"] = self._label("", 22, pad=(80, 6))
		self._label("Type it the way you normally would into a search box. Fix mistakes however you like, or leave them.\nPress Enter when you are done.", 14, MUTED, (0, 50))
		self.widgets["target"] = self._label("", 32, font="Consolas", pad=(20, 20), wrap=self.size[0] - 200)
		box = tk.Text(self.frame, height=1, width=60, font=("Consolas", 32), bg="#ffffff", relief="solid", borderwidth=1, cursor="arrow", takefocus=0)
		box.tag_configure("ok", foreground=GOOD)
		box.tag_configure("bad", foreground=BAD, background="#fdeaea")
		box.pack(pady=20)
		self.widgets["box"] = box
		self.widgets["status"] = self._label("", 14, MUTED, (20, 20))

	def refresh_typing(self) -> None:
		session = self.typing

		if self.root is None or session is None or session.finished:
			return

		header = f"Phrase {session.real_done + 1} of {session.real_total}" if session.counted else "Practice (not counted)"
		self.widgets["header"].configure(text=header)
		self.widgets["target"].configure(text=session.target)
		box = self.widgets["box"]
		right = session.matches
		box.configure(state="normal")
		box.delete("1.0", "end")
		box.insert("end", session.typed[:right], "ok")
		box.insert("end", session.typed[right:], "bad")
		box.configure(state="disabled")
		self.widgets["status"].configure(text="")

	# ------------------------------------------------------------------ 3. the mouse introduction

	def show_mouse_intro(self) -> None:
		self.page = "mouse_intro"
		self._clear()

		if self.root is None:
			return

		self._label("Part 2: the mouse", 30, pad=(120, 20))
		self._label(
			"A dot will appear in the middle. Click it, and a blue rectangle appears somewhere else:\n"
			"click the rectangle as quickly and as accurately as you comfortably can.\n"
			"Then the next dot. Use the mouse you normally use.",
			18, MUTED, (30, 40),
		)
		self._label("Press Enter to begin", 20, GOOD)

	# ------------------------------------------------------------------ 4. the mouse

	def start_mouse(self) -> None:
		self.page = "mouse"
		self.trials = []
		self.targets = calibration.make_targets(self.trial_count, self.size[0], self.size[1], self.rng)
		self.trial = None
		self._clear()

		if self.root is not None:
			canvas = self.tk.Canvas(self.frame, width=self.size[0], height=self.size[1], bg=BG, highlightthickness=0, cursor="arrow")
			canvas.pack(fill="both", expand=True)
			self.widgets["canvas"] = canvas

		self.next_dot()

	@property
	def centre(self) -> tuple[float, float]:
		return (self.size[0] / 2, self.size[1] / 2)

	def next_dot(self) -> None:
		self.phase = "dot"
		self.trial = None
		self.message = ""
		self.draw_mouse()

	def draw_mouse(self) -> None:
		canvas = self.widgets.get("canvas")

		if canvas is None:
			return

		canvas.delete("all")
		cx, cy = self.centre
		done = len(self.trials)
		canvas.create_text(self.size[0] // 2, 48, text=f"Click {done + 1} of {self.trial_count}", font=("Segoe UI", 20), fill=INK)

		if self.phase == "dot":
			canvas.create_oval(cx - DOT_RADIUS, cy - DOT_RADIUS, cx + DOT_RADIUS, cy + DOT_RADIUS, fill="#1f1f1f")
			canvas.create_text(self.size[0] // 2, 92, text="Click the dot", font=("Segoe UI", 16), fill=MUTED)
		elif self.trial is not None:
			x, y, w, h = self.trial.rect
			canvas.create_rectangle(x, y, x + w, y + h, fill="#5c8dff", outline="#0d2d73", width=3)
			canvas.create_text(self.size[0] // 2, 92, text=self.message or "Click the blue rectangle", font=("Segoe UI", 16), fill=BAD if self.message else MUTED)

	# ------------------------------------------------------------------ 5. the result

	def show_results(self) -> None:
		self.page = "results"
		self._clear()
		self.outcome = None

		try:
			typing = calibration.analyze_typing(self.typing.records if self.typing else [])
			mouse = calibration.analyze_mouse(self.trials)
			arguments = calibration.build(typing, mouse)
			lines = calibration.describe(typing, mouse)
			path = None

			if self.save:
				path = behavior.save(self.account, notes={"recorded_with": "calibrate.py"}, **arguments)
				self._save_raw(typing, mouse)

			self.outcome = {"typing": typing, "mouse": mouse, "path": path, "lines": lines, "error": None}
		except (behavior.ProfileError, ValueError, OSError) as exc:
			self.outcome = {"error": str(exc)}

		if self.root is None:
			return

		if self.outcome["error"]:
			self._label("That was not enough to go on", 30, BAD, (120, 20))
			self._label(self.outcome["error"], 16, MUTED, (10, 40))
			self._label("Press R to do the typing part again, M to do the mouse part again, Esc to quit", 18, INK)

			return

		self._label("Done", 30, GOOD, (100, 20))
		self._label("\n".join(self.outcome["lines"]), 15, INK, (10, 20), font="Consolas")

		if self.outcome["path"]:
			self._label(f"Saved to {os.path.abspath(self.outcome['path'])}", 13, MUTED, (10, 10))
			self._label("Copy it to the same folder in the NAS's data-dir.", 13, MUTED, (0, 10))

		self._label("Press Enter to close", 20, GOOD)

	def _save_raw(self, typing: dict, mouse: dict) -> None:
		path = os.path.splitext(behavior.profile_path(self.account))[0] + ".raw.json"

		with open(path, "w", encoding="utf-8") as handle:
			json.dump(calibration.raw(self.typing.records, self.trials), handle)

	# ------------------------------------------------------------------ events

	def on_key_press(self, keysym: str, char: str) -> None:
		now = self.clock()

		if self.page == "intro":
			if keysym in calibration.ENTER_KEYS:
				self.start_typing()
		elif self.page == "typing":
			outcome = self.typing.press(keysym, char, now)

			if outcome == "finished":
				self.show_mouse_intro()
			elif outcome == "done":
				self._clear()
				self._build_typing()
				self.typing.show(self.clock())
				self.refresh_typing()
			else:
				self.refresh_typing()
		elif self.page == "mouse_intro":
			if keysym in calibration.ENTER_KEYS:
				self.start_mouse()
		elif self.page == "results":
			if self.outcome and self.outcome.get("error"):
				if keysym.lower() == "r":
					self.start_typing()
				elif keysym.lower() == "m":
					self.start_mouse()
			elif keysym in calibration.ENTER_KEYS:
				self.finish_window()

	def on_key_release(self, keysym: str) -> None:
		if self.page == "typing" and self.typing is not None:
			self.typing.release(keysym)

	def on_motion(self, x: float, y: float) -> None:
		if self.page == "mouse" and self.phase == "target" and self.trial is not None:
			self.trial.move(self.clock(), x, y)

	def on_button_press(self, x: float, y: float) -> None:
		if self.page != "mouse":
			return

		now = self.clock()

		if self.phase == "dot":
			cx, cy = self.centre

			if math.hypot(x - cx, y - cy) <= DOT_RADIUS + DOT_CLICK_SLACK:
				# The trial starts where the pointer is, not at the dot's exact centre.
				self.trial = calibration.Trial((x, y), self.targets[len(self.trials)], now)
				self.phase = "target"
				self.draw_mouse()

			return

		if self.trial is not None and not self.trial.press(now, x, y) and self.trial.samples:
			self.message = "Missed. Click the blue rectangle."
			self.draw_mouse()

	def on_button_release(self) -> None:
		if self.page != "mouse" or self.phase != "target" or self.trial is None:
			return

		self.trial.release(self.clock())

		if not self.trial.done:
			return

		self.trials.append(self.trial)

		if len(self.trials) >= self.trial_count:
			self.show_results()
		else:
			self.next_dot()


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("account", help="account name, as in REWARDS_ACCOUNTS")
	parser.add_argument("--show", action="store_true", help="print the profile in use for this account and exit")
	args = parser.parse_args(argv[1:])

	if args.show:
		import make_behavior_profile

		return make_behavior_profile.show(args.account)

	import tkinter as tk

	root = tk.Tk()
	app = App(args.account, root)
	root.mainloop()

	if app.abandoned:
		print("Quit before the end. Nothing was written.")

		return 1

	outcome = app.outcome or {}

	if outcome.get("error"):
		print(f"Not enough to go on: {outcome['error']}")

		return 1

	print("\n".join(outcome.get("lines", [])))
	print(f"Wrote {outcome.get('path')}")

	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
