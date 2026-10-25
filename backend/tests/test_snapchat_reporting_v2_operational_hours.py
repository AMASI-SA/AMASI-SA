"""Real transport tests for the spend-only current-day observation boundary."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from snapchat_v2.client import SnapchatClientError, SnapchatV2Client
from snapchat_v2.operational_hours import fetch_current_operational_hours

NOW = datetime(2026, 10, 25, 22, 20, tzinfo=timezone.utc)
START = datetime(2026, 10, 25, 21, tzinfo=timezone.utc)
END = START + timedelta(hours=2)
DAY = date(2026, 10, 26)


class Tokens:
    async def get_access_token(self, user_id, **kwargs):
        assert user_id == "owner"
        return "synthetic-test-token"


def payload(account_id="Opaque-ACCOUNT-AbC", *, empty=False, breakdown=False, spend=0):
    points = [{"start_time": (START + timedelta(hours=i)).isoformat(),
               "end_time": (START + timedelta(hours=i + 1)).isoformat(), "stats": {"spend": spend}}
              for i in range(2)]
    stat = {"id": account_id, "type": "AD_ACCOUNT", "granularity": "HOUR",
            "start_time": START.isoformat(), "end_time": END.isoformat()}
    if breakdown:
        stat["breakdown_stats"] = {"campaign": [] if empty else [{"id": "Paused-Campaign-MiXeD", "timeseries": points}]}
    else:
        stat["timeseries"] = [] if empty else points
    return {"request_status": "SUCCESS", "timeseries_stats": [
        {"sub_request_status": "SUCCESS", "timeseries_stat": stat}], "paging": {}}


async def fetch(body, *, currency="SAR", zone="Asia/Riyadh", first_status=200, fallback=None, now=NOW):
    requests = []

    def transport(request):
        requests.append(request)
        assert request.method == "GET" and request.url.path == "/v1/adaccounts/Opaque-ACCOUNT-AbC/stats"
        assert request.url.params["fields"] == "spend" and request.url.params["granularity"] == "HOUR"
        assert request.url.params["omit_empty"] == "false"
        assert request.url.params["action_report_time"] == "conversion"
        assert request.url.params["swipe_up_attribution_window"] == "28_DAY"
        assert request.url.params["view_attribution_window"] == "7_DAY"
        assert datetime.fromisoformat(request.url.params["start_time"]) == START
        assert datetime.fromisoformat(request.url.params["end_time"]) == END
        if len(requests) == 1:
            assert "breakdown" not in request.url.params
            return httpx.Response(first_status, json=body)
        assert request.url.params["breakdown"] == "campaign" and first_status == 400
        return httpx.Response(200, json=fallback)

    client = SnapchatV2Client(None, "owner", token_store=Tokens(), now=lambda: now,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kwargs))
    result = await fetch_current_operational_hours(client,
        {"ad_account_id": "Opaque-ACCOUNT-AbC", "currency": currency, "timezone": zone},
        report_date=DAY, sync_run_id="run", owner_id="worker", now=NOW,
        projection_generated_at=NOW - timedelta(seconds=2))
    return result, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("currency,zone", [("USD", "America/Los_Angeles"), ("SAR", "Asia/Riyadh")])
@pytest.mark.parametrize("spend", [0, 1_500_000])
async def test_same_native_account_path_preserves_scope_explicit_zero_and_positive(currency, zone, spend):
    result, requests = await fetch(payload(spend=spend), currency=currency, zone=zone)
    proof = result["operational_observation"]
    assert len(requests) == 1 and proof["currency"] == currency and proof["account_timezone"] == zone
    assert proof["ad_account_id"] == "Opaque-ACCOUNT-AbC" and proof["explicit_spend_present"] is True
    assert proof["data_state"] == "explicit_hour_spend" and proof["provider_rows"] == 2
    hours = result["operational_hours"]
    assert [hour["spend_native"] for hour in hours[:3]] == [spend / 1_000_000, spend / 1_000_000, None]
    assert hours[1]["hour_start_utc"] <= NOW < hours[1]["hour_end_utc"]
    assert hours[2]["status"] == "future"
    assert all(not ({"impressions", "purchases", "swipes"} & hour.keys()) for hour in hours)
    assert set(result) == {"operational_observation", "operational_hours"}


@pytest.mark.asyncio
async def test_strict_successful_empty_is_no_activity_without_explicit_metric_claim():
    result, _ = await fetch(payload(empty=True))
    proof = result["operational_observation"]
    assert proof["data_state"] == "observed_no_activity" and proof["provider_rows"] == 0
    assert proof["explicit_spend_present"] is False
    assert [h["spend_native"] for h in result["operational_hours"][:3]] == [0, 0, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [True, False])
async def test_only_explicit_http400_enables_fixed_strict_campaign_fallback(empty):
    result, requests = await fetch({}, first_status=400, fallback=payload(empty=empty, breakdown=True))
    assert len(requests) == 2
    assert result["operational_observation"]["source_mode"] == "campaign_hour_http400"
    assert result["operational_observation"]["completed_requests"] == 2


@pytest.mark.asyncio
async def test_permission_denial_is_one_request_without_fallback_or_retry():
    calls = []

    def deny(request):
        calls.append(request)
        return httpx.Response(403, json={"untrusted": "denial"})

    client = SnapchatV2Client(None, "owner", token_store=Tokens(), now=lambda: NOW,
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(deny), **kwargs))
    with pytest.raises(SnapchatClientError, match="rejected"):
        await fetch_current_operational_hours(client, {"ad_account_id": "account", "currency": "SAR", "timezone": "Asia/Riyadh"},
            report_date=DAY, sync_run_id="run", owner_id="worker", now=NOW, projection_generated_at=NOW)
    assert len(calls) == 1


def corrupt(kind):
    body = payload()
    stat = body["timeseries_stats"][0]["timeseries_stat"]
    if kind == "missing_request_success": body.pop("request_status")
    elif kind == "failed_request": body["request_status"] = "ERROR"
    elif kind == "missing_subrequest_success": body["timeseries_stats"][0].pop("sub_request_status")
    elif kind == "missing_envelope": body.pop("timeseries_stats")
    elif kind == "empty_envelope": body["timeseries_stats"] = []
    elif kind == "missing_series": stat.pop("timeseries")
    elif kind == "wrong_account": stat["id"] = "other-account"
    elif kind == "case_changed_account": stat["id"] = stat["id"].lower()
    elif kind == "wrong_type": stat["type"] = "CAMPAIGN"
    elif kind == "wrong_granularity": stat["granularity"] = "DAY"
    elif kind == "wrong_window": stat["end_time"] = (END + timedelta(hours=1)).isoformat()
    elif kind == "naive_window": stat["start_time"] = START.replace(tzinfo=None).isoformat()
    elif kind == "truncated": body["paging"] = {"next_link": "https://adsapi.snapchat.com/v1/next"}
    elif kind == "bad_paging": body["paging"] = {"next_link": False}
    elif kind == "gap": stat["timeseries"].pop()
    elif kind == "duplicate": stat["timeseries"][1] = deepcopy(stat["timeseries"][0])
    elif kind == "future_point":
        stat["timeseries"][1]["start_time"] = END.isoformat()
        stat["timeseries"][1]["end_time"] = (END + timedelta(hours=1)).isoformat()
    elif kind == "missing_spend": stat["timeseries"][0]["stats"] = {}
    else: stat["timeseries"][0]["stats"]["spend"] = {"null": None, "bool": False, "nan": float("nan"), "negative": -1, "string": "0"}[kind]
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing_request_success", "failed_request", "missing_subrequest_success",
    "missing_envelope", "empty_envelope", "missing_series", "wrong_account", "case_changed_account", "wrong_type",
    "wrong_granularity", "wrong_window", "naive_window", "truncated", "bad_paging", "gap", "duplicate",
    "future_point", "missing_spend", "null", "bool", "nan", "negative", "string"])
async def test_malformed_partial_or_unobserved_metric_cannot_be_operational_zero(kind):
    # Response(content=...) permits a nonfinite JSON fixture at the true transport
    # boundary; the parser must reject it, rather than default it to zero.
    if kind == "nan":
        import json
        body = corrupt(kind)
        client = SnapchatV2Client(None, "owner", token_store=Tokens(), now=lambda: NOW,
            client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=json.dumps(body))), **kwargs))
        with pytest.raises(ValueError):
            await fetch_current_operational_hours(client, {"ad_account_id": "Opaque-ACCOUNT-AbC", "currency": "SAR", "timezone": "Asia/Riyadh"},
                report_date=DAY, sync_run_id="run", owner_id="worker", now=NOW, projection_generated_at=NOW)
    else:
        with pytest.raises((ValueError, SnapchatClientError)):
            await fetch(corrupt(kind))


@pytest.mark.asyncio
async def test_fallback_is_also_strict_and_incomplete_campaign_points_fail_closed():
    fallback = payload(breakdown=True)
    fallback["timeseries_stats"][0]["timeseries_stat"]["breakdown_stats"]["campaign"][0]["timeseries"].pop()
    with pytest.raises(ValueError):
        await fetch({}, first_status=400, fallback=fallback)


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", [NOW - timedelta(seconds=1), NOW + timedelta(minutes=16)])
async def test_observation_clock_and_request_duration_are_bounded(observed):
    with pytest.raises(ValueError):
        await fetch(payload(empty=True), now=observed)


@pytest.mark.asyncio
async def test_stale_worker_initial_reset_cannot_clear_newer_proof_after_lost_heartbeat_ack():
    from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION
    from snapchat_v2.sync_pipeline import _reset_operational_observation
    from tests.test_snapchat_reporting_v2_refresh_visibility import Database

    db = Database()
    collection = db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION]
    identity = {"user_id": "owner", "provider": "snapchat_ads", "ad_account_id": "account",
        "projection_timezone": "Asia/Riyadh", "report_date": DAY.isoformat(), "action_report_time": "conversion"}
    collection.insert_one({**identity, "sync_run_id": "prior-run", "generated_at": NOW - timedelta(minutes=2),
        "operational_attempt_run_id": "prior-run", "operational_attempt_owner_id": "prior-worker",
        "operational_observation": {"sync_run_id": "prior-run"}, "operational_hours": [{"spend_native": 0}]})
    winner = None

    async def heartbeat_loses_ack_after_new_worker_publishes(_progress):
        nonlocal winner
        # The old owner was valid when the heartbeat reached Mongo; its return
        # is delayed until a different owner has acquired and published.
        collection.update_one(identity, {"$set": {"sync_run_id": "winner-run", "generated_at": NOW,
            "operational_attempt_run_id": "winner-run", "operational_attempt_owner_id": "winner-worker",
            "operational_observation": {"sync_run_id": "winner-run", "observed_at": NOW},
            "operational_hours": [{"spend_native": 1, "status": "provisional"}]}})
        winner = deepcopy(collection.find_one(identity))

    matched = await _reset_operational_observation(db, identity, sync_run_id="stale-run", owner_id="stale-worker",
        heartbeat=heartbeat_loses_ack_after_new_worker_publishes)
    assert matched is False
    assert collection.find_one(identity) == winner


@pytest.mark.asyncio
async def test_current_owner_initial_reset_removes_old_proof_and_claims_exact_attempt():
    from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION
    from snapchat_v2.sync_pipeline import _reset_operational_observation
    from tests.test_snapchat_reporting_v2_refresh_visibility import Database

    db = Database()
    identity = {"user_id": "owner", "ad_account_id": "account"}
    db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].insert_one({**identity, "sync_run_id": "prior-run",
        "generated_at": NOW, "operational_observation": {"sync_run_id": "prior-run"}, "operational_hours": []})
    heartbeats = []

    async def heartbeat(progress):
        heartbeats.append(progress)

    assert await _reset_operational_observation(db, identity, sync_run_id="current-run", owner_id="current-worker", heartbeat=heartbeat)
    row = db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find_one(identity)
    assert heartbeats == [{}] and row["operational_attempt_run_id"] == "current-run"
    assert row["operational_attempt_owner_id"] == "current-worker"
    assert "operational_observation" not in row and "operational_hours" not in row
