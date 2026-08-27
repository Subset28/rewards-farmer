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
# command line both handle without quoting. The character set alone is not
# enough: "." and ".." are made of allowed characters and still walk out of the
# directory, so they are rejected by name below and the resolved path is
# checked as well.
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")

# Reserved by every filesystem that has directories at all.
RESERVED_NAMES = {".", ".."}


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
	user_data_dir = os.path.join(USER_DATA_DIR, name)

	# The name passed the character check, but that only constrains the
	# characters, not where they end up pointing. Confirm against the resolved
	# path, which is the thing Edge is actually handed.
	root = os.path.abspath(USER_DATA_DIR)
	resolved = os.path.abspath(user_data_dir)

	if os.path.commonpath([root, resolved]) != root or resolved == root:
		raise ValueError(
			f"{ENV_VAR} entry {name!r} resolves outside the profile directory"
		)

	return Account(
		name=name,
		user_data_dir=user_data_dir,
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
		if not SAFE_NAME.match(name) or name in RESERVED_NAMES:
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
