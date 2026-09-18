"""Explicit Preview-only recognition of a matched provider sale.
Uses the existing BNPL ledger bridge, never opening balances.
The provider-issued sale row proves capture; the original order supplies the
principal. Mismatches, ambiguous identities, refunds and historical rows fail
closed. No provider API is called and no existing financial document is changed.
"""
from __future__ import annotations
import argparse
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from urllib.parse import urlsplit
import uuid

HOST = "agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3"
BASE = "bcd7920c7831a9ccccffb6119d2455f1b2ac3a20"
ORIGIN = "https://salla-analytics.preview.emergentagent.com"
RUNTIME = Path("/opt/mezan-preview-runtime-20260916")
METHODS = {"emkan": {"emkaninstallment", "emkan", "imkan", "إمكان", "امكان"},
           "tamara": {"tamara", "تمارا"}}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def money(value):
    try:
        n = Decimal(str(value))
        require(n.is_finite() and n >= 0, "Invalid principal")
        require(n == n.quantize(Decimal("0.01")), "Sub-cent principal")
        return n
    except (InvalidOperation, TypeError):
        raise ValueError("Invalid principal") from None

def normalize_evidence(owner, provider, entry, order, statement, cutoff):
    require(provider in METHODS, "Provider identity adapter unavailable")
    require(all(x.get("user_id") == owner for x in (entry, order, statement)), "Owner mismatch")
    require(entry.get("provider") == provider and statement.get("provider") == provider, "Provider mismatch")
    require(entry.get("file_id") == statement.get("id") and statement.get("file_hash"), "File identity missing")
    require(entry.get("matched") is True and entry.get("event_type") == "sale", "Confirmed sale evidence required")
    require(str(entry.get("order_number")) == str(order.get("order_number")), "Order mismatch")
    require(str(order.get("payment_method") or "").strip().lower() in METHODS[provider], "Original payment method mismatch")
    require(order.get("order_status") in {"تم التنفيذ", "تم التوصيل", "completed", "delivered"}, "Unfulfilled original order")
    raw = (order.get("raw_by_source") or {}).get("excel") or {}
    require(order.get("currency") == statement.get("currency") == raw.get("original_currency", raw.get("currency")) == "SAR", "SAR evidence required")
    principal = money(raw.get("original_total_amount", raw.get("total_amount")))
    require(principal > 0 and principal == money(order.get("total_amount")) == money(entry.get("actual_gross_amount")), "Original/statement principal mismatch")
    provider_id = entry.get("provider_order_id") if provider == "emkan" else entry.get("tamara_order_id")
    require(bool(provider_id) and str(uuid.UUID(str(provider_id))) == str(provider_id).lower(), "Canonical provider UUID required")
    order_day = date.fromisoformat(str(order.get("order_date")))
    statement_day = date.fromisoformat(str(entry.get("settlement_date")))
    require(order_day <= statement_day <= datetime.now(timezone.utc).date(), "Invalid evidence dates")
    require(bool(cutoff) and order_day.isoformat() >= cutoff[:10], "Post-cutover original required")
    for k in ("actual_refund_amount", "actual_partial_refund_amount", "actual_canceled_amount"):
        require(money(entry.get(k) or 0) == 0, "Sale carries refund/cancellation")
    evidence = {"source": "p01_original_order_and_provider_statement",
                "order_number": str(order["order_number"]), "order_id": order.get("order_id"),
                "source_file_id": statement["id"], "source_file_hash": statement["file_hash"],
                "source_entry_id": entry["id"], "statement_reference": entry.get("settlement_reference"),
                "original_principal": str(principal), "provider_id": provider_id}
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, owner + ":" + provider + ":" + provider_id)),
            "user_id": owner, "provider": provider, "provider_id": provider_id,
            "order_number": str(order["order_number"]), "order_reference_id": str(order["order_number"]),
            "amount": float(principal), "captured_amount": float(principal),
            "refunded_amount": 0.0, "currency": "SAR", "status": "captured",
            "status_source": "provider_statement_sale_evidence",
            "created_at_provider": order_day.isoformat(),
            "created_at_provider_source": "original_order_date",
            "effective_settlement_date": statement_day.isoformat(),
            "evidence": evidence, "evidence_sha256": digest}

def boundary():
    require(socket.gethostname() == HOST, "Preview host required")
    meta = json.loads((RUNTIME / "frontend/build/preview-meta.json").read_text())
    require(meta["environment"] == "preview" and meta["api_origin"] == ORIGIN, "Preview origin required")
    require(meta.get("external_network") == "denied" and meta.get("automatic_workers") == "disabled", "Isolated Preview required")
    status = json.loads(subprocess.check_output(["python", "-B", "/app/scripts/production_release_guard.py", "status"], text=True))
    require(status.get("active") is False, "Active release lease")
    require(subprocess.check_output(["git", "-C", "/app", "rev-parse", "HEAD"], text=True).strip() == BASE, "Base changed")
    require(not subprocess.check_output(["git", "-C", "/app", "status", "--short"], text=True).strip(), "Shared source dirty")

def verify_group(rows, txn):
    require(len(rows) == 2, "Incomplete or duplicated prior journal: manual review required")
    require(len({r["txn_group_id"] for r in rows}) == 1, "Multiple prior journals")
    expected = {("payment_gateway", txn["provider"], "receivable", "debit"),
                ("revenue", "bnpl_sales", None, "credit")}
    actual = {(r["entity_type"], r["entity_id"], r.get("sub_account"), r["side"]) for r in rows}
    require(actual == expected, "Prior journal accounts differ")
    require(all(r["status"] == "posted" and r["entry_type"] == "bnpl_sale"
                and money(r["amount"]) == money(txn["amount"]) for r in rows), "Prior journal state/amount differs")
    return rows[0]["txn_group_id"]

async def run(args):
    boundary()
    from dotenv import dotenv_values, load_dotenv
    values = dotenv_values("/app/backend/.env")
    require(urlsplit(values["MONGO_URL"]).hostname in {"localhost", "127.0.0.1", "::1"}, "Local database required")
    load_dotenv("/app/backend/.env")
    from motor.motor_asyncio import AsyncIOMotorClient
    from bnpl.ledger_bridge import post_bnpl_sale_to_ledger, _before_cutoff
    from ledger_core import compute_balance
    client = AsyncIOMotorClient(values["MONGO_URL"])
    db = client[values["DB_NAME"]]
    try:
        statement = await db.settlement_files.find_one({"id": args.file_id, "user_id": args.owner})
        require(statement is not None, "Owned statement missing")
        entries = await db.settlement_entries.find({"file_id": args.file_id, "user_id": args.owner}).to_list(2000)
        # Bounded pilot: no unrelated sales or refund history is imported.
        require(len(entries) == 1, "Single-sale pilot requires exactly one source event")
        entry = entries[0]
        orders = await db.unified_orders.find({"user_id": args.owner, "order_number": entry["order_number"]}).to_list(2)
        require(len(orders) == 1, "Original order not unique")
        txn = normalize_evidence(args.owner, args.provider, entry, orders[0], statement, values.get("BNPL_BRIDGE_CUTOFF_ISO"))
        require(not _before_cutoff(txn["created_at_provider"]), "Existing bridge cutoff rejects original")
        indexes = await db.payment_transactions.index_information()
        require(any(i.get("unique") and i["key"] == [("user_id", 1), ("provider", 1), ("provider_id", 1)] for i in indexes.values()), "Canonical transaction uniqueness missing")
        key = "bnpl_sale:" + args.provider + ":" + txn["provider_id"]
        prior = await db.general_ledger.find({"user_id": args.owner, "metadata.idempotency_key": key}).to_list(10)
        aliases = await db.general_ledger.find({"user_id": args.owner, "entry_type": "bnpl_sale",
                  "metadata.order_reference_id": txn["order_reference_id"],
                  "metadata.idempotency_key": {"$ne": key}}).to_list(10)
        require(not aliases, "Order already recognized under another identity")
        query = {"user_id": args.owner, "provider": args.provider, "provider_id": txn["provider_id"]}
        old = await db.payment_transactions.find_one(query)
        if old:
            require(old.get("evidence_sha256") == txn["evidence_sha256"], "Existing payment requires separate provenance review")
        before = await compute_balance(db, user_id=args.owner, entity_type="payment_gateway",
                                       entity_id=args.provider, sub_account="receivable")
        result = {"mode": "apply" if args.apply else "dry_run", "provider": args.provider,
                  "transaction_id": txn["id"], "evidence": txn["evidence"], "balance_before": before}
        if prior:
            result.update(skipped=True, txn_group_id=verify_group(prior, txn))
        elif args.apply:
            boundary()
            now = datetime.now(timezone.utc).isoformat()
            await db.payment_transactions.update_one(query, {"$setOnInsert": dict(txn, created_at=now, source="preview_p01_evidence")}, upsert=True)
            persisted = await db.payment_transactions.find_one(query, {"_id": 0})
            require(persisted.get("evidence_sha256") == txn["evidence_sha256"], "Concurrent payment conflict")
            posted = await post_bnpl_sale_to_ledger(db, user_id=args.owner, txn=persisted)
            rows = await db.general_ledger.find({"user_id": args.owner, "metadata.idempotency_key": key}).to_list(10)
            result.update(posted=posted, txn_group_id=verify_group(rows, txn),
                          ledger_entry_ids=[r["id"] for r in rows])
        else:
            result["eligible"] = True
        result["balance_after"] = await compute_balance(db, user_id=args.owner, entity_type="payment_gateway",
                                                       entity_id=args.provider, sub_account="receivable")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--owner", required=True)
    p.add_argument("--provider", required=True, choices=sorted(METHODS))
    p.add_argument("--file-id", required=True)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    sys.path.insert(0, "/app/backend")
    with open(RUNTIME / "p01-recognition.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args))
