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
