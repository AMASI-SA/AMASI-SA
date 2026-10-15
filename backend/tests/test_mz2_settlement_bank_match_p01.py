from types import SimpleNamespace

import pytest

import accounting_settlement_bank_match_routes as bank_match_routes
from accounting_settlement_bank_match_routes import (
    BankMatchIn,
    bank_match_review_reasons,
)
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


class _SettlementCollection:
    def __init__(self):
        self.document = {
            "id": "draft-1",
            "user_id": "owner-1",
            "provider": "salla",
            "status": "needs_review",
            "review_reasons": [{
                "code": "bank_movement_difference",
                "message": "فرق حركة البنك عن صافي الكشف 20.00 SAR",
            }],
            "bank_transaction_id": "txn-1",
            "bank_transaction_snapshot": {"id": "txn-1", "amount": 120},
            "bank_transaction_difference": 20,
            "bank_matched_by": "accountant-1",
            "bank_matched_by_name": "المحاسب",
            "bank_matched_at": "2026-08-25T00:00:00+00:00",
        }
        self.created_indexes = []
        self.dropped_indexes = []
        self.update = None

    async def create_index(self, keys, **options):
        self.created_indexes.append((keys, options))
        return options.get("name")

    async def drop_index(self, name):
        self.dropped_indexes.append(name)

    async def find_one(self, _query, _projection=None):
        return dict(self.document)

    async def update_one(self, _query, update):
        self.update = update
        self.document.update(update.get("$set") or {})
        for field in (update.get("$unset") or {}):
            self.document.pop(field, None)
        return SimpleNamespace(matched_count=1)


class _Db:
    def __init__(self):
        self.accounting_settlements_v2 = _SettlementCollection()


class _Router:
    def __init__(self):
        self.put_endpoint = None

    def get(self, _path):
        return lambda function: function

    def put(self, _path):
        def decorator(function):
            self.put_endpoint = function
            return function
        return decorator


@pytest.mark.asyncio
async def test_clear_bank_match_unsets_unique_field_and_migrates_sparse_index(
    monkeypatch,
):
    async def fresh(_db, user):
        return {"id": user["id"], "role": "owner", "name": "المالك"}

    async def audit(*_args, **_kwargs):
        return "audit-1"

    monkeypatch.setattr(bank_match_routes, "fresh_accounting_user", fresh)
    monkeypatch.setattr(
        bank_match_routes,
        "require_accounting_permission",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        bank_match_routes,
        "accounting_owner_id",
        lambda _user: "owner-1",
    )
    monkeypatch.setattr(bank_match_routes, "write_audit", audit)

    db = _Db()
    router = _Router()

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    bank_match_routes.install_accounting_settlement_bank_match_routes(
        router,
        db,
        current_user,
    )
    result = await router.put_endpoint(
        "draft-1",
        BankMatchIn(
            bank_transaction_id=None,
            confirmed=False,
            notes="تنظيف الاختبار",
        ),
        {"id": "owner-1"},
    )

    update = db.accounting_settlements_v2.update
    assert "bank_transaction_id" not in update["$set"]
    assert update["$unset"] == {
        "bank_transaction_id": "",
        "bank_transaction_snapshot": "",
        "bank_transaction_difference": "",
        "bank_matched_by": "",
        "bank_matched_by_name": "",
        "bank_matched_at": "",
    }
    assert result["status"] == "draft"
    assert result["review_reasons"] == []
    assert result["bank_match_notes"] == ""
    assert "bank_transaction_id" not in result

    _, index_options = db.accounting_settlements_v2.created_indexes[0]
    assert index_options["unique"] is True
    assert index_options["partialFilterExpression"] == {
        "bank_transaction_id": {"$type": "string"},
    }
    assert index_options["name"] == "uniq_accounting_settlement_bank_transaction_v3"
    assert db.accounting_settlements_v2.dropped_indexes == [
        "uniq_accounting_settlement_bank_transaction_v2",
    ]
