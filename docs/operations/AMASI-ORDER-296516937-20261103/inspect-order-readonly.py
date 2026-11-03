#!/usr/bin/env python3
"""Read-only, PII-free assembly/label eligibility diagnostic. Never changes orders."""
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/app")
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv
from pymongo import MongoClient
from assembly_status_policy import canonical_status

parser = argparse.ArgumentParser()
parser.add_argument("--order", default="296516937")
args = parser.parse_args()
if not re.fullmatch(r"[0-9]{6,14}", args.order):
    raise SystemExit("STOP: invalid order reference")
if not (ROOT / "backend" / ".env").is_file():
    raise SystemExit("STOP: backend runtime env not found")
load_dotenv(ROOT / "backend" / ".env", override=False)
uri, db_name = os.getenv("MONGO_URL"), os.getenv("DB_NAME")
if not uri or not db_name:
    raise SystemExit("STOP: runtime database configuration unavailable")

client = MongoClient(uri, serverSelectionTimeoutMS=8000, socketTimeoutMS=8000,
                     connectTimeoutMS=8000, retryWrites=False)
try:
    db = client[db_name]
    source_projection = {
        "_id": 0, "user_id": 1, "order_number": 1,
        "order_status": 1, "order_status_slug": 1,
        "raw_by_source.salla_direct.status": 1,
        "raw_by_source.salla_direct.status_slug": 1,
        "g47_salla_snapshot": 1,
    }
    sources = list(db.unified_orders.find({"order_number": args.order}, source_projection).limit(3))
    if len(sources) != 1:
        raise SystemExit("STOP: order is missing or tenant identity is ambiguous")
    source = sources[0]
    owner = source.get("user_id")
    if not owner:
        raise SystemExit("STOP: owner missing")
    wf_projection = {
        "_id": 0, "stage": 1, "assembly_status": 1,
        "assembly_ready_piece_count": 1, "assembly_total_piece_count": 1,
        "carrier_label_ready": 1, "carrier_label_status": 1,
        "carrier_label_type": 1, "carrier_label_error_code": 1,
        "carrier_label_print_confirmed": 1, "shipping_status_epoch": 1,
        "salla_order_status": 1, "salla_order_status_verified_at": 1,
        "assembly_delivery.version": 1, "assembly_delivery.state": 1,
        "assembly_delivery.error_code": 1, "assembly_delivery.order_confirmed": 1,
        "assembly_delivery.status_attempted": 1, "assembly_delivery.awb_attempted": 1,
    }
    wf = db.order_review_workflows.find_one(
        {"order_number": args.order, "user_id": owner}, wf_projection) or {}
    pieces = list(db.mezan_preparation_pieces_v1.find({
        "user_id": owner, "order_number": args.order,
        "status": {"$ne": "cancelled"},
        "$or": [{"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None}],
    }, {"_id": 0, "assembly_status": 1, "active_hold_id": 1,
        "status": 1, "virtual_kind": 1}).limit(10001))
    snap = source.get("g47_salla_snapshot") or {}
    outbox = wf.get("assembly_delivery") or {}
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    result = {
        "order_reference": args.order,
        "database_reads_only": True,
        "source": {
            "canonical_status": canonical_status(source),
            "projected_status": source.get("order_status"),
            "projected_slug": source.get("order_status_slug"),
            "provider_status": (raw.get("status") or {}).get("name") if isinstance(raw.get("status"), dict) else raw.get("status"),
            "provider_slug": raw.get("status_slug"),
            "component_pending": bool(snap.get("component_pending")),
            "requires_authoritative_refresh": bool(snap.get("requires_authoritative_refresh")),
            "source_cancelled": bool(snap.get("cancelled")),
        },
        "workflow": {k: wf.get(k) for k in (
            "stage", "assembly_status", "assembly_ready_piece_count",
            "assembly_total_piece_count", "carrier_label_ready", "carrier_label_status",
            "carrier_label_type", "carrier_label_error_code", "carrier_label_print_confirmed",
            "shipping_status_epoch", "salla_order_status",
            "salla_order_status_verified_at")},
        "completion": {k: outbox.get(k) for k in (
            "version", "state", "error_code", "order_confirmed",
            "status_attempted", "awb_attempted")},
        "physical_pieces": {
            "count": len(pieces),
            "assembly_status_counts": dict(Counter(str(p.get("assembly_status") or "") for p in pieces)),
            "active_hold_count": sum(bool(p.get("active_hold_id")) for p in pieces),
            "virtual_kind_counts": dict(Counter(str(p.get("virtual_kind") or "ordinary") for p in pieces)),
            "may_exclude_derived_workflow_pieces": True,
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
finally:
    client.close()
