"""Validation of bounded live category observations; no network or authority."""
import hashlib
import json
import re
from urllib.parse import urlsplit


def canonical_id(value):
    if type(value) not in (str, int) or not re.fullmatch(r"[1-9][0-9]{0,29}", str(value)):
        raise ValueError("invalid_numeric_identity")
    return str(value)


def safe_customer_url(value, store_domain):
    if not isinstance(value, str) or value != value.strip() or len(value) > 2000:
        raise ValueError("category_url_unproved")
    store = urlsplit(store_domain if "://" in store_domain else "https://" + store_domain)
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.hostname != store.hostname
            or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None, 443)
            or "\\" in value or any(ord(c) < 32 for c in value)):
        raise ValueError("category_url_unproved")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def checked_page(response, requested_page):
    if not isinstance(response, dict) or response.get("success") is not True or response.get("status") != 200:
        raise ValueError("provider_response_unproved")
    encoded = json.dumps(response, ensure_ascii=False).encode()
    if len(encoded) > 512 * 1024:
        raise ValueError("provider_response_size_limit")
    rows, pagination = response.get("data"), response.get("pagination")
    if not isinstance(rows, list) or not isinstance(pagination, dict) or any(not isinstance(r, dict) for r in rows):
        raise ValueError("pagination_unproved")
    def number(*names):
        values = [pagination[n] for n in names if n in pagination]
        if not values or any(type(n) is not int or n < 0 for n in values) or len(set(values)) != 1:
            raise ValueError("pagination_unproved")
        return values[0]
    current = number("currentPage", "current_page")
    size = number("perPage", "per_page")
    pages = number("totalPages", "total_pages", "last_page")
    total, count = number("total"), number("count")
    if (current != requested_page or not 1 <= size <= 100 or pages != max(1, (total + size - 1) // size)
            or current > pages or count != len(rows) or count != min(size, max(0, total - (current - 1) * size))):
        raise ValueError("pagination_inconsistent")
    return rows, (pages, size, total)
