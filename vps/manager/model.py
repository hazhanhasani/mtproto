"""Pure configuration validation for the VPS WEB relay and MTProxy backends."""
import re
import secrets
from urllib.parse import urlencode

HEX = re.compile(r"^[0-9a-f]{32}$")
IDENT = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
HOST = re.compile(r"^(?=.{4,253}$)[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?)+$")
CHANNEL = re.compile(r"^(?:|@[A-Za-z0-9_]{5,32}|https://t\.me/[A-Za-z0-9_]{5,32})$")
CARRIERS = {"https", "https-lanes", "websocket", "websocket-lanes"}
# The upstream WEB relay has a hard default limit of 32 active profiles.
MAX_WEB_SECRETS = 32
MAX_CLIENTS_PER_INBOUND = 31


def new_secret():
    return secrets.token_hex(16)


def validate(state):
    if not isinstance(state, dict) or state.get("version") != 1:
        raise ValueError("invalid schema version")
    for field in ("hostname", "panel_hostname"):
        host = state.get(field)
        if not isinstance(host, str) or not HOST.fullmatch(host):
            raise ValueError("invalid " + field)
    if state["hostname"] == state["panel_hostname"]:
        raise ValueError("panel domain must differ from proxy domain")
    if not isinstance(state.get("auto_update"), bool):
        raise ValueError("invalid auto_update")
    profiles = state.get("profiles")
    if not isinstance(profiles, list) or not (1 <= len(profiles) <= 32):
        raise ValueError("profile count must be 1..32")
    seen_ids, seen_secrets, seen_slots = set(), set(), set()
    enabled = 0
    active_secrets = 0
    for p in profiles:
        if not isinstance(p, dict) or not IDENT.fullmatch(str(p.get("id", ""))):
            raise ValueError("invalid profile id")
        slot = p.get("slot")
        if type(slot) is not int or not (0 <= slot < 32) or slot in seen_slots:
            raise ValueError("invalid or duplicate slot")
        if p["id"] in seen_ids:
            raise ValueError("duplicate profile id")
        secret = p.get("secret")
        if not isinstance(secret, str) or not HEX.fullmatch(secret) or secret in seen_secrets:
            raise ValueError("invalid or duplicate secret")
        tag = p.get("ad_tag")
        if not isinstance(tag, str) or (tag and not HEX.fullmatch(tag)):
            raise ValueError("ad tag must be 32 lowercase hex characters")
        channel = p.get("channel")
        if not isinstance(channel, str) or not CHANNEL.fullmatch(channel):
            raise ValueError("invalid channel")
        label = p.get("label")
        if not isinstance(label, str) or not (1 <= len(label.strip()) <= 80):
            raise ValueError("invalid label")
        if type(p.get("enabled")) is not bool:
            raise ValueError("invalid enabled")
        if p.get("carrier", "websocket") not in CARRIERS:
            raise ValueError("invalid carrier")
        # Reserve this inbound's owner secret before validating client secrets.
        seen_secrets.add(secret)
        clients = p.get("clients", [])
        if not isinstance(clients, list) or len(clients) > MAX_CLIENTS_PER_INBOUND:
            raise ValueError("invalid inbound client count")
        local_ids = set()
        for c in clients:
            if not isinstance(c, dict) or not IDENT.fullmatch(str(c.get("id", ""))):
                raise ValueError("invalid client id")
            if c["id"] in local_ids:
                raise ValueError("duplicate client id")
            local_ids.add(c["id"])
            label = c.get("label")
            if not isinstance(label, str) or not (1 <= len(label.strip()) <= 80):
                raise ValueError("invalid client label")
            token = c.get("secret")
            if not isinstance(token, str) or not HEX.fullmatch(token) or token in seen_secrets:
                raise ValueError("duplicate or invalid client secret")
            if type(c.get("enabled")) is not bool:
                raise ValueError("invalid client enabled")
            seen_secrets.add(token)
            if p["enabled"] and c["enabled"]:
                active_secrets += 1
        enabled += bool(p["enabled"])
        active_secrets += bool(p["enabled"])  # backward-compatible inbound/owner secret
        seen_ids.add(p["id"])
        seen_slots.add(slot)
    if not enabled:
        raise ValueError("at least one profile must be enabled")
    if active_secrets > MAX_WEB_SECRETS:
        raise ValueError("WEB relay supports at most 32 active inbound + client secrets")
    return state


def direct_url(host, secret, port):
    return "https://t.me/proxy?" + urlencode({"server": host, "port": port, "secret": secret})


def web_url(host, secret):
    return "https://t.me/webproxy?" + urlencode({"server": host, "secret": secret})


def effective_client_secrets(profile):
    """Enabled user secrets accepted by the shared MTProxy backend."""
    return [c["secret"] for c in profile.get("clients", []) if c["enabled"]]


def relay_profiles(state):
    """One WEB bridge capability per enabled secret, on the same inbound port."""
    validate(state)
    profiles = []
    for p in state["profiles"]:
        if not p["enabled"]:
            continue
        backend = "127.0.0.1:" + str(10443 + p["slot"])
        transport = p.get("carrier", "websocket")
        profiles.append({
            "name": p["id"],
            "secret": p["secret"],
            "backend": backend,
            "carrier_mode": transport,
        })
        for c in p.get("clients", []):
            if c["enabled"]:
                profiles.append({
                    "name": p["id"] + "--" + c["id"],
                    "secret": c["secret"],
                    "backend": backend,
                    "carrier_mode": transport,
                })
    return {"profiles": profiles}
