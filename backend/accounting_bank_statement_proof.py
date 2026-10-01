"""Verify native bank intake against preserved original bytes, never typed claims."""
import hashlib
from datetime import date, datetime
from zoneinfo import ZoneInfo
from decimal import Decimal

from fastapi import HTTPException
from accounting_atomic import SessionDatabase
from accounting_advertising_contract import digest


def invalid():
    raise HTTPException(409, detail={"code": "native_bank_statement_evidence_required"})


async def verified_bank_movement(db, owner, *, movement_id, bank_id, direction, amount):
    if not isinstance(db, SessionDatabase) or db._owner != owner or not db._session.in_transaction:
        invalid()
    rows = await db.mz2_daily_movements.find({"user_id": owner, "id": movement_id}).to_list(2)
    if len(rows) != 1:
        invalid()
    row = rows[0]
    if (row.get("status") != "unclassified" or row.get("direction") != direction
            or row.get("bank_account_id") != bank_id or row.get("currency") != "SAR"
            or row.get("source") not in {"bank_statement_import", "manual_reconciled_bank_statement"}
            or any(row.get(k) for k in ("receipt_id", "accounting_event_id", "confirmed_provider", "explicit_provider", "suggested_provider"))):
        invalid()
    source = await db.mz2_daily_movement_files.find_one({"user_id": owner, "id": row.get("file_id")})
    blob = await db.accounting_source_files.find_one({"user_id": owner, "file_id": row.get("file_id")})
    if not source or not blob or source.get("bank_account_id") != bank_id or source.get("source") != "mz2_bank_statement":
        invalid()
    content = bytes(blob.get("content") or b"")
    sha = hashlib.sha256(content).hexdigest()
    if not content or any(sha != value for value in (source.get("file_hash"), blob.get("sha256"), row.get("file_hash"))):
        invalid()
    from accounting_daily_movements import parse_daily_movement_xlsx
    try:
        parsed = parse_daily_movement_xlsx(content)
        original = [r for r in parsed["rows"] if r["row_no"] == row.get("row_no")]
        if len(original) != 1:
            invalid()
        fact = original[0]
        identity = digest([owner, bank_id, "reference", fact["reference"]]) if fact.get("reference") else digest([owner, row["file_id"], fact["row_no"], fact["row_hash"]])
        if (Decimal(row["amount"]) != Decimal(str(amount)) or Decimal(fact["amount"]) != Decimal(str(amount))
                or fact["direction"] != direction or fact["date"] != row.get("movement_date")
                or fact.get("reference") != row.get("reference")
                or fact.get("explicit_provider") or fact.get("suggested_provider")
                or row["_id"] != identity
                or date.fromisoformat(fact["date"]) > datetime.now(ZoneInfo("Asia/Riyadh")).date()):
            invalid()
    except (ValueError, KeyError, ArithmeticError):
        invalid()
    return row, {"source_namespace": "mz2_daily_movements", "source_record_id": row["id"],
                 "source_revision": digest([owner, bank_id, sha, fact]), "file_hash": sha,
                 "row_no": fact["row_no"], "movement_date": fact["date"]}
