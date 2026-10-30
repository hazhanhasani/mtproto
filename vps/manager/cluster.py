"""Persisted, locked multi-node desired state for the Blue Proxy controller.

Remote nodes initiate outbound TLS polling; the panel never holds SSH passwords,
runs arbitrary commands or connects to user-supplied node addresses.
"""
import copy
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from pathlib import Path

from .model import HOST, validate, new_secret

PATH = Path("/var/lib/blue-mtproxy/nodes.json")
LOCK = PATH.with_name("nodes.lock")
MAX_NODES = 32
MAX_DOCUMENT = 262144
MAX_PROFILES = 32


def _doc():
    try:
        with PATH.open("rb") as fp:
            data = fp.read(MAX_DOCUMENT + 1)
        if len(data) > MAX_DOCUMENT:
            raise ValueError("cluster state too large")
        result = json.loads(data)
        if result.get("version") != 1 or not isinstance(result.get("nodes"), list):
            raise ValueError("invalid cluster document")
        return result
    except FileNotFoundError:
        return {"version": 1, "nodes": []}


def read():
    return copy.deepcopy(_doc())


def mutate(fn):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        data = _doc()
        result = fn(data)
        if len(data["nodes"]) > MAX_NODES:
            raise ValueError("حداکثر ۳۲ نود مجاز است")
        rendered = (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if len(rendered) > MAX_DOCUMENT:
            raise ValueError("حجم تنظیمات نودها بیش از حد مجاز است")
        fd, name = tempfile.mkstemp(prefix=".nodes-", dir=PATH.parent)
        try:
            with os.fdopen(fd, "wb") as dest:
                dest.write(rendered)
                dest.flush()
                os.fsync(dest.fileno())
            os.chmod(name, 0o600)
            os.replace(name, PATH)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return result


def get_node(document, node_id):
    for node in document["nodes"]:
        if node["id"] == node_id:
            return node
    raise ValueError("نود پیدا نشد")


def check_hostname(host):
    if not isinstance(host, str) or not HOST.fullmatch(host):
        raise ValueError("نام دامنه معتبر نیست")
    return host


def check_label(label):
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 80:
        raise ValueError("عنوان باید بین ۱ تا ۸۰ نویسه باشد")
    return label.strip()


def node_domains(node):
    return [node["hostname"], *node.get("domains", [])]


def check_domain_available(doc, hostname, current_id=None):
    if hostname == "localhost":
        raise ValueError("دامنه عمومی لازم است")
    for node in doc["nodes"]:
        if node["id"] != current_id and hostname in node_domains(node):
            raise ValueError("این دامنه قبلاً به یک نود دیگر اختصاص یافته است")


def add_node(doc, name, location, hostname):
    name = check_label(name)
    location = check_label(location)
    hostname = check_hostname(hostname)
    check_domain_available(doc, hostname)
    token = secrets.token_urlsafe(36)
    node = {
        "id": "n" + secrets.token_hex(6),
        "name": name,
        "location": location,
        "hostname": hostname,
        "domains": [],
        "token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "profiles": [],
        "revision": 1,
        "applied_revision": 0,
        "last_seen": 0,
        "status": "pending",
        "last_error": "",
    }
    doc["nodes"].append(node)
    return node["id"], token


def validate_node_profiles(node):
    profiles = node["profiles"]
    if len(profiles) > MAX_PROFILES:
        raise ValueError("حداکثر ۳۲ اینباند برای هر نود مجاز است")
    domain_set = set(node_domains(node))
    for profile in profiles:
        if profile.get("hostname", node["hostname"]) not in domain_set:
            raise ValueError("دامنه اینباند به این نود متصل نیست")
    if profiles:
        # The stock WEB relay has a per-node, not cluster-wide, 32-secret cap.
        # A synthetic panel hostname is only needed by legacy model validation.
        validate({
            "version": 1,
            "hostname": node["hostname"],
            "panel_hostname": "panel." + node["hostname"],
            "auto_update": True,
            "profiles": profiles,
        })


def bump(node):
    validate_node_profiles(node)
    node["revision"] += 1


def new_profile(node, label, hostname):
    check_label(label)
    check_hostname(hostname)
    if hostname not in node_domains(node):
        raise ValueError("دامنه انتخاب‌شده به نود تعلق ندارد")
    slots = {p["slot"] for p in node["profiles"]}
    slot = next((v for v in range(32) if v not in slots), None)
    if slot is None:
        raise ValueError("ظرفیت نود تکمیل شده است")
    profile = {
        "id": "p" + secrets.token_hex(6),
        "slot": slot,
        "label": label.strip(),
        "hostname": hostname,
        "secret": new_secret(),
        "ad_tag": "",
        "channel": "",
        "enabled": True,
        "carrier": "websocket",
        "clients": [],
    }
    node["profiles"].append(profile)
    bump(node)
    return profile["id"]


def get_profile(node, profile_id):
    for profile in node["profiles"]:
        if profile["id"] == profile_id:
            return profile
    raise ValueError("اینباند پیدا نشد")


def authorize(node_id, token):
    if not isinstance(token, str) or not 32 <= len(token) <= 256:
        return False
    try:
        node = get_node(read(), node_id)
    except ValueError:
        return False
    return hmac.compare_digest(node["token_hash"], hashlib.sha256(token.encode()).hexdigest())


def desired(node):
    return {
        "version": 1,
        "node_id": node["id"],
        "revision": node["revision"],
        "hostname": node["hostname"],
        "domains": node_domains(node),
        "profiles": copy.deepcopy(node["profiles"]),
    }


def record_report(doc, node_id, revision, status, error=""):
    node = get_node(doc, node_id)
    if type(revision) is not int or revision < 0 or revision > node["revision"]:
        raise ValueError("invalid revision")
    if status not in ("ok", "error", "pending"):
        raise ValueError("invalid status")
    if type(error) is not str or len(error) > 180:
        raise ValueError("invalid error")
    node["last_seen"] = int(time.time())
    node["status"] = status
    node["last_error"] = error if status == "error" else ""
    if status == "ok":
        node["applied_revision"] = max(node["applied_revision"], revision)


def node_status(node, now=None):
    now = time.time() if now is None else now
    if now - node.get("last_seen", 0) > 180:
        return "offline"
    if node.get("status") == "error":
        return "error"
    if node.get("applied_revision", 0) < node.get("revision", 0):
        return "syncing"
    return "online" if node.get("status") == "ok" else "pending"
