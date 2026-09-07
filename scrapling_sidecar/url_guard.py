"""Reject private, loopback, and internal research targets (SSRF guard)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTS = {
    "localhost",
    "ai-trading-backend",
    "ai-trading-mcp",
    "ai-trading-scrapling",
    "ai-trading-litellm",
    "ai-trading-kronos",
    "ai-trading-nginx",
    "ai-trading-grafana",
    "vps-influxdb",
    "vps-qdrant",
    "n8n",
    "a0-instance",
    "hermes-webui",
}
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def is_public_http_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host or host in BLOCKED_HOSTS:
        return False
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".localhost"):
        return False
    if parsed.username or parsed.password:
        return False
    try:
        raw_ip = ipaddress.ip_address(host)
    except ValueError:
        raw_ip = None
    if raw_ip is not None:
        return _public_ip(raw_ip)

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            resolved = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            return False
        if not _public_ip(resolved):
            return False
    return True


def _public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if str(ip) in METADATA_IPS:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
