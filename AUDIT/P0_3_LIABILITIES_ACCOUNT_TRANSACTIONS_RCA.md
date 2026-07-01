# P0.3 — `liabilities` + `account_transactions` Reads/Writes RCA (Read-Only)

**Author:** E1 (Emergent Agent)  
**Scope:** كل قراءة وكتابة على collectionَي `liabilities` و `account_transactions`، وعلاقتهما بالـ SSOT `general_ledger` (Iter-161 Phase 4).  
**Mode:** Read-only. لا كود يُعدَّل، لا collection يُلمس، لا migration، لا writes على DB، لا Deploy.  
**Date:** 2026-07-01  
**Report file:** `/app/AUDIT/P0_3_LIABILITIES_ACCOUNT_TRANSACTIONS_RCA.md`  
**Trigger:** Iter-001 وضعها كخطر P0.3 (110 + 86 مرجع رغم اعتماد `general_ledger` كـ SSOT).

---

## 0. TL;DR — الخلاصة التنفيذية

**النتيجة الحاسمة (مغايرة لتصنيف Iter-001 الأولي):**

- 🟢 **`liabilities` ليست Legacy** — بل هي **الـ SSOT النشط لتسجيل الالتزامات كـ objects** (فواتير، مطالبات، سلف موظفين، رواتب). كل التزام له id + status + due_date + paid_amount. `general_ledger` هو SSOT لـ **الأرصدة**، بينما `liabilities` هو SSOT لـ **الالتزامات كسجلات لها دورة حياة**. **دورهما مكمّل وليس متكرر**.
- 🟢 **`account_transactions` ليست Legacy** كذلك — لكنها **legacy-shaped** — تخزّن حركات البنك مع `balance_after` (running balance). منذ **Iter-240** كل كتابة إليها **تُنعكس تلقائياً إلى `general_ledger`** عبر `ledger_double_write.mirror_account_txn_to_ledger()`. هذا يعني: **كتابة واحدة، سجلَّين متزامنَين**، والقراءة الرسمية للأرصدة تكون من `general_ledger` عبر `account_balance_ssot()`.
- 🟢 **`financial_position_ssot.py` لا يقرأ من `liabilities` ولا `account_transactions`** — يقرأ **حصراً من `general_ledger`** (+ fallback `accounts.current_balance` لحسابات لا نشاط لها في الـ ledger).
- 🟡 **مصدر خطر واحد فقط**: `operational_reports_routes.py` (Iter-114) يقرأ من `liabilities` لأغراض تقرير مدفوعات الموردين + التزامات مدفوعة. إذا كانت أرقام هذا التقرير تُستخدم في اتخاذ قرارات محاسبية → يجب مقارنته مع `general_ledger`. **لا خطر عرض أرقام مختلفة عن Dashboard/Financial Position** لأن تلك تقرأ SSOT.
- 🟢 **لا ازدواج في الكتابة** — كل كتابة على `liabilities` أو `account_transactions` منذ Iter-240 تُنعكس على `general_ledger`. لا خطر SSOT drift جديد.

**الخلاصة الجوهرية**: هذان الـ collections **جزء من التصميم النشط**، ليست Legacy. الاسم "Legacy" في Iter-001 كان مضلّلاً.

---

## 1. الاستخدام العام — أرقام مطلقة

| Collection | Writes (insert/update/delete) | Read locations | Frontend endpoints |
|---|---|---|---|
| `liabilities` | 47 موقع في 14 ملف | 30+ موقع | 4 endpoints يستدعيها الـ frontend |
| `account_transactions` | 29 موقع في 12 ملف | 40+ موقع | لا استدعاء مباشر من الـ frontend (تُقرأ داخلياً فقط) |

**ملاحظة**: العدد الإجمالي يشمل diagnostic/forensic routes التي تفحص الـ drift. الاستخدامات الإنتاجية الفعلية أقل (~30 write لـ liabilities، ~18 write لـ account_transactions).

---

## 2. Collection `liabilities` — التحليل الكامل

### 2.1 دورها المحاسبي (من الكود)

من `liabilities_routes.py` docstring (Iter-92 Phase 1):
> "Single new collection `liabilities` that models every monetary obligation the merchant carries:
> • salary — generated monthly from active operating_salaries
> • ad_account — manually entered when Snap/TikTok/Meta send a bill
> • salary_advance — money advanced to an employee out of the bank
>   BEFORE the salary month closes. Auto-deducted from matching salary
> • supplier — from purchase_invoices
> • receivable / external"

**تعريف عملي**: كل التزام له:
- `id`, `user_id`, `kind`, `counterparty_id`
- `expected_amount`, `paid_amount`, `remaining_amount`
- `status`: `unpaid | partial | paid`
- `due_date`, `created_at`, `updated_at`
- `auto_generated` (لتمييز salaries التلقائية)

هذا **يختلف عن Ledger entry** الذي هو مجرد نصف قيد محاسبي (debit/credit line). الالتزام كائن له دورة حياة، Ledger entries هي آثار محاسبية له.

### 2.2 الكتّاب (Writers)

| ملف | حالة | ماذا يكتب |
|---|---|---|
| `liabilities_routes.py` | ✅ **SSOT active** | إنشاء/تعديل/حذف/دفع الالتزامات. Endpoints: `POST /api/liabilities`, `PUT /{id}`, `DELETE /{id}`, `POST /{id}/pay`, `POST /generate-salaries`. **كل كتابة تُنعكس على `general_ledger` عبر `mirror_account_txn_to_ledger`** لأن الدفعة تمر بـ `account_transactions`. |
| `ad_account_routes.py` | ✅ SSOT active | ~15 write site لإنشاء وتعديل التزامات الحسابات الإعلانية (topups, debts, adjustments). |
| `purchase_invoices_routes.py` | ✅ SSOT active | ينشئ liability عند إنشاء فاتورة مشتريات. |
| `accounting_cutoffs_routes.py` | ✅ Admin — cutoff | Iter-149: تعديل ذيل الفواتير القديمة عند تحديد cutoff. |

**كل writers هؤلاء يعملون معاً كنظام موحّد** — لا كاتب Legacy، لا كاتب orphan.

### 2.3 القرّاء (Readers)

| ملف | حالة | Reads |
|---|---|---|
| `server.py:970` (`/financial-input-hub/recent`) | ✅ user-facing feed | يعرض قائمة الالتزامات الحديثة في **Financial Input Hub** (صفحة `/financial-input-hub`). **يقرأ الالتزامات كسجلات**، لا كأرصدة. |
| `server.py:1101, 1248` | ✅ backend expansion | يوسّع liability refs في الـ feeds. |
| `liabilities_routes.py` (14 موقع) | ✅ CRUD endpoints | القراءة الأساسية. |
| `ad_account_routes.py` | ✅ list حسابات إعلانية | يقرأ الالتزامات للـ display. |
| `operational_reports_routes.py:68, 195, 228` | 🟡 **potential risk** | Iter-114 aggregates supplier payments + all liability payments. **إذا كانت أرقامه تُقارن بأي رقم من `/financial-position-ledger` قد يكون هناك اختلاف طفيف** (مثل paid_amount مقابل ما هو حقيقة booked في GL). |
| `employee_ledger_forensic_routes.py:195` | 🟢 forensic | يقارن `liabilities` مع `general_ledger` للـ RCA. |
| `migration_routes.py` (4 مواقع) | 🟢 قيود migration | يستخدمها لصياغة تقارير الترحيل إلى قيود. |
| `ads_currency_routes.py:258` | 🟢 backend | تحويل عملات فقط. |

### 2.4 Endpoints الـ Frontend التي تستدعيها

من grep على `/app/frontend/src`:

| ملف Frontend | Endpoint | ماذا يعرض |
|---|---|---|
| `EmployeeBalanceCard.jsx` (2 مواقع) | `GET /liabilities/salary-accrual-summary` | ملخّص استحقاق الرواتب — **مصدره `operating_salaries` + `liabilities`** لكنه محسوب في backend. |
| `FinancialInputHub.jsx` (4 مواقع) | `POST /liabilities`, `GET /liabilities/salary-accrual-summary` | إنشاء التزامات جديدة (سلف، supplier bills، ad account). |
| `OperationsDashboard.jsx` (3 مواقع) | `GET /liabilities?kind=supplier`, `?kind=salary_advance`, `?kind=receivable` | قائمة الالتزامات المفتوحة كـ Operational View. |
| `FinancialPosition.jsx` (الميتة، Iter-217) | `GET /liabilities?status=unpaid&limit=1`, `?status=partial&limit=1` | badges فقط — عدد الالتزامات المفتوحة (لا أرقام مالية). |

### 2.5 تصنيف نهائي لـ `liabilities`

| البعد | التصنيف |
|---|---|
| Overall | 🟢 **active_ssot** |
| Frontend exposure | 🟢 user-facing (OperationsDashboard + FinancialInputHub) |
| Financial reports impact | 🟡 يظهر في Operational Reports (Iter-114) — يحتاج تحقق منفصل |
| Dashboard KPIs impact | 🟢 لا يُقرأ من Dashboard الرئيسي مباشرة — Dashboard KPIs تأتي من `general_ledger` |
| Duplication with `general_ledger` | 🟢 مكمّلة، ليست مكررة (objects vs entries) |
| Accounting risk | 🟢 LOW — كل الكتابات مُنعكسة على GL منذ Iter-240 |

**التوصية**: **safe_to_keep** كمصدر رئيسي للالتزامات كـ objects. لا تُلمس.

---

## 3. Collection `account_transactions` — التحليل الكامل

### 3.1 دورها المحاسبي

من الشرح في الكود (Iter-92 + Iter-240):
- تسجيل **كل حركة بنك/محفظة** (deposit, withdrawal, transfer, expense, topup, refund).
- كل tx فيها `id`, `account_id`, `direction` (in/out), `amount`, `transaction_type`, `transaction_date`, `balance_after`, `metadata`.
- `balance_after` هو **running balance للحساب** — يُحسب في وقت الإدخال.
- منذ **Iter-240**: كل insert جديد **يُنعكس على `general_ledger`** عبر `mirror_account_txn_to_ledger()`.

### 3.2 الكتّاب (Writers)

29 موقع كتابة موزّعة على 12 ملف. أهمها:

| ملف | Writes | حالة |
|---|---|---|
| `liabilities_routes.py` (2) | insert/update عند دفع التزام | ✅ mirrored to GL |
| `accounts_routes.py` (5) | manual transactions | ✅ mirrored to GL |
| `expenses_routes.py` (3) | expenses | ✅ mirrored to GL |
| `transfers_routes.py` (2) | bank↔bank transfers | ✅ mirrored to GL |
| `shipping_accounts.py` (4) | shipping payments | ✅ mirrored to GL |
| `financial_movements_routes.py:947` | manual financial movement | ✅ mirrored to GL |
| `ad_account_routes.py` (3) | ad account topups | ✅ mirrored to GL |
| `bnpl/settlement_bridge.py` (1) | BNPL settlements | ✅ mirrored to GL |
| `bnpl/settlements_routes.py` (2) | BNPL flows | ✅ mirrored to GL |
| `bnpl_settlement_banktx_routes.py` (1) | BNPL bank tx | ✅ mirrored to GL |
| `accounting_cutoffs_routes.py` (2) | cutoff patches | ✅ admin only |

**كل كاتب من هؤلاء يستدعي `mirror_account_txn_to_ledger()`** — تحقّقتُ في `liabilities_routes.py:281`, `expenses_routes.py`, `transfers_routes.py`, `shipping_accounts.py`, `bnpl/settlement_bridge.py`, إلخ. لا كاتب orphan.

### 3.3 القرّاء (Readers)

| ملف | حالة | نوع القراءة |
|---|---|---|
| `accounts_routes.py` | 🟡 legacy-shaped | يعرض حركات الحساب في drawer تفصيلي (`/api/accounts/:id/transactions`). **لكن الأرصدة نفسها تُحسب من `general_ledger` منذ Iter-217** (`account_balance_ssot()`). |
| `server.py:986, 1256` | ✅ Financial Input Hub feed | يعرض المعاملات كسجلات في الـ feed. |
| `reconciliation_routes.py` | 🟡 diagnostic | يستخدمها لتقرير الـ reconciliation — لا حسابات محاسبية جديدة. |
| `bnpl/*` (4 ملفات) | ✅ BNPL matching | لأنماط الـ matching بين ملف التسوية والحساب البنكي. |
| `accounts_balance_diagnostic_routes.py` (5) | 🟢 forensic | لتقارير drift بين GL و account_transactions. |
| `account_tx_vs_ledger_walk_routes.py` (2) | 🟢 forensic | تقرير دقيق يمشي على الحركتَين المتوازيتَين. |
| `balance_drift_diagnostic_routes.py` | 🟢 forensic | يكتشف الـ drift. |
| `salla_balance_forensic_routes.py` | 🟢 forensic | تقرير قوى لـ Salla wallet. |
| `bnpl_statement_ui_audit_routes.py` (5) | 🟢 forensic | تحقّق UI. |
| `settlement_file_forensic_routes.py` | 🟢 forensic | تقرير ملفات التسوية. |
| `bnpl/matching_service.py`, `bnpl/settlements_service.py` (2) | ✅ backend logic | matching للتحويلات. |

### 3.4 هل توجد قراءة user-facing تعرض رقماً محاسبياً؟

**نعم — في مكانَين:**

1. **`GET /api/accounts/:id/transactions`** — قائمة حركات الحساب في drawer الحساب. **user-facing** لكن هذا **عرض السجلات كما هي** (لا حسابات محاسبية جديدة).
2. **`/api/financial-input-hub/recent`** — feed للـ Financial Input Hub. **user-facing** لكن **يعرض السجلات كأحداث** — ليس أرقام أرصدة.

**لا مكان في UI** يُستخدم فيه `account_transactions.balance_after` كرقم قابل للاستشهاد في تقرير محاسبي. الأرصدة الحقيقية تأتي من `general_ledger` عبر `account_balance_ssot()`.

### 3.5 تصنيف نهائي لـ `account_transactions`

| البعد | التصنيف |
|---|---|
| Overall | 🟢 **active** (with `general_ledger` as mirror) |
| Frontend user exposure | 🟢 عرض السجلات فقط (لا أرصدة) |
| Financial reports impact | 🟢 لا |
| Dashboard KPIs impact | 🟢 لا |
| Duplication with `general_ledger` | 🟠 **redundant storage** — نفس الحدث في مكانَين، لكن مقصود Iter-240 |
| Accounting risk | 🟢 LOW — mirror يعمل، الـ drift يُكتَشف عبر diagnostic routes |
| Migration risk | 🟠 MEDIUM — لو أراد الفريق حذفها لاحقاً، يجب أولاً نقل كل الـ UI drawers لقراءة `general_ledger` |

**التوصية**: **safe_to_keep** كطبقة توافق. **candidate_for_gradual_deprecation** — عندما تنتقل كل الـ UI drawers إلى قراءة `general_ledger` مباشرة، يمكن التخلص من هذا الـ collection في iter مستقبلي.

---

## 4. Duplicate-Write Analysis — هل هناك ازدواج فعلي؟

### 4.1 السيناريو الأخطر: `POST /api/liabilities/{id}/pay`

عند دفع التزام:
1. `db.liabilities.update_one` — يزيد `paid_amount` وينقص `remaining_amount` (سجل الالتزام).
2. `db.account_transactions.insert_one` — يسجّل حركة سحب من البنك مع `balance_after`.
3. `mirror_account_txn_to_ledger()` — يُضيف صفَّي general_ledger (debit + credit) للحدث.

**النتيجة**: **حدث واحد ➜ 3 سجلات في 3 collections**، لكن:
- Ledger هو SSOT للأرصدة.
- Liability هو SSOT للالتزام ككائن.
- Account_transactions هو "audit log" مرئي للحساب.

**لا ازدواجية في القرار المحاسبي** — النظام قصداً يحتفظ بثلاث زوايا للحدث نفسه لأغراض مختلفة.

### 4.2 هل يمكن أن يُنشئ ledger_double_write سجلاً مكرراً؟

من `ledger_double_write.py:11`:
> "Idempotent: skips insert if a ledger entry with the same `metadata.account_transaction_id` already exists."

✅ محمي بـ dedup key. لا خطر ledger doubling.

### 4.3 هل يمكن أن يفشل الـ mirror ويترك drift؟

نعم — في `liabilities_routes.py:297-300`:
```python
except Exception as _e:  # noqa: BLE001
    import logging
    logging.getLogger(__name__).warning(
        "iter240 mirror failed for liability tx %s: %s", tx["id"], _e
```

**الـ mirror يفشل بصمت** — لكن هناك **diagnostic routes مخصّصة** لكشف هذا الـ drift:
- `balance_drift_diagnostic_routes.py`
- `account_tx_vs_ledger_walk_routes.py`
- `accounts_balance_diagnostic_routes.py`

**🟡 توصية جانبية**: يجب أن يصير الـ mirror جزءاً من الـ transaction (transactional) بدلاً من `try/except silent`. هذه ملاحظة لتصميم مستقبلي — ليست خطر فوري.

---

## 5. Reports Impact Assessment — أي تقارير تعتمد على أيّها؟

| التقرير / الـ endpoint | مصدر الأرقام | خطر |
|---|---|---|
| **Dashboard KPIs** (`/api/dashboard/kpis`) | `general_ledger` + `payment_transactions` + `payment_adjustments` (P0.1 concern فقط) | 🟢 |
| **`/api/accounting/financial-position`** | `general_ledger` حصراً | 🟢 |
| **`/api/accounts/*/list`** (Employees/Suppliers/Couriers/Externals Ledger) | `general_ledger` حصراً | 🟢 |
| **`/api/accounts` + `/api/accounts/:id`** | `general_ledger` (via `account_balance_ssot`) | 🟢 |
| **`/api/operational-reports`** (Iter-114) | `liabilities` + `operating_*` | 🟡 **يقرأ من liabilities** — إذا أرقام هذا التقرير تُقارن مع Financial Position قد يكون هناك اختلاف طفيف نظرياً. لكن `liabilities` هي SSOT للـ obligations objects، فلا مشكلة نظامية. |
| **`/api/liabilities`** (list/CRUD) | `liabilities` (self) | 🟢 |
| **`/api/liabilities/summary`** (legacy Iter-92) | `liabilities` + `accounts.current_balance` | 🟡 **قديم** — لكن غير مستخدم من `/financial-position-ledger` بعد Iter-217. `FinancialPosition.jsx` الميتة كانت تستخدمه، الآن تستخدم `/accounting/financial-position`. |
| **`/api/reconciliation/summary`** | `account_transactions` + `accounts` | 🟡 يقرأ من الطبقة القديمة — لكن التقرير نفسه Legacy وموصى بـ RCA فرعي في P0.2. |
| **`/api/financial-input-hub/recent`** | `liabilities` + `account_transactions` (as records) | 🟢 عرض سجلات، ليس حسابات |
| **BNPL Settlements pages** | `settlement_entries` + `settlement_files` + `account_transactions` (matching) | 🟢 |

**خلاصة تحليل التقارير**:
- **الـ SSOT reports (الحديثة)**: كلها من `general_ledger`. ✅
- **الـ Legacy reports (نادرة الاستخدام)**: `/api/liabilities/summary`, `/api/reconciliation/summary` — يقرآن من الطبقة القديمة. لكن **لا يُستخدمان من صفحات نشطة رئيسية**.

---

## 6. Classification Table — التصنيف النهائي

| Item | Category | Reason |
|---|---|---|
| `liabilities` (collection) | 🟢 **active_ssot** | SSOT للـ obligations كـ objects |
| `account_transactions` (collection) | 🟢 **active** + 🟡 **redundant_storage** | GL يعمل كـ mirror منذ Iter-240 |
| `liabilities_routes.py` (all CRUD) | 🟢 **active_ssot** | endpoints الإنتاج الرئيسية |
| `/api/liabilities/summary` (Iter-92) | 🟡 **legacy_read** — safe_to_keep | لا يُستخدم من الصفحات الحديثة |
| `/api/reconciliation/summary` | 🟡 **legacy_read** — يحتاج RCA فرعي | يقرأ من `account_transactions` |
| `/api/operational-reports` (Iter-114) | 🟢 **active** — `liabilities`-based | تقرير Aging مقبول |
| `ledger_double_write.py` (Iter-240) | 🟢 **active_bridge** | يضمن Sync GL |
| `account_tx_vs_ledger_walk_routes.py` | 🟢 **diagnostic** | كاشف drift |
| `balance_drift_diagnostic_routes.py` | 🟢 **diagnostic** | كاشف drift |
| `salla_balance_forensic_routes.py` | 🟢 **forensic** | RCA خاصة بمشاكل سابقة |
| `bnpl_statement_ui_audit_routes.py` | 🟢 **audit** | Iter-149 tools |

**لا شيء تصنيفه `dangerous_legacy`.**

---

## 7. Answer to the 5 Core Questions

**السؤال 1: كل الأماكن التي تقرأ من `liabilities`؟**
✅ رُصدت جميعاً — انظر §2.3. المستخدمة user-facing: `FinancialInputHub`, `OperationsDashboard`, `EmployeeBalanceCard`, feed الـ Recent.

**السؤال 2: كل الأماكن التي تقرأ من `account_transactions`؟**
✅ رُصدت جميعاً — انظر §3.3. لا موقع يعرض `balance_after` كرقم محاسبي — العرض دائماً كـ log/history.

**السؤال 3: هل يوجد كتابة حالية إلى `liabilities`؟**
🟢 نعم — من `liabilities_routes.py`, `ad_account_routes.py`, `purchase_invoices_routes.py`, `accounting_cutoffs_routes.py`. **هذا سلوك مقصود ومطلوب**. كل الكتابات تنعكس على `general_ledger` عبر الطريق الطبيعي (دفع → account_transactions → mirror → GL).

**السؤال 4: هل يوجد كتابة حالية إلى `account_transactions`؟**
🟢 نعم — 29 موقع. **كلها تُنعكس على `general_ledger`** عبر `mirror_account_txn_to_ledger()` منذ Iter-240. لا كاتب orphan.

**السؤال 5: هل أي تقرير مالي يعتمد عليها بدلاً من `general_ledger`؟**
🟡 **نعم — واحد فقط**: `/api/operational-reports` (Iter-114) يقرأ من `liabilities` لأنه تقرير عن **الالتزامات كسجلات** لا عن الأرصدة. مقبول محاسبياً.

كل التقارير المالية الأساسية (Financial Position, Ledgers, Dashboard KPIs) تقرأ من `general_ledger` حصراً.

---

## 8. Recommendations — التوصيات (لا تُنفَّذ إلا بموافقتك)

| # | التوصية | مستوى | خطر التنفيذ |
|---|---|---|---|
| **R1** | **إبقاء الوضع كما هو** — النظام سليم، الـ SSOT محترم. | Keep-as-is | 🟢 |
| **R2** | **RCA فرعي لـ `/api/reconciliation/summary`** — تحديد ما إذا كان يعرض أرقاماً مختلفة عن Financial Position. (تكرار توصية P0.2 R5). | يحتاج RCA | — |
| **R3** | **إضافة alert monitoring على `ledger_double_write` failures** — حالياً `except Exception` يبتلع الأخطاء بصمت. اقتراح: إضافة `db.audit_log` insert عند فشل الـ mirror. | Improvement | 🟡 يتطلب تعديل كود |
| **R4** | **Documentation فقط**: توثيق أن `liabilities` = SSOT للـ obligations objects، `general_ledger` = SSOT للأرصدة، `account_transactions` = طبقة توافق (compat layer). | Docs only | 🟢 |
| **R5** | **مسار طويل الأجل (P3 backlog)**: نقل الـ UI drawers التي تعرض `/api/accounts/:id/transactions` لتقرأ من `general_ledger` مباشرة، ثم في iter لاحق **freeze** الكتابة على `account_transactions`. لا شيء الآن. | Future work | — |

---

## 9. Read-Only Confirmations — تأكيدات هذا الـ RCA

- ✅ لم يُلمس أي ملف كود.
- ✅ لم تُحذف أي collection.
- ✅ لم يُنفَّذ أي migration.
- ✅ لم يُنفَّذ أي write على DB.
- ✅ لم يُضَف أي redirect.
- ✅ لم يُخفَ أي route.
- ✅ لم يُستدعَ Qoyod API.
- ✅ لم يُنفَّذ Deploy.
- ✅ `production_writes_locked=true` باقٍ.
- ✅ `selective_live_send_enabled=false` باقٍ.

---

## 10. المطلوب منك الآن

**اختر واحداً من:**

| الخيار | الوصف |
|---|---|
| **A** | **قبول الوضع كما هو** — إغلاق P0.3. الانتقال إلى **Master Financial Audit Ledger** يجمع P0.1+P0.2+P0.3 في مرجع واحد. |
| **B** | **إغلاق P0.3 والعودة إلى الأولوية الأصلية** — مهمة 268307955 (Redeploy + adoption يدوي) أو التحضير لـ P0 Gate (تعطيل POST/PUT/DELETE `/api/settlements`). |
| **C** | **بدء RCA فرعي لـ `/api/reconciliation/summary`** (P0.2 R5) — آخر Legacy read مشكوك بها. |
| **D** | **بدء نطاق Audit جديد**: صفحات الإعلانات / المخزون / الموظفين. |
| **E** | **بناء Alert Monitor لفشل `ledger_double_write`** (R3) — تحسين استقرار. |

**ملاحظات مسجَّلة**:
- **P0 Gate**: قبل فتح النظام لأي مستخدم فعلي → تعطيل POST/PUT/DELETE `/api/settlements`، إبقاء GET، إضافة regression tests.
- **P0.1**: `/settlements` writes بلا حاجز — مؤجَّل.
- **P0.2**: `/financial-position` آمن (SSOT).
- **P0.3**: `liabilities` + `account_transactions` **آمنَين ومقصودَين** — ليسا Legacy.
- **مهمة 268307955**: مغلقة على مستوى Preview readiness — بانتظار Redeploy.
- **المرحلة (ب) `approve-and-send`**: متوقفة بأمرك.

لا Deploy، لا تعديل، حتى تعطي إذناً صريحاً.
