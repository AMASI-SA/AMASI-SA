"""Read current carrier details without treating unavailable lists as absence.

Synthetic fixtures retain the sparse international-carrier response shape;
no customer data, credentials, provider writes or substitute labels are used.
"""
from copy import deepcopy

import pytest
from mongomock_motor import AsyncMongoMockClient

from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as shipping
from salla_integration.service import SallaError
from salla_shipping import CURRENT_SHIPPING


NUMBER = "3001"
ORDER_ID = "42"
SHIPMENT_ID = "99"
LABEL = "https://carrier.example/current-awb.pdf"
ORDER = {"id": 42, "reference_id": 3001, "status": {"slug": "completed"},
         "delivery_method": "shipping"}
SHIPMENT = {"id": 99, "order_id": 42, "order_reference_id": 3001,
            "type": "shipment", "status": "created", "is_international": True,
            "courier_id": 321, "courier_name": "Aramex International",
            "tracking_number": "SYNTHETIC-AWB", "label": None}


async def scenario(monkeypatch, *, known=True):
    db = AsyncMongoMockClient().mezan
    baseline = {"user_id": "owner", "order_number": NUMBER, "order_id": ORDER_ID}
    if known:
        baseline[CURRENT_SHIPPING] = {"shipment_id": SHIPMENT_ID,
            "company_name": "Aramex International", "company_code": "321"}
    await db.unified_orders.insert_one(baseline)

    async def local_owner(database, owner, callback):
        return await callback(database)

    monkeypatch.setattr(shipping, "operational_owner", local_owner)
    calls = []
    return db, calls


@pytest.mark.asyncio
@pytest.mark.parametrize("list_failure", ["unavailable", "empty", "scope"])
async def test_known_current_details_can_supply_original_awb_when_lists_cannot(monkeypatch, list_failure):
    db, calls = await scenario(monkeypatch)
    before = await db.unified_orders.find_one({"user_id": "owner", "order_number": NUMBER})

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path == "/shipments/99":
            return {"data": {**SHIPMENT, "label": {"url": LABEL}}}
        if path in {"/orders/42/shipments", "/shipments"}:
            if list_failure == "empty":
                return {"data": []}
            raise SallaError("Synthetic list failure", status_code=403 if list_failure == "scope" else 503)
        raise AssertionError(path)

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)

    assert result["ready"] is True
    assert result["shipment_id"] == SHIPMENT_ID
    assert result["label_url"] == LABEL
    assert result["tracking_number"] == "SYNTHETIC-AWB"
    assert ("GET", "/shipments/99") in calls
    assert await db.unified_orders.find_one({"user_id": "owner", "order_number": NUMBER}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,code,http_status", [
    ("scope", "shipping_scope_required", 403),
    ("timeout", "salla_shipping_unavailable", 502),
    ("server", "salla_shipping_unavailable", 502),
])
async def test_failed_shipment_reads_do_not_report_an_absent_shipment(monkeypatch, failure, code, http_status):
    db, calls = await scenario(monkeypatch, known=False)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if failure == "timeout":
            raise TimeoutError("Synthetic provider timeout")
        raise SallaError("Synthetic provider failure", status_code=403 if failure == "scope" else 503)

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == code
    assert error.value.status_code == http_status


@pytest.mark.asyncio
async def test_empty_legacy_list_does_not_hide_denied_modern_list(monkeypatch):
    db, calls = await scenario(monkeypatch, known=False)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path == "/orders/42/shipments":
            return {"data": []}
        raise SallaError("Synthetic scope denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == "shipping_scope_required"


@pytest.mark.asyncio
async def test_authoritative_empty_list_remains_pending_without_writes(monkeypatch):
    db, calls = await scenario(monkeypatch, known=False)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        return {"data": deepcopy(ORDER) if path == "/orders/42" else []}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is False
    assert result["shipment_id"] is None
    assert result["label_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"id": 100}, {"order_id": 777}, {"order_reference_id": 777},
    {"id": None}, {"order_id": None},
    {"order_id": 0, "order": {"id": 42}},
    {"order_reference_id": 0, "order": {"reference_id": 3001}},
])
async def test_current_id_hint_never_authorizes_a_different_or_inactive_shipment(monkeypatch, mutation):
    db, calls = await scenario(monkeypatch)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path.startswith("/shipments/99"):
            return {"data": {**SHIPMENT, "label": {"url": LABEL}, **mutation}}
        return {"data": []}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is False
    assert result["label_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [{"status": "cancelled"}, {"type": "return"}])
async def test_inactive_direct_details_cannot_be_revived_by_tracking(monkeypatch, mutation):
    db, calls = await scenario(monkeypatch)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path == "/shipments/99":
            return {"data": {**SHIPMENT, **mutation}}
        if path == "/shipments/99/tracking":
            return {"data": {**SHIPMENT, "label": {"url": LABEL}}}
        return {"data": []}

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == "shipping_snapshot_changed"
    assert ("GET", "/shipments/99/tracking") not in calls


@pytest.mark.asyncio
async def test_superseded_current_hint_is_not_read_or_printed(monkeypatch):
    db, calls = await scenario(monkeypatch)
    await db.unified_orders.update_one({"user_id": "owner", "order_number": NUMBER},
        {"$set": {f"{CURRENT_SHIPPING}.superseded_shipment_ids": [SHIPMENT_ID]}})

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        assert not path.startswith("/shipments/99")
        return {"data": deepcopy(ORDER) if path == "/orders/42" else []}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is False
    assert result["label_url"] is None


@pytest.mark.asyncio
async def test_sparse_international_row_retains_tracking_without_printing_an_invoice(monkeypatch):
    db, calls = await scenario(monkeypatch)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        row = {**SHIPMENT, "meta": {"commercial_invoice_url": "https://carrier.example/invoice.pdf"}}
        return {"data": [row] if path in {"/orders/42/shipments", "/shipments"} else row}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["shipment_id"] == SHIPMENT_ID
    assert result["tracking_number"] == "SYNTHETIC-AWB"
    assert result["ready"] is False
    assert result["label_url"] is None
    assert "print_data" not in result


@pytest.mark.asyncio
async def test_ready_order_label_survives_independent_list_scope_failures(monkeypatch):
    db, calls = await scenario(monkeypatch)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert (method, path) == ("GET", "/orders/42")
        return {"data": {**ORDER, "shipments": [{**SHIPMENT, "label": {"url": LABEL}}]}}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is True
    assert result["label_url"] == LABEL
    assert calls == [("GET", "/orders/42")]


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", [
    {**SHIPMENT, "id": 100},
    {**SHIPMENT, "status": "cancelled"},
    {**SHIPMENT, "type": "return"},
])
@pytest.mark.parametrize("source", ["scoped", "modern", "embedded"])
async def test_current_hint_cannot_overrule_contradictory_provider_list(monkeypatch, evidence, source):
    db, calls = await scenario(monkeypatch)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": {**ORDER, "shipments": [evidence] if source == "embedded" else []}}
        if ((source == "scoped" and path == "/orders/42/shipments")
                or (source == "modern" and path == "/shipments")):
            return {"data": [evidence]}
        if path.startswith("/shipments/99"):
            return {"data": {**SHIPMENT, "label": {"url": LABEL}}}
        return {"data": []}

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == "shipping_snapshot_changed"
    assert not any(path.startswith("/shipments/99") for _, path in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("source,evidence", [
    ("embedded", {**SHIPMENT, "id": 100, "order_id": 777}),
    ("scoped", {**SHIPMENT, "id": 100, "order_id": 0}),
    ("modern", {**SHIPMENT, "id": 100, "order_id": None}),
    ("modern", {**SHIPMENT, "id": 100, "order": {"id": 777}}),
    ("scoped", {**SHIPMENT, "id": 100, "order_reference_id": 777}),
    ("scoped", {**SHIPMENT, "id": 100, "status": "cancelled"}),
    ("scoped", {**SHIPMENT, "id": 100, "type": "return"}),
    ("superseded", {**SHIPMENT, "id": 100}),
])
async def test_unrelated_or_inactive_history_does_not_block_proven_current_details(monkeypatch, source, evidence):
    db, calls = await scenario(monkeypatch)
    if source == "superseded":
        await db.unified_orders.update_one({"user_id": "owner", "order_number": NUMBER},
            {"$set": {f"{CURRENT_SHIPPING}.superseded_shipment_ids": ["100"]}})

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": {**ORDER, "shipments": [evidence] if source == "embedded" else []}}
        if path == "/shipments/99":
            return {"data": {**SHIPMENT, "label": {"url": LABEL}}}
        if ((source in {"scoped", "superseded"} and path == "/orders/42/shipments")
                or (source == "modern" and path == "/shipments")):
            return {"data": [evidence]}
        return {"data": []}

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is True
    assert result["shipment_id"] == SHIPMENT_ID
    assert result["label_url"] == LABEL
    assert ("GET", "/shipments/99") in calls


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["refresh", "complete-and-refresh"])
@pytest.mark.parametrize("detail_ready", [True, False])
async def test_native_routes_use_current_details_or_return_read_error(monkeypatch, action, detail_ready):
    from test_mobile_external_label_read_parity import external_case, KEY, PATH

    async with external_case(monkeypatch) as (db, calls, client):
        before = await db.unified_orders.find_one(KEY)
        raw = before["raw_by_source"]["salla_direct"]
        row = raw["shipments"][0]

        async def provider(database, owner, method, path, **kwargs):
            calls.append((method, path, kwargs))
            assert method == "GET"
            if path == "/orders/123":
                return {"data": {**deepcopy(raw), "shipments": []}}
            if detail_ready and path == "/shipments/99":
                return {"data": deepcopy(row)}
            raise SallaError("Synthetic shipment read denial", status_code=403)

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/" + action)
        if detail_ready:
            assert response.status_code == 200, response.text
            assert response.json()["ready"] is True
            assert response.json()["shipment_id"] == "99"
            assert response.json()["label_url"] == row["label"]["url"]
            assert any(path == "/shipments/99" for _, path, _ in calls)
        else:
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "shipping_scope_required"
            assert "label_url" not in response.json()
        assert await db.unified_orders.find_one(KEY) == before
        assert not any(method != "GET" for method, _, _ in calls)
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("source", ["scoped", "details"])
@pytest.mark.parametrize("inactive", [{"status": "cancelled"}, {"type": "return"}])
async def test_observed_inactive_child_cannot_be_revived_by_later_ready_response(monkeypatch, known, source, inactive):
    db, calls = await scenario(monkeypatch, known=known)
    ready = {**deepcopy(SHIPMENT), "label": {"url": LABEL}}
    ended = {**deepcopy(SHIPMENT), **inactive}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path == "/orders/42/shipments":
            return {"data": {"shipment": deepcopy(ended if source == "scoped" else SHIPMENT)}}
        if path == "/shipments":
            return {"data": [deepcopy(ready)] if source == "scoped" else []}
        if path == "/shipments/99":
            return {"data": deepcopy(ended)}
        return {"data": deepcopy(ready)}

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == "shipping_snapshot_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", [False, True])
async def test_scoped_current_selection_preserves_canonical_replacement_fence(monkeypatch, replacement):
    db, calls = await scenario(monkeypatch, known=replacement)
    ended = {**deepcopy(SHIPMENT), **({"id": 100} if replacement else {"status": "cancelled"})}
    ready = {**deepcopy(SHIPMENT), "id": 99 if replacement else 100, "label": {"url": LABEL}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(ORDER)}
        if path == "/orders/42/shipments":
            return {"data": {"shipment": deepcopy(ended)}}
        return {"data": [deepcopy(ready)]}

    monkeypatch.setattr(compat, "call_salla", provider)
    if replacement:
        with pytest.raises(shipping.ShippingLabelError) as error:
            await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
        assert error.value.code == "shipping_snapshot_changed"
    else:
        result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
        assert result["ready"] is True
        assert result["shipment_id"] == "100"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["order", "scoped"])
async def test_string_zero_is_not_a_current_shipment_identity(monkeypatch, source):
    db, calls = await scenario(monkeypatch, known=False)
    row = {**deepcopy(SHIPMENT), "id": "0", "label": {"url": LABEL}}
    order = {**deepcopy(ORDER), **({"shipment": row} if source == "order" else {})}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        if path == "/orders/42/shipments" and source == "scoped":
            return {"data": {"shipment": deepcopy(row)}}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError):
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["shipping_label_url", "awb_url", "waybill_url"])
@pytest.mark.parametrize("source", ["order", "scoped"])
async def test_order_label_fields_recognized_by_ingestion_are_printable(monkeypatch, field, source):
    db, calls = await scenario(monkeypatch, known=False)
    row = {**deepcopy(SHIPMENT), field: LABEL}
    order = {**deepcopy(ORDER), **({"shipment": row} if source == "order" else {})}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        if path == "/orders/42/shipments" and source == "scoped":
            return {"data": {"shipment": deepcopy(row)}}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is True
    assert result["label_url"] == LABEL
    assert result["shipment_id"] == SHIPMENT_ID
    assert not any(path.startswith("/shipments") for _, path in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("document_field", ["documents", "label"])
@pytest.mark.parametrize("documents,expected", [
    ([{"type": "commercial_invoice", "url": "https://carrier.example/invoice.pdf"}], None),
    ({"type": "invoice", "url": "https://carrier.example/invoice.pdf"}, None),
    ([{"type": "commercial_invoice", "url": "https://carrier.example/invoice.pdf"},
      {"type": "shipping_label", "url": LABEL}], LABEL),
])
async def test_order_shipment_documents_never_replace_awb_with_invoice(monkeypatch, documents, expected, document_field):
    db, calls = await scenario(monkeypatch, known=False)
    order = {**deepcopy(ORDER), "shipment": {**deepcopy(SHIPMENT), document_field: deepcopy(documents)}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is bool(expected)
    assert result["label_url"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("denied", [False, True])
async def test_read_diagnostics_do_not_expose_provider_or_customer_data(monkeypatch, caplog, denied):
    db, calls = await scenario(monkeypatch, known=False)
    private = "SYNTHETIC-PRIVATE-MARKER"
    order = {**deepcopy(ORDER), "receiver": {"name": private, "address": private},
             "shipping": {"company_name": "Aramex International", "company_code": "321"}}
    if not denied:
        order["shipping"]["shipment"] = {**deepcopy(SHIPMENT),
            "label": {"url": "https://carrier.example/" + private + ".pdf"}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        raise SallaError("Provider failure " + private, status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    caplog.set_level("INFO", logger="mezan.carrier_label_read")
    if denied:
        with pytest.raises(shipping.ShippingLabelError) as error:
            await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
        assert "أعد الربط" not in str(error.value)
    else:
        result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
        assert result["ready"] is True
    assert any(record.name == "mezan.carrier_label_read" for record in caplog.records)
    assert private not in caplog.text
    assert "https://" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("carrier", ["Aramex International", "SMSA", "iMile"])
@pytest.mark.parametrize("shape", ["shipping.shipment", "shipping.shipment_inherited_carrier", "shipment",
                                  "scoped_singular", "scoped_singular_null_plural", "scoped_direct_null_alias"])
async def test_order_read_permission_supports_each_current_shipment_shape(monkeypatch, carrier, shape):
    db, calls = await scenario(monkeypatch, known=False)
    row = {**deepcopy(SHIPMENT), "courier_name": carrier, "label": {"url": LABEL}}
    order = {**deepcopy(ORDER), "shipping": {"company_name": carrier, "company_code": "321"}}
    if shape.startswith("shipping.shipment"):
        if shape.endswith("inherited_carrier"):
            row.pop("courier_name")
            row.pop("courier_id")
        order["shipping"]["shipment"] = deepcopy(row)
    elif shape == "shipment":
        order["shipment"] = deepcopy(row)

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        if path == "/orders/42/shipments":
            if shape == "scoped_singular":
                return {"data": {"shipment": deepcopy(row)}}
            if shape == "scoped_singular_null_plural":
                return {"data": {"shipments": None, "shipment": deepcopy(row)}}
            if shape == "scoped_direct_null_alias":
                return {"data": {**deepcopy(row), "shipment": None}}
        raise SallaError("Synthetic optional shipping API not granted", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    before = await db.unified_orders.find_one({"user_id": "owner", "order_number": NUMBER})
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)

    assert result["ready"] is True
    assert result["shipment_id"] == SHIPMENT_ID
    assert result["label_url"] == LABEL
    assert result["tracking_number"] == "SYNTHETIC-AWB"
    assert not any(path.startswith("/shipments") for _, path in calls)
    assert await db.unified_orders.find_one({"user_id": "owner", "order_number": NUMBER}) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"order_id": 777}, {"order_reference_id": 777},
    {"order": {"id": 777}}, {"order": {"reference_id": 777}},
    {"id": 100}, {"id": 0}, {"id": None}, {"status": "cancelled"}, {"type": "return"},
    {"courier_id": 777, "courier_name": "SMSA"},
])
async def test_nested_order_label_cannot_bypass_current_identity_fences(monkeypatch, mutation):
    db, calls = await scenario(monkeypatch)
    row = {**deepcopy(SHIPMENT), "label": {"url": LABEL}, **deepcopy(mutation)}
    order = {**deepcopy(ORDER), "shipping": {
        "company_name": "Aramex International", "company_code": "321", "shipment": row}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError):
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("current", [{"status": "cancelled"}, {"type": "return"}, {"id": 100}])
async def test_fresh_singular_current_selection_cannot_revive_older_ready_list(monkeypatch, current):
    db, calls = await scenario(monkeypatch)
    ready = {**deepcopy(SHIPMENT), "label": {"url": LABEL}}
    order = {**deepcopy(ORDER), "shipment": {**deepcopy(ready), **current}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        return {"data": [deepcopy(ready)]}

    monkeypatch.setattr(compat, "call_salla", provider)
    with pytest.raises(shipping.ShippingLabelError) as error:
        await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert error.value.code == "shipping_snapshot_changed"
    assert calls == [("GET", "/orders/42")]


@pytest.mark.asyncio
@pytest.mark.parametrize("top_key", ["shipments", "shipment"])
async def test_explicit_top_level_selection_never_uses_nested_historical_awb(monkeypatch, top_key):
    db, calls = await scenario(monkeypatch, known=False)
    latest = {**deepcopy(SHIPMENT), "id": 100}
    order = {**deepcopy(ORDER), top_key: [latest] if top_key == "shipments" else latest,
             "shipping": {"company_name": "Aramex International", "company_code": "321",
                          "shipment": {**deepcopy(SHIPMENT), "label": {"url": LABEL}}}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is False
    assert result["shipment_id"] == "100"
    assert result["label_url"] is None


@pytest.mark.asyncio
async def test_nested_order_shipment_inherits_carrier_without_replacing_null_label(monkeypatch):
    db, calls = await scenario(monkeypatch, known=False)
    row = deepcopy(SHIPMENT)
    row.pop("courier_id")
    row.pop("courier_name")
    row["meta"] = {"commercial_invoice_url": "https://carrier.example/invoice.pdf"}
    order = {**deepcopy(ORDER), "shipping": {
        "company_name": "Aramex International", "company_code": "321", "shipment": row}}

    async def provider(database, owner, method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        if path == "/orders/42":
            return {"data": deepcopy(order)}
        raise SallaError("Synthetic optional API denial", status_code=403)

    monkeypatch.setattr(compat, "call_salla", provider)
    result = await compat.carrier_label(db, "owner", NUMBER, issue=False, completed_only=True)
    assert result["ready"] is False
    assert result["shipment_id"] == SHIPMENT_ID
    assert result["tracking_number"] == "SYNTHETIC-AWB"
    assert result["label_url"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["refresh", "complete-and-refresh"])
@pytest.mark.parametrize("shape", ["shipping.shipment", "shipment", "scoped_singular",
                                  "scoped_singular_null_plural", "scoped_direct_null_alias"])
async def test_native_routes_accept_order_payload_label_without_extra_permission(monkeypatch, action, shape):
    from test_mobile_external_label_read_parity import external_case, KEY, PATH

    async with external_case(monkeypatch) as (db, calls, client):
        before = await db.unified_orders.find_one(KEY)
        raw = deepcopy(before["raw_by_source"]["salla_direct"])
        row = raw.pop("shipments")[0]
        raw["shipping"] = {"company_name": row["courier_name"], "company_code": str(row["courier_id"])}
        if shape == "shipping.shipment":
            raw["shipping"]["shipment"] = deepcopy(row)
        elif shape == "shipment":
            raw["shipment"] = deepcopy(row)

        async def provider(database, owner, method, path, **kwargs):
            calls.append((method, path, kwargs))
            assert method == "GET"
            if path == "/orders/123":
                return {"data": deepcopy(raw)}
            if path == "/orders/123/shipments":
                if shape == "scoped_singular":
                    return {"data": {"shipment": deepcopy(row)}}
                if shape == "scoped_singular_null_plural":
                    return {"data": {"shipments": None, "shipment": deepcopy(row)}}
                if shape == "scoped_direct_null_alias":
                    return {"data": {**deepcopy(row), "shipment": None}}
            raise SallaError("Synthetic optional shipping API not granted", status_code=403)

        monkeypatch.setattr(compat, "call_salla", provider)
        response = await client.post(PATH + "/" + action)
        assert response.status_code == 200, response.text
        assert response.json()["ready"] is True
        assert response.json()["shipment_id"] == "99"
        assert response.json()["label_url"] == row["label"]["url"]
        assert not any(method != "GET" or path.startswith("/shipments") for method, path, _ in calls)
        assert await db.unified_orders.find_one(KEY) == before
        assert "manual_label_refresh" not in await db.order_review_workflows.find_one(KEY)
