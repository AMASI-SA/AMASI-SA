"""Real current API load on synthetic data only; sessions stay in memory."""
import asyncio
import json
import statistics
import sys
import time

sys.path[:0] = ["/opt/mezan/backend", "/opt/acceptance"]
from independent_runtime import validate_before_import
validate_before_import("web")
from acceptance import boundary, db, owner_session, employee_session
import httpx


async def measure(owner_cookie, employee_cookie):
    headers = {"Origin": "https://mezansalla.com", "X-Forwarded-Proto": "https"}
    async with httpx.AsyncClient(trust_env=False, timeout=30,
            limits=httpx.Limits(max_connections=24)) as client:
        async def request(path, cookie):
            started = time.perf_counter()
            response = await client.get("http://127.0.0.1:8001" + path,
                headers={**headers, "Cookie": cookie})
            assert response.status_code == 200, "synthetic HTTP load returned non-200"
            if path.startswith("/api/dashboard"):
                assert response.json()["totals"]["orders_make_count"] >= 5000, "report did not exercise seeded orders"
            return (time.perf_counter() - started) * 1000
        reports = [asyncio.create_task(request("/api/dashboard?from_date=2026-09-06&to_date=2026-09-06", owner_cookie)) for _ in range(4)]
        await asyncio.sleep(0.05)
        employees = sorted(await asyncio.gather(*[
            request("/api/auth/me", employee_cookie) for _ in range(20)]))
        report_times = await asyncio.gather(*reports)
    print(json.dumps({"kind": "real_api_synthetic_data_not_production_capacity",
        "synthetic_orders": 5000, "concurrent_report_requests": 4,
        "employee_requests": 20, "all_statuses_200": True,
        "employee_auth_p50_ms": round(statistics.median(employees), 3),
        "employee_auth_p95_ms": round(employees[18], 3),
        "report_max_ms": round(max(report_times), 3)}, sort_keys=True), flush=True)


if __name__ == "__main__":
    boundary()
    database = db()
    owner = database.users.find_one({"email": "admin@hesab.app"})
    assert owner and owner["role"] == "owner"
    # Reset only this synthetic authenticator's replay fixture before obtaining
    # a NEW genuine password+MFA session. No accepted token is fabricated.
    database.users.update_one({"id": owner["id"]}, {"$unset": {"mfa_last_totp_counter": ""}})
    owner_cookie = owner_session()
    employee_cookie = employee_session({"employee": "exit2c-employee"})
    assert not database.unified_orders.find_one({"order_number": "EXIT2D-PERF-00000"})
    database.unified_orders.insert_many([{
        "user_id": owner["id"], "order_number": f"EXIT2D-PERF-{index:05}",
        "order_date": "2026-09-06", "order_status": "completed", "total_amount": 100,
        "payment_method": "مدى", "shipping_company": "Synthetic", "products": [],
        "currency": "SAR", "total_product_cost": 10,
        "data_source": "make", "created_at": "2026-09-06T12:00:00+00:00",
    } for index in range(5000)])
    asyncio.run(measure(owner_cookie, employee_cookie))
