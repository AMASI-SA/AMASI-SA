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

if "auth" not in sys.modules:
    auth_import_stub = types.ModuleType("auth")

    async def _auth_import_user(*args, **kwargs):
        return {"id": "user-1"}

    auth_import_stub.get_current_user_from_db = _auth_import_user
    sys.modules["auth"] = auth_import_stub

from ad_account_routes import _fetch_daily_spend
from snapchat_routes import (
    _aggregate_snap_conversion_rows,
    _build_router,
    _fetch_snap_conversion_metrics,
    _parse_snap_conversion_payload,
    _parse_snap_spend_payload,
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


def _campaign_breakdown_timeseries(*campaign_metrics):
    return {
        "timeseries_stats": [{
            "timeseries_stat": {
                "breakdown_stats": {
                    "campaign": [
                        {
                            "id": f"campaign-{index}",
                            "timeseries": [{
                                "stats": {
                                    "conversion_purchases": purchases,
                                    "conversion_purchases_value": revenue_micro,
                                },
                            }],
                        }
                        for index, (purchases, revenue_micro) in enumerate(
                            campaign_metrics,
                            start=1,
                        )
                    ],
                },
            },
        }],
    }


def test_spend_parser_accepts_explicit_zero_but_rejects_partial_payload():
    explicit_zero = _parse_snap_spend_payload({
        "timeseries_stats": [{
            "timeseries_stat": {
                "timeseries": [{"stats": {"spend": 0}}],
            },
        }],
    })
    partial = _parse_snap_spend_payload({
        "timeseries_stats": [{
            "timeseries_stat": {
                "timeseries": [
                    {"stats": {"spend": 5_000_000}},
                    {"stats": {}},
                ],
            },
        }],
    })

    assert explicit_zero == {
        "spend_micro": 0,
        "spend_data_status": "available",
        "spend_data_error": None,
    }
    assert partial["spend_micro"] is None
    assert partial["spend_data_status"] == "unavailable"
    assert partial["spend_data_error"] == "invalid_spend_metric"


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


def test_campaign_breakdown_conversions_are_summed_for_ad_account():
    parsed = _parse_snap_conversion_payload(
        _campaign_breakdown_timeseries(
            (2, 10_000_000),
            (3, 15_000_000),
        ),
    )

    assert parsed == {
        "purchases": 5,
        "revenue_value_micro": 25_000_000,
        "conversion_data_status": "available",
        "conversion_data_error": None,
    }


def test_campaign_breakdown_missing_metric_fails_closed():
    payload = _campaign_breakdown_timeseries(
        (2, 10_000_000),
        (3, 15_000_000),
    )
    second_stats = payload["timeseries_stats"][0]["timeseries_stat"][
        "breakdown_stats"
    ]["campaign"][1]["timeseries"][0]["stats"]
    second_stats.pop("conversion_purchases_value")

    parsed = _parse_snap_conversion_payload(payload)

    assert parsed["purchases"] == 5
    assert parsed["revenue_value_micro"] is None
    assert parsed["conversion_data_status"] == "partial"
    assert "conversion_purchases_value" in parsed["conversion_data_error"]


def test_total_campaign_breakdown_conversions_are_supported():
    parsed = _parse_snap_conversion_payload({
        "total_stats": [{
            "total_stat": {
                "breakdown_stats": {
                    "campaign": [
                        {
                            "id": "campaign-1",
                            "stats": {
                                "conversion_purchases": 4,
                                "conversion_purchases_value": 30_000_000,
                            },
                        },
                        {
                            "id": "campaign-2",
                            "stats": {
                                "conversion_purchases": 0,
                                "conversion_purchases_value": 0,
                            },
                        },
                    ],
                },
            },
        }],
    })

    assert parsed["purchases"] == 4
    assert parsed["revenue_value_micro"] == 30_000_000
    assert parsed["conversion_data_status"] == "available"


def test_provider_failure_and_unconsumed_pagination_fail_closed():
    failed = _parse_snap_conversion_payload({
        "request_status": "FAILED",
        **_campaign_breakdown_timeseries((7, 70_000_000)),
    })
    paginated = _parse_snap_conversion_payload({
        **_campaign_breakdown_timeseries((7, 70_000_000)),
        "paging": {"next_link": "https://adsapi.snapchat.com/v1/next"},
    })
    failed_subrequest_payload = _campaign_breakdown_timeseries(
        (7, 70_000_000),
    )
    failed_subrequest_payload["timeseries_stats"][0]["timeseries_stat"][
        "breakdown_stats"
    ]["campaign"][0]["sub_request_status"] = "ERROR"
    failed_subrequest = _parse_snap_conversion_payload(
        failed_subrequest_payload,
    )

    assert failed["purchases"] is None
    assert failed["conversion_data_status"] == "unavailable"
    assert failed["conversion_data_error"].startswith("provider_status:")
    assert failed_subrequest["conversion_data_status"] == "unavailable"
    assert failed_subrequest["conversion_data_error"].startswith(
        "provider_status:",
    )
    assert paginated["purchases"] is None
    assert paginated["conversion_data_status"] == "unavailable"
    assert paginated["conversion_data_error"] == "pagination_incomplete"


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
        for key, value in query.items():
            actual = doc.get(key)
            if isinstance(value, dict):
                if "$exists" in value and (
                    (key in doc) is not bool(value["$exists"])
                ):
                    return False
                if "$in" in value and actual not in value["$in"]:
                    return False
                if "$ne" in value and actual == value["$ne"]:
                    return False
                if "$gte" in value and (
                    actual is None or actual < value["$gte"]
                ):
                    return False
                if "$lte" in value and (
                    actual is None or actual > value["$lte"]
                ):
                    return False
                continue
            if actual != value:
                return False
        return True

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

            def __aiter__(self):
                self.index = 0
                return self

            async def __anext__(self):
                if self.index >= len(self.values):
                    raise StopAsyncIteration
                value = self.values[self.index]
                self.index += 1
                return value

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
        self.snapchat_ads_daily = _Collection()
        self.snapchat_daily_stats = _Collection()
        self.daily_costs = _Collection()
        self.ads_currency_settings = _Collection()

    def __getitem__(self, name):
        return getattr(self, name)


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


class _SpendThenCampaignConversionClient:
    def __init__(self, *args, **kwargs):
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        self.calls += 1
        params = kwargs.get("params") or {}

        class _Response:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        if params.get("fields") == "spend":
            return _Response({
                "timeseries_stats": [{
                    "timeseries_stat": {
                        "timeseries": [{
                            "stats": {"spend": 5_000_000},
                        }],
                    },
                }],
            })
        assert params.get("breakdown") == "campaign"
        assert params.get("limit") == 200
        return _Response(_campaign_breakdown_timeseries((3, 12_000_000)))


class _EmptySpendClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        class _Response:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            def json(self):
                return {"timeseries_stats": []}

        return _Response()


class _ZeroSpendThenCampaignConversionClient(
    _SpendThenCampaignConversionClient,
):
    async def get(self, *args, **kwargs):
        response = await super().get(*args, **kwargs)
        params = kwargs.get("params") or {}
        if params.get("fields") == "spend":
            response.payload = {
                "timeseries_stats": [{
                    "timeseries_stat": {
                        "timeseries": [{"stats": {"spend": 0}}],
                    },
                }],
            }
        return response


class _MalformedSpendClient(_EmptySpendClient):
    async def get(self, *args, **kwargs):
        response = await super().get(*args, **kwargs)
        response.json = lambda: {"timeseries_stats": 42}
        return response


class _FailedStatusSpendClient(_EmptySpendClient):
    async def get(self, *args, **kwargs):
        response = await super().get(*args, **kwargs)
        response.json = lambda: {
            "request_status": "FAILED",
            "timeseries_stats": [{
                "timeseries_stat": {
                    "timeseries": [{
                        "stats": {"spend": 5_000_000},
                    }],
                },
            }],
        }
        return response


class _SpendThenRevenueOnlyConversionClient(
    _SpendThenCampaignConversionClient,
):
    async def get(self, *args, **kwargs):
        response = await super().get(*args, **kwargs)
        params = kwargs.get("params") or {}
        if params.get("fields") != "spend":
            response.payload = _campaign_breakdown_timeseries(
                (None, 12_000_000),
            )
        return response


class _TwoAccountMixedClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, *args, **kwargs):
        params = kwargs.get("params") or {}

        class _Response:
            status_code = 200
            text = ""

            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        if "snap-account-2" in url:
            return _Response({"timeseries_stats": []})
        if params.get("fields") == "spend":
            return _Response({
                "timeseries_stats": [{
                    "timeseries_stat": {
                        "timeseries": [{
                            "stats": {"spend": 5_000_000},
                        }],
                    },
                }],
            })
        return _Response(_campaign_breakdown_timeseries((3, 12_000_000)))


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


def _assert_unknown_route_rows(db, *, analytics_only=False):
    account_row = db.snapchat_account_daily.docs[0]
    daily_row = db.snapchat_daily_stats.docs[0]
    assert account_row["spend_sar"] == 5
    assert account_row["purchases"] is None
    assert account_row["revenue_sar"] is None
    assert account_row["conversion_data_status"] == "unavailable"
    if analytics_only:
        assert account_row["ingestion_mode"] == "analytics_backfill"
        assert account_row["accounting_eligible"] is False
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
    assert result["errors"][0]["kind"] == "conversion_quality"
    _assert_unknown_route_rows(db)
    assert db.snapchat_account_daily.docs[0]["accounting_eligible"] is True


def test_legacy_bulk_preserves_known_conversions_on_transient_failure(
    monkeypatch,
):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 4,
        "spend_sar": 4,
        "purchases": 9,
        "revenue_native": 90,
        "revenue_sar": 90,
        "conversion_data_status": "available",
    })
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

    row = db.snapchat_account_daily.docs[0]
    assert row["purchases"] == 9
    assert row["revenue_sar"] == 90
    assert row["conversion_data_status"] == "available"
    assert result["items"][0]["purchases"] == 9
    assert result["items"][0]["revenue"] == 90
    assert result["items"][0]["conversion_data_status"] == "available"
    assert result["items"][0]["conversion_refresh_status"] == "unavailable"
    assert result["errors"][0]["kind"] == "conversion_quality"


def test_legacy_bulk_does_not_persist_empty_spend_as_zero(monkeypatch):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _EmptySpendClient,
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

    assert result["saved"] == 0
    assert result["errors"][0]["error"] == "spend_metric_missing"
    assert db.snapchat_account_daily.docs == []
    assert db.snapchat_daily_stats.docs == []
    assert db.daily_costs.docs == []


def test_legacy_financial_aggregate_excludes_analytics_only_spend(
    monkeypatch,
):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "analytics-only-account",
        "date": "2026-07-28",
        "spend": 100,
        "spend_sar": 100,
        "accounting_eligible": False,
        "purchases": 1,
        "revenue_sar": 10,
        "conversion_data_status": "available",
    })
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "legacy-account-with-stale-sar",
        "date": "2026-07-28",
        "spend": 30,
        "spend_sar": 100,
        "purchases": 1,
        "revenue_sar": 10,
        "conversion_data_status": "available",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenFailedConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/daily-spend/bulk")
    payload_type = route.dependant.body_params[0].type_
    asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    assert db.snapchat_daily_stats.docs[0]["spend"] == 205
    assert db.daily_costs.docs[0]["snapchat_ads"] == 35


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
    ledger_calls = []

    async def _record_ledger_sync(*args, **kwargs):
        ledger_calls.append((args, kwargs))

    sys.modules["ad_account_routes"]._run_sync_for_all = _record_ledger_sync
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
    assert result["items"][0]["errors"] == 1
    assert result["accounts_complete"] == 0
    assert result["sync_status"] == "partial"
    assert result["source_only"] is True
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False
    assert ledger_calls == []
    assert db.daily_costs.docs == []
    _assert_unknown_route_rows(db, analytics_only=True)


def test_multi_account_route_fetches_campaign_breakdown_conversions(
    monkeypatch,
):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenCampaignConversionClient,
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
    assert result["accounts_complete"] == 1
    assert result["sync_status"] == "complete"
    account_row = db.snapchat_account_daily.docs[0]
    daily_row = db.snapchat_daily_stats.docs[0]
    assert account_row["purchases"] == 3
    assert account_row["revenue_sar"] == 12
    assert account_row["conversion_data_status"] == "available"
    assert account_row["accounting_eligible"] is False
    assert daily_row["purchases"] == 3
    assert daily_row["revenue"] == 12
    assert daily_row["conversion_data_status"] == "available"
    assert db.daily_costs.docs == []


def test_multi_account_route_does_not_turn_empty_spend_into_zero(monkeypatch):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _EmptySpendClient,
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

    assert result["items"][0]["rows_saved"] == 0
    assert result["items"][0]["errors"] == 1
    assert result["errors"][0]["error"] == "spend_metric_missing"
    assert db.snapchat_account_daily.docs == []
    assert db.snapchat_daily_stats.docs == []
    assert db.daily_costs.docs == []


def test_multi_account_route_persists_explicit_provider_zero(monkeypatch):
    db = _RouteDB()
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _ZeroSpendThenCampaignConversionClient,
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

    assert result["sync_status"] == "complete"
    assert db.snapchat_account_daily.docs[0]["spend_sar"] == 0
    assert db.snapchat_daily_stats.docs[0]["spend"] == 0
    assert db.snapchat_daily_stats.docs[0][
        "spend_data_status"
    ] == "available"


def test_malformed_and_failed_status_spend_payloads_fail_per_date(
    monkeypatch,
):
    cases = [
        (_MalformedSpendClient, "invalid_spend_timeseries"),
        (_FailedStatusSpendClient, "spend_provider_status:FAILED"),
    ]
    for client_type, expected_error in cases:
        db = _RouteDB()
        _install_route_stubs(monkeypatch)
        monkeypatch.setattr(
            snapchat_routes.httpx,
            "AsyncClient",
            client_type,
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

        assert result["sync_status"] == "partial"
        assert result["errors"][0]["error"] == expected_error
        assert db.snapchat_account_daily.docs == []
        assert db.snapchat_daily_stats.docs == []


def test_missing_second_account_is_visible_in_daily_quality(monkeypatch):
    db = _RouteDB()
    db.snapchat_ad_accounts.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account-2",
        "enabled": True,
        "currency_native": "SAR",
        "timezone": "Asia/Riyadh",
        "name": "Second Snap account",
    })
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "disabled-snap-account",
        "date": "2026-07-28",
        "spend": 999,
        "spend_sar": 999,
        "purchases": 99,
        "revenue_sar": 999,
        "conversion_data_status": "available",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _TwoAccountMixedClient,
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

    assert result["accounts_synced"] == 2
    assert result["accounts_complete"] == 1
    assert result["sync_status"] == "partial"
    assert len(result["errors"]) == 1
    daily_row = db.snapchat_daily_stats.docs[0]
    assert daily_row["spend"] == 5
    assert daily_row["spend_data_status"] == "partial"
    assert daily_row["spend_accounts_total"] == 2
    assert daily_row["spend_accounts_complete"] == 1
    assert daily_row["conversion_data_status"] == "unavailable"
    assert daily_row["conversion_accounts_total"] == 2
    assert "account_day_missing" in daily_row["conversion_data_error"]


def test_failed_refresh_does_not_bless_stale_row_as_fresh(monkeypatch):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 77,
        "spend_sar": 77,
        "purchases": 7,
        "revenue_sar": 70,
        "conversion_data_status": "available",
        "updated_at": "2026-07-01T00:00:00+00:00",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _EmptySpendClient,
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

    assert result["sync_status"] == "partial"
    assert result["items"][0]["rows_saved"] == 0
    assert db.snapchat_account_daily.docs[0]["spend_sar"] == 77
    assert db.snapchat_account_daily.docs[0]["updated_at"] == (
        "2026-07-01T00:00:00+00:00"
    )
    assert db.snapchat_daily_stats.docs == []


def test_transient_conversion_failure_preserves_provider_proven_values(
    monkeypatch,
):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 4,
        "spend_sar": 4,
        "purchases": 9,
        "revenue_native": 90,
        "revenue_sar": 90,
        "conversion_data_status": "available",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenFailedConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/sync-all-accounts")
    payload_type = route.dependant.body_params[0].type_
    asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    row = db.snapchat_account_daily.docs[0]
    assert row["spend_sar"] == 5
    assert row["purchases"] == 9
    assert row["revenue_sar"] == 90
    assert row["conversion_data_status"] == "available"
    assert row["conversion_refresh_status"] == "unavailable"
    assert row["conversion_refresh_error"] == "http_403"
    daily_row = db.snapchat_daily_stats.docs[0]
    assert daily_row["conversion_data_status"] == "available"
    assert daily_row["conversion_refresh_status"] == "unavailable"
    assert daily_row["conversion_refresh_accounts_complete"] == 0
    accounting_rows, _ = asyncio.run(_fetch_daily_spend(
        db,
        "user-1",
        "snapchat",
        "snap-account",
        "2026-07-28",
        "2026-07-28",
    ))
    assert accounting_rows == [{"date": "2026-07-28", "spend": 4.0}]


def test_transient_failure_preserves_each_known_partial_metric(monkeypatch):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 4,
        "spend_sar": 4,
        "purchases": 9,
        "revenue_native": None,
        "revenue_sar": None,
        "conversion_data_status": "partial",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenFailedConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/sync-all-accounts")
    payload_type = route.dependant.body_params[0].type_
    asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    row = db.snapchat_account_daily.docs[0]
    assert row["purchases"] == 9
    assert row["revenue_sar"] is None
    assert row["conversion_data_status"] == "partial"


def test_backfill_refreshes_snapshot_after_foreground_accounting_update(
    monkeypatch,
):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 30,
        "spend_sar": 30,
        "accounting_eligible": True,
        "accounting_spend_snapshot": 20,
        "purchases": 1,
        "revenue_native": 10,
        "revenue_sar": 10,
        "conversion_data_status": "available",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenCampaignConversionClient,
    )
    route = _route(_build_router(db), "/snapchat/sync-all-accounts")
    payload_type = route.dependant.body_params[0].type_
    asyncio.run(route.endpoint(
        payload_type(
            from_date="2026-07-28",
            to_date="2026-07-28",
        ),
        user={"id": "user-1"},
    ))

    row = db.snapchat_account_daily.docs[0]
    assert row["spend"] == 5
    assert row["accounting_eligible"] is False
    assert row["accounting_spend_snapshot"] == 30
    accounting_rows, _ = asyncio.run(_fetch_daily_spend(
        db,
        "user-1",
        "snapchat",
        "snap-account",
        "2026-07-28",
        "2026-07-28",
    ))
    assert accounting_rows == [{"date": "2026-07-28", "spend": 30.0}]


def test_concurrent_foreground_refresh_wins_over_analytics_backfill(
    monkeypatch,
):
    class _ConcurrentCollection(_Collection):
        async def update_one(self, query, update, upsert=False):
            if "accounting_eligible" in (update.get("$set") or {}):
                self.docs[0].update({
                    "spend": 30,
                    "spend_sar": 30,
                    "accounting_eligible": True,
                    "updated_at": "2026-07-28T12:00:00+00:00",
                })

                class _Result:
                    matched_count = 0

                return _Result()
            return await super().update_one(query, update, upsert=upsert)

    db = _RouteDB()
    db.snapchat_account_daily = _ConcurrentCollection([{
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 20,
        "spend_sar": 20,
        "accounting_eligible": True,
        "updated_at": "2026-07-28T11:00:00+00:00",
        "purchases": 1,
        "revenue_sar": 10,
        "conversion_data_status": "available",
    }])
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenCampaignConversionClient,
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

    row = db.snapchat_account_daily.docs[0]
    assert result["sync_status"] == "partial"
    assert result["errors"][0]["error"] == "concurrent_update_detected"
    assert row["spend"] == 30
    assert row["accounting_eligible"] is True
    assert db.snapchat_daily_stats.docs == []


def test_complementary_partial_refresh_completes_existing_metric(monkeypatch):
    db = _RouteDB()
    db.snapchat_account_daily.docs.append({
        "user_id": "user-1",
        "ad_account_id": "snap-account",
        "date": "2026-07-28",
        "spend": 4,
        "spend_sar": 4,
        "purchases": 9,
        "revenue_native": None,
        "revenue_sar": None,
        "conversion_data_status": "partial",
    })
    _install_route_stubs(monkeypatch)
    monkeypatch.setattr(
        snapchat_routes.httpx,
        "AsyncClient",
        _SpendThenRevenueOnlyConversionClient,
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

    row = db.snapchat_account_daily.docs[0]
    assert row["purchases"] == 9
    assert row["revenue_sar"] == 12
    assert row["conversion_data_status"] == "available"
    assert result["sync_status"] == "partial"
    assert result["errors"][0]["kind"] == "conversion_quality"


def test_accounting_reader_ignores_analytics_only_snapchat_rows():
    class _SpendSourceDB:
        def __init__(self):
            self.snapchat_account_daily = _Collection([
                {
                    "user_id": "user-1",
                    "ad_account_id": "snap-account",
                    "date": "2026-07-28",
                    "spend": 100,
                    "accounting_eligible": False,
                    "accounting_spend_snapshot": 20,
                },
            ])
            self.snapchat_ads_daily = _Collection()

        def __getitem__(self, name):
            return getattr(self, name)

    rows, source = asyncio.run(_fetch_daily_spend(
        _SpendSourceDB(),
        "user-1",
        "snapchat",
        "snap-account",
        "2026-07-28",
        "2026-07-28",
    ))

    assert source == "snapchat_account_daily"
    assert rows == [{"date": "2026-07-28", "spend": 20.0}]


def test_accounting_reader_does_not_fallback_for_new_analytics_only_row():
    class _SpendSourceDB:
        def __init__(self):
            self.snapchat_account_daily = _Collection([{
                "user_id": "user-1",
                "ad_account_id": "snap-account",
                "date": "2026-07-28",
                "spend": 100,
                "accounting_eligible": False,
            }])
            self.snapchat_ads_daily = _Collection([{
                "user_id": "user-1",
                "date": "2026-07-28",
                "spend": 999,
            }])

        def __getitem__(self, name):
            return getattr(self, name)

    rows, source = asyncio.run(_fetch_daily_spend(
        _SpendSourceDB(),
        "user-1",
        "snapchat",
        "snap-account",
        "2026-07-28",
        "2026-07-28",
    ))

    assert source == "snapchat_account_daily"
    assert rows == []
