"""Launch an upstream official MTProxy backend. No shell or user-controlled argv."""
import ipaddress
import json
import os
import re
import sys

ID = re.compile(r"^[a-z][a-z0-9-]{0,23}$")
HEX = re.compile(r"^[0-9a-f]{32}$")


def build_argv(profile, nat_args=""):
    if not HEX.fullmatch(profile["secret"]):
        raise ValueError("invalid secret")
    tag = profile.get("ad_tag", "")
    if tag and not HEX.fullmatch(tag):
        raise ValueError("invalid ad tag")
    slot = profile["slot"]
    if type(slot) is not int or not 0 <= slot < 32:
        raise ValueError("invalid slot")
    cmd = [
        "/opt/MTProxy/objs/bin/mtproto-proxy",
        "-u", "mtproxy", "-p", str(8888 + slot),
        "-H", str(10443 + slot),
        "-S", profile["secret"],
        "--aes-pwd", "/etc/mtproxy/proxy-secret",
        "/etc/mtproxy/proxy-multi.conf",
        "-M", "1", "-C", "4096",
    ]
    # The official backend accepts repeated -S flags. Each user can have a
    # distinct credential and be revoked without changing the inbound owner
    # secret. All users still share the same process/Ad Tag and aggregate stats.
    seen = {profile["secret"]}
    clients = profile.get("clients", [])
    if not isinstance(clients, list) or len(clients) > 31:
        raise ValueError("invalid client list")
    for c in clients:
        token = c.get("secret", "")
        if not HEX.fullmatch(token) or token in seen:
            raise ValueError("invalid or duplicate user secret")
        seen.add(token)
        if type(c.get("enabled")) is not bool:
            raise ValueError("invalid user status")
        if c["enabled"]:
            cmd.extend(["-S", token])
    if tag:
        cmd.extend(["-P", tag])
    if nat_args:
        match = re.fullmatch(r"--nat-info ([0-9.]+):([0-9.]+)", nat_args)
        if not match:
            raise ValueError("unsupported NAT parameters")
        for value in match.groups():
            ipaddress.IPv4Address(value)
        cmd.extend(["--nat-info", match.group(1) + ":" + match.group(2)])
    return cmd


def main():
    if len(sys.argv) != 2 or not ID.fullmatch(sys.argv[1]):
        raise SystemExit("invalid profile id")
    path = "/etc/blue-mtproxy/runtime/" + sys.argv[1] + ".json"
    with open(path, encoding="utf-8") as handle:
        profile = json.load(handle)
    if profile["id"] != sys.argv[1]:
        raise SystemExit("profile mismatch")
    argv = build_argv(profile, os.environ.get("MTPROXY_NAT_ARGS", ""))
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
