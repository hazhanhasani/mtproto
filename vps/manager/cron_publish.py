"""Publish one random channel-user Secret per scheduled inbound every 30 min.

The pool is persisted as normal inbound users in state.json; no new
users or secrets are created when the pool is already complete.
"""
import logging
import secrets
import time

from app import apply_mutation, get_profile, load_state
from manager.bot_config import get_config, ready, update_config
from manager.channel_pool import POOL_SIZE, pool_clients, provision_pool
from manager.telegram_bot import TelegramError, publish

LOG = logging.getLogger("blue-mtproxy-cron")
INTERVAL_SECONDS = 30 * 60


def ensure_channel_users(profile_id):
    """Create missing pool members through the validated privileged broker.

    Existing, including disabled, users are preserved; deleted pool users
    are replaced on the next scheduled run if capacity permits.
    """
    profile = get_profile(load_state(), profile_id)
    if not profile["enabled"]:
        return profile
    if len(pool_clients(profile)) < POOL_SIZE:
        apply_mutation(lambda state: provision_pool(state, profile_id))
        profile = get_profile(load_state(), profile_id)
    return profile


def pick_random_user(profile, last_id=None):
    """Cryptographically random selection, excluding the previous user."""
    users = [c for c in pool_clients(profile) if c["enabled"]]
    if not users:
        raise ValueError("هیچ کاربر فعال اختصاصی کانال وجود ندارد")
    alternative = [c for c in users if c["id"] != last_id]
    return secrets.choice(alternative or users)


def publish_due(now=None):
    """Post once per configured inbound per slot, never the owner Secret."""
    slot = int((time.time() if now is None else now) // INTERVAL_SECONDS)
    config = get_config()
    if not ready(config) or not config["scheduled_inbounds"]:
        return 0
    sent = 0
    failed = []
    for profile_id in config["scheduled_inbounds"]:
        current = get_config()
        if not ready(current) or profile_id not in current["scheduled_inbounds"]:
            continue
        if current["schedule_sent_slots"].get(profile_id) == slot:
            continue
        try:
            profile = get_profile(load_state(), profile_id)
            if not profile["enabled"]:
                continue
            profile = ensure_channel_users(profile_id)
            user = pick_random_user(
                profile, current["schedule_last_clients"].get(profile_id)
            )
            destination = current["channel_id"]
            publish(profile_id, client_id=user["id"])

            def mark(data):
                if (profile_id in data["scheduled_inbounds"] and
                        data["channel_id"] == destination):
                    data["schedule_sent_slots"][profile_id] = slot
                    data["schedule_last_clients"][profile_id] = user["id"]
            update_config(mark)
            sent += 1
        except (TelegramError, ValueError, OSError, RuntimeError) as exc:
            failed.append(profile_id)
            LOG.warning("Scheduled inbound %s failed: %s", profile_id, type(exc).__name__)
    if failed:
        raise RuntimeError("Scheduled channel publication failed for " + ", ".join(failed))
    return sent


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    count = publish_due()
    LOG.info("Scheduled Telegram posts: %s", count)


if __name__ == "__main__":
    main()
