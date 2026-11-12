"""Attach proven canonical order references without changing financial evidence.

The query is restricted to the current owner's exact Salla source IDs. Only
identity and refresh metadata are read; numbers, balances and settlements come
unchanged from the operational report supplied by the caller.
"""
from collections import defaultdict
from copy import deepcopy
import asyncio
import re

from pymongo.errors import PyMongoError


MAX_ROWS = 20_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
PROJECTION = {
    "_id": 0, "user_id": 1,
    "raw_by_source.salla_direct.id": 1,
    "raw_by_source.salla_direct.reference_id": 1,
    "raw_by_source.salla_direct.order_number": 1,
    "g47_salla_snapshot.requires_authoritative_refresh": 1,
}
NOTES = {
    "missing": "غير متاح: تعذر إثبات رقم بعض الطلبات من مصدر سلة؛ يعرض معرف الطلب دون تغيير الأرصدة.",
    "identity": "غير متاح: هوية بعض الطلبات غير مكتملة؛ لم يُخمّن رقم الطلب.",
    "ambiguous": "غير متاح: توجد سجلات متعارضة لهوية بعض الطلبات أو أرقامها؛ لم يُختر رقم من بينها.",
    "stale": "غير متاح: يحتاج مصدر سلة لبعض الطلبات إلى تحديث موثق قبل عرض أرقامها.",
    "scope": "غير متاح: تعذر التحقق من نطاق مصدر أرقام الطلبات؛ لم تُستخدم بيانات من حساب آخر.",
    "limit": "غير متاح: تجاوز عدد سجلات الطلبات حد القراءة المحدد؛ لم تُعرض مطابقة جزئية كأنها مكتملة.",
    "unavailable": "غير متاح: تعذرت قراءة أرقام الطلبات الآن؛ تبقى الأرصدة المحفوظة ظاهرة دون تغيير.",
}


def _text(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if isinstance(value, int) and value <= 0:
        return None
    value = str(value)
    if not value or len(value) > 200 or value.strip() != value or any(ord(char) < 32 for char in value):
        return None
    return value


def _identity(value):
    if isinstance(value, int) and not isinstance(value, bool) and value > MAX_SAFE_INTEGER:
        return None
    return _text(value)


def _raw(row):
    sources = row.get("raw_by_source")
    return sources.get("salla_direct") if isinstance(sources, dict) else None


def _mark(details, reason):
    if isinstance(details, tuple):
        for container in details:
            _mark(container, reason)
        return
    details["complete"] = False
    notes = details.setdefault("notes", [])
    if NOTES[reason] not in notes:
        notes.append(NOTES[reason])


async def attach_order_numbers(db, owner, result):
    """Return a copied report with labels proven by one bounded read only."""
    enriched = deepcopy(result)
    targets = []

    def target(row, containers):
        row['order_number'] = None
        identity = _identity(row.get('order_id'))
        if identity is None:
            _mark(containers, 'identity')
        else:
            targets.append((containers, row, identity))

    # The same canonical ID can occur in delivery details, a counterparty's
    # exceptions, and an unassigned table. One identity read serves them all.
    for section in [enriched, *(enriched.get('currencies') or {}).values()]:
        details = section.get('deliveries') or {}
        for row in details.get('rows') or []:
            target(row, (details,))
        for table in section.get('tables') or []:
            for row in table.get('rows') or []:
                for pending in row.get('pending_order_details') or []:
                    target(pending, (table, row))
            for pending in table.get('unassigned_order_details') or []:
                target(pending, (table,))
        for party in section.get('parties') or []:
            for pending in party.get('pending_order_details') or []:
                target(pending, (party,))
        # The main balance report groups unresolved delivery identities by
        # currency and family, without assigning them to a named counterparty.
        unassigned = section.get('unassigned_order_details')
        if isinstance(unassigned, dict):
            for families in unassigned.values():
                if isinstance(families, dict):
                    for entries in families.values():
                        for pending in entries or []:
                            target(pending, (section,))
    if not targets:
        return enriched

    identities = {identity for _, _, identity in targets}
    if not isinstance(owner, str) or not owner.strip() or len(identities) > MAX_ROWS:
        reason = "scope" if not isinstance(owner, str) or not owner.strip() else "limit"
        for details, _, _ in targets:
            _mark(details, reason)
        return enriched

    variants = []
    for identity in sorted(identities):
        variants.append(identity)
        if re.fullmatch(r"[1-9][0-9]{0,15}", identity) and int(identity) <= MAX_SAFE_INTEGER:
            variants.append(int(identity))
    query = {"user_id": owner, "raw_by_source.salla_direct.id": {"$in": variants}}
    try:
        documents = await asyncio.wait_for(db['unified_orders'].find(query, PROJECTION)
            .limit(MAX_ROWS + 1).to_list(length=MAX_ROWS + 1), timeout=10)
    except (PyMongoError, asyncio.TimeoutError):
        for details, _, _ in targets:
            _mark(details, 'unavailable')
        return enriched
    if len(documents) > MAX_ROWS:
        for details, _, _ in targets:
            _mark(details, "limit")
        return enriched
    grouped = defaultdict(list)
    for document in documents:
        if not isinstance(document, dict):
            for details, _, _ in targets:
                _mark(details, 'scope')
            return enriched
        raw = _raw(document)
        identity = _identity(raw.get("id")) if isinstance(raw, dict) else None
        if document.get("user_id") != owner or identity not in identities:
            for details, _, _ in targets:
                _mark(details, "scope")
            return enriched
        grouped[identity].append(document)

    numbers, problems = {}, {}
    for identity in identities:
        matches = grouped[identity]
        if len(matches) != 1:
            problems[identity] = "ambiguous" if matches else "missing"
            continue
        document = matches[0]
        watermark = document.get("g47_salla_snapshot")
        if (watermark is not None and not isinstance(watermark, dict)
                or isinstance(watermark, dict) and watermark.get("requires_authoritative_refresh")):
            problems[identity] = "stale"
            continue
        raw = _raw(document)
        # Match the canonical source collector's reference priority. A source
        # ID is never substituted for a missing public order reference.
        reference = raw.get("reference_id")
        number = _text(raw.get("order_number") if reference in (None, "") else reference)
        if number is None:
            problems[identity] = "missing"
        else:
            numbers[identity] = number
    by_number = defaultdict(set)
    for identity, number in numbers.items():
        by_number[number].add(identity)
    for matches in by_number.values():
        if len(matches) > 1:
            for identity in matches:
                problems[identity] = "ambiguous"
    for details, row, identity in targets:
        if identity in problems:
            _mark(details, problems[identity])
        else:
            row["order_number"] = numbers[identity]
    return enriched
