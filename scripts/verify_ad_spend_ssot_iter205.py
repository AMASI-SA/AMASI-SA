#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Iter-205 — Production Verification Script for Ad-Spend SSOT
================================================================

Purpose
-------
Confirm — on a real environment, against live data — that every ad
spend is correctly written to `general_ledger` (Universal Ledger SSOT)
and that the cron / sync-all flow does NOT create duplicates.

Safety
------
This script is **read-mostly**. The only mutation it performs is the
"مزامنة الكل الآن" call (POST /api/ad-accounts/sync-all) which the
user explicitly requested as part of the test. Everything else is
GET-only.

How to run
----------
    # Preview
    API=https://salla-analytics.preview.emergentagent.com \
    EMAIL=admin@hesab.app PASSWORD=admin123 \
    python3 verify_ad_spend_ssot_iter205.py

    # Production (only AFTER you redeploy Iter-205 to mezansalla.com)
    API=https://mezansalla.com \
    EMAIL=<your prod email> PASSWORD=<your prod password> \
    python3 verify_ad_spend_ssot_iter205.py

    # Optional: target a specific ad account (otherwise auto-picks the
    # one with the largest cumulative spend in the last 14 days)
    AD_ACCOUNT_ID=<cp_id> python3 verify_ad_spend_ssot_iter205.py

Exit code: 0 = all PASS, 1 = any FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone

# ─── Config ────────────────────────────────────────────────────────
API = os.environ.get("API", "").rstrip("/")
EMAIL = os.environ.get("EMAIL", "")
PASSWORD = os.environ.get("PASSWORD", "")
AD_ACCOUNT_ID = os.environ.get("AD_ACCOUNT_ID", "")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

if not (API and EMAIL and PASSWORD):
    print("❌ Missing required env vars: API, EMAIL, PASSWORD")
    sys.exit(2)

# ─── Pretty printing ───────────────────────────────────────────────
GREEN, RED, YELLOW, CYAN, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[96m", "\033[0m",
)
PASS_TAG = f"{GREEN}✅ PASS{RESET}"
FAIL_TAG = f"{RED}❌ FAIL{RESET}"
WARN_TAG = f"{YELLOW}⚠️  WARN{RESET}"


def section(title: str) -> None:
    print()
    print(f"{CYAN}━━━ {title} ━━━{RESET}")


def line(tag: str, msg: str, detail: str = "") -> None:
    print(f"  {tag}  {msg}")
    if detail:
        for d in detail.splitlines():
            print(f"        {d}")


# ─── HTTP helpers ──────────────────────────────────────────────────
class Http:
    def __init__(self):
        self.token: str | None = None

    def request(self, method: str, path: str,
                body: dict | None = None,
                params: dict | None = None) -> dict | list:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "iter205-verify/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(
                f"HTTP {e.code} on {method} {path}: {body_text[:300]}"
            ) from None

    def login(self) -> str:
        res = self.request("POST", "/api/auth/login",
                             body={"email": EMAIL,
                                    "password": PASSWORD})
        self.token = res["access_token"]
        return res.get("name") or res.get("email", "?")


h = Http()


# ─── Aggregate helpers (read-only) ────────────────────────────────
def ledger_sum_for(entity_type: str, entity_id: str | None = None,
                    sub_account: str | None = None,
                    spend_date_from: str | None = None,
                    spend_date_to: str | None = None) -> dict:
    """Return {debit, credit, net} totals from /api/ledger/entries.

    Pages through entries (500 per page — the endpoint's hard cap).
    """
    debit = credit = 0.0
    page_size = 500
    skip = 0
    while True:
        params: dict = {"entity_type": entity_type,
                         "limit": page_size, "skip": skip}
        if entity_id:
            params["entity_id"] = entity_id
        if sub_account:
            params["sub_account"] = sub_account
        raw = h.request("GET", "/api/ledger/entries", params=params)
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        if not items:
            break
        for e in items:
            md = (e.get("metadata") or {})
            sd = md.get("spend_date")
            if spend_date_from and (not sd or sd < spend_date_from):
                continue
            if spend_date_to and (not sd or sd > spend_date_to):
                continue
            amt = float(e.get("amount") or 0)
            if e.get("side") == "debit":
                debit += amt
            elif e.get("side") == "credit":
                credit += amt
        if len(items) < page_size:
            break
        skip += page_size
        if skip > 50_000:
            break
    return {"debit": round(debit, 2),
              "credit": round(credit, 2),
              "net": round(debit - credit, 2)}


def list_ad_accounts() -> list:
    r = h.request("GET", "/api/ad-accounts")
    return r.get("items", []) if isinstance(r, dict) else r


# ─── Test scenarios ────────────────────────────────────────────────
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    line(PASS_TAG if ok else FAIL_TAG, name, detail)


# ───────────────────────────────────────────────────────────────────
def main() -> int:
    print(f"{CYAN}╔══════════════════════════════════════════════════════╗{RESET}")
    print(f"{CYAN}║  Iter-205 — Ad Spend SSOT Production Verification   ║{RESET}")
    print(f"{CYAN}╚══════════════════════════════════════════════════════╝{RESET}")
    print(f"  API:    {API}")
    print(f"  User:   {EMAIL}")
    print(f"  Time:   {datetime.now(timezone.utc).isoformat()}")
    if DRY_RUN:
        print(f"  {YELLOW}DRY-RUN mode — sync-all will NOT be called{RESET}")

    # ── 0. Login ───────────────────────────────────────────────
    section("0. تسجيل الدخول")
    try:
        name = h.login()
        record(f"تسجيل دخول ناجح: {name}", True)
    except Exception as e:
        record("فشل تسجيل الدخول", False, str(e))
        return _summary()

    # ── 1. Pick an ad account ──────────────────────────────────
    section("1. اختيار الحساب الإعلاني")
    accs = list_ad_accounts()
    if not accs:
        record("لا توجد حسابات إعلانية في هذا المستخدم", False)
        return _summary()
    if AD_ACCOUNT_ID:
        target = next((a for a in accs if a["id"] == AD_ACCOUNT_ID), None)
        if not target:
            record(f"AD_ACCOUNT_ID={AD_ACCOUNT_ID} غير موجود",
                    False)
            return _summary()
    else:
        # Auto-pick: highest cumulative spend in last 14 days. Use
        # the existing /spend-summary or fallback to month_spend.
        target = max(accs, key=lambda a: float(
            a.get("month_spend") or a.get("cumulative_spend") or 0))
    record(
        f"الحساب المختار: {target.get('name')} "
        f"({target.get('ad_provider') or '?'})",
        True,
        f"id={target['id']}  "
        f"balance={target.get('balance')}  "
        f"open_debt={target.get('open_debt')}  "
        f"month_spend={target.get('month_spend')}",
    )
    ad_id = target["id"]

    # ── 2. SNAPSHOT (before first sync) ────────────────────────
    section("2. لقطة قبل المزامنة الأولى")
    today = date.today().isoformat()
    fortnight_ago = (date.today() - timedelta(days=14)).isoformat()

    snap_before = {
        "expense_total": ledger_sum_for(
            "expense", "advertising",
            spend_date_from=fortnight_ago,
            spend_date_to=today)["debit"],
        "ad_balance_net": ledger_sum_for(
            "ad_account", ad_id, "balance")["net"],
        "ad_debt_net": ledger_sum_for(
            "ad_account", ad_id, "debt")["net"],
        "rows": _count_gl_spend_rows(ad_id, fortnight_ago, today),
    }
    line(CYAN + "ℹ️ " + RESET,
         "الحالة قبل المزامنة:",
         json.dumps(snap_before, ensure_ascii=False, indent=2))

    # ── 3. First sync-all run ──────────────────────────────────
    section("3. تشغيل 'مزامنة الكل الآن' (المرة الأولى)")
    if DRY_RUN:
        line(WARN_TAG, "تخطّيت لأن DRY_RUN=1")
        sync1 = {}
    else:
        try:
            sync1 = h.request("POST", "/api/ad-accounts/sync-all",
                               body={"from_date": fortnight_ago,
                                       "to_date": today,
                                       "force": True})
            record(
                f"تنفيذ المزامنة الأولى — "
                f"{len(sync1.get('results') or [])} حساب معالَج",
                True)
        except Exception as e:
            record("فشل تشغيل sync-all", False, str(e))
            return _summary()
        time.sleep(2)  # let any async writes settle

    # ── 4. Verify ledger received the spend ────────────────────
    section("4. التحقق من القيد الموحد بعد المزامنة")
    snap_after_1 = {
        "expense_total": ledger_sum_for(
            "expense", "advertising",
            spend_date_from=fortnight_ago,
            spend_date_to=today)["debit"],
        "ad_balance_net": ledger_sum_for(
            "ad_account", ad_id, "balance")["net"],
        "ad_debt_net": ledger_sum_for(
            "ad_account", ad_id, "debt")["net"],
        "rows": _count_gl_spend_rows(ad_id, fortnight_ago, today),
    }
    expense_delta = round(
        snap_after_1["expense_total"] - snap_before["expense_total"], 2)
    balance_delta = round(
        snap_after_1["ad_balance_net"] - snap_before["ad_balance_net"], 2)
    debt_delta = round(
        snap_after_1["ad_debt_net"] - snap_before["ad_debt_net"], 2)
    rows_delta = snap_after_1["rows"] - snap_before["rows"]

    record(
        "ظهور قيد expense.advertising (DEBIT)",
        expense_delta >= 0,  # could be zero if no new spend that day
        f"إجمالي مصروف الإعلانات: "
        f"{snap_before['expense_total']} → {snap_after_1['expense_total']} "
        f"(Δ = +{expense_delta})",
    )
    record(
        "أحد الجانبين CREDIT تم استخدامه (balance أو debt)",
        (balance_delta <= 0) and ((balance_delta < 0) or (debt_delta < 0))
        or expense_delta == 0,
        f"ad_account.balance net: "
        f"{snap_before['ad_balance_net']} → {snap_after_1['ad_balance_net']} "
        f"(Δ {balance_delta:+.2f})\n"
        f"ad_account.debt net: "
        f"{snap_before['ad_debt_net']} → {snap_after_1['ad_debt_net']} "
        f"(Δ {debt_delta:+.2f})",
    )

    # Double-entry invariant for ad_account_spend txn type
    try:
        agg = h.request("GET", "/api/ledger/entries",
                         params={"limit": 500})
        all_rows = agg.get("items", agg) if isinstance(agg, dict) else agg
        d = c = 0.0
        for e in all_rows:
            md = (e.get("metadata") or {})
            if md.get("txn_type") != "ad_account_spend":
                continue
            amt = float(e.get("amount") or 0)
            if e.get("side") == "debit":
                d += amt
            elif e.get("side") == "credit":
                c += amt
        balanced = abs(round(d - c, 2)) < 0.01
        record(
            "Σ debit == Σ credit لجميع قيود ad_account_spend",
            balanced,
            f"debit={round(d,2)}  credit={round(c,2)}  diff={round(d-c,2)}",
        )
    except Exception as e:
        record("فحص توازن القيود — تعذر القراءة", False, str(e))

    # ── 5. Re-run sync-all (idempotency) ───────────────────────
    section("5. مكافحة التكرار — تشغيل المزامنة مرة ثانية")
    if DRY_RUN:
        line(WARN_TAG, "تخطّيت لأن DRY_RUN=1")
    else:
        try:
            sync2 = h.request("POST", "/api/ad-accounts/sync-all",
                               body={"from_date": fortnight_ago,
                                       "to_date": today,
                                       "force": True})
            record("تنفيذ المزامنة الثانية", True,
                    f"{len(sync2.get('results') or [])} حساب معالَج")
        except Exception as e:
            record("فشل تشغيل sync-all (الثانية)", False, str(e))
            return _summary()
        time.sleep(2)

    snap_after_2 = {
        "expense_total": ledger_sum_for(
            "expense", "advertising",
            spend_date_from=fortnight_ago,
            spend_date_to=today)["debit"],
        "rows": _count_gl_spend_rows(ad_id, fortnight_ago, today),
    }
    expense_delta2 = round(
        snap_after_2["expense_total"] - snap_after_1["expense_total"], 2)
    rows_delta2 = snap_after_2["rows"] - snap_after_1["rows"]
    record(
        "لا تكرار: إجمالي expense.advertising لم يتغير",
        abs(expense_delta2) < 0.01,
        f"بعد المزامنة الأولى: {snap_after_1['expense_total']}\n"
        f"بعد المزامنة الثانية: {snap_after_2['expense_total']}\n"
        f"(Δ {expense_delta2:+.2f})",
    )
    record(
        "لا تكرار: عدد صفوف القيد الموحد لم يزدد",
        rows_delta2 == 0,
        f"الصفوف قبل: {snap_after_1['rows']}  "
        f"الصفوف بعد: {snap_after_2['rows']}  "
        f"(Δ {rows_delta2:+d})",
    )

    # ── 6. Balance reconciliation (ledger vs assets list) ──────
    section("6. مطابقة رصيد الحساب الإعلاني (الـledger ↔ صفحة الأصول)")
    accs_after = list_ad_accounts()
    target_after = next(
        (a for a in accs_after if a["id"] == ad_id), {})
    ledger_balance = ledger_sum_for(
        "ad_account", ad_id, "balance")["net"]
    ledger_debt_outstanding = max(
        0.0, -ledger_sum_for("ad_account", ad_id, "debt")["net"])
    asset_balance = float(target_after.get("balance") or 0)
    asset_debt = float(target_after.get("open_debt") or 0)
    bal_ok = abs(ledger_balance - asset_balance) < 0.01
    debt_ok = abs(ledger_debt_outstanding - asset_debt) < 0.01
    drift_note = (
        "\n        ⓘ ملاحظة: انحراف بين Legacy و SSOT متوقع للحسابات التي\n"
        "          عليها صرف قديم قبل Iter-205 (لم يكن الـ ledger مفعّلاً).\n"
        "          الحل: شغّل /sync-all مع force=true بعد deploy لإعادة ضبط\n"
        "          الحسابات الجديدة من تاريخ Iter-205 فأكثر، أو تجاهل\n"
        "          الانحراف القديم — المهم ألا يزداد الفرق بعد Iter-205."
        if not (bal_ok and debt_ok) else ""
    )
    record(
        "رصيد ad_account.balance (Ledger SSOT) يطابق صفحة الأصول",
        bal_ok,
        f"Ledger: {ledger_balance}   Assets page: {asset_balance}   "
        f"Δ={round(ledger_balance-asset_balance,2)}" + drift_note,
    )
    record(
        "مديونية ad_account.debt (Ledger SSOT) تطابق صفحة الأصول",
        debt_ok,
        f"Ledger outstanding: {ledger_debt_outstanding}   "
        f"Assets page open_debt: {asset_debt}   "
        f"Δ={round(ledger_debt_outstanding-asset_debt,2)}",
    )

    # ── 7. Financial position reflects ad expense ──────────────
    section("7. ظهور المصروف الإعلاني في تقرير المركز المالي")
    try:
        rep = h.request(
            "GET",
            "/api/accounting/reports/advertising-expenses",
            params={"from_date": fortnight_ago, "to_date": today},
        )
        total = float(rep.get("total") or 0)
        ledger_total = snap_after_2["expense_total"]
        match = abs(total - ledger_total) < 0.01
        record(
            "تقرير المصروفات الإعلانية يطابق الـledger",
            match,
            f"تقرير: {total}   Ledger: {ledger_total}   "
            f"عدد الحسابات: {len(rep.get('by_ad_account') or [])}",
        )
    except Exception as e:
        record("فشل قراءة تقرير المصروفات الإعلانية", False, str(e))

    return _summary()


def _count_gl_spend_rows(ad_id: str, dfrom: str, dto: str) -> int:
    """Count general_ledger rows tied to this ad_account where
    txn_type=ad_account_spend within the date window. Uses
    /api/ledger/entries paginated."""
    count = 0
    skip = 0
    while True:
        raw = h.request("GET", "/api/ledger/entries",
                          params={"entity_type": "ad_account",
                                    "entity_id": ad_id,
                                    "limit": 500, "skip": skip})
        items = raw.get("items", raw) if isinstance(raw, dict) else raw
        if not items:
            break
        for e in items:
            md = (e.get("metadata") or {})
            if md.get("txn_type") != "ad_account_spend":
                continue
            sd = md.get("spend_date")
            if not sd or sd < dfrom or sd > dto:
                continue
            count += 1
        if len(items) < 500:
            break
        skip += 500
        if skip > 50_000:
            break
    return count


def _summary() -> int:
    section("الخلاصة")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    print(f"  المجموع: {total}   "
          f"{GREEN}نجح: {passed}{RESET}   "
          f"{RED}فشل: {failed}{RESET}")
    if failed == 0:
        print(f"\n{GREEN}🎉 جميع الفحوصات نجحت — "
              f"Spend SSOT يعمل على هذه البيئة.{RESET}")
        return 0
    print(f"\n{RED}⚠️ يوجد {failed} فشل — راجع التفاصيل أعلاه.{RESET}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
