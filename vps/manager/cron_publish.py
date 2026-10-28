"""30-minute per-inbound Telegram channel publisher.

Systemd invokes this once at :00 and :30. This is independent of the
long-polling bot and reuses the existing channel publisher and Secret.
"""
import logging
import time

from app import load_state
from manager.bot_config import get_config, ready, update_config
from manager.telegram_bot import TelegramError, publish

LOG = logging.getLogger("blue-mtproxy-cron")
INTERVAL_SECONDS = 30 * 60


def publish_due(now=None):
    """One post per selected enabled inbound per 30-minute clock slot.

    A slot number is persisted only after Telegram confirms the send.
    Failed sends are retried by the next timer tick; configuration changes
    and disabled/missing inbounds never trigger publication.
    """
    slot = int((time.time() if now is None else now) // INTERVAL_SECONDS)
    config = get_config()
    if not ready(config) or not config["scheduled_inbounds"]:
        return 0
    profiles = {p["id"]: p for p in load_state()["profiles"]}
    sent = 0
    errors = []
    for profile_id in config["scheduled_inbounds"]:
        # The dashboard can disable the schedule while the job runs.
        current = get_config()
        if not ready(current) or profile_id not in current["scheduled_inbounds"]:
            continue
        if current["schedule_sent_slots"].get(profile_id) == slot:
            continue
        profile = profiles.get(profile_id)
        if profile is None or not profile["enabled"]:
            continue
        destination = current["channel_id"]
        try:
            publish(profile_id)
            def mark(data):
                if (profile_id in data["scheduled_inbounds"] and
                        data["channel_id"] == destination):
                    data["schedule_sent_slots"][profile_id] = slot
            update_config(mark)
            sent += 1
        except (TelegramError, ValueError, OSError, RuntimeError) as exc:
            errors.append(profile_id)
            LOG.warning("Scheduled post for inbound %s failed: %s", profile_id, type(exc).__name__)
    if errors:
        raise RuntimeError("Scheduled publication failed for " + ", ".join(errors))
    return sent


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    count = publish_due()
    LOG.info("Scheduled Telegram posts: %s", count)


if __name__ == "__main__":
    main()
