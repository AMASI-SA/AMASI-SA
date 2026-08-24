"""Provider and currency detection guard for the P01 settlement uploader."""
from __future__ import annotations

import io
import re
from typing import Any

import openpyxl

from accounting_settlement_currency_guard import (
    SUPPORTED_SETTLEMENT_CURRENCIES,
    normalize_settlement_currency,
)
from accounting_settlement_service import canonical_provider, provider_label
from settlements_import.registry import detect_provider
from settlements_import.service import import_file as _base_import_file

_CURRENCY_HEADER_LABELS = frozenset({
    "currency",
    "currencycode",
    "settlementcurrency",
    "payoutcurrency",
    "transactioncurrency",
    "العملة",
    "عملة",
    "رمزالعملة",
})
_ISO_CURRENCY_RE = re.compile(r"(?<![A-Z])([A-Z]{3})(?![A-Z])")
_ARABIC_CURRENCY_NAMES = {
    "ريالسعودي": "SAR",
    "دولارامريكي": "USD",
    "الدولارالامريكي": "USD",
    "دولار": "USD",
    "درهماماراتي": "AED",
    "الدرهمالاماراتي": "AED",
    "ريالقطري": "QAR",
    "ديناربحريني": "BHD",
    "ديناركويتي": "KWD",
    "ريالعماني": "OMR",
    "يورو": "EUR",
    "جنيهاسترليني": "GBP",
}


def _compact(value: Any) -> str:
    return re.sub(r"[\s\-_.:()/\\]+", "", str(value or "").strip()).lower()


def _currency_candidate(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    normalized = normalize_settlement_currency(raw)
    if normalized in SUPPORTED_SETTLEMENT_CURRENCIES:
        return normalized

    compact = _compact(raw)
    for name, code in _ARABIC_CURRENCY_NAMES.items():
        if name in compact:
            return code

    match = _ISO_CURRENCY_RE.search(raw.upper())
    if match:
        return match.group(1)
    return ""


def _workbook_sheets(workbook) -> list[Any]:
    sheets = list(getattr(workbook, "worksheets", []) or [])
    if not sheets:
        active = getattr(workbook, "active", None)
        if active is not None:
            sheets = [active]
    return sheets


def detect_workbook_currency(workbook, provider: str) -> tuple[str, str]:
    """Read explicit workbook currency cells before the legacy importer writes.

    Tabby and Tamara exports contain a dedicated ``Currency`` column. Salla
    and current Emkan exports may not contain one; those are resolved through
    the Saudi provider contract and the provenance is persisted. Any explicit
    non-SAR value fails before ``settlement_files`` or order actuals are written.
    """
    explicit: set[str] = set()
    sources: set[str] = set()

    for sheet in _workbook_sheets(workbook):
        try:
            iterator = sheet.iter_rows(values_only=True)
        except Exception:
            continue
        currency_columns: set[int] = set()
        for row_index, row in enumerate(iterator):
            if row_index >= 3000:
                break
            values = list(row or [])[:250]
            for column_index, value in enumerate(values):
                if not isinstance(value, str):
                    continue
                compact = _compact(value)
                if compact in _CURRENCY_HEADER_LABELS:
                    currency_columns.add(column_index)
                    sources.add("workbook.currency_column")
                    # Some exports put the selected currency beside the label.
                    for adjacent in values[column_index + 1:column_index + 4]:
                        candidate = _currency_candidate(adjacent)
                        if candidate:
                            explicit.add(candidate)
                            sources.add("workbook.currency_label")
                    continue
                if any(label in compact for label in _CURRENCY_HEADER_LABELS):
                    candidate = _currency_candidate(value)
                    if candidate:
                        explicit.add(candidate)
                        sources.add("workbook.currency_label")

            for column_index in currency_columns:
                if column_index >= len(values):
                    continue
                candidate = _currency_candidate(values[column_index])
                if candidate:
                    explicit.add(candidate)

    unsupported = sorted(explicit - set(SUPPORTED_SETTLEMENT_CURRENCIES))
    if unsupported:
        raise ValueError(
            "عملة كشف التسوية غير مدعومة في P01: "
            f"{', '.join(unsupported)}. المدعوم حاليًا SAR فقط."
        )
    if len(explicit) > 1:
        raise ValueError(
            "كشف التسوية يحتوي أكثر من عملة ولا يمكن ترحيله كقيد SAR واحد: "
            f"{', '.join(sorted(explicit))}"
        )
    if explicit:
        return next(iter(explicit)), "+".join(sorted(sources)) or "workbook.currency"

    selected = canonical_provider(provider)
    return "SAR", f"provider_contract_sar:{selected}"


async def _persist_detected_currency(
    db,
    *,
    user_id: str,
    file_id: str | None,
    currency: str,
    source: str,
) -> None:
    if not file_id:
        return
    collection = getattr(db, "settlement_files", None)
    if collection is None or not hasattr(collection, "update_one"):
        return
    await collection.update_one(
        {"id": file_id, "user_id": user_id},
        {"$set": {
            "currency": currency,
            "currency_source": source,
            "header.currency": currency,
            "header.currency_source": source,
        }},
    )


async def import_accounting_settlement_file(
    db,
    user_id: str,
    *,
    filename: str,
    content: bytes,
    provider_hint: str | None = None,
) -> dict:
    """Detect provider/currency and require them to satisfy the P01 contract.

    The historical importer treats ``provider_hint`` as authoritative and some
    parsers hard-code SAR in their returned header. P01 validates the original
    workbook first: a Tabby statement selected as Tamara, or an explicit USD
    statement parsed as SAR, must never reach storage.
    """
    try:
        workbook = openpyxl.load_workbook(
            io.BytesIO(content),
            data_only=True,
            read_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise ValueError(f"تعذّر فتح الملف كـ Excel: {exc}") from exc

    try:
        detected = canonical_provider(detect_provider(workbook))
        currency, currency_source = detect_workbook_currency(workbook, detected)
    finally:
        workbook.close()

    selected = canonical_provider(provider_hint) if provider_hint else detected
    if selected != detected:
        raise ValueError(
            "نوع الملف لا يطابق المزود المختار: "
            f"اخترت {provider_label(selected)} بينما الملف يعود إلى "
            f"{provider_label(detected)}"
        )

    result = await _base_import_file(
        db,
        user_id,
        filename=filename,
        content=content,
        provider_hint=detected,
    )
    returned = canonical_provider(result.get("provider") or detected)
    if returned != detected:
        raise ValueError("رجع المستورد مزودًا مختلفًا عن المزود المكتشف")

    await _persist_detected_currency(
        db,
        user_id=user_id,
        file_id=result.get("file_id"),
        currency=currency,
        source=currency_source,
    )
    return {
        **result,
        "currency": currency,
        "currency_source": currency_source,
    }


__all__ = [
    "detect_workbook_currency",
    "import_accounting_settlement_file",
]
