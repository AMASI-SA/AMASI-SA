"""Report index bootstrap must retain contradictory financial evidence."""
import ast
from pathlib import Path

import pytest
from mongomock_motor import AsyncMongoMockClient

from accounting_ledger_v2 import (
    GROUPS_COLLECTION,
    ensure_accounting_ledger_v2_indexes,
    read_supplier_invoice_journal_evidence_v2,
)
from preparation_piece_operations import PIECES, ensure_piece_order_cost_index
from supplier_receiving_routes import (
    SUPPLIER_INVOICES,
    ensure_supplier_invoice_identity_index,
)


@pytest.mark.asyncio
async def test_report_indexes_preserve_duplicate_and_cross_operation_proof():
    db = AsyncMongoMockClient()["report-index-contract"]
    await db[PIECES].insert_many([
        {"user_id": "owner", "order_number": "10", "batch_id": batch}
        for batch in ("first", "second")
    ])
    await db[SUPPLIER_INVOICES].insert_many([
        {"user_id": "owner", "id": "duplicate", "status": status}
        for status in ("payable_posted", "draft")
    ])
    await db[GROUPS_COLLECTION].insert_many([
        {"user_id": "owner", "operation_id": "first", "idempotency_key": "a",
         "txn_group_id": "group", "status": "posted"},
        {"user_id": "owner", "operation_id": "second", "idempotency_key": "b",
         "txn_group_id": "group", "status": "draft"},
        {"user_id": "owner", "operation_id": "third", "idempotency_key": "c",
         "txn_group_id": "reversal", "reversal_of_txn_group_id": "group", "status": "posted"},
    ])
    # Startup runs only the narrow piece/invoice builders. They must work even
    # if older operational data would not satisfy unrelated unique indexes.
    for _ in range(2):
        await ensure_piece_order_cost_index(db)
        await ensure_supplier_invoice_identity_index(db)
        await ensure_accounting_ledger_v2_indexes(db)

    evidence = await read_supplier_invoice_journal_evidence_v2(
        db, user_id="owner", txn_group_ids=["group"],
    )
    assert len(evidence) == 3
    assert {row["status"] for row in evidence} == {"posted", "draft"}
    assert any(row.get("reversal_of_txn_group_id") == "group" for row in evidence)
    assert await db[PIECES].count_documents({"user_id": "owner", "order_number": "10"}) == 2
    assert await db[SUPPLIER_INVOICES].count_documents({"user_id": "owner", "id": "duplicate"}) == 2
    for collection, name in (
        (PIECES, "ix_preparation_piece_order_cost_v1"),
        (SUPPLIER_INVOICES, "ix_supplier_invoice_identity_v2"),
        (GROUPS_COLLECTION, "ix_accounting_v2_group_identity"),
        (GROUPS_COLLECTION, "ix_accounting_v2_group_reversal_proof"),
    ):
        index = (await db[collection].index_information())[name]
        assert not index.get("unique")
        assert "partialFilterExpression" not in index
        assert not index.get("sparse")


def test_report_indexes_bootstrap_before_global_startup_returns():
    tree = ast.parse((Path(__file__).parents[1] / "server.py").read_text())
    startup = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                   and node.name == "_global_startup")
    top_level_awaits = [node.value.value.func.id for node in startup.body
                        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Await)
                        and isinstance(node.value.value, ast.Call)
                        and isinstance(node.value.value.func, ast.Name)]
    for name in ("ensure_piece_order_cost_index", "ensure_supplier_invoice_identity_index",
                 "ensure_financial_provider_app_indexes"):
        assert top_level_awaits.count(name) == 1
