"""Playwright UI Test for Iter-151 Scenario C retest."""
import asyncio
import os
import uuid
import datetime as dt
import requests

BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
).rstrip("/")


def setup_via_api():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter151c-{suffix}@test.com"
    pwd = "TestPass123!"
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"email": email, "password": pwd, "name": "Iter151C"}, timeout=15)
    print(f"REGISTER: {r.status_code}")
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=15)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    print(f"LOGIN: token len={len(token)}")

    # bank
    bank = requests.post(f"{BASE_URL}/api/accounts",
                        json={"name": "Test Bank", "account_type": "bank",
                              "opening_balance": 100000.0},
                        headers=hdr, timeout=10).json()
    print(f"BANK: {bank.get('id')}")

    start = (dt.date.today() - dt.timedelta(days=400)).isoformat()
    # khalid daily
    khalid = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                          json={"name": "خالد", "category": "employee",
                                "monthly_amount": 3000.0,
                                "accrual_mode": "daily",
                                "start_date": start,
                                "status": "active"},
                          headers=hdr, timeout=10).json()
    print(f"KHALID: {khalid.get('id')}")
    # abu khalid monthly
    abu = requests.post(f"{BASE_URL}/api/operating-expenses/salaries",
                       json={"name": "ابو خالد", "category": "employee",
                             "monthly_amount": 4000.0,
                             "accrual_mode": "monthly",
                             "start_date": "2024-01-01",
                             "status": "active"},
                       headers=hdr, timeout=10).json()
    print(f"ABU KHALID: {abu.get('id')}")

    # generate-salaries
    gen = requests.post(f"{BASE_URL}/api/liabilities/generate-salaries",
                       headers=hdr, timeout=20)
    print(f"GEN-SALARIES: {gen.status_code}")

    # set khalid's accrual mode to daily (in case generate-salaries created differently)
    liabs_resp = requests.get(f"{BASE_URL}/api/liabilities",
                        headers=hdr, timeout=10).json()
    liabs = liabs_resp.get("items", []) if isinstance(liabs_resp, dict) else liabs_resp
    print(f"LIABS count: {len(liabs)}; sample: {liabs[0] if liabs else 'none'}")
    khalid_liab = None
    for l in liabs:
        if l.get("employee_salary_id") == khalid["id"] and l.get("kind") == "salary":
            khalid_liab = l
            break
    if not khalid_liab:
        print(f"All liabs: {liabs}")
        raise RuntimeError("khalid_liab not found")
    print(f"KHALID LIAB: id={khalid_liab.get('id')} expected={khalid_liab.get('expected_amount')}")

    requests.put(f"{BASE_URL}/api/liabilities/{khalid_liab['id']}/accrual-mode",
                json={"mode": "daily", "start_date": start},
                headers=hdr, timeout=10)

    # Refresh liability to get updated expected_amount after daily accrual
    liabs2_resp = requests.get(f"{BASE_URL}/api/liabilities",
                         headers=hdr, timeout=10).json()
    liabs2 = liabs2_resp.get("items", []) if isinstance(liabs2_resp, dict) else liabs2_resp
    for l in liabs2:
        if l.get("id") == khalid_liab["id"]:
            khalid_liab = l
            break
    remaining = khalid_liab.get("remaining_amount") or (
        khalid_liab.get("expected_amount", 0) - khalid_liab.get("paid_amount", 0)
    )
    print(f"KHALID LIAB remaining after accrual: {remaining}")

    # pay khalid liability in full
    pay = requests.post(f"{BASE_URL}/api/liabilities/{khalid_liab['id']}/pay",
                       json={"amount": remaining,
                             "paid_from_account_id": bank["id"],
                             "payment_date": dt.date.today().isoformat(),
                             "notes": "Full payment"},
                       headers=hdr, timeout=15)
    print(f"PAY: {pay.status_code} {pay.text[:200] if pay.status_code >= 400 else 'OK'}")

    return {"email": email, "password": pwd, "token": token,
            "khalid_id": khalid["id"], "bank_id": bank["id"]}


ctx = setup_via_api()
print(f"=== SETUP DONE === {ctx['email']}")
