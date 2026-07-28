"""One-shot fix for shipping facts lost during explicit review refresh."""
from pathlib import Path


SYNC_PATH = Path("backend/salla_integration/sync.py")
ROUTES_PATH = Path("backend/order_review_routes.py")
TEST_PATH = Path("backend/tests/test_order_review_stage_one.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


# 1) Preserve address/courier context while keeping shipment labels authoritative.
sync_source = SYNC_PATH.read_text(encoding="utf-8")
start_marker = "async def _fetch_salla_shipment_details("
end_marker = "\nasync def _fetch_salla_order_details("
if sync_source.count(start_marker) != 1 or sync_source.count(end_marker) != 1:
    raise SystemExit("shipment function markers were not found exactly once")
start = sync_source.index(start_marker)
end = sync_source.index(end_marker, start)
new_shipment_block = r'''_SHIPMENT_CLEARABLE_FIELDS = {
    "label",
    "label_url",
    "shipping_number",
    "tracking_number",
    "tracking_link",
    "tracking_url",
    "status",
}

_SHIPMENT_CONTEXT_FIELDS = {
    "ship_to",
    "shipping_address",
    "address",
    "courier",
    "courier_name",
    "company",
    "company_name",
    "method",
    "shipping_method",
    "service",
    "recipient",
    "receiver",
    "pickup_address",
    "branch",
    "total_weight",
}


def _shipment_context(row: Any) -> dict:
    if not isinstance(row, dict):
        return {}
    return {
        key: value
        for key, value in row.items()
        if key in _SHIPMENT_CONTEXT_FIELDS and value not in (None, "", [], {})
    }


def _embedded_shipment_context(row: dict, embedded_rows: list[dict]) -> dict:
    shipment_id = _str(row.get("id"))
    if shipment_id:
        for embedded in embedded_rows:
            if _str(embedded.get("id")) == shipment_id:
                return _shipment_context(embedded)
    if len(embedded_rows) == 1:
        return _shipment_context(embedded_rows[0])
    return {}


def _merge_shipment_payload(base: dict, overlay: dict) -> dict:
    merged = dict(base or {})
    for key, value in (overlay or {}).items():
        if key in _SHIPMENT_CLEARABLE_FIELDS or value not in (None, "", [], {}):
            merged[key] = value
    return merged


async def _fetch_salla_shipment_details(
    db,
    user_id: str,
    internal_order_id: str,
    embedded_shipments: Any,
) -> list[dict]:
    """Return current shipment rows without losing order delivery context.

    The shipment-list/detail endpoints remain authoritative for labels, tracking
    and status.  Salla may omit the shipping address and courier from those
    responses, or return an empty list before a shipment is created.  In that
    case we preserve only address/courier context from Order Details and strip
    any embedded label/tracking fields so cancelled labels cannot reappear.
    """
    embedded_rows = [
        dict(row)
        for row in (embedded_shipments or [])
        if isinstance(row, dict)
    ]
    rows: list[dict] = []
    listed_succeeded = False

    try:
        response = await call_salla(
            db,
            user_id,
            "GET",
            "/shipments",
            params={
                "order_id": internal_order_id,
                "per_page": 50,
            },
        )
        listed = response.get("data") if isinstance(response, dict) else None
        if isinstance(listed, list):
            listed_succeeded = True
            rows = [dict(row) for row in listed if isinstance(row, dict)]
        else:
            rows = embedded_rows
    except SallaError as exc:
        logger.warning(
            "Could not list Salla shipments for order %s: %s",
            internal_order_id,
            exc,
        )
        rows = embedded_rows

    if listed_succeeded and not rows:
        return [
            context
            for context in (_shipment_context(row) for row in embedded_rows)
            if context
        ]

    async def enrich(row: dict) -> dict:
        base = (
            _embedded_shipment_context(row, embedded_rows)
            if listed_succeeded
            else {}
        )
        merged = _merge_shipment_payload(base, row)
        shipment_id = _str(row.get("id"))
        if not shipment_id:
            return merged

        try:
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/shipments/{shipment_id}",
            )
        except SallaError as exc:
            logger.warning(
                "Could not fetch Salla shipment %s details: %s",
                shipment_id,
                exc,
            )
            return merged

        details = response.get("data") if isinstance(response, dict) else None
        if not isinstance(details, dict):
            return merged
        return _merge_shipment_payload(merged, details)

    if not rows:
        return []

    return list(await asyncio.gather(*(enrich(row) for row in rows)))
'''
sync_source = sync_source[:start] + new_shipment_block + sync_source[end:]
SYNC_PATH.write_text(sync_source, encoding="utf-8")


# 2) Version the explicit review refresh so orders affected by the prior refresh
#    are repaired once after this deployment.
routes_source = ROUTES_PATH.read_text(encoding="utf-8")
routes_source = replace_once(
    routes_source,
    'REVIEWED_STATUS_NAMES = {"تم المراجعة", "تمت المراجعة"}\n',
    'REVIEWED_STATUS_NAMES = {"تم المراجعة", "تمت المراجعة"}\nREVIEW_SOURCE_REFRESH_VERSION = 2\n',
    "review refresh version constant",
)
routes_source = replace_once(
    routes_source,
    '{"_id": 0, "order_review_source_refreshed_at": 1},',
    '{"_id": 0, "order_review_source_refreshed_at": 1, "order_review_source_refresh_version": 1},',
    "review refresh projection",
)
routes_source = replace_once(
    routes_source,
    '''        if _text((existing or {}).get("order_review_source_refreshed_at")):
            return False
''',
    '''        refresh_version = int((existing or {}).get("order_review_source_refresh_version") or 0)
        if (
            refresh_version >= REVIEW_SOURCE_REFRESH_VERSION
            and _text((existing or {}).get("order_review_source_refreshed_at"))
        ):
            return False
''',
    "review refresh version gate",
)
routes_source = replace_once(
    routes_source,
    '''                "order_review_source_refreshed_at": refreshed_at,
                "order_review_source_refresh_mode": "explicit_review_open",
''',
    '''                "order_review_source_refreshed_at": refreshed_at,
                "order_review_source_refresh_mode": "explicit_review_open",
                "order_review_source_refresh_version": REVIEW_SOURCE_REFRESH_VERSION,
''',
    "review refresh version persistence",
)
ROUTES_PATH.write_text(routes_source, encoding="utf-8")


# 3) Add regressions for shipping context and the one-time repair version.
test_source = TEST_PATH.read_text(encoding="utf-8")
test_source = replace_once(
    test_source,
    'from order_engine.mapper import map_salla_order\n',
    'from order_engine.mapper import map_salla_order\nfrom salla_integration.sync import _fetch_salla_shipment_details\n',
    "shipment helper test import",
)
test_source = replace_once(
    test_source,
    '''from order_review_routes import (
    _can_review,
''',
    '''from order_review_routes import (
    REVIEW_SOURCE_REFRESH_VERSION,
    _can_review,
''',
    "refresh version test import",
)
test_source = replace_once(
    test_source,
    '''    def __init__(self):
        self.row = {"user_id": "owner-1", "order_number": "274724433"}
''',
    '''    def __init__(self):
        self.row = {
            "user_id": "owner-1",
            "order_number": "274724433",
            # Marker written by the first implementation, before versioning.
            "order_review_source_refreshed_at": "2026-07-28T20:00:00+00:00",
        }
''',
    "legacy refresh marker fixture",
)
test_source = replace_once(
    test_source,
    '''    refresh.assert_awaited_once_with(db, "owner-1", "274724433")
    assert db.unified_orders.row["order_review_source_refresh_mode"] == "explicit_review_open"
''',
    '''    refresh.assert_awaited_once_with(db, "owner-1", "274724433")
    assert db.unified_orders.row["order_review_source_refresh_mode"] == "explicit_review_open"
    assert db.unified_orders.row["order_review_source_refresh_version"] == REVIEW_SOURCE_REFRESH_VERSION
''',
    "refresh version assertion",
)
if "test_empty_current_shipments_preserve_embedded_delivery_context" not in test_source:
    test_source += r'''


@pytest.mark.asyncio
async def test_empty_current_shipments_preserve_embedded_delivery_context():
    embedded = [{
        "id": "shipment-1",
        "courier": {"name": "iMile"},
        "shipping_address": {
            "country": {"name": "السعودية"},
            "city": {"name": "الرياض"},
            "district": "العليا",
            "street": "شارع الاختبار",
        },
        "label_url": "https://example.test/stale-label.pdf",
        "tracking_number": "STALE-TRACKING",
    }]

    async def fake_call(_db, _user_id, method, path, params=None):
        assert method == "GET"
        assert path == "/shipments"
        assert params == {"order_id": "order-1", "per_page": 50}
        return {"data": []}

    with patch("salla_integration.sync.call_salla", new=fake_call):
        rows = await _fetch_salla_shipment_details(
            object(), "owner-1", "order-1", embedded
        )

    assert len(rows) == 1
    assert rows[0]["courier"]["name"] == "iMile"
    assert rows[0]["shipping_address"]["city"]["name"] == "الرياض"
    assert rows[0]["shipping_address"]["district"] == "العليا"
    assert "label_url" not in rows[0]
    assert "tracking_number" not in rows[0]


@pytest.mark.asyncio
async def test_current_shipment_merges_embedded_address_without_reviving_stale_label():
    embedded = [{
        "id": "shipment-1",
        "courier": {"name": "iMile"},
        "shipping_address": {
            "city": {"name": "جدة"},
            "district": "الروضة",
            "street": "شارع الأمير",
        },
        "label_url": "https://example.test/stale-label.pdf",
    }]

    async def fake_call(_db, _user_id, method, path, params=None):
        assert method == "GET"
        if path == "/shipments":
            return {"data": [{"id": "shipment-1", "status": "created"}]}
        assert path == "/shipments/shipment-1"
        return {"data": {
            "id": "shipment-1",
            "tracking_number": "CURRENT-TRACKING",
            "shipping_address": {},
        }}

    with patch("salla_integration.sync.call_salla", new=fake_call):
        rows = await _fetch_salla_shipment_details(
            object(), "owner-1", "order-1", embedded
        )

    assert rows[0]["shipping_address"]["city"]["name"] == "جدة"
    assert rows[0]["shipping_address"]["street"] == "شارع الأمير"
    assert rows[0]["tracking_number"] == "CURRENT-TRACKING"
    assert "label_url" not in rows[0]
'''
TEST_PATH.write_text(test_source, encoding="utf-8")

print("Order review shipping context patch applied.")
