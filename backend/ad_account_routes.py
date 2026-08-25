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
from tz_utils import riyadh_today_iso


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


# Iter-159i — per-account credit limit + alert threshold.
class CreditLimitIn(BaseModel):
    credit_limit: Optional[float] = Field(None, ge=0, le=10_000_000)
    alert_threshold_pct: Optional[float] = Field(None, ge=0, le=100)


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


class TopupEditIn(BaseModel):
    """Iter-112 — edit an existing topup's amount and/or date.

    Pass only fields you want to change. The ledger entry, the
    counterparty balance, and the linked bank transaction will all be
    updated atomically. We do this by reversing the original effects
    and re-applying with the new values (keeping the same ledger_id /
    bank_tx_id so any external references survive)."""
    amount: Optional[float] = Field(None, gt=0)
    transaction_date: Optional[str] = Field(None, min_length=10, max_length=10)
    description: Optional[str] = Field(None, max_length=500)
    notes: Optional[str] = Field(None, max_length=500)


class SyncFromPlatformIn(BaseModel):
    from_date: str = Field(..., min_length=10, max_length=10)
    to_date:   str = Field(..., min_length=10, max_length=10)
    # Iter-110 — when true, bypass the per-account `last_auto_sync_date`
    # idempotency guard (used when a previous buggy sync set the flag
    # but did not actually create the liability rows).
    force: bool = False


# ── Iter-110 — Historical Migration ────────────────────────────────────
class MigrationPreviewIn(BaseModel):
    from_date: str = Field(..., min_length=10, max_length=10)
    to_date:   str = Field(..., min_length=10, max_length=10)


class MigrationApplyIn(BaseModel):
    from_date: str = Field(..., min_length=10, max_length=10)
    to_date:   str = Field(..., min_length=10, max_length=10)
    mode: Literal["daily", "lump"] = "daily"
    account_ids: list[str] = Field(default_factory=list, min_length=1)


class OpeningIn(BaseModel):
    """Manual opening figures for an ad account.

    All fields are optional — pass only what you want to set. The
    endpoint writes an `opening` ledger row + (if `opening_debt > 0`)
    creates / refreshes a dedicated open liability tagged
    source=ad_account_opening so it's auditable & non-conflicting with
    the auto sync liabilities (source=ad_account_engine|ad_account_cron).
    """
    opening_balance: Optional[float] = Field(None, ge=0)
    opening_debt: Optional[float] = Field(None, ge=0)
    start_date: Optional[str] = Field(None, min_length=10, max_length=10)
    method: Optional[Literal["auto", "manual"]] = None
    notes: Optional[str] = Field(None, max_length=2000)


# ── Helpers ────────────────────────────────────────────────────────────
# Iter-110 — Per-provider source-of-truth collections + the column used
# to scope a sub-account inside each collection.
#   • snapchat → snapchat_account_daily.ad_account_id  (ALSO falls back
#                to snapchat_ads_daily when no per-account rows exist).
#   • meta     → meta_ads_daily.account_id
#   • tiktok   → tiktok_ads_daily — has NO sub-account column. When the
#                merchant has more than one TikTok counterparty without
#                a discriminator, every account would otherwise see the
#                full platform spend → we WARN and skip those.
PROVIDER_SOURCES = {
    "snapchat": [
        {"collection": "snapchat_account_daily", "scope_field": "ad_account_id"},
        {"collection": "snapchat_ads_daily",     "scope_field": None},
    ],
    "meta": [
        {"collection": "meta_ads_daily", "scope_field": "account_id"},
    ],
    "tiktok": [
        {"collection": "tiktok_ads_daily", "scope_field": None},
    ],
}


# Iter-212 — Whitelist of providers that are pulled via DIRECT platform
# APIs (and therefore safe to run on the 30-minute cron). Anything else
# (currently TikTok and any future provider) is delivered via Make.com
# on its own schedule (~5h) — the cron must NOT process those accounts
# or we'd double-count once Make.com re-delivers.
#
# Per-account override: setting `counterparties.sync_via = "make_com"`
# explicitly opts an individual account OUT of the half-hour cron
# regardless of its provider.
HALFHOUR_SYNC_PROVIDERS = {"snapchat", "meta"}


async def _fetch_daily_spend(
    db, user_id: str, provider: str, external_id: Optional[str],
    from_date: str, to_date: str,
) -> tuple[list[dict], str]:
    """Return ([{date, spend}], source_collection_used).

    For Snapchat we prefer `snapchat_account_daily` (per-account
    granular). If that has zero rows for this user we transparently
    fall back to the older `snapchat_ads_daily` (campaign-level only).

    Iter-163 — Critical guard against cross-account aggregation:
    If a source declares a `scope_field` (i.e. requires per-account
    scoping like `ad_account_id` / `account_id`) but no `external_id`
    is supplied, we MUST NOT silently return rows for all of the user's
    accounts — that previously caused a single counterparty to absorb
    spend from every Snap/Meta account and ballooned "today's spend"
    by 10×–100× on the dashboard. We now SKIP scoped sources when the
    counterparty has no external id; only unscoped sources (scope_field
    is None) are read. If none qualify, return `[]` with the first
    source name so the caller can surface a "missing external id"
    warning.
    """
    sources = PROVIDER_SOURCES.get(provider, [])
    for src in sources:
        col_name = src["collection"]
        scope_field = src["scope_field"]
        # Iter-163 — skip per-account sources when the counterparty has
        # no external id; falling through would aggregate spend across
        # ALL of the user's ad accounts on this provider.
        if scope_field and not external_id:
            continue
        q = {
            "user_id": user_id,
            "date": {"$gte": from_date, "$lte": to_date},
        }
        if scope_field and external_id:
            q[scope_field] = external_id
        rows: dict[str, float] = {}
        any_row = False
        source_rows_seen = False
        projection = {"_id": 0, "date": 1, "spend": 1}
        if col_name == "snapchat_account_daily":
            projection.update({
                "accounting_eligible": 1,
                "accounting_spend_snapshot": 1,
            })
        async for row in db[col_name].find(q, projection):
            source_rows_seen = True
            d = row.get("date")
            if not d:
                continue
            spend_value = row.get("spend")
            if (
                col_name == "snapchat_account_daily"
                and row.get("accounting_eligible") is False
            ):
                spend_value = row.get("accounting_spend_snapshot")
                if spend_value is None:
                    continue
            any_row = True
            rows[d] = rows.get(d, 0.0) + float(spend_value or 0)
        if any_row:
            ordered = [{"date": d, "spend": round(s, 2)} for d, s in sorted(rows.items())]
            return ordered, col_name
        if source_rows_seen and scope_field:
            # A scoped source existed but every row was analytics-only. Do
            # not fall through to an unscoped legacy aggregate that could
            # leak another Snapchat account's spend into this counterparty.
            return [], col_name
    return [], (sources[0]["collection"] if sources else "")


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
    # Iter-175 — PERMANENT FIX (extends Iter-174):
    # The Iter-174 walk treated `topup` events as "balance += amount" only,
    # which DOUBLE-COUNTED debt whenever a topup paid down an existing
    # liability (because the topup endpoint allocates part of the cash to
    # debt and part to balance, but the walk ignored the debt portion).
    # This caused balance AND debt to inflate on every half-hour cron
    # sync — repeatedly reported on Snap/Meta cards.
    #
    # The walk now mirrors the actual `POST /topup` endpoint logic:
    #   topup   → pay off outstanding debt_walk first, remainder ↑ balance
    #   opening → ↑ balance (openings are pure asset posting; debt openings
    #             come from a separate ad_account_opening liability row)
    #   spend (positive) → cover from balance, rest → debt
    #   spend (negative, correction) → unwind debt first, refund balance
    #   settlement / writeoff → debt -= amount
    balance_walk = 0.0
    debt_walk = 0.0
    total_spend = 0.0
    last_events = {"topup": None, "spend": None, "debt": None}

    async for row in db.ad_account_ledger.find(
        {"user_id": user_id, "counterparty_id": cp["id"]},
        {"_id": 0},
    ).sort([("date", 1), ("created_at", 1)]):
        ev = row.get("type")
        amt = float(row.get("amount") or 0)
        if ev == "topup":
            # Iter-175 — mirror /topup endpoint: pay debt first, then balance.
            to_debt = min(debt_walk, amt) if amt > 0 else 0.0
            debt_walk = max(0.0, debt_walk - to_debt)
            balance_walk += (amt - to_debt)
            last_events["topup"] = row
        elif ev == "opening":
            balance_walk += amt
            last_events["topup"] = row
        elif ev == "spend":
            total_spend += amt
            if amt >= 0:
                covered = min(balance_walk, amt)
                balance_walk -= covered
                debt_walk += (amt - covered)
            else:
                refund = -amt
                if debt_walk > 0:
                    unwind = min(debt_walk, refund)
                    debt_walk -= unwind
                    refund -= unwind
                balance_walk += refund
            last_events["spend"] = row
        elif ev == "settlement":
            debt_walk = max(0.0, debt_walk - amt)
        elif ev == "writeoff":
            debt_walk = max(0.0, debt_walk - amt)
        elif ev == "debt":
            last_events["debt"] = row

    # Iter-160 — apply posted general_ledger adjustments on top of the
    # ledger-derived debt (settlement/writeoff/adjustment).
    adj_debit = 0.0
    adj_credit = 0.0
    async for row in db.general_ledger.aggregate([
        {"$match": {"user_id": user_id, "entity_type": "ad_account",
                     "entity_id": cp["id"], "status": "posted",
                     "entry_type": {"$in": ["settlement", "writeoff",
                                              "adjustment"]}}},
        {"$group": {"_id": "$side", "total": {"$sum": "$amount"}}},
    ]):
        if row["_id"] == "debit":
            adj_debit = float(row.get("total") or 0)
        elif row["_id"] == "credit":
            adj_credit = float(row.get("total") or 0)
    debt_walk = max(0.0, debt_walk - adj_debit + adj_credit)

    # Reference the auto-cron liability id (if any) so the UI's
    # «liability detail» link still works.
    debt = await _current_open_debt(db, user_id, cp["id"])

    return {
        "id": cp["id"],
        "name": cp["name"],
        "ad_provider": cp.get("ad_provider"),
        "external_account_id": cp.get("external_account_id"),
        "balance": _round(balance_walk),
        "debt_mode": cp.get("debt_mode") or "auto",
        "open_debt": _round(debt_walk),
        "open_debt_id": debt["id"] if debt else None,
        "total_spend": _round(total_spend),
        "last_topup": last_events["topup"],
        "last_spend": last_events["spend"],
        "last_debt": last_events["debt"],
        "last_auto_sync_date": cp.get("last_auto_sync_date"),
        "notes": cp.get("notes"),
        "credit_limit": cp.get("credit_limit"),
        "alert_threshold_pct": cp.get("alert_threshold_pct"),
        "adjustments_total_debit": _round(adj_debit),
        "adjustments_total_credit": _round(adj_credit),
        # Iter-174 — expose the cached value for debugging/comparison.
        "_cached_balance": _round(cp.get("balance") or 0),
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
    doc.pop("_id", None)
    return doc


async def _post_spend_to_ledger(
    db, *, user_id: str, actor_name: str, cp: dict,
    amount: float, spend_date: str, source: str,
    description: str = "", notes: str = "",
    extra_metadata: Optional[dict] = None,
) -> dict:
    """Iter-205 — Post a daily ad-account spend as a balanced txn_group
    into `general_ledger` (Universal Ledger SSOT).

    Accounting model (per merchant spec):
        Σ debits == Σ credits
        DEBIT  expense.advertising  = spend                (مصروف إعلاني)
        CREDIT ad_account.balance   = min(spend, prepaid)  (يستهلك الرصيد)
        CREDIT ad_account.debt      = spend − prepaid      (مديونية ↑)

    Idempotency:
        key = "spend:{cp_id}:{ad_provider}:{spend_date}:{source}:{amount}"
        Stored in metadata.idempotency_key. Same key found ⇒ skip
        with `{ok: True, skipped: True, txn_group_id: <existing>}`.
        Protects the half-hour cron, sync-from-platform, and accidental
        double-clicks on the manual /spend button.
    """
    from ledger_core import post_txn_group as _ptg, compute_balance as _cb

    amount = round(float(amount or 0), 2)
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "non_positive"}

    ad_provider = (cp.get("ad_provider") or "").strip() or "unknown"
    idem_key = (
        f"spend:{cp['id']}:{ad_provider}:{spend_date}:{source}"
        f":{amount:.2f}"
    )
    existing = await db.general_ledger.find_one(
        {"user_id": user_id,
         "metadata.idempotency_key": idem_key,
         "status": "posted"},
        {"_id": 0, "txn_group_id": 1},
    )
    if existing:
        return {"ok": True, "skipped": True,
                "txn_group_id": existing.get("txn_group_id"),
                "reason": "idempotent_duplicate"}

    # Compute LIVE prepaid balance from the universal ledger (NOT the
    # legacy `counterparties.balance` field which can drift).
    bal = await _cb(
        db, user_id=user_id, entity_type="ad_account",
        entity_id=cp["id"], sub_account="balance",
    )
    prepaid_live = max(0.0, round(float(bal.get("net_balance") or 0), 2))

    covered = round(min(amount, prepaid_live), 2)
    uncovered = round(amount - covered, 2)

    entries = [{
        "entity_type": "expense", "entity_id": "advertising",
        "side": "debit", "amount": amount,
        "entry_type": "expense_record",
        "notes": f"مصروف إعلانات — {cp.get('name')}",
        "metadata": {"category": "advertising",
                     "ad_account_id": cp["id"],
                     "ad_account_name": cp.get("name")},
    }]
    if covered > 0:
        entries.append({
            "entity_type": "ad_account", "entity_id": cp["id"],
            "sub_account": "balance", "side": "credit",
            "amount": covered, "entry_type": "spend",
            "notes": f"استهلاك رصيد مدفوع مسبقاً — {cp.get('name')}",
        })
    if uncovered > 0:
        entries.append({
            "entity_type": "ad_account", "entity_id": cp["id"],
            "sub_account": "debt", "side": "credit",
            "amount": uncovered, "entry_type": "spend",
            "notes": f"مديونية إعلانية جديدة — {cp.get('name')}",
        })

    group = await _ptg(
        db, user_id=user_id, actor_id=user_id, actor_name=actor_name,
        txn_type="ad_account_spend",
        notes=description or f"صرف يومي — {cp.get('name')}",
        metadata={
            "ad_account_id": cp["id"],
            "ad_account_name": cp.get("name"),
            "ad_provider": ad_provider,
            "spend_date": spend_date,
            "source": source,
            "amount": amount,
            "covered": covered,
            "uncovered": uncovered,
            "idempotency_key": idem_key,
            "iter": "iter205",
            **(extra_metadata or {}),
        },
        entries=entries,
    )
    return {
        "ok": True, "skipped": False,
        "txn_group_id": group.get("txn_group_id"),
        "covered": covered, "uncovered": uncovered,
        "amount": amount,
    }



async def _post_bank_tx(db, user_id: str, *,
                        account_id: str, amount: float, direction: str,
                        transaction_date: str, description: str) -> dict:
    """Use the existing accounts ledger for any cash movement.

    NOTE: ad-account topups are NOT an Iter-240 leak site. The
    `/topup` route already posts a balanced general_ledger pair
    (bank credit + ad_account debit, `entry_type="topup"`) directly,
    so mirroring here would double-count. Do not add a double-write
    helper call to this function.
    """
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
    # Iter-205 — idempotency on spend SSOT entries. Partial index so
    # only general_ledger rows that carry an idempotency_key are
    # indexed (cheap & unique per user).
    try:
        await db.general_ledger.create_index(
            [("user_id", 1), ("metadata.idempotency_key", 1)],
            name="gl_user_idem",
            partialFilterExpression={
                "metadata.idempotency_key": {"$exists": True}},
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

    # ── GET /diagnose (Iter-110) — read-only data-source diagnostic ──
    # NOTE: this MUST be registered before `GET /{cp_id}` otherwise
    # FastAPI matches "diagnose" as a counterparty id.
    @router.get("/diagnose")
    async def diagnose_data_sources(user: dict = Depends(current_user)):
        """Health check that pinpoints WHY an ad-account isn't syncing.

        For each ad-account counterparty: returns its
        external_account_id, and for the matching provider collection
        the set of ad_account_id values the source has rows for. The
        merchant can compare visually — if the counterparty's value
        isn't in the "available" list, the sync will return 0.
        """
        uid = user["id"]
        out_accounts = []

        # Pre-fetch the distinct external IDs per source collection for
        # this user. This is what the sync code will actually filter on.
        avail = {}  # {collection: {scope_field, distinct_ids, total_rows, sample_dates}}
        for provider, sources in PROVIDER_SOURCES.items():
            for src in sources:
                col = src["collection"]
                scope = src["scope_field"]
                if col in avail:
                    continue
                ids = []
                if scope:
                    ids = await db[col].distinct(scope, {"user_id": uid})
                rows = await db[col].count_documents({"user_id": uid})
                sample = []
                async for d in db[col].find(
                    {"user_id": uid}, {"_id": 0, "date": 1, "spend": 1},
                ).sort([("date", -1)]).limit(3):
                    sample.append({"date": d.get("date"), "spend": d.get("spend")})
                avail[col] = {
                    "scope_field": scope, "distinct_ids": ids,
                    "total_rows": rows, "sample_recent": sample,
                }

        async for cp in db.counterparties.find(
            {"user_id": uid, "kind": "ad_account"}, {"_id": 0},
        ).sort([("name", 1)]):
            provider = cp.get("ad_provider")
            ext_id = (cp.get("external_account_id") or "").strip() or None
            sources = PROVIDER_SOURCES.get(provider, [])
            per_source_status = []
            for src in sources:
                col = src["collection"]
                src_data = avail.get(col, {})
                distinct_ids = src_data.get("distinct_ids") or []
                match_ok = (
                    ext_id is not None
                    and src["scope_field"]
                    and ext_id in distinct_ids
                )
                # When the source has NO scope field, any data counts as
                # "could be picked up" but with the multi-account risk.
                if src["scope_field"] is None and src_data.get("total_rows", 0) > 0:
                    match_ok = True
                per_source_status.append({
                    "collection": col,
                    "scope_field": src["scope_field"],
                    "available_ids": distinct_ids[:10],  # cap
                    "total_rows_in_source": src_data.get("total_rows", 0),
                    "your_external_id_matches": match_ok,
                    "sample_recent": src_data.get("sample_recent", []),
                })

            # Heuristic diagnosis text
            problems = []
            if not provider or provider not in PROVIDER_SOURCES:
                problems.append(f"المنصة `{provider}` غير مدعومة للمزامنة التلقائية.")
            elif not ext_id and any(s["scope_field"] for s in sources):
                problems.append(
                    "هذا الحساب لا يحتوي `external_account_id`. اضغط ⚙️ الإعدادات على البطاقة وأضف Ad Account ID."
                )
            elif ext_id and not any(s["your_external_id_matches"] for s in per_source_status):
                # Suggest likely IDs
                guesses = []
                for s in per_source_status:
                    for x in s["available_ids"]:
                        if x not in guesses:
                            guesses.append(x)
                problems.append(
                    f"الـ external_account_id الذي أدخلته (`{ext_id}`) لا يطابق أي ID موجود في بيانات الصرف. " +
                    (f"الـ IDs المتاحة فعلياً: {guesses[:5]}" if guesses else
                     "لا توجد بيانات صرف مخزّنة لهذه المنصة — اربط الـ Snapchat OAuth أو فعّل Make.com webhook أولاً.")
                )
            elif ext_id and all(
                s["total_rows_in_source"] == 0 for s in per_source_status
            ):
                problems.append("لا توجد بيانات صرف مخزّنة لهذه المنصة (التراكم اليومي = 0). اربط مصدر البيانات (OAuth أو Make.com).")

            out_accounts.append({
                "id": cp["id"], "name": cp.get("name"),
                "ad_provider": provider, "external_account_id": ext_id,
                "balance": _round(cp.get("balance") or 0),
                "last_auto_sync_date": cp.get("last_auto_sync_date"),
                "debt_mode": cp.get("debt_mode") or "auto",
                "per_source_status": per_source_status,
                "diagnosis": problems or ["لا توجد مشكلة واضحة — المزامنة يجب أن تعمل."],
                "healthy": not problems,
            })
        return {
            "user_id": uid,
            "checked_at": _now(),
            "accounts": out_accounts,
            "sources_overview": avail,
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

    # ── Iter-159i — PUT /{id}/credit-limit ────────────────────────────
    # Per-ad-account configurable credit limit + alert threshold.
    # `credit_limit`        — max debt allowed (SAR).  None ⇒ unlimited.
    # `alert_threshold_pct` — % of limit at which the "debt is about to
    #                         max out" alert is generated.  Default 80.
    @router.put("/{cp_id}/credit-limit")
    async def set_credit_limit(
        cp_id: str, payload: CreditLimitIn,
        user: dict = Depends(current_user),
    ):
        await _get_account(db, user["id"], cp_id)
        upd: dict = {"updated_at": _now()}
        if payload.credit_limit is not None:
            upd["credit_limit"] = float(payload.credit_limit)
        if payload.alert_threshold_pct is not None:
            upd["alert_threshold_pct"] = float(payload.alert_threshold_pct)
        if len(upd) == 1:
            raise HTTPException(400, "أرسل على الأقل أحد الحقلين: "
                                       "credit_limit أو alert_threshold_pct")
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]}, {"$set": upd},
        )
        cp = await _get_account(db, user["id"], cp_id)
        return await _summarise(db, user["id"], cp)

    # ── Iter-160 — Accounting Adjustments (replaces reset-debt/recompute-debt)
    # Three operations available via the Universal Ledger:
    #   • settlement: تسوية مديونية (reduces outstanding debt)
    #   • writeoff:   شطب رصيد معتمد (reduces outstanding debt)
    #   • adjustment: قيد تعديل عام (موجب أو سالب)
    # All adjustments require a `reason_code` from REASON_CODES.
    # All adjustments are POSTED entries — they NEVER delete or modify
    # existing ledger entries. Full audit trail in accounting_audit_log.
    #
    # To "undo" a single ledger entry, use POST /ledger/entries/{id}/reverse.
    # The OLD reset-debt and recompute-debt endpoints have been removed
    # because they violated double-entry accounting (DELETE on a ledger).

    @router.post("/{cp_id}/adjustments")
    async def create_adjustment(
        cp_id: str,
        payload: dict,
        user: dict = Depends(current_user),
    ):
        """Create a settlement / writeoff / generic adjustment against
        an ad-account's outstanding debt. Writes a posted entry to
        general_ledger + an audit row.

        body: {
            kind: "settlement"|"writeoff"|"adjustment",
            amount: float (>0),
            direction: "reduce_debt"|"increase_debt" (default reduce_debt),
            reason_code: str (required, from /api/ledger/reason-codes),
            notes: str (optional)
        }
        """
        from ledger_core import (
            REASON_CODES as _RC,
            post_ledger_entry as _post_le,
        )
        cp = await _get_account(db, user["id"], cp_id)
        kind = (payload or {}).get("kind")
        if kind not in ("settlement", "writeoff", "adjustment"):
            raise HTTPException(400, "kind غير صحيح")
        try:
            amount = float(payload.get("amount") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "amount غير صحيح")
        if amount <= 0:
            raise HTTPException(400, "amount يجب أن يكون موجباً")
        direction = payload.get("direction") or "reduce_debt"
        if direction not in ("reduce_debt", "increase_debt"):
            raise HTTPException(400, "direction غير صحيح")
        reason_code = payload.get("reason_code") or ""
        if not reason_code:
            raise HTTPException(400, "reason_code إلزامي")
        if reason_code not in _RC:
            raise HTTPException(400, f"reason_code غير معتمد: {reason_code}")
        notes = payload.get("notes") or ""

        # reduce_debt → debit (lowers the credit side, i.e. lowers debt)
        # increase_debt → credit (raises debt)
        side = "debit" if direction == "reduce_debt" else "credit"
        entry = await _post_le(
            db, user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entity_type="ad_account", entity_id=cp_id,
            entry_type=kind, amount=amount, side=side,
            reason_code=reason_code, notes=notes,
            metadata={"direction": direction,
                      "ad_provider": cp.get("ad_provider"),
                      "counterparty_name": cp.get("name")},
            status="posted",
        )
        entry.pop("_id", None)
        return {"ok": True, "entry": entry,
                "account": await _summarise(db, user["id"], cp)}

    @router.get("/{cp_id}/audit-log")
    async def account_audit_log(
        cp_id: str, limit: int = 100,
        user: dict = Depends(current_user),
    ):
        await _get_account(db, user["id"], cp_id)
        cur = db.accounting_audit_log.find(
            {"user_id": user["id"], "entity_type": "ad_account",
             "entity_id": cp_id}, {"_id": 0},
        ).sort("timestamp", -1).limit(int(limit or 100))
        items = await cur.to_list(int(limit or 100))
        return {"items": items}

    @router.get("/{cp_id}/adjustment-entries")
    async def account_adjustment_entries(
        cp_id: str, limit: int = 200,
        user: dict = Depends(current_user),
    ):
        """List general_ledger entries for this ad account (all entry
        types: settlement, writeoff, adjustment, reversal). Useful for
        a 'view history' drawer in the UI."""
        await _get_account(db, user["id"], cp_id)
        cur = db.general_ledger.find(
            {"user_id": user["id"], "entity_type": "ad_account",
             "entity_id": cp_id}, {"_id": 0},
        ).sort("entry_no", -1).limit(int(limit or 200))
        items = await cur.to_list(int(limit or 200))
        return {"items": items}


    # ── GET /diagnostics/sync-health (Iter-211) — staleness diagnosis ─
    @router.get("/diagnostics/sync-health")
    async def sync_health(user: dict = Depends(current_user)):
        """For each ad-account, returns the most recent platform-data
        row (from snapchat_account_daily / meta_ads_daily /
        tiktok_ads_daily) AND the last successful cron application.
        Used by the UI to flag accounts where Make.com (or whichever
        upstream) stopped delivering data."""
        from datetime import datetime, date as _date, timezone as _tz
        accs = await db.counterparties.find(
            {"user_id": user["id"], "kind": "ad_account"},
            {"_id": 0, "id": 1, "name": 1, "ad_provider": 1,
             "external_account_id": 1, "platform_account_ids": 1,
             "sync_via": 1},
        ).to_list(500)
        today = _date.today()
        results = []
        for cp in accs:
            provider = (cp.get("ad_provider") or "").lower()
            sources = PROVIDER_SOURCES.get(provider, [])
            scope_ids = list(
                set(filter(None, [
                    cp.get("external_account_id"),
                    *(cp.get("platform_account_ids") or []),
                ]))
            )
            latest = None
            latest_received_at = None
            collection_used = None
            for src in sources:
                # Iter-212b — Mirror the cross-account safety guard from
                # `_fetch_daily_spend` (Iter-163): never query a scoped
                # collection without a concrete external id, or we'd
                # pull rows belonging to OTHER ad accounts and report
                # bogus freshness.
                if src.get("scope_field") and not scope_ids:
                    continue
                coll = db[src["collection"]]
                q: dict = {"user_id": user["id"]}
                if src.get("scope_field") and scope_ids:
                    q[src["scope_field"]] = {"$in": scope_ids}
                # Production schema uses `date` (not `spend_date`).
                doc = await coll.find_one(
                    q, sort=[("date", -1), ("received_at", -1)],
                )
                if doc:
                    sd = (doc.get("date") or doc.get("spend_date"))
                    rec = doc.get("received_at") or doc.get("created_at")
                    if not latest or (sd and sd > latest):
                        latest = sd
                        latest_received_at = rec
                        collection_used = src["collection"]
            days_stale = None
            if latest:
                try:
                    parsed = (
                        _date.fromisoformat(latest)
                        if isinstance(latest, str) else latest
                    )
                    days_stale = (today - parsed).days
                except Exception:
                    days_stale = None
            # Iter-212 — Staleness thresholds depend on the schedule:
            #   • Direct-API (Snap/Meta) → expect daily freshness.
            #   • Make.com (TikTok/...)  → 5h cycle, allow 2-day grace.
            sync_via = (
                "make_com"
                if (cp.get("sync_via") == "make_com"
                    or provider not in HALFHOUR_SYNC_PROVIDERS)
                else "direct_api"
            )
            if days_stale is None:
                status = "no_data"
            elif sync_via == "make_com":
                if days_stale <= 1:
                    status = "healthy"
                elif days_stale <= 2:
                    status = "warning"
                else:
                    status = "stale"
            else:  # direct_api
                if days_stale <= 1:
                    status = "healthy"
                elif days_stale <= 3:
                    status = "warning"
                else:
                    status = "stale"
            results.append({
                "id": cp["id"],
                "name": cp.get("name"),
                "ad_provider": provider,
                # Iter-212 — show which schedule owns each account so
                # the UI tooltip mentions the right expected freshness.
                "sync_via": sync_via,
                "expected_interval": (
                    "كل 30 دقيقة (API مباشر)"
                    if sync_via == "direct_api"
                    else "كل 5 ساعات (Make.com)"
                ),
                "last_spend_date": latest,
                "last_received_at": (
                    latest_received_at.isoformat()
                    if isinstance(latest_received_at, datetime)
                    else latest_received_at
                ),
                "days_stale": days_stale,
                "source_collection": collection_used,
                "status": status,
            })
        results.sort(key=lambda x: (
            {"stale": 0, "warning": 1, "no_data": 2, "healthy": 3}[x["status"]],
            -(x["days_stale"] or 0),
        ))
        return {
            "as_of": today.isoformat(),
            "accounts": results,
            "summary": {
                "total": len(results),
                "healthy": sum(1 for r in results if r["status"] == "healthy"),
                "warning": sum(1 for r in results if r["status"] == "warning"),
                "stale": sum(1 for r in results if r["status"] == "stale"),
                "no_data": sum(1 for r in results if r["status"] == "no_data"),
            },
        }

    # ── POST /{id}/topup ──────────────────────────────────────────────
    @router.post("/{cp_id}/topup")
    async def topup(
        cp_id: str, payload: TopupIn,
        user: dict = Depends(current_user),
    ):
        cp = await _get_account(db, user["id"], cp_id)
        amount = _round(payload.amount)

        # ─── Iter-203 — P0 Fix: SSOT Asset Transfer ──────────────────
        # Top-up is a transfer between TWO assets (bank → ad-account
        # prepaid balance). It must NEVER be recorded as an expense.
        #
        # We enforce:
        #   1) Source bank/cash must have enough live balance.
        #   2) general_ledger receives a balanced 2-leg entry:
        #         DEBIT  ad_account.balance  (asset ↑)
        #         CREDIT bank.main           (asset ↓)
        #      so the bank statement (Iter-198 SSOT) AND the financial
        #      position page (Iter-192) reflect the deduction live.
        from universal_accounting_routes import (
            _enforce_sufficient_funds,
            _ensure_opening_balance_seeded,
        )
        from ledger_core import post_txn_group as _ptg
        await _enforce_sufficient_funds(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id, amount=amount,
        )

        # 1) Deduct from bank (legacy account_transactions row — kept
        #    for non-migrated bank UI compatibility).
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

        ledger_doc = await _ledger_write(
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
        ledger_id = ledger_doc.get("id")

        # 4) Iter-203 — SSOT double-entry into general_ledger.
        #    Seed bank opening_balance lazily for non-migrated banks so
        #    the ledger sum stays consistent with the displayed balance.
        await _ensure_opening_balance_seeded(
            db, user_id=user["id"],
            account_id=payload.paid_from_account_id,
        )
        bank_acc = await db.accounts.find_one(
            {"id": payload.paid_from_account_id, "user_id": user["id"]},
            {"_id": 0, "name": 1, "account_type": 1},
        ) or {}
        gl_group = await _ptg(
            db, user_id=user["id"], actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            txn_type="ad_account_topup",
            notes=f"تعبئة رصيد إعلاني — {cp['name']}",
            metadata={
                "ad_account_id": cp_id,
                "ad_account_name": cp.get("name"),
                "ad_provider": cp.get("ad_provider"),
                "bank_account_id": payload.paid_from_account_id,
                "bank_account_name": bank_acc.get("name"),
                "to_debt": _round(amount_to_debt),
                "to_balance": _round(amount_to_balance),
                "legacy_tx_id": tx["id"],
                "legacy_ledger_id": ledger_id,  # iter-218 lookup key
                "iter": "iter203",
            },
            entries=[
                {"entity_type": "ad_account", "entity_id": cp_id,
                 "sub_account": "balance", "side": "debit",
                 "amount": amount, "entry_type": "topup",
                 "notes": f"تعبئة من {bank_acc.get('name') or 'البنك'}"},
                {"entity_type": "bank",
                 "entity_id": payload.paid_from_account_id,
                 "sub_account": "main", "side": "credit",
                 "amount": amount, "entry_type": "topup",
                 "notes": f"تعبئة الحساب الإعلاني — {cp['name']}"},
            ],
        )

        cp_fresh = await _get_account(db, user["id"], cp_id)
        return {
            "ok": True,
            "amount": amount,
            "applied_to_debt": _round(amount_to_debt),
            "applied_to_balance": _round(amount_to_balance),
            "ad_account": await _summarise(db, user["id"], cp_fresh),
            "ledger_txn_group_id": gl_group.get("txn_group_id"),
        }

    # ── PUT /{cp_id}/topup/{ledger_id} — edit existing topup (Iter-112) ─
    @router.put("/{cp_id}/topup/{ledger_id}")
    async def edit_topup(
        cp_id: str, ledger_id: str, payload: TopupEditIn,
        user: dict = Depends(current_user),
    ):
        """Edit amount and/or date of a previous topup. Strategy:
          1. Reverse the original (return cash to bank, revert cp.balance
             and any liability deduction).
          2. Re-apply with the new amount/date using the same logic as
             POST /topup. Re-uses the existing bank-tx and ledger ids so
             foreign references stay intact.
        """
        cp = await _get_account(db, user["id"], cp_id)
        old = await db.ad_account_ledger.find_one(
            {"id": ledger_id, "user_id": user["id"], "counterparty_id": cp_id},
            {"_id": 0},
        )
        if not old:
            raise HTTPException(404, "حركة غير موجودة")
        if old.get("type") != "topup":
            raise HTTPException(400, "هذه العملية ليست تعبئة — لا يمكن تعديلها من هنا.")

        old_amount = float(old.get("amount") or 0)
        old_brk = old.get("breakdown") or {}
        old_to_balance = float(old_brk.get("to_balance") or 0)
        old_to_debt = float(old_brk.get("to_debt") or 0)
        bank_id = old.get("account_id")
        # Iter-112 — handle both old (related_tx_id) and new
        # (related_transaction_id) field names; live data uses the
        # latter via _ledger_write but defensive lookup makes the
        # endpoint robust to future renames.
        old_tx_id = old.get("related_transaction_id") or old.get("related_tx_id")
        old_liab_id = old.get("related_liability_id")

        # Iter-218 — SSOT: locate and reverse the original general_ledger
        # group for this topup so the universal ledger stays in sync.
        # Lookup priority: new (legacy_ledger_id) → legacy (legacy_tx_id).
        # Skipped silently for legacy edits where no SSOT group was ever
        # posted (topups created before Iter-203). Reversal uses the same
        # atomic loop as /api/ledger/groups/{id}/reverse.
        from ledger_core import reverse_entry as _rev_entry
        ssot_lookup = (
            {"user_id": user["id"], "metadata.legacy_ledger_id": ledger_id}
            if ledger_id else None
        )
        ssot_doc = (
            await db.general_ledger.find_one(ssot_lookup, {"_id": 0,
                "txn_group_id": 1}) if ssot_lookup else None
        )
        if not ssot_doc and old_tx_id:
            ssot_doc = await db.general_ledger.find_one(
                {"user_id": user["id"], "metadata.legacy_tx_id": old_tx_id},
                {"_id": 0, "txn_group_id": 1},
            )
        old_ssot_group_id = ssot_doc and ssot_doc.get("txn_group_id")
        if old_ssot_group_id:
            ssot_legs = await db.general_ledger.find(
                {"user_id": user["id"], "txn_group_id": old_ssot_group_id,
                 "status": "posted"},
                {"_id": 0, "id": 1},
            ).to_list(length=20)
            for _leg in ssot_legs:
                await _rev_entry(
                    db, user_id=user["id"], actor_id=user["id"],
                    actor_name=(user.get("name")
                                or user.get("email") or ""),
                    entry_id=_leg["id"],
                    reason_code="data_entry_error",
                    notes=f"تعديل تعبئة — iter-218 (مجموعة سابقة {old_ssot_group_id})",
                )

        # 1) Reverse the cp.balance change
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]},
            {"$inc": {"balance": -old_to_balance},
             "$set": {"updated_at": _now()}},
        )

        # 2) Reverse the liability paid_amount change (if any)
        if old_liab_id and old_to_debt > 0:
            existing = await db.liabilities.find_one(
                {"id": old_liab_id, "user_id": user["id"]}, {"_id": 0},
            )
            if existing:
                restored_paid = round(
                    max(0.0, float(existing.get("paid_amount") or 0) - old_to_debt), 2,
                )
                new_status = ("paid" if restored_paid + 0.01 >= float(existing.get("expected_amount") or 0)
                              else ("partial" if restored_paid > 0 else "unpaid"))
                await db.liabilities.update_one(
                    {"id": old_liab_id, "user_id": user["id"]},
                    {"$set": {"paid_amount": restored_paid,
                              "status": new_status,
                              "updated_at": _now()}},
                )

        # 3) Reverse the bank transaction
        if old_tx_id:
            await db.account_transactions.delete_one(
                {"id": old_tx_id, "user_id": user["id"]},
            )
            from accounts_routes import _recompute_balance
            await _recompute_balance(db, user["id"], bank_id)

        # 4) Re-apply with new values (or original where unspecified)
        new_amount = _round(payload.amount if payload.amount is not None else old_amount)
        new_date = payload.transaction_date or old.get("date") or _now()[:10]
        new_desc = payload.description if payload.description is not None else old.get("description", "")
        new_notes = payload.notes if payload.notes is not None else old.get("notes", "")

        # Re-deduct from bank (same tx id so external refs survive)
        new_tx_id = str(uuid.uuid4())
        await db.account_transactions.insert_one({
            "id": new_tx_id, "user_id": user["id"],
            "account_id": bank_id, "amount": new_amount, "direction": "out",
            "transaction_type": "ad_account_topup",
            "transaction_date": new_date,
            "description": new_desc or f"تعبئة رصيد {cp['name']}",
            "created_at": _now(),
        })
        from accounts_routes import _recompute_balance  # noqa: F811
        await _recompute_balance(db, user["id"], bank_id)

        # Re-apply debt allocation
        cp_after_reverse = await _get_account(db, user["id"], cp_id)
        debt = await _current_open_debt(db, user["id"], cp_id)
        amount_to_debt = 0.0
        amount_to_balance = new_amount
        liab_after = 0.0
        liab_id = None
        if debt:
            outstanding = _round(
                (debt.get("expected_amount") or 0) - (debt.get("paid_amount") or 0)
            )
            amount_to_debt = min(new_amount, outstanding)
            amount_to_balance = _round(new_amount - amount_to_debt)
            new_paid = _round((debt.get("paid_amount") or 0) + amount_to_debt)
            new_status = "paid" if new_paid + 0.01 >= float(debt["expected_amount"]) else "partial"
            await db.liabilities.update_one(
                {"id": debt["id"], "user_id": user["id"]},
                {"$set": {"paid_amount": new_paid, "status": new_status,
                          "updated_at": _now()}},
            )
            liab_id = debt["id"]
            liab_after = _round(float(debt["expected_amount"]) - new_paid)

        new_balance = _round((cp_after_reverse.get("balance") or 0) + amount_to_balance)
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": user["id"]},
            {"$set": {"balance": new_balance, "updated_at": _now()}},
        )

        # 5) Update (not recreate) the same ledger entry to preserve id
        await db.ad_account_ledger.update_one(
            {"id": ledger_id, "user_id": user["id"]},
            {"$set": {
                "amount": new_amount,
                "balance_after": new_balance,
                "debt_after": liab_after,
                "related_liability_id": liab_id,
                "related_transaction_id": new_tx_id,
                "description": new_desc or f"تعبئة من بنك ← {cp['name']}",
                "notes": new_notes,
                "breakdown": {
                    "to_debt": _round(amount_to_debt),
                    "to_balance": _round(amount_to_balance),
                    "edited_at": _now(),
                    "previous_amount": old_amount,
                },
                "date": new_date,
                "edited_at": _now(),
            }},
        )

        # 6) Iter-218 — Post a fresh SSOT group reflecting the edit so
        # general_ledger mirrors counterparties.balance and bank net.
        from ledger_core import post_txn_group as _ptg
        new_ssot_group_id = None
        if bank_id:
            bank_acc = await db.accounts.find_one(
                {"id": bank_id, "user_id": user["id"]},
                {"_id": 0, "name": 1},
            ) or {}
            gl_group = await _ptg(
                db, user_id=user["id"], actor_id=user["id"],
                actor_name=(user.get("name")
                            or user.get("email") or ""),
                txn_type="ad_account_topup",
                notes=(
                    f"تعبئة رصيد إعلاني — {cp['name']} "
                    f"(معدّلة من {_round(old_amount)} إلى "
                    f"{_round(new_amount)})"
                ),
                metadata={
                    "ad_account_id": cp_id,
                    "ad_account_name": cp.get("name"),
                    "ad_provider": cp.get("ad_provider"),
                    "bank_account_id": bank_id,
                    "bank_account_name": bank_acc.get("name"),
                    "to_debt": _round(amount_to_debt),
                    "to_balance": _round(amount_to_balance),
                    "legacy_tx_id": new_tx_id,
                    "legacy_ledger_id": ledger_id,
                    "edited_from_amount": _round(old_amount),
                    "previous_ssot_group_id": old_ssot_group_id,
                    "iter": "iter218",
                },
                entries=[
                    {"entity_type": "ad_account", "entity_id": cp_id,
                     "sub_account": "balance", "side": "debit",
                     "amount": new_amount, "entry_type": "topup",
                     "notes": f"تعبئة من {bank_acc.get('name') or 'البنك'}"},
                    {"entity_type": "bank", "entity_id": bank_id,
                     "sub_account": "main", "side": "credit",
                     "amount": new_amount, "entry_type": "topup",
                     "notes": f"تعبئة الحساب الإعلاني — {cp['name']}"},
                ],
            )
            new_ssot_group_id = gl_group.get("txn_group_id")

        cp_fresh = await _get_account(db, user["id"], cp_id)
        return {
            "ok": True,
            "amount": new_amount,
            "previous_amount": old_amount,
            "applied_to_debt": _round(amount_to_debt),
            "applied_to_balance": _round(amount_to_balance),
            "ad_account": await _summarise(db, user["id"], cp_fresh),
            "ssot_previous_group_id": old_ssot_group_id,
            "ssot_new_group_id": new_ssot_group_id,
        }
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

        # 4) Iter-205 — Universal Ledger SSOT entry.
        #    Triple/double-entry: debit expense.advertising, credit
        #    ad_account.balance (prepaid consumed) + ad_account.debt
        #    (uncovered portion). Idempotent by
        #    (cp_id, provider, date, source, amount).
        gl_result = await _post_spend_to_ledger(
            db, user_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            cp=cp, amount=amount, spend_date=payload.spend_date,
            source="manual",
            description=payload.description or "",
            notes=payload.notes or "",
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
            "ledger_txn_group_id": gl_result.get("txn_group_id"),
            "ledger_skipped": gl_result.get("skipped", False),
        }

    # ── POST /{id}/sync-from-platform (Iter-107) ──────────────────────
    @router.post("/{cp_id}/sync-from-platform")
    async def sync_from_platform(
        cp_id: str, payload: SyncFromPlatformIn,
        user: dict = Depends(current_user),
    ):
        cp = await _get_account(db, user["id"], cp_id)
        provider = cp.get("ad_provider")
        if provider not in PROVIDER_SOURCES:
            raise HTTPException(
                400,
                f"المزامنة التلقائية غير متاحة لمنصة {provider}. استخدم /spend يدوياً.",
            )
        # Iter-110 — use the provider-source map so Snapchat reads from
        # snapchat_account_daily (per-account), Meta from
        # meta_ads_daily.account_id, etc. Filter by external_account_id
        # when set on the counterparty so multi-account users get
        # independent per-account debt.
        ext_id = (cp.get("external_account_id") or "").strip() or None
        rows, _source = await _fetch_daily_spend(
            db, user["id"], provider, ext_id,
            payload.from_date, payload.to_date,
        )
        total_spend = _round(sum(r["spend"] for r in rows))
        days_seen = {r["date"] for r in rows if r["spend"] > 0}
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

    # ── POST /recover/recompute-debt-from-ledger (Iter-169) ─────────────
    @router.post("/{cp_id}/recover/recompute-debt-from-ledger")
    async def recompute_debt_from_ledger(
        cp_id: str,
        user: dict = Depends(current_user),
    ):
        """Repair endpoint for the «card shows stale debt after sync
        correction» bug (Iter-169).

        Walks `ad_account_ledger` chronologically to derive the TRUE
        cumulative spend for this account (treating positive amounts as
        spend, negative amounts as corrections). Then resets the open
        auto-generated liability to: `max(0, true_cumulative_spend −
        from_balance_total − topup_total)`. Result: the card's «المديونية»
        figure mirrors the audit log immediately.

        Idempotent — calling it twice in a row is a no-op.
        """
        uid = user["id"]
        cp = await db.counterparties.find_one(
            {"id": cp_id, "user_id": uid, "kind": "ad_account"},
            {"_id": 0},
        )
        if not cp:
            raise HTTPException(404, "الحساب غير موجود")

        # 1) Compute true totals from the audit-log ledger.
        # Iter-171b — also reconstruct the TRUE current balance by
        # walking the ledger row-by-row in chronological order. This
        # mirrors what the sync engine actually does and ensures the
        # card's «الرصيد» figure matches the merchant's reality, not a
        # stale cached value from earlier buggy syncs.
        spend_total = 0.0       # net spend (incl. corrections)
        covered_total = 0.0     # how much was covered out of balance
        topup_total = 0.0       # opening_balance + manual topups
        balance_walk = 0.0      # ← true balance derived from ledger
        debt_walk = 0.0         # ← true debt derived from ledger
        async for r in db.ad_account_ledger.find(
            {"user_id": uid, "counterparty_id": cp_id},
            {"_id": 0},
        ).sort([("date", 1), ("created_at", 1)]):
            ev = r.get("type")
            amt = float(r.get("amount") or 0)
            if ev == "topup":
                # Iter-175 — pay off debt first, then balance (mirrors
                # /topup endpoint to avoid double-counting).
                topup_total += amt
                to_debt = min(debt_walk, amt) if amt > 0 else 0.0
                debt_walk = max(0.0, debt_walk - to_debt)
                balance_walk += (amt - to_debt)
            elif ev == "opening":
                topup_total += amt
                balance_walk += amt
            elif ev == "spend":
                spend_total += amt
                if amt >= 0:
                    # Positive spend → consume balance first, rest → debt
                    covered = min(balance_walk, amt)
                    covered_total += covered
                    balance_walk -= covered
                    debt_walk += (amt - covered)
                else:
                    # Negative spend = correction. Refund to balance,
                    # then unwind debt (Iter-169 logic).
                    refund = -amt
                    if debt_walk > 0:
                        unwind = min(debt_walk, refund)
                        debt_walk -= unwind
                        refund -= unwind
                    balance_walk += refund
            elif ev == "settlement":
                # Cash payment that closes part of the debt
                debt_walk = max(0.0, debt_walk - amt)
            elif ev == "writeoff":
                debt_walk = max(0.0, debt_walk - amt)
        spend_total = round(spend_total, 2)
        balance_walk = round(balance_walk, 2)
        debt_walk = round(debt_walk, 2)

        # 2) True remaining debt = spend that wasn't covered by topups.
        # Use the walked value (more accurate than the simple subtract).
        true_debt = max(0.0, debt_walk)
        true_balance = max(0.0, balance_walk)

        # 3) Find the auto-generated open liability for this account.
        existing = await db.liabilities.find_one(
            {"user_id": uid, "counterparty_id": cp_id,
             "kind": "ad_account", "auto_generated": True,
             "source": "ad_account_cron",
             "status": {"$in": ["unpaid", "partial"]}},
            sort=[("created_at", 1)],
        )
        prev_open = 0.0
        if existing:
            prev_open = round(
                float(existing.get("expected_amount") or 0)
                - float(existing.get("paid_amount") or 0), 2)
            paid = float(existing.get("paid_amount") or 0)
            new_expected = round(true_debt + paid, 2)
            new_status = "paid" if true_debt < 0.01 else (
                "partial" if paid > 0 else "unpaid")
            await db.liabilities.update_one(
                {"id": existing["id"], "user_id": uid},
                {"$set": {
                    "expected_amount": new_expected,
                    "status": new_status,
                    "updated_at": _now(),
                    "recomputed_at": _now(),
                    "recompute_note": (
                        "إعادة احتساب من السجل (Iter-169) — "
                        f"كان {prev_open}، أصبح {true_debt}"),
                }},
            )
        elif true_debt > 0.01:
            # No existing liability but the ledger shows uncovered spend.
            # Create a new auto-generated liability so the card matches.
            new_id = str(uuid.uuid4())
            await db.liabilities.insert_one({
                "id": new_id, "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": true_debt, "paid_amount": 0.0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
                "description": ("مديونية معاد احتسابها من السجل (Iter-169)"),
                "due_date": (cp.get("last_auto_sync_date")
                              or _now()[:10]),
                "created_at": _now(), "updated_at": _now(),
            })

        # Iter-171b — also fix the cached counterparty.balance so the
        # card's «الرصيد» figure mirrors the ledger walk.
        await db.counterparties.update_one(
            {"id": cp_id, "user_id": uid},
            {"$set": {"balance": true_balance,
                       "updated_at": _now()}},
        )

        return {
            "ok": True,
            "counterparty_id": cp_id,
            "previous_open_debt": prev_open,
            "new_open_debt": true_debt,
            "delta": round(true_debt - prev_open, 2),
            # Iter-171b — also expose the recomputed balance
            "previous_balance": round(float(cp.get("balance") or 0), 2),
            "new_balance": true_balance,
            "balance_delta": round(
                true_balance - float(cp.get("balance") or 0), 2),
            "diagnostic": {
                "net_spend_from_ledger": spend_total,
                "topup_total": round(topup_total, 2),
                "covered_from_balance": round(covered_total, 2),
                "balance_walk_final": balance_walk,
                "debt_walk_final": debt_walk,
            },
        }

    # ── POST /sync-all (Iter-108) — manual trigger of the daily cron ──
    @router.post("/sync-all")
    async def sync_all_for_user(
        payload: SyncFromPlatformIn,
        user: dict = Depends(current_user),
    ):
        """Run sync-from-platform for EVERY ad account this user owns
        that has a supported provider (snapchat/tiktok/meta). The same
        endpoint is invoked by the daily cron at 23:55.

        When `force=true` the per-account idempotency guard
        (`last_auto_sync_date == to_date`) is bypassed — useful to
        recover from a previous failed/buggy sync that already
        stamped today's date."""
        results = await _run_sync_for_all(
            db, user["id"], payload.from_date, payload.to_date,
            force=payload.force,
        )
        return {"ok": True, "results": results}

    # ── POST /recover/cross-account-leak (Iter-163) ─────────────────────
    # Production recovery endpoint: reverses auto_cron ledger rows
    # generated by the BUGGY pre-Iter-163 sync on Snap/Meta counterparties
    # that have no external_account_id. Those rows previously absorbed
    # spend from every sibling ad account on the same provider, inflating
    # today's spend (e.g. to 100K SAR) on the dashboard.
    @router.post("/recover/cross-account-leak")
    async def recover_cross_account_leak(
        user: dict = Depends(current_user),
    ):
        """For each Snap/Meta counterparty WITHOUT external_account_id,
        reverse all `auto_cron=True` spend rows in `ad_account_ledger`
        for the last 7 days. Restores the counterparty balance and
        closes/reduces the corresponding open ad_account liability.

        Read-only / dry-run is NOT offered; this is an explicit recovery
        action that the user invokes once after deploying the fix.
        """
        uid = user["id"]
        from datetime import timedelta as _td
        today = riyadh_today_iso()
        since = (datetime.fromisoformat(today) - _td(days=7)).date().isoformat()
        recovered = []
        async for cp in db.counterparties.find(
            {"user_id": uid, "kind": "ad_account",
             "ad_provider": {"$in": ["snapchat", "meta"]},
             "$or": [{"external_account_id": None},
                     {"external_account_id": ""}]},
            {"_id": 0},
        ):
            # Find buggy auto_cron rows in the recovery window.
            rows = await db.ad_account_ledger.find(
                {"user_id": uid, "counterparty_id": cp["id"],
                 "type": "spend",
                 "breakdown.auto_cron": True,
                 "date": {"$gte": since, "$lte": today}},
                {"_id": 0},
            ).to_list(2000)
            if not rows:
                continue

            total_reversed = 0.0
            for row in rows:
                total_reversed += float(row.get("amount") or 0)
                # Delete the buggy ledger row outright (this is the
                # quickest, safest reversal — these rows should never
                # have been created in the first place).
                await db.ad_account_ledger.delete_one(
                    {"id": row["id"], "user_id": uid},
                )
            # Close any open ad_account liability for this counterparty
            # that was raised by `ad_account_cron` (auto_generated).
            await db.liabilities.update_many(
                {"user_id": uid, "counterparty_id": cp["id"],
                 "kind": "ad_account", "auto_generated": True,
                 "source": "ad_account_cron",
                 "status": {"$in": ["unpaid", "partial"]}},
                {"$set": {"expected_amount": 0.0,
                          "paid_amount": 0.0,
                          "status": "paid",
                          "updated_at": _now(),
                          "recovery_note": (
                              "محو تلقائي — Iter-163 cross-account "
                              "leak recovery")}},
            )
            # Reset sync marker so the next sync can run fresh.
            await db.counterparties.update_one(
                {"id": cp["id"], "user_id": uid},
                {"$unset": {"last_auto_sync_date": "",
                            "last_auto_sync_at": ""}},
            )
            recovered.append({
                "id": cp["id"], "name": cp.get("name"),
                "ad_provider": cp.get("ad_provider"),
                "rows_deleted": len(rows),
                "amount_reversed": round(total_reversed, 2),
            })
        return {"ok": True, "recovered": recovered,
                "since": since, "until": today,
                "total_amount_reversed": round(
                    sum(r["amount_reversed"] for r in recovered), 2)}

    # ── POST /migration/preview (Iter-110) ────────────────────────────
    @router.post("/migration/preview")
    async def migration_preview(
        payload: MigrationPreviewIn,
        user: dict = Depends(current_user),
    ):
        """Read-only audit of what a historical migration WOULD do.

        For every ad-account counterparty owned by the user we report:
          • period spend (aggregated from the right provider collection),
          • whether the account is linked via `external_account_id`,
          • whether a previous sync has already touched the to_date,
          • the current balance/debt, and per-day rows (capped to 60 for
            UI brevity).

        No DB writes occur. The merchant uses this to decide which
        accounts to actually migrate.
        """
        from_d, to_d = payload.from_date, payload.to_date
        accounts_report = []
        totals = {"period_spend": 0.0, "accounts_ready": 0, "accounts_warned": 0}

        async for cp in db.counterparties.find(
            {"user_id": user["id"], "kind": "ad_account"}, {"_id": 0},
        ).sort([("name", 1)]):
            provider = cp.get("ad_provider")
            ext_id = (cp.get("external_account_id") or "").strip() or None
            supported = provider in PROVIDER_SOURCES
            warnings: list[str] = []
            blocked = False

            if not supported:
                warnings.append(f"المنصة {provider} غير مدعومة للمزامنة التلقائية — استخدم الرصيد الافتتاحي يدوياً.")
                blocked = True
                rows, source = [], ""
            else:
                rows, source = await _fetch_daily_spend(
                    db, user["id"], provider, ext_id, from_d, to_d,
                )
                if not ext_id:
                    # When the collection has a scope field (per-account),
                    # absence of external_id means we'd lump every
                    # sub-account into this one — flag & block by default.
                    has_scope = any(s["scope_field"] for s in PROVIDER_SOURCES[provider])
                    if has_scope:
                        warnings.append(
                            "هذا الحساب غير مربوط بـ Ad Account ID — لو فعّلت الترحيل ستندمج كل صرف هذه المنصة على هذا الحساب."
                        )
                        blocked = True
                    elif provider == "tiktok":
                        # tiktok collection has no scope at all; warn but
                        # still allow IF the user has only ONE tiktok cp.
                        tiktok_cps = await db.counterparties.count_documents({
                            "user_id": user["id"], "kind": "ad_account",
                            "ad_provider": "tiktok",
                        })
                        if tiktok_cps > 1:
                            warnings.append(
                                "بيانات TikTok الحالية لا تميّز بين الحسابات الفرعية. لو عندك أكثر من حساب TikTok، الترحيل سيلمّ كل الصرف على حساب واحد. (موصى به: استخدم الرصيد الافتتاحي يدوياً)."
                            )
                            blocked = True

            period_spend = round(sum(r["spend"] for r in rows), 2)
            days_with_data = len([r for r in rows if r["spend"] > 0])

            already_synced = cp.get("last_auto_sync_date")
            already_within_range = bool(
                already_synced
                and from_d <= already_synced <= to_d
            )
            if already_within_range:
                warnings.append(
                    f"تمت مزامنة هذا الحساب سابقاً حتى {already_synced} — الترحيل قد يكرر الصرف. راجع السجل قبل التنفيذ."
                )

            debt = await _current_open_debt(db, user["id"], cp["id"])
            current_open_debt = 0.0
            if debt:
                current_open_debt = _round(
                    (debt.get("expected_amount") or 0)
                    - (debt.get("paid_amount") or 0)
                )

            accounts_report.append({
                "id": cp["id"],
                "name": cp["name"],
                "ad_provider": provider,
                "external_account_id": ext_id,
                "current_balance": _round(cp.get("balance") or 0),
                "current_open_debt": current_open_debt,
                "debt_mode": cp.get("debt_mode") or "auto",
                "last_auto_sync_date": already_synced,
                "supported": supported,
                "source_collection": source,
                "period_spend": period_spend,
                "days_with_data": days_with_data,
                "first_date": rows[0]["date"] if rows else None,
                "last_date":  rows[-1]["date"] if rows else None,
                "daily_rows": rows[:60],     # cap the response
                "daily_rows_truncated": len(rows) > 60,
                "warnings": warnings,
                "blocked_by_default": blocked,
            })
            totals["period_spend"] += period_spend
            if blocked or not supported:
                totals["accounts_warned"] += 1
            elif period_spend > 0:
                totals["accounts_ready"] += 1

        return {
            "from_date": from_d,
            "to_date":   to_d,
            "accounts":  accounts_report,
            "totals": {k: (round(v, 2) if isinstance(v, float) else v)
                       for k, v in totals.items()},
        }

    # ── POST /migration/apply (Iter-110) ──────────────────────────────
    @router.post("/migration/apply")
    async def migration_apply(
        payload: MigrationApplyIn,
        user: dict = Depends(current_user),
    ):
        """Apply the historical migration for the EXPLICITLY chosen
        account_ids in `payload.account_ids`. Each posted spend row
        respects the account's debt_mode (auto vs manual) — auto creates
        / extends a dedicated liability, manual records spend only.

        Returns per-account: rows_posted, total_spend_applied, debt_created,
        balance_after, debt_after.
        """
        if not payload.account_ids:
            raise HTTPException(400, "يجب اختيار حساب واحد على الأقل")
        results: list[dict] = []

        for cp_id in payload.account_ids:
            cp = await db.counterparties.find_one(
                {"id": cp_id, "user_id": user["id"], "kind": "ad_account"},
                {"_id": 0},
            )
            if not cp:
                results.append({"id": cp_id, "ok": False, "error": "not_found"})
                continue
            provider = cp.get("ad_provider")
            if provider not in PROVIDER_SOURCES:
                results.append({"id": cp_id, "name": cp["name"], "ok": False,
                                "error": f"provider {provider} not supported"})
                continue

            # ── Iter-133 — Idempotent re-migration ────────────────────
            # If the merchant has already migrated all (or part of) this
            # window before, we MUST reverse those prior postings before
            # applying fresh figures.  Otherwise spend / liabilities
            # double up exactly as the user observed in production.
            # Pattern is identical to the `force=True` auto-sync reversal
            # below — find prior `breakdown.migration=True` ledger rows in
            # range, restore `from_balance`, shrink/delete the migration
            # liability by `uncovered`, then drop those ledger rows.
            prev_rows = await db.ad_account_ledger.find({
                "user_id": user["id"], "counterparty_id": cp_id,
                "type": "spend",
                "breakdown.migration": True,
                "date": {"$gte": payload.from_date, "$lte": payload.to_date},
            }, {"_id": 0}).to_list(5000)
            reversed_rows = 0
            if prev_rows:
                prev_covered = round(sum(
                    (r.get("breakdown") or {}).get("from_balance", 0)
                    for r in prev_rows), 2)
                prev_uncovered = round(sum(
                    (r.get("breakdown") or {}).get("uncovered", 0)
                    for r in prev_rows), 2)
                # 1) Restore balance the prior migration had consumed.
                if prev_covered > 0:
                    await db.counterparties.update_one(
                        {"id": cp_id, "user_id": user["id"]},
                        {"$inc": {"balance": prev_covered},
                         "$set": {"updated_at": _now()}},
                    )
                    cp["balance"] = round(
                        float(cp.get("balance") or 0) + prev_covered, 2,
                    )
                # 2) Shrink (or delete) the open migration liability.
                if prev_uncovered > 0 and (cp.get("debt_mode") or "auto") == "auto":
                    existing_liab = await db.liabilities.find_one(
                        {"user_id": user["id"], "kind": "ad_account",
                         "counterparty_id": cp_id,
                         "source": "ad_account_migration",
                         "status": {"$in": ["unpaid", "partial"]}},
                        {"_id": 0},
                    )
                    if existing_liab:
                        new_exp = round(
                            (existing_liab.get("expected_amount") or 0)
                            - prev_uncovered, 2,
                        )
                        paid = float(existing_liab.get("paid_amount") or 0)
                        if new_exp <= max(paid, 0.01):
                            if paid > 0:
                                # Partially settled — keep history,
                                # clamp expected to what was actually paid.
                                await db.liabilities.update_one(
                                    {"id": existing_liab["id"], "user_id": user["id"]},
                                    {"$set": {"expected_amount": paid,
                                              "status": "paid",
                                              "updated_at": _now()}},
                                )
                            else:
                                await db.liabilities.delete_one(
                                    {"id": existing_liab["id"], "user_id": user["id"]},
                                )
                        else:
                            await db.liabilities.update_one(
                                {"id": existing_liab["id"], "user_id": user["id"]},
                                {"$set": {"expected_amount": new_exp,
                                          "updated_at": _now()}},
                            )
                # 3) Drop the prior ledger rows so they don't double-count.
                ids_to_delete = [r["id"] for r in prev_rows if r.get("id")]
                if ids_to_delete:
                    res = await db.ad_account_ledger.delete_many({
                        "user_id": user["id"], "id": {"$in": ids_to_delete},
                    })
                    reversed_rows = res.deleted_count

            ext_id = (cp.get("external_account_id") or "").strip() or None
            rows, source = await _fetch_daily_spend(
                db, user["id"], provider, ext_id, payload.from_date, payload.to_date,
            )
            rows = [r for r in rows if r["spend"] > 0]
            if not rows:
                results.append({
                    "id": cp_id, "name": cp["name"], "ok": True,
                    "rows_posted": 0, "total_spend": 0.0,
                    "message": "لا توجد بيانات صرف في الفترة المختارة",
                })
                continue

            mode = cp.get("debt_mode") or "auto"
            total_spend = 0.0
            total_debt_created = 0.0
            rows_posted = 0

            # Snapshot starting balance once; we'll keep computing
            # incrementally so we don't re-read the document each row.
            balance_now = float(cp.get("balance") or 0)

            if payload.mode == "lump":
                lump_total = round(sum(r["spend"] for r in rows), 2)
                covered = min(lump_total, balance_now)
                uncovered = round(lump_total - covered, 2)
                balance_now = round(balance_now - covered, 2)
                liab_id, debt_after = await _apply_uncovered(
                    db, user["id"], cp, uncovered, mode, payload.to_date,
                    description=f"ترحيل تاريخي ({payload.from_date} → {payload.to_date})",
                    source_tag="ad_account_migration",
                )
                if uncovered > 0 and mode == "auto":
                    total_debt_created += uncovered
                await db.ad_account_ledger.insert_one({
                    "id": str(uuid.uuid4()), "user_id": user["id"],
                    "counterparty_id": cp_id, "type": "spend",
                    "amount": lump_total, "balance_after": balance_now,
                    "debt_after": debt_after,
                    "related_liability_id": liab_id,
                    "description": f"ترحيل تاريخي (lump) {payload.from_date} → {payload.to_date}",
                    "breakdown": {"from_balance": covered, "uncovered": uncovered,
                                  "mode": mode, "migration": True,
                                  "source_collection": source,
                                  "days_count": len(rows)},
                    "date": payload.to_date, "created_at": _now(),
                })
                rows_posted = 1
                total_spend = lump_total
            else:
                # daily mode — one ledger row per (account, day).
                # Iter-159m FIX: previously this INSERTED a new ledger row
                # per day without checking if a same-day row already
                # existed (from the half-hour cron or a prior migration).
                # This violated the global rule "ONE ledger row per
                # account per day".  Now we look up the existing row,
                # compute the delta (new platform total − previously
                # applied), apply only the delta to balance/liability,
                # and either UPDATE the existing row's amount or INSERT
                # a new one when no prior row exists.
                for r in rows:
                    amt = round(float(r["spend"]), 2)
                    day = r["date"]

                    # Find ALL prior spend rows for the same (account, day)
                    # — auto_cron / migration / both.
                    prior_rows = await db.ad_account_ledger.find(
                        {"user_id": user["id"],
                         "counterparty_id": cp_id,
                         "type": "spend",
                         "date": day},
                        {"_id": 0, "id": 1, "amount": 1, "breakdown": 1,
                         "created_at": 1},
                    ).sort("created_at", 1).to_list(50)

                    prior_applied = round(
                        sum(float(p.get("amount") or 0) for p in prior_rows),
                        2)
                    delta = round(amt - prior_applied, 2)

                    if delta <= 0:
                        # Platform reports same-or-less than what's already
                        # on the books for this day → no-op (we don't
                        # roll back historical debt automatically).
                        continue

                    # Apply ONLY the delta to balance + liability.
                    covered = min(delta, balance_now)
                    uncovered = round(delta - covered, 2)
                    balance_now = round(balance_now - covered, 2)
                    liab_id, debt_after = await _apply_uncovered(
                        db, user["id"], cp, uncovered, mode, day,
                        description=f"ترحيل تاريخي يوم {day}",
                        source_tag="ad_account_migration",
                    )
                    if uncovered > 0 and mode == "auto":
                        total_debt_created += uncovered

                    if prior_rows:
                        # Collapse + update the OLDEST prior row.  Delete
                        # any extras (defensive cleanup of pre-fix data).
                        keep = prior_rows[0]
                        dupes = [p["id"] for p in prior_rows[1:]]
                        if dupes:
                            await db.ad_account_ledger.delete_many(
                                {"user_id": user["id"],
                                 "id": {"$in": dupes}},
                            )
                        merged_bd = dict(keep.get("breakdown") or {})
                        merged_bd.update({
                            "migration": True,
                            "mode": mode,
                            "source_collection": source,
                            "from_balance": (merged_bd.get("from_balance", 0.0)
                                             + covered),
                            "uncovered": (merged_bd.get("uncovered", 0.0)
                                          + uncovered),
                            "delta_applied": (merged_bd.get("delta_applied", 0.0)
                                              + delta),
                            "platform_total": amt,
                            "last_migration_at": _now(),
                        })
                        await db.ad_account_ledger.update_one(
                            {"id": keep["id"], "user_id": user["id"]},
                            {"$set": {
                                "amount": amt,           # cumulative
                                "balance_after": balance_now,
                                "debt_after": debt_after,
                                "related_liability_id": liab_id,
                                "description": (f"ترحيل تاريخي — {day} "
                                                 "(تحديث تراكمي)"),
                                "breakdown": merged_bd,
                            }},
                        )
                    else:
                        await db.ad_account_ledger.insert_one({
                            "id": str(uuid.uuid4()), "user_id": user["id"],
                            "counterparty_id": cp_id, "type": "spend",
                            "amount": amt, "balance_after": balance_now,
                            "debt_after": debt_after,
                            "related_liability_id": liab_id,
                            "description": f"ترحيل تاريخي — {day}",
                            "breakdown": {"from_balance": covered,
                                          "uncovered": uncovered,
                                          "mode": mode, "migration": True,
                                          "source_collection": source,
                                          "platform_total": amt,
                                          "delta_applied": delta},
                            "date": day, "created_at": _now(),
                        })
                    rows_posted += 1
                    total_spend += delta

            # Persist final balance + mark migration sync date
            await db.counterparties.update_one(
                {"id": cp_id, "user_id": user["id"]},
                {"$set": {
                    "balance": round(balance_now, 2),
                    "last_migration_at": _now(),
                    "last_migration_range": {"from": payload.from_date,
                                             "to":   payload.to_date,
                                             "mode": payload.mode},
                    "updated_at": _now(),
                }},
            )
            results.append({
                "id": cp_id, "name": cp["name"], "ok": True,
                "rows_posted": rows_posted,
                "total_spend": round(total_spend, 2),
                "debt_created": round(total_debt_created, 2),
                "balance_after": round(balance_now, 2),
                "mode_used": payload.mode,
                "source_collection": source,
                # Iter-133 — surface how many prior migration rows were
                # rolled back so the user knows this run replaced them.
                "reversed_prior_rows": reversed_rows,
            })
        return {"ok": True, "results": results}

    # ── POST /migration/cleanup-duplicates (Iter-133 follow-up) ───────
    @router.post("/migration/cleanup-duplicates")
    async def migration_cleanup_duplicates(
        request: Request,
        user: dict = Depends(current_user),
    ):
        """One-shot cleanup for the duplication that accumulated BEFORE
        Iter-133 made the migration idempotent.  Two passes:

        Pass A — duplicate ledger rows: for every (counterparty, date)
        with multiple `breakdown.migration=True` ledger rows we KEEP
        the most recent one (max `created_at`) and REVERSE every older
        duplicate (restore `from_balance` to the counterparty, reduce
        the migration liability by `uncovered`, delete the row).

        Pass B — duplicate open migration liabilities: if a single
        counterparty has more than one `status ∈ {unpaid, partial}`
        liability with `source=ad_account_migration`, we MERGE them
        into the newest by summing `expected_amount` + `paid_amount`,
        then delete the older rows.

        Query param `dry_run=true` (default) returns the plan WITHOUT
        writing anything.  Pass `dry_run=false` to actually apply it.
        """
        params = dict(request.query_params)
        dry_run = (params.get("dry_run", "true").lower() != "false")

        scanned = 0
        ledger_removed = 0
        balance_restored = 0.0
        liab_amount_reduced = 0.0
        liabs_merged = 0
        details: list[dict] = []

        async for cp in db.counterparties.find(
            {"user_id": user["id"], "kind": "ad_account"},
            {"_id": 0},
        ):
            scanned += 1
            cp_id = cp["id"]
            per_cp = {
                "id": cp_id, "name": cp.get("name"),
                "removed_rows": 0,
                "balance_restored": 0.0,
                "liab_amount_reduced": 0.0,
                "liabs_merged": 0,
            }

            # ── Pass A — group migration ledger rows by date ──────────
            buckets: dict[str, list[dict]] = {}
            async for row in db.ad_account_ledger.find(
                {"user_id": user["id"], "counterparty_id": cp_id,
                 "type": "spend", "breakdown.migration": True},
                {"_id": 0},
            ):
                buckets.setdefault(row.get("date") or "", []).append(row)

            cp_covered_restore = 0.0
            cp_uncovered_remove = 0.0
            ids_to_drop: list[str] = []
            for _date, group in buckets.items():
                if len(group) <= 1:
                    continue
                group.sort(key=lambda r: r.get("created_at") or "")
                victims = group[:-1]   # keep the newest only
                for v in victims:
                    bd = v.get("breakdown") or {}
                    cp_covered_restore += float(bd.get("from_balance") or 0)
                    cp_uncovered_remove += float(bd.get("uncovered") or 0)
                    if v.get("id"):
                        ids_to_drop.append(v["id"])
                per_cp["removed_rows"] += len(victims)

            cp_covered_restore = round(cp_covered_restore, 2)
            cp_uncovered_remove = round(cp_uncovered_remove, 2)
            per_cp["balance_restored"]    = cp_covered_restore
            per_cp["liab_amount_reduced"] = cp_uncovered_remove

            if not dry_run and per_cp["removed_rows"] > 0:
                if cp_covered_restore > 0:
                    await db.counterparties.update_one(
                        {"id": cp_id, "user_id": user["id"]},
                        {"$inc": {"balance": cp_covered_restore},
                         "$set": {"updated_at": _now()}},
                    )
                if cp_uncovered_remove > 0:
                    open_liab = await db.liabilities.find_one(
                        {"user_id": user["id"], "kind": "ad_account",
                         "counterparty_id": cp_id,
                         "source": "ad_account_migration",
                         "status": {"$in": ["unpaid", "partial"]}},
                        {"_id": 0},
                    )
                    if open_liab:
                        new_exp = round(
                            (open_liab.get("expected_amount") or 0)
                            - cp_uncovered_remove, 2,
                        )
                        paid = float(open_liab.get("paid_amount") or 0)
                        if new_exp <= max(paid, 0.01):
                            if paid > 0:
                                await db.liabilities.update_one(
                                    {"id": open_liab["id"], "user_id": user["id"]},
                                    {"$set": {"expected_amount": paid,
                                              "status": "paid",
                                              "updated_at": _now()}},
                                )
                            else:
                                await db.liabilities.delete_one(
                                    {"id": open_liab["id"], "user_id": user["id"]},
                                )
                        else:
                            await db.liabilities.update_one(
                                {"id": open_liab["id"], "user_id": user["id"]},
                                {"$set": {"expected_amount": new_exp,
                                          "updated_at": _now()}},
                            )
                if ids_to_drop:
                    await db.ad_account_ledger.delete_many({
                        "user_id": user["id"], "id": {"$in": ids_to_drop},
                    })

            # ── Pass B — merge duplicate OPEN migration liabilities ──
            opens = await db.liabilities.find(
                {"user_id": user["id"], "kind": "ad_account",
                 "counterparty_id": cp_id,
                 "source": "ad_account_migration",
                 "status": {"$in": ["unpaid", "partial"]}},
                {"_id": 0},
            ).to_list(500)
            if len(opens) > 1:
                opens.sort(key=lambda lab: lab.get("created_at") or "")
                survivor   = opens[-1]
                duplicates = opens[:-1]
                merged_exp  = float(survivor.get("expected_amount") or 0)
                merged_paid = float(survivor.get("paid_amount") or 0)
                for d in duplicates:
                    merged_exp  += float(d.get("expected_amount") or 0)
                    merged_paid += float(d.get("paid_amount") or 0)
                merged_exp  = round(merged_exp, 2)
                merged_paid = round(merged_paid, 2)
                new_status = (
                    "paid"    if merged_paid >= merged_exp
                    else ("partial" if merged_paid > 0 else "unpaid")
                )
                per_cp["liabs_merged"] = len(duplicates)
                if not dry_run:
                    await db.liabilities.update_one(
                        {"id": survivor["id"], "user_id": user["id"]},
                        {"$set": {"expected_amount": merged_exp,
                                  "paid_amount":     merged_paid,
                                  "status":          new_status,
                                  "updated_at":      _now()}},
                    )
                    await db.liabilities.delete_many({
                        "user_id": user["id"],
                        "id": {"$in": [d["id"] for d in duplicates]},
                    })

            ledger_removed      += per_cp["removed_rows"]
            balance_restored    += per_cp["balance_restored"]
            liab_amount_reduced += per_cp["liab_amount_reduced"]
            liabs_merged        += per_cp["liabs_merged"]
            if per_cp["removed_rows"] or per_cp["liabs_merged"]:
                details.append(per_cp)

        return {
            "ok": True,
            "dry_run": dry_run,
            "summary": {
                "counterparties_scanned":        scanned,
                "duplicate_ledger_rows_removed": ledger_removed,
                "balance_restored":              round(balance_restored, 2),
                "liability_amount_reduced":      round(liab_amount_reduced, 2),
                "duplicate_liabilities_merged":  liabs_merged,
            },
            "details": details,
        }

    # ── PUT /{cp_id}/opening (Iter-110) ───────────────────────────────
    @router.put("/{cp_id}/opening")
    async def set_opening(
        cp_id: str, payload: OpeningIn,
        user: dict = Depends(current_user),
    ):
        """Set / refresh the manual opening figures for an ad account.

        Each fields is optional — only fields present in the payload are
        applied. Writes a ledger entry of type=opening for full audit.
        """
        cp = await _get_account(db, user["id"], cp_id)
        upd: dict = {"updated_at": _now()}
        ledger_changes: dict = {}

        # 1) Opening balance — overwrites the current balance & records
        #    a manual movement for transparency.
        if payload.opening_balance is not None:
            new_bal = _round(payload.opening_balance)
            old_bal = _round(cp.get("balance") or 0)
            upd["balance"] = new_bal
            upd["opening_balance"] = new_bal
            ledger_changes["balance_from"] = old_bal
            ledger_changes["balance_to"] = new_bal

        # 2) Opening debt — find existing opening-liability row to update
        #    or create a new one when amount > 0.
        if payload.opening_debt is not None:
            new_debt = _round(payload.opening_debt)
            existing = await db.liabilities.find_one(
                {"user_id": user["id"], "kind": "ad_account",
                 "counterparty_id": cp_id, "source": "ad_account_opening"},
                {"_id": 0},
            )
            if new_debt <= 0 and existing:
                # User wants opening debt cleared
                await db.liabilities.delete_one(
                    {"id": existing["id"], "user_id": user["id"]},
                )
                ledger_changes["opening_debt"] = 0.0
            elif new_debt > 0:
                due_date = payload.start_date or _now()[:10]
                if existing:
                    await db.liabilities.update_one(
                        {"id": existing["id"], "user_id": user["id"]},
                        {"$set": {
                            "expected_amount": new_debt,
                            "due_date": due_date,
                            "status": "partial" if (existing.get("paid_amount") or 0) > 0 else "unpaid",
                            "updated_at": _now(),
                        }},
                    )
                else:
                    await db.liabilities.insert_one({
                        "id": str(uuid.uuid4()),
                        "user_id": user["id"], "kind": "ad_account",
                        "ad_provider": cp.get("ad_provider"),
                        "ad_account_label": cp["name"],
                        "counterparty_id": cp_id,
                        "expected_amount": new_debt, "paid_amount": 0.0,
                        "advance_deducted": 0.0,
                        "due_date": due_date, "status": "unpaid",
                        "description": f"رصيد افتتاحي — {cp['name']}",
                        "auto_generated": False,
                        "source": "ad_account_opening",
                        "created_at": _now(), "updated_at": _now(),
                    })
                ledger_changes["opening_debt"] = new_debt

        if payload.start_date is not None:
            upd["opening_start_date"] = payload.start_date
        if payload.method is not None:
            upd["debt_mode"] = payload.method  # method = auto|manual
        if payload.notes is not None:
            upd["opening_notes"] = payload.notes

        if upd:
            await db.counterparties.update_one(
                {"id": cp_id, "user_id": user["id"]}, {"$set": upd},
            )

        if ledger_changes:
            from ledger_core import post_txn_group as _ptg
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": user["id"],
                "counterparty_id": cp_id, "type": "opening",
                "amount": _round(payload.opening_balance or 0),
                "balance_after": upd.get("balance", _round(cp.get("balance") or 0)),
                "debt_after": _round(payload.opening_debt or 0),
                "description": f"تعيين رصيد افتتاحي يدوي — {cp['name']}",
                "notes": payload.notes or "",
                "breakdown": ledger_changes,
                "date": payload.start_date or _now()[:10],
                "created_at": _now(),
            })

            # Iter-218 — Post a balanced SSOT entry for the opening
            # delta(s). The opening is intentionally booked against
            # equity.opening_balance so the universal ledger's net
            # position absorbs the change without inventing a fake
            # counter-party. Each PUT /opening posts an INCREMENTAL
            # delta (new_cp_balance − old_cp_balance) — successive
            # edits stack correctly without needing to reverse the
            # previous opening entry. Skipped when only metadata
            # (notes/start_date/method) changed and there is no
            # numeric delta.
            delta_balance = _round(
                float(ledger_changes.get("balance_to") or 0)
                - float(ledger_changes.get("balance_from") or 0),
            ) if "balance_to" in ledger_changes else 0.0
            delta_debt = float(
                ledger_changes.get("opening_debt") or 0,
            ) if "opening_debt" in ledger_changes else 0.0

            entries: list = []
            if abs(delta_balance) > 0.005:
                if delta_balance > 0:
                    entries.append({"entity_type": "ad_account",
                                     "entity_id": cp_id,
                                     "sub_account": "balance",
                                     "side": "debit",
                                     "amount": delta_balance,
                                     "entry_type": "opening_balance"})
                    entries.append({"entity_type": "equity",
                                     "entity_id": "opening_balance",
                                     "side": "credit",
                                     "amount": delta_balance,
                                     "entry_type": "opening_balance"})
                else:
                    entries.append({"entity_type": "ad_account",
                                     "entity_id": cp_id,
                                     "sub_account": "balance",
                                     "side": "credit",
                                     "amount": -delta_balance,
                                     "entry_type": "opening_balance"})
                    entries.append({"entity_type": "equity",
                                     "entity_id": "opening_balance",
                                     "side": "debit",
                                     "amount": -delta_balance,
                                     "entry_type": "opening_balance"})
            if delta_debt > 0.005:
                entries.append({"entity_type": "ad_account",
                                 "entity_id": cp_id,
                                 "sub_account": "debt",
                                 "side": "credit",
                                 "amount": delta_debt,
                                 "entry_type": "opening_balance"})
                entries.append({"entity_type": "equity",
                                 "entity_id": "opening_balance",
                                 "side": "debit",
                                 "amount": delta_debt,
                                 "entry_type": "opening_balance"})
            if entries:
                await _ptg(
                    db, user_id=user["id"], actor_id=user["id"],
                    actor_name=(user.get("name")
                                or user.get("email") or ""),
                    txn_type="ad_account_opening",
                    notes=f"رصيد افتتاحي يدوي — {cp['name']}",
                    metadata={
                        "ad_account_id": cp_id,
                        "ad_account_name": cp.get("name"),
                        "ad_provider": cp.get("ad_provider"),
                        "delta_balance": delta_balance,
                        "delta_debt": delta_debt,
                        "iter": "iter218_opening",
                    },
                    entries=entries,
                )

        cp_fresh = await _get_account(db, user["id"], cp_id)
        return await _summarise(db, user["id"], cp_fresh)

    # ── Iter-148 — Diagnostic + cleanup for duplicate TOPUP rows ──────
    @router.get("/diagnostics/duplicate-topups")
    async def diagnose_duplicate_topups(
        user: dict = Depends(current_user),
    ):
        """List ad-account topup ledger rows that look like duplicates.

        A "duplicate" is two-or-more `type=topup` rows for the SAME
        counterparty on the SAME date with the SAME amount.  These piled
        up before the topup endpoint had any client-side debounce.

        Returns the list grouped by (counterparty, date, amount) so the
        merchant can decide which ones to wipe.  No writes happen here.
        """
        uid = user["id"]
        report: list[dict] = []

        async for cp in db.counterparties.find(
            {"user_id": uid, "kind": "ad_account"},
            {"_id": 0, "id": 1, "name": 1, "balance": 1, "ad_provider": 1},
        ):
            cp_id = cp["id"]
            buckets: dict[tuple, list[dict]] = {}
            async for row in db.ad_account_ledger.find(
                {"user_id": uid, "counterparty_id": cp_id, "type": "topup"},
                {"_id": 0},
            ):
                key = (row.get("date") or "", round(float(row.get("amount") or 0), 2))
                buckets.setdefault(key, []).append(row)

            cp_dups: list[dict] = []
            for (d, amt), rows in buckets.items():
                if len(rows) <= 1:
                    continue
                rows.sort(key=lambda r: r.get("created_at") or "")
                cp_dups.append({
                    "date":  d,
                    "amount": amt,
                    "count": len(rows),
                    "keep_id":   rows[0].get("id"),
                    "kept_created_at": rows[0].get("created_at"),
                    "victims": [
                        {
                            "id":          r.get("id"),
                            "created_at":  r.get("created_at"),
                            "related_tx_id": r.get("related_tx_id"),
                            "related_liability_id": r.get("related_liability_id"),
                            "breakdown":   r.get("breakdown") or {},
                        }
                        for r in rows[1:]
                    ],
                })
            if cp_dups:
                cp_dups.sort(key=lambda x: (x["date"], -x["count"]))
                report.append({
                    "counterparty_id":   cp_id,
                    "name":              cp.get("name"),
                    "ad_provider":       cp.get("ad_provider"),
                    "current_balance":   _round(cp.get("balance") or 0),
                    "duplicate_groups":  cp_dups,
                    "total_extra_rows":  sum(len(g["victims"]) for g in cp_dups),
                    "total_extra_amount": _round(
                        sum(g["amount"] * len(g["victims"]) for g in cp_dups),
                    ),
                })
        return {
            "checked_at":   _now(),
            "accounts_with_duplicates": len(report),
            "total_extra_rows": sum(a["total_extra_rows"] for a in report),
            "total_extra_amount": _round(
                sum(a["total_extra_amount"] for a in report),
            ),
            "accounts": report,
        }

    @router.post("/diagnostics/duplicate-topups/cleanup")
    async def cleanup_duplicate_topups(
        request: Request,
        user: dict = Depends(current_user),
    ):
        """Remove duplicate topup ledger rows + reverse their financial
        impact.  Keeps the OLDEST row per (counterparty, date, amount)
        group and reverses every younger duplicate:

          • reverses the bank-side cash movement (deletes the linked
            `account_transactions` row)
          • decreases `counterparty.balance` by the row's `to_balance`
            portion
          • decreases the linked liability's `paid_amount` by the
            `to_debt` portion (and re-opens it if needed)
          • deletes the ledger row itself

        Pass `?dry_run=true` (default) for a no-write preview.
        """
        uid = user["id"]
        params = dict(request.query_params)
        dry_run = (params.get("dry_run", "true").lower() != "false")

        removed_rows = 0
        balance_decreased = 0.0
        liab_decreased    = 0.0
        bank_tx_removed   = 0
        per_account: list[dict] = []

        async for cp in db.counterparties.find(
            {"user_id": uid, "kind": "ad_account"},
            {"_id": 0, "id": 1, "name": 1, "balance": 1},
        ):
            cp_id = cp["id"]
            buckets: dict[tuple, list[dict]] = {}
            async for row in db.ad_account_ledger.find(
                {"user_id": uid, "counterparty_id": cp_id, "type": "topup"},
                {"_id": 0},
            ):
                key = (row.get("date") or "", round(float(row.get("amount") or 0), 2))
                buckets.setdefault(key, []).append(row)

            cp_removed = 0
            cp_bal_dec = 0.0
            cp_liab_dec = 0.0
            cp_tx_dropped = 0
            for (_d, _amt), rows in buckets.items():
                if len(rows) <= 1:
                    continue
                rows.sort(key=lambda r: r.get("created_at") or "")
                victims = rows[1:]
                for v in victims:
                    bd = v.get("breakdown") or {}
                    to_balance = _round(float(bd.get("to_balance") or 0))
                    to_debt    = _round(float(bd.get("to_debt") or 0))
                    cp_bal_dec  += to_balance
                    cp_liab_dec += to_debt
                    cp_removed  += 1

                    if dry_run:
                        continue

                    # Reverse the bank-side tx
                    if v.get("related_tx_id"):
                        del_res = await db.account_transactions.delete_one(
                            {"id": v["related_tx_id"], "user_id": uid},
                        )
                        cp_tx_dropped += int(getattr(del_res, "deleted_count", 0) or 0)
                    # Decrease counterparty balance
                    if to_balance:
                        await db.counterparties.update_one(
                            {"id": cp_id, "user_id": uid},
                            {"$inc": {"balance": -to_balance},
                             "$set": {"updated_at": _now()}},
                        )
                    # Reverse liability deduction
                    if to_debt and v.get("related_liability_id"):
                        liab = await db.liabilities.find_one(
                            {"id": v["related_liability_id"], "user_id": uid},
                        )
                        if liab:
                            new_paid = _round(
                                max(0, (liab.get("paid_amount") or 0) - to_debt),
                            )
                            new_status = (
                                "paid"
                                if new_paid + 0.01 >= float(liab.get("expected_amount") or 0)
                                else ("partial" if new_paid > 0 else "unpaid")
                            )
                            await db.liabilities.update_one(
                                {"id": liab["id"], "user_id": uid},
                                {"$set": {
                                    "paid_amount": new_paid,
                                    "status":      new_status,
                                    "updated_at":  _now(),
                                }},
                            )
                    # Finally drop the ledger row
                    await db.ad_account_ledger.delete_one(
                        {"id": v["id"], "user_id": uid},
                    )

            if cp_removed:
                per_account.append({
                    "counterparty_id":   cp_id,
                    "name":              cp.get("name"),
                    "removed_rows":      cp_removed,
                    "balance_decreased": _round(cp_bal_dec),
                    "liab_decreased":    _round(cp_liab_dec),
                    "bank_tx_removed":   cp_tx_dropped,
                })
                removed_rows     += cp_removed
                balance_decreased += cp_bal_dec
                liab_decreased    += cp_liab_dec
                bank_tx_removed   += cp_tx_dropped

        return {
            "ok":               True,
            "dry_run":          dry_run,
            "removed_rows":     removed_rows,
            "balance_decreased": _round(balance_decreased),
            "liab_decreased":    _round(liab_decreased),
            "bank_tx_removed":   bank_tx_removed,
            "per_account":       per_account,
        }

    parent_router.include_router(router)


# ── Module-level cron helpers (Iter-108) ───────────────────────────────
async def _apply_uncovered(
    db, user_id: str, cp: dict, uncovered: float, mode: str,
    due_date: str, *,
    description: str = "",
    source_tag: str = "ad_account_engine",
) -> tuple[Optional[str], float]:
    """Extend/create an open ad-account liability for the uncovered
    portion of spend. Returns (liability_id_or_None, remaining_debt).

    Only runs the writes when uncovered > 0 AND mode == 'auto'.
    Used by both /spend and the historical migration endpoint.
    """
    if uncovered <= 0:
        return None, 0.0
    if mode != "auto":
        # Manual mode — leave the uncovered amount un-tracked as debt.
        existing = await db.liabilities.find_one(
            {"user_id": user_id, "kind": "ad_account",
             "counterparty_id": cp["id"],
             "status": {"$in": ["unpaid", "partial"]}},
            {"_id": 0},
        )
        if existing:
            return existing["id"], round(
                (existing.get("expected_amount") or 0)
                - (existing.get("paid_amount") or 0), 2,
            )
        return None, 0.0
    existing = await db.liabilities.find_one(
        {"user_id": user_id, "kind": "ad_account",
         "counterparty_id": cp["id"],
         "status": {"$in": ["unpaid", "partial"]}},
        {"_id": 0},
    )
    if existing:
        new_exp = round((existing.get("expected_amount") or 0) + uncovered, 2)
        await db.liabilities.update_one(
            {"id": existing["id"], "user_id": user_id},
            {"$set": {"expected_amount": new_exp,
                      "status": "partial" if (existing.get("paid_amount") or 0) > 0 else "unpaid",
                      "updated_at": datetime.now(timezone.utc).isoformat()}},
        )
        return existing["id"], round(
            new_exp - (existing.get("paid_amount") or 0), 2,
        )
    liab_id = str(uuid.uuid4())
    await db.liabilities.insert_one({
        "id": liab_id, "user_id": user_id, "kind": "ad_account",
        "ad_provider": cp.get("ad_provider"),
        "ad_account_label": cp["name"],
        "counterparty_id": cp["id"],
        "expected_amount": round(uncovered, 2), "paid_amount": 0.0,
        "advance_deducted": 0.0,
        "due_date": due_date, "status": "unpaid",
        "description": description or f"مديونية {cp['name']}",
        "auto_generated": True, "source": source_tag,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return liab_id, round(uncovered, 2)


async def _run_sync_for_all(
    db, user_id: str, from_date: str, to_date: str,
    *, force: bool = False,
    provider_filter: Optional[set[str]] = None,
    include_make: bool = False,
    account_ids: Optional[set[str]] = None,
) -> list[dict]:
    """For each ad_account counterparty (supported providers only),
    aggregate daily-platform spend in the range and post it as a /spend
    via the same internal helpers. Idempotent per (account, to_date)
    via `last_auto_sync_date` on the counterparty — unless `force=True`
    in which case the guard is bypassed.

    Iter-110 fix: uses PROVIDER_SOURCES so Snapchat reads
    snapchat_account_daily by ad_account_id, Meta reads
    meta_ads_daily by account_id, etc.

    Iter-212: ONLY accounts whose data is delivered via direct
    platform API are processed by this half-hour cron. Anything
    delivered via Make.com (e.g. TikTok) is on Make.com's own 5-hour
    schedule and must NOT be touched here. Direct-API providers are

    Webhook/manual callers may opt into a narrow provider/account set with
    ``provider_filteri` / ``account_ids`` and set ``include_make=True``. This
    keeps the half-hour scheduler direct-API-only while allowing Make.com to
    reconcile the exact TikTok day it delivered into the financial SSOT.
    declared in `HALFHOUR_SYNC_PROVIDERS`; an explicit
    `cp.sync_via == "make_com"` always opts out.
    """
    out = []
    selected_providers = set(provider_filter or HALFHOUR_SYNC_PROVIDERS)
    cp_query: dict = {
        "user_id": user_id,
        "kind": "ad_account",
        "ad_provider": {"$in": sorted(selected_providers)},
    }
    if not include_make:
        cp_query["sync_via"] = {"$ne": "make_com"}
    if account_ids:
        cp_query["id"] = {"$in": sorted(account_ids)}
    async for cp in db.counterparties.find(
        cp_query,
        {"_id": 0},
    ):
        # Skip if already synced for this `to_date` (idempotency),
        # unless the caller explicitly forced a re-sync.
        if not force and cp.get("last_auto_sync_date") == to_date:
            out.append({"id": cp["id"], "name": cp["name"],
                        "skipped": True, "reason": "already_synced"})
            continue

        # ── Iter-150 fix — DELTA-BASED sync. The old "drop + recreate"
        # pattern (Iter-110 fix B) was unsafe: if the user PAID OFF the
        # auto-cron liability between sync passes, the reverse step
        # couldn't find an open liability to reduce, then the apply
        # step recreated a brand-new liability for the full daily spend
        # — undoing the user's payment. Reported bug (Feb 2026):
        # "عند تسديد مديونيه ... المزامنه الثانيه يضيف المديونيه من جديد".
        #
        # NEW LOGIC: sum the amounts ALREADY applied today via auto_cron
        # ledger rows (`prev_total_applied`), fetch the platform's fresh
        # daily total, and only apply the DELTA (total − prev_total) as
        # new spend. Re-runs with no new platform spend become genuine
        # no-ops — they never touch the (possibly paid) liability.
        # Iter-163 — Snapchat/Meta REQUIRE `external_account_id` so we
        # can isolate this counterparty's spend from sibling ad accounts
        # on the same provider. Missing → skip with a clear warning.
        # (Production bug Feb 2026: one un-scoped counterparty absorbed
        # 100K of spend that actually belonged to other accounts.)
        ext_id = (cp.get("external_account_id") or "").strip() or None
        needs_ext = cp.get("ad_provider") in ("snapchat", "meta")
        if needs_ext and not ext_id:
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now()}},
            )
            out.append({
                "id": cp["id"], "name": cp["name"],
                "skipped": True,
                "reason": "missing_external_account_id",
                "warning": (
                    "هذا الحساب لا يحتوي على معرّف خارجي "
                    "(external_account_id) — لذلك تم تخطّي المزامنة "
                    "لتفادي خلط مصاريف الحسابات الأخرى. عدّل الحساب "
                    "وأضف Ad Account ID."
                ),
            })
            continue
        rows, source = await _fetch_daily_spend(
            db, user_id, cp["ad_provider"], ext_id, from_date, to_date,
        )
        total = round(sum(r["spend"] for r in rows), 2)

        if force:
            prev_rows = await db.ad_account_ledger.find({
                "user_id": user_id, "counterparty_id": cp["id"],
                "type": "spend",
                "breakdown.auto_cron": True,
                "date": {"$gte": from_date, "$lte": to_date},
            }, {"_id": 0, "amount": 1}).to_list(5000)
            prev_total_applied = round(sum(
                float(r.get("amount") or 0) for r in prev_rows
            ), 2)
        else:
            prev_total_applied = 0.0

        delta = round(total - prev_total_applied, 2)

        if total <= 0 and prev_total_applied <= 0:
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now()}},
            )
            out.append({"id": cp["id"], "name": cp["name"], "spend": 0.0,
                        "source_collection": source})
            continue

        # No new spend since last cron pass → genuine no-op. We do NOT
        # touch the liability (it may have been paid by the user) and
        # we do NOT add a ledger row. Just bump the sync timestamps.
        if delta == 0:
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now()}},
            )
            out.append({
                "id": cp["id"], "name": cp["name"], "spend": total,
                "covered": 0.0, "uncovered": 0.0,
                "debt_created": 0.0,
                "source_collection": source,
                "delta_applied": 0.0, "prev_total_applied": prev_total_applied,
                "no_op": True,
            })
            continue

        mode = cp.get("debt_mode") or "auto"
        balance_before = float(cp.get("balance") or 0)

        if delta > 0:
            # Apply DELTA as additional spend.
            covered = min(delta, balance_before)
            uncovered = round(delta - covered, 2)
            new_balance = round(balance_before - covered, 2)
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"balance": new_balance,
                          "last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now(),
                          "updated_at": _now()}},
            )
            liab_id, debt_after = await _apply_uncovered(
                db, user_id, cp, uncovered, mode, to_date,
                description=f"مديونية تلقائية (cron) — {cp['name']}",
                source_tag="ad_account_cron",
            )
            # Iter-159f — ONE cumulative ledger row per (account, day).
            # Earlier this inserted a NEW row every half-hour, cluttering
            # the ledger with ~48 duplicate auto_cron entries per day.
            # Now we look for the existing same-day auto_cron row and
            # bump its `amount` to the fresh cumulative platform total
            # (and refresh balance_after/debt_after). Only inserts on
            # the FIRST sync of the day.
            existing_ledger_rows = await db.ad_account_ledger.find(
                {"user_id": user_id, "counterparty_id": cp["id"],
                 "type": "spend", "date": to_date,
                 "breakdown.auto_cron": True},
                {"_id": 0, "id": 1, "amount": 1, "breakdown": 1,
                 "created_at": 1},
            ).sort("created_at", 1).to_list(200)

            # Iter-159f cleanup — collapse any pre-existing duplicates
            # (created by the previous "row-per-sync" behavior) into the
            # oldest row of the day.  Idempotent: on subsequent passes
            # only one row exists, so the delete is a no-op.
            existing_ledger = (
                existing_ledger_rows[0] if existing_ledger_rows else None)
            if len(existing_ledger_rows) > 1:
                dupe_ids = [r["id"] for r in existing_ledger_rows[1:]]
                await db.ad_account_ledger.delete_many(
                    {"id": {"$in": dupe_ids}, "user_id": user_id})

            if existing_ledger:
                merged_bd = dict(existing_ledger.get("breakdown") or {})
                merged_bd.update({
                    "from_balance": merged_bd.get("from_balance", 0.0) + covered,
                    "uncovered": merged_bd.get("uncovered", 0.0) + uncovered,
                    "mode": mode,
                    "auto_cron": True,
                    "source_collection": source,
                    "created_debt": (
                        merged_bd.get("created_debt", 0.0)
                        + (uncovered if mode == "auto" else 0.0)),
                    "delta_applied": (merged_bd.get("delta_applied", 0.0) + delta),
                    "platform_total": total,
                    "prev_total_applied": prev_total_applied,
                    "last_sync_at": _now(),
                })
                await db.ad_account_ledger.update_one(
                    {"id": existing_ledger["id"]},
                    {"$set": {
                        "amount": total,            # cumulative for the day
                        "balance_after": new_balance,
                        "debt_after": debt_after,
                        "related_liability_id": liab_id,
                        "description": f"مزامنة تراكمية من {cp['ad_provider']} — {to_date}",
                        "breakdown": merged_bd,
                    }},
                )
            else:
                await db.ad_account_ledger.insert_one({
                    "id": str(uuid.uuid4()), "user_id": user_id,
                    "counterparty_id": cp["id"], "type": "spend",
                    "amount": total,                # cumulative from the start
                    "balance_after": new_balance,
                    "debt_after": debt_after,
                    "related_liability_id": liab_id,
                    "description": f"مزامنة تراكمية من {cp['ad_provider']} — {to_date}",
                    "breakdown": {"from_balance": covered, "uncovered": uncovered,
                                  "mode": mode, "auto_cron": True,
                                  "source_collection": source,
                                  "created_debt": uncovered if mode == "auto" else 0.0,
                                  "delta_applied": delta,
                                  "platform_total": total,
                                  "prev_total_applied": prev_total_applied},
                    "date": to_date, "created_at": _now(),
                })
            out.append({
                "id": cp["id"], "name": cp["name"], "spend": total,
                "covered": covered, "uncovered": uncovered,
                "debt_created": uncovered if mode == "auto" else 0.0,
                "source_collection": source,
                "delta_applied": delta,
                "prev_total_applied": prev_total_applied,
            })
            # Iter-215 — For HALFHOUR_SYNC_PROVIDERS (Snap/Meta) the
            # SSOT posting is now driven exclusively by the AM/PM
            # window scheduler (see ad_spend_windows.py). The 30-minute
            # cron is fetch-only here: it refreshes the upstream
            # `*_account_daily` tables and the local `ad_account_ledger`
            # for card-display purposes, but does NOT touch
            # `general_ledger`. Legacy providers (TikTok / Make.com)
            # retain the Iter-205 delta posting until they get their
            # own dedicated window logic.
            if (cp.get("ad_provider") in HALFHOUR_SYNC_PROVIDERS
                    and cp.get("sync_via") != "make_com"):
                pass  # AM/PM scheduler will book this account.
            else:
                # Iter-205 — also write the DELTA to the Universal
                # Ledger (idempotent via the per-(cp,date,source,delta)
                # key, so retried cron passes are safe).
                await _post_spend_to_ledger(
                    db, user_id=user_id, actor_name="ad_account_cron",
                    cp=cp, amount=delta, spend_date=to_date,
                    source="ad_account_cron",
                    description=(
                        f"مزامنة تراكمية من {cp.get('ad_provider')} — "
                        f"{to_date} (دلتا {delta})"
                    ),
                    extra_metadata={
                        "delta": delta,
                        "platform_total": total,
                        "prev_total_applied": prev_total_applied,
                        "source_collection": source,
                    },
                )
        else:
            # delta < 0 — platform reported LESS than what we already
            # logged today (rare correction). Refund the absolute delta
            # to the balance.
            #
            # Iter-169 — BUG FIX. Previously this branch deliberately did
            # NOT touch the open liability (comment: "if the user wants
            # to reduce a paid liability, that's a separate manual
            # action"). In practice that left a STALE high debt on the
            # account card (e.g. 201,753.81 displayed while the audit
            # log clearly showed the correction was applied). The fix:
            # if this account has an auto-generated open liability from
            # the cron sync, reduce its expected_amount by the same
            # refund amount (capped so we never go below paid_amount).
            refund = round(-delta, 2)
            new_balance = round(balance_before + refund, 2)
            await db.counterparties.update_one(
                {"id": cp["id"]},
                {"$set": {"balance": new_balance,
                          "last_auto_sync_date": to_date,
                          "last_auto_sync_at": _now(),
                          "updated_at": _now()}},
            )

            # Iter-169 — also unwind any auto_cron liability so the card
            # debt figure mirrors the audit log immediately.
            new_debt_after = 0.0
            existing_debt = await db.liabilities.find_one(
                {"user_id": user_id, "counterparty_id": cp["id"],
                 "kind": "ad_account", "auto_generated": True,
                 "source": "ad_account_cron",
                 "status": {"$in": ["unpaid", "partial"]}},
                sort=[("created_at", 1)],
            )
            if existing_debt:
                paid = float(existing_debt.get("paid_amount") or 0)
                expected = float(existing_debt.get("expected_amount") or 0)
                # Subtract refund from expected, but never go below paid
                # (which would make the row negative-balance).
                new_expected = max(paid, expected - refund)
                remaining = max(0.0, new_expected - paid)
                new_status = "paid" if remaining < 0.01 else (
                    "partial" if paid > 0 else "unpaid")
                await db.liabilities.update_one(
                    {"id": existing_debt["id"], "user_id": user_id},
                    {"$set": {
                        "expected_amount": round(new_expected, 2),
                        "status": new_status,
                        "updated_at": _now(),
                        "auto_correction_applied": (
                            float(existing_debt.get(
                                "auto_correction_applied") or 0)
                            + round(min(refund, expected - paid), 2)),
                    }},
                )
                new_debt_after = round(remaining, 2)

            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": user_id,
                "counterparty_id": cp["id"], "type": "spend",
                "amount": delta,  # negative — keeps sum() consistent
                "balance_after": new_balance,
                "debt_after": new_debt_after,
                "related_liability_id": (
                    existing_debt["id"] if existing_debt else None),
                "description": f"تصحيح مزامنة (إنخفاض إنفاق) — {cp['ad_provider']}",
                "breakdown": {"from_balance": 0.0, "uncovered": 0.0,
                              "mode": mode, "auto_cron": True,
                              "source_collection": source,
                              "created_debt": 0.0,
                              "correction": True,
                              "delta_applied": delta,
                              "liability_reduced_by": round(
                                  min(refund, (existing_debt or {}).get(
                                      "expected_amount", 0)
                                      - (existing_debt or {}).get(
                                          "paid_amount", 0)), 2)
                              if existing_debt else 0,
                              "platform_total": total,
                              "prev_total_applied": prev_total_applied},
                "date": to_date, "created_at": _now(),
            })
            out.append({
                "id": cp["id"], "name": cp["name"], "spend": total,
                "covered": 0.0, "uncovered": 0.0,
                "debt_created": 0.0,
                "debt_after": new_debt_after,
                "source_collection": source,
                "delta_applied": delta,
                "prev_total_applied": prev_total_applied,
                "correction": True,
            })
    return out


async def run_daily_cron(db) -> dict:
    """Iterate ALL users with at least one ad-account counterparty and
    sync today's spend.  Idempotent — re-runs reverse the previous
    cron rows of the same day and apply fresh totals (Iter-110 fix B).

    Iter-139 — this used to be invoked from a 23:55 daily scheduler.
    Now it's called every 30 minutes by `_ad_account_halfhour_sync`
    in server.py so daily ad-balances reflect spend in near-realtime.
    """
    # Iter-140 — Asia/Riyadh calendar date (server runs in UTC).
    today = riyadh_today_iso()
    users_done = []
    seen_users = set()
    async for cp in db.counterparties.find(
        {"kind": "ad_account"}, {"_id": 0, "user_id": 1},
    ):
        if cp["user_id"] in seen_users:
            continue
        seen_users.add(cp["user_id"])
        # Iter-139 — force=True so the half-hour cadence keeps the
        # ad-account balance / liability in sync with the latest
        # platform spend without double-counting earlier passes of
        # the SAME day.
        results = await _run_sync_for_all(
            db, cp["user_id"], today, today, force=True,
        )
        users_done.append({"user_id": cp["user_id"], "results": results})
    return {"ran_at": _now(), "today": today,
            "users_processed": len(users_done), "details": users_done}


# ── Iter-159k — Final sync for YESTERDAY (runs once per day) ───────────
# The half-hour cron above only syncs `today`.  At midnight Riyadh time
# we still need one last pull for the *previous* day to ensure delayed
# platform impressions (Snapchat/Meta sometimes post conversions 2-6 h
# after midnight) are captured.  This task is idempotent thanks to the
# cumulative-ledger logic (Iter-159f) — running it again on the same
# day is a no-op when no new platform data arrived.
async def run_yesterday_final_sync(db) -> dict:
    """For each user with ad accounts, do ONE final sync for the
    Riyadh-yesterday date.  A `last_yesterday_sync_date` marker on the
    counterparty prevents running more than once per calendar day.
    """
    from datetime import timedelta as _td
    today = riyadh_today_iso()
    yesterday = (datetime.fromisoformat(today) - _td(days=1)) \
        .date().isoformat()
    users_done = []
    seen_users = set()
    async for cp in db.counterparties.find(
        {"kind": "ad_account"},
        {"_id": 0, "user_id": 1, "last_yesterday_sync_date": 1},
    ):
        uid = cp["user_id"]
        if uid in seen_users:
            continue
        seen_users.add(uid)
        # Guard: if any account for this user already finalised yesterday
        # today, skip the whole batch.
        marker = await db.counterparties.find_one(
            {"user_id": uid, "kind": "ad_account",
             "last_yesterday_sync_date": today,
             "last_yesterday_synced_for": yesterday},
            {"_id": 0, "id": 1},
        )
        if marker:
            continue
        results = await _run_sync_for_all(
            db, uid, yesterday, yesterday, force=True,
        )
        await db.counterparties.update_many(
            {"user_id": uid, "kind": "ad_account"},
            {"$set": {"last_yesterday_sync_date": today,
                      "last_yesterday_synced_for": yesterday,
                      "updated_at": _now()}},
        )
        users_done.append({"user_id": uid, "results": results})
    return {"ran_at": _now(), "yesterday": yesterday,
            "users_processed": len(users_done), "details": users_done}
