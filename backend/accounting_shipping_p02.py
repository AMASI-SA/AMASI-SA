"""Isolated MZ2 P02 shipping/COD accounting for UAT.

This module deliberately does not call the historical store-delivery accounting
bridge.  It proves the replacement contract first:

* shipping fees never create sales/revenue/VAT legs;
* Salla's customer-facing shipping charge is review evidence only, never the
  carrier-cost basis;
* store-driver cash COD recognition requires the operational cash-collection
  row and the matching MZ2 Salla COD order evidence;
* COD sale and driver fee are separate balanced groups inside one owner
  transaction;
* all writes remain behind the explicit P02 runtime gate.

The old operational delivery/settlement writers stay Production-locked and are
not enabled by this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import re
import uuid
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from accounting_atomic import atomic_owner
from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
    require_owner,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_sales_tax_service import read_policy, sale_snapshot
from accounting_sales_tax import TaxError
from ledger_core import post_txn_group
from store_delivery_accounting import require_p02_shipping_financial_writes
from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_EARNINGS
from store_delivery_driver_routes import STORE_DRIVERS


RIYADH = ZoneInfo("Asia/Riyadh")
MONEY = Decimal("0.01")
SOURCE = "accounting_shipping_p02"
RATE_TREATMENT = "gross_expense_no_input_vat"
MAX_POLICY_VERSIONS = 1000


class ShippingAccountingError(ValueError):
    pass


def _money(value: Any) -> Decimal:
    try:
        number = Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise ShippingAccountingError("shipping_amount_invalid") from None
    if not number.is_finite() or number < 0:
        raise ShippingAccountingError("shipping_amount_invalid")
    return number


def _positive(value: Any) -> Decimal:
    amount = _money(value)
    if amount <= 0:
        raise ShippingAccountingError("shipping_amount_must_be_positive")
    return amount


def _norm(value: Any) -> str:
    text = " ".join(str(value or "").strip().split()).lower()
    text = re.sub(r"[\u064B-\u0652]", "", text)
    return (
        text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        .replace("ى", "ي").replace("ة", "ه")
    )


def _hash(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _instant(value: Any, *, source_timezone: bool = False) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ShippingAccountingError("shipping_accounting_date_required")
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
    if parsed is None:
        raise ShippingAccountingError("shipping_accounting_date_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if not source_timezone:
            raise ShippingAccountingError("shipping_accounting_timezone_required")
        parsed = parsed.replace(tzinfo=RIYADH)
    return parsed.astimezone(timezone.utc)


def _event_date_from_salla(value: Any) -> str:
    event = _instant(value, source_timezone=True)
    if event > datetime.now(timezone.utc):
        raise ShippingAccountingError("shipping_event_in_future")
    return event.isoformat()


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_id"}


class ShippingRateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    courier_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(min_length=1, max_length=30)
    total_fee: Decimal
    effective_at: datetime
    evidence_ref: str = Field(min_length=3, max_length=500)
    revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)
    tax_treatment: Literal["gross_expense_no_input_vat"] = RATE_TREATMENT

    @field_validator("courier_id", "name", "evidence_ref", "reason")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("aliases")
    @classmethod
    def aliases_valid(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(str(value or "").strip().split()) for value in values]
        cleaned = [value for value in cleaned if value]
        normalized = {_norm(value) for value in cleaned}
        if not cleaned or len(normalized) != len(cleaned):
            raise ValueError("shipping_rate_aliases_invalid")
        return cleaned

    @field_validator("total_fee")
    @classmethod
    def fee_valid(cls, value: Decimal) -> Decimal:
        return _positive(value)


async def read_shipping_policy(db, owner: str) -> dict[str, Any]:
    return await db.mz2_shipping_rate_policies.find_one({"_id": owner}) or {
        "_id": owner,
        "user_id": owner,
        "revision": 0,
        "versions": [],
        "audit": [],
    }


async def save_shipping_rate(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    payload: ShippingRateInput,
) -> dict[str, Any]:
    require_accounting_permission(actor, "accounting.rules.manage")
    require_owner(actor)
    current = await read_shipping_policy(db, owner)
    if current["revision"] != payload.revision:
        raise HTTPException(409, "shipping_rate_policy_changed_refresh_required")
    if len(current["versions"]) >= MAX_POLICY_VERSIONS:
        raise HTTPException(409, "shipping_rate_policy_history_limit")

    effective = payload.effective_at
    if effective.tzinfo is None or effective.utcoffset() is None:
        raise HTTPException(400, "shipping_rate_timezone_required")
    effective_at = effective.astimezone(timezone.utc).isoformat()

    aliases_norm = sorted({_norm(value) for value in payload.aliases})
    # At one exact effective instant an alias cannot point at two couriers.
    for version in current["versions"]:
        if (
            version.get("effective_at") == effective_at
            and set(version.get("aliases_normalized") or []).intersection(aliases_norm)
            and version.get("courier_id") != payload.courier_id
        ):
            raise HTTPException(409, "shipping_rate_alias_conflict")

    now = datetime.now(timezone.utc).isoformat()
    version = {
        "id": str(uuid.uuid4()),
        "courier_id": payload.courier_id,
        "name": payload.name,
        "aliases": payload.aliases,
        "aliases_normalized": aliases_norm,
        "total_fee": format(_positive(payload.total_fee), ".2f"),
        "effective_at": effective_at,
        "evidence_ref": payload.evidence_ref,
        "tax_treatment": payload.tax_treatment,
        "revision": payload.revision + 1,
        "verification_status": "approved",
        "created_by": actor["id"],
        "created_at": now,
    }
    audit = {
        "id": str(uuid.uuid4()),
        "action": "shipping_rate_version",
        "actor_id": actor["id"],
        "at": now,
        "reason": payload.reason,
        "previous_revision": payload.revision,
        "version": version,
    }
    record = {
        **current,
        "revision": payload.revision + 1,
        "versions": [*current["versions"], version],
        "audit": [*current["audit"], audit],
    }
    if payload.revision == 0:
        try:
            await db.mz2_shipping_rate_policies.insert_one(record)
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "code", None) == 11000:
                raise HTTPException(
                    409, "shipping_rate_policy_changed_refresh_required"
                ) from None
            raise
    else:
        result = await db.mz2_shipping_rate_policies.replace_one(
            {"_id": owner, "revision": payload.revision},
            record,
        )
        if result.matched_count != 1:
            raise HTTPException(
                409, "shipping_rate_policy_changed_refresh_required"
            )
    return {key: value for key, value in record.items() if key != "_id"}


def select_courier_rate(
    policy: dict[str, Any],
    *,
    shipping_company: str,
    accounting_at: str,
) -> dict[str, Any]:
    when = _instant(accounting_at)
    alias = _norm(shipping_company)
    eligible = []
    for version in policy.get("versions") or []:
        if version.get("verification_status") != "approved":
            continue
        if alias not in set(version.get("aliases_normalized") or []):
            continue
        try:
            effective = _instant(version.get("effective_at"))
        except ShippingAccountingError:
            continue
        if effective <= when:
            eligible.append((effective, int(version.get("revision") or 0), version))
    if not eligible:
        raise ShippingAccountingError("shipping_rate_not_configured")
    eligible.sort(key=lambda item: (item[0], item[1]))
    latest_time, latest_revision, selected = eligible[-1]
    ties = [
        row for effective, revision, row in eligible
        if effective == latest_time and revision == latest_revision
    ]
    if len(ties) != 1:
        raise ShippingAccountingError("shipping_rate_ambiguous")
    if selected.get("tax_treatment") != RATE_TREATMENT:
        raise ShippingAccountingError("shipping_rate_tax_treatment_unsupported")
    return selected


async def _event_record(db, owner: str, event_id: str) -> dict[str, Any] | None:
    return await db.mz2_shipping_accounting_events.find_one(
        {"_id": event_id, "user_id": owner}
    )


async def _insert_event_posting(
    db,
    *,
    owner: str,
    event_id: str,
    kind: str,
    economic_hash: str,
    facts: dict[str, Any],
    actor_id: str,
) -> None:
    prior = await _event_record(db, owner, event_id)
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise ShippingAccountingError("shipping_event_source_conflict")
        if prior.get("status") == "posted":
            return
        raise ShippingAccountingError("shipping_event_requires_recovery")
    await db.mz2_shipping_accounting_events.insert_one({
        "_id": event_id,
        "id": event_id,
        "user_id": owner,
        "operation_id": OPERATION_ID,
        "source": SOURCE,
        "kind": kind,
        "economic_hash": economic_hash,
        "facts": facts,
        "status": "posting",
        "actor_id": actor_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


async def prepare_courier_fee(
    db,
    *,
    owner: str,
    evidence_id: str,
) -> dict[str, Any]:
    evidence = await db.mz2_salla_order_evidence.find_one(
        {"user_id": owner, "id": evidence_id},
        {"_id": 0},
    )
    if not evidence:
        raise ShippingAccountingError("order_evidence_missing")
    if evidence.get("conflict"):
        raise ShippingAccountingError("order_evidence_conflict")
    if not evidence.get("delivery_source_text"):
        raise ShippingAccountingError("courier_delivery_evidence_required")
    company = str(evidence.get("shipping_company") or "").strip()
    waybill = str(evidence.get("waybill") or "").strip()
    if not company or not waybill:
        raise ShippingAccountingError("courier_identity_or_waybill_missing")

    accounting_at = _event_date_from_salla(evidence["delivery_source_text"])
    policy = await read_shipping_policy(db, owner)
    rate = select_courier_rate(
        policy,
        shipping_company=company,
        accounting_at=accounting_at,
    )
    courier_id = rate["courier_id"]

    same_waybill = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "waybill": waybill,
        },
        {"_id": 0, "id": 1, "order_number": 1},
    ).limit(3).to_list(3)
    if len({row.get("id") for row in same_waybill}) != 1:
        raise ShippingAccountingError("courier_waybill_not_unique")

    prior_for_order = await db.mz2_shipping_accounting_events.find_one({
        "user_id": owner,
        "kind": "courier_fee",
        "facts.order_number": evidence["order_number"],
        "status": "posted",
        "facts.waybill": {"$ne": waybill},
    })
    if prior_for_order:
        raise ShippingAccountingError("order_has_prior_shipping_fee_event")

    event_id = _hash([owner, "courier_fee", courier_id, waybill])
    facts = {
        "event_id": event_id,
        "order_evidence_id": evidence["id"],
        "order_number": evidence["order_number"],
        "courier_id": courier_id,
        "courier_name": rate["name"],
        "shipping_company_source": company,
        "waybill": waybill,
        "accounting_at": accounting_at,
        "total_fee": rate["total_fee"],
        "rate_version_id": rate["id"],
        "rate_evidence_ref": rate["evidence_ref"],
        "tax_treatment": rate["tax_treatment"],
        "salla_shipping_charge_for_review": evidence.get("shipping_cost_source"),
    }
    economic_hash = _hash(facts)
    prior = await _event_record(db, owner, event_id)
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise ShippingAccountingError("shipping_event_source_conflict")
        if prior.get("status") == "posted":
            return {
                "state": "already_posted",
                "event_id": event_id,
                "facts": facts,
                "txn_group_id": prior.get("txn_group_id"),
            }
        raise ShippingAccountingError("shipping_event_requires_recovery")
    return {
        "state": "eligible",
        "event_id": event_id,
        "facts": facts,
        "economic_hash": economic_hash,
    }


async def post_courier_fee(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    evidence_id: str,
) -> dict[str, Any]:
    async def commit(scoped):
        proposal = await prepare_courier_fee(
            scoped, owner=owner, evidence_id=evidence_id
        )
        if proposal["state"] == "already_posted":
            return proposal
        facts = proposal["facts"]
        await require_p02_shipping_financial_writes(
            scoped,
            user_id=owner,
            event_at=facts["accounting_at"],
        )
        await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=[
                ("courier", facts["courier_id"], "payable"),
            ],
        )
        await _insert_event_posting(
            scoped,
            owner=owner,
            event_id=proposal["event_id"],
            kind="courier_fee",
            economic_hash=proposal["economic_hash"],
            facts=facts,
            actor_id=actor["id"],
        )
        result = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            txn_type="mz2_shipping_fee_accrual",
            notes=f"تكلفة شحن — {facts['courier_name']} — {facts['order_number']}",
            metadata={
                "operation_id": OPERATION_ID,
                "source": SOURCE,
                "shipping_event_id": proposal["event_id"],
                "shipping_event_kind": "courier_fee",
                "accounting_at": facts["accounting_at"],
                "order_reference_id": facts["order_number"],
                "courier_id": facts["courier_id"],
                "waybill": facts["waybill"],
                "rate_version_id": facts["rate_version_id"],
                "rate_evidence_ref": facts["rate_evidence_ref"],
                "tax_treatment": facts["tax_treatment"],
                "salla_shipping_charge_for_review": facts[
                    "salla_shipping_charge_for_review"
                ],
            },
            entries=[
                {
                    "entity_type": "expense",
                    "entity_id": "shipping",
                    "side": "debit",
                    "amount": facts["total_fee"],
                    "entry_type": "shipping_fee_accrual",
                },
                {
                    "entity_type": "courier",
                    "entity_id": facts["courier_id"],
                    "sub_account": "payable",
                    "side": "credit",
                    "amount": facts["total_fee"],
                    "entry_type": "shipping_fee_accrual",
                },
            ],
        )
        now = datetime.now(timezone.utc).isoformat()
        await scoped.mz2_shipping_accounting_events.update_one(
            {
                "_id": proposal["event_id"],
                "user_id": owner,
                "status": "posting",
            },
            {"$set": {
                "status": "posted",
                "txn_group_id": result["txn_group_id"],
                "posted_at": now,
            }},
        )
        await scoped.mz2_salla_order_evidence.update_one(
            {"user_id": owner, "id": evidence_id},
            {"$set": {
                "shipping_fee_event_id": proposal["event_id"],
                "shipping_fee_txn_group_id": result["txn_group_id"],
                "shipping_fee_courier_id": facts["courier_id"],
                "shipping_fee_sar": facts["total_fee"],
            }},
        )
        return {
            **proposal,
            "state": "posted",
            "txn_group_id": result["txn_group_id"],
        }
    return await atomic_owner(db, owner, commit)


async def prepare_store_driver_cod(
    db,
    *,
    owner: str,
    assignment_id: str,
) -> dict[str, Any]:
    collection = await db[DRIVER_COLLECTIONS].find_one(
        {"user_id": owner, "assignment_id": assignment_id},
        {"_id": 0},
    )
    if not collection:
        raise ShippingAccountingError("store_driver_collection_missing")
    if collection.get("payment_method") != "cash":
        raise ShippingAccountingError("store_driver_cod_requires_cash_collection")
    if collection.get("review_status") not in {None, "", "not_required"}:
        raise ShippingAccountingError("store_driver_collection_requires_review")

    gross = _positive(collection.get("cod_custody_amount"))
    if _money(collection.get("amount")) != gross:
        raise ShippingAccountingError("store_driver_collection_amount_conflict")
    order_number = str(collection.get("order_number") or "").strip()
    driver_id = str(collection.get("driver_id") or "").strip()
    if not order_number or not driver_id:
        raise ShippingAccountingError("store_driver_collection_identity_missing")

    driver = await db[STORE_DRIVERS].find_one(
        {
            "user_id": owner,
            "id": driver_id,
            "status": {"$ne": "inactive"},
        },
        {"_id": 0, "id": 1, "name": 1},
    )
    if not driver:
        raise ShippingAccountingError("store_driver_missing")

    earning = await db[DRIVER_EARNINGS].find_one(
        {
            "user_id": owner,
            "assignment_id": assignment_id,
            "driver_id": driver_id,
            "order_number": order_number,
        },
        {"_id": 0},
    )
    if not earning:
        raise ShippingAccountingError("store_driver_earning_missing")
    delivery_fee = _money(earning.get("amount"))

    orders = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "order_number": order_number,
        },
        {"_id": 0},
    ).limit(2).to_list(2)
    if len(orders) != 1:
        raise ShippingAccountingError("unique_cod_order_evidence_required")
    evidence = orders[0]
    if evidence.get("conflict"):
        raise ShippingAccountingError("order_evidence_conflict")
    if evidence.get("accounting_provider") != "cod":
        raise ShippingAccountingError("order_is_not_cod")
    if evidence.get("recognition_txn_group_id"):
        raise ShippingAccountingError("cod_order_already_recognized_elsewhere")
    if _money(evidence.get("refunded_sar")) != Decimal("0.00"):
        raise ShippingAccountingError("cod_refund_requires_review")
    if _money(evidence.get("current_net_sar")) != gross:
        raise ShippingAccountingError("cod_collection_order_amount_conflict")

    accounting_at = _instant(collection.get("collected_at")).isoformat()
    if _instant(collection.get("collected_at")) > datetime.now(timezone.utc):
        raise ShippingAccountingError("shipping_event_in_future")

    event_id = _hash([owner, "store_driver_cod", assignment_id])
    facts = {
        "event_id": event_id,
        "assignment_id": assignment_id,
        "collection_id": collection.get("id"),
        "earning_id": earning.get("id"),
        "order_evidence_id": evidence["id"],
        "order_number": order_number,
        "driver_id": driver_id,
        "driver_name": driver.get("name") or "",
        "gross": format(gross, ".2f"),
        "delivery_fee": format(delivery_fee, ".2f"),
        "accounting_at": accounting_at,
    }
    economic_hash = _hash(facts)
    prior = await _event_record(db, owner, event_id)
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise ShippingAccountingError("shipping_event_source_conflict")
        if prior.get("status") == "posted":
            return {
                "state": "already_posted",
                "event_id": event_id,
                "facts": facts,
                "sale_txn_group_id": prior.get("sale_txn_group_id"),
                "fee_txn_group_id": prior.get("fee_txn_group_id"),
                "tax": prior.get("sales_tax"),
            }
        raise ShippingAccountingError("shipping_event_requires_recovery")

    return {
        "state": "eligible",
        "event_id": event_id,
        "facts": facts,
        "economic_hash": economic_hash,
        "evidence": evidence,
    }


async def post_store_driver_cod(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    assignment_id: str,
) -> dict[str, Any]:
    async def commit(scoped):
        proposal = await prepare_store_driver_cod(
            scoped,
            owner=owner,
            assignment_id=assignment_id,
        )
        if proposal["state"] == "already_posted":
            return proposal
        facts = proposal["facts"]
        await require_p02_shipping_financial_writes(
            scoped,
            user_id=owner,
            event_at=facts["accounting_at"],
        )

        required = [
            ("store_driver", facts["driver_id"], "cod_receivable"),
        ]
        if Decimal(facts["delivery_fee"]) > 0:
            required.append(
                ("store_driver", facts["driver_id"], "delivery_fee_payable")
            )
        await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=required,
        )

        event = {
            "kind": "sale",
            "provider": "cod",
            "provider_payment_id": facts["collection_id"],
            "canonical_event_id": facts["assignment_id"],
            "order_number": facts["order_number"],
            "amount": facts["gross"],
            "sale_amount": facts["gross"],
            "currency": "SAR",
            "recognized_at": facts["accounting_at"],
        }
        tax = sale_snapshot(
            await read_policy(scoped, owner),
            event,
            {
                "tax_amount": proposal["evidence"].get("source_tax_sar"),
            },
        )

        existing_sale = await scoped.general_ledger.find_one({
            "user_id": owner,
            "status": {"$in": ["posted", "reversed"]},
            "$or": [
                {
                    "metadata.order_reference_id": facts["order_number"],
                    "entry_type": {"$in": ["bnpl_sale", "cod_sale"]},
                },
                {
                    "metadata.shipping_event_id": proposal["event_id"],
                    "entry_type": "cod_sale",
                },
            ],
        })
        if existing_sale:
            raise ShippingAccountingError(
                "cod_sale_existing_journal_requires_review"
            )

        await _insert_event_posting(
            scoped,
            owner=owner,
            event_id=proposal["event_id"],
            kind="store_driver_cod",
            economic_hash=proposal["economic_hash"],
            facts=facts,
            actor_id=actor["id"],
        )

        base_meta = {
            "operation_id": OPERATION_ID,
            "source": SOURCE,
            "shipping_event_id": proposal["event_id"],
            "shipping_event_kind": "store_driver_cod",
            "accounting_at": facts["accounting_at"],
            "order_reference_id": facts["order_number"],
            "assignment_id": facts["assignment_id"],
            "collection_id": facts["collection_id"],
            "driver_id": facts["driver_id"],
        }
        sale = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            txn_type="mz2_cod_sale",
            notes=f"بيع COD — {facts['order_number']} — {facts['driver_name']}",
            metadata={
                **base_meta,
                "sales_tax": tax,
                "recognition_source": "store_driver_cash_collection",
            },
            entries=[
                {
                    "entity_type": "store_driver",
                    "entity_id": facts["driver_id"],
                    "sub_account": "cod_receivable",
                    "side": "debit",
                    "amount": tax["gross"],
                    "entry_type": "cod_sale",
                },
                {
                    "entity_type": "revenue",
                    "entity_id": "bnpl_sales",
                    "side": "credit",
                    "amount": tax["net"],
                    "entry_type": "cod_sale",
                },
                *(
                    [{
                        "entity_type": "tax",
                        "entity_id": "sales_vat_payable",
                        "side": "credit",
                        "amount": tax["tax"],
                        "entry_type": "cod_sale",
                    }]
                    if Decimal(tax["tax"]) > 0
                    else []
                ),
            ],
        )

        fee_group_id = None
        fee = Decimal(facts["delivery_fee"])
        if fee > 0:
            fee_group = await post_txn_group(
                scoped,
                user_id=owner,
                actor_id=actor["id"],
                actor_name=actor.get("name") or actor.get("email") or actor["id"],
                txn_type="mz2_store_driver_fee_accrual",
                notes=(
                    f"أجرة موصل المتجر — {facts['order_number']} — "
                    f"{facts['driver_name']}"
                ),
                metadata={
                    **base_meta,
                    "earning_id": facts["earning_id"],
                    "fee_snapshot_source": "store_delivery_driver_earnings",
                },
                entries=[
                    {
                        "entity_type": "expense",
                        "entity_id": "store_delivery",
                        "side": "debit",
                        "amount": facts["delivery_fee"],
                        "entry_type": "shipping_fee_accrual",
                    },
                    {
                        "entity_type": "store_driver",
                        "entity_id": facts["driver_id"],
                        "sub_account": "delivery_fee_payable",
                        "side": "credit",
                        "amount": facts["delivery_fee"],
                        "entry_type": "shipping_fee_accrual",
                    },
                ],
            )
            fee_group_id = fee_group["txn_group_id"]

        now = datetime.now(timezone.utc).isoformat()
        await scoped.mz2_shipping_accounting_events.update_one(
            {
                "_id": proposal["event_id"],
                "user_id": owner,
                "status": "posting",
            },
            {"$set": {
                "status": "posted",
                "sale_txn_group_id": sale["txn_group_id"],
                "fee_txn_group_id": fee_group_id,
                "sales_tax": tax,
                "posted_at": now,
            }},
        )
        for collection_name in (DRIVER_COLLECTIONS, DRIVER_EARNINGS):
            await scoped[collection_name].update_one(
                {
                    "user_id": owner,
                    "assignment_id": assignment_id,
                },
                {"$set": {
                    "mz2_p02_accounting_status": "posted",
                    "mz2_p02_event_id": proposal["event_id"],
                    "mz2_p02_sale_txn_group_id": sale["txn_group_id"],
                    "mz2_p02_fee_txn_group_id": fee_group_id,
                }},
            )
        await scoped.mz2_salla_order_evidence.update_one(
            {
                "user_id": owner,
                "id": proposal["evidence"]["id"],
            },
            {"$set": {
                "status": "recognized_cod",
                "recognition_txn_group_id": sale["txn_group_id"],
                "recognized_provider": "cod",
                "recognized_gross_sar": tax["gross"],
                "recognized_net_sar": tax["net"],
                "recognized_tax_sar": tax["tax"],
                "recognized_at": facts["accounting_at"],
                "recognized_by": actor["id"],
                "recognition_posted_at": now,
                "shipping_p02_event_id": proposal["event_id"],
            }},
        )
        return {
            **proposal,
            "state": "posted",
            "sale_txn_group_id": sale["txn_group_id"],
            "fee_txn_group_id": fee_group_id,
            "tax": tax,
        }

    return await atomic_owner(db, owner, commit)


class CourierFeePostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str = Field(min_length=1, max_length=200)


class DriverCodPostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignment_id: str = Field(min_length=1, max_length=200)


def install_shipping_p02_routes(router, db, current_user) -> None:
    base = "/accounting-module/shipping-p02"

    async def scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.get(base + "/rates")
    async def rates(user: dict = Depends(current_user)):
        _, owner = await scope(user, "accounting.shipping.view")
        result = dict(await read_shipping_policy(db, owner))
        result.pop("_id", None)
        return result

    @router.put(base + "/rates")
    async def put_rate(
        payload: ShippingRateInput,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.rules.manage")
        return await save_shipping_rate(
            db, owner=owner, actor=actor, payload=payload
        )

    @router.get(base + "/courier-fee/{evidence_id}/preview")
    async def courier_preview(
        evidence_id: str,
        user: dict = Depends(current_user),
    ):
        _, owner = await scope(user, "accounting.shipping.view")
        try:
            return await prepare_courier_fee(
                db, owner=owner, evidence_id=evidence_id
            )
        except (ShippingAccountingError, TaxError) as exc:
            return {"state": "rejected", "reasons": [str(exc)]}

    @router.post(base + "/courier-fee")
    async def courier_post(
        payload: CourierFeePostIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.settlements.post")
        try:
            return await post_courier_fee(
                db,
                owner=owner,
                actor=actor,
                evidence_id=payload.evidence_id,
            )
        except ShippingAccountingError as exc:
            raise HTTPException(
                409, detail={"code": str(exc), "message": str(exc)}
            ) from None

    @router.get(base + "/store-driver-cod/{assignment_id}/preview")
    async def driver_preview(
        assignment_id: str,
        user: dict = Depends(current_user),
    ):
        _, owner = await scope(user, "accounting.shipping.view")
        try:
            return await prepare_store_driver_cod(
                db, owner=owner, assignment_id=assignment_id
            )
        except (ShippingAccountingError, TaxError) as exc:
            return {"state": "rejected", "reasons": [str(exc)]}

    @router.post(base + "/store-driver-cod")
    async def driver_post(
        payload: DriverCodPostIn,
        user: dict = Depends(current_user),
    ):
        actor, owner = await scope(user, "accounting.settlements.post")
        try:
            return await post_store_driver_cod(
                db,
                owner=owner,
                actor=actor,
                assignment_id=payload.assignment_id,
            )
        except (ShippingAccountingError, TaxError) as exc:
            raise HTTPException(
                409, detail={"code": str(exc), "message": str(exc)}
            ) from None
