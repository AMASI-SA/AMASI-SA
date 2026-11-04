"""Read supplementary native GETs and the separately labeled browser assembly board."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Request
from motor.motor_asyncio import AsyncIOMotorClient
from ai_store_access_contract import ROLE_ASSIGNMENTS
from fulfillment_v2_routes import make_fulfillment_v2_router
from product_costs import attach_product_costs_routes
import probe

async def run(port, sha):
    probe.bench.limit_network(port)
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=6000)
    try:
        db = client.supplementary_native_fixture
        assert await db.list_collection_names() == []
        await probe.prepare(db)
        await db.unified_orders.update_one({"order_number": "202"}, {"$set": {"products": [
            {"sku": "TEST", "name": "Synthetic catalog item", "quantity": 2, "price": 80}]}})
        await db.unified_orders.insert_one(probe.order("404", current="in_progress", native="قيد التنفيذ"))
        await db[probe.WORKFLOWS].insert_one({"user_id": probe.OWNER, "order_number": "404",
                                               "stage": "ready_to_ship", "ready_to_ship_source": "preparation_receipt"})
        await db.users.insert_many([{"id":"reviewed-sales", "created_by":probe.OWNER, "role":"employee"},
                                    {"id":"web-shipping", "created_by":probe.OWNER, "role":"viewer"}])
        await db[probe.MOBILE_APP_ACCESS].insert_one({"owner_user_id":probe.OWNER, "user_id":"reviewed-sales",
            "enabled":True, "permissions":["app.page.reviewed_preparation", "app.page.products"]})
        await db[ROLE_ASSIGNMENTS].insert_one({"owner_user_id":probe.OWNER,"user_id":"web-shipping","enabled":True,
            "role_key":"shipping_operator","fulfillment_responsibilities":["instant_ready","shipping_labeling"]})
        app = probe.make_app(db)
        async def actor(request: Request):
            user = await db.users.find_one({"id":request.headers["x-fixture-employee"]},{"_id":0})
            if request.url.path.endswith("/ready-to-ship"): return user  # Browser session after authentication.
            return await probe.mobile_app_request_user(db,{**user,"_session_client":"amasi_mobile"},
                path=request.url.path,method=request.method)
        router = APIRouter()
        attach_product_costs_routes(router, db, actor)
        app.include_router(router,prefix="/api")
        app.include_router(make_fulfillment_v2_router(db, actor), prefix="/api")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://fixture") as http:
            async def read(path, principal, expect=200):
                reply = await http.get(path,headers={"x-fixture-employee":principal})
                assert reply.status_code == expect,(path,reply.status_code,reply.text[:400])
                return reply.json()
            result = []
            for limit in (1,50):
                url=f"/api/order-reviews-v1/reviewed?page=1&limit={limit}"
                body=await read(url,"reviewed")
                assert body["total_count"]==1 and [row["order_number"] for row in body["items"]]==["202"]
                result.append({"path":url,"http":200,"order":"202"})
            url="/api/preparation-work-v1/assembly/orders?state=in_progress&limit=1&offset=0"
            body=await read(url,"assembly")
            assert body["total"]==1 and body["items"][0]["order_number"]=="303"
            result.append({"path":url,"http":200,"order":"303"})
            riyadh_day=datetime.now(ZoneInfo("Asia/Riyadh")).date().isoformat()
            url=f"/api/product-costs/product-sales?from_date=2026-08-01&to_date={riyadh_day}"
            err=await read(url,"reviewed",403)
            assert err["detail"]["code"]=="mobile_app_page_permission_required",err
            data=await read(url,"reviewed-sales")
            assert data["count"]==1 and data["items"][0]["units_sold"]==2 and data["items"][0]["total_sales"]==160,data
            result.append({"path":url,"reviewed_only_http":403,"reviewed_and_products_http":200,"units_sold":2,"sales":160})
            url="/api/fulfillment-v2/ready-to-ship?limit=100"
            body=await read(url,"web-shipping")
            assert [r["order_number"] for r in body["items"]]==["404"] and body["permissions"]["can_print"],body
            await read(url,"blocked",403)
            result.append({"path":url,"web_shipping_employee_http":200,"ungranted_http":403,"order":"404"})
            print(json.dumps({"source_sha":sha,"native_build_sha":"eea242896f3001234b30cf6d552b0e3f394af02a",
                              "validated":result},ensure_ascii=False,indent=2))
    finally:
        client.close()

if __name__ == "__main__":
    probe.local_run(run)
