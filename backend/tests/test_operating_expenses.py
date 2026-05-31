"""Backend tests for the Operating Expenses (المصروفات التشغيلية) feature.

Covers:
- CRUD for salaries / rentals / daily expenses
- Daily-cost derivation (salary/days_in_month, rental/365)
- Status filtering (active vs stopped/expired)
- Summary endpoint shape & math
- Report endpoint daily/monthly/yearly aggregation
- Dashboard integration: net_profit deducts operating expenses,
  and net_sales respects the new deduct_operating_expenses toggle
- 404 on bad ids
- Input validation
"""
import os
import uuid
import calendar
from datetime import datetime, timezone, timedelta

import pytest
import requests


BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://salla-analytics.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"TEST_op_{uuid.uuid4().hex[:8]}@hesab.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "OpUser", "email": email, "password": "test12345"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], email


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def user_token():
    tok, _ = _register()
    return tok


@pytest.fixture(scope="function")
def clean_user_token():
    """Fresh user for each test that needs clean state."""
    tok, _ = _register()
    return tok


# ── 1. Salaries CRUD ──────────────────────────────────────────────────────
def test_salary_create_list_get(user_token):
    payload = {
        "name": "أحمد محاسب",
        "category": "employee",
        "monthly_amount": 3000.0,
        "start_date": "2026-01-01",
        "status": "active",
        "notes": "محاسب رئيسي",
    }
    r = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(user_token), json=payload
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] and body["name"] == "أحمد محاسب"
    assert body["monthly_amount"] == 3000.0

    # List shows it
    rl = requests.get(
        f"{API}/operating-expenses/salaries", headers=_hdr(user_token)
    )
    assert rl.status_code == 200
    items = rl.json()["items"]
    assert any(i["id"] == body["id"] for i in items)


def test_salary_update_and_delete(clean_user_token):
    tok = clean_user_token
    created = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "ل", "category": "household", "monthly_amount": 1000,
              "start_date": "2026-01-01", "status": "active"},
    ).json()

    # Update
    r = requests.put(
        f"{API}/operating-expenses/salaries/{created['id']}",
        headers=_hdr(tok),
        json={"monthly_amount": 1500, "notes": "تم التعديل", "status": "stopped"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["monthly_amount"] == 1500
    assert r.json()["status"] == "stopped"
    assert r.json()["notes"] == "تم التعديل"

    # Delete
    rd = requests.delete(
        f"{API}/operating-expenses/salaries/{created['id']}", headers=_hdr(tok)
    )
    assert rd.status_code == 200

    # 404 on missing
    rd2 = requests.delete(
        f"{API}/operating-expenses/salaries/does-not-exist", headers=_hdr(tok)
    )
    assert rd2.status_code == 404


def test_salary_invalid_category_rejected(clean_user_token):
    r = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(clean_user_token),
        json={"name": "x", "category": "invalid", "monthly_amount": 100,
              "start_date": "2026-01-01"},
    )
    assert r.status_code == 400


def test_salary_country_field_persists_and_breakdown(clean_user_token):
    """Country field (saudi/yemen/other) is stored on creation, can be edited,
    and appears in the summary `by_country` aggregation."""
    tok = clean_user_token

    # Create one Saudi + one Yemen salary
    r1 = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "محمد", "category": "employee", "country": "saudi",
              "monthly_amount": 3000, "start_date": "2026-01-01"},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["country"] == "saudi"

    r2 = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "علي", "category": "employee", "country": "yemen",
              "monthly_amount": 800, "start_date": "2026-01-01"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["country"] == "yemen"

    # Default country (omitted) -> 'saudi'
    r3 = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "افتراضي", "category": "household", "monthly_amount": 500,
              "start_date": "2026-01-01"},
    )
    assert r3.status_code == 200
    assert r3.json()["country"] == "saudi"

    # Invalid country rejected
    bad = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "x", "category": "employee", "country": "iran",
              "monthly_amount": 100, "start_date": "2026-01-01"},
    )
    assert bad.status_code == 400

    # Summary by_country aggregates correctly
    s = requests.get(
        f"{API}/operating-expenses/summary", headers=_hdr(tok)
    ).json()
    by_country = s["salaries"]["by_country"]
    assert by_country["saudi"]["monthly_total"] == 3500  # 3000 + 500
    assert by_country["saudi"]["count"] == 2
    assert by_country["yemen"]["monthly_total"] == 800
    assert by_country["yemen"]["count"] == 1


def test_salary_edit_full_record_persists(clean_user_token):
    """All editable salary fields (name/category/country/amount/start/status/notes)
    persist correctly via PUT."""
    tok = clean_user_token
    created = requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "old", "category": "employee", "country": "saudi",
              "monthly_amount": 1000, "start_date": "2026-01-01",
              "status": "active", "notes": "old note"},
    ).json()

    r = requests.put(
        f"{API}/operating-expenses/salaries/{created['id']}", headers=_hdr(tok),
        json={
            "name": "new",
            "category": "household",
            "country": "yemen",
            "monthly_amount": 2500,
            "start_date": "2026-02-15",
            "status": "stopped",
            "notes": "new note",
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "new"
    assert out["category"] == "household"
    assert out["country"] == "yemen"
    assert out["monthly_amount"] == 2500
    assert out["start_date"] == "2026-02-15"
    assert out["status"] == "stopped"
    assert out["notes"] == "new note"


# ── 2. Rentals CRUD ───────────────────────────────────────────────────────
def test_rental_create_and_validation(clean_user_token):
    tok = clean_user_token

    # Valid creation
    r = requests.post(
        f"{API}/operating-expenses/rentals", headers=_hdr(tok),
        json={"property_name": "مكتب الرياض", "property_type": "office",
              "annual_amount": 36500, "start_date": "2026-01-01",
              "end_date": "2026-12-31", "status": "active"},
    )
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["annual_amount"] == 36500.0

    # Invalid type
    bad = requests.post(
        f"{API}/operating-expenses/rentals", headers=_hdr(tok),
        json={"property_name": "x", "property_type": "garage",
              "annual_amount": 100, "start_date": "2026-01-01",
              "end_date": "2027-01-01"},
    )
    assert bad.status_code == 400

    # End before start
    bad2 = requests.post(
        f"{API}/operating-expenses/rentals", headers=_hdr(tok),
        json={"property_name": "x", "property_type": "office",
              "annual_amount": 100, "start_date": "2026-12-31",
              "end_date": "2026-01-01"},
    )
    assert bad2.status_code == 400

    # Update + delete
    upd = requests.put(
        f"{API}/operating-expenses/rentals/{rid}", headers=_hdr(tok),
        json={"annual_amount": 50000, "status": "expired"},
    )
    assert upd.status_code == 200
    assert upd.json()["annual_amount"] == 50000
    assert upd.json()["status"] == "expired"

    rd = requests.delete(
        f"{API}/operating-expenses/rentals/{rid}", headers=_hdr(tok)
    )
    assert rd.status_code == 200


# ── 3. Daily expenses CRUD ────────────────────────────────────────────────
def test_daily_expense_crud(clean_user_token):
    tok = clean_user_token
    today = datetime.now(timezone.utc).date().isoformat()

    r = requests.post(
        f"{API}/operating-expenses/daily", headers=_hdr(tok),
        json={"date": today, "expense_type": "وقود",
              "description": "تعبئة السيارة", "amount": 75,
              "payment_method": "نقدي"},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["id"]
    assert r.json()["amount"] == 75.0

    # Filter by date range
    rl = requests.get(
        f"{API}/operating-expenses/daily",
        headers=_hdr(tok),
        params={"from_date": today, "to_date": today},
    )
    assert rl.status_code == 200
    items = rl.json()["items"]
    assert any(i["id"] == eid for i in items)

    # Delete
    requests.delete(f"{API}/operating-expenses/daily/{eid}", headers=_hdr(tok))


# ── 4. Summary endpoint math ──────────────────────────────────────────────
def test_summary_aggregates_categories(clean_user_token):
    tok = clean_user_token
    today = datetime.now(timezone.utc).date()
    today_iso = today.isoformat()
    days_in_month = calendar.monthrange(today.year, today.month)[1]

    # Add 3 salaries (one per category) — all active and started long ago
    for name, cat, amt in [
        ("موظف", "employee", 3000),
        ("بيت", "household", 6000),
        ("صدقة", "charity", 600),
    ]:
        requests.post(
            f"{API}/operating-expenses/salaries", headers=_hdr(tok),
            json={"name": name, "category": cat, "monthly_amount": amt,
                  "start_date": "2026-01-01", "status": "active"},
        ).raise_for_status()

    # Add a stopped salary that should NOT count
    requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "متوقف", "category": "employee", "monthly_amount": 100000,
              "start_date": "2026-01-01", "status": "stopped"},
    ).raise_for_status()

    # Add an active rental
    requests.post(
        f"{API}/operating-expenses/rentals", headers=_hdr(tok),
        json={"property_name": "مكتب", "property_type": "office",
              "annual_amount": 36500, "start_date": "2026-01-01",
              "end_date": "2027-01-01", "status": "active"},
    ).raise_for_status()

    # Add an expired rental that should NOT count
    requests.post(
        f"{API}/operating-expenses/rentals", headers=_hdr(tok),
        json={"property_name": "مستودع قديم", "property_type": "warehouse",
              "annual_amount": 999999, "start_date": "2020-01-01",
              "end_date": "2021-01-01", "status": "expired"},
    ).raise_for_status()

    # Add today's daily expense
    requests.post(
        f"{API}/operating-expenses/daily", headers=_hdr(tok),
        json={"date": today_iso, "expense_type": "صيانة", "amount": 50},
    ).raise_for_status()

    s = requests.get(
        f"{API}/operating-expenses/summary", headers=_hdr(tok)
    ).json()

    # Salaries
    assert s["salaries"]["employee_monthly"] == 3000
    assert s["salaries"]["household_monthly"] == 6000
    assert s["salaries"]["charity_monthly"] == 600
    assert s["salaries"]["total_monthly"] == 9600
    assert s["salaries"]["active_count"] == 3

    # Rentals
    assert s["rentals"]["annual_total"] == 36500
    assert s["rentals"]["daily_total"] == round(36500 / 365.0, 2)

    # Today's per-day cost = (sum / days_in_month) + (36500 / 365) + 50
    expected_salaries_daily = round(3000 / days_in_month, 4) \
        + round(6000 / days_in_month, 4) + round(600 / days_in_month, 4)
    expected_total = round(expected_salaries_daily + (36500 / 365.0) + 50, 2)
    # Within ±0.05 SAR (cumulative rounding of three salary categories)
    assert abs(s["today"]["operating_total"] - expected_total) < 0.05
    assert s["today"]["daily_other_total"] == 50.0


# ── 5. Report endpoint ────────────────────────────────────────────────────
def test_report_daily_monthly_yearly(clean_user_token):
    tok = clean_user_token

    # Single active employee salary, 31_000/year = ~84.93/day  (use 31000/year worth of salary)
    requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "x", "category": "employee", "monthly_amount": 1000,
              "start_date": "2026-01-01", "status": "active"},
    ).raise_for_status()

    r = requests.get(f"{API}/operating-expenses/report", headers=_hdr(tok))
    assert r.status_code == 200
    body = r.json()
    assert "daily" in body and "monthly" in body and "yearly" in body

    # Daily total > 0, monthly >= daily, yearly >= monthly
    assert body["daily"]["operating_total"] >= 0
    assert body["monthly"]["operating_total"] >= body["daily"]["operating_total"]
    assert body["yearly"]["operating_total"] >= body["monthly"]["operating_total"]

    # With explicit range (last 7 days)
    today = datetime.now(timezone.utc).date()
    fd = (today - timedelta(days=6)).isoformat()
    td = today.isoformat()
    r2 = requests.get(
        f"{API}/operating-expenses/report",
        headers=_hdr(tok),
        params={"from_date": fd, "to_date": td},
    )
    assert r2.status_code == 200
    assert r2.json()["range"]["from_date"] == fd
    assert r2.json()["range"]["to_date"] == td


# ── 6. Stopped salary excluded from daily breakdown ───────────────────────
def test_stopped_salary_not_in_daily(clean_user_token):
    tok = clean_user_token

    # Add a stopped salary with huge amount
    requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "موقوف", "category": "employee",
              "monthly_amount": 50000, "start_date": "2026-01-01",
              "status": "stopped"},
    ).raise_for_status()

    s = requests.get(
        f"{API}/operating-expenses/summary", headers=_hdr(tok)
    ).json()
    # Stopped salaries are skipped entirely (no monthly, no daily)
    assert s["salaries"]["total_monthly"] == 0
    assert s["today"]["salaries_total_daily"] == 0


# ── 7. Dashboard integration ──────────────────────────────────────────────
def test_dashboard_includes_operating_expenses(clean_user_token):
    tok = clean_user_token

    # Add a salary that contributes ~96.77/day in a 31-day month or ~107.14/day in Feb
    requests.post(
        f"{API}/operating-expenses/salaries", headers=_hdr(tok),
        json={"name": "x", "category": "employee", "monthly_amount": 3000,
              "start_date": "2026-01-01", "status": "active"},
    ).raise_for_status()

    # Today-only filter
    today = datetime.now(timezone.utc).date().isoformat()
    r = requests.get(
        f"{API}/dashboard", headers=_hdr(tok),
        params={"from_date": today, "to_date": today},
    )
    assert r.status_code == 200, r.text
    totals = r.json()["totals"]
    assert "operating_expenses_total" in totals
    assert "operating_salaries_total" in totals
    assert "operating_rentals_total" in totals
    assert "operating_daily_other_total" in totals
    assert totals["operating_expenses_total"] > 0
    # For a freshly registered user with no sales, net_profit becomes negative
    # equal to -operating_expenses_total. This proves the deduction is wired in.
    assert totals["net_profit"] == round(-totals["operating_expenses_total"], 2)


# ── 8. Net Sales toggle integration ───────────────────────────────────────
def test_net_sales_config_deduct_operating_expenses(clean_user_token):
    tok = clean_user_token

    # GET settings — toggle defaults to True
    s = requests.get(f"{API}/settings", headers=_hdr(tok)).json()
    assert s["net_sales_config"].get("deduct_operating_expenses") is True

    # Flip it off
    s["net_sales_config"]["deduct_operating_expenses"] = False
    r = requests.put(
        f"{API}/settings", headers=_hdr(tok),
        json={
            "payment_methods": s["payment_methods"],
            "shipping_companies": s["shipping_companies"],
            "net_sales_config": s["net_sales_config"],
        },
    )
    assert r.status_code == 200

    s2 = requests.get(f"{API}/settings", headers=_hdr(tok)).json()
    assert s2["net_sales_config"]["deduct_operating_expenses"] is False


# ── 9. Prepaid expenses ───────────────────────────────────────────────────
def test_prepaid_crud_and_math(clean_user_token):
    """Full CRUD + daily-cost math for prepaid expenses."""
    tok = clean_user_token

    # Create — 1825 SAR over 365 days = 5 SAR/day exactly
    r = requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "vehicle_insurance", "beneficiary": "ABC-1234",
              "amount": 1825, "start_date": "2026-01-01",
              "end_date": "2026-12-31", "status": "active"},
    )
    assert r.status_code == 200, r.text
    created = r.json()
    assert created["period_days"] == 365  # inclusive
    assert created["daily_cost"] == 5.0

    # List returns derived fields
    rl = requests.get(f"{API}/operating-expenses/prepaid", headers=_hdr(tok))
    items = rl.json()["items"]
    assert any(i["id"] == created["id"] and i["daily_cost"] == 5.0 for i in items)

    # Update — change amount; daily_cost should recompute
    upd = requests.put(
        f"{API}/operating-expenses/prepaid/{created['id']}", headers=_hdr(tok),
        json={"amount": 3650},
    )
    assert upd.status_code == 200
    assert upd.json()["daily_cost"] == 10.0

    # Invalid type rejected
    bad = requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "house_party", "beneficiary": "x",
              "amount": 100, "start_date": "2026-01-01",
              "end_date": "2026-12-31"},
    )
    assert bad.status_code == 400

    # End < start rejected
    bad2 = requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "other", "beneficiary": "x",
              "amount": 100, "start_date": "2026-06-01",
              "end_date": "2026-01-01"},
    )
    assert bad2.status_code == 400

    # Delete
    rd = requests.delete(
        f"{API}/operating-expenses/prepaid/{created['id']}", headers=_hdr(tok)
    )
    assert rd.status_code == 200


def test_prepaid_summary_and_by_type(clean_user_token):
    """Summary endpoint exposes prepaid.total_paid, prepaid.daily_total,
    prepaid.by_type with correctly amortized daily_cost per type."""
    tok = clean_user_token

    today = datetime.now(timezone.utc).date()
    far_past = (today - timedelta(days=30)).isoformat()
    far_future = (today + timedelta(days=335)).isoformat()  # ~1 year window

    # vehicle insurance: 3650 over 366 days ≈ 9.97 SAR/day
    requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "vehicle_insurance", "beneficiary": "9999 ABC",
              "amount": 3650, "start_date": far_past,
              "end_date": far_future, "status": "active"},
    ).raise_for_status()

    # iqama: 1100 over the same window
    requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "iqama_visa", "beneficiary": "علي",
              "amount": 1100, "start_date": far_past,
              "end_date": far_future, "status": "active"},
    ).raise_for_status()

    # expired one — should NOT count
    requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "annual_subscription", "beneficiary": "Old",
              "amount": 999999, "start_date": "2020-01-01",
              "end_date": "2021-01-01", "status": "expired"},
    ).raise_for_status()

    s = requests.get(
        f"{API}/operating-expenses/summary", headers=_hdr(tok)
    ).json()

    p = s["prepaid"]
    assert p["active_count"] == 2
    assert p["total_paid"] == 4750.0  # 3650 + 1100
    assert "vehicle_insurance" in p["by_type"]
    assert "iqama_visa" in p["by_type"]
    assert "annual_subscription" not in p["by_type"]  # expired excluded
    assert p["by_type"]["vehicle_insurance"]["count"] == 1
    assert p["by_type"]["iqama_visa"]["count"] == 1

    # today's prepaid_daily ≈ 9.97 + 3.005 ≈ 12.97 (within rounding)
    today_b = s["today"]
    assert today_b["prepaid_daily"] > 0
    # Should be approximately (3650 + 1100) / 366 = 12.978
    assert abs(today_b["prepaid_daily"] - 4750 / 366) < 0.05


def test_prepaid_in_dashboard_and_report(clean_user_token):
    """Prepaid expenses contribute to dashboard.operating_expenses_total,
    operating_prepaid_total, and to /report's prepaid_total per period."""
    tok = clean_user_token

    today_d = datetime.now(timezone.utc).date()
    today_iso = today_d.isoformat()
    # Single-day window so daily_cost == amount and we know exact value
    requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "iqama_visa", "beneficiary": "أحمد",
              "amount": 100, "start_date": today_iso,
              "end_date": today_iso, "status": "active"},
    ).raise_for_status()

    # Dashboard
    r = requests.get(
        f"{API}/dashboard", headers=_hdr(tok),
        params={"from_date": today_iso, "to_date": today_iso},
    )
    assert r.status_code == 200
    totals = r.json()["totals"]
    assert "operating_prepaid_total" in totals
    assert totals["operating_prepaid_total"] == 100.0
    # operating_expenses_total >= operating_prepaid_total
    assert totals["operating_expenses_total"] >= 100.0

    # Report endpoint exposes prepaid_total + prepaid_by_type
    rep = requests.get(f"{API}/operating-expenses/report", headers=_hdr(tok)).json()
    assert rep["daily"]["prepaid_daily"] == 100.0
    assert rep["daily"]["prepaid_by_type"]["iqama_visa"] == 100.0


def test_prepaid_expired_status_excluded(clean_user_token):
    """A prepaid with status=expired is excluded from per-day cost even if
    today is inside [start_date, end_date]."""
    tok = clean_user_token

    today_d = datetime.now(timezone.utc).date()
    week_ago = (today_d - timedelta(days=7)).isoformat()
    week_ahead = (today_d + timedelta(days=7)).isoformat()

    # Status=expired but date range covers today
    requests.post(
        f"{API}/operating-expenses/prepaid", headers=_hdr(tok),
        json={"expense_type": "vehicle_insurance", "beneficiary": "ZZZ",
              "amount": 14000, "start_date": week_ago,
              "end_date": week_ahead, "status": "expired"},
    ).raise_for_status()

    s = requests.get(
        f"{API}/operating-expenses/summary", headers=_hdr(tok)
    ).json()
    assert s["today"]["prepaid_daily"] == 0.0
    assert s["prepaid"]["active_count"] == 0
