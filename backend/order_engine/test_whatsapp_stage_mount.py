"""No-network opt-in Mezan WhatsApp stage mount regression."""
import asyncio
import os
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import APIRouter
from backend.order_engine.whatsapp_stage_mount import mount_if_enabled, _fresh_salla_source

class MountTest(unittest.TestCase):
    def test_disabled_has_no_side_effects(self):
        with patch.dict(os.environ, {"AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"0"}):
            with patch("backend.order_engine.whatsapp_stage_mount.MongoNonceClaim") as nonce:
                router=APIRouter()
                self.assertFalse(mount_if_enabled(router,object()))
                self.assertEqual(router.routes,[])
                nonce.assert_not_called()

    def test_catalog_only_does_not_require_chatwoot_buyer_credentials(self):
        env={
            "AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"0",
            "AMASI_WHATSAPP_CATALOG_ENABLED":"1",
            "AMASI_WHATSAPP_STAGE_OWNER_ID":"owner_test",
            "AMASI_WHATSAPP_STAGE_KEY_ID":"key1",
            "AMASI_WHATSAPP_STAGE_KEY_HEX":"ab"*32,
            "MONGO_URL":"mongodb://example.invalid:27017",
            "DB_NAME":"test",
        }
        with patch.dict(os.environ,env,clear=True), \
             patch("backend.order_engine.whatsapp_stage_mount.MongoNonceClaim") as nonce, \
             patch("backend.order_engine.whatsapp_stage_mount.MongoAssistantRateLimit") as limit, \
             patch("backend.order_engine.whatsapp_stage_mount.ChatwootVerifiedSenderReader") as sender:
            router=APIRouter()
            self.assertTrue(mount_if_enabled(router,object()))
            paths={r.path for r in router.routes}
            self.assertIn("/internal/whatsapp/catalog/read",paths)
            self.assertNotIn("/internal/whatsapp/order-stage/read",paths)
            nonce.assert_called_once()
            limit.assert_called_once()
            sender.assert_not_called()

    def test_catalog_only_does_not_mount_without_signed_credentials(self):
        with patch.dict(os.environ,{
            "AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"0",
            "AMASI_WHATSAPP_CATALOG_ENABLED":"1"},clear=True):
            with self.assertRaisesRegex(RuntimeError,"missing_whatsapp_readonly_configuration"):
                mount_if_enabled(APIRouter(),object())

    def test_invalid_catalog_flag_denied(self):
        with patch.dict(os.environ,{
            "AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"0",
            "AMASI_WHATSAPP_CATALOG_ENABLED":"true"},clear=True):
            with self.assertRaisesRegex(RuntimeError,"invalid_whatsapp_readonly_flag"):
                mount_if_enabled(APIRouter(),object())

    def test_legacy_stage_enabled_still_requires_chatwoot_identity_token(self):
        with patch.dict(os.environ,{
            "AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"1",
            "AMASI_WHATSAPP_CATALOG_ENABLED":"0",
            "AMASI_WHATSAPP_STAGE_OWNER_ID":"owner_test",
            "AMASI_WHATSAPP_STAGE_KEY_ID":"key1",
            "AMASI_WHATSAPP_STAGE_KEY_HEX":"ab"*32,
            "MONGO_URL":"mongodb://example.invalid:27017",
            "DB_NAME":"test",
        },clear=True):
            with self.assertRaisesRegex(RuntimeError,"missing_whatsapp_readonly_configuration"):
                mount_if_enabled(APIRouter(),object())

    def test_enabled_fails_on_missing_secrets(self):
        with patch.dict(os.environ, {"AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"1"},
                        clear=True):
            with self.assertRaisesRegex(RuntimeError,"missing_whatsapp_readonly_configuration"):
                mount_if_enabled(APIRouter(),object())

    def test_source_freshness_is_independent_of_local_engine_updated_at(self):
        now=datetime.now(timezone.utc)
        source=lambda ts:SimpleNamespace(source=SimpleNamespace(fetched_at=ts))
        self.assertTrue(asyncio.run(_fresh_salla_source(source(now-timedelta(minutes=2)))))
        self.assertFalse(asyncio.run(_fresh_salla_source(source(now-timedelta(days=3)))))
        self.assertFalse(asyncio.run(_fresh_salla_source(source(None))))

if __name__=="__main__":
    unittest.main()
