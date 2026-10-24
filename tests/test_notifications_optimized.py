import json
import os
import tempfile
import unittest
from unittest import mock

import notify
import weekly


class TestDiscordNotificationsOptimized(unittest.TestCase):
	HOOK = "https://discord.com/api/webhooks/123456789/fake-token"

	def test_discord_webhook_includes_bot_name_and_avatar(self):
		with mock.patch.dict(os.environ, {"NOTIFY_URL": self.HOOK}), mock.patch.object(notify.urllib.request, "urlopen") as post:
			self.assertTrue(notify.send("Daily points", "default: today 150", priority="default", account="default"))

		request = post.call_args.args[0]
		body = json.loads(request.data)
		self.assertEqual(body["username"], "Rewards Farmer")
		self.assertIn("microsoft-rewards.png", body["avatar_url"])
		self.assertEqual(body["embeds"][0]["footer"]["text"], "rewards-farmer • Synology DS1019+")

	def test_styles_include_amazon_puzzle_and_gold(self):
		# Amazon Gift Card Orange
		sign, colour = notify.style_for("Amazon Gift Card Ready", "default")
		self.assertEqual(sign, "📦")
		self.assertEqual(colour, 0xFF9900)

		# Puzzle Purple
		sign, colour = notify.style_for("Weekly puzzle piece claimed", "default")
		self.assertEqual(sign, "🧩")
		self.assertEqual(colour, 0x8B5CF6)

		# Gold Trophy
		sign, colour = notify.style_for("Gold status unlocked", "default")
		self.assertEqual(sign, "🏆")
		self.assertEqual(colour, 0xF59E0B)

		# Gift card emerald green
		sign, colour = notify.style_for("gift card available", "default")
		self.assertEqual(sign, "🎟")
		self.assertEqual(colour, 0x10B981)

	def test_discord_multipart_upload_when_image_provided(self):
		with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
			tf.write(b"\x89PNG\r\n\x1a\nfakeimagebytes")
			image_path = tf.name

		try:
			with mock.patch.dict(os.environ, {"NOTIFY_URL": self.HOOK}), mock.patch.object(notify.urllib.request, "urlopen") as post:
				self.assertTrue(
					notify.send("Rewards bot paused", "Session expired", priority="high", account="default", image_path=image_path)
				)

			request = post.call_args.args[0]
			content_type = request.get_header("Content-type")
			self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
			body_data = request.data
			self.assertIn(b"payload_json", body_data)
			self.assertIn(b"files[0]", body_data)
			self.assertIn(b"fakeimagebytes", body_data)
			self.assertIn(f"attachment://{os.path.basename(image_path)}".encode("utf-8"), body_data)
		finally:
			if os.path.exists(image_path):
				os.unlink(image_path)

	def test_weekly_redemption_threshold_is_6500(self):
		self.assertEqual(weekly.REDEEM_EVERY, 6500)


if __name__ == "__main__":
	unittest.main()
