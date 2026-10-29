"""Mandatory installed official v25 oracle; missing schema is failure, not skip."""
from google.protobuf.json_format import ParseDict,ParseError
from google.ads.googleads.v25.services.types.google_ads_service import MutateGoogleAdsRequest
from google.ads.googleads.v25.enums.types.campaign_status import CampaignStatusEnum
import pytest
from integrations_control_center import google_ads_funded_creation_contract as funded
from tests.test_google_ads_funded_creation_contract import inputs

@pytest.mark.parametrize('validate_only',[True,False])
def test_actual_existing_draft_full_new_enabled_request_matches_official_v25(validate_only):
    result=funded.prepare_google_funded_creation_intent(**inputs())
    parsed=ParseDict({'customerId':'123',**result['request'],'validateOnly':validate_only},
        MutateGoogleAdsRequest.pb(MutateGoogleAdsRequest()),ignore_unknown_fields=False)
    assert parsed.partial_failure is False and parsed.validate_only is validate_only
    campaigns=[op.campaign_operation.create for op in parsed.mutate_operations if op.HasField('campaign_operation')]
    assert len(campaigns)==1 and campaigns[0].status==CampaignStatusEnum.CampaignStatus.ENABLED
    assert len(parsed.mutate_operations)==9
    assert all(op.WhichOneof('operation') for op in parsed.mutate_operations)
    goals=[op.campaign_conversion_goal_operation for op in parsed.mutate_operations if op.HasField('campaign_conversion_goal_operation')]
    assert len(goals)==1 and '/campaignConversionGoals/-' in goals[0].update.resource_name
    assert list(goals[0].update_mask.paths)==['biddable']
    assert result['funded_dispatch_authorized'] is False


def test_official_oracle_rejects_unknown_wire_field():
    result=funded.prepare_google_funded_creation_intent(**inputs());result['request']['mutateOperations'][0]['hidden']=True
    with pytest.raises(ParseError):ParseDict({'customerId':'123',**result['request']},MutateGoogleAdsRequest.pb(MutateGoogleAdsRequest()),ignore_unknown_fields=False)
