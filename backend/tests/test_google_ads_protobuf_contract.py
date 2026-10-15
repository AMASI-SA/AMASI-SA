"""Independent official v25 protobuf oracle for the native wire contract."""
import runpy
from pathlib import Path

import pytest

protobuf = pytest.importorskip("google.protobuf.json_format")
schema = pytest.importorskip("google.ads.googleads.v25.services.types.google_ads_service")

from integrations_control_center.google_ads_campaign_builder import build_campaign_operations


@pytest.mark.parametrize("kind", ["SEARCH", "DISPLAY", "SHOPPING", "PERFORMANCE_MAX", "DEMAND_GEN", "APP", "HOTEL"])
def test_full_campaign_graph_serializes_against_official_v25_schema(kind):
    fixtures = runpy.run_path(str(Path(__file__).with_name("test_google_ads_campaign_builder.py")))
    operations = build_campaign_operations(fixtures["draft_for"](kind), fixtures["CUSTOMER"])
    for operation in operations:
        protobuf.ParseDict(operation, schema.MutateOperation.pb(schema.MutateOperation()), ignore_unknown_fields=False)


def test_search_request_uses_v25_search_settings_not_removed_top_level_flag():
    request = {"query": "SELECT campaign.id FROM campaign", "searchSettings": {"returnTotalResultsCount": True}}
    parsed = protobuf.ParseDict(request, schema.SearchGoogleAdsRequest.pb(schema.SearchGoogleAdsRequest()), ignore_unknown_fields=False)
    assert parsed.search_settings.return_total_results_count is True


def test_schema_oracle_rejects_removed_field_so_wire_check_cannot_silently_pass():
    with pytest.raises(protobuf.ParseError):
        protobuf.ParseDict({"query": "SELECT campaign.id FROM campaign", "returnTotalResultsCount": True}, schema.SearchGoogleAdsRequest.pb(schema.SearchGoogleAdsRequest()), ignore_unknown_fields=False)
