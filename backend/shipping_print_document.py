"""Fetch and freeze a provable carrier PDF. No provider writes or bearer forwarding."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import re
import secrets
import socket
from urllib.parse import quote, urlsplit

import httpx

COLLECTION = "shipping_print_documents"
PUBLIC_ORIGIN = "https://mezansalla.com"
MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 8
TTL_SECONDS = 300
FETCH_SECONDS = 8


class DocumentError(RuntimeError):
    def __init__(self, code, *, status_code=409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


async def _public_target(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or any(ord(c) < 33 for c in url)):
            raise ValueError()
        host = parsed.hostname.encode("idna").decode("ascii")
        addresses = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM), 3)
        ips = {str(row[4][0]) for row in addresses}
        if not ips or any(not ipaddress.ip_address(ip).is_global
                          or ipaddress.ip_address(ip).is_multicast
                          or ipaddress.ip_address(ip).is_reserved for ip in ips):
            raise ValueError()
        ip = sorted(ips)[0]
        authority = f"[{ip}]" if ":" in ip else ip
        # Pin the validated address; a second DNS lookup must not permit rebinding.
        target = f"https://{authority}{parsed.path or '/'}"
        if parsed.query:
            target += "?" + parsed.query
        return target, host
    except (ValueError, UnicodeError, OSError, TimeoutError) as exc:
        raise DocumentError("shipping_document_unsafe_url") from exc


async def _download(url):
    async def fetch():
        target, hostname = await _public_target(url)
        async with httpx.AsyncClient(timeout=httpx.Timeout(5, connect=3),
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", target,
                    headers={"Host": hostname, "Accept": "application/pdf", "Accept-Encoding": "identity"},
                    extensions={"sni_hostname": hostname}) as response:
                if response.status_code != 200:
                    raise DocumentError("shipping_document_http_rejected")
                if response.headers.get("content-encoding", "identity").lower() not in ("", "identity"):
                    raise DocumentError("shipping_document_encoding_rejected")
                if response.headers.get("content-type", "").split(";", 1)[0].lower().strip() not in (
                        "application/pdf", "application/octet-stream"):
                    raise DocumentError("shipping_document_not_pdf")
                length = response.headers.get("content-length")
                if length is not None:
                    try:
                        declared = int(length)
                    except ValueError as exc:
                        raise DocumentError("shipping_document_size_invalid") from exc
                    if not 0 < declared <= MAX_BYTES:
                        raise DocumentError("shipping_document_size_exceeded")
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise DocumentError("shipping_document_size_exceeded")
                    data.extend(chunk)
                return bytes(data)
    try:
        return await asyncio.wait_for(fetch(), FETCH_SECONDS)
    except DocumentError:
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        raise DocumentError("shipping_document_fetch_failed", status_code=502) from exc




async def verify_and_store(db, owner, number, snapshot):
    tracking = str(snapshot.get("tracking_number") or "").strip()
    shipment = str(snapshot.get("shipment_id") or "").strip()
    source_url = snapshot.get("label_url")
    if (not owner or not number or not tracking or len(tracking) > 200 or not shipment
            or not isinstance(source_url, str) or snapshot.get("ready") is not True):
        raise DocumentError("shipping_document_identity_missing")
    from shipping_pdf_sandbox import ParserError, document_slot, verify_pdf
    try:
        async with document_slot():
            data = await _download(source_url)
            await verify_pdf(data, tracking)
    except ParserError as exc:
        raise DocumentError(exc.code, status_code=503 if exc.code in {
            "shipping_document_parser_busy", "shipping_document_parser_failed",
            "shipping_document_parser_timeout", "shipping_document_isolation_unavailable"} else 409) from exc
    digest = hashlib.sha256(data).hexdigest()
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    row = {"_id": hashlib.sha256(token.encode()).hexdigest(), "user_id": str(owner),
           "order_number": str(number), "shipment_id": shipment, "tracking_number": tracking,
           "source_url": source_url, "courier_name": snapshot.get("courier_name"),
           "shipment_status": snapshot.get("status"), "status": "verified",
           "document_sha256": digest, "bytes": data, "created_at": now,
           "expires_at": now + timedelta(seconds=TTL_SECONDS)}
    await db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    await db[COLLECTION].insert_one(row)
    return {**snapshot, "label_url": f"{PUBLIC_ORIGIN}/api/fulfillment-v2/completed/"
            f"{quote(str(number), safe='')}/carrier-label/document/{token}", "document_sha256": digest}


async def load_document(db, number, token):
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise DocumentError("shipping_document_not_found", status_code=404)
    row = await db[COLLECTION].find_one({"_id": hashlib.sha256(token.encode()).hexdigest(),
                                        "order_number": str(number), "status": "verified"})
    if not row:
        raise DocumentError("shipping_document_not_found", status_code=404)
    expires = row.get("expires_at")
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # BSON UTC with default Motor codec.
    if not isinstance(expires, datetime) or expires <= datetime.now(timezone.utc):
        raise DocumentError("shipping_document_expired", status_code=410)
    data = row.get("bytes")
    if (not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES
            or hashlib.sha256(data).hexdigest() != row.get("document_sha256")):
        raise DocumentError("shipping_document_integrity_failed", status_code=409)
    return row
