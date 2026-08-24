from accounting_settlement_bank_match_routes import bank_match_review_reasons
from financial_provider_apps import make_financial_provider_apps_router


def test_bank_match_exact_amount_has_no_review_reason():
    reasons = bank_match_review_reasons({
        "bank_account_id": "bank-1",
        "bank_transaction_id": "txn-1",
        "bank_transaction_snapshot": {
            "id": "txn-1",
            "account_id": "bank-1",
            "direction": "in",
            "amount": 1000,
        },
        "amounts": {"reported_net": 1000},
    })
    assert reasons == []


def test_bank_match_difference_is_explicit_and_blocking():
    reasons = bank_match_review_reasons({
        "bank_account_id": "bank-1",
        "bank_transaction_id": "txn-1",
        "bank_transaction_snapshot": {
            "id": "txn-1",
            "account_id": "bank-1",
            "direction": "in",
            "amount": 995.25,
        },
        "amounts": {"reported_net": 1000},
    })
    assert reasons == [{
        "code": "bank_movement_difference",
        "message": "فرق حركة البنك عن صافي الكشف -4.75 SAR (البنك 995.25، الكشف 1000.00)",
    }]


def test_bank_match_rejects_wrong_account_and_direction():
    reasons = bank_match_review_reasons({
        "bank_account_id": "bank-1",
        "bank_transaction_id": "txn-1",
        "bank_transaction_snapshot": {
            "id": "txn-1",
            "account_id": "bank-2",
            "direction": "out",
            "amount": 1000,
        },
        "amounts": {"reported_net": 1000},
    })
    assert {item["code"] for item in reasons} == {
        "bank_movement_account_mismatch",
        "bank_movement_direction_invalid",
    }


def test_router_exposes_bank_candidates_and_match_routes():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(object(), current_user)
    paths = {route.path for route in router.routes}
    assert {
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/bank-candidates",
        "/financial-provider-apps/accounting-module/settlements/drafts/{draft_id}/bank-match",
    } <= paths


def test_authoritative_lifecycle_handlers_are_registered_before_compatibility_handlers():
    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    router = make_financial_provider_apps_router(object(), current_user)
    for suffix in ("submit", "review", "reject", "post"):
        path = (
            "/financial-provider-apps/accounting-module/settlements/"
            f"drafts/{{draft_id}}/{suffix}"
        )
        matching = [route for route in router.routes if route.path == path]
        assert len(matching) >= 2
        assert matching[0].endpoint.__module__ == "accounting_settlement_lifecycle_routes"
