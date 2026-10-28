"""Preflight gates for a fresh one-command VPS installation."""
import argparse
import ipaddress
import socket
import sys
from urllib.request import Request, urlopen

PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ifconfig.co/ip",
)


def valid_host(host):
    if not isinstance(host, str) or len(host) > 253 or len(host) < 4 or host.lower() != host:
        return False
    labels = host.rstrip(".").split(".")
    if len(labels) < 2 or host.endswith("."):
        return False
    return all(
        1 <= len(label) <= 63
        and label[0].isalnum() and label[-1].isalnum()
        and label.isascii()
        and all(char.islower() or char.isdigit() or char == "-" for char in label)
        for label in labels
    ) and labels[-1].isalpha()


def get_ipv4(host):
    return {
        info[4][0]
        for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    }


def get_public_ipv4():
    for url in PUBLIC_IP_ENDPOINTS:
        try:
            request = Request(url, headers={"User-Agent": "BlueProxy-Install/1.0"})
            with urlopen(request, timeout=7) as response:
                candidate = response.read(80).decode("ascii").strip()
            ip = ipaddress.IPv4Address(candidate)
            if ip.is_global:
                return str(ip)
        except (OSError, ValueError, UnicodeError):
            continue
    raise RuntimeError("Could not determine public IPv4; check outbound HTTPS or use --skip-dns-check if the server is behind NAT.")


def check_dns(domain, panel):
    public_ip = get_public_ipv4()
    for name in (domain, panel):
        try:
            addresses = get_ipv4(name)
        except socket.gaierror as error:
            raise RuntimeError(f"DNS missing for {name}: {error}") from error
        if addresses != {public_ip}:
            raise RuntimeError(
                f"DNS mismatch for {name}: {sorted(addresses)!r}; VPS public IPv4 is {public_ip}. "
                "Point DNS-only A records directly to the VPS, then retry. "
                "Do not enable Cloudflare orange-cloud for these hostnames."
            )
        print(f"[ok] DNS {name} -> {public_ip}")


def check_ports():
    for family, address in (
        (socket.AF_INET, "0.0.0.0"),
        (socket.AF_INET6, "::"),
    ):
        for port in (80, 443):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    if family == socket.AF_INET6:
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                    sock.bind((address, port))
            except OSError as error:
                # No IPv6 support is not an error for an IPv4-only VPS.
                if family == socket.AF_INET6 and error.errno in (97, 99):
                    continue
                raise RuntimeError(
                    f"TCP {port} is unavailable on {address}. "
                    "This installer needs a fresh VPS with no existing webserver."
                ) from error
    print("[ok] TCP 80 and 443 available on this host")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True)
    parser.add_argument("--panel-domain", required=True)
    parser.add_argument("--skip-dns-check", action="store_true")
    args = parser.parse_args()
    if not valid_host(args.domain) or not valid_host(args.panel_domain) or args.domain == args.panel_domain:
        parser.error("Use distinct lowercase ASCII DNS hostnames.")
    check_ports()
    if not args.skip_dns_check:
        check_dns(args.domain, args.panel_domain)
    else:
        print("[warning] DNS verification explicitly skipped; SSL may fail.")
    print("[ok] Initial host preflight complete; provider firewall and CA validation remain to be verified.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("Preflight failed: " + str(error), file=sys.stderr)
        raise SystemExit(1)
