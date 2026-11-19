"""Helpers for handling untrusted text and URLs coming from the web/agent."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking parameters removed during URL normalization.
TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid", "yclid",
    "ref", "ref_src", "referrer", "trk", "trkinfo", "_hsenc", "_hsmi",
}
TRACKING_PREFIXES = ("utm_",)

_CONTROL_RE = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")
_WS_RE = re.compile(r"[ \t]+")


class UnsafeURLError(ValueError):
    pass


def clean_text(value: str, max_len: int, *, single_line: bool = False) -> str:
    """Normalize unicode, drop control/bidi characters and clamp length."""
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL_RE.sub("", value)
    if single_line:
        value = " ".join(value.split())
    else:
        value = "\n".join(_WS_RE.sub(" ", line).strip() for line in value.splitlines())
        value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if len(value) > max_len:
        value = value[: max_len - 1].rstrip() + "…"
    return value


def _is_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def validate_public_url(url: str, blocked_domains: Iterable[str] = ()) -> str:
    """Return the URL if it is a safe public http(s) URL, else raise UnsafeURLError."""
    url = url.strip()
    if len(url) > 2048:
        raise UnsafeURLError("URL too long")
    if _CONTROL_RE.search(url) or any(c.isspace() for c in url):
        raise UnsafeURLError("URL contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise UnsafeURLError(f"unsupported URL scheme: {parts.scheme!r}")
    if parts.username or parts.password:
        raise UnsafeURLError("URL must not contain credentials")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or ("." not in host and ":" not in host):
        raise UnsafeURLError("URL must have a public hostname")
    if _is_private_host(host):
        raise UnsafeURLError("URL points to a private or local address")
    for d in blocked_domains:
        if host == d or host.endswith("." + d):
            raise UnsafeURLError(f"domain is blocked: {d}")
    return url


def normalize_url(url: str) -> str:
    """Deterministic canonical form used for deduplication."""
    parts = urlsplit(url.strip())
    scheme = "https" if parts.scheme.lower() in {"http", "https"} else parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1:
        path = path.rstrip("/")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query.sort()
    return urlunsplit((scheme, netloc, path, urlencode(query), ""))


def domain_of(url: str) -> Optional[str]:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else (host or None)
