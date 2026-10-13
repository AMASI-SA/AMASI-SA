from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from tracking_diagnostics.evidence import BrowserObservation, PixelEvent, ProviderObservation, Scope, NAMESPACES, compare

NOW = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
JOB = "a" * 32


def inputs(platform="tiktok"):
    scope = Scope(user_id="owner", store_id="store", provider=platform, account_id="account", campaign_id="campaign")
    common = dict(scope=scope, job_id=JOB, observed_at=NOW)
    event = PixelEvent(provider=platform, identifier_namespace=NAMESPACES[platform], pixel_id="pixel-one", event="page_view", evidence_kind="browser_request_attempt")
    browser = BrowserObservation(**common, status="completed", consent="granted", events=(event,))
    provider = ProviderObservation(**common, status="verified", identifier_namespace=NAMESPACES[platform], binding_complete=True, campaign_pixel_ids=("pixel-one",))
    return scope, browser, provider


def run(scope, browser, provider):
    return compare(scope, JOB, browser=browser, provider=provider, now=NOW)


@pytest.mark.parametrize("platform", ["tiktok", "snapchat", "meta", "google"])
def test_request_observation_never_proves_delivery_sales_or_duplicate_purchase(platform):
    result = run(*inputs(platform))
    assert result["matched_pixel_ids"] == ["pixel-one"]
    assert result["status"] == "partial"
    assert "request_observed" in [row["code"] for row in result["findings"]]
    assert result["provider_purchase_receipt"] == "unknown"
    assert all(result[key] is False for key in ("financial_attribution_verified", "duplicate_purchase_verified", "pixel_broken_verified", "mutations_performed"))


def test_two_pixels_are_informational_not_a_broken_pixel_diagnosis():
    scope, browser, provider = inputs()
    extra = browser.events[0].model_copy(update={"pixel_id": "second-account-pixel"})
    result = run(scope, browser.model_copy(update={"events": (*browser.events, extra)}), provider)
    assert result["other_pixel_ids"] == ["second-account-pixel"]
    assert "multiple_pixels" in [row["code"] for row in result["findings"]]
    assert not result["pixel_broken_verified"]


@pytest.mark.parametrize("field", ["user_id", "store_id", "account_id", "campaign_id", "provider"])
def test_rejects_cross_scope_evidence(field):
    scope, browser, provider = inputs()
    other = scope.model_copy(update={field: "meta" if field == "provider" else "other"})
    with pytest.raises(ValueError, match="scope_mismatch"):
        run(scope, browser.model_copy(update={"scope": other}), provider)


def test_rejects_other_job_and_expired_or_future_evidence():
    scope, browser, provider = inputs()
    with pytest.raises(ValueError, match="scope_mismatch"):
        run(scope, browser.model_copy(update={"job_id": "b" * 32}), provider)
    for offset in (timedelta(minutes=-16), timedelta(minutes=2)):
        result = run(scope, browser.model_copy(update={"observed_at": NOW + offset}), provider)
        assert result["matched_pixel_ids"] == []
        assert result["findings"][0]["code"] == "stale"


def test_blocked_page_does_not_become_missing_pixel():
    scope, browser, provider = inputs()
    result = run(scope, browser.model_copy(update={"status": "blocked"}), provider)
    assert result["findings"][0]["code"] == "browser_unavailable"
    assert result["matched_pixel_ids"] == []


def test_configured_tag_is_not_an_emitted_event():
    scope, browser, provider = inputs()
    event = browser.events[0].model_copy(update={"evidence_kind": "configured"})
    result = run(scope, browser.model_copy(update={"events": (event,)}), provider)
    assert "configured_only" in [row["code"] for row in result["findings"]]


def test_account_pixel_list_is_not_a_campaign_binding():
    scope, browser, provider = inputs()
    result = run(scope, browser, provider.model_copy(update={"binding_complete": False}))
    assert result["matched_pixel_ids"] == []
    assert "binding_unknown" in [row["code"] for row in result["findings"]]


def test_missing_pixel_with_unknown_consent_remains_unverified():
    scope, browser, provider = inputs()
    result = run(scope, browser.model_copy(update={"events": (), "consent": "unknown"}), provider)
    assert result["status"] == "unverified"
    assert {"consent_limited", "expected_not_observed"} <= {row["code"] for row in result["findings"]}


def test_rejects_raw_payload_and_media_or_event_writes():
    _, browser, _ = inputs()
    for update in ({"raw_body": "email=private"}, {"media_downloaded": True}, {"provider_events_sent": True}):
        with pytest.raises(ValidationError):
            BrowserObservation.model_validate({**browser.model_dump(), **update})


def test_other_platform_event_does_not_match_same_identifier():
    scope, browser, provider = inputs()
    event = browser.events[0].model_copy(update={"provider": "meta"})
    assert run(scope, browser.model_copy(update={"events": (event,)}), provider)["matched_pixel_ids"] == []


def test_provider_pixel_ids_require_explicit_browser_namespace():
    _, _, provider = inputs()
    payload = provider.model_dump()
    payload.pop("identifier_namespace")
    with pytest.raises(ValidationError, match="identifier_namespace_required"):
        ProviderObservation.model_validate(payload)


@pytest.mark.parametrize("record_kind", ["browser", "provider"])
def test_rejects_cross_platform_identifier_namespace(record_kind):
    _, browser, provider = inputs()
    record = browser.events[0] if record_kind == "browser" else provider
    with pytest.raises(ValidationError, match="identifier_namespace_mismatch"):
        type(record).model_validate({**record.model_dump(), "identifier_namespace": "meta_pixel_id"})


def test_rejects_tiktok_native_numeric_id_namespace():
    _, _, provider = inputs()
    with pytest.raises(ValidationError):
        ProviderObservation.model_validate({**provider.model_dump(), "identifier_namespace": "tiktok_pixel_id"})


def test_comparison_does_not_match_equal_strings_in_unknown_namespace():
    scope, browser, provider = inputs()
    result = run(scope, browser, provider.model_copy(update={"identifier_namespace": None}))
    assert result["matched_pixel_ids"] == []
    assert "identity_unknown" in [item["code"] for item in result["findings"]]
