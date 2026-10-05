"""Account names are checked in full: `$` alone would let a trailing newline through."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import accounts


class TestAccountNames(unittest.TestCase):
	def test_a_trailing_newline_does_not_pass_the_name_check(self):
		for name in ("second\n", "a\n\n", "\n"):
			self.assertIsNone(accounts.SAFE_NAME.fullmatch(name), repr(name))

		self.assertIsNotNone(accounts.SAFE_NAME.fullmatch("second"))

	def test_a_name_with_a_newline_in_it_is_refused_by_configured(self):
		# Surrounding whitespace is trimmed before the check, so only an embedded newline can reach it.
		for raw in ("second\nx", "ok,bad\nname"):
			with self.subTest(raw=raw), mock.patch.dict(os.environ, {accounts.ENV_VAR: raw}), self.assertRaises(ValueError):
				accounts.configured()


if __name__ == "__main__":
	unittest.main()
