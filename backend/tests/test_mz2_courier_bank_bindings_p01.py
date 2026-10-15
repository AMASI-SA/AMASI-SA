from accounting_courier_bank_routes import external_courier_catalog_from_rows
from financial_provider_apps import make_financial_provider_apps_router


def test_external_courier_catalog_dedupes_aliases_and_excludes_store_delivery():
    catalog = external_courier_catalog_from_rows([
        {"name": "سمسا", "is_deferred": True},
        {"name": "SMSA", "is_deferred": False},
        {"name": "أرامكس", "is_deferred": True},
        {"name": "Aramex", "is_deferred": True},
        {"name": "مندوب الرياض", "is_deferred": True},
        {"name": "مندوب المتجر", "is_deferred": True},
        {"name": "استلام من المتجر", "is_deferred": False},
        {"name": "تسليم مباشر", "is_deferred": False},
        {"name": "شركة محلية خارجية", "is_deferred": True},
        {"name": "", "is_deferred": True},
    ])
    assert [item["courier_key"] for item in catalog[:2]] == ["smsa", "aramex"]
    assert sum(item["courier_key"] == "smsa" for item in catalog) == 1
    assert sum(item["courier_key"] == "aramex" for item in catalog) == 1
    assert all(item["courier_key"] not in {"mandoob", "mandoob_riyadh", "pickup"} for item in catalog)
    assert all("مندوب" not in item["display_name"] for item in catalog)
    assert any(item["display_name"] == "شركة محلية خارجية" for item in catalog)
    assert all(item["provider_id"].startswith("shipping:") for item in catalog)


def test_external_courier_catalog_exposes_only_identity_and_payment_mode_not_financial_logic():
    [courier] = external_courier_catalog_from_rows([
        {
            "name": "سمسا",
            "cost_per_order": 23,
            "cod_fee_percent": 0.02,
            "is_deferred": True,
        }
    ])
    assert courier == {
        "courier_key": "smsa",
        "provider_id": "shipping:smsa",
        "display_name": "سمسا",
        "configured_name": "سمسا",
        "active": True,
        "payment_mode": "deferred",
    }
    assert "cost_per_order" not in courier
    assert "cod_fee_percent" not in courier


def test_router_registers_external_courier_bank_binding_contract():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(object(), current_user)
    paths = {route.path for route in router.routes}
    assert {
        "/financial-provider-apps/accounting-module/settlements/courier-bindings",
        "/financial-provider-apps/accounting-module/settlements/courier-bindings/{courier_key}",
    } <= paths
