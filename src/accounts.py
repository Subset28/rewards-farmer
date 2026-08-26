"""Which accounts a run works through.

Rewards is per Microsoft account, and the browser profile is what holds the
sign-in, so an account here is just a profile directory. One directory per
account keeps their cookies apart, which is the whole requirement.

    REWARDS_ACCOUNTS=personal,spare python src/main.py

Unset, the run uses the single profile in constants.py exactly as before, so
nothing about an existing setup changes.
"""

import os
import re
from dataclasses import dataclass

from constants import USER_DATA_DIR, PROFILE_NAME

ENV_VAR = "REWARDS_ACCOUNTS"

# Names become directory names, so keep them to something a filesystem and a
# command line both handle without quoting.
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class Account:
	"""A named browser profile to run the tasks against."""

	name: str
	user_data_dir: str
	profile_name: str

	@property
	def is_default(self) -> bool:
		return self.user_data_dir == USER_DATA_DIR


def _named(name: str) -> Account:
	# Each account gets its own directory under the configured one, so the
	# existing data-dir stays where it is and the new ones sit beside the
	# profile it already holds.
	return Account(
		name=name,
		user_data_dir=os.path.join(USER_DATA_DIR, name),
		profile_name=PROFILE_NAME,
	)


def configured() -> list[Account]:
	"""Accounts for this run, in order.

	Raises ValueError on a name that cannot be a directory, rather than
	silently creating something surprising next to the real profiles.
	"""
	raw = os.environ.get(ENV_VAR, "").strip()

	if not raw:
		return [Account(name="default", user_data_dir=USER_DATA_DIR, profile_name=PROFILE_NAME)]

	names = [part.strip() for part in raw.split(",")]
	names = [name for name in names if name]

	if not names:
		return [Account(name="default", user_data_dir=USER_DATA_DIR, profile_name=PROFILE_NAME)]

	seen: set[str] = set()
	accounts: list[Account] = []

	for name in names:
		if not SAFE_NAME.match(name):
			raise ValueError(
				f"{ENV_VAR} entry {name!r} is not usable as a directory name; "
				"use letters, digits, dot, dash or underscore"
			)

		# Duplicates would run the same profile twice, which earns nothing the
		# second time and doubles the run length.
		if name.lower() in seen:
			continue

		seen.add(name.lower())
		accounts.append(_named(name))

	return accounts
