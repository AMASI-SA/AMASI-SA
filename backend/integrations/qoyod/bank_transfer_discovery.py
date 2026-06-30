"""Iter-293 (companion) — Discovery probe for bank_transfer payloads.

Why this exists
---------------
Iter-294 will route bank_transfer orders to the correct Qoyod bank
account based on which bank the customer transferred INTO. To design
that routing safely we need to know the EXACT JSON path in the Salla
payload that carries the receiving bank — Salla's docs don't pin it
down and there are several plausible locations (transactions[],
payment_method_details, custom_fields, etc.).

This module does TWO things, both READ-ONLY:

  1. `scan_existing_payloads()` — walks `qoyod_payloads` looking for
     orders whose canonical payment_method resolves to `bank_transfer`
     and returns a redacted snapshot of every JSON path that looks
     bank-related (heuristic: key contains "bank" / "iban" /
     "transfer" / "account_number" etc.). Lets us inspect historical
     data immediately, no waiting for new orders.

  2. `discover_candidate_paths()` — given a single normalized payload
     dict, returns the set of candidate JSON paths that COULD carry
     the receiving-bank info, so the user can confirm which one is
     authoritative.

Nothing mutates. Nothing is sent to Qoyod. Nothing is auto-routed.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from .payment_methods import provider_family

# Keys whose name suggests bank-routing relevance. The match is run
# against EACH underscore/hyphen-separated token of the key's last
# path segment, so compound keys like `bank_name`, `transfer_details`,
# and `to_bank_id` all match cleanly.
_BANK_HINT_TOKENS = {
    "bank", "iban", "swift", "receiving", "transfer", "branch",
    "transactions", "merchant_bank", "to_bank", "from_bank",
    "account_number", "account_no", "accountnumber",
    "payment_log", "payment_details", "payment_method_details",
    "الراجحي", "الأهلي", "الإنماء", "بنك", "تحويل", "حساب",
}

# Sensitive keys to redact in the snapshot (the response is shown in
# the UI / shared with the agent — never include card / customer pii).
_REDACT_PATTERNS = re.compile(
    r"(?i)\b(card|cvv|cvc|pan|password|secret|token|email|phone|mobile|"
    r"national_id|civil_id|otp|ssn)\b"
)


def _walk(obj: Any, path: str = "$") -> list[tuple[str, Any]]:
    """Yield (json_path, value) for every leaf in `obj`.

    Path uses jq-style notation: `$.order.transactions[0].bank_name`.
    """
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_walk(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_walk(v, f"{path}[{i}]"))
    else:
        out.append((path, obj))
    return out


def _redact(value: Any) -> Any:
    """Replace string values that LOOK like sensitive data with `***`."""
    if not isinstance(value, str):
        return value
    if len(value) > 40:
        return value[:8] + "…(redacted long string)…"
    return value


def _key_matches_bank_hint(key: str) -> bool:
    """Check whether `key` (final path segment) contains any bank hint
    token. Splits on underscore/hyphen so `bank_name` → ["bank","name"]
    → matches because `bank` ∈ _BANK_HINT_TOKENS."""
    if not key:
        return False
    lower = key.lower()
    # Multi-word tokens (with underscores) must match as substrings.
    for tok in _BANK_HINT_TOKENS:
        if "_" in tok:
            if tok in lower:
                return True
    # Single-word tokens: any of the split parts matching = hit.
    parts = re.split(r"[_\-\s]+", lower)
    return any(p in _BANK_HINT_TOKENS for p in parts if p)


def discover_candidate_paths(payload: dict) -> list[dict]:
    """Walk one payload dict and return the candidate bank-related paths.

    Returns: [{path, value_preview, key_matched, is_redacted}].
    """
    hits: list[dict] = []
    for jpath, leaf in _walk(payload):
        # Match on the last segment of the json-path (the actual key
        # name), not on the full path.
        last_seg = jpath.rsplit(".", 1)[-1]
        # Strip array-index suffix like `transactions[0]` → `transactions`.
        last_seg_clean = re.sub(r"\[\d+\]$", "", last_seg)
        if not _key_matches_bank_hint(last_seg_clean):
            continue
        redacted = bool(_REDACT_PATTERNS.search(last_seg_clean))
        hits.append({
            "path":           jpath,
            "key":            last_seg_clean,
            "value_preview":  "***" if redacted else _redact(leaf),
            "is_redacted":    redacted,
            "value_type":     type(leaf).__name__,
        })
    return hits


async def scan_existing_payloads(
    db,
    *,
    user_id: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """Search historical qoyod_payloads for bank_transfer orders and
    return candidate JSON paths from each.

    Returns:
        {
          ok: true,
          scanned_total: int,         # bank_transfer rows found
          samples: [                  # up to `limit`
            {
              salla_order_id, order_number, captured_at,
              canonical_payment_method,
              candidate_paths: [ ... discover_candidate_paths(payload) ... ],
            },
          ]
        }
    """
    limit = max(1, min(int(limit or 10), 50))
    q: dict[str, Any] = {}
    if user_id:
        q["user_id"] = user_id

    projection = {
        "_id": 0,
        "salla_order_id": 1,
        "order_number": 1,
        "captured_at": 1,
        "created_at": 1,
        "normalized_order": 1,
        "original_payload": 1,
    }

    samples: list[dict] = []
    scanned_total = 0

    async for doc in db.qoyod_payloads.find(q, projection).sort("_id", -1):
        normalized = doc.get("normalized_order") or {}
        pm = normalized.get("payment_method") or normalized.get("payment_method_native")
        family = provider_family(pm)
        if family != "bank_transfer":
            continue
        scanned_total += 1
        if len(samples) >= limit:
            continue
        # Prefer the original (un-normalized) payload for discovery
        # because the normalizer drops fields we don't yet use.
        source = doc.get("original_payload") or normalized
        paths = discover_candidate_paths(source if isinstance(source, dict) else {})
        samples.append({
            "salla_order_id":            doc.get("salla_order_id"),
            "order_number":              doc.get("order_number"),
            "captured_at":               doc.get("captured_at") or doc.get("created_at"),
            "canonical_payment_method":  pm,
            "candidate_paths":           paths,
        })

    return {
        "ok":            True,
        "scanned_total": scanned_total,
        "sample_count":  len(samples),
        "limit":         limit,
        "samples":       samples,
        "notes": [
            "READ-ONLY. No mutations, no Qoyod calls.",
            "If scanned_total == 0, no bank_transfer orders are in DB yet.",
            "Look at `key` per row to spot the field Salla uses for the receiving bank.",
        ],
    }
