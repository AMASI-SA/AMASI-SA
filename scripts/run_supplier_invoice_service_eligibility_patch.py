#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

script_path = Path(__file__).with_name("apply_supplier_invoice_service_eligibility.py")
source = script_path.read_text(encoding="utf-8")
original_replace_once = '''def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)
'''
robust_replace_once = '''def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 1:
        return text.replace(old, new, 1)

    if label == "filter invoice-ineligible product services":
        marker = "        if _service_is_complete(raw):\\n"
        if text.count(marker) == 1:
            return text.replace(
                marker,
                "        if not _service_is_invoice_eligible(raw):\\n"
                "            continue\\n"
                + marker,
                1,
            )

    if label == "publish service eligibility evidence":
        start = text.find("def supplier_piece_invoice_services(")
        end = text.find("def supplier_piece_reference_price(", start)
        marker = "            \\\"add_to_product\\\": False,\\n"
        if start >= 0 and end > start:
            segment = text[start:end]
            if segment.count(marker) == 1:
                replacement = (
                    "            \\\"customer_selected\\\": bool(\\n"
                    "                raw.get(\\\"customer_selected\\\") is True\\n"
                    "                or _text(raw.get(\\\"source\\\")).casefold() == \\\"option\\\"\\n"
                    "            ),\\n"
                    "            \\\"supplier_invoice_required\\\": bool(\\n"
                    "                raw.get(\\\"supplier_invoice_required\\\") is True\\n"
                    "            ),\\n"
                    + marker
                )
                segment = segment.replace(marker, replacement, 1)
                return text[:start] + segment + text[end:]

    if label == "respect explicit empty invoice services":
        start = text.find("def _invoice_group_key(scan: dict[str, Any])")
        end = text.find("    services = tuple(sorted(", start)
        if start >= 0 and end > start:
            replacement = (
                "def _invoice_group_key(scan: dict[str, Any]) -> tuple[Any, ...]:\\n"
                "    invoice_services = scan.get(\\\"invoice_services\\\")\\n"
                "    scan_services = (\\n"
                "        invoice_services\\n"
                "        if isinstance(invoice_services, list)\\n"
                "        else scan.get(\\\"services\\\")\\n"
                "    ) or []\\n"
            )
            return text[:start] + replacement + text[end:]

    if label == "rebuild active invoice service candidates":
        start = text.find("async def _recent_session_events(")
        next_function = text.find("async def _cancellable_session_events(", start)
        return_index = text.rfind("    return rows\\n", start, next_function)
        if start >= 0 and next_function > start and return_index > start:
            insertion = (
                "    session = await db[SESSIONS].find_one(\\n"
                "        {\\\"user_id\\\": user_id, \\\"id\\\": session_id},\\n"
                "        {\\\"_id\\\": 0},\\n"
                "        **kwargs,\\n"
                "    )\\n"
                "    if session:\\n"
                "        service_catalog = await _supplier_service_catalog(\\n"
                "            db,\\n"
                "            user_id=user_id,\\n"
                "            session=session,\\n"
                "            mongo_session=mongo_session,\\n"
                "        )\\n"
                "        for row in rows:\\n"
                "            row[\\\"invoice_services\\\"] = supplier_piece_invoice_services(\\n"
                "                row, session, service_catalog,\\n"
                "            )\\n"
                "    return rows\\n"
            )
            return (
                text[:return_index]
                + insertion
                + text[return_index + len("    return rows\\n"):]
            )

    raise RuntimeError(f"{label}: expected exactly one match, found {count}")
'''
if original_replace_once not in source:
    raise RuntimeError("replace_once definition anchor not found")
source = source.replace(original_replace_once, robust_replace_once, 1)
exec(
    compile(source, str(script_path), "exec"),
    {"__name__": "__main__", "__file__": str(script_path)},
)
