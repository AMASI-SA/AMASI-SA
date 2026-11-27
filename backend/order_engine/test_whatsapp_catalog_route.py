"""No-network Salla public catalog projection and signed route acceptance."""
import hashlib
import hmac
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.order_engine.whatsapp_catalog_route import (
    public_salla_product, make_whatsapp_catalog_router, InvalidProduct
)
from backend.order_engine.whatsapp_stage_service_auth import CATALOG_PATH

NOW=1800000000
KEY=b"K"*32
NONCE="abcdefgh1234567890abcdefgh"
KID="key1"


def product(**changes):
    row={
        "id":963652299,
        "name":"سوار بالاسم وزهرة لامعة",
        "price":{"amount":99,"currency":"SAR"},
        "pre_tax_price":{"amount":99,"currency":"SAR"},
        "tax":{"amount":7.92,"currency":"SAR"},
        "taxed_price":{"amount":106.92,"currency":"SAR"},
        "status":"sale","quantity":4,"unlimited_quantity":False,
        "urls":{"customer":"https://amasi-sa.com/ar/swar/p963652299"},
        "main_image":"https://cdn.salla.sa/product.png",
        "images":[{"url":"https://cdn.salla.sa/product.png"}],
        "options":[],
        "supplier_cost":45,
        "customer_mobile":"private",
    }
    row.update(changes)
    return row


def signed(body):
    ts=str(NOW)
    msg="\n".join(("POST",CATALOG_PATH,ts,NONCE,KID,
                   hashlib.sha256(body).hexdigest())).encode()
    return {
        "x-amasi-key-id":KID,"x-amasi-nonce":NONCE,
        "x-amasi-timestamp":ts,
        "x-amasi-signature":hmac.new(KEY,msg,hashlib.sha256).hexdigest(),
        "content-type":"application/json"
    }


class SallaCatalogTest(unittest.TestCase):
    def test_public_product_excludes_internal_fields(self):
        info=public_salla_product(product(),fetched_at=NOW,detailed=True)
        self.assertEqual(info["base_price_sar"],"99.00")
        self.assertEqual(info["image_urls"],["https://cdn.salla.sa/product.png"])
        self.assertEqual(info["display_price_sar"],"106.92")
        self.assertTrue(info["price_includes_tax"])
        self.assertTrue(info["options_verified"])
        self.assertNotIn("supplier_cost",str(info))
        self.assertNotIn("customer_mobile",str(info))

    def test_salla_discount_price_not_free_zero(self):
        sale=public_salla_product(
            product(sale_price={"amount":80,"currency":"SAR"},
                    pre_tax_price={"amount":80,"currency":"SAR"},
                    tax={"amount":6.40,"currency":"SAR"},
                    taxed_price={"amount":86.40,"currency":"SAR"}),
            fetched_at=NOW,detailed=True
        )
        self.assertEqual(sale["base_price_sar"],"99.00")
        self.assertEqual(sale["display_price_sar"],"86.40")
        zero=public_salla_product(
            product(sale_price={"amount":0,"currency":"SAR"}),
            fetched_at=NOW,detailed=True
        )
        self.assertEqual(zero["display_price_sar"],"106.92")

    def test_real_salla_detail_field_shapes_with_options(self):
        # Same public field structure verified against Amasi Salla read API.
        row=product(
            quantity=None, unlimited_quantity=True,
            url="https://amasi-sa.com/ar/AMS11847-سوار-بالاسم-وزهرة-لامعة-مخصص",
            urls={"customer":"https://amasi-sa.com/ar/AMS11847-سوار-بالاسم-وزهرة-لامعة-مخصص"},
            sale_price={"amount":99,"currency":"SAR"},
            options=[
                {"id":858952591,"name":"الاسم","type":"text",
                 "purpose":"form","required":False,"visibility":"always","values":[]},
                {"id":220181128,"name":"اللون","type":"radio",
                 "purpose":"form","required":True,"visibility":"always",
                 "values":[
                     {"id":245832621,"name":"ذهبي",
                      "price":{"amount":0,"currency":"SAR"},"is_out_of_stock":False},
                     {"id":1485585070,"name":"فضي",
                      "price":{"amount":0,"currency":"SAR"},"is_out_of_stock":False}
                 ]}
            ]
        )
        result=public_salla_product(row,fetched_at=NOW,detailed=True)
        self.assertTrue(result["options_verified"])
        self.assertEqual(result["display_price_sar"],"106.92")
        self.assertEqual(result["inventory_hint"],"unlimited")
        self.assertEqual(len(result["options"]),2)
        self.assertFalse(result["options"][0]["required"])
        self.assertTrue(result["options"][1]["required"])
        self.assertEqual(len(result["options"][1]["values"]),2)
        self.assertEqual(result["options"][1]["values"][0]["name"],"ذهبي")
        self.assertNotIn("supplier_cost",str(result))

    def test_official_salla_image_field_without_main_image(self):
        info=public_salla_product(
            product(main_image=None,images=[{
                "image":"https://cdn.salla.sa/salla-gallery.jpg",
                "thumbnail":"https://cdn.salla.sa/salla-thumbnail.jpg"
            }]),fetched_at=NOW,detailed=True
        )
        self.assertEqual(info["image_urls"],[
            "https://cdn.salla.sa/salla-gallery.jpg"
        ])

    def test_untrusted_gallery_image_remains_blocked(self):
        with self.assertRaises(Exception):
            public_salla_product(
                product(main_image=None,images=[{
                    "image":"https://other.invalid/gallery.jpg"
                }]),fetched_at=NOW,detailed=True
            )

    def test_real_salla_100_pre_tax_108_storefront(self):
        result=public_salla_product(product(
            price={"amount":100,"currency":"SAR"},
            pre_tax_price={"amount":100,"currency":"SAR"},
            tax={"amount":8,"currency":"SAR"},
            taxed_price={"amount":108,"currency":"SAR"},
            sale_price={"amount":0,"currency":"SAR"},
        ),fetched_at=NOW,detailed=True)
        self.assertEqual(result["base_price_sar"],"100.00")
        self.assertEqual(result["display_price_sar"],"108.00")
        self.assertIs(result["price_includes_tax"],True)
        self.assertNotIn("supplier_cost",str(result))

    def test_unverified_or_inconsistent_storefront_tax_denied(self):
        for change in (
            {"taxed_price":None},
            {"taxed_price":{"amount":99,"currency":"USD"}},
            {"taxed_price":{"amount":100,"currency":"SAR"}},
            {"tax":{"amount":-1,"currency":"SAR"}},
            {"taxed_price":{"amount":"NaN","currency":"SAR"}},
        ):
            with self.subTest(change=change),self.assertRaises(InvalidProduct):
                public_salla_product(product(**change),fetched_at=NOW,detailed=True)

    def test_unrecognized_options_not_claimed(self):
        row=product(options=[{"unknown_option":"unverified"}])
        result=public_salla_product(row,fetched_at=NOW,detailed=True)
        self.assertFalse(result["options_verified"])
        self.assertEqual(result["options"],[])

    def test_hidden_foreign_image_or_currency_rejected(self):
        for change in ({"status":"hidden"},
                       {"main_image":"http://127.0.0.1/private"},
                       {"price":{"amount":99,"currency":"USD"}}):
            with self.subTest(change=change),self.assertRaises(Exception):
                public_salla_product(product(**change),fetched_at=NOW,detailed=True)

    def test_signed_catalog_search_no_order_or_pii(self):
        fake=AsyncMock(return_value={
            "success":True,"data":[product()]})
        app=FastAPI()
        app.include_router(make_whatsapp_catalog_router(
            db=object(),fixed_owner_id="test_tenant",active_keys={KID:KEY},
            claim_nonce=lambda *_:True,now_fn=lambda:NOW),prefix="/api")
        data=json.dumps({"action":"search","keyword":"سوار"},
                         separators=(",",":")).encode()
        with patch("backend.order_engine.whatsapp_catalog_route.call_salla",fake):
            response=TestClient(app).post(CATALOG_PATH,content=data,headers=signed(data))
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(len(result["products"]),1)
        self.assertNotIn("customer_mobile",str(result))
        self.assertNotIn("supplier_cost",str(result))
        self.assertEqual(fake.await_args.args[1],"test_tenant")
        self.assertEqual(fake.await_args.args[2],"GET")
        self.assertIn("keyword=",fake.await_args.args[3])

    def test_signature_blocks_provider_lookup(self):
        fake=AsyncMock()
        app=FastAPI()
        app.include_router(make_whatsapp_catalog_router(
            db=object(),fixed_owner_id="test_tenant",active_keys={KID:KEY},
            claim_nonce=lambda *_:True,now_fn=lambda:NOW),prefix="/api")
        data='{"action":"search","keyword":"سوار"}'.encode("utf-8")
        bad=signed(data)
        bad["x-amasi-signature"]="0"*64
        with patch("backend.order_engine.whatsapp_catalog_route.call_salla",fake):
            resp=TestClient(app).post(CATALOG_PATH,content=data,headers=bad)
        self.assertEqual(resp.status_code,401)
        fake.assert_not_awaited()

if __name__=="__main__":
    unittest.main()
