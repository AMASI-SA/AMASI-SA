#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPLIER = ROOT / "backend" / "supplier_receiving_routes.py"
PIECES = ROOT / "backend" / "preparation_piece_operations.py"
TEST = ROOT / "backend" / "tests" / "test_supplier_invoice_service_eligibility.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_span(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start anchor not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end anchor not found")
    return text[:start_index] + replacement + text[end_index:]


supplier = SUPPLIER.read_text(encoding="utf-8")
supplier = replace_once(
    supplier,
    'ADD_PRODUCT_SERVICE_PERMISSION = "supplier_receiving.service.add"\n',
    'ADD_PRODUCT_SERVICE_PERMISSION = "supplier_receiving.service.add"\n'
    'PERMANENT_SUPPLIER_SERVICE_SOURCE = "supplier_receiving_permanent"\n',
    "permanent service source constant",
)

supplier = replace_once(
    supplier,
    '''def supplier_piece_invoice_services(
''',
    '''def _service_is_invoice_eligible(service: dict[str, Any]) -> bool:
    """Expose only customer-selected or explicitly permanent invoice services."""
    source = _text(service.get("source")).casefold()
    return bool(
        service.get("customer_selected") is True
        or service.get("supplier_invoice_required") is True
        or source == "option"
        or source in {
            PERMANENT_SUPPLIER_SERVICE_SOURCE,
            "supplier_receiving_addition",
        }
    )


def supplier_piece_invoice_services(
''',
    "invoice eligibility helper",
)

supplier = replace_once(
    supplier,
    '''        if not service_id or service_id not in supplier_links:
            continue
        if _service_is_complete(raw):
''',
    '''        if not service_id or service_id not in supplier_links:
            continue
        if not _service_is_invoice_eligible(raw):
            continue
        if _service_is_complete(raw):
''',
    "filter invoice-ineligible product services",
)

supplier = replace_once(
    supplier,
    '''            "eligibility_source": _text(raw.get("source")) or "product",
            "eligibility_condition": dict(raw.get("condition") or {}) or None,
            "add_to_product": False,
''',
    '''            "eligibility_source": _text(raw.get("source")) or "product",
            "eligibility_condition": dict(raw.get("condition") or {}) or None,
            "customer_selected": bool(
                raw.get("customer_selected") is True
                or _text(raw.get("source")).casefold() == "option"
            ),
            "supplier_invoice_required": bool(
                raw.get("supplier_invoice_required") is True
            ),
            "add_to_product": False,
''',
    "publish service eligibility evidence",
)

supplier = replace_once(
    supplier,
    '''def _invoice_group_key(scan: dict[str, Any]) -> tuple[Any, ...]:
    scan_services = (
        scan.get("invoice_services")
        if scan.get("invoice_services")
        else scan.get("services")
    ) or []
''',
    '''def _invoice_group_key(scan: dict[str, Any]) -> tuple[Any, ...]:
    invoice_services = scan.get("invoice_services")
    scan_services = (
        invoice_services
        if isinstance(invoice_services, list)
        else scan.get("services")
    ) or []
''',
    "respect explicit empty invoice services",
)

supplier = replace_span(
    supplier,
    '''def supplier_piece_service_blocker(
''',
    '''def supplier_receipt_piece_patch(
''',
    '''def supplier_piece_service_blocker(
    piece: dict[str, Any],
    session: dict[str, Any],
    *,
    allow_service_addition: bool = False,
) -> dict[str, Any] | None:
    """Supplier services are optional; product receipt can be invoiced alone.

    Service eligibility is enforced only for a service the employee explicitly
    selects. A product-linked service that the customer did not choose must not
    block scanning or force itself into the supplier invoice.
    """
    del piece, session, allow_service_addition
    return None


''',
    "make supplier services optional",
)

supplier = replace_once(
    supplier,
    '''            .to_list(limit)
    )
    return rows


async def _cancellable_session_events(
''',
    '''            .to_list(limit)
    )
    session = await db[SESSIONS].find_one(
        {"user_id": user_id, "id": session_id},
        {"_id": 0},
        **kwargs,
    )
    if session:
        service_catalog = await _supplier_service_catalog(
            db,
            user_id=user_id,
            session=session,
            mongo_session=mongo_session,
        )
        for row in rows:
            # Always rebuild this derived field. Older open sessions may still
            # contain product-level services that the customer did not choose.
            row["invoice_services"] = supplier_piece_invoice_services(
                row,
                session,
                service_catalog,
            )
    return rows


async def _cancellable_session_events(
''',
    "rebuild active invoice service candidates",
)

supplier = replace_once(
    supplier,
    '''                    "status": {"$ne": "inactive"},
                    "service_ids.0": {"$exists": True},
''',
    '''                    "status": {"$ne": "inactive"},
''',
    "allow suppliers without mandatory service links",
)

supplier = replace_once(
    supplier,
    '''        if not list(supplier.get("service_links") or []):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_supplier_services_required"},
            )
''',
    "",
    "remove open-session service requirement",
)

supplier = replace_once(
    supplier,
    '''            "supplier_service_link_status": "catalog_linked",
''',
    '''            "supplier_service_link_status": (
                "catalog_linked"
                if list(supplier.get("service_links") or [])
                else "not_required"
            ),
''',
    "record optional supplier service status",
)

helpers = r'''
async def _supplier_receiving_product(
    db: Any,
    *,
    user_id: str,
    product_id: str,
    mongo_session: Any = None,
) -> dict[str, Any]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    normalized = _text(product_id)
    product = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": normalized},
                {"mezan_product_id": normalized},
                {"salla_product_id": normalized},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "sku": 1,
        },
        **kwargs,
    )
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_receiving_product_not_found"},
        )
    return product


def _supplier_receiving_product_identifiers(
    product: dict[str, Any],
    requested_id: str = "",
) -> list[str]:
    return sorted({
        value
        for value in (
            _text(requested_id),
            _text(product.get("id")),
            _text(product.get("mezan_product_id")),
            _text(product.get("salla_product_id")),
        )
        if value
    })


def _permanent_supplier_service_snapshot(
    resource: dict[str, Any],
    *,
    actor: dict[str, Any] | None = None,
    added_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = added_at or _now()
    return {
        "service_id": _text(resource.get("id")),
        "service_name": _text(resource.get("name"))
        or _text(resource.get("id")),
        "service_code": _text(resource.get("code")) or None,
        "required_quantity": 1.0,
        "unit": _text(resource.get("unit")) or "job",
        "reference_unit_cost": resource.get("unit_cost"),
        "source": PERMANENT_SUPPLIER_SERVICE_SOURCE,
        "condition": None,
        "customer_selected": False,
        "supplier_invoice_required": True,
        "supplier_invoice_added_at": timestamp,
        "supplier_invoice_added_by": _text((actor or {}).get("id")) or None,
        "status": "pending",
        "completed_quantity": 0.0,
    }


def _permanent_supplier_invoice_service_row(
    resource: dict[str, Any],
) -> dict[str, Any]:
    reference_halalas = _halalas(resource.get("unit_cost"))
    return {
        "service_id": _text(resource.get("id")),
        "service_name": _text(resource.get("name"))
        or _text(resource.get("id")),
        "service_code": _text(resource.get("code")) or None,
        "unit": _text(resource.get("unit")) or "job",
        "required_quantity": 1.0,
        "reference_unit_price_halalas": reference_halalas,
        "reference_price_complete": reference_halalas is not None,
        "linked_to_product": True,
        "eligibility_source": PERMANENT_SUPPLIER_SERVICE_SOURCE,
        "eligibility_condition": None,
        "customer_selected": False,
        "supplier_invoice_required": True,
        "add_to_product": False,
    }


async def _supplier_invoice_service_candidate_context(
    db: Any,
    *,
    user_id: str,
    session: dict[str, Any],
    product_id: str,
    mongo_session: Any = None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    set[str],
    set[str],
]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    product = await _supplier_receiving_product(
        db,
        user_id=user_id,
        product_id=product_id,
        mongo_session=mongo_session,
    )
    salla_product_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_product_id},
        {"_id": 0, "resource_id": 1},
        **kwargs,
    ).to_list(5000)
    option_links = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "mode": "resource",
            "resource_id": {"$nin": [None, ""]},
        },
        {"_id": 0, "resource_id": 1},
        **kwargs,
    ).to_list(5000)
    product_link_ids = {
        _text(row.get("resource_id"))
        for row in product_links
        if _text(row.get("resource_id"))
    }
    option_link_ids = {
        _text(row.get("resource_id"))
        for row in option_links
        if _text(row.get("resource_id"))
    }
    blocked = product_link_ids | option_link_ids
    catalog = await _supplier_service_catalog(
        db,
        user_id=user_id,
        session=session,
        mongo_session=mongo_session,
    )
    candidates = [
        dict(row)
        for candidate_service_id, row in catalog.items()
        if candidate_service_id not in blocked
    ]
    candidates.sort(key=lambda row: (
        _text(row.get("name")).casefold(),
        _text(row.get("id")),
    ))
    return product, candidates, product_link_ids, option_link_ids


async def _apply_permanent_supplier_invoice_service(
    db: Any,
    *,
    context: dict[str, Any],
    actor: dict[str, Any],
    session: dict[str, Any],
    product_id: str,
    service_id: str,
    mongo_session: Any,
) -> dict[str, Any]:
    merchant_id = context["merchant_id"]
    product, candidates, product_link_ids, option_link_ids = (
        await _supplier_invoice_service_candidate_context(
            db,
            user_id=merchant_id,
            session=session,
            product_id=product_id,
            mongo_session=mongo_session,
        )
    )
    normalized_service_id = _text(service_id)
    if normalized_service_id in product_link_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "supplier_receiving_service_already_linked_to_product",
                "service_id": normalized_service_id,
            },
        )
    if normalized_service_id in option_link_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "supplier_receiving_service_already_linked_to_option",
                "service_id": normalized_service_id,
            },
        )
    resource = next(
        (
            row for row in candidates
            if _text(row.get("id")) == normalized_service_id
        ),
        None,
    )
    if not resource:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "supplier_receiving_service_not_available",
                "service_id": normalized_service_id,
            },
        )

    now = _now()
    salla_product_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    selector = {
        "user_id": merchant_id,
        "salla_product_id": salla_product_id,
        "resource_id": normalized_service_id,
    }
    await db[PRODUCT_RESOURCE_BINDINGS].update_one(
        selector,
        {
            "$set": {
                **selector,
                "mezan_product_id": (
                    product.get("mezan_product_id") or product.get("id")
                ),
                "product_name": product.get("name"),
                "resource_name": resource.get("name"),
                "quantity": 1.0,
                "supplier_invoice_required": True,
                "supplier_invoice_source": "supplier_receiving",
                "supplier_invoice_added_at": now,
                "supplier_invoice_added_by": context["actor_id"],
                "supplier_invoice_added_by_name": _actor_name(actor),
                "updated_at": now,
            },
            "$setOnInsert": {
                "id": uuid.uuid4().hex,
                "created_at": now,
            },
        },
        upsert=True,
        session=mongo_session,
    )

    identifiers = _supplier_receiving_product_identifiers(product, product_id)
    snapshot = _permanent_supplier_service_snapshot(
        resource,
        actor=actor,
        added_at=now,
    )
    uninvoiced_piece_query = {
        "user_id": merchant_id,
        "product_id": {"$in": identifiers},
        "status": {"$nin": [PIECE_STATUS_CANCELLED, PIECE_STATUS_RECEIVED]},
        "$and": [
            {
                "$or": [
                    {"supplier_receiving_history": {"$exists": False}},
                    {"supplier_receiving_history": None},
                    {"supplier_receiving_history": []},
                ]
            },
            {
                "services": {
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                }
            },
        ],
    }
    piece_result = await db[PIECES].update_many(
        uninvoiced_piece_query,
        {
            "$push": {"services": snapshot},
            "$inc": {
                "service_count": 1,
                "remaining_service_count": 1,
            },
            "$set": {
                "service_plan_status": "pending",
                "service_plan_updated_at": now,
                "updated_at": now,
            },
        },
        session=mongo_session,
    )

    invoice_row = _permanent_supplier_invoice_service_row(resource)
    event_query = {
        "user_id": merchant_id,
        "session_id": _text(session.get("id")),
        "event_type": "supplier_piece_scanned",
        "product_id": {"$in": identifiers},
    }
    event_service_updates = 0
    event_invoice_updates = 0
    for collection_name in (RECEIVING_EVENTS, PIECE_EVENTS):
        service_result = await db[collection_name].update_many(
            {
                **event_query,
                "services": {
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                },
            },
            {
                "$push": {"services": snapshot},
                "$inc": {"remaining_service_count": 1},
                "$set": {"updated_at": now},
            },
            session=mongo_session,
        )
        invoice_result = await db[collection_name].update_many(
            {
                **event_query,
                "invoice_services": {
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                },
            },
            {
                "$push": {"invoice_services": invoice_row},
                "$set": {"updated_at": now},
            },
            session=mongo_session,
        )
        if collection_name == RECEIVING_EVENTS:
            event_service_updates = int(service_result.modified_count or 0)
            event_invoice_updates = int(invoice_result.modified_count or 0)

    audit = {
        "id": uuid.uuid4().hex,
        "user_id": merchant_id,
        "event_type": "supplier_receiving_permanent_service_added",
        "session_id": _text(session.get("id")),
        "session_reference": _text(session.get("reference")),
        "product_id": _text(product.get("mezan_product_id") or product.get("id")),
        "salla_product_id": salla_product_id,
        "product_name": _text(product.get("name")),
        "service_id": normalized_service_id,
        "service_name": _text(resource.get("name")),
        "impacted_uninvoiced_piece_count": int(
            piece_result.modified_count or 0
        ),
        "impacted_active_scan_count": event_invoice_updates,
        "historical_invoices_changed": False,
        "actor_id": context["actor_id"],
        "actor_name": _actor_name(actor),
        "created_at": now,
    }
    await db[AUDIT].insert_one(audit, session=mongo_session)
    return {
        "product": product,
        "service": resource,
        "service_snapshot": snapshot,
        "invoice_service": invoice_row,
        "impacted_uninvoiced_piece_count": int(
            piece_result.modified_count or 0
        ),
        "impacted_active_scan_count": max(
            event_service_updates,
            event_invoice_updates,
        ),
        "historical_invoices_changed": False,
    }


'''

supplier = replace_once(
    supplier,
    '''async def ensure_supplier_receiving_indexes(db: Any) -> None:
''',
    helpers + '''async def ensure_supplier_receiving_indexes(db: Any) -> None:
''',
    "permanent supplier service helpers",
)

endpoints = r'''
    @router.get(
        "/sessions/{session_id}/products/{product_id}/service-candidates"
    )
    async def supplier_invoice_service_candidates(
        session_id: str,
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        _require_permission(context, ADD_PRODUCT_SERVICE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        product, candidates, _product_links, _option_links = (
            await _supplier_invoice_service_candidate_context(
                db,
                user_id=context["merchant_id"],
                session=session,
                product_id=product_id,
            )
        )
        return {
            "ok": True,
            "product": {
                "id": _text(product.get("mezan_product_id") or product.get("id")),
                "salla_product_id": _text(product.get("salla_product_id")),
                "name": _text(product.get("name")) or "منتج",
                "sku": _text(product.get("sku")),
            },
            "services": [
                {
                    "id": _text(row.get("id")),
                    "name": _text(row.get("name")) or _text(row.get("id")),
                    "code": _text(row.get("code")),
                    "unit": _text(row.get("unit")) or "job",
                    "unit_cost": row.get("unit_cost"),
                    "unit_price_halalas": int(
                        _halalas(row.get("unit_cost")) or 0
                    ),
                }
                for row in candidates
            ],
            "existing_product_services_hidden": True,
            "existing_option_services_hidden": True,
            "historical_invoices_immutable": True,
        }

    @router.post(
        "/sessions/{session_id}/products/{product_id}/services/{service_id}",
        status_code=201,
    )
    async def add_permanent_supplier_invoice_service(
        session_id: str,
        product_id: str,
        service_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        _require_permission(context, ADD_PRODUCT_SERVICE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        mongo_client = getattr(db, "client", None)
        if mongo_client is None or not hasattr(mongo_client, "start_session"):
            raise HTTPException(
                status_code=503,
                detail={"code": "supplier_receiving_atomic_transaction_required"},
            )
        async with await mongo_client.start_session() as mongo_session:
            async with mongo_session.start_transaction():
                fresh_session = await db[SESSIONS].find_one(
                    {
                        "user_id": context["merchant_id"],
                        "id": session_id,
                        "status": "open",
                        "opened_by": context["actor_id"],
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                if not fresh_session:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "supplier_receiving_session_closed"},
                    )
                result = await _apply_permanent_supplier_invoice_service(
                    db,
                    context=context,
                    actor=user,
                    session=fresh_session,
                    product_id=product_id,
                    service_id=service_id,
                    mongo_session=mongo_session,
                )
        return {
            "ok": True,
            "product": {
                "id": _text(
                    result["product"].get("mezan_product_id")
                    or result["product"].get("id")
                ),
                "salla_product_id": _text(
                    result["product"].get("salla_product_id")
                ),
                "name": _text(result["product"].get("name")) or "منتج",
                "sku": _text(result["product"].get("sku")),
            },
            "service": {
                **result["invoice_service"],
                "unit_cost": result["service"].get("unit_cost"),
            },
            "impacted_uninvoiced_piece_count": (
                result["impacted_uninvoiced_piece_count"]
            ),
            "impacted_active_scan_count": (
                result["impacted_active_scan_count"]
            ),
            "historical_invoices_changed": False,
            "permanent_for_future_orders": True,
        }

'''

supplier = replace_once(
    supplier,
    '''    @router.get("/invoices/{invoice_id}")
''',
    endpoints + '''    @router.get("/invoices/{invoice_id}")
''',
    "supplier service candidate routes",
)

SUPPLIER.write_text(supplier, encoding="utf-8")

pieces = PIECES.read_text(encoding="utf-8")
old_inherit = r'''def inherit_required_services(
    *,
    line: dict[str, Any],
    product_links: Iterable[dict[str, Any]],
    option_bindings: Iterable[dict[str, Any]],
    resources_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inherit services from the product and the selected option values."""
    selected_pairs = _selected_spec_pairs(line)
    inherited: dict[str, dict[str, Any]] = {}

    def add(resource_id: Any, quantity: Any, source: str, condition=None) -> None:
        key = _text(resource_id)
        resource = resources_by_id.get(key)
        if not key or not resource or not _resource_is_service(resource):
            return
        try:
            required_quantity = float(quantity or 1)
        except (TypeError, ValueError, OverflowError):
            required_quantity = 1.0
        if required_quantity <= 0:
            required_quantity = 1.0
        inherited[key] = {
            "service_id": key,
            "service_name": _text(resource.get("name")) or key,
            "service_code": _text(resource.get("code")) or None,
            "required_quantity": required_quantity,
            "unit": _text(resource.get("unit")) or "job",
            "reference_unit_cost": resource.get("unit_cost"),
            "source": source,
            "condition": condition,
            "status": "pending",
            "completed_quantity": 0.0,
        }

    for link in product_links:
        add(link.get("resource_id"), link.get("quantity"), "product")

    for binding in option_bindings:
        if _text(binding.get("mode")) != "resource":
            continue
        option_name = _normalized(binding.get("option_name"))
        value_name = _normalized(binding.get("value_name"))
        if not option_name or not value_name:
            continue
        if (option_name, value_name) not in selected_pairs:
            continue
        add(
            binding.get("resource_id"),
            binding.get("quantity"),
            "option",
            {
                "option_id": binding.get("option_id"),
                "option_name": binding.get("option_name"),
                "value_id": binding.get("value_id"),
                "value_name": binding.get("value_name"),
            },
        )

    return sorted(
        inherited.values(),
        key=lambda row: (
            _normalized(row.get("service_name")),
            _text(row.get("service_id")),
        ),
    )
'''
new_inherit = r'''def inherit_required_services(
    *,
    line: dict[str, Any],
    product_links: Iterable[dict[str, Any]],
    option_bindings: Iterable[dict[str, Any]],
    resources_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inherit services while preserving why each service is invoice-eligible."""
    selected_pairs = _selected_spec_pairs(line)
    inherited: dict[str, dict[str, Any]] = {}

    def add(
        resource_id: Any,
        quantity: Any,
        source: str,
        condition=None,
        *,
        customer_selected: bool = False,
        supplier_invoice_required: bool = False,
    ) -> None:
        key = _text(resource_id)
        resource = resources_by_id.get(key)
        if not key or not resource or not _resource_is_service(resource):
            return
        try:
            required_quantity = float(quantity or 1)
        except (TypeError, ValueError, OverflowError):
            required_quantity = 1.0
        if required_quantity <= 0:
            required_quantity = 1.0
        inherited[key] = {
            "service_id": key,
            "service_name": _text(resource.get("name")) or key,
            "service_code": _text(resource.get("code")) or None,
            "required_quantity": required_quantity,
            "unit": _text(resource.get("unit")) or "job",
            "reference_unit_cost": resource.get("unit_cost"),
            "source": source,
            "condition": condition,
            "customer_selected": customer_selected,
            "supplier_invoice_required": supplier_invoice_required,
            "status": "pending",
            "completed_quantity": 0.0,
        }

    for link in product_links:
        permanent_invoice_service = (
            link.get("supplier_invoice_required") is True
        )
        add(
            link.get("resource_id"),
            link.get("quantity"),
            (
                "supplier_receiving_permanent"
                if permanent_invoice_service
                else "product"
            ),
            customer_selected=False,
            supplier_invoice_required=permanent_invoice_service,
        )

    for binding in option_bindings:
        if _text(binding.get("mode")) != "resource":
            continue
        option_name = _normalized(binding.get("option_name"))
        value_name = _normalized(binding.get("value_name"))
        if not option_name or not value_name:
            continue
        if (option_name, value_name) not in selected_pairs:
            continue
        add(
            binding.get("resource_id"),
            binding.get("quantity"),
            "option",
            {
                "option_id": binding.get("option_id"),
                "option_name": binding.get("option_name"),
                "value_id": binding.get("value_id"),
                "value_name": binding.get("value_name"),
            },
            customer_selected=True,
            supplier_invoice_required=False,
        )

    return sorted(
        inherited.values(),
        key=lambda row: (
            _normalized(row.get("service_name")),
            _text(row.get("service_id")),
        ),
    )
'''
pieces = replace_once(
    pieces,
    old_inherit,
    new_inherit,
    "preserve service eligibility in future pieces",
)
PIECES.write_text(pieces, encoding="utf-8")

TEST.write_text(
    r'''from __future__ import annotations

from preparation_piece_operations import inherit_required_services
from supplier_receiving_routes import (
    _invoice_group_key,
    _permanent_supplier_service_snapshot,
    supplier_piece_invoice_services,
    supplier_piece_service_blocker,
)


def _session() -> dict:
    return {
        "supplier_snapshot": {
            "id": "supplier-1",
            "company_name": "مورد",
            "service_links": [
                {"service_id": "ordinary", "service_name": "عادية"},
                {"service_id": "option", "service_name": "خيار العميل"},
                {"service_id": "permanent", "service_name": "دائمة"},
            ],
        }
    }


def test_product_link_not_selected_by_customer_is_hidden_from_invoice() -> None:
    rows = supplier_piece_invoice_services(
        {
            "services": [
                {
                    "service_id": "ordinary",
                    "service_name": "عادية",
                    "source": "product",
                    "status": "pending",
                    "required_quantity": 1,
                },
                {
                    "service_id": "option",
                    "service_name": "خيار العميل",
                    "source": "option",
                    "status": "pending",
                    "required_quantity": 1,
                },
                {
                    "service_id": "permanent",
                    "service_name": "دائمة",
                    "source": "supplier_receiving_permanent",
                    "supplier_invoice_required": True,
                    "status": "pending",
                    "required_quantity": 1,
                },
            ]
        },
        _session(),
        {},
    )

    assert [row["service_id"] for row in rows] == ["option", "permanent"]
    assert rows[0]["customer_selected"] is True
    assert rows[1]["supplier_invoice_required"] is True


def test_explicit_empty_invoice_services_never_falls_back_to_product_services() -> None:
    key = _invoice_group_key(
        {
            "product_id": "p1",
            "sku": "sku",
            "product_name": "منتج",
            "invoice_services": [],
            "services": [{"service_id": "ordinary", "required_quantity": 1}],
        }
    )
    assert key[-1] == ()


def test_service_selection_is_optional_for_supplier_receipt() -> None:
    assert supplier_piece_service_blocker({}, _session()) is None


def test_future_piece_marks_permanent_service_but_not_ordinary_product_link() -> None:
    resources = {
        "ordinary": {
            "id": "ordinary",
            "name": "عادية",
            "kind": "service",
            "unit_cost": 10,
        },
        "permanent": {
            "id": "permanent",
            "name": "دائمة",
            "kind": "service",
            "unit_cost": 15,
        },
    }
    rows = inherit_required_services(
        line={},
        product_links=[
            {"resource_id": "ordinary", "quantity": 1},
            {
                "resource_id": "permanent",
                "quantity": 1,
                "supplier_invoice_required": True,
            },
        ],
        option_bindings=[],
        resources_by_id=resources,
    )
    by_id = {row["service_id"]: row for row in rows}
    assert by_id["ordinary"]["source"] == "product"
    assert by_id["ordinary"]["supplier_invoice_required"] is False
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
    assert by_id["permanent"]["supplier_invoice_required"] is True


def test_permanent_snapshot_is_pending_and_invoice_required() -> None:
    row = _permanent_supplier_service_snapshot(
        {
            "id": "service-1",
            "name": "تطريز",
            "kind": "service",
            "unit_cost": 12.5,
        }
    )
    assert row["source"] == "supplier_receiving_permanent"
    assert row["supplier_invoice_required"] is True
    assert row["customer_selected"] is False
    assert row["status"] == "pending"
''',
    encoding="utf-8",
)

print("Applied supplier invoice service eligibility and permanence patch.")
