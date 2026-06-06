"""Parser registry — picks the right parser by sniffing column headers."""
from __future__ import annotations

from typing import Any

import openpyxl

from .parsers import salla as p_salla
from .parsers import tamara as p_tamara
from .parsers import tabby as p_tabby

PROVIDER_SALLA = "salla"
PROVIDER_TAMARA = "tamara"
PROVIDER_TABBY = "tabby"


_PROVIDER_MAP = {
    PROVIDER_SALLA: p_salla,
    PROVIDER_TAMARA: p_tamara,
    PROVIDER_TABBY: p_tabby,
}


def detect_provider(workbook: openpyxl.Workbook) -> str:
    """Sniff the first sheet to guess the provider. Returns one of
    PROVIDER_SALLA / PROVIDER_TAMARA / PROVIDER_TABBY, else raises
    ValueError when the file shape doesn't match any known provider.

    Detection rules (ordered most-specific → least-specific):
      • Salla   — sheet title starts with "Invoice #" AND row 1 has
                  the literal header "رقم الطلب".
      • Tamara  — anywhere in the first 30 rows there's a cell with
                  "Tamara Order ID" or "Merchant Order ID".
      • Tabby   — anywhere in the first 12 rows there's the cell
                  "Refundable Commission" or sheet title == "SR".
    """
    ws = workbook.active
    title = (ws.title or "").strip()

    # Read first 30 rows once for cheap scans
    preview: list[list[Any]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True, max_row=30)):
        preview.append(list(row))

    def has_cell(needle: str, rows: list[list[Any]]) -> bool:
        needle_lower = needle.lower()
        for r in rows:
            for c in r:
                if c is None:
                    continue
                if needle_lower in str(c).strip().lower():
                    return True
        return False

    # Salla
    if title.startswith("Invoice #") and has_cell("رقم الطلب", preview[:2]):
        return PROVIDER_SALLA

    # Tamara
    if has_cell("Tamara Order ID", preview) or has_cell("Tamara Merchant ID", preview):
        return PROVIDER_TAMARA

    # Tabby
    if title.strip() == "SR" or has_cell("Refundable Commission", preview):
        return PROVIDER_TABBY

    raise ValueError(
        "تعذّر تحديد نوع ملف التسوية. تأكّد أن الملف من سلة أو تمارا أو تابي."
    )


def parse(provider: str, workbook: openpyxl.Workbook) -> dict:
    """Run the provider-specific parser and return:
        {
            "provider": str,
            "header": {...provider-specific metadata...},
            "entries": [ ...row-level dicts... ],
            "totals":  {...aggregated totals over entries... },
        }
    """
    mod = _PROVIDER_MAP.get(provider)
    if mod is None:
        raise ValueError(f"Unknown provider '{provider}'")
    return mod.parse(workbook)
