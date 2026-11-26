"""Synthetic internal order-stage route tests; no real customer, Salla, Chatwoot."""
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.order_engine.whatsapp_stage_route import make_whatsapp_stage_router

NOW=1800000000
TS=str(NOW)
KEY=b"S"*32
PATH="/api/internal/whatsapp/order-stage/read"
KID="test-key"
NONCE="abcdefghijklmnopqrstuvwx"

def body(order="301153281", conversation=10, message=20):
    return json.dumps({"order_number":order,"conversation_id":conversation,
                       "message_id":message},separators=(",",":")).encode()

def signed(data,nonce=NONCE):
    msg="\n".join(("POST",PATH,TS,nonce,KID,hashlib.sha256(data).hexdigest()))
    return {"x-amasi-timestamp":TS,"x-amasi-nonce":nonce,"x-amasi-key-id":KID,
            "x-amasi-signature":hmac.new(KEY,msg.encode(),hashlib.sha256).hexdigest(),
            "content-type":"application/json"}

def fake_order(phone="0550000001",status="قيد التنفيذ"):
    return SimpleNamespace(order_number="301153281",status_native=status,
       customer=SimpleNamespace(mobile=phone),
       engine_updated_at=datetime.fromtimestamp(NOW-100,timezone.utc))

class OrderStageRouteTest(unittest.TestCase):
    def make_client(self, *,sender="+966550000001",fresh=True):
        async def verified(conversation,message):
            if conversation!=10 or message!=20:
                raise ValueError("not latest or not bot")
            return sender
        async def freshness(dto):
            return fresh
        seen=set()
        def claim(nonce,ttl):
            if nonce in seen:
                return False
            seen.add(nonce)
            return True
        app=FastAPI()
        app.include_router(make_whatsapp_stage_router(
            repository=object(),fixed_owner_id="fixed_test_tenant",
            active_keys={KID:KEY},claim_nonce=claim,
            resolve_verified_sender=verified,source_freshness=freshness,
            now_fn=lambda:NOW),prefix="/api")
        return TestClient(app)

    def test_verified_only_minimal_status_and_replay(self):
        client=self.make_client()
        data=body()
        with patch("backend.order_engine.whatsapp_stage_route.get_order",
                   return_value=fake_order()) as reader:
            response=client.post(PATH,content=data,headers=signed(data))
            self.assertEqual(response.status_code,200)
            payload=response.json()
            self.assertEqual(payload["stage"],"preparing")
            self.assertNotIn("customer",str(payload))
            reader.assert_awaited_once()
            self.assertEqual(reader.await_args.kwargs["user_id"],"fixed_test_tenant")
            retry=client.post(PATH,content=data,headers=signed(data))
            self.assertEqual(retry.status_code,401)

    def test_wrong_buyer_and_nonlatest_uniform_denial(self):
        for sender,data in (("+966550000002",body()),("+966550000001",body(message=21))):
            with self.subTest(sender=sender,data=data):
                client=self.make_client(sender=sender)
                with patch("backend.order_engine.whatsapp_stage_route.get_order",
                           return_value=fake_order()):
                    response=client.post(PATH,content=data,headers=signed(data))
                self.assertEqual(response.status_code,404)
                self.assertEqual(response.json()["detail"],"verification_required")

    def test_stale_upstream_never_reported_as_current(self):
        client=self.make_client(fresh=False)
        data=body()
        with patch("backend.order_engine.whatsapp_stage_route.get_order",
                   return_value=fake_order()):
            response=client.post(PATH,content=data,headers=signed(data))
        self.assertEqual(response.status_code,404)

    def test_bad_signature_no_lookup(self):
        data=body()
        headers=signed(data)
        headers["x-amasi-signature"]="0"*64
        client=self.make_client()
        with patch("backend.order_engine.whatsapp_stage_route.get_order") as reader:
            response=client.post(PATH,content=data,headers=headers)
        self.assertEqual(response.status_code,401)
        reader.assert_not_called()

if __name__=="__main__":
    unittest.main()
