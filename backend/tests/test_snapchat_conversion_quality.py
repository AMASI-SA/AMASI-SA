"""Snapchat conversion quality must distinguish unknown from true zero."""
from __future__ import annotations

import asyncio
import sys
import types

import httpx

from ads_v2.sync import adapters, core

# The pure quality helpers do not use JWT.  Keep this unit test runnable in
# the repository's lightweight review environment where PyJWT is optional.
try:
    import jwt as _jwt  # noqa: F401
except ModuleNotFoundError:
    sys.modules["jwt"] = types.ModuleType("jwt")

import snapchat_routes
from snapchat_routes import (
    _aggregate_snap_conversion_rows,
    _build_router,
    _fetch_snap_conversion_metrics,
    _parse_snap_conversion_payload,
)


def _timeseries(purchases, revenue_micro):
    return {
        "timeseries_stats": [{
            "timeseries_stat": {
                "timeseries": [{
                    "stats": {
                        "conversion_purchases": purchases,
                        "conversion_purchases_value": revenue_micro,
                    },
                }],
            },
        }],
    }


def test_explicit_provider_zero_is_available_not_unknown():
    parsed = _parse_snap_conversion_payload(_timeseries(0, 0))

    assert parsed == {
        "purchases": 0,
        "revenue_value_micro": 0,
        "conversion_data_status": "available",
        "conversion_data_error": None,
    }


def test_missing_or_malformed_conversion_payload_fails_closed():
    empty = _parse_snap_conversion_payload({"timeseries_stats": []})
    malformed = _parse_snap_conversion_payload({
        "timeseries_stats": [{
            "timeseries_stat": {
                "timeseries": [{
                    "stats": {
                        "conversion_purchases": "not-a-number",
                        "conversion_purchases_value": None,
                    },
                }],
            },
        }],
    })

    for parsed in (empty, malformed):
        assert parsed["purchases"] is None
        assert parsed["revenue_value_micro"] is None
        assert parsed["conversion_data_status"] == "unavailable"
        assert parsed["conversion_data_error"]


def test_partial_payload_keeps_only_the_proven_metric():
    parsed = _parse_snap_conversion_payload({
        "timeseries_stats": [{
            "timeseries_stat": {
                "timeseries": [{
                    "stats": {"conversion_purchases": "3"},
                }],
            },
        }],
    })

    assert parsed["purchases"] == 3
    assert parsed["revenue_value_micro"] is None
    assert parsed["conversion_data_status"] == "partial"


def test_http_failure_is_unknown_and_does_not_raise():
    class _Response:
        status_code = 403

    class _Client:
        async def get(self, *args, **kwargs):
            return _Response()

    parsed = asyncio.run(_fetch_snap_conversion_metrics(
        _Client(), "https://example.invalid", {}, {},
    ))

    assert parsed["purchases"] is None
    assert parsed["revenue_value_micro"] is None
    assert parsed["conversion_data_status"] == "unavailable"
    assert parsed["conversion_data_error"] == "http_403"


def test_network_failure_is_unknown_and_does_not_raise():
    class _Client:
        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("offline")

    parsed = asyncio.run(_fetch_snap_conversion_metrics(
        _Client(), "https://example.invalid", {}, {},
    ))

    assert parsed["purchases"] is None
    assert parsed["revenue_value_micro"] is None
    assert parsed["conversion_data_status"] == "unavailable"
    assert parsed["conversion_data_error"] == "network_error:ConnectError"


def test_aggregate_propagates_unknown_instead_of_summing_it_as_zero():
    result = _aggregate_snap_conversion_rows([
        {
            "purchases": 2,
            "revenue_sar": 100.0,
            "conversion_data_status": "available",
        },
        {
            "purchases": None,
            "revenue_sar": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "http_403",
        },
    ])

    assert result["purchases"] is None
    assert result["revenue"] is None
    assert result["conversion_data_status"] == "unavailable"
    assert result["conversion_accounts_complete"] == 1
    assert "http_403" in result["conversion_data_error"]


def test_aggregate_accepts_explicit_zero_but_not_ambiguous_legacy_zero():
    explicit = _aggregate_snap_conversion_rows([
        {
            "purchases": 0,
            "revenue_sar": 0.0,
            "conversion_data_status": "available",
        },
        {
            "purchases": 2,
            "revenue_sar": 100.0,
            "conversion_data_status": "available",
        },
    ])
    legacy = _aggregate_snap_conversion_rows([
        {"purchases": 0, "revenue_sar": 0.0},
    ])

    assert explicit["purchases"] == 2
    assert explicit["revenue"] == 100.0
    assert explicit["conversion_data_status"] == "available"
    assert legacy["purchases"] is None
    assert legacy["revenue"] is None
    assert legacy["conversion_data_status"] == "unavailable"


def test_ads_v2_snap_adapter_marks_spend_only_conversions_unknown(monkeypatch):
    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {
                "total_stats": [{
                    "total_stat": {"stats": {"spend": 5_000_000}},
                }],
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(adapters.httpx, "AsyncClient", _Client)
    row, status = asyncio.run(adapters.fetch_snapchat_day(
        "token", "account", "2026-07-01",
    ))

    assert status["code"] == "ok"
    assert row["spend_native"] == 5.0
    assert row["purchases"] is None
    assert row["conversion_data_status"] == "unavailable"
    assert row["conversion_data_error"] == "account_level_spend_only"


def test_ads_v2_empty_snap_day_does_not_claim_zero_conversions(monkeypatch):
    class _Response:
        status_code = 200
        text = ""

        def json(self):
            return {"total_stats": []}

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return _Response()

    monkeypatch.setattr(adapters.httpx, "AsyncClient", _Client)
    row, status = asyncio.run(adapters.fetch_snapchat_day(
        "token", "account", "2026-07-01",
    ))

    assert status["code"] == "empty"
    assert row["spend_native"] == 0.0
    assert row["purchases"] is None
    assert row["conversion_data_status"] == "unavailable"


class _Collection:
    def __init__(self, docs=None):
        self.docs = [dict(doc) for doc in (docs or [])]

    @staticmethod
    def _matches(doc, query):
        return all(doc.get(key) == value for key, value in query.items())

    async def find_one(self, query, projection=None, **kwargs):
        return next(
            (dict(doc) for doc in self.docs if self._matches(doc, query)),
            None,
        )

    def find(self, query, projection=None):
        rows = [
            dict(doc)
            for doc in self.docs
            if self._matches(doc, query)
        ]

        class _Cursor:
            def __init__(self, values):
                self.values = values

            def sort(self, *args, **kwargs):
                return self

            async def to_list(self, length):
                return self.values[:length]

        return _Cursor(rows)

    async def update_one(self, query, update, upsert=False):
        doc = next(
            (item for item in self.docs if self._matches(item, query)),
            None,
        )
        if doc is None:
            if not upsert:
                return
            doc = dict(query)
            doc.update(update.get("$setOnInsert") or {})
            self.docs.append(doc)
        doc.update(update.get("$set") or {})
        for key, value in (update.get("$inc") or {}).items():
            doc[key] = doc.get(key, 0) + value

    async def insert_one(self, doc):
        self.docs.append(dict(doc))


class _DB:
    def __init__(self):
        self.ads_accounts = _Collection([{
            "id": "snap-account",
            "user_id": "user-1",
            "provider": "snapchat",
            "external_account_id": "external-1",
            "soft_deleted": False,
            "sync_enabled": True,
            "timezone": "Asia/Riyadh",
            "currency_native": "USD",
            "fx_to_sar": {"rate": 3.75, "mode": "manual"},
            "bank_fee": {"enabled": False},
            "review_settings": {},
        }])
        self.ads_daily = _Collection()
        self.ads_sync_logs = _Collection()


def test_ads_v2_core_persists_unknown_without_coercing_to_zero(monkeypatch):
    async def _token(db, account):
        return "token", {"code": "ok"}

    async def _fetch(**kwargs):
        return {
            "spend_native": 5.0,
            "currency_native": "USD",
            "impressions": 0,
            "clicks": 0,
            "purchases": None,
            "conversion_data_status": "unavailable",
            "conversion_data_error": "account_level_spend_only",
            "raw_excerpt": {},
        }, {"code": "ok"}

    monkeypatch.setattr(core, "_resolve_access_token", _token)
    monkeypatch.setattr(core.adapters, "fetch_day", _fetch)
    db = _DB()

    result = asyncio.run(core.run_sync_for_account(
        db, "user-1", "snap-account", "2026-07-01",
    ))
    row = db.ads_daily.docs[0]

    assert result["ok"] is True
    assert row["spend_sar"] == 18.75
    assert row["purchases"] is None
    assert row["conversion_data_status"] == "unavailable"
    assert row["conversion_data_error"] == "account_level_spend_only"


class _RouteDB:
    def __init__(self):
        self.snapchat_connections = _Collection([{
            "user_id": "user-1",
            "refresh_token": "refresh",
            "access_token": "access",
            "access_token_expires_at": "2099-01-01T00:00:00+00:00",
            "ad_account_id": "snap-account",
            "ad_account_currency": "SAR",
            "ad_account_timezone": "Asia/Riyadh",
        }])
        self.snapchat_ad_accounts = _Collection([{
            "user_id": "user-1",
            "ad_account_id": "snap-account",
            "enabled": True,
            "currency_native": "SAR",
            "timezone": "Asia/Riyadh",
            "name": "Snap account",
        }])
        self.snapchat_account_daily = _Collection()
        self.snapchat_daily_stats = _Collection()
        self.daily_costs = _Collection()
        self.ads_currency_settings = _Collection()


class _SpendThenFailedConversionClient:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        self.calls += 1

        class _Response:
            text = ""

            def __init__(self, is_spend):
                self.status_code = 200 if is_spend else 403
                self.is_spend = is_spend

            def raise_for_status(self):
                if self.status_code >= 400:
                    request = httpx.Request("GET", "https://example.invalid")
                    response = httpx.Response(
                        self.status_code,
                        request=request,
                    )
                    raise httpx.HTTPStatusError(
                        "failed",
                        request=request,
                        response=response,
                    )

            def json(self):
                if not self.is_spend:
                    return {}
                return {
                    "timeseries_stats": [{
                        "timeseries_stat": {
                            "timeseries": [{
                                "stats": {"spend": 5_000_000},
                            }],
                        },
                    }],
                }

        return _Response(self.calls % 2 == 1)


def _route(router, path):
    return next(route for route in router.routes if route.path == path)


async def _noop_ledger_sync(*args, **kwargs):
    return None


def _install_route_stubs(monkeypatch):
    auth_stub = types.ModuleType("auth")

    async def _current_user(*args, **kwargs):
        return {"id": "user-1"}

    auth_stub.get_current_user_from_db = _current_user
    ledger_stub = types.ModuleType("ad_account_routes")
    ledger_stub._run_sync_for_all = _noop_ledger_sync
    monkeypatch.setitem(sys.modules, "auth", auth_stub)
    monkeypatch.setitem(sys.modules, "ad_account_routes", ledger_stub)


def _assert_unknown_route_rows(db):
    account_row = db.snapchat_account_daily.docs[0]
    daily_row = db.snapchat_daily_stats.docs[0]
    assert account_row["spend_sar"] == 5
    assert account_row["purchases"] is None
    assert account_row["revenue_sar"] is None
    assert account_row["conversion_data_status"] == "unavailable"
    assert daily_row["purchases"] is None
    assert daily_row["revenue"] is None
    assert daily_row["conversion_data_status"] == "unavailable"


def test_legacy_bulk_route_wires_failed_conversions_as_unknown(monkeypatch):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenFailedConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/daily-spend/bulk")
    payload_type = route.dependant.body_params[0].type_
    result = asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    assert result["saved"] == 1
    assert result["items"][0]["purchases"] is None
    assert result["items"][0]["revenue"] is None
    assert result["items"][0]["conversion_data_status"] == "unavailable"
    _assert_unknown_route_rows(db)


def test_multi_account_route_wires_failed_conversions_as_unknown(
    monkeypatch,
):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenFailedConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/sync-all-accounts")
    payload_type = route.dependant.body_params[0].type_
    result = asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    assert result["accounts_synced"] == 1
    assert result["items"][0]["rows_saved"] == 1
    _assert_unknown_route_rows(db)
