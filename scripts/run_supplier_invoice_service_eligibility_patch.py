#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

script_path = Path(__file__).with_name("apply_supplier_invoice_service_eligibility.py")
source = script_path.read_text(encoding="utf-8")
source = source.replace(
    '''supplier = replace_once(
    supplier,
    ''' + "'''" + '''        if not service_id or service_id not in supplier_links:
            continue
        if _service_is_complete(raw):
''' + "'''" + ''',
    ''' + "'''" + '''        if not service_id or service_id not in supplier_links:
            continue
        if not _service_is_invoice_eligible(raw):
            continue
        if _service_is_complete(raw):
''' + "'''" + ''',
    "filter invoice-ineligible product services",
)
''',
    '''supplier = replace_once(
    supplier,
    ''' + "'''" + '''        if _service_is_complete(raw):
''' + "'''" + ''',
    ''' + "'''" + '''        if not _service_is_invoice_eligible(raw):
            continue
        if _service_is_complete(raw):
''' + "'''" + ''',
    "filter invoice-ineligible product services",
)
''',
)
source = source.replace(
    "            .to_list(limit)\n    )\n    return rows",
    "        .to_list(limit)\n    )\n    return rows",
)
source = source.replace(
    "            .to_list(limit)\n    )\n    session = await db[SESSIONS].find_one",
    "        .to_list(limit)\n    )\n    session = await db[SESSIONS].find_one",
)
exec(
    compile(source, str(script_path), "exec"),
    {"__name__": "__main__", "__file__": str(script_path)},
)
