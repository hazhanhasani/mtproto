"""Telegram Bot API long-polling channel publisher for Blue Proxy Manager.

No public webhook, inbound port or third-party bot library is required.
All privileged proxy changes go through app.apply_mutation and its
existing root-owned control broker. Only the configured Telegram UID can act.
"""
import html
import json
import logging
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app import CDN_FLAG, apply_mutation, get_profile, load_state
from manager.bot_config import get_config, ready, update_config
from manager.model import direct_url, new_secret, web_url

LOG = logging.getLogger("blue-mtproxy-bot")


class TelegramError(RuntimeError):
    pass


def telegram_api(token, method, data, timeout=25):
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    request = Request(
        "https://api.telegram.org/bot" + token + "/" + method,
        data=payload, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read(128 * 1024))
        if not isinstance(result, dict) or not result.get("ok"):
            reason = str(result.get("description", "Telegram API error"))[:180] if isinstance(result, dict) else "Invalid Telegram response"
            raise TelegramError(reason)
        return result.get("result")
    except HTTPError as exc:
        # Never include the URL: it contains the secret bot token.
        try:
            body = json.loads(exc.read(4096))
            reason = str(body.get("description", "HTTP " + str(exc.code)))[:180]
        except (ValueError, UnicodeError, OSError):
            reason = "HTTP " + str(exc.code)
        raise TelegramError(reason) from None
    except (URLError, TimeoutError, OSError, ValueError, UnicodeError):
        raise TelegramError("ارتباط با Telegram Bot API برقرار نشد") from None


def post_payload(state, profile, cdn_only):
    """Render a compact branded post with the correct transport link."""
    host = state["hostname"]
    secret = profile["secret"]
    if cdn_only:
        port = 443
        url = web_url(host, secret)
        transport = "WEB · Cloudflare"
        button = "🔗 اتصال WEB"
        compatibility = " · مخصوص کلاینت سازگار"
    else:
        port = 10443 + profile["slot"]
        url = direct_url(host, secret, port)
        transport = "MTProto"
        button = "🔗 اتصال به پروکسی"
        compatibility = ""

    message = (
        "🔷 <b>BLUE PROXY</b>\n"
        "📡 <b>" + html.escape(profile["label"]) + "</b>\n\n"
        "🌐 <b>Server:</b> <code>" + html.escape(host) + "</code>\n"
        "🔌 <b>Port:</b> <code>" + str(port) + "</code>\n"
        "🔑 <b>Secret:</b> <code>" + html.escape(secret) + "</code>\n\n"
        "<i>" + transport + compatibility + "</i>"
    )
    return {
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": {"inline_keyboard": [[{"text": button, "url": url}]]},
    }


def publish(profile_id, *, token=None, channel=None, client_id=None):
    config = get_config()
    if not ready(config):
        raise ValueError("تنظیمات ربات، مدیر یا کانال ناقص است")
    state = load_state()
    profile = get_profile(state, profile_id)
    if not profile["enabled"]:
        raise ValueError("اینباند غیرفعال را نمی‌توان منتشر کرد")
    # Scheduled posts must contain a dedicated channel user's credential,
    # never the inbound/owner secret. Re-check membership and enabled state
    # against live state immediately before Telegram delivery.
    selected = profile
    if client_id is not None:
        client = next(
            (c for c in profile.get("clients", []) if c["id"] == client_id),
            None,
        )
        if not client or not client.get("channel_pool") or not client["enabled"]:
            raise ValueError("کاربر اختصاصی کانال یافت نشد یا غیرفعال است")
        selected = {**profile, "secret": client["secret"]}
    destination = channel or config["channel_id"]
    params = post_payload(state, selected, CDN_FLAG.is_file())
    response = telegram_api(token or config["token"], "sendMessage", {
        "chat_id": destination, **params,
        "disable_web_page_preview": True,
    })
    if not isinstance(response, dict) or type(response.get("message_id")) is not int:
        raise TelegramError("پاسخ انتشار تلگرام تأیید نشد")

    def mark(data):
        # If the channel was changed during the HTTP request, do not mark
        # the new channel as published.
        if data["channel_id"] == destination and profile_id not in data["posted"]:
            data["posted"] = (data["posted"] + [profile_id])[-1024:]
    update_config(mark)
    return response["message_id"]


def create_profile(label):
    label = label.strip()
    if not (1 <= len(label) <= 80):
        raise ValueError("عنوان پروکسی باید بین ۱ تا ۸۰ نویسه باشد")
    profile_id = "p" + secrets.token_hex(4)

    def change(state):
        occupied = {p["slot"] for p in state["profiles"]}
        slot = next((i for i in range(32) if i not in occupied), None)
        if slot is None:
            raise ValueError("ظرفیت ۳۲ اینباند تکمیل است")
        state["profiles"].append({
            "id": profile_id, "slot": slot, "label": label,
            "secret": new_secret(), "ad_tag": "", "channel": "",
            "enabled": True, "carrier": "websocket",
        })
    apply_mutation(change)
    return profile_id


def respond(config, chat_id, text, keyboard=None):
    data = {"chat_id": chat_id, "text": text}
    if keyboard:
        data["reply_markup"] = {"inline_keyboard": keyboard}
    return telegram_api(config["token"], "sendMessage", data)


def handle_message(config, message):
    sender = message.get("from") or {}
    chat = message.get("chat") or {}
    # A channel posting bot is not a public proxy vending bot.
    if sender.get("id") != config["admin_id"] or chat.get("type") != "private":
        return
    text = message.get("text", "").strip()
    command, _, arg = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    chat_id = chat["id"]
    try:
        if command in ("/start", "/help"):
            respond(config, chat_id,
                    "🛡 Blue Proxy Manager\n\n"
                    "/new عنوان — ساخت اینباند و Secret جدید\n"
                    "/list — اینباندها و دکمه انتشار\n"
                    "/publish شناسه — ارسال به کانال\n"
                    "/channel — نمایش کانال مقصد\n\n"
                    "تنظیمات و انتشار خودکار در داشبورد بخش «ربات کانال» قرار دارد.")
        elif command == "/channel":
            respond(config, chat_id, "📣 کانال مقصد: " + config["channel_id"])
        elif command == "/list":
            profiles = load_state()["profiles"]
            keyboard = [
                [{"text": ("🟢 " if p["enabled"] else "🔴 ") + p["label"][:45],
                  "callback_data": "pub:" + p["id"]}]
                for p in profiles if p["enabled"]
            ]
            respond(config, chat_id, "اینباند موردنظر برای انتشار را انتخاب کن:", keyboard or None)
        elif command == "/new":
            profile_id = create_profile(arg)
            respond(config, chat_id, "✅ اینباند ساخته شد: " + profile_id,
                    [[{"text": "📤 انتشار در کانال", "callback_data": "pub:" + profile_id}]])
            # Auto-post mode is picked up by the worker after the update.
        elif command == "/publish":
            profile_id = arg.strip()
            publish(profile_id)
            respond(config, chat_id, "✅ پروکسی در کانال منتشر شد.")
        elif command.startswith("/"):
            respond(config, chat_id, "دستور ناشناخته است. /help")
    except (ValueError, TelegramError, OSError, RuntimeError) as exc:
        respond(config, chat_id, "❌ " + str(exc)[:220])


def handle_callback(config, callback):
    sender = callback.get("from") or {}
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if sender.get("id") != config["admin_id"] or chat.get("type") != "private":
        return
    data = callback.get("data", "")
    answer = "دستور نامعتبر است"
    if isinstance(data, str) and data.startswith("pub:"):
        try:
            publish(data[4:])
            answer = "✅ در کانال منتشر شد"
        except (ValueError, TelegramError, OSError, RuntimeError) as exc:
            answer = "❌ " + str(exc)[:150]
    telegram_api(config["token"], "answerCallbackQuery", {
        "callback_query_id": callback["id"], "text": answer,
        "show_alert": answer.startswith("❌"),
    })


def handle_update(config, update):
    if "message" in update and isinstance(update["message"], dict):
        handle_message(config, update["message"])
    elif "callback_query" in update and isinstance(update["callback_query"], dict):
        handle_callback(config, update["callback_query"])


def auto_publish_once(config):
    if not (ready(config) and config["auto_publish"]):
        return
    for p in load_state()["profiles"]:
        if p["enabled"] and p["id"] not in config["posted"]:
            publish(p["id"])
            return  # Avoid Telegram flood limits; retry next iteration.


def serve():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOG.info("Telegram publisher started")
    previous_token = None
    while True:
        try:
            config = get_config()
            if not ready(config):
                time.sleep(5)
                continue
            if previous_token != config["token"]:
                previous_token = config["token"]
                LOG.info("Telegram publisher configured")
            updates = telegram_api(config["token"], "getUpdates", {
                "offset": config["offset"], "limit": 30, "timeout": 12,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=20)
            for update in updates:
                update_id = update.get("update_id")
                if type(update_id) is not int:
                    continue
                try:
                    handle_update(config, update)
                except (ValueError, TelegramError, RuntimeError, OSError):
                    LOG.warning("Bot update processing failed")
                # Advance the offset even if a command failed, preventing
                # a failing command from running infinitely.
                update_config(lambda data: data.__setitem__(
                    "offset", max(data["offset"], update_id + 1)))
            auto_publish_once(get_config())
            time.sleep(2)
        except (ValueError, TelegramError, RuntimeError, OSError):
            LOG.warning("Bot polling/publishing failed; check bot permissions and channel")
            time.sleep(12)


if __name__ == "__main__":
    serve()
