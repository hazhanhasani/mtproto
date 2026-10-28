"""Read-only, bounded inbound telemetry from official MTProxy localhost /stats.

Never interpret buffer allocations or arbitrary counters as billed traffic.
The official backend exposes total_special_connections (current inbound
connections), but *not* per-user bytes. Missing data returns None, not zero.
"""
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

MAX_STATS_BYTES = 32_768


def parse_stats(raw):
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_STATS_BYTES:
        return {}
    result = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        name, val = parts[0].strip(), parts[1].strip()
        if name not in {"total_special_connections", "total_ready_targets",
                        "total_allocated_inbound_connections"}:
            continue
        if val.isascii() and val.isdecimal():
            parsed = int(val)
            if parsed <= 10_000_000:
                result[name] = parsed
    return result


def inbound_connections(slot, timeout=1.5):
    if type(slot) is not int or not 0 <= slot < 32:
        return None
    # Official -p 8888+slot is a loopback-only stats listener.
    url = "http://127.0.0.1:" + str(8888 + slot) + "/stats"
    try:
        with urlopen(Request(url, headers={"Accept": "text/plain"}), timeout=timeout) as response:
            if response.status != 200:
                return None
            sample = parse_stats(response.read(MAX_STATS_BYTES + 1))
        return sample.get("total_special_connections")
    except (OSError, URLError, HTTPError, ValueError):
        return None


def relay_traffic(timeout=2):
    """Return exact process-lifetime WEB relay byte counters, or None.

    The upstream server's loopback Prometheus endpoint exposes only GLOBAL
    tproxy_bytes_up_total/down_total, not inbound/user-specific counters.
    """
    try:
        with urlopen("http://127.0.0.1:8081/metrics", timeout=timeout) as response:
            if response.status != 200:
                return None
            raw = response.read(MAX_STATS_BYTES + 1)
        if len(raw) > MAX_STATS_BYTES:
            return None
        values = {}
        for line in raw.decode("utf-8", errors="replace").splitlines():
            fields = line.strip().split()
            if len(fields) != 2 or fields[0] not in (
                "tproxy_bytes_up_total", "tproxy_bytes_down_total"
            ):
                continue
            if fields[1].isascii() and fields[1].isdecimal():
                value = int(fields[1])
                if value <= 1_000_000_000_000_000:
                    values[fields[0]] = value
        if set(values) != {"tproxy_bytes_up_total", "tproxy_bytes_down_total"}:
            return None
        return {
            "up": values["tproxy_bytes_up_total"],
            "down": values["tproxy_bytes_down_total"],
            "total": sum(values.values()),
            "scope": "all_inbounds_since_relay_restart",
        }
    except (OSError, URLError, HTTPError, UnicodeError, ValueError):
        return None


def format_bytes(value):
    if type(value) is not int or value < 0:
        return "—"
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
