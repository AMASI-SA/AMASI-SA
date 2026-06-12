"""Iter-156 — Salla Settlements page backend tests.

The Salla parser already existed; this iteration adds an analytics
endpoint that powers the new SallaSettlements.jsx page.
"""
import os
import uuid
import io

import pytest
import requests
import openpyxl


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"i156-{suffix}@example.com"
    pwd = "T#156abcD"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I156"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    yield {"hdr": {"Authorization": f"Bearer {r.json()['access_token']}"}}


def _make_salla_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice # 9999001"
    ws.append([
        "رقم الطلب", "إجمالي الطلب (ر.س)", "طريقة الدفع",
        "الرسوم (ر.س)", "المستحق قبل الضريبة (ر.س)",
        "الضريبة", "المستحق بعد الضريبة (ر.س)",
    ])
    # 3 sales — mada, credit card, apple pay
    ws.append(["265094843", 755.08, "مدى", 8.55, 746.53, 1.28, 745.25])
    ws.append(["265099462", 226.04, "البطاقة الائتمانية", 6.26, 219.78, 0.94, 218.84])
    ws.append(["265100363", 126.55, "أبل باي", 3.27, 123.28, 0.49, 122.79])
    # 1 refund
    ws.append(["261453456", -143.64, "مدى", 0.0, -143.64, 0.0, -143.64])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_salla_analytics_empty(ctx):
    r = requests.get(f"{BASE_URL}/api/payment-settlements/_analytics/salla",
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["totals"]["files"] == 0
    assert body["per_method"] == []
    assert body["files"] == []


def test_salla_upload_and_analytics_aggregates_per_method(ctx):
    xlsx_bytes = _make_salla_xlsx()
    files = {"file": ("salla_test.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    data = {"provider_hint": "salla"}
    r = requests.post(
        f"{BASE_URL}/api/payment-settlements/upload",
        headers=ctx["hdr"], files=files, data=data, timeout=20,
    )
    assert r.status_code == 200, r.text
    upload = r.json()
    assert upload["provider"] == "salla"
    assert upload["rows"] == 4  # 3 sales + 1 refund (no wallet recharge)

    # Pull analytics
    r2 = requests.get(f"{BASE_URL}/api/payment-settlements/_analytics/salla",
                      headers=ctx["hdr"], timeout=10)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["totals"]["files"] == 1
    methods = {m["payment_method"]: m for m in body["per_method"]}
    # mada has one sale + one refund
    assert "mada" in methods
    mada = methods["mada"]
    assert mada["count"] == 2  # sale + refund row
    # Credit card: 1 sale
    assert "credit_card" in methods
    cc = methods["credit_card"]
    assert cc["count"] == 1
    assert cc["fees"] == 6.26
    # Apple Pay: 1 sale
    assert "apple_pay" in methods
    ap = methods["apple_pay"]
    assert ap["count"] == 1
    assert ap["fees"] == 3.27
    # Refund tracking
    assert body["totals"]["refund_full"] > 0 or body["totals"]["refund_partial"] > 0


def test_salla_analytics_excludes_other_providers(ctx):
    """Files from tabby/tamara must NOT appear in /_analytics/salla."""
    # Upload a Salla file
    xlsx_bytes = _make_salla_xlsx()
    requests.post(
        f"{BASE_URL}/api/payment-settlements/upload",
        headers=ctx["hdr"],
        files={"file": ("salla.xlsx", xlsx_bytes,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"provider_hint": "salla"}, timeout=20,
    )
    r = requests.get(f"{BASE_URL}/api/payment-settlements/_analytics/salla",
                     headers=ctx["hdr"], timeout=10)
    body = r.json()
    # All file entries must have provider=salla
    for f in body["files"]:
        assert f["provider"] == "salla"
