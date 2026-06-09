"""Iter-114 — Operational Reports endpoint.

Returns aggregated operational expense data for daily/monthly/yearly
reports. Aggregates across:
  • Salaries (operating_salaries)
  • Rentals (operating_rentals)
  • Daily expenses (operating_daily_expenses)
  • Prepaid expenses (operating_prepaid_expenses)
  • Purchase invoices (purchase_invoices)
  • Supplier payments (liability payments where kind=supplier)
  • Liability payments (all kinds)
  • Shipping company expenses (operating_shipping)
  • Advances/loans (operating_advances)

The endpoint returns line items by day + per-employee summary.
"""
from datetime import datetime, timezone, date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
import calendar


def attach_operational_reports_routes(parent_router, db, current_user):
    router = APIRouter(prefix="/operational-reports", tags=["operational-reports"])

    def _period_bounds(period: str, year: int, month: int, day: int) -> tuple[date, date]:
        if period == "daily":
            d = date(year, month, day)
            return d, d
        if period == "monthly":
            start = date(year, month, 1)
            dim = calendar.monthrange(year, month)[1]
            return start, date(year, month, dim)
        if period == "yearly":
            return date(year, 1, 1), date(year, 12, 31)
        raise HTTPException(400, "period must be daily|monthly|yearly")

    async def _list(coll, uid, from_d, to_d, date_field="date", extra_filter=None):
        """Generic helper: fetch docs in date range as list of dicts."""
        q = {"user_id": uid, date_field: {
            "$gte": from_d.isoformat(), "$lte": to_d.isoformat()
        }}
        if extra_filter:
            q.update(extra_filter)
        out = []
        async for d in db[coll].find(q, {"_id": 0}).sort(date_field, 1):
            out.append(d)
        return out

    @router.get("")
    async def get_report(
        period: str = "daily",
        year: Optional[int] = None,
        month: Optional[int] = None,
        day: Optional[int] = None,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        today = datetime.now(timezone.utc).date()
        year = year or today.year
        month = month or today.month
        day = day or today.day

        from_d, to_d = _period_bounds(period, year, month, day)

        # ── Line items by category ───────────────────────────────────
        salary_payments = []
        async for liab in db.liabilities.find({
            "user_id": uid, "kind": "salary",
            "due_date": {"$gte": from_d.isoformat(), "$lte": to_d.isoformat()},
        }, {"_id": 0}):
            salary_payments.append(liab)

        rentals = await _list("operating_rentals", uid, from_d, to_d, "rental_date")
        daily_expenses = await _list("operating_daily_expenses", uid, from_d, to_d, "expense_date")
        prepaid = await _list("operating_prepaid_expenses", uid, from_d, to_d, "expense_date")
        shipping = await _list("operating_shipping", uid, from_d, to_d, "expense_date")
        advances = await _list("operating_advances", uid, from_d, to_d, "advance_date")

        # Purchase invoices and supplier liability payments
        purchase_invoices = []
        async for pi in db.purchase_invoices.find({
            "user_id": uid, "invoice_date": {
                "$gte": from_d.isoformat(), "$lte": to_d.isoformat()
            },
        }, {"_id": 0}):
            purchase_invoices.append(pi)

        # All liability payments in range
        liab_payments = []
        async for lp in db.liability_payments.find({
            "user_id": uid, "payment_date": {
                "$gte": from_d.isoformat(), "$lte": to_d.isoformat()
            },
        }, {"_id": 0}):
            liab_payments.append(lp)

        def _sum(items, field):
            return round(sum(float(i.get(field) or 0) for i in items), 2)

        categories = {
            "salaries": {
                "label": "الرواتب",
                "items": salary_payments,
                "total_expected": _sum(salary_payments, "expected_amount"),
                "total_paid":     _sum(salary_payments, "paid_amount"),
            },
            "rentals": {
                "label": "الإيجارات",
                "items": rentals,
                "total": _sum(rentals, "amount"),
            },
            "daily_expenses": {
                "label": "المصروفات اليومية",
                "items": daily_expenses,
                "total": _sum(daily_expenses, "amount"),
            },
            "prepaid": {
                "label": "المدفوعات المسبقة",
                "items": prepaid,
                "total": _sum(prepaid, "amount"),
            },
            "purchase_invoices": {
                "label": "فواتير المشتريات",
                "items": purchase_invoices,
                "total": _sum(purchase_invoices, "total_amount"),
                "paid":  _sum(purchase_invoices, "paid_amount"),
            },
            "shipping": {
                "label": "مصاريف شركات الشحن",
                "items": shipping,
                "total": _sum(shipping, "amount"),
            },
            "advances": {
                "label": "العهد والسلف",
                "items": advances,
                "total": _sum(advances, "amount"),
            },
            "liability_payments": {
                "label": "سداد الالتزامات والموردين",
                "items": liab_payments,
                "total": _sum(liab_payments, "amount"),
            },
        }

        # ── Per-employee summary ─────────────────────────────────────
        emp_map: dict[str, dict] = {}
        for liab in salary_payments:
            emp_id = liab.get("employee_salary_id") or "unknown"
            slot = emp_map.setdefault(emp_id, {
                "employee_id": emp_id, "name": "",
                "days_worked": 0, "salary_due": 0.0, "paid": 0.0,
                "remaining": 0.0, "advance": 0.0,
            })
            slot["days_worked"] += int(liab.get("days_worked") or 0)
            slot["salary_due"] += float(liab.get("expected_amount") or 0)
            slot["paid"] += float(liab.get("paid_amount") or 0)
        # Resolve names + advance amounts
        for emp_id, slot in emp_map.items():
            doc = await db.operating_salaries.find_one(
                {"id": emp_id, "user_id": uid}, {"_id": 0, "name": 1},
            )
            if doc:
                slot["name"] = doc["name"]
            slot["remaining"] = round(max(0.0, slot["salary_due"] - slot["paid"]), 2)
            slot["advance"]   = round(max(0.0, slot["paid"] - slot["salary_due"]), 2)
            slot["net"] = round(slot["salary_due"] - slot["paid"], 2)
            # Round
            slot["salary_due"] = round(slot["salary_due"], 2)
            slot["paid"] = round(slot["paid"], 2)

        # ── Monthly breakdown (for yearly period) ─────────────────────
        monthly_breakdown = []
        if period == "yearly":
            for m in range(1, 13):
                mfrom = date(year, m, 1)
                mto   = date(year, m, calendar.monthrange(year, m)[1])
                if mfrom > today:
                    break
                if mto > today:
                    mto = today
                # Sum each category for this month
                month_totals = {}
                for col_name, date_field in [
                    ("operating_rentals", "rental_date"),
                    ("operating_daily_expenses", "expense_date"),
                    ("operating_prepaid_expenses", "expense_date"),
                    ("operating_shipping", "expense_date"),
                    ("operating_advances", "advance_date"),
                ]:
                    items = await _list(col_name, uid, mfrom, mto, date_field)
                    month_totals[col_name] = round(sum(float(i.get("amount") or 0) for i in items), 2)
                # Salaries: sum expected for the month
                m_sal = 0.0
                async for liab in db.liabilities.find({
                    "user_id": uid, "kind": "salary",
                    "period_key": f"{year:04d}-{m:02d}",
                }, {"_id": 0, "expected_amount": 1, "paid_amount": 1}):
                    m_sal += float(liab.get("expected_amount") or 0)
                month_totals["salaries"] = round(m_sal, 2)
                # Purchase invoices & liability payments
                m_pi = m_lp = 0.0
                async for pi in db.purchase_invoices.find({
                    "user_id": uid, "invoice_date": {
                        "$gte": mfrom.isoformat(), "$lte": mto.isoformat()
                    },
                }, {"_id": 0, "total_amount": 1}):
                    m_pi += float(pi.get("total_amount") or 0)
                async for lp in db.liability_payments.find({
                    "user_id": uid, "payment_date": {
                        "$gte": mfrom.isoformat(), "$lte": mto.isoformat()
                    },
                }, {"_id": 0, "amount": 1}):
                    m_lp += float(lp.get("amount") or 0)
                month_totals["purchase_invoices"] = round(m_pi, 2)
                month_totals["liability_payments"] = round(m_lp, 2)
                month_totals["grand_total"] = round(sum(month_totals.values()), 2)
                monthly_breakdown.append({
                    "month": m,
                    "month_name": ["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"][m-1],
                    "from": mfrom.isoformat(),
                    "to": mto.isoformat(),
                    "totals": month_totals,
                })

        # ── Open liabilities (for closing summary) ────────────────────
        open_liabs_total = 0.0
        async for liab in db.liabilities.find(
            {"user_id": uid, "status": {"$in": ["unpaid", "partial"]}},
            {"_id": 0, "expected_amount": 1, "paid_amount": 1},
        ):
            open_liabs_total += float(liab.get("expected_amount") or 0) - float(liab.get("paid_amount") or 0)

        # ── Grand totals ──────────────────────────────────────────────
        gross_expense = (
            categories["salaries"]["total_expected"]
            + categories["rentals"]["total"]
            + categories["daily_expenses"]["total"]
            + categories["prepaid"]["total"]
            + categories["purchase_invoices"]["total"]
            + categories["shipping"]["total"]
            + categories["advances"]["total"]
        )
        gross_paid = (
            categories["salaries"]["total_paid"]
            + categories["rentals"]["total"]
            + categories["daily_expenses"]["total"]
            + categories["prepaid"]["total"]
            + categories["purchase_invoices"]["paid"]
            + categories["shipping"]["total"]
            + categories["liability_payments"]["total"]
        )
        unpaid = max(0.0, gross_expense - gross_paid)

        employees_owed_to_us = round(sum(e["advance"] for e in emp_map.values()), 2)
        employees_we_owe     = round(sum(e["remaining"] for e in emp_map.values()), 2)

        return {
            "period": period,
            "from_date": from_d.isoformat(),
            "to_date": to_d.isoformat(),
            "year": year, "month": month, "day": day,
            "categories": categories,
            "employees": sorted(emp_map.values(), key=lambda e: e["name"] or ""),
            "monthly_breakdown": monthly_breakdown,
            "summary": {
                "gross_expense": round(gross_expense, 2),
                "gross_paid": round(gross_paid, 2),
                "unpaid": round(unpaid, 2),
                "open_liabilities_total": round(open_liabs_total, 2),
                "employees_we_owe": employees_we_owe,
                "employees_owed_to_us": employees_owed_to_us,
                "net_we_owe":  round(unpaid + employees_we_owe + open_liabs_total, 2),
                "net_owed_to_us": employees_owed_to_us,
            },
        }

    parent_router.include_router(router)
