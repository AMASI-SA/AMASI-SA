"""Expected event representation, not proof of Salla event delivery/schema."""
import copy
import hashlib
import hmac
import json
from order_fixture import ORDER_IDS, order_fixture
from salla_http_simulator import STATUS

OWNER_MERCHANT='exit2d-webhook-owner-store'
OTHER_MERCHANT='exit2d-webhook-other-store'


def signed_body(event, secret):
    if not isinstance(secret,str) or len(secret)<32:
        raise ValueError('WEBHOOK_TEST_SECRET_REQUIRED')
    payload=json.dumps(event,separators=(',',':'),ensure_ascii=True).encode()
    return payload,{'x-salla-signature':hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest(),
                    'x-salla-security-strategy':'Signature'}


def require_ingestion(result):
    capture=result.get('capture') if isinstance(result,dict) else None
    if not isinstance(capture,dict) or capture.get('order_sync',{}).get('synced') is not True:
        raise ValueError('WEBHOOK_NOT_SYNCED')
    sync=capture['order_sync']
    if (sync.get('attribution_ledger',{}).get('synced') is not True
            or capture.get('snapchat_capi',{}).get('reason')!='capi_disabled'):
        raise ValueError('WEBHOOK_ANCILLARY_FAILURE')
    def failed(value):
        if isinstance(value,dict):
            return any(k in ('error','error_type','exception_type') and bool(v) or failed(v)
                       for k,v in value.items())
        if isinstance(value,list):return any(failed(v) for v in value)
        return False
    if failed(capture):raise ValueError('WEBHOOK_ANCILLARY_FAILURE')
    return capture


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
