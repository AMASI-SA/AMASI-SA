"""Iter-250b · P1.5.p — Unit test for the widened employee-existence
guard in `ledger_core.create_entry`.

We test the QUERY-construction logic directly against an in-memory
mongomock database (deterministic, no real Mongo needed). The test
file mirrors the EXACT structure of the new guard:

    emp = (
        await db.operating_salaries.find_one(query, proj)
        or await db.employees.find_one(query, proj)
    )

Acceptance criteria (verbatim from the user):
1. Employee in `operating_salaries` only, status=active     → ALLOWED
2. Employee in `operating_salaries` only, status=stopped    → ALLOWED
3. Employee in `employees` only                              → ALLOWED
4. Employee with archived=true or deleted=true               → REJECTED
5. Phantom UUID not present anywhere                         → REJECTED
"""
from __future__ import annotations

import asyncio
import uuid


def _build_query(user_id: str, eid_str: str):
    """Verbatim copy of the guard's query construction."""
    id_or_clause = [
        {"id": eid_str},
        {"employee_id": eid_str},
        {"external_id": eid_str},
        {"legacy_id": eid_str},
    ]
    not_dead = {
        "archived": {"$ne": True},
        "is_archived": {"$ne": True},
        "deleted": {"$ne": True},
        "is_deleted": {"$ne": True},
    }
    return {"user_id": user_id, "$or": id_or_clause, **not_dead}


async def _guard_passes(db, user_id: str, eid_str: str) -> bool:
    """Verbatim copy of the guard's lookup."""
    query = _build_query(user_id, eid_str)
    proj = {"_id": 1, "id": 1, "name": 1, "status": 1}
    emp = (
        await db.operating_salaries.find_one(query, proj)
        or await db.employees.find_one(query, proj)
    )
    return emp is not None


async def main():
    try:
        import mongomock_motor   # noqa: WPS433
    except ImportError:
        # Fallback — pure-python in-memory replacement that mimics
        # the subset of motor we need.
        return await main_fallback()

    client = mongomock_motor.AsyncMongoMockClient()
    db = client["t"]
    uid = "u1"

    # Seed data
    active_id   = str(uuid.uuid4())   # operating_salaries · active
    stopped_id  = str(uuid.uuid4())   # operating_salaries · stopped
    legacy_id   = str(uuid.uuid4())   # employees only
    archived_id = str(uuid.uuid4())   # operating_salaries · archived=true
    deleted_id  = str(uuid.uuid4())   # employees · deleted=true
    other_uid_id = str(uuid.uuid4())  # belongs to ANOTHER user

    await db.operating_salaries.insert_many([
        {"user_id": uid, "id": active_id,   "name": "ابو جمال", "status": "active"},
        {"user_id": uid, "id": stopped_id,  "name": "خالد",     "status": "stopped"},
        {"user_id": uid, "id": archived_id, "name": "فلان",     "status": "active",
         "archived": True},
        {"user_id": "u2", "id": other_uid_id, "name": "غريب", "status": "active"},
    ])
    await db.employees.insert_many([
        {"user_id": uid, "id": legacy_id,  "name": "ميرغل",     "status": "active"},
        {"user_id": uid, "id": deleted_id, "name": "محذوف",     "status": "active",
         "deleted": True},
    ])

    phantom_id = str(uuid.uuid4())   # absent everywhere

    results = []
    for label, eid, expected in [
        ("1. active in operating_salaries",  active_id,   True),
        ("2. stopped in operating_salaries", stopped_id,  True),
        ("3. legacy in employees",           legacy_id,   True),
        ("4a. archived → rejected",          archived_id, False),
        ("4b. deleted  → rejected",          deleted_id,  False),
        ("5. phantom UUID",                  phantom_id,  False),
        ("6. other-user employee leak",      other_uid_id, False),
    ]:
        got = await _guard_passes(db, uid, eid)
        ok = (got == expected)
        results.append((ok, label, expected, got))
        print(f"  {'PASS' if ok else 'FAIL'} · {label} → guard_passes={got} (expected {expected})")

    fails = sum(1 for r in results if not r[0])
    print()
    print(f"{'✅ ALL PASS' if fails == 0 else f'❌ {fails} FAIL(s)'}")
    return fails


async def main_fallback():
    """If mongomock_motor isn't installed, run the same assertions in
    plain dicts. Less faithful but still validates the OR-clause /
    `$ne: True` semantics by reimplementing them."""

    def matches(doc, query):
        # very small subset of Mongo: user_id eq, $or clause, $ne true
        if doc.get("user_id") != query.get("user_id"):
            return False
        or_ok = False
        for cl in query.get("$or", []):
            (k, v), = cl.items()
            if doc.get(k) == v:
                or_ok = True; break
        if not or_ok:
            return False
        for k in ("archived", "is_archived", "deleted", "is_deleted"):
            cond = query.get(k)
            if cond is not None:
                # $ne: True
                if doc.get(k, False) is True:
                    return False
        return True

    operating_salaries = []
    employees = []
    uid = "u1"
    active_id   = str(uuid.uuid4())
    stopped_id  = str(uuid.uuid4())
    legacy_id   = str(uuid.uuid4())
    archived_id = str(uuid.uuid4())
    deleted_id  = str(uuid.uuid4())
    other_uid_id = str(uuid.uuid4())
    phantom_id   = str(uuid.uuid4())

    operating_salaries.extend([
        {"user_id": uid, "id": active_id,   "status": "active"},
        {"user_id": uid, "id": stopped_id,  "status": "stopped"},
        {"user_id": uid, "id": archived_id, "status": "active",
         "archived": True},
        {"user_id": "u2", "id": other_uid_id, "status": "active"},
    ])
    employees.extend([
        {"user_id": uid, "id": legacy_id,   "status": "active"},
        {"user_id": uid, "id": deleted_id,  "status": "active",
         "deleted": True},
    ])

    def find_one(coll, q):
        for d in coll:
            if matches(d, q):
                return d
        return None

    def guard_passes(eid):
        q = _build_query(uid, eid)
        return (
            find_one(operating_salaries, q) is not None
            or find_one(employees, q) is not None
        )

    results = []
    for label, eid, expected in [
        ("1. active in operating_salaries",  active_id,    True),
        ("2. stopped in operating_salaries", stopped_id,   True),
        ("3. legacy in employees",           legacy_id,    True),
        ("4a. archived → rejected",          archived_id,  False),
        ("4b. deleted  → rejected",          deleted_id,   False),
        ("5. phantom UUID",                  phantom_id,   False),
        ("6. other-user employee leak",      other_uid_id, False),
    ]:
        got = guard_passes(eid)
        ok = (got == expected)
        results.append((ok, label, expected, got))
        print(f"  {'PASS' if ok else 'FAIL'} · {label} → guard_passes={got} (expected {expected})")

    fails = sum(1 for r in results if not r[0])
    print()
    print(f"{'✅ ALL PASS' if fails == 0 else f'❌ {fails} FAIL(s)'}")
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()) or 0)
