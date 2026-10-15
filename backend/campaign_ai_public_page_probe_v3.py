"""Bounded public product-page probe for Decision Intelligence V3.

Only the canonical product host already known from Salla may be contacted.
Redirects are followed manually and every hop is revalidated *before* a request,
preventing an ad-controlled URL or redirect from turning the diagnostic into an
SSRF primitive. The probe is read-only and never adds a product to a cart.

The response exposes bounded customer-visible HTML evidence (title, meta/OG
metadata and a compact visible-text excerpt). It does not retain the raw page.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx


MAX_BODY_BYTES = 1_000_000
MAX_REDIRECTS = 3
MAX_VISIBLE_TEXT_CHARS = 4500


def _text(value: Any, limit: int = 2000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _host(url: str | None) -> str | None:
    try:
        parsed = urlsplit(str(url or ""))
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return parsed.hostname.casefold().rstrip(".")


def _public_dns(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


async def _allowed(url: str, canonical_host: str) -> bool:
    return _host(url) == canonical_host and await asyncio.to_thread(_public_dns, canonical_host)


def _meta_content(source: str, *, name: str | None = None, prop: str | None = None) -> str | None:
    if not name and not prop:
        return None
    target_attr = "name" if name else "property"
    target_value = name or prop
    patterns = (
        rf'<meta[^>]+{target_attr}=["\']{re.escape(str(target_value))}["\'][^>]+content=["\']([^"\']*)["\'][^>]*>',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+{target_attr}=["\']{re.escape(str(target_value))}["\'][^>]*>',
    )
    for pattern in patterns:
        match = re.search(pattern, source, re.I | re.S)
        if match:
            return _text(html.unescape(match.group(1)), 1000) or None
    return None


def _tag_boundary(source: str, index: int) -> bool:
    return index >= len(source) or source[index].isspace() or source[index] in {">", "/"}


def _find_html_token(source: str, token: str, start: int) -> int:
    """Find an ASCII HTML tag token case-insensitively without normalizing indexes."""
    target = token.lower()
    index = source.find("<", max(0, start))
    while index >= 0:
        end = index + len(target)
        if source[index:end].lower() == target and _tag_boundary(source, end):
            return index
        index = source.find("<", index + 1)
    return -1


def _strip_raw_text_element(source: str, tag: str) -> str:
    """Remove script/style/noscript blocks with a bounded tolerant scanner.

    Raw-text elements are stripped before HTMLParser sees the page. A malformed
    or unclosed raw-text block is dropped through end-of-input rather than
    leaking script/style content into customer-visible evidence.
    """
    opening = f"<{tag}"
    closing = f"</{tag}"
    cursor = 0
    parts: list[str] = []
    while cursor < len(source):
        open_start = _find_html_token(source, opening, cursor)
        if open_start < 0:
            parts.append(source[cursor:])
            break
        parts.append(source[cursor:open_start])
        open_end = source.find(">", open_start + len(opening))
        if open_end < 0:
            break
        close_start = _find_html_token(source, closing, open_end + 1)
        if close_start < 0:
            break
        close_end = source.find(">", close_start + len(closing))
        if close_end < 0:
            break
        cursor = close_end + 1
    return "".join(parts)


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)


def _visible_text(source: str) -> str | None:
    clean = source
    for tag in ("script", "style", "noscript"):
        clean = _strip_raw_text_element(clean, tag)

    parser = _VisibleTextParser()
    try:
        parser.feed(clean)
        parser.close()
    except Exception:
        # Fail closed: malformed HTML must never make hidden script/style data
        # look like customer-visible evidence.
        return None
    rendered = _text(" ".join(parser.parts), MAX_VISIBLE_TEXT_CHARS)
    return rendered or None


async def probe_product_page(url: str | None, *, canonical_url: str | None) -> dict[str, Any]:
    requested = _text(url) or None
    canonical = _text(canonical_url) or None
    canonical_host = _host(canonical)
    if not requested or not canonical_host:
        return {
            "checked": False,
            "status": "PRODUCT_URL_UNKNOWN",
            "reason": "canonical_product_url_missing",
        }
    if not await _allowed(requested, canonical_host):
        return {
            "checked": False,
            "status": "PRODUCT_URL_WRONG_DESTINATION",
            "requested_url": requested,
            "canonical_url": canonical,
            "reason": "destination_not_on_trusted_public_product_host",
        }

    current = requested
    redirects: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(12.0),
            follow_redirects=False,
            headers={"User-Agent": "MezanProductHealth/1.0"},
        ) as client:
            for hop in range(MAX_REDIRECTS + 1):
                if not await _allowed(current, canonical_host):
                    return {
                        "checked": False,
                        "status": "PRODUCT_URL_WRONG_DESTINATION",
                        "requested_url": requested,
                        "final_url": current,
                        "redirects": redirects,
                        "reason": "redirect_target_failed_trusted_host_check",
                    }
                async with client.stream("GET", current) as response:
                    status_code = response.status_code
                    location = response.headers.get("location")
                    if status_code in {301, 302, 303, 307, 308} and location:
                        target = urljoin(current, location)
                        target_host = _host(target)
                        redirects.append({
                            "status": status_code,
                            "from": current,
                            "to": target,
                        })
                        if target_host != canonical_host:
                            return {
                                "checked": True,
                                "status": "PRODUCT_URL_WRONG_DESTINATION",
                                "requested_url": requested,
                                "final_url": target,
                                "http_status": status_code,
                                "redirects": redirects,
                                "reason": "redirect_left_canonical_product_host",
                            }
                        if hop >= MAX_REDIRECTS:
                            return {
                                "checked": True,
                                "status": "PRODUCT_PAGE_UNAVAILABLE",
                                "requested_url": requested,
                                "final_url": target,
                                "redirects": redirects,
                                "reason": "redirect_limit_exceeded",
                            }
                        current = target
                        continue

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) >= MAX_BODY_BYTES:
                            break
                        remaining = MAX_BODY_BYTES - len(body)
                        body.extend(chunk[:remaining])
                    encoding = response.encoding or "utf-8"
                    source = bytes(body).decode(encoding, errors="replace")
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", source, re.I | re.S)
                    lowered = source.casefold()
                    add_to_cart_present = any(marker in lowered for marker in (
                        "add-to-cart",
                        "add_to_cart",
                        "addtocart",
                        "أضف للسلة",
                        "أضف إلى السلة",
                        "اضف للسلة",
                    ))
                    unavailable_markers = any(marker in lowered for marker in (
                        "out of stock",
                        "sold out",
                        "غير متوفر",
                        "نفدت الكمية",
                        "غير متاح",
                    ))
                    if status_code == 404:
                        status = "PRODUCT_URL_BROKEN"
                    elif status_code >= 400:
                        status = "PRODUCT_PAGE_UNAVAILABLE"
                    elif redirects:
                        status = "PRODUCT_URL_REDIRECTED"
                    else:
                        status = "PRODUCT_URL_OK"
                    return {
                        "checked": True,
                        "status": status,
                        "requested_url": requested,
                        "final_url": current,
                        "http_status": status_code,
                        "redirected": bool(redirects),
                        "redirects": redirects,
                        "page_title": (
                            _text(html.unescape(title_match.group(1)), 300)
                            if title_match
                            else None
                        ),
                        "meta_description": _meta_content(source, name="description"),
                        "og_title": _meta_content(source, prop="og:title"),
                        "og_description": _meta_content(source, prop="og:description"),
                        "og_image": _meta_content(source, prop="og:image"),
                        "visible_text_excerpt": _visible_text(source),
                        "add_to_cart_marker_present": add_to_cart_present,
                        "unavailable_marker_present": unavailable_markers,
                        "body_truncated": len(body) >= MAX_BODY_BYTES,
                        "synthetic_check_scope": "read_only_page_fetch_no_cart_mutation",
                    }
        return {
            "checked": True,
            "status": "PRODUCT_PAGE_UNAVAILABLE",
            "requested_url": requested,
            "reason": "unexpected_probe_exit",
        }
    except Exception as exc:
        return {
            "checked": True,
            "status": "PRODUCT_PAGE_UNAVAILABLE",
            "requested_url": requested,
            "final_url": current,
            "redirects": redirects,
            "error_type": type(exc).__name__,
        }


__all__ = ["MAX_REDIRECTS", "probe_product_page"]
