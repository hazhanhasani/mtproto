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
from manager import cron_publish

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

    def test_migrate_pre_cron_configuration(self):
        legacy = {"token": TOKEN, "admin_id": 123, "channel_id": "@testchannel",
                  "auto_publish": False, "posted": [], "offset": 12}
        settings.CONFIG.write_text(__import__("json").dumps(legacy))
        migrated = settings.get_config()
        self.assertEqual(migrated["scheduled_inbounds"], [])
        self.assertEqual(migrated["schedule_sent_slots"], {})
        self.assertEqual(migrated["offset"], 12)

    def test_schedule_toggle_requires_configured_bot_and_valid_id(self):
        with self.assertRaises(ValueError):
            settings.schedule_inbound("main", True)
        self.configured()
        settings.schedule_inbound("main", True)
        self.assertEqual(settings.get_config()["scheduled_inbounds"], ["main"])
        with self.assertRaises(ValueError):
            settings.schedule_inbound("../../etc/passwd", True)
        settings.schedule_inbound("main", False)
        self.assertEqual(settings.get_config()["scheduled_inbounds"], [])

    def test_half_hour_cron_dedupe_and_next_slot(self):
        self.configured()
        settings.schedule_inbound("main", True)
        with patch.object(cron_publish, "load_state", return_value=STATE), patch.object(
                cron_publish, "publish", return_value=55) as publish:
            self.assertEqual(cron_publish.publish_due(now=3600), 1)
            self.assertEqual(settings.get_config()["schedule_sent_slots"], {"main": 2})
            self.assertEqual(cron_publish.publish_due(now=3650), 0)
            self.assertEqual(cron_publish.publish_due(now=5400), 1)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(settings.get_config()["schedule_sent_slots"], {"main": 3})

    def test_half_hour_cron_skips_disabled_and_removed_inbounds(self):
        self.configured()
        settings.schedule_inbound("main", True)
        disabled = {"hostname": STATE["hostname"],
                    "profiles": [{**PROFILE, "enabled": False}]}
        with patch.object(cron_publish, "load_state", return_value=disabled), patch.object(
                cron_publish, "publish") as publish:
            self.assertEqual(cron_publish.publish_due(now=3600), 0)
        publish.assert_not_called()
        with patch.object(cron_publish, "load_state", return_value={
                "hostname": STATE["hostname"], "profiles": []}), patch.object(
                cron_publish, "publish") as publish:
            self.assertEqual(cron_publish.publish_due(now=3600), 0)
        publish.assert_not_called()

    def test_half_hour_cron_failure_does_not_mark_delivered(self):
        self.configured()
        settings.schedule_inbound("main", True)
        with patch.object(cron_publish, "load_state", return_value=STATE), patch.object(
                cron_publish, "publish", side_effect=bot.TelegramError("network fail")):
            with self.assertRaises(RuntimeError):
                cron_publish.publish_due(now=3600)
        self.assertEqual(settings.get_config()["schedule_sent_slots"], {})

    def test_channel_change_resets_cron_dedupe(self):
        self.configured()
        settings.schedule_inbound("main", True)
        settings.update_config(lambda data: data["schedule_sent_slots"].update({"main": 15}))
        settings.configure("", "123", "@newchannel", False, ["main"])
        self.assertEqual(settings.get_config()["schedule_sent_slots"], {})
        self.assertEqual(settings.get_config()["scheduled_inbounds"], ["main"])

    def test_cron_installer_has_persistent_half_hour_timer(self):
        installer = (ROOT / "install-bot.sh").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* *:00,30:00", installer)
        self.assertIn("Persistent=true", installer)
        self.assertIn("User=bluepanel", installer)
        self.assertIn("manager.cron_publish", installer)
        self.assertIn("systemctl enable --now blue-mtproxy-telegram-cron.timer", installer)

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
        self.assertIn("مخصوص کلاینت سازگار", web["text"])
        self.assertIn("<b>Port:</b> <code>443</code>", web["text"])
        self.assertIn("Main &amp; Fast", web["text"])
        self.assertNotIn("<b>Port:</b> <code>10443</code>", web["text"])
        self.assertEqual(
            web["reply_markup"]["inline_keyboard"][0][0]["text"],
            "🔗 اتصال WEB",
        )
        self.assertEqual(
            direct["reply_markup"]["inline_keyboard"][0][0]["text"],
            "🔗 اتصال به پروکسی",
        )
        self.assertNotIn("کلاینت سازگار", direct["text"])
        self.assertNotIn("⚠️", web["text"])
        self.assertLess(len(web["text"]), 350)

    def test_html_entities_are_escaped_in_channel_post(self):
        profile = {**PROFILE, "label": "<script> & Fast"}
        payload = bot.post_payload(STATE, profile, True)
        self.assertIn("&lt;script&gt; &amp; Fast", payload["text"])
        self.assertNotIn("<script>", payload["text"])
        self.assertEqual(payload["parse_mode"], "HTML")

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
        cdn_flag = pathlib.Path(self.temp.name) / "cdn-only"
        cdn_flag.touch()
        with patch.object(bot, "load_state", return_value=copy.deepcopy(STATE)), patch.object(
                bot, "CDN_FLAG", cdn_flag), patch.object(
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
