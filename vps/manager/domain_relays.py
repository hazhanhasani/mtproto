"""Independent WEB relay per extra hostname.

The upstream Telegram WEB server derives a bridge capability from exactly
one public_hostname at startup. Multiple Caddy names cannot share a single
WEB relay process. Each alias gets an isolated loopback process/config, with
the same root-only credential source and backend definitions as the primary.
"""
import grp
import json
import os
from pathlib import Path
import re
import tempfile

if __package__:
    from .model import HOST
else:
    from model import HOST

BASE_CONFIG = Path("/etc/tproxy-server/config.json")
CONFIG_DIR = Path("/etc/tproxy-server/domains")
PROFILES = Path("/etc/tproxy-server/profiles.json")
RELAY_PREFIX = "blue-mtproxy-relay@"
MAX_ALIASES = 15


def ports(domains, original_hostname):
    aliases = sorted(set(domains) - {original_hostname})
    if len(aliases) > MAX_ALIASES or any(not HOST.fullmatch(h) for h in aliases):
        raise ValueError("invalid relay aliases")
    # These ports are loopback-only, outside MTProxy direct/metrics ports.
    return {host: (9120 + i, 9220 + i) for i, host in enumerate(aliases)}


def service_name(host):
    if not HOST.fullmatch(host):
        raise ValueError("invalid alias hostname")
    return RELAY_PREFIX + host + ".service"


def config_for(host, listen, admin_listen, original):
    config = dict(original)
    config["public_hostname"] = host
    config["listen"] = "127.0.0.1:" + str(listen)
    config["admin_listen"] = "127.0.0.1:" + str(admin_listen)
    config["profiles_file"] = "/run/credentials/" + service_name(host) + "/profiles.json"
    return (json.dumps(config, sort_keys=True, indent=2) + "\n").encode()


def snapshot():
    if not CONFIG_DIR.exists():
        return {}
    result = {}
    for path in CONFIG_DIR.glob("*.json"):
        if not HOST.fullmatch(path.stem) or path.is_symlink() or not path.is_file():
            raise ValueError("unsafe relay config file")
        result[path.stem] = path.read_bytes()
    return result


def atomic(path, data):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".rel-")
    try:
        with os.fdopen(fd, "wb") as fp:
            fp.write(data)
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(temp, 0o640)
        os.chown(temp, 0, grp.getgrnam("tproxy").gr_gid)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def configure(desired, original_hostname, run):
    new_ports = ports(desired["domains"], original_hostname)
    old = snapshot()
    CONFIG_DIR.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(CONFIG_DIR, 0, grp.getgrnam("tproxy").gr_gid)
    os.chmod(CONFIG_DIR, 0o750)
    original = json.loads(BASE_CONFIG.read_text())
    # Validate every candidate before changing the running alias services.
    for host, (web_port, admin_port) in new_ports.items():
        path = CONFIG_DIR / (host + ".json")
        atomic(path, config_for(host, web_port, admin_port, original))
        run(["/usr/local/bin/tproxy-server", "-config", str(path),
             "-profiles-file", str(PROFILES), "-check"])
    for host in old.keys() - new_ports.keys():
        run(["/usr/bin/systemctl", "disable", "--now", service_name(host)])
        (CONFIG_DIR / (host + ".json")).unlink(missing_ok=True)
    for host in new_ports:
        unit = service_name(host)
        run(["/usr/bin/systemctl", "enable", unit])
        run(["/usr/bin/systemctl", "restart", unit])
        run(["/usr/bin/systemctl", "is-active", "--quiet", unit])
    return old


def restore(old, run):
    active = snapshot()
    for host in active.keys() - old.keys():
        try:
            run(["/usr/bin/systemctl", "disable", "--now", service_name(host)])
        except Exception:
            pass
        (CONFIG_DIR / (host + ".json")).unlink(missing_ok=True)
    for host, content in old.items():
        atomic(CONFIG_DIR / (host + ".json"), content)
        try:
            unit = service_name(host)
            run(["/usr/bin/systemctl", "enable", unit])
            run(["/usr/bin/systemctl", "restart", unit])
        except Exception:
            pass
