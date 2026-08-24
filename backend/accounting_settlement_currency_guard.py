"""Fail-closed currency contract for P01 provider settlements.

P01 posts only SAR journals. Provider parsers do not all expose a currency cell,
so a missing cell is resolved through the documented provider contract and is
stored explicitly as ``SAR`` with ``provider_contract_sar`` provenance. An
explicit non-SAR currency is rejected before a draft is written. Existing
legacy drafts without an explicit currency are blocked from submit/review/post.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from accounting_settlement_service import PROVIDERS, canonical_provider

SUPPORTED_SETTLEMENT_CURRENCIES = frozenset({"SAR"})
_CURRENCY_KEYS = (
    "currency",
    "currency_code",
    "settlement_currency",
    "payout_currency",
    "transaction_currency",
    "payment_currency",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def normalize_settlement_currency(value: Any) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    compact = (
        raw.upper()
        .replace(" ", "")
        .replace(".", "")
        .replace("_", "")
        .replace("-", "")
    )
    sar_aliases = {
        "SAR",
        "SR",
        "RS",
        "رسم",
        "رس",
        "ريالسعودي",
        "SAUDIRIYAL",
        "SAUDIARABIANRIYAL",
    }
    if compact in sar_aliases:
        return "SAR"
    return raw.upper()


def settlement_currency_from_file(file_doc: dict[str, Any]) -> tuple[str, str]:
    header = file_doc.get("header") or {}
    totals = file_doc.get("totals") or {}
    candidates: list[tuple[Any, str]] = []
    for key in _CURRENCY_KEYS:
        candidates.append((file_doc.get(key), f"file.{key}"))
        if isinstance(header, dict):
            candidates.append((header.get(key), f"header.{key}"))
        if isinstance(totals, dict):
            candidates.append((totals.get(key), f"totals.{key}"))

    for value, source in candidates:
        if not _clean(value):
            continue
        currency = normalize_settlement_currency(value)
        if currency not in SUPPORTED_SETTLEMENT_CURRENCIES:
            raise HTTPException(
                400,
                {
                    "code": "unsupported_settlement_currency",
                    "message": (
                        f"عملة كشف التسوية {currency or value} غير مدعومة. "
                        "P01 يسمح حاليًا بكشوف SAR فقط."
                    ),
                    "currency": currency or _clean(value),
                    "supported": sorted(SUPPORTED_SETTLEMENT_CURRENCIES),
                    "source": source,
                },
            )
        return currency, source

    provider = canonical_provider(file_doc.get("provider"))
    if provider in PROVIDERS:
        # This is an explicit provider-contract resolution, not a silent UI
        # assumption. The provenance is persisted on the accounting record.
        return "SAR", "provider_contract_sar"

    raise HTTPException(
        400,
        {
            "code": "settlement_currency_missing",
            "message": "تعذر تحديد عملة كشف التسوية بصورة موثقة",
            "supported": sorted(SUPPORTED_SETTLEMENT_CURRENCIES),
        },
    )


def currency_review_reasons(draft: dict[str, Any]) -> list[dict[str, str]]:
    raw = draft.get("currency")
    currency = normalize_settlement_currency(raw)
    if not currency:
        return [{
            "code": "settlement_currency_missing",
            "message": "عملة التسوية غير محفوظة صراحة؛ أعد إنشاء المسودة من الكشف",
        }]
    if currency not in SUPPORTED_SETTLEMENT_CURRENCIES:
        return [{
            "code": "settlement_currency_unsupported",
            "message": f"لا يمكن ترحيل تسوية بعملة {currency}; المدعوم حاليًا SAR فقط",
        }]
    return []


def build_currency_guarded_create(
    original: Callable[..., Awaitable[dict[str, Any]]],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    if getattr(original, "_mz2_currency_guarded", False):
        return original

    @wraps(original)
    async def guarded_create(
        db,
        *,
        owner_id: str,
        actor: dict[str, Any],
        file_doc: dict[str, Any],
        bank_account_id: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        currency, currency_source = settlement_currency_from_file(file_doc)
        verified_at = _now()
        header = dict(file_doc.get("header") or {})
        header["currency"] = currency
        header["currency_source"] = currency_source
        explicit_file_doc = {
            **file_doc,
            "currency": currency,
            "currency_source": currency_source,
            "header": header,
        }
        draft = await original(
            db,
            owner_id=owner_id,
            actor=actor,
            file_doc=explicit_file_doc,
            bank_account_id=bank_account_id,
            notes=notes,
        )
        if not draft:
            return draft

        source_snapshot = dict(draft.get("source_snapshot") or {})
        source_snapshot["currency"] = currency
        source_snapshot["currency_source"] = currency_source
        enriched = {
            **draft,
            "currency": currency,
            "currency_source": currency_source,
            "currency_verified_at": verified_at,
            "source_snapshot": source_snapshot,
        }
        draft_id = _clean(draft.get("id"))
        if draft_id:
            await db.accounting_settlements_v2.update_one(
                {"id": draft_id, "user_id": owner_id},
                {"$set": {
                    "currency": currency,
                    "currency_source": currency_source,
                    "currency_verified_at": verified_at,
                    "source_snapshot.currency": currency,
                    "source_snapshot.currency_source": currency_source,
                }},
            )
        return enriched

    guarded_create._mz2_currency_guarded = True
    return guarded_create


def explicit_currency_register_item(
    original: Callable[[dict[str, Any]], dict[str, Any]],
    document: dict[str, Any],
) -> dict[str, Any]:
    item = original(document)
    currency = normalize_settlement_currency(document.get("currency"))
    item["currency"] = currency or None
    item["currency_source"] = document.get("currency_source")
    item["currency_supported"] = currency in SUPPORTED_SETTLEMENT_CURRENCIES
    return item


def install_accounting_settlement_currency_guard(
    routes_module,
    lifecycle_module,
    register_module=None,
) -> None:
    """Patch module globals resolved by registered route handlers.

    The project already uses runtime guards for importer identity and evidence
    deletion. Keeping the currency contract in one guard avoids duplicating the
    large compatibility route module while still applying before any write or
    lifecycle transition.
    """
    original_create = getattr(
        routes_module,
        "_mz2_currency_original_create",
        routes_module._create_draft_from_file,
    )
    routes_module._mz2_currency_original_create = original_create
    routes_module._create_draft_from_file = build_currency_guarded_create(original_create)

    original_bank_reasons = getattr(
        lifecycle_module,
        "_mz2_currency_original_bank_reasons",
        lifecycle_module.bank_match_review_reasons,
    )
    lifecycle_module._mz2_currency_original_bank_reasons = original_bank_reasons

    def bank_and_currency_reasons(draft: dict[str, Any]) -> list[dict[str, str]]:
        return [
            *original_bank_reasons(draft),
            *currency_review_reasons(draft),
        ]

    lifecycle_module.bank_match_review_reasons = bank_and_currency_reasons

    if register_module is not None:
        original_register_item = getattr(
            register_module,
            "_mz2_currency_original_register_item",
            register_module._register_item,
        )
        register_module._mz2_currency_original_register_item = original_register_item

        def register_item(document: dict[str, Any]) -> dict[str, Any]:
            return explicit_currency_register_item(original_register_item, document)

        register_module._register_item = register_item


__all__ = [
    "SUPPORTED_SETTLEMENT_CURRENCIES",
    "build_currency_guarded_create",
    "currency_review_reasons",
    "explicit_currency_register_item",
    "install_accounting_settlement_currency_guard",
    "normalize_settlement_currency",
    "settlement_currency_from_file",
]
