"""Choose a Salla buyer ID from already-verified Mezan order history.

This module has no database or external API access. Its caller must provide
only tenant-scoped authoritative Salla order-detail records, not guessed
phone-to-customer matches. Recipient and buyer are separate identities.

Salla's current Create Order API requires customer.name + customer.mobile +
customer.email when customer.id is unavailable. Never invent an email.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .payload import Customer

_EMAIL = re.compile(r"^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,40}$")
_SA = re.compile(r"^(?:\+966|00966|966|0)?(5[0-9]{8})$")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


class CustomerIdentityUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class KnownSallaCustomer:
    """Authenticated owner-scoped provider evidence, never LLM-supplied."""
    user_id: str
    salla_customer_id: int
    buyer_mobile: str
    source: str = "salla_order_details"


@dataclass(frozen=True)
class ResolvedBuyer:
    customer: Customer
    source: str
    email_requested: bool = False


def normalized_mobile(value: str) -> str:
    if type(value) is not str:
        raise CustomerIdentityUnavailable("invalid_customer_mobile")
    txt = re.sub(r"[\s()\-]", "", value.strip())
    match = _SA.fullmatch(txt)
    if match:
        return "+966" + match.group(1)
    if _E164.fullmatch(txt):
        return txt
    raise CustomerIdentityUnavailable("invalid_customer_mobile")


def resolve_salla_buyer(
    *, user_id: str, verified_whatsapp_buyer_mobile: str,
    previous_customers: tuple[KnownSallaCustomer, ...],
    buyer_name: str | None = None, buyer_email: str | None = None,
) -> ResolvedBuyer:
    """Prefer ONE exact existing Salla customer ID; never invent guest email.

    The buyer mobile must have been verified as the Chatwoot customer contact
    by the trusted inbox. Do not substitute gift recipient name or phone.
    """
    if type(user_id) is not str or not user_id.strip():
        raise CustomerIdentityUnavailable("owner_scope_missing")
    number = normalized_mobile(verified_whatsapp_buyer_mobile)
    if type(previous_customers) is not tuple or len(previous_customers) > 250:
        raise CustomerIdentityUnavailable("invalid_customer_evidence")
    matched = set()
    for row in previous_customers:
        if not isinstance(row,KnownSallaCustomer):
            raise CustomerIdentityUnavailable("invalid_customer_evidence")
        if row.user_id != user_id:
            # A cross-merchant identity must never authorize Salla creation.
            raise CustomerIdentityUnavailable("customer_evidence_scope_mismatch")
        if row.source != "salla_order_details":
            raise CustomerIdentityUnavailable("unverified_customer_evidence")
        if type(row.salla_customer_id) is not int or row.salla_customer_id < 1:
            raise CustomerIdentityUnavailable("invalid_salla_customer_id")
        if normalized_mobile(row.buyer_mobile) == number:
            matched.add(row.salla_customer_id)
    if len(matched) > 1:
        raise CustomerIdentityUnavailable("ambiguous_salla_customer_identity")
    if matched:
        return ResolvedBuyer(Customer(id=next(iter(matched))),"existing_salla_customer_id")
    if not buyer_name or not isinstance(buyer_name,str) or len(buyer_name.strip()) < 2:
        raise CustomerIdentityUnavailable("new_buyer_name_required")
    if not buyer_email or type(buyer_email) is not str:
        raise CustomerIdentityUnavailable("new_buyer_email_required")
    email = buyer_email.strip().lower()
    if len(email) > 254 or not _EMAIL.fullmatch(email):
        raise CustomerIdentityUnavailable("invalid_new_buyer_email")
    return ResolvedBuyer(
        Customer(name=buyer_name.strip(),mobile=number,email=email),
        "verified_customer_supplied_email",
    )
