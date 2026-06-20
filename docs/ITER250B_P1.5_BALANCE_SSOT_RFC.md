# Iter-250b · P1.5 — RFC تنفيذي: توحيد SSOT للرصيد + إصلاح Iter-249

**Status:** 📐 RFC للموافقة · لم تُنفَّذ أي إصلاحات بعد
**Iteration:** 250b · Phase 1.5
**Author:** E1 Agent
**Builds on:** `ITER250B_P1.4_ACCOUNTS_REVIEW.md`
**Scope:** خطة تنفيذية لـ 8 إصلاحات (F1–F8) لجعل `general_ledger` هو الـ SSOT الوحيد للرصيد، وإصلاح جذر مشكلة Iter-249.

---

## 0) ما يُسلَّم في هذه الجولة (READ-ONLY)

| البند | الحالة |
|---|---|
| 📄 RFC تنفيذي (هذا المستند) | ✅ مكتمل |
| 🔧 `GET /api/diagnostics/balance-drift` (dry-run) | ✅ مُنفَّذ (read-only) |
| 🖥️ صفحة `/audit/balance-drift` (UI تشخيصي Read-Only) | ✅ مُنفَّذ |
| ❌ أي تعديل في BNPL bridge / accounts_routes / transfers / liabilities | ❌ لم يُنفَّذ — تنتظر الموافقة |
| ❌ أي DB write / migration / recompute / cleanup | ❌ ممنوع تماماً |

---

## 1) الهدف النهائي (Target State)

> **`general_ledger` هو مصدر الحقيقة الوحيد لرصيد أي حساب بنك/كاش/منصة دفع.**

ينتج عن ذلك ٤ التزامات بنيوية:

| # | الالتزام | الوضع الحالي | الوضع المستهدف |
|---|---|---|---|
| C1 | كل قراءة رصيد حساس (transfers, sufficient funds, displays) → تمر عبر `account_balance_ssot()` | جزئي — 4 شاشات تقرأ raw | 100% |
| C2 | `accounts.current_balance` يصبح Cache فقط (مخرَج للأداء)، لا يستهلكه أي قرار مالي | لا يزال يُكتب ويُقرأ كمصدر حقيقة | يصبح كحقل عرض فقط |
| C3 | BNPL Bridge والـ UI feed يستخدمان **نفس** `sub_account` للبنك | mismatch (`balance` vs `main`) | موحَّد |
| C4 | اختبار يومي تلقائي يكشف أي drift بين Ledger SSOT و stored | غير موجود | موجود (pytest + dashboard) |

---

## 2) قرارات معمارية (Architecture Decisions)

### AD-1 · أيهما نُغيِّر: الـ Bridge أم الـ UI Filter؟

| الخيار | الفائدة | التكلفة |
|---|---|---|
| **A. تعديل UI filter** ليشمل `{main, balance}` (في `accounts_routes.py:199`) | بسيط جداً (سطر واحد) · لا migration | يُكرِّس الفوضى — يُبقي `sub_account="balance"` للـ bridge فقط (دلالة غامضة) |
| **B. تعديل BNPL bridge** ليكتب `sub_account="main"` للـ bank leg | يُوحِّد العقد · يُلغي الازدواجية | يحتاج re-tag لـ N سطور موجودة في DB (one-time migration) |

> **الاقتراح:** **A أولاً (Quick Fix · غير مدمر · فوري)** ثم **B لاحقاً (Clean Fix · migration مُحكمة)** كمرحلتين منفصلتين.

### AD-2 · ماذا نفعل بـ `_recompute_balance` (الذي يحدّث `accounts.current_balance`)؟

| الخيار | الفائدة | التكلفة |
|---|---|---|
| Freeze كلياً (no-op) للحسابات المهاجَرة | يجعل SSOT الوحيد هو ledger | يكسر `_account_with_meta`'s dw_net hack المعتمد على stored |
| Freeze لكن إبقاءه كـ Cache (مع flag `is_migrated`) | يحتفظ بالأداء + يوضح الدلالة | تعقيد إضافي |
| إبقاؤه كما هو لكن إلزامياً يُساوي SSOT (مع assert) | لا تغيير في السلوك · يكشف الانحرافات | لا يحلّ المشكلة |

> **الاقتراح:** خطوة وسطى — **Cache Wrapper:** نُعيد تسمية الحقل دلالياً إلى `current_balance_cached` (داخلياً) ونوثّق أن أي قراءة حقيقية تستخدم `account_balance_ssot()`. لا حاجة لـ DB rename — مجرد توثيق + linter rule.

### AD-3 · أين يُحسم الـ Hybrid Formula (Iter-240)؟

الـ formula الحالي:
```
balance = ledger_net + (current_balance − dw_net)   [if no opening_balance row]
```

> **الاقتراح:** إبقاؤه كما هو في المرحلة 1 (لا نلمس Iter-240). في المرحلة 3 بعد ترحيل كل الحسابات إلى opening_balance, نُلغي الـ hybrid ونصبح `balance = ledger_net` فقط.

---

## 3) خطة تنفيذية مُرحَّلة على ٤ مراحل

> كل مرحلة قابلة للموافقة والتنفيذ على حدة. **لا تنتقل لمرحلة إلا بعد تمرير Dry-Run + اختبار يدوي على Production.**

### Phase 0 · Discovery (الحالية) ✅
- ✅ تقرير P1.4 (forensic)
- ✅ هذا الـ RFC
- ✅ `/api/diagnostics/balance-drift` (مُنفَّذ في هذه الجولة)
- ✅ صفحة `/audit/balance-drift` (مُنفَّذ في هذه الجولة)

**Exit criteria:** أنت تُراجع نتائج Drift على Production. لو أي حساب يُظهر فرقاً > 1 ر.س، نُقرّر هل F1 يكفي أم نحتاج F1+F2 معاً.

---

### Phase 1 · Quick Fix لـ Iter-249 (F1 only) 🟡

**الإصلاح:** توسيع فلتر `_ledger_based_tx_feed` ليشمل `sub_account ∈ {main, balance}`.

| الملف | السطر | التغيير المقترَح |
|---|---|---|
| `backend/accounts_routes.py` | 195–200 | استبدال `"sub_account": "main"` بـ `"sub_account": {"$in": ["main", "balance"]}` |

**أثر:**
- ✅ تسويات BNPL تظهر في الـ feed
- ✅ الـ running balance يحسبها صحيحاً (لأن `side` و `amount` مكتوبان بصواب)
- ⚠️ سطور `sub_account="balance"` التي تنتمي لـ entities **غير بنوك** (مثل ad_account) لن تتأثر لأن الفلتر يبقى `entity_type="bank"`

**Risk:** 🟢 LOW · لا migration · قابل للـ rollback بـ git revert.

**اختبار:**
- استدعاء `GET /accounts/{bank_id}/transactions` للراجحي بعد الـ deploy ⇒ يجب أن تظهر تسويات Tabby/Tamara/Emkan.
- مقارنة `Σ balance_after (آخر سطر)` مع `account.current_balance` ⇒ فرق < 0.5 ر.س.

---

### Phase 2 · إجبار جميع القراءات الحساسة على SSOT (F2 + F4 + F8) 🟡

**الهدف:** إزالة كل قراءة `accounts.current_balance` raw من القرارات المالية.

| Fix | الملف · السطر | التغيير |
|---|---|---|
| **F2.a** | `backend/transfers_routes.py:219` | `from_bal = await account_balance_ssot(db, user_id=uid, account=from_acc)` بدل `from_acc.get("current_balance")` |
| **F2.b** | `backend/liabilities_routes.py:926` | نفس الاستبدال قبل التحقق من sufficient funds |
| **F4** | `backend/reconciliation_routes.py:128` | استدعاء `account_balance_ssot` للبنوك (الـ BNPL canonical موجود مسبقاً) |
| **F8** | `backend/accounts_routes.py:649` (`bank-transfer-routing/map`) | استدعاء `account_balance_ssot` |

**أثر:**
- ✅ يصبح من المستحيل قبول تحويل بمبلغ أكبر من المتاح الحقيقي
- ✅ شاشة المطابقة تُظهر الرقم نفسه الذي في `/accounts/:id`
- ✅ تطابق UI شامل

**Risk:** 🟡 MEDIUM — كل تغيير يحتاج اختبار وحدوي + يدوي.

**اختبار:**
- محاولة تحويل من بنك برصيد ledger = 1000, stored = 1500 بمبلغ 1200 ⇒ يجب الرفض.
- نفس السيناريو في `pay_liability` ⇒ رفض.
- مقارنة `/reconciliation` مع `/accounts` ⇒ يجب أن يتطابقا.

---

### Phase 3 · توحيد BNPL Bridge على `sub_account="main"` (F1b) 🔴

**الهدف:** Migration واحدة دائمة تنقل سجلات BNPL القديمة من `sub_account="balance"` إلى `"main"` + تعديل الـ bridge ليكتب `"main"` للسجلات الجديدة.

| الخطوة | الإجراء |
|---|---|
| 3.1 | بناء `POST /api/admin/migrate-bnpl-bank-subaccount?dry_run=true` يُحصي عدد السطور المتأثرة + يُظهر `before/after` |
| 3.2 | الموافقة من المستخدم على Dry-Run output |
| 3.3 | تنفيذ Migration مع `dry_run=false` + Idempotency lock |
| 3.4 | تعديل `backend/bnpl/settlement_bridge.py:298` ⇒ `"sub_account": "main"` |
| 3.5 | إزالة workaround F1 من `accounts_routes.py:199` (يعود لـ `"main"` فقط) |
| 3.6 | حذف workaround في `account_balance_ssot` (Iter-240 dw_net hack) **اختياري — قد يبقى للحماية** |

**Risk:** 🔴 HIGH — Migration دائمة في DB. تتطلب نسخة احتياطية كاملة قبل التنفيذ.

**اختبار:**
- قبل migration: `GET /api/diagnostics/balance-drift` يُظهر `gl_main` و `gl_balance` لكل بنك.
- بعد migration: `gl_balance` للبنوك = 0 · `gl_main` = `gl_main + gl_balance` السابق.
- جميع الـ displayed balances تبقى **متطابقة** (لأن الـ formula الهجين كانت تضيفها أصلاً).

---

### Phase 4 · حماية مستدامة (F3 + F5 + F6 + F7) 🟢

| Fix | الإجراء |
|---|---|
| **F3** | تعديل `_recompute_balance` ليُكتب فقط `current_balance_cached_at` (لا تأثير محاسبي) |
| **F5** | حذف `balance_resolver.resolve_live_balance` بعد التأكد من عدم وجود مستهلكين (grep) |
| **F6** | إضافة `tests/test_balance_drift_iter250.py` يفحص `account_balance_ssot == accounts.current_balance ± 0.02` لكل حساب — يفشل CI لو drift |
| **F7** | تحديث `account_breakdown` ليُضيف صف `ledger_ssot` للمقارنة |

**Risk:** 🟢 LOW.

---

## 4) معايير القبول (Acceptance Criteria) النهائية

| Criterion | كيف نتحقَّق |
|---|---|
| AC-1: BNPL settlements تظهر في `/accounts/:id/transactions` | اختبار يدوي + screenshot للراجحي بعد آخر تسوية Tabby/Tamara |
| AC-2: لا توجد قراءة raw `current_balance` في قرارات مالية | `grep -rn "\.current_balance" backend/` ⇒ كل النتائج إما enrichment أو display أو comment |
| AC-3: `/api/diagnostics/balance-drift` يُرجع `drift = 0` لكل حساب | UI dashboard أخضر بالكامل |
| AC-4: pytest `test_balance_drift_iter250.py` ينجح | CI pipeline أخضر |
| AC-5: BNPL bridge يكتب `sub_account="main"` فقط | `grep "sub_account.*balance" backend/bnpl/` ⇒ صفر نتائج للبنوك |

---

## 5) خطة الاختبار التفصيلية (Test Plan)

### 5.1 حسابات تحت الاختبار

| الحساب | السبب | السيناريو المتوقع |
|---|---|---|
| **الراجحي** | يستلم Tabby + COD + رواتب + تحويلات | يجب أن تظهر **جميع** الحركات في feed |
| **الأهلي** | يستلم Tamara + Emkan + COD | نفس |
| **الإنماء** | تحويلات داخلية + موردين | feed كامل |
| **صندوق نقدي رئيسي** | مصاريف يومية + سُلف موظفين | feed كامل |
| **صندوق نقدي فرعي** | مدخلات صغيرة فقط | feed كامل |

### 5.2 مصادر العمليات

| العملية | الـ entry_type المتوقع | sub_account المتوقع | اختبار |
|---|---|---|---|
| **Tabby Settlement** | `bnpl_settlement` | بعد F1b: `main` · قبل: `balance` | استدعاء `/bnpl-settlements` ⇒ التسوية ⇒ /accounts/:bank/transactions |
| **Tamara Settlement** | `bnpl_settlement` | نفس | نفس |
| **Emkan Settlement** | `bnpl_settlement` | نفس | نفس |
| **COD Settlement** | `cod_settlement` | `main` (✓ مسبقاً) | يظهر في feed بدون تغيير |
| **Supplier Payment** | `liability_payment` | `main` | يجب أن يفشل لو رصيد < المبلغ |
| **Salary Payment** | `salary_payment` | `main` | نفس |
| **Internal Transfer** | `internal_transfer` | `main` | نفس |
| **Manual Account Tx** | `manual_<type>` | `main` | نفس |

### 5.3 سيناريوهات Drift

| سيناريو | Drift متوقع قبل الإصلاح | Drift متوقع بعد الإصلاح |
|---|---|---|
| بنك استلم Tabby بعد migration | `displayed > stored` بحجم التسوية | 0 |
| بنك استلم تحويل يدوي قبل migration | 0 (لأن `_recompute_balance` يحدّث stored) | 0 |
| بنك استلم Tabby قبل migration + بعد | جزئي (بعد فقط) | 0 |
| صندوق نقدي بدون BNPL | 0 | 0 |

### 5.4 Regression Tests (لا يجب أن تتغير)

- إجمالي assets في `/financial-position-ledger` يبقى نفسه ± 0.02 ر.س
- `/accounts` list يُظهر نفس الأرصدة قبل/بعد
- `/dashboard` summary cards بدون تغيير

---

## 6) متطلبات نشر آمنة (Deployment Safety)

| المرحلة | متطلب |
|---|---|
| Phase 1 (F1) | git tag قبل deploy · rollback plan جاهز · مراقبة logs لمدة 24h |
| Phase 2 (F2+F4+F8) | تشغيل `test_balance_drift_iter250.py` قبل deploy · اختبار يدوي على 3 حسابات على الأقل |
| Phase 3 (F1b) | نسخة احتياطية كاملة لـ `general_ledger` · Dry-Run output مُوقَّع من المستخدم · idempotency key |
| Phase 4 (F3+F5+F6+F7) | تنفيذ بعد استقرار Phase 1–3 لمدة أسبوع |

---

## 7) Out of Scope (خارج نطاق هذا الـ RFC)

- 🚫 أي تغيير في صيغة BNPL canonical balance
- 🚫 أي تغيير في `compute_financial_position`
- 🚫 أي تغيير في `general_ledger` schema (الحقول/الفهارس)
- 🚫 إعادة هيكلة `accounts` collection
- 🚫 ربط Inventory / SKUs

---

## 8) قرار المستخدم المطلوب

اختر إحدى:

1. ✅ **اعتماد المرحلة 0 (التشخيص)** ⇒ نقرأ نتائج `/audit/balance-drift` من Production ثم نوافق على Phase 1.
2. 🛠️ **اعتماد F1 فقط (Quick Fix)** ⇒ ننفّذ Phase 1 فوراً ونتأكد من ظهور BNPL.
3. 📋 **اعتماد F1+F2+F4+F8 معاً** ⇒ ننفّذ Phase 1 + Phase 2 في deploy واحد.
4. ⏸️ **تأجيل** ⇒ ننتقل لـ `/purchase-invoices` ونعود لاحقاً.

---

## 9) ملاحق

### الملحق A — Endpoint Contract لـ `/api/diagnostics/balance-drift`

```
GET /api/diagnostics/balance-drift
Query params:
  - account_id (optional): فحص حساب واحد فقط
  - account_type (optional): "bank" | "cash" | "payment_platform" | "all" (default)
  - include_zero_drift (optional, bool, default=false)
  - tolerance (optional, float, default=0.02)

Response 200:
{
  "ok": true,
  "tolerance": 0.02,
  "generated_at": "2026-02-XX...",
  "accounts": [
    {
      "id": "...",
      "name": "بنك الراجحي",
      "account_type": "bank",
      "stored_current_balance": 12345.67,           // accounts.current_balance
      "ledger_main_net": 11500.00,                  // gl: sub=main net
      "ledger_balance_net": 845.67,                 // gl: sub=balance net (BNPL bridge)
      "ledger_main_plus_balance": 12345.67,
      "ssot_value": 12345.67,                       // account_balance_ssot()
      "account_transactions_walk": 12345.67,        // Σ in − Σ out
      "displayed_balance": 12345.67,                // المعروض في الـ UI = SSOT بعد enrichment
      "drift_ssot_vs_stored": 0.00,
      "drift_ssot_vs_walk": 0.00,
      "drift_ledger_main_vs_displayed": 845.67,    // ⚠️ موجب = BNPL مفقود في feed
      "has_bnpl_drift": true,
      "tx_in_ledger": 142,
      "tx_in_account_transactions": 87,
      "tx_in_feed_main_only": 87,                   // ما يظهر فعلاً في /accounts/:id
      "tx_missing_from_feed": 55,                   // BNPL settlements مخفية
      "status": "ITER249_BNPL_HIDDEN"               // ok | drift | ITER249_BNPL_HIDDEN
    },
    ...
  ],
  "summary": {
    "total_accounts": 12,
    "ok": 8,
    "drift": 1,
    "iter249_bnpl_hidden": 3,
    "total_hidden_amount": 87123.45
  }
}
```

### الملحق B — أرقام الإصدارات والاعتمادات

- يعتمد على: Iter-192 (ledger SSOT) · Iter-217 (account_balance_ssot) · Iter-240 (double-write) · Iter-249 (sub_account mismatch discovery)
- لا يكسر: أي endpoint موجود (فقط يُغيّر السلوك الداخلي)

---

**نهاية RFC · Iter-250b · P1.5**
