"""Build an account's behavior profile from a person's own recordings.

    python src/typing_test.py        # a fullscreen test: type 12 search-style phrases
                                     # -> writes keypress_times.txt when it finishes
    python src/fitts_law.py          # 18 quick clicks -> "MT = a + b * ID"
    python src/make_behavior_profile.py second --keys keypress_times.txt --fitts 0.43 0.16

    python src/make_behavior_profile.py --show second

The profile is written to data-dir/behavior/<account>.json. Record on the
machine and with the hands of the person the account belongs to: the point is
that two accounts do not move and type alike.
"""

import argparse
import sys

import behavior


def read_intervals(path: str) -> list[float]:
	with open(path, encoding="utf-8") as handle:
		return [float(line) for line in handle.read().split() if line.strip()]


def show(name: str) -> int:
	profile = behavior.load(name)
	fast, medium, slow = profile.typing_weights

	print(f"{name}: {profile.source} profile")
	print(f"  typing: {fast:.1%} of intervals under 0.1s, {medium:.1%} from 0.1 to 0.2s, {slow:.1%} slower")
	print(f"  mouse : Fitts' law MT = {profile.fitts_a:.4f} + {profile.fitts_b:.4f} * ID")

	if profile.source != "recorded":
		print(f"  not recorded; written to {behavior.profile_path(name)} once you run this tool with --keys and --fitts")

	return 0


def main(argv: list[str]) -> int:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("account", nargs="?", help="account name, as in REWARDS_ACCOUNTS")
	parser.add_argument("--keys", help="keypress_times.txt from typing_test.py")
	parser.add_argument("--fitts", nargs=2, type=float, metavar=("A", "B"), help="a and b from fitts_law.py")
	parser.add_argument("--show", action="store_true", help="print the profile in use and exit")
	args = parser.parse_args(argv[1:])

	if not args.account:
		parser.error("name the account")

	if args.show:
		return show(args.account)

	if not args.keys or not args.fitts:
		parser.error("both --keys and --fitts are needed to build a profile")

	try:
		fast, medium = behavior.typing_shares(read_intervals(args.keys))
		path = behavior.save(args.account, fast, medium, args.fitts[0], args.fitts[1], notes={"keys_file": args.keys})
	except (OSError, ValueError) as exc:
		print(f"Could not build the profile: {exc}")

		return 1

	print(f"Wrote {path}")

	return show(args.account)


if __name__ == "__main__":
	sys.exit(main(sys.argv))
