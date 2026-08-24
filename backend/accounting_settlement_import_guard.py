"""Provider-detection guard used by the P01 accounting settlement uploader."""
from __future__ import annotations

import io

import openpyxl

from accounting_settlement_service import canonical_provider, provider_label
from settlements_import.registry import detect_provider
from settlements_import.service import import_file as _base_import_file


async def import_accounting_settlement_file(
    db,
    user_id: str,
    *,
    filename: str,
    content: bytes,
    provider_hint: str | None = None,
) -> dict:
    """Detect the workbook provider and require it to match the UI choice.

    The historical importer treats ``provider_hint`` as authoritative. P01
    must fail closed instead: a Tabby statement selected as Tamara must never
    be parsed or stored under the wrong provider.
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
    return result


__all__ = ["import_accounting_settlement_file"]
