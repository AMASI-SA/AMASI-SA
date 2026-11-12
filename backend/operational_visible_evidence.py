"""Bounded native evidence acquisition owned by an explicit visible refresh.

Only gaps in the last saved snapshot are inspected. The caller reconciles once
after this stage; no scheduler, second calculation loop, or financial writer is
introduced here. Source proof writes retain the adapters' version-bound CAS.
"""
import asyncio

import httpx

from operational_balance_archive import ArchiveError, ISSUES, SNAPSHOT, unpack_financial_snapshot, unpack_issues
from operational_balance_store import STATES, check_authorization, fail
from operational_demand_refresh import checkpoint, demand_context, run_calculation


ACQUISITION_SECONDS = 25
LIMIT_PER_SOURCE = 4
_COLLECTIONS = frozenset({STATES, "unified_orders", "salla_integrations", "salla_payment_banks",
                          "salla_webhook_event_captures"})
_PROJECTION = {"_id": 0, "owner_id": 1, "status": 1, "started_at": 1,
               "engine.issues": 1, "engine.customer_tender_coverage": 1, ISSUES: 1, SNAPSHOT: 1}


def _gaps(stored):
    return unpack_issues(unpack_financial_snapshot(stored, selected={"customer_tender_coverage"}))


class _Cursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def max_time_ms(self, value):
        self._cursor = self._cursor.max_time_ms(value)
        return self

    def limit(self, value):
        self._cursor = self._cursor.limit(value)
        return self

    async def to_list(self, *args, **kwargs):
        await check_authorization()
        return await self._cursor.to_list(*args, **kwargs)

    def __aiter__(self):
        return self

    async def __anext__(self):
        await check_authorization()
        return await self._cursor.__anext__()


class _Collection:
    """Guard nested connector/cache/capture reads as well as the final proof CAS."""
    def __init__(self, collection):
        self._collection = collection

    def find(self, *args, **kwargs):
        # Motor find constructs a lazy cursor. Authorization precedes its I/O.
        return _Cursor(self._collection.find(*args, **kwargs))

    def __getattr__(self, name):
        if name not in {"find_one", "find_one_and_update", "update_one", "delete_one"}:
            raise AttributeError(name)

        async def execute(*args, **kwargs):
            await check_authorization()
            return await getattr(self._collection, name)(*args, **kwargs)
        return execute


class _EvidenceDatabase:
    """Expose only the existing adapters' source and connector collections."""
    def __init__(self, db):
        self._db = db

    def __getitem__(self, name):
        if name not in _COLLECTIONS:
            raise AttributeError(name)
        return _Collection(self._db[name])

    def __getattr__(self, name):
        return self[name]


async def acquire_visible_evidence(db, owner):
    """Acquire at most four proofs per source within this visible request."""
    context = demand_context()
    if context is None or context.db is not db or context.owner != owner:
        fail("operational_visible_refresh_required", "جلب الأدلة متاح أثناء تحديث الصفحة المفتوحة فقط", 403)
    await checkpoint()
    guarded = _EvidenceDatabase(db)
    stored = await guarded[STATES].find_one({"_id": owner, "owner_id": owner}, _PROJECTION)
    if not stored or stored.get("owner_id") != owner or stored.get("status") != "active":
        fail("operational_not_active", "النظام التشغيلي غير متاح للتحديث", 409)
    try:
        state = await run_calculation(_gaps, stored)
    except ArchiveError:
        fail("operational_source_issues_invalid", "تعذر التحقق من قائمة الأدلة المطلوبة", 503)
    from salla_integration.bank_payment_sync import acquire_missing as acquire_bank
    from salla_integration.courier_delivery_sync import ISSUE_CODES, acquire_missing as acquire_courier
    from salla_integration.paid_sar_sync import acquire_missing as acquire_sar
    from salla_integration.service import SallaError

    issues = state.get("engine", {}).get("issues", [])
    bank = [issue for issue in issues if issue.get("code") == "operational_order_bank_binding_incomplete"]
    courier = [issue for issue in issues if issue.get("code") in ISSUE_CODES]
    sar = (state.get("engine", {}).get("customer_tender_coverage", {}).get("sar", {}).get("proof_lookups") or [])
    result = {"status": "attempted", "bank_candidates": len(bank), "courier_candidates": len(courier),
              "sar_candidates": len(sar)}
    try:
        async with asyncio.timeout(ACQUISITION_SECONDS):
            for name, pending in (("bank", bank), ("courier", courier), ("sar", sar)):
                if not pending:
                    continue
                await checkpoint()
                try:
                    if name == "bank":
                        await acquire_bank(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                    elif name == "courier":
                        await acquire_courier(guarded, owner, pending, started_at=state["started_at"], limit=LIMIT_PER_SOURCE)
                    else:
                        await acquire_sar(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                except (SallaError, httpx.RequestError):
                    # Missing native evidence remains an explicit saved-source
                    # gap. Local authorization/identity errors are never caught.
                    result.update(status="deferred", reason="native_evidence_unavailable")
    except TimeoutError:
        # The timeout cancels and joins the awaited adapter; nothing continues
        # after this request. Any completed source CAS is safe to replay.
        result.update(status="deferred", reason="native_evidence_budget_exhausted")
    await checkpoint()
    return result
