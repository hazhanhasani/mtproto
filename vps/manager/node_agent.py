#!/usr/bin/env python3
"""Outbound-only Blue Proxy node reconciler.

No inbound agent API, SSH connection or remote shell. The controller sends
strictly validated declarative state over authenticated HTTPS. A failed apply
restores the last known good state and Caddy domain snippet.
"""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

if __package__:
    from .model import HOST, validate
else:
    from model import HOST, validate

CONFIG = Path("/etc/blue-mtproxy/node.json")
STATE = Path("/var/lib/blue-mtproxy/state.json")
REVISION = Path("/var/lib/blue-mtproxy/node-revision")
CADDY_SNIPPET = Path("/etc/caddy/blue-mtproxy-domains.caddy")
MAX_REPLY = 131072
NODE_ID = re.compile(r"^n[0-9a-f]{12}$")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = build_opener(NoRedirect)


def atomic(path, body, mode=0o600, owner=None):
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".node-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        if owner is not None:
            os.chown(temp, owner, owner)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def request_controller(settings, method, route, body=None):
    headers = {"Authorization": "Bearer " + settings["token"], "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(settings["controller"].rstrip("/") + route, method=method,
                  headers=headers,
                  data=json.dumps(body, separators=(",", ":")).encode() if body is not None else None)
    with OPENER.open(req, timeout=18) as response:
        if response.status != 200:
            raise RuntimeError("controller unavailable")
        raw = response.read(MAX_REPLY + 1)
    if len(raw) > MAX_REPLY:
        raise ValueError("oversized controller response")
    return json.loads(raw)


def load_settings():
    config = json.loads(CONFIG.read_text())
    url = urlsplit(config["controller"])
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError("controller must be an HTTPS origin")
    if not NODE_ID.fullmatch(config.get("id", "")):
        raise ValueError("invalid node ID")
    if not isinstance(config.get("token"), str) or len(config["token"]) < 32:
        raise ValueError("invalid node credential")
    return config


def verify_desired(desired, settings, existing):
    if (type(desired) is not dict or desired.get("version") != 1
            or desired.get("node_id") != settings["id"]
            or type(desired.get("revision")) is not int or desired["revision"] < 1):
        raise ValueError("invalid node control document")
    hostname = desired.get("hostname")
    domains = desired.get("domains")
    profiles = desired.get("profiles")
    if not isinstance(hostname, str) or not HOST.fullmatch(hostname):
        raise ValueError("invalid primary hostname")
    if (not isinstance(domains, list) or not 1 <= len(domains) <= 16
            or domains[0] != hostname or len(set(domains)) != len(domains)
            or any(not isinstance(h, str) or not HOST.fullmatch(h) for h in domains)):
        raise ValueError("invalid node domains")
    if existing["panel_hostname"] in domains:
        raise ValueError("panel hostname cannot be a relay domain")
    if not isinstance(profiles, list) or len(profiles) > 32:
        raise ValueError("invalid node inbounds")
    if not profiles:
        return None  # unassigned nodes never overwrite their bootstrap state
    for profile in profiles:
        if not isinstance(profile, dict) or profile.get("hostname", hostname) not in domains:
            raise ValueError("inbound domain is not assigned")
    updated = {
        **existing, "hostname": hostname,
        "profiles": profiles,
    }
    validate(updated)
    return updated


def caddy_config(desired, original_hostname, panel_hostname):
    # Reverse proxy every path to the upstream relay. Do not bypass the
    # relay for public content or authenticate based on URL paths.
    hosts = [h for h in desired["domains"] if h != original_hostname]
    if panel_hostname in hosts:
        raise ValueError("panel hostname cannot be an alias")
    return ("# Managed by blue-mtproxy-node; do not edit.\n" +
            "".join(
                host + " {\n"
                "  encode zstd gzip\n"
                "  header -Via\n"
                "  reverse_proxy 127.0.0.1:8080 {\n"
                "    transport http {\n"
                "      response_header_timeout 40s\n"
                "    }\n"
                "  }\n"
                "}\n\n" for host in hosts
            )).encode()


def run_fixed(args, env=None):
    subprocess.run(args, check=True, timeout=110, env=env,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reconcile(desired, settings):
    previous = STATE.read_bytes()
    existing = json.loads(previous)
    updated = verify_desired(desired, settings, existing)
    if updated is None:
        return "pending"
    recorded = int(REVISION.read_text()) if REVISION.exists() else 0
    if recorded > desired["revision"]:
        raise ValueError("configuration rollback rejected")
    if recorded == desired["revision"]:
        # Cached revision is not health: self-heal inactive processes and
        # report failures instead of showing a misleading green node.
        services = ["tproxy-server.service", "caddy.service"]
        services += ["blue-mtproxy@" + p["id"] + ".service"
                     for p in updated["profiles"] if p["enabled"]]
        try:
            for service in services:
                run_fixed(["/usr/bin/systemctl", "is-active", "--quiet", service])
        except subprocess.CalledProcessError:
            run_fixed(["/usr/local/sbin/blue-mtproxy-apply"])
            for service in services:
                run_fixed(["/usr/bin/systemctl", "is-active", "--quiet", service])
        return "ok"
    original_hostname = settings["original_hostname"]
    expected = caddy_config(desired, original_hostname, existing["panel_hostname"])
    prior_caddy = CADDY_SNIPPET.read_bytes() if CADDY_SNIPPET.exists() else b""
    panel_uid = pwd.getpwnam("bluepanel").pw_uid
    try:
        atomic(CADDY_SNIPPET, expected, 0o644)
        # Original upstream Caddyfile can interpolate these environment
        # variables, even if an interactive shell did not source them.
        env = dict(os.environ, TPROXY_HOSTNAME=original_hostname,
                   TPROXY_SITE_ROOT="/srv/tproxy-site",
                   ACME_EMAIL="certificates@" + original_hostname)
        run_fixed(["/usr/local/bin/caddy", "validate", "--config",
                   "/etc/caddy/Caddyfile", "--adapter", "caddyfile"], env=env)
        atomic(STATE, (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode(),
               owner=panel_uid)
        run_fixed(["/usr/local/sbin/blue-mtproxy-apply"])
        run_fixed(["/usr/bin/systemctl", "reload", "caddy.service"])
        atomic(REVISION, str(desired["revision"]).encode() + b"\n")
        return "ok"
    except Exception:
        atomic(STATE, previous, owner=panel_uid)
        atomic(CADDY_SNIPPET, prior_caddy, 0o644)
        try:
            run_fixed(["/usr/local/sbin/blue-mtproxy-apply"])
            run_fixed(["/usr/bin/systemctl", "reload", "caddy.service"])
        except Exception:
            pass
        raise


def main():
    if os.geteuid() != 0:
        raise PermissionError("node agent must run as root")
    settings = load_settings()
    route = "/api/nodes/" + settings["id"]
    revision = int(REVISION.read_text()) if REVISION.exists() else 0
    status, error = "error", ""
    try:
        desired = request_controller(settings, "GET", route + "/desired")
        status = reconcile(desired, settings)
        if status == "ok":
            revision = desired["revision"]
    except Exception as exc:
        # Never report configs, tokens or raw responses to the panel.
        error = type(exc).__name__
        print("Node sync failed: " + error, file=sys.stderr)
    try:
        request_controller(settings, "POST", route + "/report", {
            "revision": revision, "status": status, "error": error,
        })
    except Exception:
        print("Node report failed", file=sys.stderr)
    return 0 if status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
