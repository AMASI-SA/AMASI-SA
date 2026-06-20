# Iter-250b · P1.4 — Forensic Read-Only Review: `/accounts/:id`

**Status:** ✅ READ-ONLY · لا تعديلات على الكود/DB
**Author:** E1 Agent
**Scope:** Page `/accounts/:id` (Account Details) — Backend endpoints + Frontend renderers + جميع المسارات التي تكتب/تقرأ رصيد الحساب
**Iteration:** 250b · Phase 1.4
**Related issues:** Iter-249 BNPL Settlement invisibility (UI filter mismatch `main` vs `balance`)

---

## 1) ملخص تنفيذي

| سؤال | الجواب |
|---|---|
| ما الـ SSOT الفعلي لرصيد الحساب؟ | `financial_position_ssot.account_balance_ssot()` — تركيبة (ledger + dw_net correction + legacy opening fallback) |
| هل هناك مصادر متوازية؟ | نعم. ٤ مصادر متعايشة: `accounts.current_balance` (legacy) · `general_ledger sub_account="main"` (الجديد) · `account_transactions` (مجمَّد للهجرة) · `expected_orders_balance` (طلبات سلة) |
| هل هناك انحراف فعلي؟ | ✅ نعم: ٨٧ك+ ر.س بين top-card و آخر سطر balance_after في `AccountDetails` (سبب اختفاء BNPL Settlements — موثَّق في Iter-249). |
| التوصية النهائية للصفحة | **MERGE (إصلاح SSOT) — لا حذف.** الصفحة جوهرية ولا بديل. لكن الـ feed يحتاج إصلاح فلتر `sub_account`. |

---

## 2) خريطة Endpoints مرتبطة بـ `/accounts/:id`

| # | Endpoint | الملف · السطر | يقرأ من | يكتب في |
|---|---|---|---|---|
| 1 | `GET /accounts/{account_id}` | `accounts_routes.py:620` | `accounts` + `_account_with_meta()` → `account_balance_ssot()` | — |
| 2 | `GET /accounts/{account_id}/transactions` | `accounts_routes.py:843` | `general_ledger` (migrated) أو `account_transactions` (legacy) | — |
| 3 | `POST /accounts/{account_id}/transactions` | `accounts_routes.py:888` | — | `account_transactions` ➜ `_recompute_balance()` ➜ `accounts.current_balance` |
| 4 | `DELETE /accounts/{account_id}/transactions/{tx_id}` | `accounts_routes.py:921` | — | يحذف من `account_transactions` ➜ `_recompute_balance()` ➜ `accounts.current_balance` |
| 5 | `PUT /accounts/{account_id}` | `accounts_routes.py:755` | — | يحدّث metadata + `bank_transfer_aliases` فقط |
| 6 | `DELETE /accounts/{account_id}` | `accounts_routes.py:820` | — | يحذف الحساب (يرفض إن > 1 حركة) |
| 7 | `GET /accounts/{account_id}/breakdown` | `accounts_routes.py:657` | `account_transactions` فقط (legacy) — لأغراض تحليل bucketed | — |

---

## 3) Source-of-Truth Matrix (SSOT)

### 3.1 مصادر الرصيد المُكتشَفة

| المصدر | الملف | الدور الفعلي | يُعتبر SSOT الآن؟ |
|---|---|---|---|
| `accounts.current_balance` | `accounts` collection | حقل مخزَّن قديم — يُحدَّث بواسطة `_recompute_balance` على كل POST/DELETE transaction | ❌ Legacy (لكن لا يزال يُكتب) |
| `general_ledger` (sub_account="main") | `general_ledger` collection | الـ SSOT الجديد (Iter-192 / Iter-198) | ✅ نعم (للبنوك/الكاش/منصات الدفع) |
| `general_ledger` (sub_account="balance") | `general_ledger` collection | BNPL Settlement bridge يكتب هنا فقط (`bnpl/settlement_bridge.py:298`) | ⚠️ **منفصل** (ولا يُقرأ في الـ feed!) |
| `account_transactions` | `account_transactions` collection | مجمَّد بعد الـ migration للحسابات المهاجرة. لكن لا يزال يقبل POST جديد عبر `_recompute_balance` | ⚠️ Hybrid |
| `expected_orders_balance` | حقل في `accounts` | إجمالي طلبات سلة المتوقعة (مدخل أساسي قبل الكوارير/الخصومات) | ✅ معتمد كـ implicit opening |
| `dw_net` (double-write correction) | محسوب في `financial_position_ssot.py:140-148` | يخصم تأثير `account_transaction_double_write` من ledger كي لا يُحسب مرتين | ✅ ضروري للهجين |
| `balance_resolver.resolve_live_balance()` | `balance_resolver.py` | Resolver أقدم (Iter-195) — غير مُستخدم في `/accounts/:id` لكن لا يزال يُستدعى من شاشات أخرى | ⚠️ مكرر مع `account_balance_ssot` |

### 3.2 شجرة استدعاءات `/accounts/:id` للحصول على الرصيد

```
GET /accounts/{account_id}
  └─ db.accounts.find_one(...)                       [يقرأ accounts.current_balance المخزَّن]
  └─ _account_with_meta(db, user_id, doc)
       └─ IF account_type ∈ {bank, cash, payment_platform}:
            └─ account_balance_ssot(db, user_id, account)
                 ├─ IF is_bnpl_account(account):
                 │    └─ get_bnpl_provider_balance(...)           [BNPL canonical formula]
                 ├─ ELIF general_ledger has activity:
                 │    ├─ compute_balance(sub_account="main")      [ledger net]
                 │    └─ IF no opening_balance entry:
                 │         └─ + (accounts.current_balance − dw_net)   [Iter-240 hybrid correction]
                 └─ ELSE: accounts.current_balance                [legacy fallback]
       └─ out["current_balance"] = SSOT value
       └─ out["balance_source"] = "ssot"
       └─ IF |SSOT − stored| > 0.005:
            └─ out["current_balance_legacy"] = stored
```

**نتيجة:** الـ top-card في `AccountDetails.jsx` يعرض `account.current_balance` وهو **بعد** الـ overwrite بـ SSOT. لذا الـ headline صحيح.

### 3.3 شجرة استدعاءات `/accounts/:id/transactions`

```
GET /accounts/{account_id}/transactions
  └─ acc = db.accounts.find_one(...)
  └─ IF account_type ∈ {bank, cash}:
       └─ anchor = db.general_ledger.find_one(
              entity_type="bank", entity_id=acc_id,
              entry_type="opening_balance", status="posted")
       └─ is_migrated = bool(anchor)
  └─ IF is_migrated:
       └─ _ledger_based_tx_feed(db, uid, account_id)
            └─ db.general_ledger.find(
                  entity_type="bank",
                  entity_id=account_id,
                  sub_account="main",        ⚠️ ← المشكلة الجذرية لـ Iter-249
                  status="posted")
            └─ running_balance من debits − credits
  └─ ELSE:
       └─ db.account_transactions.find(...)            [legacy]
```

---

## 4) Cache Usage Matrix (هل الصفحة تقرأ Cache أم SSOT؟)

| الشاشة / المسار | الواجهة (Endpoint) | المصدر الفعلي | SSOT أم Cache؟ |
|---|---|---|---|
| `/accounts/:id` — **headline balance** | `GET /accounts/{id}` | `account_balance_ssot()` ⇨ overwrite `current_balance` | ✅ SSOT |
| `/accounts/:id` — **transactions feed** | `GET /accounts/{id}/transactions` | `general_ledger` (مهاجَر) أو `account_transactions` (قديم) | ⚠️ SSOT جزئي (يفلتر `sub_account="main"` فقط — يفقد BNPL!) |
| `/accounts/:id` — **opening_balance + opening_balance_date** | `GET /accounts/{id}` (raw doc field) | `accounts` collection | ✅ Stored field (immutable seed) |
| `/accounts/:id` — **expected_orders_balance** (للـ payment_platform card) | `GET /accounts/{id}` (raw doc field) | `accounts.expected_orders_balance` (مكتوب من `sync-payment-methods`) | ✅ Stored field |
| `/accounts/:id/breakdown` (Iter-111 diagnostic) | `GET /accounts/{id}/breakdown` | `account_transactions` (legacy) ⇄ `accounts.current_balance` | ❌ **Legacy fully** — يقارن مصدرين قديمين |
| `/accounts` (list) | `GET /accounts` | يستدعي `_account_with_meta` لكل سطر ⇨ SSOT | ✅ SSOT |
| `/accounts/summary` | `GET /accounts/summary` | يستدعي `account_balance_ssot` صراحةً | ✅ SSOT |
| `/transfers` (page) | `GET /accounts` (للقائمة) | SSOT (عبر list) | ✅ SSOT |
| `/financial-input-hub` (banks selectors) | `GET /accounts` | SSOT | ✅ SSOT |
| `/shipping/transfers` (banks selectors) | `GET /accounts` | SSOT | ✅ SSOT |
| `/receivables` (banks selectors) | `GET /accounts` | SSOT | ✅ SSOT |
| `/advances`, `/operating-expenses`, `/ad-accounts` (banks selectors) | `GET /accounts` | SSOT | ✅ SSOT |
| `/reconciliation` | `GET /reconciliation/*` | `reconciliation_routes.py:128` يقرأ `acc.get("current_balance")` ولا يستدعي SSOT — لكنه يستخدم BNPL canonical للـ providers (`reconciliation_routes.py:142`) | ⚠️ Hybrid — banks من stored field, BNPL من canonical |
| `/cod-diagnostic` | endpoint مخصص | يعرض `current_balance` + `walk_confirmed_total` صراحةً (تشخيصي) | ✅ Diagnostic (مقصود) |
| `/ad-account-forensic` | endpoint forensic | `current_balance_cached` بشكل صريح | ✅ Diagnostic (مقصود) |
| `/accounts/bank-transfer-routing/map` | `GET /accounts/bank-transfer-routing/map` | يقرأ `b.get("current_balance")` مباشرة — **بدون SSOT** | ❌ Cache (raw stored) |
| `/migration/*` | `migration_routes.py` | يستخدم `current_balance` كـ legacy seed | ✅ مقصود |

---

## 5) كشف القراءات من Cache بدل SSOT

### 5.1 قراءات `accounts.current_balance` **الخام** (بدون SSOT enrichment) — اللي يجب فحصها

| # | الملف · السطر | الاستخدام | درجة الخطورة | تعليق |
|---|---|---|---|---|
| 1 | `accounts_routes.py:649` (`bank-transfer-routing/map`) | يعرض `current_balance` من stored field | 🟡 **MEDIUM** | شاشة Admin · لا تُستخدم لحساب transactions, لكنها قد تُظهر فرق |
| 2 | `accounts_routes.py:733` (`breakdown`) | `recorded = acc.get("current_balance")` لمقارنة مع computed من `account_transactions` | 🟢 LOW | تشخيصي بطبيعته |
| 3 | `reconciliation_routes.py:128` | `current_balance = acc.get("current_balance")` للبنوك | 🟡 **MEDIUM** | يجب يستخدم `account_balance_ssot` للبنوك |
| 4 | `transfers_routes.py:219` | `from_bal = acc.get("current_balance")` للتحقق من sufficient funds | 🔴 **HIGH** | إذا انحرفت `current_balance` عن SSOT الحقيقي ⇒ التحويل قد يُمنع أو يُسمح بالخطأ |
| 5 | `liabilities_routes.py:926` | فحص `bank.get("current_balance") + 0.01 < amount` قبل دفع التزام | 🔴 **HIGH** | نفس مشكلة #4 |
| 6 | `liabilities_routes.py:1396, 1412` | summing `current_balance` للـ financial position القديم | 🟢 LOW | endpoint قديم — replaced by `financial_position_ssot` |
| 7 | `financial_movements_routes.py:354` | `avail = acc.get("current_balance")` كمؤشر متاح في dropdown | 🟡 **MEDIUM** | يقارن مع SSOT في السطر 218 (Iter-246j) لكن السطر 354 لا يزال raw |
| 8 | جميع dropdowns في الـ Frontend (`Transfers.jsx`, `Advances.jsx`, `FinancialInputHub.jsx`, إلخ) | `b.current_balance` من `/accounts` list | ✅ آمن | لأن `/accounts` list يعيد قيمة SSOT-adjusted |

### 5.2 لا يوجد استخدام لـ `cached_balance` (لا في backend ولا في frontend) — فقط `current_balance_cached` في صفحات forensic مقصودة.

### 5.3 لا يوجد استخدام لـ `available_balance` كحقل مخزَّن — فقط key محسوب في response (`financial_movements_routes.py:243`).

---

## 6) مراجعة خاصة للحسابات: الراجحي · الأهلي · الإنماء · الصناديق النقدية

### 6.1 لماذا تحتاج معاملة خاصة؟
- هذه الحسابات استلمت تسويات BNPL (Tabby, Tamara) عبر `bnpl/settlement_bridge.py` الذي يكتب في `sub_account="balance"` **وليس** `"main"`.
- بنفس الوقت، استلمت تحويلات يدوية عبر `transfers_routes.py` و `financial_movements_routes.py` تكتب في `sub_account="main"`.

### 6.2 تأثير على الـ headline (top card في `/accounts/:id`):
**يعمل بشكل صحيح** ✓ لأن `account_balance_ssot()` يستدعي `compute_balance(sub_account="main")` ثم يضيف `(current_balance − dw_net)`. الـ `current_balance` المخزَّن يحتوي على آثار BNPL لأنها وصلت قبل الـ migration ⇒ الـ formula الهجينة تستردها.

**لكن** — اذا تمت BNPL settlement بعد الـ migration، فهي:
- ✅ تظهر في حركة `balance` sub_account (تَزيد ledger)
- ❌ لا تظهر في feed `sub_account="main"` (UI يفلتر)
- ❌ لا تُحدث `accounts.current_balance` (الـ bridge لا يكتب في `account_transactions`)
- ⚠️ لذا الـ headline قد ينحرف ⇒ هذا هو **بالضبط** سيناريو الـ ٨٧ك ر.س في Iter-249

### 6.3 الصناديق النقدية (`account_type="cash"`):
- نفس المنطق ينطبق (ledger + dw_net). نادراً ما تستلم BNPL.
- المخاطر هنا أقل لأن دخل الكاش غالباً يدوي عبر `account_transactions` ⇒ ledger متطابق مع stored.

---

## 7) تأثير العمليات على رصيد الحساب

| العملية | الملف الرئيسي | يكتب في `account_transactions`؟ | يكتب في `general_ledger`؟ | `sub_account` المستخدم | يحدّث `accounts.current_balance`؟ | يظهر في `/accounts/:id` feed؟ |
|---|---|---|---|---|---|---|
| **BNPL Settlement** (Tabby/Tamara) | `bnpl/settlement_bridge.py` | ❌ لا (Iter-220 Phase 2b — bypass) | ✅ نعم | **`balance`** ⚠️ | ❌ لا | ❌ **لا (الجذر)** |
| **COD Settlement** (سلة) | `financial_movements_routes.py:486-493` | ❌ (الحديثة) | ✅ نعم | `main` ✓ | ❌ (الحديثة) | ✅ نعم |
| **Internal Transfer** (يدوي بين حسابين) | `transfers_routes.py` + `ledger_double_write.py` | ✅ نعم (لكلا الحسابين) | ✅ نعم (double-write) | `main` ✓ | ✅ نعم (عبر `_recompute_balance`) | ✅ نعم |
| **Supplier Payment** | `liabilities_routes.py:921+` | ✅ نعم (`liability_payment`) | ✅ نعم | `main` (bank leg) + `payable` (supplier leg) | ✅ نعم | ✅ نعم |
| **Salary Payment** | `liabilities_routes.py` + `migration_routes.py:579+` | ✅ نعم | ✅ نعم | `main` (bank) + `salary_payable` (employee) | ✅ نعم | ✅ نعم |
| **Manual Account Transaction** (`POST /accounts/{id}/transactions`) | `accounts_routes.py:888` + `ledger_double_write.py` | ✅ نعم | ✅ نعم (double-write Iter-240) | `main` ✓ | ✅ نعم (`_recompute_balance`) | ✅ نعم |
| **Operating Expense** | `expenses_routes.py:229+` | ✅ نعم (`expense`) | ✅ نعم | `main` ✓ | ✅ نعم | ✅ نعم |
| **Ad Account Top-Up** | `ad_account_routes.py:1108-1113` | ✅ نعم | ✅ نعم | `main` (bank) + `balance` (ad_account) | ✅ نعم | ✅ نعم |
| **Opening Balance** | `migration_routes.py:614, 626` | ❌ | ✅ نعم | `main` ✓ | — (seed) | ✅ نعم (anchor الـ migration) |

🔴 **الحالة الوحيدة الشاذة:** BNPL Settlement.

---

## 8) Ledger vs Stored vs Displayed Balance — مقارنة افتراضية

> **ملاحظة:** لا يمكن الحصول على القيم الفعلية بدون استدعاء diagnostic endpoint على Production. هذا الجدول يوضح فقط **آلية الحساب** والمصادر المتوقعة.

| الحساب | Ledger Balance (sub=main) | Stored `current_balance` | Displayed (الـ Headline) | المتوقع: انحراف؟ |
|---|---|---|---|---|
| الراجحي | Σ main-debit − Σ main-credit | recorded في الـ doc + recompute legacy | SSOT = ledger + (stored − dw_net) | ⚠️ يحتمل (لأن BNPL يضيف لـ `balance` لا لـ `main`) |
| الأهلي | نفس | نفس | نفس | ⚠️ نفس |
| الإنماء | نفس | نفس | نفس | ⚠️ نفس |
| الصناديق النقدية | نفس | نفس | نفس | 🟢 أقل احتمالاً |

### للحصول على المقارنة الفعلية:
استدعِ من Production الـ endpoint التالي (موجود مسبقاً، read-only):
```
GET /api/diagnostics/bank-current-balance-source
```
يُرجِع لكل بنك: `accounts.current_balance` · `gl_main` · `gl_balance` · `gl_main_plus_balance` · `expected_orders` · والفروقات.

---

## 9) جذر مشكلة Iter-249 — تشريح كامل

### 9.1 لماذا اختفت تسويات BNPL من شاشة Bank UI؟

**السبب التقني (سطر بسطر):**

1. `bnpl/settlement_bridge.py:295-300`:
   ```python
   legs.append({
       "entity_type": "bank", "entity_id": bank_account_id,
       "sub_account": "balance", "side": "debit",   # ⚠️ "balance" not "main"
       "amount": transferred, "entry_type": "bnpl_settlement",
   })
   ```

2. `accounts_routes.py:195-200` (في `_ledger_based_tx_feed`):
   ```python
   rows = await db.general_ledger.find({
       "user_id": user_id,
       "entity_type": "bank",
       "entity_id": account_id,
       "sub_account": "main",   # ⚠️ فلتر صارم
       "status": "posted",
   }, ...)
   ```

3. **النتيجة:** سطور BNPL تُكتب لكن لا تظهر في `/accounts/:id/transactions`. الـ headline يبقى صحيحاً لأنه يستخدم formula مختلفة (تشمل stored + dw_net adjustment).

### 9.2 كم شاشة كانت متأثرة؟

| الشاشة | متأثرة؟ | السبب |
|---|---|---|
| `/accounts/:id` (feed) | 🔴 نعم | فلتر `sub_account="main"` |
| `/accounts/:id` (headline) | 🟢 لا | يستخدم `account_balance_ssot()` يحتسب stored+dw_net |
| `/bnpl-settlements` | 🟢 لا | يقرأ من `bnpl_settlements_imports` مباشرة |
| `/accounts` (list) | 🟢 لا (في الرصيد) | SSOT |
| `/financial-position` | 🟢 لا | `compute_financial_position()` يجمع جميع `sub_account` للـ banks (`financial_position_ssot.py:330` فقط `et == "bank"` بدون فلتر sub) |
| `/reconciliation` (banks) | ⚠️ ربما | يقرأ `current_balance` raw |
| `/transfers` (sufficient funds check) | 🔴 نعم | `transfers_routes.py:219` raw stored ⇒ قد يقبل تحويلاً بقيمة أكبر من stored لكن أقل من الحقيقي |
| `/liabilities/pay` (sufficient funds) | 🔴 نعم | `liabilities_routes.py:926` raw stored |

### 9.3 هل توجد فلاتر مشابهة في أي مكان آخر؟

| الملف · السطر | الفلتر | استخدام `sub_account="main"` فقط — مشكلة محتملة؟ |
|---|---|---|
| `accounts_routes.py:199` | `_ledger_based_tx_feed` | 🔴 **نعم** (سبب Iter-249) |
| `balance_resolver.py:82` | resolver | 🟡 ربما (لا يُستخدم في `/accounts/:id`) |
| `financial_position_ssot.py:116` | `account_balance_ssot` | 🔴 **نعم** — لكن يُعوَّض جزئياً بـ `current_balance + dw_net` |
| `financial_movements_routes.py:233` | `_attach_bank_balances` | 🟡 محتمل |
| `bnpl_statement_ui_audit_routes.py:288, 333` | audit endpoint | 🟢 مقصود (تشخيصي) |
| `universal_accounting_routes.py:1031` | comment "Banks only use sub_account=main" | 🔴 افتراض عام **خاطئ** بعد دخول BNPL bridge |

> **خلاصة:** الافتراض «البنوك تستخدم `main` فقط» مُتغلغل في معظم الكود. الـ bridge كسر هذا العقد دون توثيقه.

---

## 10) Risk Matrix

| # | المخاطرة | الاحتمال | التأثير | الدرجة | الأثر إن لم تُعالج |
|---|---|---|---|---|---|
| R1 | اختفاء BNPL settlements في `/accounts/:id` feed | حدث فعلاً | عالٍ — التاجر لا يرى سطر التحصيل | 🔴 **HIGH** | فقدان الثقة في الصفحة الأكثر استخداماً |
| R2 | تحويل بنكي يقبل/يرفض خاطئاً بناءً على `accounts.current_balance` raw | متوسط | عالٍ — قد يُسمح بمبلغ أكبر مما هو متاح فعلاً (أو العكس) | 🔴 **HIGH** | OVerdraft غير معتمد |
| R3 | `_recompute_balance` يكتب في `accounts.current_balance` ⇒ تتضارب مع SSOT بعد الـ migration | عالٍ (يُستدعى على كل POST/DELETE transaction) | متوسط — الهجين يحلها لكن هشّ | 🟡 **MEDIUM** | تعقيد صيانة + احتمال double-count مستقبلاً |
| R4 | `account_breakdown` يقارن مصدرين قديمين (`account_transactions` ⇄ stored `current_balance`) ولا يستخدم SSOT | متوسط | منخفض (تشخيصي فقط) | 🟢 LOW | معلومات مضللة في صفحة diagnostic |
| R5 | `bank-transfer-routing/map` يعرض raw `current_balance` | منخفض | منخفض (صفحة admin) | 🟢 LOW | عرض رقم قديم |
| R6 | `reconciliation_routes.py:128` يقرأ raw للبنوك (لكن SSOT للـ BNPL) | متوسط | متوسط | 🟡 **MEDIUM** | فروق في صفحة المطابقة |
| R7 | `balance_resolver.py` (Iter-195) ما زال موجوداً مع `account_balance_ssot` (Iter-217) — تكرار | عالٍ | منخفض | 🟢 LOW | تشتت + شك في أيهما SSOT |
| R8 | افتراض "Banks use only sub_account=main" مكتوب في تعليقات + كود — أصبح خاطئاً | عالٍ | عالٍ (لأنه يخفي الـ BNPL bug عن المراجعين القادمين) | 🔴 **HIGH** | اتخاذ قرارات مستقبلية مبنية على افتراض كاذب |
| R9 | لا يوجد test يضمن `ledger balance == stored balance` (drift detector) | عالٍ | متوسط | 🟡 **MEDIUM** | اكتشاف الانحرافات يأتي من المستخدم لا من الـ CI |

---

## 11) التوصية النهائية

### 11.1 لصفحة `/accounts/:id` نفسها:
> **🟢 KEEP** — الصفحة جوهرية وفريدة في النظام. لا تُكرَّر من قبل أي شاشة أخرى.

### 11.2 لـ Architecture الـ Backend خلف الصفحة:
> **🟡 MERGE — يلزم إصلاحات تتبع SSOT الحقيقي**

#### إصلاحات مقترَحة (مرتَّبة حسب الأولوية — للموافقة عليها قبل أي تنفيذ):

| # | الإصلاح | الملف · السطر | المخاطرة المُعالَجة | المعقَّد؟ | يتطلب migration؟ |
|---|---|---|---|---|---|
| **F1** | توسيع فلتر `_ledger_based_tx_feed` ليشمل `sub_account ∈ {"main", "balance"}` للحسابات البنكية. **أو** تعديل `bnpl/settlement_bridge.py` ليكتب `sub_account="main"` بدلاً من `"balance"` (الخيار الأنظف معمارياً). | `accounts_routes.py:199` أو `bnpl/settlement_bridge.py:298` | R1, R8 | متوسط | F1.a (filter): لا · F1.b (migrate sub): نعم (re-tag سجلات قديمة) |
| **F2** | إجبار `transfers_routes.py` و `liabilities_routes.py:926` على استدعاء `account_balance_ssot()` بدل `current_balance` raw للتحقق من sufficient funds | `transfers_routes.py:219` و `liabilities_routes.py:926` | R2 | بسيط | لا |
| **F3** | تجميد `_recompute_balance` لإيقاف الكتابة في `accounts.current_balance` بعد الـ migration (let SSOT compute on read) — أو على الأقل إضافة flag `is_migrated` يمنع الكتابة | `accounts_routes.py:447` | R3 | متوسط | لا (لكن يحتاج فترة مراقبة) |
| **F4** | تحديث `reconciliation_routes.py:128` لاستخدام `account_balance_ssot` للبنوك | `reconciliation_routes.py:128` | R6 | بسيط | لا |
| **F5** | حذف `balance_resolver.resolve_live_balance()` بعد التأكد من عدم وجود مستهلكين، أو دمجه مع `account_balance_ssot` | `balance_resolver.py` | R7 | متوسط | لا |
| **F6** | إضافة pytest تحت `/app/backend/tests/test_account_balance_drift_iter250.py` يفحص: `account_balance_ssot == accounts.current_balance` لكل حساب بنك/كاش بعد كل عملية كتابة | `tests/` | R9 | متوسط | لا |
| **F7** | تحديث `account_breakdown` ليقارن SSOT vs ledger vs stored بدلاً من اثنين legacy فقط | `accounts_routes.py:657` | R4 | متوسط | لا |
| **F8** | تحديث `bank-transfer-routing/map` لاستخدام SSOT | `accounts_routes.py:649` | R5 | بسيط | لا |

### 11.3 ❌ لا توصية بـ DEPRECATE
لا توجد شاشة بديلة. حتى `/financial-movement/all` (الجديد) لا يُظهر تفاصيل الحركة لحساب واحد بنفس عمق هذه الصفحة.

---

## 12) ما تم استثناؤه عمداً من هذا التقرير

- ❌ أي **تغيير في الكود** — رفض تام (READ-ONLY).
- ❌ أي **migration / recompute / re-tagging** — رفض تام.
- ❌ أي **DB update** — رفض تام.
- ❌ تنفيذ أي endpoint جديد — لم يُنشأ ولم يُعدَّل.

---

## 13) الخطوة التالية المقترَحة (تنتظر موافقتك)

اختر إحدى:

1. ✅ **اعتماد التوصية** (KEEP + MERGE) ⇒ ننتقل إلى P1 التالي: مراجعة `/purchase-invoices` (Read-Only).
2. 🔧 **اختيار إصلاح واحد F1–F8 لتنفيذه** ⇒ سأكتب RFC قصير لخطوات التنفيذ + الاختبار قبل أي تعديل.
3. 🔍 **استدعاء diagnostic على Production** لجلب الأرقام الفعلية (Ledger vs Stored لكل بنك) ⇒ يستخدم endpoint موجود `/api/diagnostics/bank-current-balance-source` (read-only). انسخ النتيجة هنا لنتأكد من حجم الانحراف.
4. ⏭️ **تأجيل قرارات الإصلاح** والمضي قدماً للمراجعة التالية.

---

**نهاية تقرير Iter-250b · P1.4** — `/accounts/:id`
