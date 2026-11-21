"""Warehouse compatibility for the existing independent inventory writer.

Warehouses created before inventory_scope existed retain their identities and
metadata. Their locations use the same occupancy, admission and pending-value
checks as new independent warehouses. An explicit scope never falls back.
"""


def allows_standalone_entry(warehouse):
    return (isinstance(warehouse, dict)
            and warehouse.get('status') == 'active'
            and not warehouse.get('deleted_at')
            and ('inventory_scope' not in warehouse
                 or warehouse['inventory_scope'] == 'standalone'))
