"""Ad-Account Balance + Debt Engine — Iter-106

Reuses the existing `counterparties` row (kind=ad_account) as the
permanent identity & balance carrier of each ad platform / sub-account
(Snap 1, Snap 2, TikTok …). The merchant's daily ad spend is funded
from a prepaid balance; when the balance runs out we accrue a liability
(`liabilities` kind=ad_account) so the **financial position** reflects
the real obligation owed to the ad platform.

Two fields added to counterparties (additive, no replacement):
    balance:    float — current PREPAID balance available for spend
    debt_mode:  "auto" | "manual"
                auto   = uncovered spend auto-creates / extends an
                         ad_account liability row.
                manual = user creates / edits liabilities themselves.

Atomic operations
-----------------
1) /topup     bank ↓ amount, then:
                - if open debt exists → pay it down via the liability,
                - any remainder goes to `counterparties.balance`.
2) /spend     amount = ad platform's daily spend.
                - In ANY mode: balance covers what it can (up to its
                  current value), rest becomes the "uncovered" piece.
                - In AUTO mode: the uncovered piece creates / extends
                  an open liability (kind=ad_account) for that account.
                - In MANUAL mode: the uncovered piece is recorded in
                  the ledger but no liability is created — user must
                  add it manually if they want.

A `ad_account_ledger` row is written for EVERY action — full audit.
Existing daily ad-spend collections (snapchat_ads_daily,
tiktok_ads_daily, meta_ads_daily) are NOT modified. They remain the
source of truth for daily-cost reporting. /spend is a separate hook the
merchant calls once per day per ad account.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round(v) -> float:
    return round(float(v or 0), 2)


# ── Models ─────────────────────────────────────────────────────────────
class TopupIn(BaseModel):
    amount: float = Field(..., gt=0)
    paid_from_account_id: str
    transaction_date: str = Field(..., min_length=10, max_length=10)
    notes: Optional[str] = ""


class SpendIn(BaseModel):
    amount: float = Field(..., gt=0)
    spend_date: str = Field(..., min_length=10, max_length=10)
    description: Optional[str] = ""
    notes: Optional[str] = ""


class SettingsIn(BaseModel):
    debt_mode: Literal["auto", "manual"]


# Iter-107 — inline ad-account create + daily-platform sync
class CreateAdAccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    ad_provider: Literal["snapchat", "tiktok", "meta", "google", "twitter", "other"]
    notes: Optional[str] = ""
    force: bool = False
    # Iter-109 — external ID on the ad platform itself (e.g. Snapchat
    # ad_account_id "acc_SA_001"). When set the sync filters daily
    # spend by this exact ID so multi-account users get independent
    # debt per account.
    external_account_id: Optional[str] = Field(None, max_length=120)


class UpdateAdAccountIn(BaseModel):
    """Iter-109 — edit name / notes / external_account_id (debt-mode
    has its own dedicated endpoint)."""
    name: Optional[str] = Field(None, min_length=1, max_length=160)
    notes: Optional[str] = Field(None, max_length=2000)
    external_account_id: Optional[str] = Field(None, max_length=120)


class SyncFromPlatformIn(BaseModel):
    from_date: str = Field(..., min_length=10, max_length=10)
    to_date:   str = Field(..., min_length=10, max_length=10)


# ── Helpers ────────────────────────────────────────────────────────────
async def _get_account(db, user_id: str, cp_id: str) -> dict:
    cp = await db.counterparties.find_one(
        {"id": cp_id, "user_id": user_id, "kind": "ad_account"}, {"_id": 0},
    )
    if not cp:
        raise HTTPException(404, "حساب إعلاني غير موجود")
    return cp


async def _current_open_debt(db, user_id: str, cp_id: str) -> Optional[dict]:
    """Find the single open ad_account liability for this counterparty.
    We coalesce to one open row per account so the picture stays clean."""
    return await db.liabilities.find_one(
        {
            "user_id": user_id,
            "kind": "ad_account",
            "counterparty_id": cp_id,
            "status": {"$in": ["unpaid", "partial"]},
        },
        {"_id": 0},
        sort=[("created_at", 1)],
    )


async def _summarise(db, user_id: str, cp: dict) -> dict:
    debt = await _current_open_debt(db, user_id, cp["id"])
    debt_remaining = 0.0
    if debt:
        debt_remaining = _round(
            (debt.get("expected_amount") or 0) - (debt.get("paid_amount") or 0)
        )
    # Lifetime spend = sum of ledger spend rows
    total_spend = 0.0
    async for row in db.ad_account_ledger.find(
        {"user_id": user_id, "counterparty_id": cp["id"], "type": "spend"},
        {"_id": 0, "amount": 1},
    ):
        total_spend += float(row.get("amount") or 0)
    last_events = {"topup": None, "spend": None, "debt": None}
    for ev_type in ("topup", "spend", "debt"):
        row = await db.ad_account_ledger.find_one(
            {"user_id": user_id, "counterparty_id": cp["id"], "type": ev_type},
            {"_id": 0}, sort=[("created_at", -1)],
        )
        last_events[ev_type] = row
    return {
        "id": cp["id"],
        "name": cp["name"],
        "ad_provider": cp.get("ad_provider"),
        "external_account_id": cp.get("external_account_id"),
        "balance": _round(cp.get("balance") or 0),
        "debt_mode": cp.get("debt_mode") or "auto",
        "open_debt": debt_remaining,
        "open_debt_id": debt["id"] if debt else None,
        "total_spend": _round(total_spend),
        "last_topup": last_events["topup"],
        "last_spend": last_events["spend"],
        "last_debt": last_events["debt"],
        "last_auto_sync_date": cp.get("last_auto_sync_date"),
        "notes": cp.get("notes"),
    }


async def _ledger_write(
    db, user_id: str, cp_id: str, ev_type: str,
    amount: float, balance_after: float, debt_after: float,
    *, account_id: Optional[str] = None,
    related_liability_id: Optional[str] = None,
    related_tx_id: Optional[str] = None,
    description: str = "",
    notes: str = "",
    breakdown: Optional[dict] = None,
    date: Optional[str] = None,
) -> dict:
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "counterparty_id": cp_id,
        "type": ev_type,                         # topup / spend / debt / payoff / manual / reverse
        "amount": _round(amount),
        "balance_after": _round(balance_after),
        "debt_after": _round(debt_after),
        "account_id": account_id,
        "related_liability_id": related_liability_id,
        "related_transaction_id": related_tx_id,
        "description": description,
        "notes": notes,
        "breakdown": breakdown or {},
        "date": date or _now()[:10],
        "created_at": _now(),
    }
    await db.ad_account_ledger.insert_one(doc)
    return doc


async def _post_bank_tx(db, user_id: str, *,
                        account_id: str, amount: float, direction: str,
                        transaction_date: str, description: str) -> dict:
    """Use the existing accounts ledger for any cash movement."""
    acc = await db.accounts.find_one(
        {"id": account_id, "user_id": user_id}, {"_id": 0, "id": 1, "name": 1},
    )
    if not acc:
        raise HTTPException(404, "Bank account not found")
    tx_id = str(uuid.uuid4())
    await db.account_transactions.insert_one({
        "id": tx_id,
        "user_id": user_id,
        "account_id": account_id,
        "amount": _round(amount),
        "direction": direction,                  # "out" for top-up
        "transaction_type": "ad_account_topup",
        "transaction_date": transaction_date,
        "description": description,
        "created_at": _now(),
    })
    # Recompute bank balance
    from accounts_routes import _recompute_balance  # local import to avoid cycles
    await _recompute_balance(db, user_id, account_id)
    return {"id": tx_id}


# ── Index setup ────────────────────────────────────────────────────────
async def ensure_ad_account_indexes(db) -> None:
    try:
        await db.ad_account_ledger.create_index(
            [("user_id", 1), ("counterparty_id", 1), ("created_at", -1)],
            name="ledger_owner_cp_date",
        )
    except Exception:
        pass


# ── Router ─────────────────────────────────────────────────────────────
def attach_ad_account_routes(parent_router: APIRouter, db) -> None:
    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    router = APIRouter(prefix="/ad-accounts", tags=["ad-accounts"])

    # ── GET / ─────────────────────────────────────────────────────────
    @router.get("")
    async def list_ad_accounts(user: dict = Depends(current_user)):
        out = []
        async for cp in db.counterparties.find(
            {"user_id": user["id"], "kind": "ad_account"}, {"_id": 0},
        ).sort([("name", 1)]):
            out.append(await _summarise(db, user["id"], cp))
        # Totals across all ad accounts
        totals = {
            "balance":    sum(x["balance"] for x in out),
            "open_debt":  sum(x["open_debt"] for x in out),
            "total_spend": sum(x["total_spend"] for x in out),
        }
        return {
            "items": out,
            "total": len(out),
            "totals": {k: _round(v) for k, v in totals.items()},
        }

    # ── GET /{id} ─────────────────────────────────────────────────────
    @router.get("/{cp_id}")
    async def get_ad_account(cp_id: str, user: dict = Depends(current_user)):
        cp = await _get_account(db, user["id"], cp_id)
        return await _summarise(db, user["id"], cp)

    # ── GET /{id}/ledger ──────────────────────────────────────────────
    @router.get("/{cp_id}/ledger")
    async def get_ledger(
        cp_id: str, limit: int = 200,
        user: dict = Depends(current_user),
    ):
        await _get_account(db, user["id"], cp_id)
        rows = []
        async for r in db.ad_account_ledger.find(
            {"user_id": user["id"], "counterparty_id": cp_id}, {"_id": 0},
        ).sort([("created_at", -1)]).limit(limit):
            rows.append(r)
        return {"items": rows, "total": len(rows)}

    # ── PUT /{id}/settings ────────────────────────────────────────────
    @router.put("/{cp_id}/settings")
    async def set_settings(
        cp_id: str, payload: SettingsIn,
        user: dict = Depends(current_user),
    ):
        await _get_account(db, user["id"], cp_id)
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]},
            {"$set": {"debt_mode": payload.debt_mode, "updated_at": _now()}},
        )
        cp = await _get_account(db, user["id"], cp_id)
        return await _summarise(db, user["id"], cp)

    # ── POST /{id}/topup ──────────────────────────────────────────────
    @router.post("/{cp_id}/topup")
    async def topup(
        cp_id: str, payload: TopupIn,
        user: dict = Depends(current_user),
    ):
        cp = await _get_account(db, user["id"], cp_id)
        amount = _round(payload.amount)

        # 1) Deduct from bank
        tx = await _post_bank_tx(
            db, user["id"],
            account_id=payload.paid_from_account_id,
            amount=amount, direction="out",
            transaction_date=payload.transaction_date,
            description=f"تعبئة رصيد {cp['name']}",
        )

        # 2) Apply to debt first (if any) — both modes do this.
        debt = await _current_open_debt(db, user["id"], cp_id)
        amount_to_debt = 0.0
        amount_to_balance = amount
        liab_after_remaining = 0.0
        liab_id = None
        if debt:
            outstanding = _round(
                (debt.get("expected_amount") or 0) - (debt.get("paid_amount") or 0)
            )
            amount_to_debt = min(amount, outstanding)
            amount_to_balance = _round(amount - amount_to_debt)
            new_paid = _round((debt.get("paid_amount") or 0) + amount_to_debt)
            new_status = "paid" if new_paid + 0.01 >= float(debt["expected_amount"]) else "partial"
            await db.liabilities.update_one(
                {"id": debt["id"], "user_id": user["id"]},
                {"$set": {
                    "paid_amount": new_paid,
                    "status": new_status,
                    "updated_at": _now(),
                }},
            )
            liab_id = debt["id"]
            liab_after_remaining = _round(float(debt["expected_amount"]) - new_paid)

        # 3) Any remainder bumps the balance.
        new_balance = _round((cp.get("balance") or 0) + amount_to_balance)
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]},
            {"$set": {"balance": new_balance, "updated_at": _now()}},
        )

        await _ledger_write(
            db, user["id"], cp_id, "topup",
            amount, new_balance, liab_after_remaining,
            account_id=payload.paid_from_account_id,
            related_liability_id=liab_id,
            related_tx_id=tx["id"],
            description=f"تعبئة من بنك ← {cp['name']}",
            notes=payload.notes or "",
            breakdown={
                "to_debt": _round(amount_to_debt),
                "to_balance": _round(amount_to_balance),
            },
            date=payload.transaction_date,
        )

        cp_fresh = await _get_account(db, user["id"], cp_id)
        return {
            "ok": True,
            "amount": amount,
            "applied_to_debt": _round(amount_to_debt),
            "applied_to_balance": _round(amount_to_balance),
            "ad_account": await _summarise(db, user["id"], cp_fresh),
        }

    # ── POST /{id}/spend ──────────────────────────────────────────────
    @router.post("/{cp_id}/spend")
    async def record_spend(
        cp_id: str, payload: SpendIn,
        user: dict = Depends(current_user),
    ):
        cp = await _get_account(db, user["id"], cp_id)
        amount = _round(payload.amount)
        balance_before = _round(cp.get("balance") or 0)
        mode = cp.get("debt_mode") or "auto"

        # 1) Apply as much as possible from balance.
        covered = min(amount, balance_before)
        uncovered = _round(amount - covered)
        new_balance = _round(balance_before - covered)

        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]},
            {"$set": {"balance": new_balance, "updated_at": _now()}},
        )

        # 2) Handle the uncovered portion based on mode.
        liab_id = None
        debt_after = 0.0
        if uncovered > 0:
            existing_debt = await _current_open_debt(db, user["id"], cp_id)
            if mode == "auto":
                if existing_debt:
                    new_expected = _round(
                        (existing_debt.get("expected_amount") or 0) + uncovered
                    )
                    await db.liabilities.update_one(
                        {"id": existing_debt["id"], "user_id": user["id"]},
                        {"$set": {
                            "expected_amount": new_expected,
                            "status": "partial" if (existing_debt.get("paid_amount") or 0) > 0 else "unpaid",
                            "updated_at": _now(),
                        }},
                    )
                    liab_id = existing_debt["id"]
                    debt_after = _round(new_expected - (existing_debt.get("paid_amount") or 0))
                else:
                    liab_id = str(uuid.uuid4())
                    debt_doc = {
                        "id": liab_id,
                        "user_id": user["id"],
                        "kind": "ad_account",
                        "ad_provider": cp.get("ad_provider"),
                        "ad_account_label": cp["name"],
                        "counterparty_id": cp_id,
                        "expected_amount": uncovered,
                        "paid_amount": 0.0,
                        "advance_deducted": 0.0,
                        "due_date": payload.spend_date,
                        "status": "unpaid",
                        "description": f"مديونية تلقائية — صرف يومي {cp['name']}",
                        "notes": "",
                        "auto_generated": True,
                        "source": "ad_account_engine",
                        "created_at": _now(),
                        "updated_at": _now(),
                    }
                    await db.liabilities.insert_one(debt_doc)
                    debt_after = uncovered
                # write a separate "debt" ledger row to highlight the event
                await _ledger_write(
                    db, user["id"], cp_id, "debt",
                    uncovered, new_balance, debt_after,
                    related_liability_id=liab_id,
                    description=f"إنشاء/زيادة مديونية {cp['name']}",
                    date=payload.spend_date,
                )
            # manual mode: do NOT auto-create debt. Just record in ledger.

            else:
                debt_after = _round(
                    (existing_debt.get("expected_amount", 0) - existing_debt.get("paid_amount", 0))
                    if existing_debt else 0.0
                )

        # 3) Always write the spend ledger row.
        await _ledger_write(
            db, user["id"], cp_id, "spend",
            amount, new_balance, debt_after,
            related_liability_id=liab_id,
            description=payload.description or f"صرف يومي {cp['name']}",
            notes=payload.notes or "",
            breakdown={
                "from_balance": covered,
                "uncovered": uncovered,
                "mode": mode,
                "created_debt": uncovered if (uncovered > 0 and mode == "auto") else 0.0,
            },
            date=payload.spend_date,
        )

        cp_fresh = await _get_account(db, user["id"], cp_id)
        return {
            "ok": True,
            "amount": amount,
            "covered_by_balance": covered,
            "uncovered": uncovered,
            "debt_created": uncovered if (uncovered > 0 and mode == "auto") else 0.0,
            "mode": mode,
            "ad_account": await _summarise(db, user["id"], cp_fresh),
        }

    # ── POST /{id}/sync-from-platform (Iter-107) ──────────────────────
    @router.post("/{cp_id}/sync-from-platform")
    async def sync_from_platform(
        cp_id: str, payload: SyncFromPlatformIn,
        user: dict = Depends(current_user),
    ):
        cp = await _get_account(db, user["id"], cp_id)
        provider = cp.get("ad_provider")
        collection_map = {
            "snapchat": "snapchat_ads_daily",
            "tiktok":   "tiktok_ads_daily",
            "meta":     "meta_ads_daily",
        }
        col_name = collection_map.get(provider)
        if not col_name:
            raise HTTPException(
                400,
                f"المزامنة التلقائية غير متاحة لمنصة {provider}. استخدم /spend يدوياً.",
            )
        # Sum spend in the date range — filter by external_account_id
        # when set on the counterparty so multi-account users get
        # independent per-account debt (Iter-109).
        q = {
            "user_id": user["id"],
            "date": {"$gte": payload.from_date, "$lte": payload.to_date},
        }
        ext_id = (cp.get("external_account_id") or "").strip()
        if ext_id:
            q["ad_account_id"] = ext_id
        cur = db[col_name].find(q, {"_id": 0, "spend": 1, "date": 1})
        total_spend = 0.0
        days_seen = set()
        async for row in cur:
            total_spend += float(row.get("spend") or 0)
            days_seen.add(row.get("date"))
        total_spend = _round(total_spend)
        if total_spend <= 0:
            return {
                "ok": True, "spend": 0.0, "days_synced": 0,
                "message": "لا توجد بيانات صرف في الفترة المختارة",
                "ad_account": await _summarise(db, user["id"], cp),
            }

        # Reuse the existing /spend logic by calling record_spend manually.
        spend_payload = SpendIn(
            amount=total_spend,
            spend_date=payload.to_date,
            description=f"مزامنة تلقائية من {provider} "
                        f"({payload.from_date} → {payload.to_date})",
            notes=f"مدمج عبر sync-from-platform · {len(days_seen)} يوم",
        )
        return await record_spend(cp_id, spend_payload, user)

    # ── POST / (create new ad account inline — Iter-107) ──────────────
    @router.post("")
    async def create_ad_account(
        payload: CreateAdAccountIn,
        user: dict = Depends(current_user),
    ):
        """Inline shortcut — creates a counterparty(kind=ad_account)
        + initialises balance=0 and debt_mode=auto. Re-uses the
        counterparties duplicate-guard helpers."""
        from counterparties_routes import _norm, _fuzzy_match

        name = payload.name.strip()
        if not name:
            raise HTTPException(400, "الاسم مطلوب")
        name_lower = _norm(name)

        # Exact duplicate inside the same provider
        existing = await db.counterparties.find_one(
            {"user_id": user["id"], "kind": "ad_account",
             "ad_provider": payload.ad_provider, "name_lower": name_lower},
            {"_id": 0},
        )
        if existing:
            raise HTTPException(
                409,
                {"message": "duplicate",
                 "existing": {"id": existing["id"], "name": existing["name"]}},
            )

        # Fuzzy warn (within same provider) unless force=True
        if not payload.force:
            candidates = []
            async for d in db.counterparties.find(
                {"user_id": user["id"], "kind": "ad_account",
                 "ad_provider": payload.ad_provider},
                {"_id": 0, "id": 1, "name": 1, "name_lower": 1, "ad_provider": 1},
            ):
                candidates.append(d)
            match = _fuzzy_match(name, candidates)
            if match:
                raise HTTPException(
                    409,
                    {"message": "similar_name_exists",
                     "suggestion": {"id": match["id"], "name": match["name"],
                                    "ad_provider": match.get("ad_provider")}},
                )

        now = _now()
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "kind": "ad_account",
            "ad_provider": payload.ad_provider,
            "name": name,
            "name_lower": name_lower,
            "notes": payload.notes or "",
            "balance": 0.0,
            "debt_mode": "auto",
            "external_account_id": (payload.external_account_id or "").strip() or None,
            "created_at": now,
            "updated_at": now,
        }
        await db.counterparties.insert_one(doc)
        return await _summarise(db, user["id"], doc)

    # ── PATCH /{cp_id} — edit name / notes / external_account_id (Iter-109)
    @router.patch("/{cp_id}")
    async def update_ad_account(
        cp_id: str, payload: UpdateAdAccountIn,
        user: dict = Depends(current_user),
    ):
        await _get_account(db, user["id"], cp_id)
        upd = {"updated_at": _now()}
        if payload.name is not None:
            upd["name"] = payload.name.strip()
            upd["name_lower"] = payload.name.strip().lower()
        if payload.notes is not None:
            upd["notes"] = payload.notes
        if payload.external_account_id is not None:
            upd["external_account_id"] = payload.external_account_id.strip() or None
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]}, {"$set": upd},
        )
        cp = await _get_account(db, user["id"], cp_id)
        return await _summarise(db, user["id"], cp)

    # ── DELETE /{cp_id} ───────────────────────────────────────────────
    @router.delete("/{cp_id}")
    async def delete_ad_account(cp_id: str, user: dict = Depends(current_user)):
        cp = await _get_account(db, user["id"], cp_id)
        # Refuse delete when there's open debt or a non-zero balance.
        debt = await _current_open_debt(db, user["id"], cp_id)
        if debt:
            raise HTTPException(
                400, "لا يمكن الحذف. الحساب عليه مديونية مفتوحة — سدّدها أولاً.",
            )
        if _round(cp.get("balance") or 0) > 0:
            raise HTTPException(
                400, "لا يمكن الحذف. الحساب فيه رصيد متبقي.",
            )
        await db.counterparties.delete_one({"id": cp_id, "user_id": user["id"]})
        return {"ok": True}

    # ── POST /sync-all (Iter-108) — manual trigger of the daily cron ──
    @router.post("/sync-all")
    async def sync_all_for_user(
        payload: SyncFromPlatformIn,
        user: dict = Depends(current_user),
    ):
        """Run sync-from-platform for EVERY ad account this user owns
        that has a supported provider (snapchat/tiktok/meta). The same
        endpoint is invoked by the daily cron at 23:55."""
        results = await _run_sync_for_all(
            db, user["id"], payload.from_date, payload.to_date,
        )
        return {"ok": True, "results": results}

    parent_router.include_router(router)


# ── Module-level cron helpers (Iter-108) ───────────────────────────────
async def _run_sync_for_all(db, user_id: str, from_date: str, to_date: str) -> list[dict]:
    """For each ad_account counterparty (supported providers only),
    aggregate daily-platform spend in the range and post it as a /spend
    via the same internal helpers. Idempotent per (account, to_date)
    via `last_auto_sync_date` on the counterparty."""
    SUPPORTED = {"snapchat": "snapchat_ads_daily",
                 "tiktok":   "tiktok_ads_daily",
                 "meta":     "meta_ads_daily"}
    out = []
    async for cp in db.counterparties.find(
        {"user_id": user_id, "kind": "ad_account",
         "ad_provider": {"$in": list(SUPPORTED)}},
        {"_id": 0},
    ):
        # Skip if already synced for this `to_date` (idempotency).
        if cp.get("last_auto_sync_date") == to_date:
            out.append({"id": cp["id"], "name": cp["name"],
                        "skipped": True, "reason": "already_synced"})
            continue
        col = SUPPORTED[cp["ad_provider"]]
        # Iter-109 — per-account filter via external_account_id.
        q = {"user_id": user_id, "date": {"$gte": from_date, "$lte": to_date}}
        ext_id = (cp.get("external_account_id") or "").strip()
        if ext_id:
            q["ad_account_id"] = ext_id
        total = 0.0
        async for row in db[col].find(q, {"_id": 0, "spend": 1}):
            total += float(row.get("spend") or 0)
        total = round(total, 2)
        if total <= 0:
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now()}},
            )
            out.append({"id": cp["id"], "name": cp["name"], "spend": 0.0})
            continue

        # Apply via the same internal logic as /spend.
        balance_before = float(cp.get("balance") or 0)
        covered = min(total, balance_before)
        uncovered = round(total - covered, 2)
        new_balance = round(balance_before - covered, 2)
        await db.counterparties.update_one(
            {"id": cp["id"]},
            {"$set": {"balance": new_balance,
                      "last_auto_sync_date": to_date,
                      "last_auto_sync_at": _now(),
                      "updated_at": _now()}},
        )
        mode = cp.get("debt_mode") or "auto"
        liab_id = None
        debt_after = 0.0
        if uncovered > 0 and mode == "auto":
            existing = await db.liabilities.find_one(
                {"user_id": user_id, "kind": "ad_account",
                 "counterparty_id": cp["id"],
                 "status": {"$in": ["unpaid", "partial"]}},
                {"_id": 0},
            )
            if existing:
                new_exp = round((existing.get("expected_amount") or 0) + uncovered, 2)
                await db.liabilities.update_one(
                    {"id": existing["id"]},
                    {"$set": {"expected_amount": new_exp, "updated_at": _now()}},
                )
                liab_id = existing["id"]
                debt_after = round(new_exp - (existing.get("paid_amount") or 0), 2)
            else:
                liab_id = str(uuid.uuid4())
                await db.liabilities.insert_one({
                    "id": liab_id, "user_id": user_id, "kind": "ad_account",
                    "ad_provider": cp["ad_provider"],
                    "ad_account_label": cp["name"],
                    "counterparty_id": cp["id"],
                    "expected_amount": uncovered, "paid_amount": 0.0,
                    "advance_deducted": 0.0,
                    "due_date": to_date, "status": "unpaid",
                    "description": f"مديونية تلقائية (cron) — {cp['name']}",
                    "auto_generated": True, "source": "ad_account_cron",
                    "created_at": _now(), "updated_at": _now(),
                })
                debt_after = uncovered

        # Ledger
        await db.ad_account_ledger.insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id,
            "counterparty_id": cp["id"], "type": "spend",
            "amount": total, "balance_after": new_balance,
            "debt_after": debt_after,
            "related_liability_id": liab_id,
            "description": f"مزامنة مجدولة من {cp['ad_provider']} ({from_date} → {to_date})",
            "breakdown": {"from_balance": covered, "uncovered": uncovered,
                          "mode": mode, "auto_cron": True,
                          "created_debt": uncovered if mode == "auto" else 0.0},
            "date": to_date, "created_at": _now(),
        })
        out.append({
            "id": cp["id"], "name": cp["name"], "spend": total,
            "covered": covered, "uncovered": uncovered,
            "debt_created": uncovered if mode == "auto" else 0.0,
        })
    return out


async def run_daily_cron(db) -> dict:
    """Iterate ALL users with at least one ad-account counterparty and
    sync today's spend. Designed to be called from a scheduler at 23:55."""
    from datetime import date
    today = date.today().isoformat()
    users_done = []
    seen_users = set()
    async for cp in db.counterparties.find(
        {"kind": "ad_account"}, {"_id": 0, "user_id": 1},
    ):
        if cp["user_id"] in seen_users:
            continue
        seen_users.add(cp["user_id"])
        results = await _run_sync_for_all(db, cp["user_id"], today, today)
        users_done.append({"user_id": cp["user_id"], "results": results})
    return {"ran_at": _now(), "today": today,
            "users_processed": len(users_done), "details": users_done}
