"""Iter-250b · P1.5.ab — Suppliers Unification Forensic (READ-ONLY).

The user observed a logical split between two screens:

  * `/suppliers-new` (management tab) reads ONLY `db.suppliers`
    → users like "خالد" appear here with `financial_movements`
      but never appear in the ledger because no GL entry exists.

  * `/suppliers-new?tab=balances` (ledger tab) reads
    `db.counterparties` + `general_ledger`
    → users like "العنبري" appear here with GL entries but
      never appear in `db.suppliers`.

This module exposes TWO **strictly READ-ONLY** endpoints that unify
both sources and surface the relationship between them:

  GET /api/suppliers/unified
      Merged list for the Management tab. Each row carries a
      `link_status` field (`new_only` | `ledger_only` | `linked`)
      so the UI can render a clear badge per row.

  GET /api/audit/suppliers-unification-forensic
      Full diagnostic dump:
        * counts per category
        * lists per category
        * fuzzy duplicate suspects matched by lowercased name /
          phone / email across the two sources.

No writes. No migrations. No automatic linking. The user explicitly
forbade any auto-fix at this stage.
"""
from __future__ import annotations

import re
from typing import Any
from fastapi import APIRouter, Depends


def _norm_lower(value: Any) -> str:
    """Lowercase + collapse whitespace + strip Arabic tatweel."""
    s = (str(value or "")).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("\u0640", "")  # arabic tatweel
    return s


def _norm_phone(value: Any) -> str:
    """Keep digits only (handles +966, leading 0, spaces)."""
    s = str(value or "")
    return re.sub(r"\D", "", s)


async def _load_suppliers(db, uid: str) -> list[dict]:
    """db.suppliers — the new (Iter-244) management entries."""
    return await db.suppliers.find(
        {"user_id": uid},
        {"_id": 0, "id": 1, "company_name": 1, "contact_person": 1,
         "phone": 1, "email": 1, "status": 1, "category_ids": 1,
         "created_at": 1, "updated_at": 1},
    ).sort([("created_at", -1)]).to_list(5000)


async def _load_counterparties_suppliers(db, uid: str) -> list[dict]:
    """db.counterparties (kind=supplier) — legacy + bridged rows."""
    return await db.counterparties.find(
        {"user_id": uid, "kind": "supplier"},
        {"_id": 0},
    ).to_list(5000)


async def _gl_referenced_supplier_ids(db, uid: str) -> set[str]:
    """All supplier entity_ids that appear in general_ledger."""
    ids: set[str] = set()
    async for doc in db.general_ledger.find(
        {"user_id": uid, "entity_type": "supplier", "status": "posted"},
        {"_id": 0, "entity_id": 1},
    ):
        eid = doc.get("entity_id")
        if eid:
            ids.add(eid)
    return ids


async def _movements_referenced_supplier_ids(db, uid: str) -> set[str]:
    """All supplier_ids that appear in financial_movements."""
    ids: set[str] = set()
    async for m in db.financial_movements.find(
        {"user_id": uid, "supplier_id": {"$exists": True, "$ne": None}},
        {"_id": 0, "supplier_id": 1},
    ):
        sid = m.get("supplier_id")
        if sid:
            ids.add(sid)
    return ids


async def _last_activity_bulk(
    db, uid: str, supplier_ids: list[str],
) -> dict[str, dict]:
    """Compute the most recent activity timestamps PER supplier across
    both GL and financial_movements in TWO bulk Mongo aggregations
    (one per collection) — no N+1.

    Returns: { supplier_id: { last_gl, last_fm } } as ISO strings or
    None when absent.
    """
    if not supplier_ids:
        return {}

    out: dict[str, dict] = {sid: {"last_gl": None, "last_fm": None}
                            for sid in supplier_ids}

    # GL — prefer txn_date (business date) else created_at.
    gl_pipeline = [
        {"$match": {
            "user_id": uid,
            "entity_type": "supplier",
            "entity_id": {"$in": supplier_ids},
            "status": "posted",
        }},
        {"$group": {
            "_id": "$entity_id",
            "last_txn_date":   {"$max": "$txn_date"},
            "last_created_at": {"$max": "$created_at"},
        }},
    ]
    async for row in db.general_ledger.aggregate(gl_pipeline):
        sid = row.get("_id")
        if sid in out:
            out[sid]["last_gl"] = (
                row.get("last_txn_date") or row.get("last_created_at")
            )

    # FM — prefer entry_date else created_at.
    fm_pipeline = [
        {"$match": {
            "user_id": uid,
            "supplier_id": {"$in": supplier_ids},
        }},
        {"$group": {
            "_id": "$supplier_id",
            "last_entry_date": {"$max": "$entry_date"},
            "last_created_at": {"$max": "$created_at"},
        }},
    ]
    async for row in db.financial_movements.aggregate(fm_pipeline):
        sid = row.get("_id")
        if sid in out:
            out[sid]["last_fm"] = (
                row.get("last_entry_date") or row.get("last_created_at")
            )

    return out


def _activity_summary(act: dict) -> dict:
    """Derive `last_activity`, `activity_source` and the
    active/inactive badge from the raw {last_gl, last_fm} pair.

    A supplier is "active" when its most-recent activity (across either
    source) is within the last 90 days. 90d is a soft threshold that
    matches the user's request — easy to tune in one place.
    """
    from datetime import datetime, timezone, timedelta

    def _to_dt(v):
        if not v:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        try:
            s = str(v)
            # Mongo may store with `Z` suffix.
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except Exception:
            return None

    gl_dt = _to_dt(act.get("last_gl"))
    fm_dt = _to_dt(act.get("last_fm"))

    if gl_dt and fm_dt:
        source = "both"
        last_dt = max(gl_dt, fm_dt)
    elif gl_dt:
        source = "gl"
        last_dt = gl_dt
    elif fm_dt:
        source = "fm"
        last_dt = fm_dt
    else:
        source = "none"
        last_dt = None

    is_active = False
    days_since = None
    if last_dt:
        now = datetime.now(timezone.utc)
        delta = now - last_dt
        days_since = delta.days
        is_active = days_since <= 90

    return {
        "last_gl": (gl_dt.isoformat() if gl_dt else None),
        "last_fm": (fm_dt.isoformat() if fm_dt else None),
        "last_activity": (last_dt.isoformat() if last_dt else None),
        "activity_source": source,
        "is_active": is_active,
        "days_since_last_activity": days_since,
    }


async def _drift_count_for_supplier(
    db, uid: str, supplier_id: str,
) -> tuple[int, float]:
    """Return (count, total) of supplier_invoice movements that have
    no SUPPLIER-PAYABLE leg posted in GL."""
    mvs = await db.financial_movements.find(
        {"user_id": uid,
         "supplier_id": supplier_id,
         "movement_type": "supplier_invoice"},
        {"_id": 0, "ledger_txn_group_id": 1, "total_amount": 1},
    ).to_list(5000)
    if not mvs:
        return (0, 0.0)
    groups = [m.get("ledger_txn_group_id") for m in mvs
              if m.get("ledger_txn_group_id")]
    posted: set[str] = set()
    if groups:
        async for g in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "supplier",
             "sub_account": "payable",
             "txn_group_id": {"$in": groups},
             "status": "posted"},
            {"_id": 0, "txn_group_id": 1},
        ):
            posted.add(g["txn_group_id"])
    cnt = 0
    tot = 0.0
    for m in mvs:
        tg = m.get("ledger_txn_group_id")
        if (not tg) or (tg not in posted):
            cnt += 1
            tot += float(m.get("total_amount") or 0)
    return (cnt, round(tot, 2))


def make_suppliers_unification_forensic_router(db, current_user):
    router = APIRouter(tags=["suppliers-unification"])

    # ---------- Unified list (for Management tab) ----------
    @router.get("/suppliers-unified")
    async def suppliers_unified(user: dict = Depends(current_user)):
        """READ-ONLY merged list. Each row has:
          link_status  : new_only | ledger_only | linked
          source_ids   : { supplier_id?, counterparty_id? }
          outstanding_debt, drift_count, drift_total (from GL/FM)
        """
        uid = user["id"]
        sup_rows = await _load_suppliers(db, uid)
        cp_rows = await _load_counterparties_suppliers(db, uid)

        sup_by_id: dict[str, dict] = {s["id"]: s for s in sup_rows}
        cp_by_id: dict[str, dict] = {c["id"]: c for c in cp_rows}

        # Linked = same `id` in both (bridge keeps them aligned).
        linked_ids = set(sup_by_id) & set(cp_by_id)
        new_only_ids = set(sup_by_id) - linked_ids
        ledger_only_ids = set(cp_by_id) - linked_ids

        # GL-only ghosts: a supplier_id appears in general_ledger but
        # is ABSENT from both `db.suppliers` and `db.counterparties`.
        gl_ids = await _gl_referenced_supplier_ids(db, uid)
        fm_ids = await _movements_referenced_supplier_ids(db, uid)
        known_ids = set(sup_by_id) | set(cp_by_id)
        ghost_ids = (gl_ids | fm_ids) - known_ids

        # Compute balances in bulk for everyone we plan to surface.
        all_visible_ids = list(linked_ids | new_only_ids
                               | ledger_only_ids | ghost_ids)
        balances = {}
        if all_visible_ids:
            ledger_core = __import__("ledger_core")
            balances = await ledger_core.compute_balances_bulk(
                db, user_id=uid, entity_type="supplier",
                entity_ids=all_visible_ids,
            )

        # P1.5.ab.2 — Last-activity in bulk across GL + FM.
        activity_raw = await _last_activity_bulk(db, uid, all_visible_ids)

        rows: list[dict] = []

        def _push(link_status: str, sid: str,
                  sup: dict | None, cp: dict | None):
            name = (sup or {}).get("company_name") or \
                   (cp or {}).get("name") or "?"
            phone = (sup or {}).get("phone") or \
                    (cp or {}).get("phone")
            email = (sup or {}).get("email") or \
                    (cp or {}).get("email")
            contact = (sup or {}).get("contact_person") or \
                      (cp or {}).get("contact_person")
            status = (sup or {}).get("status") or \
                     (cp or {}).get("status") or "active"
            b = balances.get(sid, {})
            act = _activity_summary(activity_raw.get(sid) or {})
            rows.append({
                "id": sid,
                "link_status": link_status,
                "company_name": name,
                "contact_person": contact,
                "phone": phone,
                "email": email,
                "status": status,
                "category_ids": (sup or {}).get("category_ids") or [],
                "source_ids": {
                    "supplier_id":  sup.get("id") if sup else None,
                    "counterparty_id": cp.get("id") if cp else None,
                },
                "outstanding_debt": b.get("outstanding_debt", 0.0),
                "debits": b.get("debits", 0.0),
                "credits": b.get("credits", 0.0),
                "editable": link_status in ("new_only", "linked"),
                # P1.5.ab.2 — activity meta.
                **act,
            })

        for sid in linked_ids:
            _push("linked", sid, sup_by_id[sid], cp_by_id[sid])
        for sid in new_only_ids:
            _push("new_only", sid, sup_by_id[sid], None)
        for sid in ledger_only_ids:
            _push("ledger_only", sid, None, cp_by_id[sid])
        for sid in ghost_ids:
            # GL/FM ghosts get a synthetic row so the merchant can
            # see them. They are flagged so the UI never tries to
            # "edit" them through /suppliers patch.
            b = balances.get(sid, {})
            act = _activity_summary(activity_raw.get(sid) or {})
            rows.append({
                "id": sid,
                "link_status": "ledger_only",
                "company_name": "?? (مرجع GL/FM فقط)",
                "contact_person": None,
                "phone": None,
                "email": None,
                "status": "unknown",
                "category_ids": [],
                "source_ids": {
                    "supplier_id": None,
                    "counterparty_id": None,
                },
                "outstanding_debt": b.get("outstanding_debt", 0.0),
                "debits": b.get("debits", 0.0),
                "credits": b.get("credits", 0.0),
                "editable": False,
                **act,
            })

        # Sort: linked + new_only first (alphabetical by name), then
        # ledger_only / ghosts at the bottom.
        order_key = {"new_only": 0, "linked": 1, "ledger_only": 2}
        rows.sort(key=lambda r: (
            order_key.get(r["link_status"], 9),
            (r.get("company_name") or "").lower(),
        ))

        totals = {
            "total":         len(rows),
            "new_only":      sum(1 for r in rows
                                 if r["link_status"] == "new_only"),
            "linked":        sum(1 for r in rows
                                 if r["link_status"] == "linked"),
            "ledger_only":   sum(1 for r in rows
                                 if r["link_status"] == "ledger_only"),
            "ghost":         len(ghost_ids),
            # P1.5.ab.2 — activity roll-ups.
            "active":        sum(1 for r in rows if r.get("is_active")),
            "inactive":      sum(1 for r in rows if not r.get("is_active")),
        }
        return {"items": rows, "totals": totals}

    # ---------- Full forensic / duplicate report ----------
    @router.get("/audit/suppliers-unification-forensic")
    async def suppliers_unification_forensic(
        user: dict = Depends(current_user),
    ):
        """READ-ONLY diagnostic. Returns:

          summary: { counts per category }
          new_only:    list (supplier rows missing from counterparties)
          ledger_only: list (counterparties missing from db.suppliers)
          linked:      list (present in both, same id)
          ghosts:      list of supplier_ids referenced by GL/FM but
                       absent from BOTH tables
          duplicate_suspects: pairs that share lowercased name OR
                              phone OR email across sources
        """
        uid = user["id"]
        sup_rows = await _load_suppliers(db, uid)
        cp_rows = await _load_counterparties_suppliers(db, uid)

        sup_by_id: dict[str, dict] = {s["id"]: s for s in sup_rows}
        cp_by_id: dict[str, dict] = {c["id"]: c for c in cp_rows}
        linked_ids = set(sup_by_id) & set(cp_by_id)
        new_only_ids = set(sup_by_id) - linked_ids
        ledger_only_ids = set(cp_by_id) - linked_ids

        gl_ids = await _gl_referenced_supplier_ids(db, uid)
        fm_ids = await _movements_referenced_supplier_ids(db, uid)
        known_ids = set(sup_by_id) | set(cp_by_id)
        ghost_ids = (gl_ids | fm_ids) - known_ids

        # P1.5.ab.2 — Bulk last-activity for everyone we'll surface.
        all_ids = list(set(sup_by_id) | set(cp_by_id) | ghost_ids)
        activity_raw = await _last_activity_bulk(db, uid, all_ids)

        def _strip(s: dict) -> dict:
            # Just the fields useful for the report.
            return {
                "id": s.get("id"),
                "company_name":   s.get("company_name") or s.get("name"),
                "contact_person": s.get("contact_person"),
                "phone":  s.get("phone"),
                "email":  s.get("email"),
                "status": s.get("status"),
                "created_at": s.get("created_at"),
            }

        # ----- Drift + Activity per visible supplier -----
        async def enrich(rows, link_status):
            out = []
            for r in rows:
                sid = r.get("id")
                cnt, tot = await _drift_count_for_supplier(db, uid, sid)
                act = _activity_summary(activity_raw.get(sid) or {})
                out.append({
                    **_strip(r),
                    "link_status": link_status,
                    "drift_count": cnt,
                    "drift_total": tot,
                    **act,
                })
            return out

        new_only_list   = await enrich(
            [sup_by_id[i] for i in new_only_ids], "new_only")
        linked_list     = await enrich(
            [sup_by_id[i] for i in linked_ids], "linked")
        ledger_only_list = await enrich(
            [cp_by_id[i] for i in ledger_only_ids], "ledger_only")

        ghosts_list = []
        for gid in ghost_ids:
            cnt, tot = await _drift_count_for_supplier(db, uid, gid)
            act = _activity_summary(activity_raw.get(gid) or {})
            ghosts_list.append({
                "id": gid,
                "drift_count": cnt,
                "drift_total": tot,
                "appears_in_gl": gid in gl_ids,
                "appears_in_financial_movements": gid in fm_ids,
                **act,
            })

        # ----- Duplicate suspects across the TWO sources -----
        # Buckets keyed by normalized name / phone / email.
        by_name: dict[str, list[dict]] = {}
        by_phone: dict[str, list[dict]] = {}
        by_email: dict[str, list[dict]] = {}

        def _index(record: dict, source: str):
            nm = _norm_lower(record.get("company_name")
                             or record.get("name"))
            ph = _norm_phone(record.get("phone"))
            em = _norm_lower(record.get("email"))
            tag = {
                "id": record.get("id"),
                "source": source,
                "name": record.get("company_name")
                        or record.get("name"),
                "phone": record.get("phone"),
                "email": record.get("email"),
            }
            if nm:
                by_name.setdefault(nm, []).append(tag)
            if ph:
                by_phone.setdefault(ph, []).append(tag)
            if em:
                by_email.setdefault(em, []).append(tag)

        for s in sup_rows:
            _index(s, "db.suppliers")
        for c in cp_rows:
            _index(c, "db.counterparties")

        def _dups(bucket: dict[str, list[dict]], key_label: str):
            out = []
            for key, lst in bucket.items():
                # A duplicate suspect must (a) have >=2 entries AND
                # (b) cross source OR have different ids.
                if len(lst) < 2:
                    continue
                ids = {x["id"] for x in lst}
                sources = {x["source"] for x in lst}
                if len(ids) < 2 and len(sources) < 2:
                    continue
                out.append({
                    "match_key": key_label,
                    "match_value": key,
                    "entries": lst,
                })
            return out

        duplicate_suspects = {
            "by_name":  _dups(by_name,  "name"),
            "by_phone": _dups(by_phone, "phone"),
            "by_email": _dups(by_email, "email"),
        }

        # P1.5.ab.2 — roll-ups for activity (across ALL surfaced rows).
        _all_rows = new_only_list + linked_list + ledger_only_list \
                    + ghosts_list
        active_count = sum(1 for r in _all_rows if r.get("is_active"))

        return {
            "read_only": True,
            "summary": {
                "db_suppliers_total":      len(sup_rows),
                "db_counterparties_total": len(cp_rows),
                "linked":      len(linked_ids),
                "new_only":    len(new_only_ids),
                "ledger_only": len(ledger_only_ids),
                "ghosts":      len(ghost_ids),
                "active":      active_count,
                "inactive":    len(_all_rows) - active_count,
                "active_window_days": 90,
                "duplicate_suspect_groups": (
                    len(duplicate_suspects["by_name"])
                    + len(duplicate_suspects["by_phone"])
                    + len(duplicate_suspects["by_email"])
                ),
            },
            "linked":      linked_list,
            "new_only":    new_only_list,
            "ledger_only": ledger_only_list,
            "ghosts":      ghosts_list,
            "duplicate_suspects": duplicate_suspects,
            "notes": [
                "READ-ONLY: لا يوجد أي تعديل على قاعدة البيانات.",
                "خالد يظهر هنا تحت new_only لأن قيوده غير مرحّلة لـ GL.",
                "العنبري يظهر هنا تحت ledger_only لأنه غير موجود في "
                "جدول الموردين الجديد.",
                "نشط = آخر حركة (GL أو FM) خلال آخر 90 يوم.",
            ],
        }

    return router
