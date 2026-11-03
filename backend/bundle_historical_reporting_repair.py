"""Explicit owner-attested missing-bundle reporting correction.

No order resync, catalog repricing, operational status transition, supplier,
stock, purchase invoice, journal or Qoyod write is reachable from this module.
The only mutation capability is the existing reporting-correction collection
plus its shared cost-cache revision, in one Mongo transaction.

Salla raw-by-source can be refreshed; an owner attestation is *not* represented
as an immutable first-ingestion or provider invoice. Reports call it
owner_attested_expected, and no cost is inferred for other sales.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import os
import re
from typing import Any

from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from bundle_historical_reporting_audit import (
    BundleHistoricalEvidenceUnavailable, _hash, dry_run_attested_bundle_cost,
    inspect_historical_bundle_order,
)
from order_cost_authority import (
    CORRECTIONS, LIFECYCLES, SNAPSHOTS, VERSION as COST_VERSION,
    _unknown, decimal, line_aliases, line_fingerprint, line_id,
)
from order_cost_reporting_repair import (
    _RepairDB, _correction_line_identity, _immutable_correction,
    source_authority_evidence_hash,
)
from product_cost_revision import bump_product_cost_revision
from stock_component_consumption_service import PLANS

REPAIR_VERSION = "missing-bundle-owner-reporting-v1"
APPLY_FLAG = "MZ2_HISTORICAL_BUNDLE_REPORTING_APPLY_ENABLED"
APPROVAL_PHRASE = "APPROVE_EXACT_REPORTING_ONLY_BUNDLE_CORRECTION"


def _fail(code: str) -> None:
    raise BundleHistoricalEvidenceUnavailable(code)


def _owner_id(value: Any) -> str:
    if not isinstance(value, str) or not 0 < len(value.strip()) <= 256 or "\x00" in value:
        _fail("historical_repair_owner_invalid")
    return value.strip()


async def _bounded(db: Any, name: str, query: dict, projection: dict,
                   *, max_rows: int = 2) -> list[dict]:
    # All source reads are tenant-scoped. The _RepairDB wrapper additionally
    # rejects any collection query lacking the exact owner user_id.
    result = await db[name].find(query, projection).limit(max_rows + 1).to_list(max_rows + 1)
    if len(result) > max_rows:
        _fail("historical_repair_duplicate_source")
    return result


def _order_view(source: dict) -> tuple[dict, list[dict]]:
    """Rehydrate the exact owner-scoped saved provider order, not a live GET."""
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row
    try:
        discovered = MongoOrderRepository._to_discovery_row(source)
        if discovered is None:
            _fail("historical_repair_source_unavailable")
        order = _map_row(discovered.salla_raw, current_status=discovered.current_status)
        return order.model_dump(mode="python"), [
            item.model_dump(mode="python") for item in order.items
        ]
    except BundleHistoricalEvidenceUnavailable:
        raise
    except (ValueError, TypeError, KeyError):
        _fail("historical_repair_order_mapping_invalid")


async def _read_evidence(db: Any, owner: str, number: str, item_id: str) -> tuple[dict, dict, dict]:
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{5,20}", number):
        _fail("historical_repair_order_number_invalid")
    if not isinstance(item_id, str) or not 0 < len(item_id) <= 256 or "\x00" in item_id:
        _fail("historical_repair_line_id_required")

    orders = await _bounded(
        db, "unified_orders",
        {"user_id": owner, "order_number": number,
         "raw_by_source.salla_direct": {"$type": "object"}},
        {"_id": 0}, max_rows=1,
    )
    if len(orders) != 1:
        _fail("historical_repair_order_not_found")
    source = orders[0]
    order, items = _order_view(source)
    targets = [item for item in items if line_id(item) == item_id]
    if len(targets) != 1:
        _fail("historical_repair_line_not_unique")
    item = targets[0]
    ids = {str(value) for value in (item.get("parent_product_id"),
                                   item.get("product_id")) if value not in (None, "")}
    matches = await _bounded(
        db, "mezan_bundle_definitions_v2",
        {"user_id": owner, "product_type": "composite",
         "product_id": {"$in": sorted(ids)}},
        {"_id": 0, "product_id": 1}, max_rows=1,
    ) if ids else []
    if len(matches) != 1:
        _fail("historical_repair_bundle_definition_unverified")
    product_id = str(matches[0]["product_id"])

    raw = (source.get("raw_by_source") or {}).get("salla_direct")
    evidence = inspect_historical_bundle_order(
        order_number=number, raw_order=raw, canonical_items=items,
        target_line_id=item_id,
    )
    if evidence.get("line_fingerprint") != line_fingerprint(item):
        _fail("historical_repair_source_fingerprint_changed")

    aliases = sorted(line_aliases(item))
    if not aliases:
        _fail("historical_repair_line_identity_missing")
    stored = await _bounded(db, SNAPSHOTS,
        {"user_id": owner, "order_number": number,
         "order_item_id": {"$in": aliases}},
        {"_id": 1}, max_rows=1)
    if stored:
        _fail("historical_repair_existing_cost_snapshot")
    lifecycle = await _bounded(db, LIFECYCLES,
        {"user_id": owner, "order_number": number},
        {"_id": 0, "bundle_intake_snapshots": 1,
         "reporting_cost_snapshots": 1}, max_rows=1)
    if lifecycle:
        for field in ("bundle_intake_snapshots", "reporting_cost_snapshots"):
            saved = lifecycle[0].get(field) or {}
            if not isinstance(saved, dict) or set(saved).intersection(aliases):
                _fail("historical_repair_existing_lifecycle_cost")
    # An existing supplier execution plan is not price proof. Preserve the
    # legacy plan boundary by refusing to write a parallel financial record.
    plans = await _bounded(db, PLANS,
        {"user_id": owner, "order_id": number},
        {"_id": 1}, max_rows=1)
    if plans:
        _fail("historical_repair_existing_operational_plan")
    return order, item, {**evidence, "product_id": product_id, "item_id": item_id}


def _correction_record(owner: str, order: dict, item: dict,
                       evidence: dict, proposal: dict) -> dict:
    quantity = decimal(proposal["quantity"])
    base = Decimal(proposal["base_unit_cost_sar"]) * quantity
    component = Decimal(proposal["component_unit_cost_sar"]) * quantity
    service = Decimal(proposal["external_service_unit_cost_sar"]) * quantity
    total = base + component + service
    if total != Decimal(proposal["line_total_sar"]):
        _fail("historical_repair_arithmetic_mismatch")
    # Both cards and executive profit consume one frozen report authority.
    # The existing effective_product_cost scales refunds/cancellations once.
    cost = _unknown(quantity, "")
    cost.update({
        "base_cost_source": "historical_owner_attested_bundle",
        "base_complete": True,
        "calculation_cost_available": True,
        "calculation_cost_source": "historical_owner_attested_bundle",
        "mezan_cost_complete": True,
        "mezan_cost_missing": False,
        "uses_salla_fallback": False,
        "cost_semantics_version": COST_VERSION,
        "currency": "SAR",
        "cost_currency": "SAR",
        "base_total": float(base),
        "product_components_total": float(component),
        "selected_options_total": float(service),
        "components_total": float(component + service),
        "line_total": float(total),
        "cost_stage": "expected",
        "cost_authority": "historical_owner_attested_bundle",
        "historical_cost_verified": True,
        "owner_attested_not_provider_invoice": True,
        "missing_cost_reasons": [],
    })
    number = str(order["order_number"])
    key = line_id(item)
    source_hash = source_authority_evidence_hash(None)
    proof = {
        "provenance_version": REPAIR_VERSION,
        "source_choice_evidence_hash": proposal["source_choice_evidence_hash"],
        "attestation_hash": proposal["attestation_hash"],
        "source_created_at": evidence["source_created_at"],
        "immutable_first_ingestion_proven": False,
        "owner_sale_time_attested": True,
        "provider_invoice_verified": False,
        "proposal_manifest_sha": proposal["proposal_manifest_sha"],
    }
    identity = "cost-correction:" + _hash([
        owner, number, _correction_line_identity(item)])
    entry = {
        "_id": identity, "user_id": owner,
        "order_number": number, "order_item_id": key,
        "product_id": str(evidence["product_id"]),
        "quantity": float(quantity), "line_fingerprint": line_fingerprint(item),
        "cost": cost, "repair_version": REPAIR_VERSION,
        "source_authority_evidence_hash": source_hash,
        "source_cost_stage": "unknown",
        "evidence_hash": _hash({"source": source_hash, "proof": proof,
                                "line_fingerprint": line_fingerprint(item),
                                "repair_version": REPAIR_VERSION}),
        "base_provenance": proof,
        "before_after": {"before_frozen_authority": None,
                         "after_reporting_correction": float(total),
                         "difference_from_known_cost": None},
        "state": "applied",
    }
    entry["correction_fingerprint"] = _hash(_immutable_correction(entry))
    return entry


async def build_missing_bundle_repair_plan(db: Any, user_id: str, order_number: str,
                                           order_item_id: str, attestation: dict) -> dict:
    """Read-only explicit plan. No implicit order discovery or auto-backfill."""
    owner = _owner_id(user_id)
    scoped = db if isinstance(db, _RepairDB) else _RepairDB(db, owner)
    order, item, evidence = await _read_evidence(scoped, owner, order_number, order_item_id)
    proposal = dry_run_attested_bundle_cost(evidence, attestation)
    if proposal["line_fingerprint"] != line_fingerprint(item):
        _fail("historical_repair_line_changed")
    correction = _correction_record(owner, order, item, evidence, proposal)
    previous = await _bounded(scoped, CORRECTIONS,
        {"user_id": owner, "order_number": order_number,
         "order_item_id": {"$in": sorted(line_aliases(item))}},
        {"_id": 0, "state": 1, "cost": 1, "repair_version": 1, "line_fingerprint": 1,
         "quantity": 1, "product_id": 1, "source_authority_evidence_hash": 1,
         "source_cost_stage": 1, "evidence_hash": 1, "base_provenance": 1,
         "order_number": 1, "order_item_id": 1, "user_id": 1}, max_rows=1)
    state = "pending"
    if previous:
        if (previous[0].get("state") != "applied" or
            previous[0].get("repair_version") != REPAIR_VERSION or
            _immutable_correction({**previous[0], "_id": correction["_id"]}) !=
            _immutable_correction(correction)):
            _fail("historical_repair_existing_correction_conflict")
        state = "already_applied"
    # Only an attested expected cost is known. The old missing amount is NULL,
    # not zero; no invented net-profit delta or false completeness assertion.
    manifest = {
        "repair_version": REPAIR_VERSION,
        "owner": owner,
        "order_number": order_number,
        "order_item_id": order_item_id,
        "line_fingerprint": line_fingerprint(item),
        "source_choice_evidence_hash": evidence["source_choice_evidence_hash"],
        "attestation_hash": proposal["attestation_hash"],
        "proposal_manifest_sha": proposal["proposal_manifest_sha"],
        "correction_fingerprint": correction["correction_fingerprint"],
    }
    return {
        "mode": "dry_run",
        "manifest_sha": _hash(manifest),
        "ready": True,
        "application_state": state,
        "order_number": order_number,
        "order_item_id": order_item_id,
        "line_fingerprint": line_fingerprint(item),
        "source_choice_evidence_hash": evidence["source_choice_evidence_hash"],
        "source_created_at": evidence["source_created_at"],
        "cost_provenance": "owner_attested_expected_not_provider_invoice",
        "original_snapshot_complete": False,
        "before_line_cost_sar": None,
        "after_line_cost_sar": correction["cost"]["line_total"],
        "base_total_sar": correction["cost"]["base_total"],
        "components_total_sar": correction["cost"]["components_total"],
        "currency": "SAR",
        "no_other_orders_modified": True,
        "writes_allowed_after_approval": ["mezan_order_cost_corrections_v1",
                                           "mezan_product_cost_revisions_v2"],
        "correction": correction,  # Private server-side; route strips it.
    }


async def apply_missing_bundle_repair(db: Any, user_id: str,
                                      order_number: str, order_item_id: str,
                                      attestation: dict, *,
                                      expected_manifest_sha: str,
                                      approval_phrase: str) -> dict:
    """Disabled by default. Apply only an exactly reviewed, unchanged plan."""
    if os.environ.get(APPLY_FLAG, "").strip() != "1":
        _fail("historical_repair_apply_disabled")
    if approval_phrase != APPROVAL_PHRASE:
        _fail("historical_repair_explicit_approval_required")
    if not isinstance(expected_manifest_sha, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_manifest_sha):
        _fail("historical_repair_manifest_required")
    owner = _owner_id(user_id)
    if not hasattr(db, "client") or not hasattr(db.client, "start_session"):
        _fail("historical_repair_transaction_required")
    for attempt in range(2):
        try:
            async with await db.client.start_session() as session:
                async def transaction(active_session):
                    scoped = _RepairDB(db, owner, session=active_session,
                                       writable=True)
                    plan = await build_missing_bundle_repair_plan(
                        scoped, owner, order_number, order_item_id, attestation)
                    if plan["manifest_sha"] != expected_manifest_sha:
                        _fail("historical_repair_manifest_changed")
                    if not plan["ready"]:
                        _fail("historical_repair_not_ready")
                    changed = plan["application_state"] == "pending"
                    if changed:
                        record = deepcopy(plan["correction"])
                        record["manifest_sha"] = plan["manifest_sha"]
                        record["applied_at"] = datetime.now(timezone.utc)
                        await scoped[CORRECTIONS].insert_one(record)
                        await bump_product_cost_revision(scoped, owner)
                    return {
                        "mode": "applied" if changed else "no_op",
                        "manifest_sha": plan["manifest_sha"],
                        "order_number": plan["order_number"],
                        "order_item_id": plan["order_item_id"],
                        "after_line_cost_sar": plan["after_line_cost_sar"],
                        "changed_reporting_corrections": int(changed),
                        "cost_revision_bumped": changed,
                        "accounting_or_operational_writes": False,
                    }
                return await session.with_transaction(
                    transaction, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY)
        except DuplicateKeyError:
            if attempt == 1:
                _fail("historical_repair_concurrent_correction_conflict")
    _fail("historical_repair_concurrent_correction_conflict")
