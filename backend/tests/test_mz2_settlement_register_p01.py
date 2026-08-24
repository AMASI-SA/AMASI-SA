from datetime import date

from fastapi import APIRouter

from accounting_settlement_register_routes import (
    REGISTER_STATUSES,
    _matches_q,
    _period_overlaps,
    _register_item,
    install_accounting_settlement_register_routes,
)


def test_register_exposes_searchable_list_and_auditable_detail_routes():
    router = APIRouter()

    async def current_user():
        return {"id": "owner"}

    install_accounting_settlement_register_routes(router, object(), current_user)
    routes = {(route.path, tuple(sorted(route.methods or []))) for route in router.routes}
    assert (
        "/accounting-module/settlements/register",
        ("GET",),
    ) in routes
    assert (
        "/accounting-module/settlements/register/{draft_id}",
        ("GET",),
    ) in routes


def test_register_supports_authoritative_matched_and_reversed_states():
    assert "matched" in REGISTER_STATUSES
    assert "reversed" in REGISTER_STATUSES
    assert "posted" in REGISTER_STATUSES


def test_register_item_links_posted_settlement_to_ledger_detail():
    item = _register_item({
        "id": "draft-1",
        "provider": "tabby",
        "provider_label": "تابي",
        "status": "posted",
        "statement_reference": "TB-001",
        "amounts": {"reported_net": 120.5, "gross_sales": 130},
        "ledger_txn_group_id": "txn-group-1",
        "source_snapshot": {"filename": "tabby.xlsx"},
    })
    assert item["journal_href"] == "/transactions?txn_group_id=txn-group-1"
    assert item["reported_net"] == 120.5
    assert item["source_filename"] == "tabby.xlsx"


def test_register_search_and_period_overlap_are_fail_closed_and_predictable():
    document = {
        "statement_reference": "INV-4401",
        "provider_label": "تمارا",
        "bank_account_name": "الراجحي",
        "period_from": "2026-08-01",
        "period_to": "2026-08-07",
        "source_snapshot": {"filename": "tamara-week-31.xlsx"},
    }
    assert _matches_q(document, "4401") is True
    assert _matches_q(document, "الراجحي") is True
    assert _matches_q(document, "unrelated") is False
    assert _period_overlaps(document, date(2026, 8, 7), date(2026, 8, 10)) is True
    assert _period_overlaps(document, date(2026, 8, 8), date(2026, 8, 10)) is False
