"""Manage ten persistent, independently revocable channel credentials per inbound.

This mutates the normal version-1 state through app.apply_mutation, so the
existing capacity checks, secret reconciliation and rollback still apply.
"""
import secrets

from manager.model import MAX_CLIENTS_PER_INBOUND, MAX_STORED_CLIENTS, MAX_WEB_SECRETS, new_secret

POOL_SIZE = 10


def pool_clients(profile):
    return [c for c in profile.get("clients", []) if c.get("channel_pool") is True]


def provision_pool(state, profile_id):
    """Complete a channel pool atomically, never touching existing users.

    Call inside the locked app.apply_mutation callback. Existing pool users
    are reused across server restarts, updates and retries. Revoked clients
    are not re-enabled or overwritten.
    """
    profile = next((p for p in state["profiles"] if p["id"] == profile_id), None)
    if profile is None:
        raise ValueError("اینباند موردنظر وجود ندارد")
    if not profile["enabled"]:
        raise ValueError("اینباند غیرفعال است")
    clients = profile.setdefault("clients", [])
    existing = pool_clients(profile)
    missing = POOL_SIZE - len(existing)
    if missing < 0:
        raise ValueError("تعداد کاربران کانال نامعتبر است")
    if missing == 0:
        return 0

    if len(clients) + missing > MAX_CLIENTS_PER_INBOUND:
        raise ValueError("ظرفیت کاربران این اینباند برای ۱۰ کاربر کانال کافی نیست")
    total_stored = sum(len(p.get("clients", [])) for p in state["profiles"])
    if total_stored + missing > MAX_STORED_CLIENTS:
        raise ValueError("ظرفیت کل کاربران ذخیره‌شده کافی نیست")
    active = sum(
        1 + sum(bool(c["enabled"]) for c in p.get("clients", []))
        for p in state["profiles"] if p["enabled"]
    )
    if active + missing > MAX_WEB_SECRETS:
        raise ValueError("ظرفیت WEB Relay برای ۱۰ Secret مستقل کافی نیست (سقف ۳۲)")

    used_ids = {c["id"] for c in clients}
    used_secrets = {
        secret
        for p in state["profiles"]
        for secret in [p["secret"]] + [c["secret"] for c in p.get("clients", [])]
    }
    taken_labels = {c["label"] for c in clients}
    for number in range(1, POOL_SIZE + 1):
        if missing == 0:
            break
        label = "Channel Proxy %02d" % number
        if label in taken_labels:
            continue
        client_id = "u" + secrets.token_hex(4)
        while client_id in used_ids:
            client_id = "u" + secrets.token_hex(4)
        client_secret = new_secret()
        while client_secret in used_secrets:
            client_secret = new_secret()
        clients.append({
            "id": client_id,
            "label": label,
            "secret": client_secret,
            "enabled": True,
            "channel_pool": True,
        })
        used_ids.add(client_id)
        used_secrets.add(client_secret)
        taken_labels.add(label)
        missing -= 1

    # Manual user renames may take numbered labels. Still provision all ten.
    while missing:
        client_id = "u" + secrets.token_hex(4)
        while client_id in used_ids:
            client_id = "u" + secrets.token_hex(4)
        client_secret = new_secret()
        while client_secret in used_secrets:
            client_secret = new_secret()
        label = "Channel " + secrets.token_hex(3)
        while label in taken_labels:
            label = "Channel " + secrets.token_hex(3)
        clients.append({
            "id": client_id, "label": label, "secret": client_secret,
            "enabled": True, "channel_pool": True,
        })
        used_ids.add(client_id)
        used_secrets.add(client_secret)
        taken_labels.add(label)
        missing -= 1
    return POOL_SIZE - len(existing)
