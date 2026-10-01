"""Read-only conflict check for imported shipping fees versus current Salla facts.

The import remains immutable accounting evidence. Only the explicit current
carrier observation written by verified Salla intake is compared; legacy root
labels and historical raw shipments cannot become current-carrier evidence.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from shipping_companies import normalize_shipping_company


CARRIER_CONFLICT = "shipping_current_carrier_conflict_review_required"
ORDER_AMBIGUOUS = "shipping_current_order_ambiguous_review_required"
SHIPMENT_CANCELLED = "shipping_current_shipment_cancelled_review_required"
CARRIER_UNRESOLVED = "shipping_current_carrier_unresolved_review_required"
_CANCELLED = frozenset({"cancelled", "canceled", "void", "deleted"})
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")
_REFERENCE_FIELDS = ("order_number", "order_reference", "order_reference_id", "reference_id")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _INVISIBLE.sub("", _text(value)))
    text = " ".join(text.strip("'’`\"").split()).casefold()
    text = re.sub(r"[\u064b-\u0652]", "", text)
    return (text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
            .replace("ى", "ي").replace("ة", "ه"))


def _identity_values(value: Any) -> list[Any]:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return []
    text = str(value).strip()
    if not text:
        return []
    values: list[Any] = [text]
    # Accept JSON number/string transport differences without conflating
    # a textual reference with significant leading zeroes.
    if text.isascii() and text.isdigit() and str(int(text)) == text:
        values.append(int(text))
    return values


def _current_observation(row: dict[str, Any]) -> dict[str, Any] | None:
    observation = row.get("salla_shipping_current")
    if not isinstance(observation, dict):
        return None
    name = _text(observation.get("company_name"))
    human_name = bool(name and not name.replace(".", "", 1).isdigit()
                      and normalize_shipping_company(name)[0] != "unknown")
    if (observation.get("source_kind") not in {"order", "shipment"}
            or not _text(observation.get("source_path"))
            or not (human_name or _text(observation.get("company_code")))):
        return None
    return {**observation, "company_name": name if human_name else None}


def _same_carrier(
    current_name: str, evidence_name: str, *, policy: dict[str, Any],
    courier_id: str, courier_name: str,
) -> bool:
    current_label = _label(current_name)
    if current_label == _label(evidence_name):
        return True

    # Approved policy aliases identify the accounting counterparty. Provider
    # company_code is deliberately excluded: its ID namespace is independent.
    matches: set[str] = set()
    for version in policy.get("versions") or []:
        if not isinstance(version, dict) or version.get("verification_status") != "approved":
            continue
        aliases = [*(version.get("aliases_normalized") or []), *(version.get("aliases") or []),
                   version.get("name")]
        if current_label in {_label(alias) for alias in aliases if _text(alias)}:
            identity = _text(version.get("courier_id"))
            if identity:
                matches.add(identity)
    if matches:
        return matches == {courier_id}

    current_key = normalize_shipping_company(current_name)[0]
    # The generic "مندوب" alias covers multiple distinct local couriers; only
    # exact labels or an approved policy may equate those names.
    if current_key in {"unknown", "mandoob"} or current_key.startswith("other:"):
        return False
    return current_key in {
        normalize_shipping_company(evidence_name)[0],
        normalize_shipping_company(courier_name)[0],
    }


async def current_shipping_review_code(
    db: Any, *, owner: str, evidence: dict[str, Any], policy: dict[str, Any],
    courier_id: str, courier_name: str,
) -> str | None:
    """Return a fixed review code for a reliable contradiction, without writes.

    Export ``order_reference`` is the public reference and takes precedence
    over its internal ``order_number``. Without that field, support both known
    canonical identity shapes but require a unique owner-scoped match.
    """
    public_reference = evidence.get("order_reference") or evidence.get("order_reference_id")
    explicit_values = _identity_values(public_reference)
    values = explicit_values or _identity_values(evidence.get("order_number"))
    if not values:
        return None
    identity_fields = _REFERENCE_FIELDS if explicit_values else (*_REFERENCE_FIELDS, "order_id", "salla_order_id")
    rows = await db.unified_orders.find(
        {"user_id": owner, "$or": [{field: {"$in": values}} for field in identity_fields]},
        {"salla_shipping_current": 1},
    ).limit(3).to_list(3)
    observations = [observation for row in rows
                    if (observation := _current_observation(row)) is not None]
    if not observations:
        return None
    if len(rows) != 1:
        return ORDER_AMBIGUOUS
    current = observations[0]
    status = _label(current.get("status")).replace("-", "_").replace(" ", "_")
    if status in _CANCELLED:
        return SHIPMENT_CANCELLED
    # A confirmed provider ID without a resolved name cannot be mapped into
    # the imported courier's accounting namespace. Review rather than silently
    # reuse the old import or infer a name from the opaque provider ID.
    if not current.get("company_name"):
        return CARRIER_UNRESOLVED
    if not _same_carrier(
        current["company_name"], _text(evidence.get("shipping_company")),
        policy=policy, courier_id=courier_id, courier_name=courier_name,
    ):
        return CARRIER_CONFLICT
    return None
