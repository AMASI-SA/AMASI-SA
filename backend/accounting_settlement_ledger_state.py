"""Read settlement state from its authoritative ledger without rewriting history.

The stored lifecycle remains the posting snapshot. An audited full reversal
projects a terminal reversed state across draft and register APIs. This also
covers historical reversals and single-entry reversal paths.
"""

async def with_ledger_state(db, owner_id, documents):
    groups = {d.get("ledger_txn_group_id") for d in documents
              if d.get("status") == "posted" and d.get("ledger_txn_group_id")}
    if not groups:
        return documents
    rows = await db.general_ledger.find(
        {"user_id": owner_id, "txn_group_id": {"$in": list(groups)}},
        {"_id": 0, "txn_group_id": 1, "status": 1,
         "reversed_by_entry_id": 1, "metadata.txn_type": 1},
    ).to_list(None)
    by_group = {}
    for row in rows:
        by_group.setdefault(row["txn_group_id"], []).append(row)
    result = []
    for document in documents:
        legs = by_group.get(document.get("ledger_txn_group_id"), [])
        fully_reversed = len(legs) >= 2 and all(
            (leg.get("metadata") or {}).get("txn_type") == "provider_settlement_v2"
            and leg.get("status") == "reversed"
            and leg.get("reversed_by_entry_id") for leg in legs
        )
        if document.get("status") == "posted" and fully_reversed:
            document = {**document, "status": "reversed", "workflow_state": "reversed",
                        "stored_status": "posted", "status_source": "general_ledger"}
        result.append(document)
    return result


def ledger_status_candidates(statuses):
    """Reversed records can still carry their original posted snapshot."""
    return sorted(set(statuses) | ({"posted"} if "reversed" in statuses else set()))
