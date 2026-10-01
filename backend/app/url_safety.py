from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit


class UnsafeURL(ValueError):
    def __init__(self, code: str = "unsafe_url_destination") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ResolvedURL:
    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


def validate_url_syntax(url: str) -> SplitResult:
    try:
        parsed = urlsplit(url.strip())
        host_value = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("invalid_url") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not host_value or parsed.username or parsed.password:
        raise UnsafeURL("invalid_url")
    host = _normalize_host(host_value)
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise UnsafeURL()
    expected_port = 443 if parsed.scheme.lower() == "https" else 80
    if port is not None and port != expected_port:
        raise UnsafeURL()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not _is_public(address):
        raise UnsafeURL()
    return parsed


def resolve_public_url(url: str) -> ResolvedURL:
    parsed = validate_url_syntax(url)
    host = _normalize_host(parsed.hostname or "")
    port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return ResolvedURL(url, host, port, (str(literal),))
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise UnsafeURL("url_dns_failed") from exc
    addresses = tuple(dict.fromkeys(answer[4][0].split("%", 1)[0] for answer in answers))
    if not addresses:
        raise UnsafeURL("url_dns_failed")
    try:
        parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    except ValueError as exc:
        raise UnsafeURL("url_dns_failed") from exc
    if any(not _is_public(address) for address in parsed_addresses):
        raise UnsafeURL()
    return ResolvedURL(url, host, port, addresses)


def _normalize_host(value: str) -> str:
    host = value.rstrip(".").lower()
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UnsafeURL("invalid_url") from exc


def _is_public(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
