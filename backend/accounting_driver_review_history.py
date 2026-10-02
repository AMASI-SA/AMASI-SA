"""Read sealed native decisions, including revisions superseded in the queue.

This reader neither reconstructs pre-V2 decisions nor consumes receipts. A
decision remains history when its journal is subsequently reversed.
"""
import base64
import binascii
import json
import re

from accounting_driver_payment_review import REVIEWS, sealed
from accounting_ledger_v2 import read_verified_journal_metadata_v2, get_journal_v2, GROUPS_COLLECTION
from accounting_module_contract import OPERATION_ID
from accounting_shipping_native import _actor
from accounting_shipping_native_contract import EVENTS, SOURCE, digest, instant, money, fail

INTEGRITY = "driver_payment_history_integrity_failure"
CURSOR_ERROR = "driver_payment_history_cursor_invalid"
MAX_COVERAGE = 10000
METADATA = (
    "review_id", "review_revision", "assignment_id", "order_id", "order_number",
    "party_type", "party_id", "amount", "payment_method", "receipt_reference",
    "approval_actor", "approval_at", "decision", "note", "review_idempotency_key",
    "action", "settlement_origin", "evidence_id",
)


def validate_event(row, owner):
    if (not sealed(row) or row.get("user_id") != owner
            or row.get("kind") != "driver_payment_review"
            or not all(key in row for key in METADATA)
            or not all(isinstance(row.get(key), str) and row[key] for key in (
                "review_id", "assignment_id", "order_id", "order_number", "party_id",
                "approval_actor", "approval_at", "evidence_id", "request_hash"))
            or type(row.get("review_revision")) is not int or row["review_revision"] < 1
            or row.get("party_type") != "store_driver"
            or row.get("action") != "receive_cod"
            or row.get("settlement_origin") != "driver_payment_review"
            or row.get("payment_method") not in {"bank_transfer", "card_terminal"}
            or row.get("decision") not in {"approved", "rejected"}):
        fail(INTEGRITY)
    key = digest([owner, "driver-review", row["review_id"], row["review_revision"]])
    if row.get("_id") != key or row["review_idempotency_key"] != key:
        fail(INTEGRITY)
    instant(row["approval_at"])
    money(row["amount"], positive=True)
    if row["decision"] == "rejected":
        if row.get("txn_group_id") is not None or row.get("destination") is not None:
            fail(INTEGRITY)
    else:
        dest = row.get("destination")
        if (not row.get("txn_group_id") or not isinstance(dest, dict)
                or dest.get("user_id") != owner or dest.get("currency") != "SAR"
                or dest.get("status") != "verified" or money(dest.get("amount")) != money(row["amount"])
                or not all(dest.get(k) for k in ("entity_type", "entity_id", "sub_account",
                    "financial_account_id", "source_namespace", "source_record_id", "source_revision"))):
            fail(INTEGRITY)
        if row["payment_method"] == "card_terminal":
            if (dest.get("destination_kind") != "pos_receivable" or dest.get("entity_type") != "asset"
                    or dest.get("sub_account") != "other_receivable"
                    or dest.get("verification") != "accountant_pos_review_approved"
                    or not dest.get("display_name")):
                fail(INTEGRITY)
        elif (dest.get("destination_kind") != "bank" or dest.get("entity_type") != "bank"
                or dest.get("verification") != "bank_arrival_confirmed"):
            fail(INTEGRITY)


async def present(db, owner, row):
    validate_event(row, owner)
    reversed_journal = False
    if row["decision"] == "rejected" and await db[GROUPS_COLLECTION].find_one({
            "user_id": owner, "operation_id": OPERATION_ID, "idempotency_key": row["_id"]}):
        fail(INTEGRITY)
    if row["decision"] == "approved":
        metadata = await read_verified_journal_metadata_v2(db, user_id=owner,
            txn_group_id=row["txn_group_id"])
        if any(metadata.get(key) != row[key] for key in METADATA) or metadata.get("destination") != row["destination"]:
            fail(INTEGRITY)
        journal = await get_journal_v2(db, user_id=owner, txn_group_id=row["txn_group_id"])
        group = (journal or {}).get("group") or {}
        if (group.get("idempotency_key") != row["_id"] or group.get("source") != SOURCE
                or group.get("txn_type") != "driver_payment_review_settlement"
                or group.get("reversal_of_txn_group_id")):
            fail(INTEGRITY)
        reversals = await db[GROUPS_COLLECTION].find({"user_id": owner, "operation_id": OPERATION_ID,
            "reversal_of_txn_group_id": row["txn_group_id"]}).limit(2).to_list(2)
        if len(reversals) > 1:
            fail(INTEGRITY)
        for reversal in reversals:
            await read_verified_journal_metadata_v2(db, user_id=owner, txn_group_id=reversal["txn_group_id"])
            reversed_journal = True
    destination = row.get("destination")
    return {"id": row["_id"], "status": row["decision"], "review_id": row["review_id"],
        "review_revision": row["review_revision"], "assignment_id": row["assignment_id"],
        "party_id": row["party_id"], "driver_id": row["party_id"],
        "order_id": row["order_id"], "order_number": row["order_number"],
        "payment_method": row["payment_method"], "amount": row["amount"],
        "receipt_reference": row["receipt_reference"], "reviewed_by": row["approval_actor"],
        "reviewed_at": row["approval_at"], "note": row["note"],
        "financial_handoff_status": "posted" if row["decision"] == "approved" else "rejected_no_financial_effect",
        "financial_txn_group_id": row["txn_group_id"], "journal_reversed": reversed_journal,
        "destination": {key: destination.get(key) for key in (
            "financial_account_id", "destination_kind", "entity_type", "entity_id", "sub_account",
            "display_name", "currency", "verification", "source_namespace", "source_record_id",
            "source_revision", "evidence_reference", "transaction_reference")}
            if destination else None}


def cursor_for(row, binding):
    payload = {"v": 1, "binding": binding, "at": row["approval_at"], "id": row["_id"]}
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


async def cursor_anchor(db, owner, query, cursor, binding):
    try:
        if not isinstance(cursor, str) or not 1 <= len(cursor) <= 1500 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError()
        data = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if (not isinstance(data, dict) or set(data) != {"v", "binding", "at", "id"}
                or type(data["v"]) is not int or data["v"] != 1 or data["binding"] != binding
                or not isinstance(data["at"], str) or not isinstance(data["id"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", data["id"])):
            raise ValueError()
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError):
        fail(CURSOR_ERROR, 422)
    row = await db[EVENTS].find_one({**query, "_id": data["id"], "approval_at": data["at"]})
    if not row:
        fail(CURSOR_ERROR, 422)
    validate_event(row, owner)
    return data


async def coverage(db, owner):
    # Current operational rows only identify gaps. They never supply financial
    # history, amounts or decisions to the result list.
    rows = await db[REVIEWS].find({"user_id": owner, "status": {"$in": ["pending", "approved", "rejected"]}},
        {"id": 1, "revision": 1, "financial_event_id": 1, "financial_txn_group_id": 1,
         "status": 1}).limit(MAX_COVERAGE + 1).to_list(MAX_COVERAGE + 1)
    if len(rows) > MAX_COVERAGE:
        fail("driver_payment_history_coverage_scope_too_large")
    expected = []
    for row in rows:
        revision = row.get("revision", 1)
        if type(revision) is not int or not 1 <= revision <= MAX_COVERAGE:
            fail("driver_payment_history_coverage_revision_invalid")
        # Resubmission is permitted only after rejection. The counter records
        # how many earlier decisions must exist; it cannot supply those decisions.
        last = revision if row["status"] != "pending" else revision - 1
        expected.append([(number, digest([owner, "driver-review", row.get("id"), number]))
                         for number in range(1, last + 1)])
        if sum(len(revisions) for revisions in expected) > MAX_COVERAGE:
            fail("driver_payment_history_coverage_scope_too_large")
    keys = [key for revisions in expected for _, key in revisions]
    events = await db[EVENTS].find({"user_id": owner, "kind": "driver_payment_review", "_id": {"$in": keys}}).to_list(MAX_COVERAGE)
    by_id = {row["_id"]: row for row in events}
    missing, missing_revisions = set(), []
    for row, revisions in zip(rows, expected):
        review_id = str(row.get("id") or row["_id"])
        for number, key in revisions:
            event = by_id.get(key)
            if event:
                validate_event(event, owner)
            current = number == row.get("revision", 1) and row["status"] != "pending"
            if (not event or (not current and event["decision"] != "rejected")
                    or (current and (row.get("financial_event_id") != key
                        or row["status"] != event["decision"]
                        or row.get("financial_txn_group_id") != event["txn_group_id"]))):
                missing.add(review_id)
                missing_revisions.append({"review_id": review_id, "revision": number})
    return {"native_only": True, "unlinked_current_decisions": len(missing),
            "unlinked_current_review_ids": sorted(missing), "missing_native_revisions": missing_revisions}


async def read_driver_payment_history(db, owner, actor_id, *, limit=50, cursor=None,
        driver_id=None, payment_method=None, decision=None):
    await _actor(db, owner, actor_id, "accounting.shipping.view")
    if (type(limit) is not int or not 1 <= limit <= 250
            or payment_method not in {None, "bank_transfer", "card_terminal"}
            or decision not in {None, "approved", "rejected"}
            or (driver_id is not None and (not isinstance(driver_id, str) or not 1 <= len(driver_id) <= 160))):
        fail("driver_payment_history_filter_invalid", 422)
    query = {"user_id": owner, "kind": "driver_payment_review"}
    for key, value in (("party_id", driver_id), ("payment_method", payment_method), ("decision", decision)):
        if value is not None:
            query[key] = value
    binding = digest(query)
    if cursor is not None:
        anchor = await cursor_anchor(db, owner, query, cursor, binding)
        query["$or"] = [{"approval_at": {"$lt": anchor["at"]}},
                        {"approval_at": anchor["at"], "_id": {"$lt": anchor["id"]}}]
    rows = await db[EVENTS].find(query).sort([("approval_at", -1), ("_id", -1)]).limit(limit + 1).to_list(limit + 1)
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [await present(db, owner, row) for row in page]
    return {"schema": "mz2.driver.review_history.v1", "ledger_source": "accounting_v2",
        "scope": "native_v2_decisions_only", "read_only": True, "items": items,
        "has_more": has_more, "next_cursor": cursor_for(page[-1], binding) if has_more else None,
        "coverage": await coverage(db, owner)}
