"""A typing test that records the rhythm of your keystrokes.

    python src/typing_test.py
    python src/make_behavior_profile.py <account> --keys keypress_times.txt --fitts A B

A fullscreen window shows a phrase; type it as you normally would, fix mistakes
with Backspace, and the next one comes up when it matches. After one practice
phrase there are twelve real ones, search-style, in lower case, the way the bot
types. Esc quits without writing anything.

What is timed is the gap between one keystroke and the next inside a phrase: not
the gap between phrases, not shift and the other modifier keys (the bot sends one
keystroke per character), and not a held key repeating. The gaps are written to
keypress_times.txt, one per line, in seconds, which is what
make_behavior_profile.py reads.

The logic is in TypingSession, which knows nothing about the window, so it can be
tested without one.
"""

import argparse
import os
import random
import statistics
import sys
import time

PRACTICE = "type this to warm up"

PHRASES = (
	"best hiking trails near me this weekend",
	"how to cook salmon in an air fryer",
	"weather forecast for the next ten days",
	"cheap flights to lisbon in march 2026",
	"what time does the pharmacy close tonight",
	"easy weeknight dinner ideas for two people",
	"how to fix a leaky kitchen faucet",
	"who won the basketball game last night",
	"best budget laptops for college students",
	"how long to boil eggs for a soft yolk",
	"used car prices for a honda civic",
	"movies coming out next month worth watching",
)

# Keys that are not typing: modifiers, navigation and the like. Held down while
# typing a capital or a shortcut they would add intervals the bot never has.
NOT_TYPING = {
	"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R", "Caps_Lock", "Num_Lock", "Scroll_Lock",
	"Win_L", "Win_R", "Meta_L", "Meta_R", "Super_L", "Super_R", "Menu", "ISO_Level3_Shift", "Mode_switch",
	"Return", "KP_Enter", "Tab", "Escape", "Left", "Right", "Up", "Down", "Home", "End", "Prior", "Next",
	"Insert", "Delete", "Pause", "Print", "App",
}

# Longer than this between two keystrokes is thinking, not typing rhythm, and is
# left out of the speed figure (the profile builder applies its own cut as well).
PAUSE = 2.0

MIN_INTERVALS = 30


class TypingSession:
	"""The phrases, what has been typed, and the gaps between keystrokes."""

	def __init__(self, phrases=PHRASES, practice: str | None = PRACTICE, shuffle: bool = True, rng=random):
		order = list(phrases)

		if shuffle:
			rng.shuffle(order)

		self.items = ([(practice, False)] if practice else []) + [(p, True) for p in order]
		self.position = 0
		self.typed = ""
		self.intervals: list[float] = []
		self.keystrokes = 0
		self.errors = 0
		self._previous: float | None = None
		self._down: set[str] = set()

	@property
	def finished(self) -> bool:
		return self.position >= len(self.items)

	@property
	def target(self) -> str:
		return self.items[self.position][0] if not self.finished else ""

	@property
	def counted(self) -> bool:
		return self.items[self.position][1] if not self.finished else False

	@property
	def real_total(self) -> int:
		return sum(1 for _, counted in self.items if counted)

	@property
	def real_done(self) -> int:
		return sum(1 for _, counted in self.items[:self.position] if counted)

	@property
	def matches(self) -> int:
		"""How many leading characters of what is typed are right."""
		count = 0

		for typed, wanted in zip(self.typed, self.target):
			if typed != wanted:
				break

			count += 1

		return count

	def release(self, keysym: str) -> None:
		self._down.discard(keysym)

	def press(self, keysym: str, char: str, now: float) -> str:
		"""One key going down at time `now`. Returns what it did: "typed", "back", "done", "finished" or "ignored"."""
		if self.finished:
			return "ignored"

		if keysym in self._down:
			# A held key repeating: the first press counted, the repeats do not.
			return "ignored"

		self._down.add(keysym)

		if keysym in NOT_TYPING:
			return "ignored"

		if keysym == "BackSpace":
			action = "back"
			self.typed = self.typed[:-1]
		elif len(char) == 1 and char.isprintable():
			action = "typed"
			self.typed += char

			if not self.target.startswith(self.typed):
				self.errors += 1 if self.counted else 0
		else:
			# Ctrl+V and other combinations arrive with a control character or none.
			return "ignored"

		if self.counted:
			self.keystrokes += 1

			if self._previous is not None:
				self.intervals.append(now - self._previous)

		self._previous = now

		if self.typed == self.target:
			self.position += 1
			self.typed = ""
			self._previous = None

			return "finished" if self.finished else "done"

		return action

	def summary(self) -> dict:
		gaps = [i for i in self.intervals if 0 <= i < PAUSE]
		active = sum(gaps)
		fast = sum(i < 0.1 for i in self.intervals if 0 <= i < 0.7)
		medium = sum(0.1 <= i < 0.2 for i in self.intervals if 0 <= i < 0.7)
		typed = sum(1 for i in self.intervals if 0 <= i < 0.7)

		return {
			"keystrokes": self.keystrokes,
			"intervals": len(self.intervals),
			"errors": self.errors,
			"error_rate": self.errors / self.keystrokes if self.keystrokes else 0.0,
			"median_gap": statistics.median(gaps) if gaps else 0.0,
			"words_per_minute": (len(gaps) / 5) / (active / 60) if active > 0 else 0.0,
			"fast_share": fast / typed if typed else 0.0,
			"medium_share": medium / typed if typed else 0.0,
		}

	def write_intervals(self, path: str) -> None:
		with open(path, "w", encoding="utf-8") as handle:
			handle.writelines(f"{interval}\n" for interval in self.intervals)


def run_window(session: TypingSession) -> bool:
	"""Show the test. Returns False if it was abandoned with Esc."""
	import tkinter as tk

	root = tk.Tk()
	root.title("Typing Calibration")
	root.attributes("-fullscreen", True)
	root.attributes("-topmost", True)
	root.configure(bg="#f3f3f3")

	width = root.winfo_screenwidth()
	header = tk.Label(root, bg="#f3f3f3", fg="#111111", font=("Segoe UI", 20))
	header.pack(pady=(80, 10))
	hint = tk.Label(root, bg="#f3f3f3", fg="#555555", font=("Segoe UI", 14), text="Type it the way you normally would. Backspace fixes mistakes. Esc quits.")
	hint.pack(pady=(0, 60))
	target = tk.Label(root, bg="#f3f3f3", fg="#111111", font=("Consolas", 32), wraplength=width - 200)
	target.pack(pady=20)
	typed = tk.Text(root, height=1, width=60, font=("Consolas", 32), bg="#ffffff", relief="solid", borderwidth=1, cursor="arrow", takefocus=0)
	typed.tag_configure("ok", foreground="#1b7f3a")
	typed.tag_configure("bad", foreground="#d32f2f", background="#fdeaea")
	typed.pack(pady=20)
	status = tk.Label(root, bg="#f3f3f3", fg="#555555", font=("Segoe UI", 14))
	status.pack(pady=20)

	result = {"abandoned": False}

	def refresh() -> None:
		if session.finished:
			return

		if session.counted:
			header.configure(text=f"Phrase {session.real_done + 1} of {session.real_total}")
		else:
			header.configure(text="Practice (not counted)")

		target.configure(text=session.target)
		ok = session.matches
		typed.configure(state="normal")
		typed.delete("1.0", "end")
		typed.insert("end", session.typed[:ok], "ok")
		typed.insert("end", session.typed[ok:], "bad")
		typed.configure(state="disabled")
		status.configure(text=f"{session.keystrokes} keystrokes so far")

	def on_press(event) -> str:
		outcome = session.press(event.keysym, event.char, time.perf_counter())

		if outcome == "finished":
			root.destroy()

			return "break"

		refresh()

		return "break"

	def on_release(event) -> str:
		session.release(event.keysym)

		return "break"

	def abandon(_event=None) -> None:
		result["abandoned"] = True
		root.destroy()

	root.bind("<KeyPress>", on_press)
	root.bind("<KeyRelease>", on_release)
	root.bind("<Escape>", abandon)
	root.bind("<<Paste>>", lambda _e: "break")
	root.focus_force()
	refresh()
	root.mainloop()

	return not result["abandoned"]


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--out", default="keypress_times.txt", help="where to write the gaps (default: keypress_times.txt)")
	args = parser.parse_args(argv[1:])

	session = TypingSession()

	if not run_window(session):
		print("Quit before the end. Nothing was written.")

		return 1

	if len(session.intervals) < MIN_INTERVALS:
		print(f"Only {len(session.intervals)} usable gaps were recorded, need at least {MIN_INTERVALS}. Nothing was written.")

		return 1

	session.write_intervals(args.out)
	stats = session.summary()

	print(f"Wrote {len(session.intervals)} gaps to {os.path.abspath(args.out)}")
	print(f"  keystrokes     : {stats['keystrokes']} ({stats['errors']} wrong, {stats['error_rate']:.1%})")
	print(f"  median gap     : {stats['median_gap'] * 1000:.0f} ms")
	print(f"  speed          : about {stats['words_per_minute']:.0f} words per minute")
	print(f"  gaps under 0.1s: {stats['fast_share']:.1%}, 0.1 to 0.2s: {stats['medium_share']:.1%}")
	print("Next: python src/make_behavior_profile.py <account> --keys " + args.out + " --fitts A B")

	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
