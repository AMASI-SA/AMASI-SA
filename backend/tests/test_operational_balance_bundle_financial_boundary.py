"""Financial-only pricing is not a received/unreceived physical unit.

Fail closed until the operational adapter explicitly links supplemental base
and service proof to the unchanged legacy supplier receipt identities.
"""
from copy import deepcopy
import pytest
from test_operational_bundle_cost_projection import project, snapshot


@pytest.mark.parametrize('report', [None, {}, {'version':'bundle-reporting-cost-v1', 'complete':True},
    {'version':'bundle-reporting-cost-v1', 'added_bases':[{'unit_cost_sar':'15'}], 'components':[]}])
def test_unmapped_financial_envelope_cannot_publish_a_partial_recipe_total(report):
    source=snapshot();source['reporting_cost']=report;before=deepcopy(source)
    result=project(source)
    assert not result['complete']
    assert result['recipe_cost_sar'] is None and result['unreceived_expected_sar'] is None
    assert result['issues']==[{'code':'bundle_financial_policy_operational_mapping_required'}]
    assert not result['posting_allowed'] and source==before
    assert project(source)==result


def test_physical_recipe_without_financial_supplement_keeps_exact_existing_cost():
    result=project(snapshot())
    assert result['complete'] and result['unreceived_expected_sar']=='220.00'
    assert not result['posting_allowed']
