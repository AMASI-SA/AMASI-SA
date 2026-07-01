# Financial System Audit — Read-Only, Iter-001

**Author:** E1 (Emergent Agent)  
**Scope:** Financial subsystem only — Qoyod, ZATCA, invoices, payments, COD,
bank_transfer, settlements, financial reports, and duplicated
pages/routes/collections related to the financial domain.  
**Mode:** Read-only. No code changed. No routes hidden. No collections
touched. No writes to Production. No Qoyod calls. No Deploys.  
**Date:** 2026-07-01  
**Report file:** `/app/AUDIT/FINANCIAL_SYSTEM_AUDIT_ITER_001.md`

---

## 0. TL;DR — الخلاصة التنفيذية

- 🔴 **7 صفحات تسويات** (Settlements ecosystem) بمصادر بيانات مختلفة —
  خطر عالي للأرقام المتضاربة. مصدر الحقيقة الأحدث هو
  **Settlement Engine (Iter-251 Phase 2A)** الذي يعرض عبر
  `SettlementDashboard.jsx`. البقية Legacy أو تخصصية.
- 🔴 **صفحة `FinancialPosition.jsx` (Iter-93) قديمة رسمياً** — استُبدلت بـ
  `FinancialPositionLedger.jsx` (Iter-161 Phase 4). لكن كلاهما لا يزال
  مُفعّلاً في `App.js`. الأولى مخفية من الـ Sidebar لكن الـ route
  `/financial-position` يعمل ويعطي أرقاماً مختلفة عن الـ SSOT.
- 🔴 **`Reconciliation.jsx` + `ReconciliationReport.jsx` + `ReconciliationDetail.jsx`**
  — الأخيرة تحتوي على تعليق حرفي "compared legacy vs current-empty-ledger
  which always looked terrible" ⚠️.
- 🟠 **`ShippingLedgerStub.jsx` مقابل `ShippingLedger.jsx`** — يستخدمان
  endpoints مختلفة (`/shipping-accounts/ledger` مقابل `/shipping-ledger`)
  ويرجعان طرق حساب مختلفة (per-courier مقابل per-order).
- 🟠 **16 صفحة قيود** — كلها لغرض واحد (المزامنة مع قيود) لكن بعضها
  scaffold مبكّر ("Pre-Day 3 Placeholders") ولم يُحدَّث.
- 🟢 **مجموعة "Ledger-only" (Iter-161 Phase 4)** هي **SSOT الحالي المالي**
  للأرصدة (`EmployeesLedger`, `SuppliersLedger`, `CouriersLedger`,
  `ExternalsLedger`, `FinancialPositionLedger`). كلها تقرأ من
  `general_ledger` عبر endpoints `/accounting/*/list`.

**عدد الروابط في `App.js`**: ~110 route  
**عدد الروابط في `Sidebar.jsx`**: ~80 route  
**الفرق**: ~30 route مخفية من الـ Sidebar — بعضها تفاصيل (`/:id`) وبعضها Legacy.

---

## 1. SSOT (Source of Truth) Map — خريطة مصادر الحقيقة المالية

| المفهوم المحاسبي | Collection SSOT | Endpoint SSOT | Legacy Alternatives |
|---|---|---|---|
| **الأرصدة (assets / liabilities)** | `general_ledger` | `/api/accounting/*/list`, `/api/accounting/financial-position` | `liabilities`, `account_transactions` (aggregation) |
| **المركز المالي** | `general_ledger` | `/api/accounting/financial-position` | `/api/liabilities/summary` + `/api/reconciliation/summary` |
| **الموردون** | `counterparties` + `general_ledger` | `/api/accounting/suppliers/list` | `suppliers` (قديمة) |
| **الموظفون** | `counterparties` + `general_ledger` | `/api/accounting/employees/list` | `employees` + `operating_salaries.balance` |
| **الشحن (بالطلب)** | `unified_orders` | `/api/shipping-ledger` | — |
| **الشحن (بالمندوب)** | `general_ledger` | `/api/shipping-accounts/ledger` | — |
| **فواتير المبيعات (قيود)** | `qoyod_invoices` | `/api/integrations/qoyod/invoices` | — |
| **سندات القبض (قيود)** | `qoyod_invoice_payments` | (داخل `qoyod_invoices`) | — |
| **طلبات معلقة (قيود)** | `integration_inbox` | `/api/integrations/qoyod/admin/qoyod/pending-orders` | — |
| **BNPL Settlements** | `settlement_entries` + `settlement_files` | `/api/bnpl/settlements/*` | `settlement_periods` (قد يكون مكرر) |
| **Bank Transfer Review** | `bank_transfer_reviews` | `/api/bank-transfer-review` | — |
| **Payment Settlements (Uploads)** | `settlement_files` | `/api/payment-settlements` | — |
| **Reconciliation (المستوى المحاسبي)** | `general_ledger` | `/api/accounting/migration/reconciliation` | `/api/reconciliation/*` (قديم/عام) |

**قاعدة عامة (استُنبطت من التعليقات في الكود):**
> بعد **Iter-161 Phase 4**، جميع الأرصدة يجب أن تُشتق حصراً من
> `general_ledger`. أي صفحة تقرأ من `liabilities` أو
> `account_transactions` مباشرة → **Legacy**.

---

## 2. الصفحات المالية — التصنيف التفصيلي

### 2.1 مجموعة قيود (Qoyod)

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر محاسبي | بديل أحدث | التوصية |
|---|---|---|---|---|---|---|---|---|
| 1 | `QoyodSettings.jsx` | `/integrations/qoyod/settings` | `/api/integrations/qoyod/settings` | ✅ SSOT | لا | 🟡 (إعدادات ZATCA + الحسابات) | — | **keep** |
| 2 | `QoyodInvoices.jsx` | `/integrations/qoyod/invoices` | `/api/integrations/qoyod/invoices` | ⚠️ SSOT + scaffolds | لا | 🟢 read-only | — | **keep** — كن حذراً: التعليق يقول "Pre-Day 3 Placeholders" — يحتاج تحقق أن جميع البلاطات (tiles) مفعّلة الآن. |
| 3 | `QoyodPendingOrders.jsx` | `/integrations/qoyod/pending-orders` | `/api/integrations/qoyod/admin/qoyod/pending-orders` | ✅ SSOT (Iter-293.5) | لا | 🟢 read-only حالياً | — | **keep** |
| 4 | `QoyodMigration.jsx` | `/integrations/qoyod/migration`, `/accounting/migration` | `/api/integrations/qoyod/migration/*` | ✅ SSOT | لا | 🟡 (writes: `.../confirm`) | — | **keep** — ملاحظة: مُسجَّل تحت route\_ين مختلفين في `App.js`، هذا مشكلة تصميمية لكنها ليست خطراً محاسبياً. |
| 5 | `QoyodCodReceiptsReport.jsx` | `/integrations/qoyod/cod-receipts-report` | `/api/integrations/qoyod/admin/cod-receipts-report` | ✅ SSOT (Iter-293) | لا | 🟢 read-only | — | **keep** |
| 6 | `QoyodRoundingReport.jsx` | `/integrations/qoyod/rounding-report` | `/api/integrations/qoyod/admin/rounding-report` | ✅ SSOT (Iter-290j) | جزئي مع #7 | 🟢 read-only | — | **keep** — للعمليات. |
| 7 | `QoyodRoundingDryRun.jsx` | `/integrations/qoyod/rounding-dry-run` | `/api/integrations/qoyod/admin/rounding-dry-run` | ✅ SSOT (Iter-290k) | جزئي مع #6 | 🟢 read-only | — | **keep** — لكن ملاحظة: #6 و#7 يعالجان جانبين مختلفين من نفس المشكلة. يمكن دمجهما كتبوبين لاحقاً. |
| 8 | `QoyodUnallocatedReceipts.jsx` | `/integrations/qoyod/unallocated-receipts` | `/api/integrations/qoyod/admin/unallocated-receipts` | ✅ SSOT (Iter-290h) | لا | 🟢 read-only | — | **keep** |
| 9 | `QoyodFreshStart.jsx` | `/integrations/qoyod/fresh-start` | `/api/integrations/qoyod/fresh-start/*` | ✅ SSOT | لا | 🔴 writes (cleanup) — يجب أن يكون مسموح فقط في Preview | — | **keep** — تحقق أن يكون معطّلاً في Production. |
| 10 | `QoyodGoLive.jsx` | `/integrations/qoyod/go-live` | `/api/integrations/qoyod/go-live/*` | ✅ SSOT | لا | 🔴 writes (ينقل الإعداد إلى Production Mode) | — | **keep** |
| 11 | `QoyodFirstSyncMonitor.jsx` | `/integrations/qoyod/first-sync-monitor` | `/api/integrations/qoyod/first-sync/*` | ✅ SSOT | لا | 🟢 read-only | — | **keep** |
| 12 | `QoyodWebhookMonitor.jsx` | `/integrations/qoyod/error-log` أو `/sync-log` | `/api/integrations/qoyod/webhook-events` | ✅ SSOT (Iter-294) | 🔴 يستبدل placeholder قديم | 🟢 read-only | ← يستبدل `/sync-log` سابقاً | **needs RCA** — تعليقه يقول "Replaces the previous placeholder at `/integrations/qoyod/sync-log`" لكن `/sync-log` لا يزال route فعّالاً في `App.js`. يجب التأكد إن كان `/sync-log` يشير الآن لنفس الصفحة أم صفحة أخرى قديمة. |

**ملاحظة على Qoyod**: كل الصفحات (12) لها غرض واضح ومختلف. **لا يوجد تكرار حقيقي داخل قيود** — التداخل الوحيد هو #6 مع #7 (تقرير الـ Rounding + Dry-Run simulator) وهذا مبرَّر.

---

### 2.2 مجموعة التسويات (Settlements) — 🔴 المنطقة الأكثر تعقيداً

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر محاسبي | التوصية |
|---|---|---|---|---|---|---|---|
| 13 | `Settlements.jsx` | `/settlements` (**مخفية من Sidebar!**) | `/api/settlements` + `/api/settlements/summary` + `POST /api/settlements` | ⚠️ غير معروف الدور | 🔴 مع #14 #15 #16 #17 #18 | 🔴 يكتب (POST) | **needs RCA** — لماذا مخفية لكن الـ route فعّال؟ من يستخدم POST؟ |
| 14 | `SettlementsOverview.jsx` | `/settlements-overview` | (Iter-158 aggregator) | 🟡 قد يكون Legacy | 🔴 | 🟢 read-only | **needs RCA** — نطاق Iter-158 قديم جداً. |
| 15 | `SettlementDashboard.jsx` | `/settlement-engine` | `/api/settlement-engine/dry-run` + `/api/settlement-engine/stats` | ✅ SSOT (Iter-251 Phase 2A) | لا (الأحدث) | 🟢 dry-run only | **keep as canonical** |
| 16 | `PaymentSettlements.jsx` | `/payment-settlements` | `/api/payment-settlements` + `/api/payment-settlements/_analytics/coverage` + `POST /api/payment-settlements/upload` | ⚠️ رفع ملفات — قد يكون SSOT لجزء منفصل | جزئي مع #17 | 🔴 يكتب (upload) | **keep** — منفصل عن #15 (upload flow). |
| 17 | `SallaSettlements.jsx` | `/salla-settlements` | `/api/payment-settlements/_analytics/salla` | 🟡 view خاص للسلة فقط | جزئي مع #16 | 🟢 read-only | **merge** مع #16 كتبويب "سلة" |
| 18 | `BnplSettlements.jsx` | `/bnpl-settlements` | `/api/bnpl/settlements/summary` | ✅ SSOT خاص بـ BNPL | لا | 🟢 read-only | **keep** |
| 19 | `BnplSettlementsRegister.jsx` | `/bnpl-settlements/register` | `/api/bnpl/settlements/registration-overview` + `/registered` | ✅ SSOT (Iter-221 Phase 2b) | لا | 🟢 read-only (register UI فقط) | **keep** |

**🔴 خطر رئيسي**: صفحات `#13 Settlements` و`#14 SettlementsOverview` مصدرها غير موثّق، ومختفية عن الـ Sidebar. أي رقم فيها قد يتضارب مع الـ Settlement Engine (#15). **تحتاج RCA فوراً**.

---

### 2.3 مجموعة المركز المالي والـ Reconciliation

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر محاسبي | التوصية |
|---|---|---|---|---|---|---|---|
| 20 | `FinancialPosition.jsx` | `/financial-position` (**مخفية من Sidebar**) | `/api/accounting/financial-position` + `/api/liabilities/summary` + `/api/reconciliation/summary` + `/api/liabilities?...` + `/api/shipping-accounts/ledger` | 🔴 **LEGACY** (Iter-93) | 🔴 مع #21 | 🔴 يعرض أرقاماً مختلفة عن الـ SSOT | **hide** (بعد التأكد أن `/financial-position-ledger` يغطي كل ما تعرضه) — الـ route فعّال لكنه لا يظهر في Sidebar، مما يعني قد يُشارك رابط قديم. |
| 21 | `FinancialPositionLedger.jsx` | `/financial-position-ledger` | `/api/accounting/financial-position` فقط | ✅ SSOT (Iter-161 Phase 4) | — | 🟢 read-only | **keep as canonical** — التعليق حرفياً: "Replaces legacy financial-position page". |
| 22 | `Reconciliation.jsx` | `/reconciliation` (**مخفية من Sidebar**) | `/api/reconciliation/summary` | 🟡 قد يكون Legacy | 🔴 مع #23 #24 | 🟡 | **needs RCA** — لماذا مخفية لكن `/reconciliation/:accountId` (وهي الـ detail) موجودة في `App.js`. الـ Sidebar يستخدم `/accounting/reconciliation` (route مختلف). |
| 23 | `ReconciliationDetail.jsx` | `/reconciliation/:accountId` | (detail) | جزء من #22 | مع #22 | 🟡 | **keep** إن أُبقيت #22. |
| 24 | `ReconciliationReport.jsx` | (لا يوجد route مباشر — يُستدعى من `/accounting/reconciliation`) | `/api/accounting/migration/reconciliation` | ✅ SSOT | — | 🟢 | **keep** — الأحدث والأصح. تعليق التطوير يعترف صراحة بأن الإصدار القديم "compared legacy vs current-empty-ledger which always looked terrible". |

**🔴 خطر رئيسي**: `/financial-position` مخفية لكن route فعّال، والأرقام التي تعرضها قد تختلف عن `/financial-position-ledger`. أي رابط قديم مشارَك يُعطي أرقاماً مختلفة عن SSOT.

---

### 2.4 مجموعة الـ Ledgers (Iter-161 Phase 4 — SSOT الحالي)

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر | التوصية |
|---|---|---|---|---|---|---|---|
| 25 | `EmployeesLedger.jsx` | `/employees-ledger` | `/api/accounting/employees/list` | ✅ SSOT | — | 🟢 | **keep** |
| 26 | `SuppliersLedger.jsx` | `/suppliers-ledger` | `/api/accounting/suppliers/list` | ✅ SSOT (Iter-250b) | — | 🟢 | **keep** |
| 27 | `CouriersLedger.jsx` | `/couriers-ledger` | `/api/accounting/couriers/list` | ✅ SSOT | — | 🟢 | **keep** |
| 28 | `ExternalsLedger.jsx` | `/externals-ledger` | `/api/accounting/externals/list` | ✅ SSOT | — | 🟢 | **keep** |
| 29 | `EntityLedgerByIdPage.jsx` | `/entity-ledger/:type/:id` | (تفصيلي، polymorphic) | ✅ SSOT | — | 🟢 | **keep** |
| 30 | `SupplierLedgerDetailPage.jsx` | `/suppliers/:id/ledger-detail` | (تفصيل مورد) | ✅ SSOT | مع #29 (polymorphic vs specific) | 🟢 | **needs RCA** — لماذا مسار تفصيلي منفصل للموردين؟ #29 يجب أن يغطي هذا. قد يكون منها بديل قديم أو مخصص. |
| 31 | `LedgerTransactionsPage.jsx` | (بدون route ظاهر — يُستدعى من drawer) | `/api/general-ledger/transactions` | ✅ SSOT | — | 🟢 | **keep** |
| 32 | `LedgerHealthDiagnostic.jsx` | `/audit/ledger-health` | `/api/audit/*` | ✅ SSOT | — | 🟢 diagnostic | **keep** |

---

### 2.5 مجموعة الشحن (Shipping)

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر | التوصية |
|---|---|---|---|---|---|---|---|
| 33 | `ShippingLedger.jsx` | `/shipping/ledger` (**مخفية من Sidebar**) | `/api/shipping-ledger` | ✅ SSOT (Iter-192-ext) — per-order | جزئي مع #34 (لكن غرضهما مختلف) | 🟢 read-only | **needs RCA** — لماذا مخفية من Sidebar؟ لها غرض تشغيلي مهم. |
| 34 | `ShippingLedgerStub.jsx` | `/shipping/orders-ledger` (**مخفية من Sidebar**) | `/api/shipping-accounts/ledger` | ✅ SSOT (Iter-144) — per-courier deferred-only | جزئي مع #33 | 🟢 read-only | **needs RCA** — اسم "Stub" مضلّل. |

**ملاحظة**: كلاهما SSOT لكن لأغراض مختلفة (per-order مقابل per-courier). التسمية `Stub` واختفاؤها من الـ Sidebar يوحي بأنها قديمة، بينما هي مفيدة فعلياً. **يحتاج توضيح**.

---

### 2.6 مجموعة الحركات المالية والمعاملات

| # | الصفحة | Route | مصدر البيانات | SSOT/Legacy | تكرار | خطر | التوصية |
|---|---|---|---|---|---|---|---|
| 35 | `FinancialMovementNewPage.jsx` | `/financial-movement/new` (**مخفية من Sidebar**) | `POST /api/financial-movements` | ⚠️ قد يكون Legacy | مع #36 | 🔴 يكتب | **needs RCA** — نموذج جديد لحركة يدوية. Sidebar لا يعرضه — قد يكون نموذجاً قديماً استُبدل بـ `/new-transaction`. |
| 36 | `FinancialMovementsListPage.jsx` | `/financial-movements` | `/api/financial-movements` | ⚠️ قد يكون Legacy | مع #35 | 🟢 read-only | **needs RCA** — Sidebar يعرضه، وهو مفيد. لكن هل هو SSOT أم `general_ledger` هو الأدق؟ |
| 37 | `FinancialInputHub.jsx` | `/financial-input-hub` (**مخفية من Sidebar**) | (hub / launcher) | ⚠️ launcher قديم؟ | — | 🟢 | **needs RCA** — قد يكون قديماً استُبدل بـ Sidebar directly. |
| 38 | `PurchaseInvoices.jsx` | `/purchase-invoices` | `POST /api/purchase-invoices` | ✅ SSOT (Iter-103) | — | 🔴 يكتب — يُنشئ `liabilities` | **keep** |
| 39 | `CODDiagnostic.jsx` | `/diagnostics/cod-source` | `/api/diagnostics/cod-source` | ✅ SSOT (Iter-176) | — | 🟢 read-only | **keep** |
| 40 | `BankTransferReview.jsx` | `/bank-transfer-review` | `/api/bank-transfer-review` | ✅ SSOT (Iter-251 Phase 1) | — | 🔴 يكتب (confirm/reject) | **keep** |

---

### 2.7 صفحات مخفية إضافية (ذات صلة مالية)

هذه الـ routes موجودة في `App.js` لكنها **غير موجودة في Sidebar** — يجب مراجعتها:

| # | Route | ملاحظة | التوصية |
|---|---|---|---|
| — | `/transactions` | يُشير إلى `LedgerTransactionsPage` (drawer usually) | **verify** — قد يكون Legacy standalone. |
| — | `/transfers` | صفحة تحويلات قديمة (`Iter-144` استُبدل بـ `bank_transfer_reviews`) | **needs RCA** |
| — | `/upload` | صفحة رفع عامة | **needs RCA** — قد تكون قديمة. |
| — | `/receivables` | مستحقات | **verify** |
| — | `/advances` | سلف | **verify** |
| — | `/reconciliation/:accountId` | detail لصفحة #22 المخفية | **verify** — يعتمد على مصير #22. |
| — | `/counterparties` | تسجيل أطراف (SSOT الحديث للأطراف) | **verify** — قد تكون مفيدة لكن يجب توثيق دورها. |
| — | `/reports/ads` | تقرير إعلانات — قد يكون قديماً | **verify** — Sidebar فيه `/reports/advertising-expenses` بدلاً منه. |

---

## 3. جداول MongoDB — تصنيف Collections المالية

**~130 collection في النظام**. المالية منها:

### 3.1 SSOT Collections (المصادر الرئيسية — لا تُلمس)

| Collection | الغرض | الاستخدام | SSOT |
|---|---|---|---|
| `general_ledger` | كل حركة محاسبية (double-entry) | 223 مرجع في الكود | ✅ **SSOT للأرصدة** (Iter-161) |
| `unified_orders` | كل طلب سلة | 121 مرجع | ✅ SSOT للطلبات |
| `qoyod_invoices` | فواتير قيود المُرسَلة | 30 مرجع | ✅ SSOT لفواتير قيود |
| `qoyod_invoice_payments` | سندات القبض في قيود | 4 مراجع | ✅ SSOT لسندات القبض |
| `integration_inbox` | كل طلب في pipeline قيود | 81 مرجع | ✅ SSOT لحالة الـ pipeline |
| `settlement_entries` | تسويات BNPL | 30 مرجع | ✅ SSOT للـ BNPL |
| `settlement_files` | ملفات التسوية المرفوعة | 20 مرجع | ✅ SSOT للـ uploads |
| `counterparties` | الأطراف (موردين، موظفين، ...) | 114 مرجع | ✅ SSOT للأطراف |
| `bank_transfer_reviews` | مراجعة التحويلات البنكية | 19 مرجع | ✅ SSOT |
| `payment_transactions` | معاملات الدفع الخام من سلة | 85 مرجع | ✅ SSOT للدفع الخام |
| `payment_refunds` | استرجاعات | 36 مرجع | ✅ SSOT |
| `qoyod_settings` | إعدادات المزامنة مع قيود | 20 مرجع | ✅ SSOT |
| `qoyod_products_mapping` | mapping SKU → product_id | 15 مرجع | ✅ SSOT |
| `qoyod_customers_mapping` | mapping phone/email → contact_id | 11 مرجع | ✅ SSOT |
| `qoyod_webhook_events` | أحداث webhook | 10 | ✅ SSOT |
| `qoyod_webhook_tokens` | tokens الـ webhook | 6 | ✅ SSOT |
| `qoyod_credentials` | API keys قيود | 6 | ✅ SSOT |
| `qoyod_per_order_approvals` | موافقات فردية | 4 | ✅ SSOT |
| `qoyod_unallocated_dismissals` | dismissals للـ report | 4 | ✅ SSOT |
| `qoyod_rounding_warning_audits` | audits الـ rounding | 1 | ✅ SSOT |
| `qoyod_fresh_start_audits` + `qoyod_fresh_start_cleanups` | تدقيق البداية | 3+5 | ✅ SSOT |
| `qoyod_migration_*` (3 جداول) | Migration data | 4 | ✅ SSOT |
| `qoyod_external_customers` + `qoyod_external_products` | Fresh Start snapshot | 4 | ✅ SSOT |

### 3.2 ⚠️ Suspect — Legacy / Duplicated (تحتاج مراجعة)

| Collection | الغرض المُفترض | Suspect Reason | التوصية |
|---|---|---|---|
| `liabilities` | التزامات (قديماً كانت SSOT للأرصدة) | ⚠️ استُبدلت بـ `general_ledger` في Iter-161 Phase 4. لا تزال مستخدمة في 110 مكان! | **needs RCA** — تحديد ما إذا كانت `liabilities` مصدر مساعد أم Legacy فعلاً. صفحة `FinancialPosition.jsx` القديمة تعتمد عليها. |
| `account_transactions` | حركات الحسابات (قديماً كانت SSOT) | ⚠️ استُبدلت بـ `general_ledger`. لا تزال في 86 مكان. | **needs RCA** — نفس السبب. |
| `settlement_periods` | فترات تسوية | 6 مراجع فقط، منخفض. غامض مقارنة بـ `settlement_entries` (30 مرجع). | **needs RCA** — قد يكون شبه مهجور. |
| `settlement_invoices` | فواتير تسوية | 9 مراجع. مقارنة بـ `qoyod_invoices` (30) قد يكون مكرراً محلياً. | **needs RCA** |
| `settlement_alerts` | تنبيهات تسوية | 14 مرجع. متداخلة مع `alert_settings`? | **needs RCA** |
| `operating_salaries` | رواتب — كان يُقرأ منه `.balance` قديماً | استُبدل بـ `general_ledger` (Iter-161) لكن لا يزال يُستخدم في 49 مرجع للـ upsert. | **verify** — قد يكون مصدر إدخال (input) وليس مصدر رصيد. |
| `liability_payments` | مدفوعات الالتزامات | 2 مراجع فقط. قد يكون شبه مهجور. | **needs RCA** |
| `expected_transfers` | تحويلات متوقّعة | 5 مراجع. قد يكون مكرراً مع `bank_transfer_reviews`. | **needs RCA** |
| `courier_transfers` | تحويلات المندوبين | 5 مراجع. قد يكون مكرراً مع `payment_transactions`. | **needs RCA** |
| `expense_accounts` | حسابات مصاريف | 2 مراجع فقط. مقابل `expense_categories` (15) و `expense_category_tree` (42). | **needs RCA** |
| `webhook_orders` + `webhook_parse_failures` | إدارة webhooks | 4+3 مراجع فقط. مقابل `qoyod_webhook_events` و `integration_inbox`. | **needs RCA** — قد يكون قديماً قبل توحيد الـ inbox. |
| `unclassified_payment_methods` | طرق دفع غير مصنّفة | 3 مراجع. قد يُعالجها الآن `qoyod_settings.payment_methods_map`. | **needs RCA** |

### 3.3 🟢 Support Collections (خاصة بمكونات مساعدة)

`accounts` (115), `orders` (3+ deprecated by unified_orders), `order_items` (3), `order_adjustments` (2), `order_status_policy` (4), `payment_adjustments` (18), `accounting_cutoffs` (5), `accounting_audit_log` (8), `daily_costs` (10), `expenses_*`, `financial_movements` (21), `import_jobs` (9), `product_costs` (24), etc.

---

## 4. Backend Routes — التصنيف

**~76 route module في `server.py`**. المالية منها:

### 4.1 SSOT Routes (المصادر الرئيسية)

| Route Module | Endpoint Pattern | SSOT |
|---|---|---|
| `integrations/qoyod/routes.py` | `/api/integrations/qoyod/*` | ✅ Qoyod الأساسي |
| `integrations/qoyod/migration_routes.py` | `/api/integrations/qoyod/migration/*` | ✅ |
| `ledger_routes.py` | `/api/general-ledger/*` | ✅ |
| `accounts_routes.py` | `/api/accounts/*` + `/api/accounting/*/list` | ✅ |
| `settlement_engine_routes.py` | `/api/settlement-engine/*` | ✅ (Iter-251) |
| `bnpl_settlement_*_routes.py` (3 ملفات) | `/api/bnpl/settlements/*` | ✅ |
| `bank_transfer_review_routes.py` | `/api/bank-transfer-review/*` | ✅ |
| `purchase_invoices_routes.py` | `/api/purchase-invoices` | ✅ |
| `financial_movements_routes.py` | `/api/financial-movements/*` | ⚠️ قد يكون قديماً — راجع #35/36 |
| `expenses_routes.py` + `expense_categories_routes.py` | `/api/expenses*` | ✅ |
| `shipping_ledger_routes.py` | `/api/shipping-ledger` + `/api/shipping-accounts/ledger` | ✅ |
| `settlements_import/routes.py` | `/api/payment-settlements/*` | ✅ |
| `reconciliation_routes.py` | `/api/reconciliation/*` | ⚠️ — راجع #22 |

### 4.2 ⚠️ Forensic / Diagnostic Routes (كثير جداً — قد يحتاج تنظيم)

**تقريباً ~25 route module** فقط للتشخيص المالي:
- `account_balance_diagnostic_iter246i.py`
- `account_tx_vs_ledger_walk_routes.py`
- `accounts_balance_diagnostic_routes.py`
- `ad_account_actual_debt_routes.py`
- `ad_account_dryrun_diff_routes.py`
- `ad_account_forensic_routes.py`
- `ad_account_recompute_dryrun_routes.py`
- `ad_account_root_cause_routes.py`
- `ad_debt_diagnostic_routes.py`
- `ad_spend_rca_routes.py`
- `balance_drift_diagnostic_routes.py`
- `bank_balance_subaccount_diagnostic_routes.py`
- `bank_current_balance_source_routes.py`
- `bnpl_settlement_banktx_routes.py`, `bnpl_settlement_health_routes.py`, `bnpl_settlement_trace_routes.py`
- `bnpl_statement_ui_audit_routes.py`
- `bnpl_timezone_health_routes.py`
- `cod_diagnostic_routes.py`
- `employee_ledger_forensic_routes.py`
- `employee_lookup_diagnostic_routes.py`
- `employee_orphan_diagnostic_routes.py`
- `movements_gl_drift_routes.py`
- `reconciliation_forensic_routes.py`
- `salla_balance_forensic_routes.py`
- `settlement_file_forensic_routes.py`
- `suppliers_unification_forensic_routes.py`
- `tamara_forensic_routes.py`, `tamara_receivable_diagnostic_routes.py`, `tamara_refund_audit_routes.py`, `tamara_ssot_diagnostic_routes.py`
- `reversal_impact_audit_routes.py`
- `financial_pages_inventory_routes.py`
- `legacy_usage_report_routes.py`
- `iter250a_verification_routes.py`

**🟡 التوصية**: مجموعة كبيرة من الـ forensic routes نُشئت لحل مشاكل محاسبية سابقة. **معظمها لا يزال مُحمَّلاً في `server.py`**. يجب:
1. **تحديد أيها Read-Only فعلاً** (وهذه لا خطر منها).
2. **تحديد أيها يستدعي write** (backfill/repair/migration).
3. الأخيرة يجب أن تكون معطّلة في Production أو محمية بحاجز.

---

## 5. Risk Register — سجل المخاطر المرتّبة

| # | الخطر | مستوى | الموقع | التوصية الفورية |
|---|---|---|---|---|
| R1 | صفحة `/financial-position` القديمة لا تزال route فعّالاً وتُعطي أرقاماً مختلفة عن `/financial-position-ledger` | 🔴 P0 | Frontend | **hide** (redirect إلى `/financial-position-ledger`) بعد التأكد أن الأخيرة تغطي جميع الحقول. |
| R2 | `/settlements` (Settlements.jsx) مخفية لكن الـ `POST` route فعّال — احتمال كتابة عرضية | 🔴 P0 | Frontend + Backend | **needs RCA** — تحديد إن كان يكتب على `settlement_entries` بشكل يتضارب مع Settlement Engine. |
| R3 | `liabilities` + `account_transactions` collections لا تزال مقروءة في 110+86 مكان بعد Iter-161 | 🔴 P0 | DB | **needs RCA** — قد تكون مصادر مساعدة أو Legacy فعلاً. |
| R4 | `ShippingLedger` و `ShippingLedgerStub` مخفيتان لكن مفيدتان محاسبياً | 🟠 P1 | Frontend | إظهارهما في Sidebar. |
| R5 | `SettlementsOverview` (Iter-158) قديم جداً | 🟠 P1 | Frontend | **needs RCA** — قارن ما يعرضه مع `SettlementDashboard` (Iter-251). |
| R6 | `Reconciliation.jsx` مخفية والـ Sidebar يشير إلى `/accounting/reconciliation` — إن كان `reconciliation_routes.py` يعتمد على مصدر قديم فسنعرض أرقاماً غير SSOT | 🟠 P1 | Both | **needs RCA** — قارن `/api/reconciliation/summary` مع `/api/accounting/migration/reconciliation`. |
| R7 | `QoyodInvoices.jsx` تعليقها يقول "Pre-Day 3 Placeholders" | 🟡 P2 | Frontend | تحقق أن جميع البلاطات مفعّلة. |
| R8 | `settlement_periods` و `settlement_invoices` و `settlement_alerts` قد تكون مكررة | 🟡 P2 | DB | **needs RCA** لكل واحد. |
| R9 | `/sync-log` قديم لكن الـ route لا يزال في App.js — `QoyodWebhookMonitor` يستبدله | 🟡 P2 | Frontend | **redirect** `/sync-log` → `/error-log`. |
| R10 | ~25 forensic route module في Backend — تحمّل ذاكرة/تعقيد | 🟡 P2 | Backend | **archive** ما ثبت أنه لأصلاح ماضٍ منتهي. |

---

## 6. Cleanup Plan — الخطة المقترحة (لا تُنفّذ إلا بموافقة صريحة)

### 🔴 P0 — أشياء خطيرة (تنفّذ أولاً بموافقتك):

| # | الإجراء | الأثر | يحتاج |
|---|---|---|---|
| P0.1 | **RCA لـ `/settlements`**: من يستدعي `POST /api/settlements`؟ ماذا يكتب على أي collection؟ | يحدد إن كنا نكتب أرقاماً متضاربة | فحص السجلات + قراءة الكود |
| P0.2 | **إخفاء `/financial-position`** بعد التأكد أن `/financial-position-ledger` تغطي كل ما تعرضه | يُبعد رابطاً يُظهر أرقاماً مختلفة عن SSOT | مقارنة الحقول |
| P0.3 | **RCA لـ `liabilities` + `account_transactions`**: هل هي reads متبقية Legacy أم مصادر إدخال؟ | يوضّح إن كنّا نقرأ من غير SSOT | grep عميق |

### 🟠 P1 — أشياء مهمة (بعد إغلاق P0):

| # | الإجراء |
|---|---|
| P1.1 | **RCA لـ `SettlementsOverview` (Iter-158)** — هل يعرض نفس أرقام `SettlementDashboard`؟ إن نعم، ندمج. |
| P1.2 | **RCA لـ `Reconciliation.jsx` + `ReconciliationReport.jsx`** — أي واحد SSOT؟ |
| P1.3 | **إظهار `ShippingLedger` + `ShippingLedgerStub` في الـ Sidebar** أو دمجهما في تبويبات. |
| P1.4 | **RCA لـ `SupplierLedgerDetailPage.jsx`** — لماذا تفصيل خاص بالموردين فقط؟ |
| P1.5 | **دمج `SallaSettlements` كتبويب داخل `PaymentSettlements`**. |
| P1.6 | **RCA لـ `FinancialMovementNewPage` مقابل `/new-transaction`** — أي واحد الحديث؟ |

### 🟡 P2 — تحسينات:

| # | الإجراء |
|---|---|
| P2.1 | تحقق من `QoyodInvoices` (Placeholders) — استكمال المفقود. |
| P2.2 | Redirect `/sync-log` إلى `/error-log`. |
| P2.3 | Archive الـ forensic routes التي انتهى دورها. |
| P2.4 | RCA لكل من: `settlement_periods`, `settlement_invoices`, `settlement_alerts`, `expected_transfers`, `courier_transfers`, `webhook_orders`. |
| P2.5 | توثيق Public API للمقاول (`/api/accounting/*` كنموذج SSOT). |

---

## 7. Migrations / Data Fixes — ما لا يُلمس الآن

**لا** توصيات بحذف أي collection. **لا** توصيات بـ backfill.  
كل قرار حذف/backfill يجب أن يُسبَق بـ:
1. تقرير RCA خاص بذاك المكون.
2. مقارنة أرقام قبل/بعد (dry-run).
3. موافقة صريحة منك.

---

## 8. Read-Only Confirmations — تأكيدات المرحلة 1

- ✅ لم يُلمس أي ملف كود.
- ✅ لم يُخفَ أي route.
- ✅ لم تُحذف أي collection.
- ✅ لم يُشغَّل أي migration.
- ✅ لم يُنفَّذ أي write على Production.
- ✅ لم يُستدعَ Qoyod API.
- ✅ لم يُنفَّذ Deploy.
- ✅ `production_writes_locked = true` باقٍ.
- ✅ `selective_live_send_enabled = false` باقٍ.

---

## 9. المطلوب منك الآن

**اختر واحداً من:**

| الخيار | الوصف |
|---|---|
| **A** | ابدأ RCA للـ P0.1 (`/settlements` write path) فقط، ثم عُد بتقرير Iter-002. |
| **B** | ابدأ RCA لـ P0.2 (`/financial-position` مقارنة الحقول) فقط. |
| **C** | ابدأ RCA لـ P0.3 (`liabilities` + `account_transactions` reads) فقط. |
| **D** | ابدأ الـ 3 (P0.1 + P0.2 + P0.3) في تقرير واحد Iter-002. |
| **E** | لا تبدأ RCA الآن — انتظر مراجعتي لهذا التقرير. |
| **F** | امتد إلى نطاق آخر (مثلاً الإعلانات، أو المخزون). |

**التذكير**: لا كتابة، لا إخفاء، لا تعديل حتى تعطي إذناً صريحاً لكل خطوة.
