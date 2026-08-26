from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from integrations_control_center import snapchat_campaign_management as management


class _Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                if isinstance(actual, list):
                    if not any(value in expected["$in"] for value in actual):
                        return False
                elif actual not in expected["$in"]:
                    return False
            if "$nin" in expected:
                if isinstance(actual, list):
                    if any(value in expected["$nin"] for value in actual):
                        return False
                elif actual in expected["$nin"]:
                    return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args):
        return self

    def limit(self, limit):
        self.rows = self.rows[:limit]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *_args, **_kwargs):
        return None

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def find_one(self, query, _projection=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    def find(self, query, _projection=None):
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            for key, value in (update.get("$inc") or {}).items():
                row[key] = row.get(key, 0) + value
            return _Result(1)
        if upsert:
            inserted = {**query, **deepcopy(update.get("$set") or {})}
            self.rows.append(inserted)
            return _Result(1)
        return _Result(0)

    async def update_many(self, query, update):
        count = 0
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                count += 1
        return _Result(count)


class _DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


@pytest.mark.asyncio
async def test_readiness_exposes_shared_pixel_for_every_linked_account(monkeypatch):
    db = _DB()
    db[management.TRACKING_ASSET_COLLECTION].rows.append({
        "user_id": "owner-1",
        "pixel_id": "shared-pixel",
        "display_name": "Self Service Pixel",
        "ad_account_id": "saudi",
        "ad_account_ids": ["self-service", "saudi"],
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "last_observed_at": "2026-08-14T13:00:00+00:00",
    })

    async def selected_accounts(*_args, **_kwargs):
        return [
            {
                "ad_account_id": "self-service",
                "display_name": "متجر أماسي Self Service",
                "currency": "USD",
                "timezone": "America/Los_Angeles",
            },
            {
                "ad_account_id": "saudi",
                "display_name": "متجر أماسي سعودي",
                "currency": "SAR",
                "timezone": "Asia/Riyadh",
            },
        ]

    class Provider:
        async def management_role(self, *_args, **_kwargs):
            return {"role": "admin", "allowed": True, "reason": None}

    monkeypatch.setattr(management, "_load_selected_accounts", selected_accounts)
    result = await management.snapchat_management_readiness(
        db,
        "owner-1",
        provider=Provider(),
    )

    assert [account["pixels"][0]["pixel_id"] for account in result["accounts"]] == [
        "shared-pixel",
        "shared-pixel",
    ]


def _ad_squad_payload(**overrides):
    payload = {
        "name": "AMASI Pixel Squad",
        "type": "SNAP_ADS",
        "targeting": {
            "regulated_content": False,
            "geos": [{"country_code": "sa"}],
        },
        "placement_v2": {"config": "AUTOMATIC"},
        "billing_event": "IMPRESSION",
        "bid_strategy": "AUTO_BID",
        "optimization_goal": "PIXEL_PURCHASE",
        "pixel_id": "pixel-1",
        "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        "daily_budget_micro": 50_000_000,
        "delivery_constraint": "NOT_TRUSTED_FROM_CALLER",
    }
    payload.update(overrides)
    return management.SnapchatManagementProposalInput(
        action="ad_squad.create",
        account_id="account-1",
        parent_id="campaign-1",
        payload=payload,
        reason="اختبار Pixel آمن داخل ميزان",
        idempotency_key="pixel-management-v2-001",
        safety_protocol_version=2,
    )


def _ad_create_operation():
    return management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="ad.create",
            account_id="account-1",
            parent_id="squad-1",
            payload={
                "name": "Safe paused ad",
                "creative_id": "creative-1",
                "type": "REMOTE_WEBPAGE",
            },
            reason="اختبار إعادة فحص تبعيات الإعلان",
            idempotency_key="ad-create-final-preflight-v2",
            safety_protocol_version=2,
        )
    )


def _approved_v2_create_row(proposal_id, operation, *, pixel_proof=None):
    return {
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved_v2",
        "revision": 1,
        "safety_protocol_version": 2,
        "account_id": str(operation.get("account_id") or ""),
        "parent_id": operation.get("parent_id"),
        "action": str(operation.get("action") or ""),
        "operation": deepcopy(operation),
        "intent_fingerprint": management.snapchat_management_intent_fingerprint(
            operation
        ),
        "pixel_eligibility": deepcopy(pixel_proof or {}),
        "products": [],
        "product_link_state": "not_supplied",
        "provider_write_state": "not_attempted",
        "provider_write_reached": False,
        "provider_write_uncertain": False,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=15)
        ).isoformat(),
    }


def _prepare_execution(monkeypatch):
    async def selected(*_args, **_kwargs):
        return {
            "ad_account_id": "account-1",
            "organization_id": "org-1",
        }

    async def no_cache_write(*_args, **_kwargs):
        return None

    async def no_decision_write(*_args, **_kwargs):
        return None

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_upsert_entity", no_cache_write)
    monkeypatch.setattr(
        management,
        "_record_management_decision_deferred_safe",
        no_decision_write,
    )
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")


def _uncertain_campaign_case(proposal_id="uncertain-campaign"):
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.create",
            account_id="account-1",
            payload={
                "name": "Uncertain create",
                "start_time": "2026-08-13T00:00:00Z",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
                "daily_budget_micro": 50_000_000,
            },
            reason="مصالحة محاولة إنشاء غير محسومة",
            idempotency_key=f"{proposal_id}-key",
            safety_protocol_version=2,
        )
    )
    started = datetime.now(timezone.utc) - timedelta(minutes=10)
    failed = started + timedelta(minutes=1)
    row = {
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "account_id": "account-1",
        "action": "campaign.create",
        "status": "failed",
        "operation": operation,
        "safety_protocol_version": 2,
        "intent_fingerprint": management.snapchat_management_intent_fingerprint(
            operation
        ),
        "execution_started_at": started.isoformat(),
        "failed_at": failed.isoformat(),
        "provider_write_state": "unknown_needs_reconciliation",
        "provider_write_uncertain": True,
    }
    candidate = deepcopy(operation["body"]["campaigns"][0])
    candidate.update(
        {
            "id": f"{proposal_id}-provider-id",
            "created_at": (started + timedelta(seconds=30)).isoformat(),
        }
    )
    return operation, row, candidate


def test_pixel_goal_requires_explicit_pixel_and_conversion_window():
    with pytest.raises(ValueError, match="pixel_id"):
        management.build_snapchat_operation(
            _ad_squad_payload(pixel_id=None)
        )
    with pytest.raises(ValueError, match="conversion_window"):
        management.build_snapchat_operation(
            _ad_squad_payload(conversion_window=None)
        )
    with pytest.raises(ValueError, match="pixel_id"):
        management.build_snapchat_operation(
            _ad_squad_payload(
                optimization_goal="PIXEL_FUTURE_EVENT",
                pixel_id=None,
            )
        )


def test_ad_squad_post_forces_paused_and_delivery_constraint():
    operation = management.build_snapchat_operation(_ad_squad_payload())
    entity = operation["body"]["adsquads"][0]
    assert operation["path"] == "/campaigns/campaign-1/adsquads"
    assert entity["status"] == "PAUSED"
    assert entity["delivery_constraint"] == "DAILY_BUDGET"
    assert entity["pixel_id"] == "pixel-1"
    assert entity["conversion_window"] == "SWIPE_28DAY_VIEW_1DAY"


@pytest.mark.asyncio
async def test_preview_persists_v2_pixel_warning_and_intent_before_any_write(
    monkeypatch,
):
    db = _DB()
    calls = []

    async def selected(*_args, **_kwargs):
        return {"ad_account_id": "account-1", "organization_id": "org-1"}

    async def baseline(*_args, **_kwargs):
        return {"campaign_id": "campaign-1", "windows": []}

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_capture_proposal_baseline", baseline)

    class PreviewProvider:
        async def management_role(self, _account, _action):
            calls.append("role")
            return {"allowed": True, "role": "general"}

        async def read_entity(self, entity_type, entity_id):
            calls.append(("read", entity_type, entity_id))
            return {
                "id": "campaign-1",
                "ad_account_id": "account-1",
            }

        async def validate_pixel_ad_squad_intent(self, **kwargs):
            calls.append(("pixel", deepcopy(kwargs)))
            return {
                "verified": True,
                "pixel_id": "pixel-1",
                "account_id": "account-1",
                "optimization_goal": "PIXEL_PURCHASE",
                "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
                "eligibility_status": "ELIGIBLE_WARNING",
                "warning": True,
                "warning_properties": ["limited_purchase_volume"],
                "membership_verified": True,
                "pixel_status": "ACTIVE",
                "pixel_effective_status": "ACTIVE",
                "membership_verified_at": datetime.now(timezone.utc).isoformat(),
            }

        async def execute(self, _operation):
            raise AssertionError("preview must never write to Snapchat")

    result = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        _ad_squad_payload(),
        provider=PreviewProvider(),
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert result["status"] == "previewed_v2"
    assert result["pixel_eligibility"]["warning"] is True
    assert result["pixel_eligibility"]["membership_verified"] is True
    assert stored["safety_protocol_version"] == 2
    assert stored["intent_fingerprint"] == (
        management.snapchat_management_intent_fingerprint(stored["operation"])
    )
    assert stored["operation"]["body"]["adsquads"][0]["status"] == "PAUSED"
    assert [call for call in calls if call == "execute"] == []


class _PagedProvider(management.SnapchatManagementProvider):
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def _request(self, method, path, *, body=None, content_type="application/json", params=None):
        self.calls.append((method, path, deepcopy(params)))
        return deepcopy(self.responses.pop(0))


class _TokenContext:
    async def access_token(self):
        return "test-token"


class _HTTPResponse:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return deepcopy(self.payload)


class _HTTPClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def request(self, *_args, **_kwargs):
        return _HTTPResponse(self.payload)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["PARTIAL", "UNKNOWN", "PENDING"])
async def test_request_rejects_nonterminal_top_status_from_real_http_path(
    monkeypatch, status
):
    client = _HTTPClient({"request_status": status, "pixels": []})
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **_kwargs: client)
    provider = management.SnapchatManagementProvider(_DB(), "owner-1")
    provider.context = _TokenContext()
    with pytest.raises(HTTPException) as raised:
        await provider._request("GET", "/adaccounts/account-1/pixels")
    assert raised.value.detail["code"] == "snapchat_management_request_failed"


@pytest.mark.asyncio
async def test_request_rejects_missing_top_status_from_real_http_path(monkeypatch):
    client = _HTTPClient({"pixels": []})
    monkeypatch.setattr(management.httpx, "AsyncClient", lambda **_kwargs: client)
    provider = management.SnapchatManagementProvider(_DB(), "owner-1")
    provider.context = _TokenContext()
    with pytest.raises(HTTPException) as raised:
        await provider._request("GET", "/adaccounts/account-1/pixels")
    assert raised.value.detail["code"] == "snapchat_management_request_failed"


@pytest.mark.asyncio
async def test_complete_catalog_rejects_partial_subrequest_and_conflicting_duplicate():
    partial = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [{
            "sub_request_status": "PARTIAL",
            "pixel": {"id": "pixel-1"},
        }],
    }])
    with pytest.raises(HTTPException) as raised:
        await partial.list_account_pixels("account-1")
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"

    duplicate = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [
            {"sub_request_status": "SUCCESS", "pixel": {"id": "pixel-1", "name": "A"}},
            {"sub_request_status": "SUCCESS", "pixel": {"id": "pixel-1", "name": "B"}},
        ],
    }])
    with pytest.raises(HTTPException) as duplicate_error:
        await duplicate.list_account_pixels("account-1")
    assert duplicate_error.value.detail["code"] == (
        "snapchat_management_catalog_duplicate_conflict"
    )

    missing = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [{"pixel": {"id": "pixel-1"}}],
    }])
    with pytest.raises(HTTPException) as missing_error:
        await missing.list_account_pixels("account-1")
    assert missing_error.value.detail["code"] == (
        "snapchat_management_catalog_incomplete"
    )

    missing_pixel_singular = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [{
            "sub_request_status": "SUCCESS",
            "id": "pixel-1",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
        }],
    }])
    with pytest.raises(HTTPException) as missing_singular_error:
        await missing_pixel_singular.list_account_pixels("account-1")
    assert missing_singular_error.value.detail["code"] == (
        "snapchat_management_catalog_incomplete"
    )


@pytest.mark.asyncio
async def test_paginated_catalog_rejects_mixed_missing_wrapper_status():
    provider = _PagedProvider([
        {
            "request_status": "SUCCESS",
            "pixels": [{
                "sub_request_status": "SUCCESS",
                "pixel": {"id": "pixel-1"},
            }],
            "paging": {
                "next_link": (
                    "https://adsapi.snapchat.com/v1/adaccounts/account-1/"
                    "pixels?cursor=2"
                )
            },
        },
        {
            "request_status": "SUCCESS",
            "pixels": [{"pixel": {"id": "pixel-2"}}],
            "paging": {},
        },
    ])
    with pytest.raises(HTTPException) as raised:
        await provider.list_account_pixels("account-1")
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"


@pytest.mark.parametrize(
    "payload",
    [
        {"adsquads": [{"adsquad": {"id": "squad-1"}}]},
        {
            "request_status": "SUCCESS",
            "adsquads": [{"adsquad": {"id": "squad-1"}}],
        },
    ],
)
def test_create_response_missing_required_status_is_never_confirmed(payload):
    with pytest.raises(HTTPException) as raised:
        management._extract_entity(payload, "adsquads", "adsquad")
    assert raised.value.detail["code"] == "snapchat_management_subrequest_failed"


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"adsquads": []}, "snapchat_management_entity_missing"),
        (
            {
                "adsquads": [
                    {
                        "sub_request_status": "SUCCESS",
                        "adsquad": {"id": "squad-1"},
                    },
                    {
                        "sub_request_status": "SUCCESS",
                        "adsquad": {"id": "squad-2"},
                    },
                ]
            },
            "snapchat_management_entity_missing",
        ),
        ({"adsquads": ["not-a-wrapper"]}, "snapchat_management_entity_invalid"),
        (
            {"adsquads": [{"sub_request_status": "SUCCESS"}]},
            "snapchat_management_entity_invalid",
        ),
        (
            {
                "adsquads": [{
                    "sub_request_status": "SUCCESS",
                    "adsquad": {"id": "squad-1"},
                }],
                "ads": [{
                    "sub_request_status": "SUCCESS",
                    "ad": {"id": "ad-1"},
                }],
            },
            "snapchat_management_entity_invalid",
        ),
    ],
)
def test_extract_entity_requires_exactly_one_expected_success_wrapper(
    payload, expected_code
):
    with pytest.raises(HTTPException) as raised:
        management._extract_entity(payload, "adsquads", "adsquad")
    assert raised.value.detail["code"] == expected_code


@pytest.mark.asyncio
async def test_complete_catalog_rejects_unsafe_paging_malformed_rows_and_page_limit():
    unsafe = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [],
        "paging": {"next_link": "https://attacker.example/pixels?cursor=2"},
    }])
    with pytest.raises(HTTPException) as unsafe_error:
        await unsafe.list_account_pixels("account-1")
    assert unsafe_error.value.detail["code"] == "snapchat_management_catalog_incomplete"

    malformed = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": ["not-a-wrapper"],
    }])
    with pytest.raises(HTTPException) as malformed_error:
        await malformed.list_account_pixels("account-1")
    assert malformed_error.value.detail["code"] == (
        "snapchat_management_catalog_incomplete"
    )

    invalid_paging = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [],
        "paging": "not-an-object",
    }])
    with pytest.raises(HTTPException) as paging_error:
        await invalid_paging.list_account_pixels("account-1")
    assert paging_error.value.detail["code"] == (
        "snapchat_management_catalog_incomplete"
    )

    repeating = _PagedProvider([
        {
            "request_status": "SUCCESS",
            "pixels": [],
            "paging": {
                "next_link": (
                    "https://adsapi.snapchat.com/v1/adaccounts/account-1/"
                    f"pixels?cursor={index + 1}"
                )
            },
        }
        for index in range(management.MAX_MANAGEMENT_LIST_PAGES)
    ])
    with pytest.raises(HTTPException) as page_error:
        await repeating.list_account_pixels("account-1")
    assert page_error.value.detail["code"] == (
        "snapchat_management_catalog_page_limit_reached"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [0, False, [], {}, " ", "\t"])
async def test_complete_catalog_rejects_falsey_or_whitespace_malformed_next_link(
    next_link,
):
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [],
        "paging": {"next_link": next_link},
    }])
    with pytest.raises(HTTPException) as raised:
        await provider.list_account_pixels("account-1")
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize("paging", [{}, {"next_link": None}, {"next_link": ""}])
async def test_complete_catalog_accepts_only_documented_terminal_paging_shapes(paging):
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "pixels": [],
        "paging": paging,
    }])
    assert await provider.list_account_pixels("account-1") == []


@pytest.mark.asyncio
async def test_identical_synthetic_eligibility_duplicate_is_deduplicated():
    eligibility = {
        "optimization_goal": "PIXEL_PURCHASE",
        "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        "eligibility_status": "ELIGIBLE",
    }
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "campaign_eligibilities": [
            {
                "sub_request_status": "SUCCESS",
                "campaign_eligibility": deepcopy(eligibility),
            },
            {
                "sub_request_status": "SUCCESS",
                "campaign_eligibility": deepcopy(eligibility),
            },
        ],
        "paging": {},
    }])
    rows = await provider._list_complete(
        "/pixels/pixel-1/campaign_eligibilities",
        plural="campaign_eligibilities",
        singular="campaign_eligibility",
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "PIXEL_PURCHASE:SWIPE_28DAY_VIEW_1DAY"


@pytest.mark.asyncio
async def test_management_role_consumes_all_trusted_pages_before_allowing():
    provider = _PagedProvider([
        {
            "request_status": "SUCCESS",
            "organizations": [],
            "paging": {
                "next_link": (
                    "https://adsapi.snapchat.com/v1/me/organizations?cursor=2"
                )
            },
        },
        {
            "request_status": "SUCCESS",
            "organizations": [{
                "sub_request_status": "SUCCESS",
                "organization": {"id": "org-1", "my_member_id": "member-1"},
            }],
            "paging": {},
        },
        {
            "request_status": "SUCCESS",
            "roles": [],
            "paging": {
                "next_link": (
                    "https://adsapi.snapchat.com/v1/members/member-1/"
                    "roles?cursor=2"
                )
            },
        },
        {
            "request_status": "SUCCESS",
            "roles": [{
                "sub_request_status": "SUCCESS",
                "role": {
                    "id": "role-1",
                    "type": "GENERAL",
                    "ad_account_id": "account-1",
                    "organization_id": "org-1",
                },
            }],
            "paging": {},
        },
    ])
    provider._management_roles_cache = {}
    result = await provider.management_role(
        {"organization_id": "org-1", "ad_account_id": "account-1"},
        "ad_squad.create",
    )
    assert result == {"allowed": True, "role": "general", "reason": None}
    assert len(provider.calls) == 4
    assert provider.calls[0][2]["with_ad_accounts"] == "true"
    assert provider.calls[2][2]["limit"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper_status", ["PARTIAL", "ERROR"])
async def test_management_role_rejects_non_success_wrapper(wrapper_status):
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "organizations": [{
            "sub_request_status": wrapper_status,
            "organization": {"id": "org-1", "my_member_id": "member-1"},
        }],
    }])
    provider._management_roles_cache = {}
    with pytest.raises(HTTPException) as raised:
        await provider.management_role(
            {"organization_id": "org-1", "ad_account_id": "account-1"},
            "ad_squad.create",
        )
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"


@pytest.mark.asyncio
async def test_management_role_rejects_success_wrapper_without_explicit_singular():
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "organizations": [{
            "sub_request_status": "SUCCESS",
            "id": "org-1",
            "my_member_id": "member-1",
        }],
    }])
    provider._management_roles_cache = {}
    with pytest.raises(HTTPException) as raised:
        await provider.management_role(
            {"organization_id": "org-1", "ad_account_id": "account-1"},
            "ad_squad.create",
        )
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"


@pytest.mark.asyncio
async def test_management_role_rejects_unsafe_paging_without_using_partial_roles():
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "organizations": [],
        "paging": {"next_link": "https://attacker.example/organizations?cursor=2"},
    }])
    provider._management_roles_cache = {}
    with pytest.raises(HTTPException) as raised:
        await provider.management_role(
            {"organization_id": "org-1", "ad_account_id": "account-1"},
            "ad_squad.create",
        )
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "plural", "singular", "path"),
    [
        ("campaign", "campaigns", "campaign", "/adaccounts/account-1/campaigns"),
        ("ad_squad", "adsquads", "adsquad", "/adaccounts/account-1/adsquads"),
        ("ad", "ads", "ad", "/adaccounts/account-1/ads"),
    ],
)
async def test_create_reconciliation_catalog_is_account_scoped_deleted_aware_without_sort(
    entity_type, plural, singular, path
):
    operation = {"entity_type": entity_type, "account_id": "account-1"}
    candidate = {"id": f"{entity_type}-1"}
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        plural: [{
            "sub_request_status": "SUCCESS",
            singular: deepcopy(candidate),
        }],
        "paging": {},
    }])
    rows = await provider.list_create_reconciliation_candidates(operation)
    assert len(rows) == 1
    assert provider.calls[0][1] == path
    params = provider.calls[0][2]
    assert params["read_deleted_entities"] == "true"
    assert params.get("return_placement_v2") == (
        "true" if entity_type == "ad_squad" else None
    )
    assert "sort" not in params


@pytest.mark.asyncio
async def test_ad_squad_point_read_requests_placement_v2_for_exact_verification():
    provider = _PagedProvider([{
        "request_status": "SUCCESS",
        "adsquads": [{
            "sub_request_status": "SUCCESS",
            "adsquad": {"id": "squad-1", "placement_v2": {"config": "AUTOMATIC"}},
        }],
    }])

    entity = await provider.read_entity("ad_squad", "squad-1")

    assert entity["id"] == "squad-1"
    assert provider.calls == [(
        "GET",
        "/adsquads/squad-1",
        {"return_placement_v2": "true"},
    )]


@pytest.mark.asyncio
async def test_pixel_validation_requires_account_association_and_exact_eligibility():
    operation = management.build_snapchat_operation(_ad_squad_payload())
    provider = _PagedProvider([])
    provider.read_entity = lambda *_args: None
    provider.list_account_pixels = lambda *_args: None

    async def parent(*_args):
        return {"id": "campaign-1", "ad_account_id": "account-1"}

    async def pixels(*_args):
        return [{
            "id": "pixel-1",
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
        }]

    async def eligible(**_kwargs):
        return {
            "verified": True,
            "pixel_id": "pixel-1",
            "account_id": "account-1",
            "optimization_goal": "PIXEL_PURCHASE",
            "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
            "eligibility_status": "ELIGIBLE_WARNING",
            "warning": True,
        }

    provider.read_entity = parent
    provider.list_account_pixels = pixels
    provider.pixel_eligibility = eligible
    proof = await provider.validate_pixel_ad_squad_intent(
        account_id="account-1",
        parent_id="campaign-1",
        operation=operation,
    )
    assert proof["warning"] is True
    assert proof["membership_verified"] is True
    assert proof["pixel_status"] == "ACTIVE"
    assert proof["pixel_effective_status"] == "ACTIVE"

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "effective_status"),
    [
        ("PAUSED", "ACTIVE"),
        ("UNKNOWN", "ACTIVE"),
        (None, "ACTIVE"),
        ("ACTIVE", "PAUSED"),
        ("ACTIVE", None),
    ],
)
async def test_pixel_membership_defers_status_to_authoritative_eligibility(
    status, effective_status
):
    operation = management.build_snapchat_operation(_ad_squad_payload())
    provider = _PagedProvider([])

    async def parent(*_args):
        return {"id": "campaign-1", "ad_account_id": "account-1"}

    async def pixels(*_args):
        return [{
            "id": "pixel-1",
            "status": status,
            "effective_status": effective_status,
        }]

    async def eligible(**_kwargs):
        return {
            "verified": True,
            "pixel_id": "pixel-1",
            "account_id": "account-1",
            "optimization_goal": "PIXEL_PURCHASE",
            "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
            "eligibility_status": "ELIGIBLE",
            "warning": False,
        }

    provider.read_entity = parent
    provider.list_account_pixels = pixels
    provider.pixel_eligibility = eligible
    proof = await provider.validate_pixel_ad_squad_intent(
        account_id="account-1",
        parent_id="campaign-1",
        operation=operation,
    )
    assert proof["verified"] is True
    assert proof["eligibility_status"] == "ELIGIBLE"
    assert proof["pixel_status"] == (status or "")
    assert proof["pixel_effective_status"] == (effective_status or "")


@pytest.mark.asyncio
async def test_eligibility_reads_trusted_pagination_and_blocks_ineligible():
    provider = _PagedProvider([
        {
            "request_status": "SUCCESS",
            "campaign_eligibilities": [],
            "paging": {
                "next_link": (
                    "https://adsapi.snapchat.com/v1/pixels/pixel-1/"
                    "campaign_eligibilities?cursor=2"
                )
            },
        },
        {
            "request_status": "SUCCESS",
            "campaign_eligibilities": [{
                "sub_request_status": "SUCCESS",
                "campaign_eligibility": {
                    "optimization_goal": "PIXEL_PURCHASE",
                    "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
                    "eligibility_status": "INELIGIBLE",
                    "ineligible_properties": ["purchase_volume"],
                },
            }],
            "paging": {},
        },
    ])
    with pytest.raises(HTTPException) as blocked:
        await provider.pixel_eligibility(
            account_id="account-1",
            pixel_id="pixel-1",
            optimization_goal="PIXEL_PURCHASE",
            conversion_window="SWIPE_28DAY_VIEW_1DAY",
        )
    assert blocked.value.detail["code"] == "snapchat_management_pixel_ineligible"
    assert len(provider.calls) == 2
    assert provider.calls[0] == (
        "GET",
        "/pixels/pixel-1/campaign_eligibilities",
        {
            "ad_account_id": "account-1",
            "optimization_goal": "PIXEL_PURCHASE",
            "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        },
    )
    assert "limit" not in provider.calls[0][2]


class _CreateExecutionProvider:
    def __init__(self, *, preflight_kind, fail_preflight=False):
        self.preflight_kind = preflight_kind
        self.fail_preflight = fail_preflight
        self.calls = []
        self.entity = None

    async def management_role(self, _account, _action):
        self.calls.append("role")
        return {"allowed": True, "role": "general"}

    async def validate_pixel_ad_squad_intent(self, **_kwargs):
        self.calls.append("pixel_preflight")
        if self.fail_preflight:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_pixel_ineligible"},
            )
        return {
            "verified": True,
            "pixel_id": "pixel-1",
            "account_id": "account-1",
            "optimization_goal": "PIXEL_PURCHASE",
            "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
            "eligibility_status": "ELIGIBLE",
            "warning": False,
            "membership_verified": True,
            "pixel_status": "ACTIVE",
            "pixel_effective_status": "ACTIVE",
            "membership_verified_at": datetime.now(timezone.utc).isoformat(),
        }

    async def validate_ad_create_dependencies(self, **_kwargs):
        self.calls.append("ad_dependency_preflight")
        if self.fail_preflight:
            raise HTTPException(
                status_code=409,
                detail={"code": "snapchat_management_parent_account_mismatch"},
            )
        return {
            "verified": True,
            "ad_squad_id": "squad-1",
            "campaign_id": "campaign-1",
            "creative_id": "creative-1",
            "creative_type": "WEB_VIEW",
            "requested_ad_type": "REMOTE_WEBPAGE",
        }

    async def execute(self, operation):
        self.calls.append("execute")
        entity = deepcopy(operation["body"][operation["plural"]][0])
        entity["id"] = (
            "created-squad" if operation["entity_type"] == "ad_squad" else "created-ad"
        )
        self.entity = entity
        return deepcopy(entity)

    async def read_entity(self, _entity_type, _entity_id):
        self.calls.append("readback")
        return deepcopy(self.entity)


@pytest.mark.asyncio
async def test_pixel_final_preflight_is_immediately_before_exact_paused_post(monkeypatch):
    _prepare_execution(monkeypatch)
    db = _DB()
    operation = management.build_snapchat_operation(_ad_squad_payload())
    proof = {
        "verified": True,
        "pixel_id": "pixel-1",
        "account_id": "account-1",
        "optimization_goal": "PIXEL_PURCHASE",
        "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        "eligibility_status": "ELIGIBLE",
        "membership_verified": True,
        "pixel_status": "ACTIVE",
        "pixel_effective_status": "ACTIVE",
    }
    row = _approved_v2_create_row("pixel-final-preflight", operation, pixel_proof=proof)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateExecutionProvider(preflight_kind="pixel")

    result = await management.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )

    assert result["status"] == "completed"
    assert provider.calls == ["role", "pixel_preflight", "execute", "readback"]
    posted = provider.entity
    assert posted["status"] == "PAUSED"
    assert posted["delivery_constraint"] == "DAILY_BUDGET"
    assert posted["pixel_id"] == "pixel-1"
    assert result["execution_pixel_eligibility"]["eligibility_status"] == "ELIGIBLE"
    assert result["execution_pixel_eligibility"]["membership_verified"] is True


@pytest.mark.asyncio
async def test_pixel_final_preflight_failure_restores_v2_without_post(monkeypatch):
    _prepare_execution(monkeypatch)
    db = _DB()
    operation = management.build_snapchat_operation(_ad_squad_payload())
    proof = {
        "verified": True,
        "pixel_id": "pixel-1",
        "account_id": "account-1",
        "optimization_goal": "PIXEL_PURCHASE",
        "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        "eligibility_status": "ELIGIBLE",
        "membership_verified": True,
        "pixel_status": "ACTIVE",
        "pixel_effective_status": "ACTIVE",
    }
    row = _approved_v2_create_row("pixel-final-blocked", operation, pixel_proof=proof)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateExecutionProvider(
        preflight_kind="pixel", fail_preflight=True
    )

    with pytest.raises(HTTPException):
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert provider.calls == ["role", "pixel_preflight"]
    assert stored["status"] == "approved_v2"
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_reached"] is False
    assert stored["provider_write_uncertain"] is False


@pytest.mark.asyncio
async def test_ad_create_dependency_helper_checks_full_chain_and_type():
    operation = _ad_create_operation()
    provider = _PagedProvider([])
    reads = []

    async def read(entity_type, entity_id):
        reads.append((entity_type, entity_id))
        return deepcopy({
            ("ad_squad", "squad-1"): {
                "id": "squad-1",
                "campaign_id": "campaign-1",
            },
            ("campaign", "campaign-1"): {
                "id": "campaign-1",
                "ad_account_id": "account-1",
            },
            ("creative", "creative-1"): {
                "id": "creative-1",
                "ad_account_id": "account-1",
                "type": "WEB_VIEW",
            },
        }[(entity_type, entity_id)])

    provider.read_entity = read
    proof = await provider.validate_ad_create_dependencies(
        account_id="account-1",
        parent_id="squad-1",
        operation=operation,
    )
    assert proof["verified"] is True
    assert reads == [
        ("ad_squad", "squad-1"),
        ("campaign", "campaign-1"),
        ("creative", "creative-1"),
    ]

    async def drifted_read(entity_type, entity_id):
        value = await read(entity_type, entity_id)
        if entity_type == "creative":
            value["type"] = "UNKNOWN_NEW_TYPE"
        return value

    provider.read_entity = drifted_read
    with pytest.raises(HTTPException) as blocked:
        await provider.validate_ad_create_dependencies(
            account_id="account-1", parent_id="squad-1", operation=operation
        )
    assert blocked.value.detail["code"] == (
        "snapchat_management_creative_ad_type_mismatch"
    )


@pytest.mark.asyncio
async def test_ad_create_final_dependency_recheck_orders_before_post(monkeypatch):
    _prepare_execution(monkeypatch)
    db = _DB()
    operation = _ad_create_operation()
    row = _approved_v2_create_row("ad-final-preflight", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateExecutionProvider(preflight_kind="ad")

    result = await management.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == "completed"
    assert provider.calls == [
        "role",
        "ad_dependency_preflight",
        "execute",
        "readback",
    ]
    assert result["execution_ad_dependency_verification"]["verified"] is True
    assert provider.entity["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_ad_create_final_dependency_drift_blocks_post_and_restores_v2(monkeypatch):
    _prepare_execution(monkeypatch)
    db = _DB()
    operation = _ad_create_operation()
    row = _approved_v2_create_row("ad-final-blocked", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateExecutionProvider(preflight_kind="ad", fail_preflight=True)

    with pytest.raises(HTTPException):
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert provider.calls == ["role", "ad_dependency_preflight"]
    assert stored["status"] == "approved_v2"
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_uncertain"] is False


def test_readback_uses_recursive_expected_dict_subset_and_exact_lists():
    expected = {
        "targeting": {
            "regulated_content": False,
            "geos": [{"country_code": "sa"}],
        }
    }
    actual = {
        "targeting": {
            "regulated_content": False,
            "geos": [{"country_code": "sa"}],
            "provider_default": True,
        }
    }
    assert management._changed_values_match(expected, actual) == (True, [])
    actual["targeting"]["geos"].append({"country_code": "us"})
    assert management._changed_values_match(expected, actual)[0] is False


@pytest.mark.asyncio
async def test_cancelled_preflight_returns_v2_to_approved_without_uncertainty(monkeypatch):
    db = _DB()
    row = {
        "proposal_id": "preflight-cancelled",
        "user_id": "owner-1",
        "status": "executing",
        "safety_protocol_version": 2,
        "provider_write_state": "preflighting",
        "provider_write_reached": False,
        "provider_write_uncertain": False,
    }
    db[management.PROPOSAL_COLLECTION].rows.append(row)
    await management._stabilize_cancelled_execution(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        proposal_id=row["proposal_id"],
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "approved_v2"
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_uncertain"] is False


@pytest.mark.asyncio
async def test_reconcile_releases_expired_no_write_lease_for_approved_v2():
    db = _DB()
    row = {
        "proposal_id": "approved-v2-expired-lease",
        "user_id": "owner-1",
        "status": "approved_v2",
        "safety_protocol_version": 2,
        "provider_write_state": "not_attempted",
        "provider_write_reached": False,
        "provider_write_uncertain": False,
    }
    lease = {
        "user_id": "owner-1",
        "proposal_id": row["proposal_id"],
        "active": True,
        "lease_token": "expired-no-write-token",
        "entity_id": "intent:approved-v2-expired-lease",
        "operation_kind": "execute",
        "expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    db[management.ENTITY_LEASE_COLLECTION].rows.append(deepcopy(lease))

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"]
    )
    assert result["status"] == "approved_v2"
    assert result["provider_write_state"] == "not_attempted"
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_reconcile_recovers_expired_preflighting_crash_without_uncertainty():
    db = _DB()
    row = {
        "proposal_id": "preflighting-v2-expired-lease",
        "user_id": "owner-1",
        "status": "executing",
        "safety_protocol_version": 2,
        "provider_write_state": "preflighting",
        "provider_write_reached": False,
        "provider_write_uncertain": False,
        "execution_started_at": datetime.now(timezone.utc).isoformat(),
    }
    lease = {
        "user_id": "owner-1",
        "proposal_id": row["proposal_id"],
        "active": True,
        "lease_token": "expired-preflight-token",
        "entity_id": "intent:preflighting-v2-expired-lease",
        "operation_kind": "execute",
        "expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    db[management.ENTITY_LEASE_COLLECTION].rows.append(deepcopy(lease))

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"]
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    stored_lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    assert result["status"] == "approved_v2"
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_reached"] is False
    assert stored["provider_write_uncertain"] is False
    assert stored["failure"]["code"] == (
        "snapchat_management_worker_lost_during_preflight"
    )
    assert stored_lease["active"] is False
    assert stored_lease["lease_token"] == "expired-preflight-token"


def test_explicit_validation_rejection_is_known_no_write_only_with_full_proof():
    proof = management._explicit_provider_validation_no_write_proof(
        {
            "request_status": "FAIL",
            "adsquads": [{"sub_request_status": "FAILURE"}],
        },
        http_status=200,
        expected_plural="adsquads",
    )
    assert proof["proof_kind"] == "explicit_whole_request_validation_rejection"
    assert management._explicit_provider_validation_no_write_proof(
        {"request_status": "FAIL"},
        http_status=200,
        expected_plural="adsquads",
    ) is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "request_status": "FAIL",
            "adsquads": [
                {"sub_request_status": "FAILURE"},
                {"sub_request_status": "FAILURE"},
            ],
        },
        {
            "request_status": "FAIL",
            "campaigns": [{"sub_request_status": "FAILURE"}],
        },
        {
            "request_status": "FAIL",
            "adsquads": [{"sub_request_status": "FAILURE"}],
            "ads": [{"sub_request_status": "FAILURE"}],
        },
        {
            "request_status": "FAIL",
            "adsquads": [
                {"sub_request_status": "FAILURE"},
                "malformed-second-wrapper",
            ],
        },
        {
            "request_status": "FAIL",
            "adsquads": [{
                "sub_request_status": "FAILURE",
                "ad": {},
            }],
        },
        {
            "request_status": "FAIL",
            "adsquads": [{
                "sub_request_status": "FAILURE",
                "adsquad": "malformed-entity",
            }],
        },
        {
            "request_status": "FAIL",
            "adsquads": [{"sub_request_status": "SUCCESS"}],
        },
    ],
)
def test_explicit_no_write_proof_is_bound_to_one_expected_failed_wrapper(payload):
    assert management._explicit_provider_validation_no_write_proof(
        payload,
        http_status=409,
        expected_plural="adsquads",
    ) is None
    for http_status in (400, 409, 422):
        assert management._explicit_provider_validation_no_write_proof(
            {"request_status": "ERROR"},
            http_status=http_status,
            expected_plural="adsquads",
        ) is None
    assert management._explicit_provider_validation_no_write_proof(
        {
            "request_status": "FAIL",
            "adsquads": [{
                "sub_request_status": "FAILURE",
                "adsquad": {"id": "maybe-created"},
            }],
        },
        http_status=200,
        expected_plural="adsquads",
    ) is None


@pytest.mark.asyncio
async def test_complete_verified_execution_requires_terminal_cas_before_side_effects(
    monkeypatch,
):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("terminal-cas-race")
    # The terminal writer expects an executing row. A concurrent transition
    # means the CAS must lose and no cache/audit/product/decision side effect
    # may occur.
    row["status"] = "failed"
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    lease = {
        "user_id": "owner-1",
        "proposal_id": row["proposal_id"],
        "lease_token": "terminal-race-fence",
        "active": True,
    }
    db[management.ENTITY_LEASE_COLLECTION].rows.append(deepcopy(lease))
    side_effects = []

    async def cache(*_args, **_kwargs):
        side_effects.append("cache")

    async def audit(*_args, **_kwargs):
        side_effects.append("audit")

    async def products(*_args, **_kwargs):
        side_effects.append("products")

    async def decision(*_args, **_kwargs):
        side_effects.append("decision")

    monkeypatch.setattr(
        management, "_refresh_verified_entity_cache_deferred_safe", cache
    )
    monkeypatch.setattr(management, "_audit", audit)
    monkeypatch.setattr(
        management, "_adopt_proposal_products_deferred_safe", products
    )
    monkeypatch.setattr(
        management, "_record_management_decision_deferred_safe", decision
    )

    with pytest.raises(HTTPException) as raised:
        await management._complete_verified_execution(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            proposal_id=row["proposal_id"],
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            provider_entity_id=candidate["id"],
            verified=deepcopy(candidate),
            event="execution_reconciled_create_by_exact_catalog_match",
        )
    assert raised.value.detail["code"] == "snapchat_management_terminal_state_race"
    assert side_effects == []
    assert db[management.PROPOSAL_COLLECTION].rows[0]["status"] == "failed"
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is True


@pytest.mark.asyncio
async def test_release_all_active_leases_for_one_proposal():
    db = _DB()
    leases = db[management.ENTITY_LEASE_COLLECTION]
    leases.rows.extend([
        {"user_id": "owner-1", "proposal_id": "p-1", "active": True, "lease_token": "a"},
        {"user_id": "owner-1", "proposal_id": "p-1", "active": True, "lease_token": "b"},
        {"user_id": "owner-1", "proposal_id": "p-2", "active": True, "lease_token": "c"},
    ])
    await management._release_proposal_entity_lease(
        db, user_id="owner-1", row={"proposal_id": "p-1"}
    )
    assert [row["active"] for row in leases.rows] == [False, False, True]


@pytest.mark.asyncio
async def test_create_reconciliation_atomically_upgrades_own_pending_lease():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("intent-upgrade")
    lease = {
        "user_id": "owner-1",
        "account_id": "account-1",
        "entity_type": "campaign",
        "entity_id": "pending:intent-upgrade",
        "proposal_id": row["proposal_id"],
        "operation_kind": "execute",
        "lease_token": "same-owned-token",
        "active": True,
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    db[management.ENTITY_LEASE_COLLECTION].rows.append(deepcopy(lease))

    updated = await management._ensure_create_intent_fence(
        db, user_id="owner-1", row=deepcopy(row), operation=operation
    )
    active = [
        item
        for item in db[management.ENTITY_LEASE_COLLECTION].rows
        if item.get("active")
    ]
    assert updated["intent_fingerprint"] == row["intent_fingerprint"]
    assert len(active) == 1
    assert active[0]["lease_token"] == "same-owned-token"
    assert active[0]["entity_id"] == f"intent:{row['intent_fingerprint']}"
    assert active[0]["operation_kind"] == "reconcile_create_intent"


@pytest.mark.asyncio
async def test_create_reconciliation_unique_exact_match_is_verified_then_adopted(
    monkeypatch,
):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("unique-exact")
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    reads = []

    async def no_cache_write(*_args, **_kwargs):
        return None

    async def no_decision_write(*_args, **_kwargs):
        return None

    monkeypatch.setattr(management, "_upsert_entity", no_cache_write)
    monkeypatch.setattr(
        management,
        "_record_management_decision_deferred_safe",
        no_decision_write,
    )

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate)]

        async def read_entity(self, entity_type, entity_id):
            reads.append((entity_type, entity_id))
            return deepcopy(candidate)

    result = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(row),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert result["status"] == "completed"
    assert result["provider_entity_id"] == candidate["id"]
    assert result["verification"]["verified"] is True
    assert reads == [("campaign", candidate["id"])]
    assert all(
        not lease.get("active")
        for lease in db[management.ENTITY_LEASE_COLLECTION].rows
    )


@pytest.mark.asyncio
async def test_out_of_window_heterogeneous_rows_do_not_block_unique_adoption(
    monkeypatch,
):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("old-row-plus-exact")
    old_row = {
        "id": "historical-different-campaign",
        "status": "PAUSED",
        "ad_account_id": "account-1",
        "created_at": (
            datetime.fromisoformat(row["execution_started_at"]) - timedelta(days=3)
        ).isoformat(),
        # Deliberately lacks this attempt's name, objective and budget shape.
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    async def no_cache_write(*_args, **_kwargs):
        return None

    async def no_decision_write(*_args, **_kwargs):
        return None

    monkeypatch.setattr(management, "_upsert_entity", no_cache_write)
    monkeypatch.setattr(
        management,
        "_record_management_decision_deferred_safe",
        no_decision_write,
    )

    class Provider:
        list_calls = 0

        async def list_create_reconciliation_candidates(self, _operation):
            self.list_calls += 1
            return [deepcopy(old_row), deepcopy(candidate)]

        async def read_entity(self, _entity_type, _entity_id):
            return deepcopy(candidate)

    provider = Provider()
    result = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(row),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=provider,
    )
    assert result["status"] == "completed"
    assert result["provider_entity_id"] == candidate["id"]
    assert provider.list_calls == 2


@pytest.mark.asyncio
async def test_out_of_window_heterogeneous_rows_can_still_yield_complete_zero_scan():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("old-row-zero")
    old_row = {
        "id": "historical-different-campaign",
        "status": "ACTIVE",
        "ad_account_id": "account-1",
        "created_at": (
            datetime.fromisoformat(row["execution_started_at"]) - timedelta(days=3)
        ).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(old_row)]

    result = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(row),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert result["provider_write_uncertain"] is True
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["create_reconciliation"]["complete_zero_scans"] == 1


@pytest.mark.asyncio
async def test_uncertain_creative_create_without_id_remains_manual_only(monkeypatch):
    db = _DB()
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="creative.create",
            account_id="account-1",
            payload={
                "name": "Creative without delivery status",
                "type": "WEB_VIEW",
                "headline": "تسوق الآن",
                "top_snap_media_id": "media-1",
                "profile_properties": {"profile_id": "profile-1"},
                "call_to_action": "SHOP_NOW",
                "web_view_properties": {"url": "https://example.com/product"},
            },
            reason="إثبات أن مصالحة Creative تبقى يدوية",
            idempotency_key="creative-manual-reconciliation-v2",
            safety_protocol_version=2,
        )
    )
    started = datetime.now(timezone.utc) - timedelta(minutes=2)
    row = {
        "proposal_id": "creative-manual-only",
        "user_id": "owner-1",
        "account_id": "account-1",
        "action": "creative.create",
        "status": "failed",
        "operation": operation,
        "safety_protocol_version": 2,
        "intent_fingerprint": management.snapchat_management_intent_fingerprint(
            operation
        ),
        "execution_started_at": started.isoformat(),
        "failed_at": (started + timedelta(seconds=10)).isoformat(),
        "provider_write_state": "unknown_needs_reconciliation",
        "provider_write_uncertain": True,
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    async def selected(*_args, **_kwargs):
        return {"ad_account_id": "account-1", "organization_id": "org-1"}

    monkeypatch.setattr(management, "_selected_account", selected)

    class Provider:
        list_calls = 0

        async def management_role(self, _account, _action):
            return {"allowed": True, "role": "creative"}

        async def list_create_reconciliation_candidates(self, _operation):
            self.list_calls += 1
            raise AssertionError("creative auto-reconciliation must stay disabled")

    provider = Provider()
    with pytest.raises(HTTPException) as raised:
        await management.reconcile_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            row["proposal_id"],
            provider=provider,
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_entity_unknown"
    )
    assert raised.value.detail["recovery_action"] == (
        "locate_provider_entity_id_manually"
    )
    assert provider.list_calls == 0
    assert db[management.PROPOSAL_COLLECTION].rows[0][
        "provider_write_uncertain"
    ] is True


@pytest.mark.asyncio
async def test_create_reconciliation_releases_only_observed_intent_lease(
    monkeypatch,
):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("exact-lease-release")
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    async def no_cache_write(*_args, **_kwargs):
        return None

    async def no_decision_write(*_args, **_kwargs):
        return None

    monkeypatch.setattr(management, "_upsert_entity", no_cache_write)
    monkeypatch.setattr(
        management,
        "_record_management_decision_deferred_safe",
        no_decision_write,
    )
    original_complete = management._complete_verified_execution

    async def complete_then_acquire_replacement(*args, **kwargs):
        result = await original_complete(*args, **kwargs)
        db[management.ENTITY_LEASE_COLLECTION].rows.append({
            "user_id": "owner-1",
            "proposal_id": row["proposal_id"],
            "entity_id": candidate["id"],
            "lease_token": "new-rollback-lease-token",
            "operation_kind": "rollback",
            "active": True,
        })
        return result

    monkeypatch.setattr(
        management,
        "_complete_verified_execution",
        complete_then_acquire_replacement,
    )

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate)]

        async def read_entity(self, _entity_type, _entity_id):
            return deepcopy(candidate)

    result = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(row),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert result["status"] == "completed"
    leases = db[management.ENTITY_LEASE_COLLECTION].rows
    intent = next(item for item in leases if item["entity_id"].startswith("intent:"))
    replacement = next(
        item for item in leases if item["lease_token"] == "new-rollback-lease-token"
    )
    assert intent["active"] is False
    assert replacement["active"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_field",
    [
        "missing_status",
        "invalid_status",
        "missing_created_at",
        "invalid_created_at",
        "missing_relationship",
        "missing_submitted_control",
    ],
)
async def test_malformed_create_candidate_never_counts_as_absence(malformed_field):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case(
        f"malformed-{malformed_field}"
    )
    if malformed_field == "missing_status":
        candidate.pop("status")
    elif malformed_field == "invalid_status":
        candidate["status"] = "UNKNOWN"
    elif malformed_field == "missing_created_at":
        candidate.pop("created_at")
    elif malformed_field == "invalid_created_at":
        candidate["created_at"] = "not-a-timestamp"
    elif malformed_field == "missing_relationship":
        candidate.pop("ad_account_id")
    elif malformed_field == "missing_submitted_control":
        candidate.pop("objective_v2_properties")
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate)]

    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=Provider(),
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_inconclusive"
        if malformed_field == "missing_submitted_control"
        else "snapchat_management_reconciliation_catalog_inconclusive"
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["provider_write_uncertain"] is True
    if malformed_field == "missing_submitted_control":
        assert stored["create_reconciliation"]["inconclusive"] is True
        assert stored["create_reconciliation"]["plausible_candidates"] == 1
    else:
        assert "create_reconciliation" not in stored
    assert stored["create_reconciliation_scan_active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift_kind",
    ["active_status", "control_changed", "deleted"],
)
async def test_same_scope_in_window_drift_is_inconclusive_not_zero(drift_kind):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case(
        f"plausible-{drift_kind}"
    )
    if drift_kind == "active_status":
        candidate["status"] = "ACTIVE"
    elif drift_kind == "control_changed":
        candidate["name"] = "Edited after uncertain create"
    elif drift_kind == "deleted":
        candidate["deleted"] = True
        candidate["status"] = "PAUSED"
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate)]

    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=Provider(),
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_inconclusive"
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["create_reconciliation"]["inconclusive"] is True
    assert stored["provider_write_uncertain"] is True
    assert "complete_zero_scans" not in stored["create_reconciliation"]


@pytest.mark.asyncio
async def test_prior_ambiguous_or_inconclusive_result_is_sticky_on_later_zero():
    for marker in (
        {"matching_candidates": 2},
        {"inconclusive": True, "reason": "same_scope_candidate_drifted"},
    ):
        db = _DB()
        operation, row, _candidate = _uncertain_campaign_case(
            f"sticky-{len(db.collections)}-{marker.get('matching_candidates', 0)}"
        )
        row["create_reconciliation"] = deepcopy(marker)
        db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

        class Provider:
            async def list_create_reconciliation_candidates(self, _operation):
                return []

        with pytest.raises(HTTPException) as raised:
            await management._reconcile_uncertain_create_without_id(
                db,
                user_id="owner-1",
                actor_id="owner-1",
                row=deepcopy(row),
                operation=operation,
                account={"ad_account_id": "account-1"},
                client=Provider(),
            )
        assert raised.value.detail["code"] == (
            "snapchat_management_reconciliation_inconclusive"
        )
        stored = db[management.PROPOSAL_COLLECTION].rows[0]
        assert stored["provider_write_uncertain"] is True
        assert stored["create_reconciliation"]["inconclusive"] is True
        assert "complete_zero_scans" not in stored["create_reconciliation"]


@pytest.mark.asyncio
async def test_invalid_attempt_window_is_manual_and_never_scanned_as_zero():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("invalid-window")
    row["failed_at"] = (
        datetime.fromisoformat(row["execution_started_at"]) - timedelta(seconds=1)
    ).isoformat()
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        calls = 0

        async def list_create_reconciliation_candidates(self, _operation):
            self.calls += 1
            return []

    provider = Provider()
    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=provider,
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_legacy_window_missing"
    )
    assert provider.calls == 0
    assert "create_reconciliation" not in db[management.PROPOSAL_COLLECTION].rows[0]


@pytest.mark.asyncio
async def test_unique_candidate_wrong_id_readback_is_never_adopted():
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("wrong-readback-id")
    wrong = {**deepcopy(candidate), "id": "different-provider-id"}
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate)]

        async def read_entity(self, _entity_type, _entity_id):
            return deepcopy(wrong)

    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=Provider(),
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_candidate_drift"
    )
    assert db[management.PROPOSAL_COLLECTION].rows[0]["status"] == "failed"
    assert db[management.PROPOSAL_COLLECTION].rows[0][
        "provider_write_uncertain"
    ] is True
    assert db[management.PROPOSAL_COLLECTION].rows[0]["create_reconciliation"][
        "inconclusive"
    ] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "confirmation_kind",
    ["second_exact", "control_drift", "deleted", "disappeared"],
)
async def test_unique_candidate_requires_second_complete_stable_unique_catalog(
    monkeypatch, confirmation_kind
):
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case(
        f"second-catalog-{confirmation_kind}"
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    second_exact = {**deepcopy(candidate), "id": "second-provider-id"}
    drifted = {**deepcopy(candidate), "name": "Changed after first catalog"}
    deleted = {**deepcopy(candidate), "deleted": True}
    confirmations = {
        "second_exact": [deepcopy(candidate), second_exact],
        "control_drift": [drifted],
        "deleted": [deleted],
        "disappeared": [],
    }
    completed = []

    async def must_not_complete(*_args, **_kwargs):
        completed.append(True)
        raise AssertionError("unstable second catalog must never be adopted")

    monkeypatch.setattr(management, "_complete_verified_execution", must_not_complete)

    class Provider:
        list_calls = 0

        async def list_create_reconciliation_candidates(self, _operation):
            self.list_calls += 1
            if self.list_calls == 1:
                return [deepcopy(candidate)]
            return deepcopy(confirmations[confirmation_kind])

        async def read_entity(self, _entity_type, _entity_id):
            return deepcopy(candidate)

    provider = Provider()
    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=provider,
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_confirmation_failed"
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "failed"
    assert stored["provider_write_uncertain"] is True
    assert stored["create_reconciliation"]["inconclusive"] is True
    assert stored["create_reconciliation"]["reason"] == (
        "exact_candidate_confirmation_changed"
    )
    assert completed == []
    assert provider.list_calls == 2


@pytest.mark.asyncio
async def test_create_reconciliation_multiple_exact_matches_blocks_without_readback():
    db = _DB()
    operation, row, candidate = _uncertain_campaign_case("ambiguous-exact")
    second = {**deepcopy(candidate), "id": "second-exact-provider-id"}
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        read_calls = 0

        async def list_create_reconciliation_candidates(self, _operation):
            return [deepcopy(candidate), deepcopy(second)]

        async def read_entity(self, _entity_type, _entity_id):
            self.read_calls += 1
            return deepcopy(candidate)

    provider = Provider()
    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=provider,
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_ambiguous"
    )
    assert provider.read_calls == 0
    assert db[management.PROPOSAL_COLLECTION].rows[0][
        "provider_write_uncertain"
    ] is True


@pytest.mark.asyncio
async def test_create_reconciliation_partial_catalog_proves_neither_adoption_nor_absence():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("partial-catalog")
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            raise HTTPException(
                status_code=502,
                detail={"code": "snapchat_management_catalog_incomplete"},
            )

    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_management_catalog_incomplete"
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["provider_write_uncertain"] is True
    assert "create_reconciliation" not in stored


@pytest.mark.asyncio
async def test_terminal_zero_scan_losing_cas_never_releases_intent_fence():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("zero-cas-race")
    row["create_reconciliation"] = {
        "complete_scan": True,
        "matching_candidates": 0,
        "complete_zero_scans": 1,
        "first_zero_scan_at": (
            datetime.now(timezone.utc)
            - management.CREATE_RECONCILIATION_GRACE
            - timedelta(seconds=1)
        ).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    proposals = db[management.PROPOSAL_COLLECTION]
    original_update = proposals.update_one

    async def race_terminal_zero(query, update, upsert=False):
        values = update.get("$set") or {}
        if values.get("provider_write_state") == "confirmed_not_applied":
            proposals.rows[0]["status"] = "executing"
            proposals.rows[0]["provider_write_uncertain"] = False
            return _Result(0)
        return await original_update(query, update, upsert=upsert)

    proposals.update_one = race_terminal_zero

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return []

    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_management_reconciliation_race"
    intent_leases = db[management.ENTITY_LEASE_COLLECTION].rows
    assert len(intent_leases) == 1
    assert intent_leases[0]["active"] is True


@pytest.mark.asyncio
async def test_parallel_create_reconciliation_scan_is_blocked_before_provider_read():
    db = _DB()
    operation, row, _candidate = _uncertain_campaign_case("scan-busy")
    row.update(
        {
            "create_reconciliation_scan_active": True,
            "create_reconciliation_scan_token": "other-scan-token",
            "create_reconciliation_scan_expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))

    class Provider:
        calls = 0

        async def list_create_reconciliation_candidates(self, _operation):
            self.calls += 1
            return []

    provider = Provider()
    with pytest.raises(HTTPException) as raised:
        await management._reconcile_uncertain_create_without_id(
            db,
            user_id="owner-1",
            actor_id="owner-1",
            row=deepcopy(row),
            operation=operation,
            account={"ad_account_id": "account-1"},
            client=provider,
        )
    assert raised.value.detail["code"] == (
        "snapchat_management_reconciliation_scan_busy"
    )
    assert provider.calls == 0


@pytest.mark.asyncio
async def test_zero_candidate_reconciliation_needs_two_scans_separated_by_full_grace(monkeypatch):
    db = _DB()
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.create",
            account_id="account-1",
            payload={
                "name": "Uncertain create",
                "start_time": "2026-08-13T00:00:00Z",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
                "daily_budget_micro": 50_000_000,
            },
            reason="مصالحة محاولة إنشاء غير محسومة",
            idempotency_key="uncertain-create-v2",
            safety_protocol_version=2,
        )
    )
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    row = {
        "proposal_id": "p-create-zero",
        "user_id": "owner-1",
        "account_id": "account-1",
        "action": "campaign.create",
        "status": "failed",
        "operation": operation,
        "safety_protocol_version": 2,
        "intent_fingerprint": management.snapchat_management_intent_fingerprint(operation),
        "execution_started_at": base.isoformat(),
        "failed_at": (base + timedelta(minutes=1)).isoformat(),
        "provider_write_state": "unknown_needs_reconciliation",
        "provider_write_uncertain": True,
    }
    db[management.PROPOSAL_COLLECTION].rows.append(row)

    class Provider:
        async def list_create_reconciliation_candidates(self, _operation):
            return []

    first = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(row),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert first["provider_write_uncertain"] is True
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["create_reconciliation"]["complete_zero_scans"] == 1

    immediate = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(stored),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert immediate["provider_write_uncertain"] is True
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    stored["create_reconciliation"]["first_zero_scan_at"] = (
        datetime.now(timezone.utc) - management.CREATE_RECONCILIATION_GRACE
        - timedelta(seconds=1)
    ).isoformat()
    final = await management._reconcile_uncertain_create_without_id(
        db,
        user_id="owner-1",
        actor_id="owner-1",
        row=deepcopy(stored),
        operation=operation,
        account={"ad_account_id": "account-1"},
        client=Provider(),
    )
    assert final["provider_write_state"] == "confirmed_not_applied"
    assert final["provider_write_uncertain"] is False
