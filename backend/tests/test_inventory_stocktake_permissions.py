from accounting_module_contract import accounting_permissions_for_user
from web_page_access import PAGES


def test_native_accounting_page_declares_count_history_aliases_only():
    page = next(r for r in PAGES if r['id'] == 'accounting.inventory.view')
    assert set(page['aliases']) == {'/inventory-v2?tab=stocktake', '/inventory-v2?tab=adjustments', '/inventory-v2?tab=movements'}
    assert '/inventory-v2?tab=opening' not in page['aliases']


def test_owner_needs_explicit_review_and_post_grants():
    permissions = set(accounting_permissions_for_user({'id': 'owner', 'role': 'owner'}))
    assert 'accounting.inventory.count' in permissions
    assert 'accounting.inventory.adjustments.review' not in permissions
    assert 'accounting.inventory.adjustments.post' not in permissions


def test_managed_read_page_never_activates_assigned_count_actions():
    actions = ['accounting.inventory.count', 'accounting.inventory.adjustments.review', 'accounting.inventory.adjustments.post']
    actor = {'id': 'staff', 'role': 'employee', 'created_by': 'owner', 'accounting_permissions': actions,
             'web_page_access': {'owner_id': 'owner', 'grants': [{'id': 'accounting.inventory.view', 'mode': 'read'}]}}
    assert accounting_permissions_for_user(actor) == ['accounting.inventory.view']
    actor['web_page_access']['grants'][0]['mode'] = 'write'
    assert set(accounting_permissions_for_user(actor)) == set(actions + ['accounting.inventory.view'])
