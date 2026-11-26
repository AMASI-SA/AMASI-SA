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

    def test_enabled_fails_on_missing_secrets(self):
        with patch.dict(os.environ, {"AMASI_WHATSAPP_ORDER_STAGE_ENABLED":"1"},
                        clear=True):
            with self.assertRaisesRegex(RuntimeError,"missing_whatsapp_stage_configuration"):
                mount_if_enabled(APIRouter(),object())

    def test_source_freshness_is_independent_of_local_engine_updated_at(self):
        now=datetime.now(timezone.utc)
        source=lambda ts:SimpleNamespace(source=SimpleNamespace(fetched_at=ts))
        self.assertTrue(asyncio.run(_fresh_salla_source(source(now-timedelta(minutes=2)))))
        self.assertFalse(asyncio.run(_fresh_salla_source(source(now-timedelta(days=3)))))
        self.assertFalse(asyncio.run(_fresh_salla_source(source(None))))

if __name__=="__main__":
    unittest.main()
