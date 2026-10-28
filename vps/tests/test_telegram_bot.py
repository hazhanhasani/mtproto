"""Telegram publisher authorization, links, configuration and delivery tests."""
import copy
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PANEL_SECRET_KEY", "test-bot-session-key-" + "x" * 32)
os.environ.setdefault("PANEL_ADMIN_PASSWORD_HASH", "test-not-used")
from manager import bot_config as settings
from manager import telegram_bot as bot

TOKEN = "123456789:" + "A" * 35
PROFILE = {"id": "main", "slot": 0, "label": "Main & Fast",
           "secret": "a" * 32, "enabled": True}
STATE = {"hostname": "proxy.example.com", "profiles": [PROFILE]}


class TelegramBotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.p1 = patch.object(settings, "CONFIG", pathlib.Path(self.temp.name) / "bot.json")
        self.p2 = patch.object(settings, "LOCK", pathlib.Path(self.temp.name) / "bot.lock")
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def configured(self, auto=False):
        return settings.configure(TOKEN, "123", "@testchannel", auto, ["main"])

    def test_secure_config_keeps_token_on_blank_and_seeds_existing(self):
        config = self.configured(auto=True)
        self.assertTrue(config["auto_publish"])
        self.assertEqual(config["posted"], ["main"])
        new = settings.configure("", "123", "@anotherchannel", True, ["main", "p123"])
        self.assertEqual(new["token"], TOKEN)
        self.assertEqual(new["posted"], ["main", "p123"])
        self.assertEqual(settings.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_new_bot_token_resets_update_cursor(self):
        self.configured()
        settings.update_config(lambda data: data.__setitem__("offset", 501))
        settings.configure("987654321:" + "B" * 35, "123", "@testchannel", False, ["main"])
        self.assertEqual(settings.get_config()["offset"], 0)

    def test_panel_template_compiles(self):
        from app import app as flask_app
        flask_app.jinja_env.get_template("manager.html")

    def test_validation_rejects_invalid_settings(self):
        with self.assertRaises(ValueError):
            settings.configure("not-a-token", "123", "@testchannel", False, [])
        with self.assertRaises(ValueError):
            settings.configure("", "abc", "@testchannel", False, [])
        with self.assertRaises(ValueError):
            settings.configure(TOKEN, "123", "https://untrusted", False, [])
        with self.assertRaises(ValueError):
            settings.configure("", "0", "@testchannel", True, [])

    def test_native_and_cdn_links_are_not_confused(self):
        direct = bot.post_payload(STATE, PROFILE, False)
        web = bot.post_payload(STATE, PROFILE, True)
        self.assertIn("port=10443", direct["reply_markup"]["inline_keyboard"][0][0]["url"])
        self.assertEqual(web["reply_markup"]["inline_keyboard"][0][0]["url"],
                         "https://t.me/webproxy?server=proxy.example.com&secret=" + "a"*32)
        self.assertIn("کلاینت سازگار", web["text"])
        self.assertIn("Port: <code>443</code>", web["text"])
        self.assertIn("Main &amp; Fast", web["text"])
        self.assertNotIn("Port: <code>10443</code>", web["text"])

    def test_unauthorized_private_or_group_commands_do_nothing(self):
        config = self.configured()
        with patch.object(bot, "telegram_api") as telegram, patch.object(
                bot, "create_profile") as create:
            bot.handle_message(config, {"from": {"id": 124},
                                        "chat": {"id": 124, "type": "private"},
                                        "text": "/new Leak"})
            bot.handle_message(config, {"from": {"id": 123},
                                        "chat": {"id": -1, "type": "group"},
                                        "text": "/new Leak"})
            bot.handle_callback(config, {"id": "1", "from": {"id": 124},
                                         "message": {"chat": {"id": 124, "type": "private"}},
                                         "data": "pub:main"})
        telegram.assert_not_called()
        create.assert_not_called()

    def test_authorized_new_command_creates_inbound(self):
        config = self.configured()
        with patch.object(bot, "create_profile", return_value="p1234") as create, patch.object(
                bot, "telegram_api", return_value={"message_id": 1}) as api:
            bot.handle_message(config, {"from": {"id": 123}, "chat": {"id": 123, "type": "private"},
                                        "text": "/new Fast Proxy"})
        create.assert_called_once_with("Fast Proxy")
        self.assertEqual(api.call_args.args[1], "sendMessage")

    def test_publish_to_channel_and_mark_prevent_repeat_auto_post(self):
        self.configured(auto=True)
        with patch.object(bot, "load_state", return_value=copy.deepcopy(STATE)), patch.object(
                bot.CDN_FLAG, "is_file", return_value=True), patch.object(
                bot, "telegram_api", return_value={"message_id": 77}) as api:
            self.assertEqual(bot.publish("main"), 77)
            self.assertIn("main", settings.get_config()["posted"])
            bot.auto_publish_once(settings.get_config())
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.args[1], "sendMessage")
        self.assertEqual(api.call_args.args[2]["chat_id"], "@testchannel")

    def test_failing_publish_does_not_mark_posted(self):
        self.configured(auto=True)
        with patch.object(bot, "load_state", return_value=copy.deepcopy(STATE)), patch.object(
                bot, "telegram_api", side_effect=bot.TelegramError("channel not found")):
            with self.assertRaises(bot.TelegramError):
                bot.publish("main")
        self.assertEqual(settings.get_config()["posted"], ["main"])
        # The previously seeded item stays seeded; a new id is retryable.
        settings.update_config(lambda data: data["posted"].clear())
        with patch.object(bot, "load_state", return_value=copy.deepcopy(STATE)), patch.object(
                bot, "telegram_api", side_effect=bot.TelegramError("channel not found")):
            with self.assertRaises(bot.TelegramError):
                bot.auto_publish_once(settings.get_config())
        self.assertEqual(settings.get_config()["posted"], [])


if __name__ == "__main__":
    unittest.main()
