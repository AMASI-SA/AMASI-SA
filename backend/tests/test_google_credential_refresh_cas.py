"""Actual Ads/GA4/Merchant helpers under deterministic synthetic relink races.

Only the provider transport and storage boundary are fake. The assertions cover
observable token return, persisted credentials and sanitized failure contracts.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from integrations_control_center import google_ads_reporting as ads
from integrations_control_center import google_analytics_realtime_routes as ga4
from integrations_control_center import google_merchant_registration as merchant

NOW = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
USER = "synthetic-owner"
SOURCES = ("ads", "ga4", "merchant")
MODULES = {"ads": ads, "ga4": ga4, "merchant": merchant}


def credential():
    return {"user_id": USER, "access_token_ciphertext": "old-access",
            "refresh_token_ciphertext": "old-refresh", "expires_at": NOW-timedelta(hours=1),
            "updated_at": NOW-timedelta(hours=2), "google_subject": "synthetic-subject",
            "google_email": "synthetic@example.invalid", "token_type": "Bearer",
            "scope": [
                ga4.GOOGLE_SCOPE_BY_PROVIDER["google_ads"], ga4.GOOGLE_SCOPE_BY_PROVIDER[ga4.GA4_PROVIDER_ID],
                merchant.GOOGLE_SCOPE_BY_PROVIDER[merchant.MERCHANT_PROVIDER]]}


def matches(row, query):
    if row is None:
        return False
    for key, expected in query.items():
        if isinstance(expected, dict):
            if "$exists" in expected and (key in row) != expected["$exists"]:
                return False
            if "$eq" in expected and row.get(key) != expected["$eq"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class Collection:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.reads = self.writes = 0
        self.options = []
        self.fault = None
        self.before_write = self.after_write = None

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    async def find_one(self, query, projection=None):
        self.reads += 1
        if self.reads > 1 and self.fault == "readback_failure":
            raise ConnectionError("private-db-material")
        if not matches(self.row, query):
            return None
        if projection and any(v == 1 for v in projection.values()):
            return {k: deepcopy(v) for k, v in self.row.items() if projection.get(k) == 1}
        return deepcopy(self.row)

    async def update_one(self, query, update, **kwargs):
        self.writes += 1
        assert kwargs.get("upsert", False) is False
        if self.before_write:
            self.before_write(self)
        matched = matches(self.row, query)
        if matched:
            self.row.update(deepcopy(update["$set"]))
        if self.after_write:
            self.after_write(self)
        if self.fault == "lost_ack":
            raise ConnectionError("private-db-material")
        return SimpleNamespace(acknowledged=self.fault != "unacknowledged", matched_count=int(matched))


class DB:
    def __init__(self, row):
        self.credentials = Collection(row)
    def __getitem__(self, name):
        assert name == ads.GOOGLE_CREDENTIALS_COLLECTION
        return self.credentials


async def call_refresh(source, monkeypatch, db, during_send=None, token="new-access", calls=None):
    module = MODULES[source]
    calls = [] if calls is None else calls
    class Response:
        status_code = 200
        def json(self):
            return {"access_token": token, "expires_in": 3600, "token_type": "Bearer"}
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            calls.append("refresh")
            if during_send:
                await during_send()
            return Response()
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(module, "decrypt_google_token", lambda value: value or "")
    monkeypatch.setattr(module, "encrypt_google_token", lambda value: value)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "synthetic")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "synthetic")
    monkeypatch.setattr(module, "_utcnow", lambda: NOW)
    if source == "ads":
        result = await ads._credential(db, USER, NOW)
    elif source == "ga4":
        result = await ga4._fresh_google_access_token(db, USER)
    else:
        monkeypatch.setattr(merchant, "_approved_developer_email", lambda: "synthetic@example.invalid")
        result = (await merchant._fresh_google_context(db, USER))["access_token"]
    return result, calls


def error_code(error):
    detail = getattr(error, "detail", None)
    return detail.get("code") if isinstance(detail, dict) else getattr(error, "code", None)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_actual_refresh_helper_returns_only_committed_readback(monkeypatch, source):
    db = DB(credential())
    before = deepcopy(db.credentials.row)
    token, calls = await call_refresh(source, monkeypatch, db)
    assert token == "new-access" and calls == ["refresh"]
    assert db.credentials.row["access_token_ciphertext"] == token
    for key in ("refresh_token_ciphertext", "scope", "google_subject", "google_email"):
        assert db.credentials.row[key] == before[key]
    assert db.credentials.writes == 1 and db.credentials.reads >= 2
    assert any(options.get("write_concern").document == {"w": "majority", "j": True}
               for options in db.credentials.options if options.get("write_concern"))
    assert any(options.get("read_concern").document == {"level": "majority"}
               for options in db.credentials.options if options.get("read_concern"))


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("change", ["access_token_ciphertext", "refresh_token_ciphertext", "scope", "google_subject",
                                    "updated_at", "grant_generation", "token_revision"])
async def test_actual_helpers_cannot_overwrite_relink_or_changed_scope(monkeypatch, source, change):
    db = DB(credential())
    async def relink():
        db.credentials.row[change] = (["new-scope"] if change == "scope" else
                                      NOW if change == "updated_at" else 1 if change == "token_revision" else "relinked")
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, db, relink)
    assert error_code(error.value) == "google_oauth_refresh_conflict"
    assert db.credentials.row["access_token_ciphertext"] == ("relinked" if change == "access_token_ciphertext" else "old-access")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("direction", ["missing_to_null", "null_to_missing"])
async def test_actual_helpers_distinguish_missing_generation_from_null(monkeypatch, source, direction):
    row = credential()
    if direction == "null_to_missing":
        row["grant_generation"] = None
    db = DB(row)
    async def relink():
        if direction == "missing_to_null":
            db.credentials.row["grant_generation"] = None
        else:
            del db.credentials.row["grant_generation"]
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, db, relink)
    assert error_code(error.value) == "google_oauth_refresh_conflict"
    assert db.credentials.row["access_token_ciphertext"] == "old-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("fault", ["lost_ack", "unacknowledged", "readback_failure"])
async def test_actual_helpers_never_return_token_after_uncertain_storage(monkeypatch, source, fault):
    db = DB(credential())
    db.credentials.fault = fault
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, db)
    assert error_code(error.value) == "google_oauth_refresh_unconfirmed"
    assert "private-db-material" not in str(error.value)
    assert db.credentials.writes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_relink_after_commit_before_verification_does_not_return_old_refresh(monkeypatch, source):
    db = DB(credential())
    db.credentials.after_write = lambda collection: collection.row.update(access_token_ciphertext="relinked-access")
    with pytest.raises(Exception) as error:
        await call_refresh(source, monkeypatch, db)
    assert error_code(error.value) == "google_oauth_refresh_conflict"
    assert db.credentials.row["access_token_ciphertext"] == "relinked-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", SOURCES)
async def test_fresh_access_path_preserves_existing_service_contract(monkeypatch, source):
    row = credential()
    row["expires_at"] = NOW+timedelta(hours=1)
    db = DB(row)
    token, calls = await call_refresh(source, monkeypatch, db)
    assert token == "old-access" and calls == []
    assert db.credentials.writes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["google_ads_needs_reauth", "google_oauth_refresh_conflict", "google_oauth_refresh_unconfirmed"])
async def test_google_scheduler_records_refresh_failure_without_revoking_new_connection(monkeypatch, code):
    from integrations_control_center import ads_auto_sync_scheduler as scheduler
    observed = []
    async def no_active(*args, **kwargs):
        return None
    async def start(*args, **kwargs):
        return "synthetic-run"
    async def failed_refresh(*args, **kwargs):
        raise ads.GoogleAdsReportingError(code, "Safe refresh diagnostic.", status_code=409)
    async def record(*args, **kwargs):
        observed.append(("error", kwargs))
        return "synthetic-error"
    async def finish(*args, **kwargs):
        observed.append(("finished", kwargs))
    async def forbidden_status_write(*args, **kwargs):
        raise AssertionError("An old refresh failure cannot revoke a newer relink")
    monkeypatch.setattr(scheduler, "google_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "google_ads_reporting_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_active_run", no_active)
    monkeypatch.setattr(scheduler, "_start_run", start)
    monkeypatch.setattr(scheduler, "run_google_ads_reporting_sync", failed_refresh)
    monkeypatch.setattr(scheduler, "_record_error", record)
    monkeypatch.setattr(scheduler, "_finish_run", finish)
    monkeypatch.setattr(scheduler, "_mark_needs_reauth", forbidden_status_write)
    result = await scheduler._refresh_google(object(), user_id=USER,
        start_date=NOW.date(), end_date=NOW.date(), now=NOW)
    assert result["status"] == "failed" and result["code"] == code
    assert [item[0] for item in observed] == ["error", "finished"]
    assert observed[0][1]["code"] == observed[1][1]["error"]["code"] == code
