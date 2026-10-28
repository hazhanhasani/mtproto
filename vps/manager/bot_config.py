"""Persistent owner-only Telegram channel bot configuration.

The bot token never appears in Flask templates, logs or the Git repository.
The file belongs to bluepanel and is atomically replaced under a shared lock.
"""
import copy
import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

CONFIG = Path("/var/lib/blue-mtproxy/telegram-bot.json")
LOCK = CONFIG.with_name("telegram-bot.lock")
TOKEN_RE = re.compile(r"^[0-9]{6,16}:[A-Za-z0-9_-]{30,128}$")
CHANNEL_RE = re.compile(r"^(?:@[A-Za-z0-9_]{5,32}|-100[0-9]{6,16})$")
PROFILE_RE = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
DEFAULT = {
    "token": "", "admin_id": 0, "channel_id": "", "auto_publish": False,
    "posted": [], "offset": 0,
}


def validate_config(config):
    if not isinstance(config, dict) or set(config) != set(DEFAULT):
        raise ValueError("invalid Telegram bot configuration")
    token = config["token"]
    if not isinstance(token, str) or (token and not TOKEN_RE.fullmatch(token)):
        raise ValueError("توکن ربات معتبر نیست")
    if type(config["admin_id"]) is not int or not 0 <= config["admin_id"] <= 2**63 - 1:
        raise ValueError("شناسه عددی مدیر معتبر نیست")
    channel = config["channel_id"]
    if not isinstance(channel, str) or (channel and not CHANNEL_RE.fullmatch(channel)):
        raise ValueError("کانال باید @username یا شناسه عددی -100... باشد")
    if type(config["auto_publish"]) is not bool:
        raise ValueError("invalid auto_publish")
    posted = config["posted"]
    if (not isinstance(posted, list) or len(posted) > 1024 or
            any(not isinstance(p, str) or not PROFILE_RE.fullmatch(p) for p in posted) or
            len(set(posted)) != len(posted)):
        raise ValueError("invalid published profile list")
    if type(config["offset"]) is not int or not 0 <= config["offset"] < 2**63:
        raise ValueError("invalid bot offset")
    return config


def _read():
    try:
        if CONFIG.stat().st_size > 64 * 1024:
            raise ValueError("Telegram bot settings too large")
        return validate_config(json.loads(CONFIG.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return copy.deepcopy(DEFAULT)


def get_config():
    # os.replace guarantees readers see either a complete old or new file.
    return copy.deepcopy(_read())


def update_config(change):
    CONFIG.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open("a+") as lock:
        os.chmod(LOCK, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = copy.deepcopy(_read())
        change(data)
        validate_config(data)
        fd, tmp = tempfile.mkstemp(prefix=".bot-", dir=CONFIG.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, CONFIG)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return copy.deepcopy(data)


def configure(token, admin_id, channel_id, auto_publish, profile_ids):
    """Changing destination or enabling auto-post seeds existing profiles.

    Only newly created profiles get auto-posted, avoiding a surprise flood.
    Empty token input retains the previous token.
    """
    if not isinstance(token, str) or not isinstance(admin_id, str):
        raise ValueError("invalid settings")
    token = token.strip()
    if token and not TOKEN_RE.fullmatch(token):
        raise ValueError("توکن BotFather نامعتبر است")
    admin_id = admin_id.strip()
    if not admin_id.isascii() or (admin_id and not admin_id.isdecimal()):
        raise ValueError("شناسه مدیر باید عدد مثبت باشد")
    uid = int(admin_id) if admin_id else 0
    channel = channel_id.strip()
    profile_ids = list(profile_ids)

    def change(data):
        if token and token != data["token"]:
            data["token"] = token
            data["offset"] = 0
        before_channel = data["channel_id"]
        before_auto = data["auto_publish"]
        data["admin_id"] = uid
        data["channel_id"] = channel
        if auto_publish and not (data["token"] and uid and channel):
            raise ValueError("برای انتشار خودکار، توکن، شناسه مدیر و کانال را وارد کنید")
        data["auto_publish"] = auto_publish
        if auto_publish and (not before_auto or before_channel != channel):
            data["posted"] = profile_ids[:1024]
    return update_config(change)


def ready(config):
    return bool(config["token"] and config["admin_id"] and config["channel_id"])
