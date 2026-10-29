"""Actual PAUSED creation reader, required GAQL fields and official v25 schema."""
from copy import deepcopy
import pytest
from google.ads.googleads.v25.resources.types.campaign import Campaign
from google.ads.googleads.v25.resources.types.campaign_budget import CampaignBudget
from integrations_control_center import google_ads_automation_creation_plan as plan
from tests.test_google_ads_automation_creation_plan import Native
from tests.test_google_ads_automation_creation_contract import prepared_case, NOW

FIELDS={'campaign.campaign_budget','campaign_budget.resource_name','campaign_budget.explicitly_shared','campaign_budget.reference_count'}
class BudgetNative(Native):
    def __init__(self,mode=None):super().__init__();self.mode=mode;self.requested=[]
    async def field_metadata(self,fields):
        self.requested.extend(fields)
        return {field:{'selectable':True} for field in fields if not(self.mode=='unselectable' and field=='campaign_budget.reference_count')}
    async def search(self,query):
        rows,complete=await super().search(query)
        if 'FROM campaign ' not in query:return rows,complete
        rows=deepcopy(rows)
        for raw in rows:
            resource='customers/123/campaignBudgets/'+raw['campaign']['id']
            raw['campaign']['campaignBudget']=resource
            raw['campaignBudget'].update(resourceName=resource,explicitlyShared=False,referenceCount='1')
        budget=rows[0]['campaignBudget']
        if self.mode=='missing_resource':budget.pop('resourceName')
        elif self.mode=='foreign_resource':budget['resourceName']='customers/999/campaignBudgets/15'
        elif self.mode=='conflicting_resource':rows[0]['campaign']['campaignBudget']='customers/123/campaignBudgets/16'
        elif self.mode=='shared':budget['explicitlyShared']=True
        elif self.mode=='missing_shared':budget.pop('explicitlyShared')
        elif self.mode=='multiple_references':budget['referenceCount']='2'
        elif self.mode=='missing_references':budget.pop('referenceCount')
        elif self.mode=='bool_reference':budget['referenceCount']=True
        elif self.mode=='period':rows[-1]['campaignBudget']['period']='CUSTOM_PERIOD'
        elif self.mode=='bad_amount':rows[-1]['campaignBudget']['amountMicros']='-1'
        elif self.mode=='duplicate_budget':rows[1]['campaignBudget']['resourceName']=rows[0]['campaignBudget']['resourceName'];rows[1]['campaign']['campaignBudget']=rows[0]['campaign']['campaignBudget']
        elif self.mode=='over_bound':rows=rows*167
        elif self.mode=='malformed_campaign':rows[0]['campaign']='not_an_object'
        elif self.mode=='malformed_budget':rows[0]['campaignBudget']='not_an_object'
        elif self.mode=='bool_amount':budget['amountMicros']=True
        elif self.mode=='float_amount':budget['amountMicros']=1.5
        return rows,complete

async def read(native):
    return await plan.read_google_creation_native_context(native,user_id='owner',account_id='123',policy=prepared_case()['policy'],read_started_at=NOW)

def test_required_budget_fields_exist_in_official_v25_schema():
    assert {'campaign_budget'} <= set(Campaign.pb(Campaign()).DESCRIPTOR.fields_by_name)
    assert {'resource_name','explicitly_shared','reference_count','period','amount_micros'} <= set(CampaignBudget.pb(CampaignBudget()).DESCRIPTOR.fields_by_name)

@pytest.mark.asyncio
async def test_actual_reader_selects_required_unique_native_budget_proof_without_mutation():
    native=BudgetNative();data=await read(native)
    query=next(q for q in native.queries if 'FROM campaign ' in q)
    assert FIELDS <= set(native.requested) and all(field in query for field in FIELDS)
    assert data['complete'] is True and len(data['campaigns'])==3
    assert all(row['shared_budget'] is False and row['budget_resource'].startswith('customers/123/campaignBudgets/') for row in data['campaigns'])
    assert data['observed_at']==NOW.isoformat() and data['account_id']=='123' and data['currency_code']=='SAR'

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['missing_resource','foreign_resource','conflicting_resource','shared','missing_shared','multiple_references','missing_references','bool_reference','period','bad_amount','duplicate_budget','over_bound','unselectable','malformed_campaign','malformed_budget','bool_amount','float_amount'])
async def test_actual_reader_missing_conflicting_or_incomplete_budget_proof_fails_closed(mode):
    try:data=await read(BudgetNative(mode))
    except Exception as error:
        assert mode=='unselectable' and error.status_code in (409,422,503)
    else:assert data['complete'] is False
