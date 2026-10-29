"""Synthetic credential session races; no secrets, environment or provider calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import traceback

import httpx
import pytest

from integrations_control_center import google_ads_reporting as module
from integrations_control_center import google_ads_client as ads_module
from integrations_control_center.google_merchant_product_observation import MerchantObservationScope, read_merchant_product_observation

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)
USER = "synthetic-owner"
ACCOUNT = {"ad_account_id": "5052346701", "external_account_id": "5052346701"}
TOKEN = "synthetic-current-token"
REAL_HTTPX_ASYNC_CLIENT = httpx.AsyncClient


def credential():
    return {"user_id": USER, "access_token_ciphertext": "synthetic-ciphertext",
        "refresh_token_ciphertext": "synthetic-refresh", "google_subject": "synthetic-subject",
        "google_email": "owner@example.invalid", "scope": ["https://www.googleapis.com/auth/adwords", "https://www.googleapis.com/auth/content"],
        "expires_at": NOW + timedelta(hours=1), "updated_at": NOW, "token_type": "Bearer"}


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def limit(self, count):
        self.rows = self.rows[:count]
        return self
    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, rows):
        self.rows, self.options, self.fail = deepcopy(rows), [], False
    def with_options(self, **options):
        self.options.append(options)
        return self
    def find(self, query, projection):
        if self.fail:
            raise RuntimeError("PRIVATE_DRIVER_SENTINEL")
        rows = [{k: v for k, v in row.items() if projection.get(k) == 1}
            for row in self.rows if row.get("user_id") == query["user_id"]]
        return Cursor(rows)


class DB:
    def __init__(self, rows=None):
        self.collection = Collection([credential()] if rows is None else rows)
    def __getitem__(self, name):
        assert name == module.GOOGLE_CREDENTIALS_COLLECTION
        return self.collection


def providers(monkeypatch, db, *, credential_effect=None, ads_effect=None, merchant_effect=None, token=TOKEN):
    calls = {"credential": 0, "ads_tokens": [], "merchant_tokens": [], "ads_closed": 0}
    async def fresh(*args):
        calls["credential"] += 1
        if credential_effect:
            credential_effect()
        return token
    monkeypatch.setattr(module, "_credential", fresh)
    monkeypatch.setattr(module, "decrypt_google_token", lambda value: TOKEN if value == "synthetic-ciphertext" else "different-token")
    class Ads:
        def __init__(self, bound_token, account):
            calls["ads_tokens"].append(bound_token)
        async def account_metadata(self):
            if ads_effect:
                ads_effect()
            return {"account_id": "5052346701"}
        async def search(self, query, max_rows=2000):
            if ads_effect:
                ads_effect()
            return [{"productLink": {"resourceName": "customers/5052346701/productLinks/12"}}], True
        async def close(self):
            calls["ads_closed"] += 1
    monkeypatch.setattr(ads_module, "GoogleAdsClient", Ads)
    def merchant_response(request):
        calls["merchant_tokens"].append(request.headers["Authorization"])
        if merchant_effect:
            merchant_effect()
        return httpx.Response(200, json={}, headers={"X-Private": "PRIVATE_PROVIDER_SENTINEL"})
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: REAL_HTTPX_ASYNC_CLIENT(transport=httpx.MockTransport(merchant_response), **kwargs))
    return calls


def session(db, **kwargs):
    return module.google_bound_read_session(db, USER, ads_account=ACCOUNT, merchant_id="123", clock=lambda: NOW, **kwargs)


@pytest.mark.asyncio
async def test_same_bound_token_reads_only_with_majority_primary_and_redacted_surface(monkeypatch):
    db = DB(); calls = providers(monkeypatch, db)
    async with session(db) as bound:
        assert bound.evidence_id.startswith("google-bound-read-")
        assert repr(bound) == "<GoogleBoundReadSession>"
        assert not hasattr(bound, "headers") and not hasattr(bound, "mutate") and not hasattr(bound, "__dict__")
        assert (await bound.account_metadata())["account_id"] == "5052346701"
        assert (await bound.search("SELECT product_link.resource_name FROM product_link"))[1] is True
        result = await read_merchant_product_observation(bound, scope=MerchantObservationScope(USER, "5052346701", "123"), clock=lambda: NOW)
        assert result.as_public_dict()["execution_authorized"] is False
    assert calls["credential"] == 1
    assert calls["ads_tokens"] == [TOKEN] and calls["merchant_tokens"] == ["Bearer " + TOKEN]
    assert calls["ads_closed"] == 1
    assert db.collection.options[0]["read_concern"].document == {"level": "majority"}
    assert db.collection.options[0]["read_preference"].name == "Primary"
    with pytest.raises(module.GoogleBoundReadError):
        await bound.check_current()


@pytest.mark.asyncio
async def test_old_returned_token_cannot_be_bound_to_new_relink_snapshot(monkeypatch):
    db = DB()
    calls = providers(monkeypatch, db, credential_effect=lambda: db.collection.rows[0].update(access_token_ciphertext="relinked-ciphertext"))
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(db):
            pytest.fail("relinked context escaped")
    assert calls["ads_tokens"] == calls["merchant_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("access_token_ciphertext", "changed"), ("refresh_token_ciphertext", "changed"),
    ("google_subject", "changed"), ("google_email", "changed@example.invalid"),
    ("scope", ["https://www.googleapis.com/auth/adwords"]), ("expires_at", NOW),
    ("updated_at", NOW + timedelta(seconds=1)), ("grant_generation", None),
    ("token_revision", 1), ("last_refresh_operation_id", "other"), ("token_type", "other"),
])
async def test_any_bound_credential_change_discards_read_before_result_escape(monkeypatch, field, value):
    db = DB(); providers(monkeypatch, db, ads_effect=lambda: db.collection.rows[0].update({field: value}))
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(db) as bound:
            await bound.search("SELECT product_link.resource_name FROM product_link")


@pytest.mark.asyncio
async def test_absent_null_and_bson_scalar_types_are_not_equivalent(monkeypatch):
    db = DB(); db.collection.rows[0]["grant_generation"] = None
    providers(monkeypatch, db)
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(db):
            del db.collection.rows[0]["grant_generation"]
    db = DB(); db.collection.rows[0]["token_revision"] = 1
    providers(monkeypatch, db)
    with pytest.raises(module.GoogleBoundReadError, match="credential_changed"):
        async with session(db):
            db.collection.rows[0]["token_revision"] = True


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [[], [credential(), credential()]])
async def test_missing_or_duplicate_credentials_never_choose_first(monkeypatch, rows):
    db = DB(rows); calls = providers(monkeypatch, db)
    with pytest.raises(module.GoogleBoundReadError):
        async with session(db):
            pytest.fail("ambiguous credential escaped")
    assert calls["ads_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["delete", "duplicate", "read_failure"])
async def test_context_exit_rechecks_even_without_any_provider_call(monkeypatch, change):
    db = DB(); calls = providers(monkeypatch, db)
    with pytest.raises(module.GoogleBoundReadError) as caught:
        async with session(db):
            if change == "delete":
                db.collection.rows.clear()
            elif change == "duplicate":
                db.collection.rows.append(credential())
            else:
                db.collection.fail = True
    assert calls["merchant_tokens"] == [] and calls["ads_closed"] == 1
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"scope": []}, {"scope": "https://www.googleapis.com/auth/content"},
    {"google_subject": ""}, {"token_type": "Other"}, {"expires_at": NOW + timedelta(seconds=119)},
])
async def test_missing_actual_scope_subject_or_useful_expiry_is_unproved(monkeypatch, patch):
    row = credential(); row.update(patch); db = DB([row]); calls = providers(monkeypatch, db)
    with pytest.raises(module.GoogleBoundReadError):
        async with session(db):
            pytest.fail("incomplete capability escaped")
    assert calls["ads_tokens"] == []


@pytest.mark.asyncio
async def test_unknown_refresh_ack_never_constructs_provider_clients(monkeypatch):
    db = DB(); calls = providers(monkeypatch, db)
    async def unconfirmed(*args):
        raise RuntimeError("PRIVATE_ACK_SENTINEL")
    monkeypatch.setattr(module, "_credential", unconfirmed)
    with pytest.raises(module.GoogleBoundReadError) as caught:
        async with session(db):
            pytest.fail("unconfirmed refresh escaped")
    assert calls["ads_tokens"] == []
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.asyncio
async def test_merchant_stream_is_scope_checked_and_never_exposes_raw_request_or_headers(monkeypatch):
    db = DB(); providers(monkeypatch, db)
    async with session(db) as bound:
        async with bound.stream("GET", "https://merchantapi.googleapis.com/products/v1/accounts/123/products", params={"pageSize": 250}, headers={"Accept-Encoding": "identity"}, follow_redirects=False) as response:
            assert response.headers == {"content-type": "application/json"}
            assert not hasattr(response, "request") and not hasattr(response, "__dict__")
            assert repr(response) == "<GoogleBoundReadResponse>"


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"method": "POST"}, {"url": "https://other.example/products"},
    {"url": "https://merchantapi.googleapis.com/products/v1/accounts/999/products"},
    {"follow_redirects": True}, {"headers": {"Authorization": "injected"}},
    {"params": {"pageSize": True}}, {"params": {"pageSize": 1001}}, {"params": {"pageSize": 250, "other": "x"}},
])
async def test_session_does_not_turn_into_arbitrary_provider_transport(monkeypatch, patch):
    db = DB(); calls = providers(monkeypatch, db)
    args = {"method": "GET", "url": "https://merchantapi.googleapis.com/products/v1/accounts/123/products", "params": {"pageSize": 250}, "headers": {"Accept-Encoding": "identity"}, "follow_redirects": False}
    args.update(patch)
    async with session(db) as bound:
        with pytest.raises(module.GoogleBoundReadError, match="request_refused"):
            async with bound.stream(**args):
                pytest.fail("forbidden transport escaped")
    assert calls["merchant_tokens"] == []


@pytest.mark.asyncio
async def test_provider_change_during_merchant_read_discards_observation(monkeypatch):
    db = DB(); providers(monkeypatch, db, merchant_effect=lambda: db.collection.rows[0].update(google_subject="relinked"))
    with pytest.raises(Exception) as caught:
        async with session(db) as bound:
            await read_merchant_product_observation(bound, scope=MerchantObservationScope(USER, "5052346701", "123"), clock=lambda: NOW)
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))
