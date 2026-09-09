"""Expected event representation, not proof of Salla event delivery/schema."""
import copy
from order_fixture import ORDER_IDS, order_fixture
from salla_http_simulator import STATUS

OWNER_MERCHANT='exit2d-webhook-owner-store'
OTHER_MERCHANT='exit2d-webhook-other-store'


def confirmed_event(fixture, internal_id, merchant=OWNER_MERCHANT):
    if merchant not in (OWNER_MERCHANT, OTHER_MERCHANT) or internal_id not in ORDER_IDS:
        raise ValueError('EVENT_IDENTITY_REJECTED')
    with fixture.lock:
        status_id=fixture.statuses[internal_id]
        if fixture.mode!='success' or status_id not in STATUS:
            raise ValueError('EVENT_NOT_CONFIRMED')
        payload=order_fixture(internal_id)
        payload['status']=copy.deepcopy(STATUS[status_id])
        return {'event':'order.status.updated','merchant':merchant,'data':payload}
