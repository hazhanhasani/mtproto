#!/usr/bin/env python3
"""Read-only nftables verifier for the CDN-only input-port guard.

Read the kernel ruleset in nftables JSON format. Do not rely on nft's
human-readable port-range formatting, which can vary between versions.
Fail closed unless the exact inet input chain includes a matching DROP
rule for non-loopback TCP ports 10443 through 10474.
"""
import json
import subprocess
import sys

NFT = "/usr/sbin/nft"
TABLE = "blue_proxy_cdn_only"
CHAIN = "drop_external_mtproxy"


def port_range_matches(value):
    """Accept nftables JSON range expression with exact inclusive bounds."""
    if isinstance(value, dict):
        if "range" in value:
            bounds = value["range"]
            if isinstance(bounds, list):
                return bounds == [10443, 10474]
            if isinstance(bounds, dict):
                return bounds.get("from") == 10443 and bounds.get("to") == 10474
        # Some nftables releases wrap an interval in an anonymous set.
        if isinstance(value.get("set"), list) and len(value["set"]) == 1:
            return port_range_matches(value["set"][0])
    return False


def is_guarded_ruleset(document):
    records = document.get("nftables", [])
    if not isinstance(records, list):
        return False
    chains = [
        e["chain"] for e in records
        if isinstance(e, dict) and isinstance(e.get("chain"), dict)
        and e["chain"].get("family") == "inet"
        and e["chain"].get("table") == TABLE
        and e["chain"].get("name") == CHAIN
    ]
    if not any(c.get("hook") == "input" and c.get("type") == "filter" for c in chains):
        return False
    for record in records:
        rule = record.get("rule") if isinstance(record, dict) else None
        if not isinstance(rule, dict):
            continue
        if (rule.get("family"), rule.get("table"), rule.get("chain")) != (
            "inet", TABLE, CHAIN
        ):
            continue
        exprs = rule.get("expr", [])
        if not isinstance(exprs, list):
            continue
        has_drop = any(isinstance(e, dict) and "drop" in e for e in exprs)
        has_not_loopback = any(
            isinstance(e, dict)
            and isinstance(e.get("match"), dict)
            and e["match"].get("op") == "!="
            and e["match"].get("left") == {"meta": {"key": "iifname"}}
            and e["match"].get("right") == "lo"
            for e in exprs
        )
        has_tcp_range = any(
            isinstance(e, dict)
            and isinstance(e.get("match"), dict)
            and e["match"].get("op") == "=="
            and e["match"].get("left") == {
                "payload": {"protocol": "tcp", "field": "dport"}
            }
            and port_range_matches(e["match"].get("right"))
            for e in exprs
        )
        if has_drop and has_not_loopback and has_tcp_range:
            return True
    return False


def main():
    try:
        result = subprocess.run(
            [NFT, "-j", "list", "table", "inet", TABLE],
            check=False, capture_output=True, text=True, timeout=8,
        )
        if result.returncode != 0:
            print("[error] CDN-only nftables table is missing or unreadable.")
            return 1
        data = json.loads(result.stdout)
        if is_guarded_ruleset(data):
            print("[ok] Verified kernel nftables input guard: external TCP 10443-10474 DROP, loopback exempt.")
            return 0
        print("[error] CDN-only table exists but required input DROP rule is missing or inconsistent.")
        return 1
    except (OSError, ValueError, subprocess.TimeoutExpired):
        print("[error] nftables rule inspection failed; no CDN-only success claim can be made.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
