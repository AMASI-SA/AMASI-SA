from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label} expected once, found {count}")
    return source.replace(old, new, 1)


route_path = Path("backend/supplier_receiving_routes.py")
source = route_path.read_text(encoding="utf-8")

source = replace_once(
    source,
    "from order_option_cost_snapshot_routes import resolve_base_unit_cost\n",
    "from order_option_cost_snapshot_routes import (\n"
    "    MEZAN_V2_COST_SOURCES,\n"
    "    resolve_base_unit_cost,\n"
    ")\n",
    "Mezan cost import",
)

old_product_validation = '''        reference_product_halalas = int(
            first.get("reference_product_unit_price_halalas") or 0
        )
        requested_product_halalas = int(line.product_unit_price_halalas)
'''
new_product_validation = '''        reference_product_price_source = _text(
            first.get("reference_product_price_source")
        ) or "missing"
        reference_product_price_complete = bool(
            first.get("reference_product_price_complete")
        )
        reference_product_is_mezan = bool(
            reference_product_price_complete
            and reference_product_price_source in MEZAN_V2_COST_SOURCES
        )
        # Supplier invoices are a Mezan cost-authority boundary. Historical
        # scans that still carry a Salla fallback are treated as missing, not
        # as an accepted product price.
        reference_product_halalas = (
            int(first.get("reference_product_unit_price_halalas") or 0)
            if reference_product_is_mezan
            else 0
        )
        requested_product_halalas = int(line.product_unit_price_halalas)
        if (
            product_charge_eligible
            and not reference_product_is_mezan
            and requested_product_halalas == 0
        ):
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "supplier_receiving_mezan_product_price_required",
                    "line_number": line_number,
                    "product_id": _text(first.get("product_id")) or None,
                    "product_name": _text(first.get("product_name")) or "منتج",
                    "price_authority": "mezan_v2",
                },
            )
'''
source = replace_once(
    source,
    old_product_validation,
    new_product_validation,
    "invoice product price validation",
)

old_public_price_fields = '''            "product_charge_eligible": product_charge_eligible,
            "quantity": quantity,
            "reference_product_unit_price_halalas": reference_product_halalas,
            "product_unit_price_halalas": requested_product_halalas,
'''
new_public_price_fields = '''            "product_charge_eligible": product_charge_eligible,
            "product_price_authority": "mezan_v2",
            "reference_product_price_complete": (
                reference_product_is_mezan
                if product_charge_eligible
                else True
            ),
            "reference_product_price_source": (
                reference_product_price_source
                if product_charge_eligible
                else "previous_supplier_invoice"
            ),
            "quantity": quantity,
            "reference_product_unit_price_halalas": reference_product_halalas,
            "product_unit_price_halalas": requested_product_halalas,
'''
source = replace_once(
    source,
    old_public_price_fields,
    new_public_price_fields,
    "invoice public product price fields",
)

old_reference_function = '''async def _supplier_product_reference_price(
    db: Any,
    *,
    user_id: str,
    piece: dict[str, Any],
) -> dict[str, Any]:
    product_id = _text(piece.get("product_id"))
    product = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": product_id},
                {"mezan_product_id": product_id},
                {"salla_product_id": product_id},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "cost_price_from_salla": 1,
            "variants": 1,
        },
    )
    if not product:
        return {
            "reference_product_unit_price_halalas": 0,
            "reference_product_price_complete": False,
            "reference_product_price_source": "missing",
        }
    salla_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    profile = await db[COST_PROFILES].find_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ) or {}
    amount, source = resolve_base_unit_cost(
        {
            "variant_id": piece.get("variant_id") or piece.get("salla_variant_id"),
            "sku": piece.get("sku"),
        },
        profile,
        product,
    )
    amount_halalas = _halalas(amount)
    return {
        "reference_product_unit_price_halalas": int(amount_halalas or 0),
        "reference_product_price_complete": amount_halalas is not None,
        "reference_product_price_source": source,
    }
'''
new_reference_function = '''def supplier_mezan_product_reference_price(
    *,
    piece: dict[str, Any],
    profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Resolve a supplier-invoice product price from Mezan V2 only.

    Salla cost remains available to profitability calculations elsewhere, but
    it is never an authority for a supplier payable. An absent Mezan base or
    variant cost is returned as missing so an authorised employee must record
    the real supplier price in Mezan before approval.
    """
    amount, source = resolve_base_unit_cost(
        {
            "variant_id": piece.get("variant_id") or piece.get("salla_variant_id"),
            "sku": piece.get("sku"),
        },
        profile or {},
        {},  # Deliberately disable every Salla product/variant fallback.
    )
    if source not in MEZAN_V2_COST_SOURCES:
        amount = None
        source = "missing"
    amount_halalas = _halalas(amount)
    return {
        "reference_product_unit_price_halalas": int(amount_halalas or 0),
        "reference_product_price_complete": amount_halalas is not None,
        "reference_product_price_source": source,
        "product_price_authority": "mezan_v2",
        "salla_price_fallback_allowed": False,
    }


async def _supplier_product_reference_price(
    db: Any,
    *,
    user_id: str,
    piece: dict[str, Any],
    mongo_session: Any = None,
) -> dict[str, Any]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    product_id = _text(piece.get("product_id"))
    product = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": product_id},
                {"mezan_product_id": product_id},
                {"salla_product_id": product_id},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
        },
        **kwargs,
    )
    if not product:
        return supplier_mezan_product_reference_price(piece=piece, profile={})
    salla_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    profile = await db[COST_PROFILES].find_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
        **kwargs,
    ) or {}
    return supplier_mezan_product_reference_price(
        piece=piece,
        profile=profile,
    )
'''
source = replace_once(
    source,
    old_reference_function,
    new_reference_function,
    "Mezan-only product reference function",
)

old_recent_rows = '''    if session:
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
'''
new_recent_rows = '''    if session:
        service_catalog = await _supplier_service_catalog(
            db,
            user_id=user_id,
            session=session,
            mongo_session=mongo_session,
        )
        product_price_cache: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            # Always rebuild derived fields. Older open sessions may contain
            # unselected services or a product price copied from Salla before
            # the Mezan-only supplier-price boundary was enforced.
            row["invoice_services"] = supplier_piece_invoice_services(
                row,
                session,
                service_catalog,
            )
            if row.get("product_charge_eligible") is False:
                row.update({
                    "reference_product_unit_price_halalas": 0,
                    "reference_product_price_complete": True,
                    "reference_product_price_source": "previous_supplier_invoice",
                    "product_price_authority": "mezan_v2",
                    "salla_price_fallback_allowed": False,
                })
                continue
            cache_key = (
                _text(row.get("product_id")),
                _text(row.get("variant_id") or row.get("salla_variant_id")),
                _text(row.get("sku")).casefold(),
            )
            if cache_key not in product_price_cache:
                product_price_cache[cache_key] = (
                    await _supplier_product_reference_price(
                        db,
                        user_id=user_id,
                        piece=row,
                        mongo_session=mongo_session,
                    )
                )
            row.update(dict(product_price_cache[cache_key]))
    return rows
'''
source = replace_once(
    source,
    old_recent_rows,
    new_recent_rows,
    "open-session product price refresh",
)

source = replace_once(
    source,
    '    "supplier_piece_reference_price",\n',
    '    "supplier_piece_reference_price",\n'
    '    "supplier_mezan_product_reference_price",\n',
    "Mezan price helper export",
)

route_path.write_text(source, encoding="utf-8")


test_path = Path("backend/tests/test_supplier_receiving.py")
tests = test_path.read_text(encoding="utf-8")
tests = replace_once(
    tests,
    "import pytest\n",
    "import pytest\nfrom fastapi import HTTPException\n",
    "HTTPException test import",
)
tests = replace_once(
    tests,
    "    _supplier_invoice_filename,\n",
    "    _supplier_invoice_filename,\n"
    "    _supplier_product_reference_price,\n",
    "private product price import",
)
tests = replace_once(
    tests,
    "    supplier_piece_reference_price,\n",
    "    supplier_piece_reference_price,\n"
    "    supplier_mezan_product_reference_price,\n",
    "Mezan price helper test import",
)

marker = "def test_supplier_product_price_is_mezan_only_and_never_uses_salla():"
if marker not in tests:
    tests += '''


def test_supplier_product_price_is_mezan_only_and_never_uses_salla():
    piece = {"variant_id": "variant-1", "sku": "SKU-1"}

    missing = supplier_mezan_product_reference_price(
        piece=piece,
        profile={},
    )
    assert missing == {
        "reference_product_unit_price_halalas": 0,
        "reference_product_price_complete": False,
        "reference_product_price_source": "missing",
        "product_price_authority": "mezan_v2",
        "salla_price_fallback_allowed": False,
    }

    variant = supplier_mezan_product_reference_price(
        piece=piece,
        profile={
            "base_cost": 99,
            "variant_costs": {"variant-1": 12.75},
        },
    )
    assert variant["reference_product_unit_price_halalas"] == 1275
    assert variant["reference_product_price_complete"] is True
    assert variant["reference_product_price_source"] == "mezan_v2_variant"

    explicit_zero = supplier_mezan_product_reference_price(
        piece={"sku": "SKU-2"},
        profile={"base_cost": 0},
    )
    assert explicit_zero["reference_product_unit_price_halalas"] == 0
    assert explicit_zero["reference_product_price_complete"] is True
    assert explicit_zero["reference_product_price_source"] == "mezan_v2_base"


@pytest.mark.asyncio
async def test_supplier_product_lookup_ignores_salla_cost_when_mezan_cost_is_missing():
    class _Collection:
        def __init__(self, row):
            self.row = row

        async def find_one(self, *_args, **_kwargs):
            return dict(self.row) if isinstance(self.row, dict) else None

    class _DB:
        def __init__(self, product, profile):
            self.rows = [product, profile]

        def __getitem__(self, _name):
            return _Collection(self.rows.pop(0))

    product = {
        "id": "product-1",
        "salla_product_id": "salla-1",
        "cost_price_from_salla": 88.0,
        "variants": [{
            "id": "variant-1",
            "sku": "SKU-1",
            "cost_price_from_salla": 77.0,
        }],
    }
    missing = await _supplier_product_reference_price(
        _DB(product, {}),
        user_id="owner-1",
        piece={
            "product_id": "product-1",
            "variant_id": "variant-1",
            "sku": "SKU-1",
        },
    )
    assert missing["reference_product_unit_price_halalas"] == 0
    assert missing["reference_product_price_complete"] is False
    assert missing["reference_product_price_source"] == "missing"
    assert missing["salla_price_fallback_allowed"] is False

    priced = await _supplier_product_reference_price(
        _DB(product, {"base_cost": 14.5}),
        user_id="owner-1",
        piece={"product_id": "product-1", "sku": "SKU-1"},
    )
    assert priced["reference_product_unit_price_halalas"] == 1450
    assert priced["reference_product_price_source"] == "mezan_v2_base"


def test_supplier_invoice_rejects_stale_salla_product_reference():
    scan = {
        "piece_id": "piece-1",
        "product_id": "product-1",
        "product_name": "منتج",
        "sku": "SKU-1",
        "product_charge_eligible": True,
        "reference_product_unit_price_halalas": 1500,
        "reference_product_price_complete": True,
        "reference_product_price_source": "salla_product_fallback",
        "invoice_services": [{
            "service_id": "paint",
            "service_name": "طلاء",
            "required_quantity": 1,
            "reference_unit_price_halalas": 200,
            "customer_selected": True,
        }],
    }
    line = SupplierReceivingInvoiceLineRequest(
        piece_ids=["piece-1"],
        product_unit_price_halalas=0,
        services=[SupplierReceivingInvoiceServiceRequest(
            service_id="paint",
            unit_price_halalas=200,
        )],
    )
    with pytest.raises(HTTPException) as captured:
        build_supplier_receiving_invoice(
            session={
                "reference": "SR-MEZAN-ONLY",
                "supplier_snapshot": {
                    "service_links": [{"service_id": "paint"}],
                },
            },
            scans=[scan],
            requested_lines=[line],
            saved_at=datetime.now(timezone.utc),
        )
    assert captured.value.status_code == 422
    assert captured.value.detail["code"] == (
        "supplier_receiving_mezan_product_price_required"
    )


def test_authorised_manual_price_replaces_stale_salla_reference_and_updates_mezan():
    scan = {
        "piece_id": "piece-1",
        "product_id": "product-1",
        "product_name": "منتج",
        "sku": "SKU-1",
        "product_charge_eligible": True,
        "reference_product_unit_price_halalas": 1500,
        "reference_product_price_complete": True,
        "reference_product_price_source": "salla_product_fallback",
        "invoice_services": [],
    }
    invoice = build_supplier_receiving_invoice(
        session={"reference": "SR-MEZAN-MANUAL", "supplier_snapshot": {}},
        scans=[scan],
        requested_lines=[SupplierReceivingInvoiceLineRequest(
            piece_ids=["piece-1"],
            product_unit_price_halalas=1200,
            services=[],
        )],
        saved_at=datetime.now(timezone.utc),
        permissions={EDIT_PRODUCT_PRICE_PERMISSION},
    )
    assert invoice["lines"][0]["reference_product_unit_price_halalas"] == 0
    assert invoice["lines"][0]["product_unit_price_halalas"] == 1200
    assert invoice["lines"][0]["product_price_authority"] == "mezan_v2"
    assert invoice["price_changes"][0]["before_halalas"] == 0
    assert invoice["price_changes"][0]["after_halalas"] == 1200
'''

test_path.write_text(tests, encoding="utf-8")
