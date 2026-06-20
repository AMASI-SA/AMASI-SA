# Iter-250b · P1.5 — F1 Quick Fix + Account-Tx vs Ledger Walk

**Date:** 2026-02-XX
**Status:** ✅ Implemented on Preview · Awaiting Production deploy + verification

---

## 1) ما تم تنفيذه

### F1 — توسيع فلتر `_ledger_based_tx_feed`
- **الملف:** `backend/accounts_routes.py`
- **السطر 199 (قبل):** `"sub_account": "main"`
- **السطر 199 (بعد):** `"sub_account": {"$in": ["main", "balance"]}`
- **سطر إضافي 201:** projection تُضيف `"sub_account": 1`
- **سطر إضافي ~300:** `out["sub_account"] = r.get("sub_account") or "main"` لكي يصل للـ frontend.

**أثر متوقع على Production لبنك الإنماء:**
- 26 سطر `main` (ظاهر سابقاً) + **2 سطر BNPL bridge** (مخفي سابقاً) = **28 سطر total**.
- المجموع: `+29,303.73 ر.س` ⇒ final `balance_after` في feed سيصبح **205,441.44** بدلاً من 176,137.71.

### F1 (UI Badge) — `frontend/src/pages/AccountDetails.jsx`
- إضافة badge `💳 BNPL Bridge` (فوشي) لأي سطر `sub_account === "balance"`.
- يساعد التاجر في تمييز السطور القادمة من جسر BNPL.

### Diagnostic جديد — `/api/diagnostics/account-tx-vs-ledger-walk`
- **الملف:** `backend/account_tx_vs_ledger_walk_routes.py`
- يقارن لحساب bank/cash واحد:
  - `account_transactions` (legacy): total · in/out · by_type · by_month · orphans بدون `txn_group_id`
  - `general_ledger`: total · debit/credit · by (entry_type, sub_account) · by_month
  - **Crosswalk** عبر `txn_group_id`: shared / at_only / ledger_only
  - **unmatched_account_tx_net** — هو المُتَّهم الرئيسي للفجوة 70k على الإنماء
  - **summary.gap_breakdown_hypothesis** — كسر تلقائي للفجوة إلى bnpl + unmatched_at + remaining

**نتيجة Preview لبنك الإنماء (مختبَر):**
```json
{
  "stored": 73525.86,
  "account_transactions": { "total": 11, "net": 66914.95 },
  "general_ledger":       { "total": 12, "net": 50986.91 },
  "crosswalk": {
    "shared_txn_group_ids": 0,
    "account_tx_only": 0,
    "ledger_only": 12,
    "unmatched_account_tx_net": 66914.95
  },
  "summary.gap_stored_minus_ledger": 22538.95
}
```

⚠️ ملاحظة: 0 shared txn_group_id في Preview يعني الـ double-write لم يكن مُفعَّل عند كتابة الـ 11 row القديمة في account_transactions. هذا **مؤشر هام** يفسّر سبب وجود رواسب legacy لا تطابق ledger.

---

## 2) تأثير الـ Headline (مهم — للمراجعة)

| الموقع | قبل F1 | بعد F1 | تغيُّر؟ |
|---|---|---|---|
| `/accounts/:id` headline (top card) | `account_balance_ssot()` = ledger_main fallback | **نفسه** = ledger_main | ❌ لا تغيير |
| `/accounts/:id/transactions` feed (آخر سطر balance_after) | جمع تراكمي من sub=main فقط | جمع تراكمي من sub ∈ {main, balance} | ✅ نعم — يزيد بمقدار `ledger_balance_net` |
| `/accounts` (list) — `current_balance` | SSOT | SSOT | ❌ لا تغيير |
| `/financial-position` | summation منفصل (entity_type="bank") | نفسه | ❌ لا تغيير |
| `accounts.current_balance` (stored) | غير مُتأثر | غير مُتأثر | ❌ لا تغيير (لم نكتب في DB) |

**الخلاصة:** الـ headline **لم يتغيَّر** كما طلبت.
**لكن** الـ feed سيُظهر سطور BNPL جديدة، مما يعني أن `balance_after` للسطر الأحدث في الـ feed سيختلف عن الـ headline بمقدار `ledger_balance_net` (29,303.73 ر.س للإنماء).

هذا الفرق **متعمَّد ومشروح**:
- الـ headline = SSOT الرسمي (ledger_main) — كان مُحَوَّل قبل ظهور BNPL bridge.
- الـ feed = الـ trail الحقيقي للحركات (يشمل BNPL bridge).
- الفرق بينهما = حجم الـ BNPL settlements التي لم تُدمَج في SSOT.

> سنحل هذا التضارب في **Phase 3** (Migration دائمة لـ BNPL sub_account من "balance" → "main") بعد موافقتك على Dry-Run.

---

## 3) ما لم يُنفَّذ (حسب طلبك)

| Fix | الحالة | السبب |
|---|---|---|
| F2 (transfers + liabilities sufficient funds) | ⏸️ Postponed | حسب طلبك |
| F4 (reconciliation_routes SSOT) | ⏸️ Postponed | حسب طلبك |
| F8 (bank-transfer-routing/map SSOT) | ⏸️ Postponed | حسب طلبك |
| Migration BNPL `balance → main` | ⏸️ Postponed | يحتاج Dry-Run + موافقة |
| Recompute balances | ❌ ممنوع | حسب القيد الصارم |
| أي DB write | ❌ ممنوع | حسب القيد الصارم |

---

## 4) خطوات التحقق على Production (بعد deploy)

1. افتح `/audit/balance-drift` → تأكد أن summary لبنك الإنماء يُظهر:
   - `feed_visible_tx_count` ارتفع من 26 إلى 28 (أو ما يقاربها)
   - `feed_hidden_tx_count` نزل من 2 إلى 0
   - `iter249_bnpl_hidden` نزل من 1 إلى 0
   - `total_hidden_amount` نزل من 29,303 إلى 0

2. افتح `/accounts/{inma_id}` → تأكد:
   - Top card balance = **176,137.71** (ثابت — لا تغيير)
   - في الـ feed، تظهر سطور جديدة بـ `💳 BNPL Bridge` badge
   - آخر `balance_after` = **205,441.44** (= ledger_main + ledger_balance)
   - الفرق بين headline (176k) وbalance_after (205k) = 29,303.73 (= BNPL bridge net)

3. استدعِ:
   ```
   GET /api/diagnostics/account-tx-vs-ledger-walk?account_id=<inma_id>&include_rows=true
   ```
   انسخ JSON هنا لمعرفة:
   - كم row في `account_transactions` ليست في `general_ledger` (المتوقع: 51 row, net ≈ 70k)
   - أنواع المعاملات في الـ unmatched (مثلاً: قد تكون كلها `supplier_invoice` قديمة أو `internal_transfer` قبل الـ double-write)

---

## 5) أوامر اختبار محلية

```bash
# تأكد من سلوك السطر 199:
grep -n '"sub_account":' /app/backend/accounts_routes.py | head -3
# يجب أن يُرجع: "sub_account": {"$in": ["main", "balance"]}

# Smoke test بعد login
curl -H "Authorization: Bearer $TOKEN" \
  "$API_URL/api/diagnostics/account-tx-vs-ledger-walk?account_id=<inma_id>"
```

---

**نهاية تقرير F1 + Walker · Iter-250b · P1.5**
