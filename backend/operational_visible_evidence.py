"""Bounded native evidence acquisition owned by an explicit visible refresh.

Only gaps in the last saved snapshot are inspected. The caller reconciles once
after this stage; no scheduler, second calculation loop, or financial writer is
introduced here. Source proof writes retain the adapters' version-bound CAS.
"""
import asyncio

import httpx

from operational_balance_archive import ArchiveError, ISSUES, SNAPSHOT, unpack_financial_snapshot, unpack_issues
from operational_balance_store import STATES, check_authorization, fail
from operational_demand_refresh import calculation_checkpoint, checkpoint, demand_context, run_calculation


ACQUISITION_SECONDS = 25
LIMIT_PER_SOURCE = 4
_COLLECTIONS = frozenset({STATES, "unified_orders", "salla_integrations", "salla_payment_banks",
                          "salla_webhook_event_captures"})
_LOCAL_PROOF_READS = frozenset({'mz2_shipping_setup_v2', 'store_delivery_assignments', 'order_activity_events_v2'})
_PROJECTION = {"_id": 0, "owner_id": 1, "status": 1, "started_at": 1,
               "engine.issues": 1, "engine.customer_tender_coverage": 1, 'engine.facts': 1,
               'engine.obligations': 1, 'engine.delivery_register': 1, 'movements.allocations': 1,
               'courier_tariffs': 1, ISSUES: 1, SNAPSHOT: 1}


def _gaps(stored):
    return unpack_issues(unpack_financial_snapshot(stored,
        selected={"customer_tender_coverage", 'facts', 'obligations', 'delivery_register'}))


def _courier_status_exclusions(state, issues):
    """Keep dated financial history on the same recovery path as the resolver."""
    from operational_courier_fee_history import build_recorded_courier_fee_lookup
    from operational_delivery_evidence import status_delivery
    facts = state.get('engine', {}).get('facts', {})
    recorded_fee = build_recorded_courier_fee_lookup(state)
    excluded, seen = set(), set()
    for row in issues:
        calculation_checkpoint()
        identity = str(row.get('source_id') or '')
        if not identity or identity in seen:
            continue
        seen.add(identity)
        frozen_event = facts.get('delivery_event:' + identity)
        # A prior status event remains eligible even after its fee was saved.
        if not status_delivery(frozen_event) and (
                frozen_event is not None or facts.get('shipping:' + identity) is not None
                or recorded_fee(identity)):
            excluded.add(identity)
    return frozenset(excluded)


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
    def __init__(self, collection, *, read_only=False):
        self._collection = collection
        self._read_only = read_only

    def find(self, *args, **kwargs):
        # Motor find constructs a lazy cursor. Authorization precedes its I/O.
        return _Cursor(self._collection.find(*args, **kwargs))

    def __getattr__(self, name):
        if name not in {"find_one", "find_one_and_update", "update_one", "delete_one"}:
            raise AttributeError(name)
        if self._read_only and name != 'find_one':
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
        if name not in _COLLECTIONS | _LOCAL_PROOF_READS:
            raise AttributeError(name)
        return _Collection(self._db[name], read_only=name in _LOCAL_PROOF_READS)

    def __getattr__(self, name):
        return self[name]


async def _unconfirmed_courier_dates(db, owner, state, issues):
    """Recheck at most four saved manual proofs before buying native history.

    A proof only replaces a missing delivery clock. Any other acquisition gap,
    changed executor, negative local history or invalid proof retains the old
    acquisition path. These bounded local reads share the same visible budget.
    """
    from operational_delivery_confirmation import saved_record
    from operational_delivery_confirmation_sources import read_candidate
    codes = {}
    for row in issues:
        codes.setdefault(str(row.get('source_id') or ''), set()).add(row.get('code'))
    skipped, inspected = set(), 0
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat()
    for identity in sorted(codes):
        if codes[identity] != {'operational_delivery_timestamp_missing'}:
            continue
        try:
            record = saved_record(state, identity)
            if record is None or record['party_type'] != 'courier':
                continue
            if inspected >= LIMIT_PER_SOURCE:
                break
            inspected += 1
            await checkpoint()
            await read_candidate(db, owner, state,
                {key: record[key] for key in ('order_id', 'order_number', 'party_type', 'party_id')},
                stamp, record=record)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            continue
        skipped.add(identity)
    return [row for row in issues if str(row.get('source_id') or '') not in skipped]


async def acquire_visible_evidence(db, owner, *, courier_order_id=None):
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
    from operational_bank_order_milestone import HISTORY_MISSING
    bank = [issue for issue in issues if issue.get("code") in {"operational_order_bank_binding_incomplete", HISTORY_MISSING}]
    courier = [issue for issue in issues if issue.get("code") in ISSUE_CODES]
    sar = (state.get("engine", {}).get("customer_tender_coverage", {}).get("sar", {}).get("proof_lookups") or [])
    if courier_order_id is not None:
        # The explicit target is an intersection with this owner's saved gaps,
        # never a request to inspect an arbitrary provider order or all orders.
        courier = [issue for issue in courier if str(issue.get("source_id") or "") == courier_order_id]
        if not courier:
            fail("operational_courier_target_not_pending", "لم يعد الطلب ضمن حالات مطابقة الشحن في التقرير المحفوظ؛ حدّث الصفحة", 409)
        bank, sar = [], []
    result = {"status": "attempted", "bank_candidates": len(bank), "courier_candidates": len(courier),
              "sar_candidates": len(sar)}
    jobs = [(name, pending) for name, pending in (("bank", bank), ("courier", courier), ("sar", sar)) if pending]
    if courier_order_id is None:
        from operational_return_waybills import MISSING
        returns = [i for i in issues if i.get("code") == MISSING]
        if returns:
            jobs.append(("return_waybills", returns))
        from operational_delivery_register import POLICY
        pending_history = state.get("engine", {}).get("delivery_register", {}).get(POLICY, {}).get("history_candidates")
        if pending_history:
            jobs.append(("delivery_history", pending_history))
    finished = set()
    deadline = asyncio.get_running_loop().time() + ACQUISITION_SECONDS

    def deferred(name, reason):
        result.update(status="deferred", reason=reason)
        # A cancelled adapter may have saved earlier proofs. Counts are unknown,
        # so do not misreport zero successes or a completed native acquisition.
        result[name] = {"status": "deferred", "reason": reason}

    try:
        async with asyncio.timeout(ACQUISITION_SECONDS):
            for index, (name, pending) in enumerate(jobs):
                await checkpoint()
                # Reserve time for each remaining source. Completed adapters
                # donate unused time; exhausted adapters are cancelled and
                # joined before the next one starts. Native reads stay serial.
                remaining = max(0, deadline - asyncio.get_running_loop().time())
                source_budget = remaining / (len(jobs) - index)
                try:
                    async with asyncio.timeout(source_budget):
                        if name == "bank":
                            await acquire_bank(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                        elif name == "return_waybills":
                            from salla_integration.return_waybill_sync import acquire
                            result[name] = await acquire(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                        elif name == "delivery_history":
                            from salla_integration.retained_delivery_history import acquire
                            result[name] = await acquire(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                        elif name == "courier":
                            pending = await _unconfirmed_courier_dates(guarded, owner, state, pending)
                            if pending:
                                excluded = await run_calculation(_courier_status_exclusions, state, pending)
                                receipt = await acquire_courier(guarded, owner, pending, started_at=state["started_at"],
                                    limit=1 if courier_order_id is not None else LIMIT_PER_SOURCE,
                                    status_excluded_order_ids=excluded)
                                result["courier"] = receipt
                                if receipt and receipt.get("status") == "deferred":
                                    result.update(status="deferred", reason="native_evidence_unavailable")
                            else:
                                result["courier"] = {"status": "completed", "selected": 0, "outcomes": [],
                                                     "reason": "native_evidence_not_required"}
                        else:
                            await acquire_sar(guarded, owner, pending, limit=LIMIT_PER_SOURCE)
                except (SallaError, httpx.RequestError):
                    # Missing native evidence remains an explicit saved-source
                    # gap. Local authorization/identity errors are never caught.
                    deferred(name, "native_evidence_unavailable")
                except TimeoutError:
                    deferred(name, "native_evidence_budget_exhausted")
                finished.add(name)
    except TimeoutError:
        # The timeout cancels and joins the awaited adapter; nothing continues
        # after this request. Any completed source CAS is safe to replay.
        result.update(status="deferred", reason="native_evidence_budget_exhausted")
        for name, _ in jobs:
            if name not in finished:
                deferred(name, "native_evidence_budget_exhausted")
    await checkpoint()
    return result
