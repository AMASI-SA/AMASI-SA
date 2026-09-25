"""Focused duplicate-source boundary; no server startup or database connection."""
import ast
import asyncio
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

SOURCE = Path(__file__).resolve().parents[1] / "accounting_settlement_routes.py"
FUNCTION = next(
    node for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
    if isinstance(node, ast.AsyncFunctionDef)
    and node.name == "_create_draft_from_file"
)

async def no_indexes(_db):
    pass

class Collection:
    def __init__(self, row):
        self.row = row

    async def find_one(self, query, projection):
        assert query == {"user_id": "test-owner", "idempotency_key": "stable-ref"}
        return self.row

class Database:
    def __init__(self, row):
        self.accounting_settlements_v2 = Collection(row)
    # Every later lookup/write fails if the duplicate boundary is crossed.

@pytest.mark.parametrize(
    "old_hash,new_hash,conflict",
    [("same", "same", False), ("old", "new", True),
     ("", "new", True), ("old", "", True)],
)
def test_duplicate_source_boundary(old_hash, new_hash, conflict):
    namespace = {
        "Any": Any, "HTTPException": HTTPException,
        "ensure_accounting_settlement_indexes": no_indexes,
        "canonical_provider": lambda value: value,
        "statement_reference_from_file": lambda _file: "SYN-REFERENCE",
        "settlement_idempotency_key": lambda **_kw: "stable-ref",
        "_clean": lambda value: str(value or "").strip(),
    }
    exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]),
                 str(SOURCE), "exec"), namespace)
    row = {"id": "existing", "source_file_hash": old_hash, "status": "posted"}
    operation = namespace["_create_draft_from_file"](
        Database(row), owner_id="test-owner", actor={},
        file_doc={"id": "new-file", "provider": "tabby", "file_hash": new_hash},
    )
    if conflict:
        with pytest.raises(HTTPException) as error:
            asyncio.run(operation)
        assert error.value.status_code == 409
        assert error.value.detail == "settlement_source_conflict"
    else:
        assert asyncio.run(operation) == {**row, "duplicate": True}
