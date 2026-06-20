# جرد الصفحات المالية — Iter-250a

> **Read-only**. تم توليد هذا الملف من `/app/backend/financial_pages_inventory_data.py`. لا تُعدِّل يدوياً.


## الملخّص التنفيذي

- **total_pages**: `64`
- **keep_count**: `46` 🟢
- **merge_count**: `6` 🟡
- **deprecate_count**: `9` 🟠
- **delete_count**: `3` 🔴
- **legacy_pages_affecting_balance**: `10`

### Hide Safety (Iter-250a)

- ✅ **KEEP_VISIBLE**: `46`
- 🔍 **NEEDS_REVIEW**: `7`
- 🚫 **SAFE_TO_HIDE**: `8`
- ↪️ **NEEDS_REDIRECT**: `3`

## 🚫 Routes للإخفاء الآن (SAFE_TO_HIDE)

| Route القديم | البديل | السبب |
|---|---|---|
| `/financial-position` | `/financial-position-ledger` | تقرير المركز المالي القديم. تم استبداله بالنسخة المبنية على ledger مباشرة. |
| `/transfers` | `/new-transaction (type=internal_transfer)` | تكتب في account_transactions + general_ledger. /new-transaction يغطي نفس الحالة بدون ازدواج كتابة. |
| `/financial-input-hub` | `/new-transaction + /financial-movements` | صفحة قديمة تجمع liabilities + account_transactions. العرض والإدخال الموحّد البديل أفضل. |
| `/counterparties` | `/suppliers-new + /suppliers-ledger` | يكتب في liabilities + account_transactions ويعدّل current_balance. النموذج الحديث (suppliers-new) يستخدم financial_movements. |
| `/advances` | `/new-transaction (type=salary_advance)` | يكتب في liabilities + account_transactions. ندمج مع /new-transaction. |
| `/shipping-accounts` | `/shipping/orders-ledger + /couriers-ledger` | يكتب في account_transactions + ledger. تم تجاوزه بالنموذج المبني على ledger مباشرة. |
| `/settlements` | `/settlements-overview أو /salla-settlements` | صفحة قديمة لتسويات سلة. |
| `/reconciliation` | `/accounting/reconciliation` | النسخة القديمة من المطابقة (يقرأ AT + current_balance). تم استبدالها بـ forensic ledger. |

## ↪️ Routes تحتاج Redirect (NEEDS_REDIRECT)

| Route | البديل |
|---|---|
| `/financial-movement/new` | `/new-transaction (UnifiedEntryScreen)` |
| `/shipping/ledger` | `/shipping/orders-ledger` |
| `/shipping/cod-settlements` | `/shipping/orders-ledger` |

## 🔍 Routes تحتاج مراجعة قبل أي إخفاء (NEEDS_REVIEW)

| Route | التصنيف | البديل المقترح | السبب |
|---|---|---|---|
| `/accounts/:id` | MERGE | `دمج _ledger_based_tx_feed + legacy walker إلى مصدر موحّد بعد Reset` | ينقسم بين فرعين (is_migrated → ledger، غير ذلك → account_transactions). أصل مشكلة Iter-249. لا يجب إبقاء فرعين. |
| `/ad-accounts` | MERGE | `توحيد عبر /new-transaction + ad_account_ledger sub` | يكتب في general_ledger (sub_account=balance) + account_transactions + يحدّث current_balance لـ counterparty. ثلاثة مسارات متوازية — مصدر تضارب حقيقي مع نموذج Iter-249. |
| `/purchase-invoices` | MERGE | `/suppliers-new (نموذج فاتورة موحّد)` | يكتب في liabilities + account_transactions. النموذج الجديد يجب أن يتولّى دورة الفاتورة كاملة. |
| `/shipping/transfers` | MERGE | `/new-transaction (type=courier_transfer)` | ندمج تحويلات الشحن مع شاشة الإدخال الموحّدة. |
| `/receivables` | MERGE | `/new-transaction (type=receivable_collect) + /externals-ledger` | يستخدم liabilities + account_transactions. الكيان الخارجي يجب أن يُدار من /externals-ledger. |
| `/payment-settlements` | MERGE | `/settlements-overview` | نظرة عامة عن تسويات منصات الدفع. |
| `/accounting/migration` | DEPRECATE | `—` | أداة هجرة استُخدمت مرة واحدة. خطر إعادة تشغيلها بالخطأ. يجب إخفاؤها من القائمة بعد Iter-250. |

## 🔴 أعلى المخاطر (highest_risk_duplicates)

| Route | التصنيف | البديل | السبب |
|---|---|---|---|
| `/accounts/:id` | MERGE | `دمج _ledger_based_tx_feed + legacy walker إلى مصدر موحّد بعد Reset` | ينقسم بين فرعين (is_migrated → ledger، غير ذلك → account_transactions). أصل مشكلة Iter-249. لا يجب إبقاء فرعين. |
| `/ad-accounts` | MERGE | `توحيد عبر /new-transaction + ad_account_ledger sub` | يكتب في general_ledger (sub_account=balance) + account_transactions + يحدّث current_balance لـ counterparty. ثلاثة مسارات متوازية — مصدر تضارب حقيقي مع نموذج Iter-249. |
| `/accounting/migration` | DEPRECATE | `—` | أداة هجرة استُخدمت مرة واحدة. خطر إعادة تشغيلها بالخطأ. يجب إخفاؤها من القائمة بعد Iter-250. |

## 🛠️ أعلى أولوية للتنظيف القادم (recommended_next_cleanup_batch)

| Route | التصنيف | المخاطر | البديل | السبب |
|---|---|---|---|---|
| `/accounts/:id` | MERGE | 🔴 HIGH | `دمج _ledger_based_tx_feed + legacy walker إلى مصدر موحّد بعد Reset` | ينقسم بين فرعين (is_migrated → ledger، غير ذلك → account_transactions). أصل مشكلة Iter-249. لا يجب إبقاء فرعين. |
| `/ad-accounts` | MERGE | 🔴 HIGH | `توحيد عبر /new-transaction + ad_account_ledger sub` | يكتب في general_ledger (sub_account=balance) + account_transactions + يحدّث current_balance لـ counterparty. ثلاثة مسارات متوازية — مصدر تضارب حقيقي مع نموذج Iter-249. |
| `/accounting/migration` | DEPRECATE | 🔴 HIGH | `—` | أداة هجرة استُخدمت مرة واحدة. خطر إعادة تشغيلها بالخطأ. يجب إخفاؤها من القائمة بعد Iter-250. |
| `/transfers` | DEPRECATE | 🟡 MEDIUM | `/new-transaction (type=internal_transfer)` | تكتب في account_transactions + general_ledger. /new-transaction يغطي نفس الحالة بدون ازدواج كتابة. |
| `/counterparties` | DEPRECATE | 🟡 MEDIUM | `/suppliers-new + /suppliers-ledger` | يكتب في liabilities + account_transactions ويعدّل current_balance. النموذج الحديث (suppliers-new) يستخدم financial_movements. |
| `/purchase-invoices` | MERGE | 🟡 MEDIUM | `/suppliers-new (نموذج فاتورة موحّد)` | يكتب في liabilities + account_transactions. النموذج الجديد يجب أن يتولّى دورة الفاتورة كاملة. |

---

## تفاصيل كاملة بحسب القسم


### البنوك والحسابات (`banks_and_accounts`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/accounts` | `mixed` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | نعم | `—` | نقطة الدخول الرئيسية لإدارة الحسابات. ترتد على account_balance_ssot() لذا متوافقة مع SSOT. |
| `/accounts/:id` | `mixed` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🔴 HIGH | نعم | `دمج _ledger_based_tx_feed + legacy walker إلى مصدر موحّد بعد Reset` | ينقسم بين فرعين (is_migrated → ledger، غير ذلك → account_transactions). أصل مشكلة Iter-249. لا يجب إبقاء فرعين. |
| `/financial-position` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟡 MEDIUM | لا | `/financial-position-ledger` | تقرير المركز المالي القديم. تم استبداله بالنسخة المبنية على ledger مباشرة. |
| `/financial-position-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | البديل الـ SSOT لتقرير المركز المالي. |

### التحويلات والإدخالات (`transfers_and_entries`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/new-transaction` | `financial_movements` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | نعم | `—` | نقطة إدخال الحركة الموحّدة (SSOT). يجب توجيه كل الكتابات الجديدة هنا. |
| `/financial-movements` | `financial_movements` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | العرض القانوني لجدول financial_movements. |
| `/financial-movement/new` | `config_only` | LEGACY | 🔴 DELETE | ↪️ NEEDS_REDIRECT | 🟢 LOW | لا | `/new-transaction (UnifiedEntryScreen)` | الصفحة مُدمَجة فعلياً منذ Iter-246 — 40 سطر فقط، بانر redirect نقي بدون أي منطق إدخال أو API. آمن لاستبدالها بـ LegacyRedirect الموحّد. |
| `/transfers` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟡 MEDIUM | نعم | `/new-transaction (type=internal_transfer)` | تكتب في account_transactions + general_ledger. /new-transaction يغطي نفس الحالة بدون ازدواج كتابة. |
| `/financial-input-hub` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟢 LOW | لا | `/new-transaction + /financial-movements` | صفحة قديمة تجمع liabilities + account_transactions. العرض والإدخال الموحّد البديل أفضل. |
| `/transactions` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | العرض الموحّد لكل قيود الـ ledger. |

### BNPL (Tamara / Tabby) (`bnpl`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/bnpl-settlements` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | نعم | `—` | نقطة تسجيل التسوية الكنونية. تكتب الـ ledger مباشرة عبر settlement_bridge. |
| `/bnpl-settlements/register` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | نعم | `—` | صفحة تسجيل تسوية واحدة (form). |
| `/integrations/bnpl` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | إعدادات وربط Tamara/Tabby. |
| `/integrations/bnpl/diagnostics` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيصات قراءة فقط. |
| `/bnpl-balances` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص رصيد BNPL. |
| `/refund-audit` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تدقيق المرتجعات (قراءة فقط). |

### الحسابات الإعلانية (`ad_accounts`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/ad-accounts` | `mixed` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🔴 HIGH | نعم | `توحيد عبر /new-transaction + ad_account_ledger sub` | يكتب في general_ledger (sub_account=balance) + account_transactions + يحدّث current_balance لـ counterparty. ثلاثة مسارات متوازية — مصدر تضارب حقيقي مع نموذج Iter-249. |
| `/snapchat-accounts` | `external` | EXTERNAL | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | مزامنة قراءة من Snapchat API. لا تكتب في الـ ledger. |
| `/audit/ad-debt` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص ديون الإعلانات. |
| `/settings/ads-currencies` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تكوين أسعار الصرف للحسابات الإعلانية. |

### الموردين (`suppliers`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/counterparties` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟡 MEDIUM | نعم | `/suppliers-new + /suppliers-ledger` | يكتب في liabilities + account_transactions ويعدّل current_balance. النموذج الحديث (suppliers-new) يستخدم financial_movements. |
| `/suppliers-new` | `financial_movements` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | نعم | `—` | النموذج الحديث للموردين. يكتب عبر financial_movements (SSOT). |
| `/suppliers-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | كشف حساب الموردين من الـ ledger مباشرة. |
| `/purchase-invoices` | `mixed` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🟡 MEDIUM | نعم | `/suppliers-new (نموذج فاتورة موحّد)` | يكتب في liabilities + account_transactions. النموذج الجديد يجب أن يتولّى دورة الفاتورة كاملة. |
| `/reports/suppliers` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تقرير قراءة فقط من ledger. |

### الموظفين والرواتب (`employees_and_salaries`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/employees-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | كشف الموظف من ledger. |
| `/employees/custody-balances` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | عُهد الموظفين من ledger. |
| `/advances` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟡 MEDIUM | نعم | `/new-transaction (type=salary_advance)` | يكتب في liabilities + account_transactions. ندمج مع /new-transaction. |
| `/operating-expenses` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | إدارة الموظفين الأساسية (بيانات مرجعية فقط، الراتب اليومي يحتسب لاحقاً). |
| `/employee-corrections` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟡 MEDIUM | نعم | `—` | تصحيح قيود الموظفين عبر ledger correction (Iter-196). أداة إدارية. |
| `/salary-reversals` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟡 MEDIUM | نعم | `—` | عكس قيود رواتب (admin). |
| `/audit/employee-orphans` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص قيود الموظفين اليتيمة. |

### شركات الشحن (`shipping`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/shipping-accounts` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟡 MEDIUM | نعم | `/shipping/orders-ledger + /couriers-ledger` | يكتب في account_transactions + ledger. تم تجاوزه بالنموذج المبني على ledger مباشرة. |
| `/shipping/orders-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | كشف طلبات الشحن من ledger. |
| `/couriers-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | كشف شركات الشحن من ledger. |
| `/shipping/ledger` | `general_ledger` | LEGACY | 🔴 DELETE | ↪️ NEEDS_REDIRECT | 🟢 LOW | لا | `/shipping/orders-ledger` | Stub فارغ يعيد توجيه للنسخة الـ ledger. |
| `/shipping/transfers` | `mixed` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🟡 MEDIUM | نعم | `/new-transaction (type=courier_transfer)` | ندمج تحويلات الشحن مع شاشة الإدخال الموحّدة. |
| `/shipping/cod-settlements` | `general_ledger` | LEGACY | 🔴 DELETE | ↪️ NEEDS_REDIRECT | 🟢 LOW | لا | `/shipping/orders-ledger` | نفس الـ stub مكرّر تحت مسار آخر. |
| `/shipping/settings` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تكوين شركات الشحن. |
| `/diagnostics/cod-source` | `mixed` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص COD (قراءة فقط). |

### الذمم والعملاء (`receivables`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/receivables` | `mixed` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🟡 MEDIUM | نعم | `/new-transaction (type=receivable_collect) + /externals-ledger` | يستخدم liabilities + account_transactions. الكيان الخارجي يجب أن يُدار من /externals-ledger. |
| `/externals-ledger` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | كشف الأطراف الخارجية من ledger. |

### التسويات (`settlements`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/settlements` | `account_transactions` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟢 LOW | لا | `/settlements-overview أو /salla-settlements` | صفحة قديمة لتسويات سلة. |
| `/payment-settlements` | `account_transactions` | LEGACY | 🟡 MERGE | 🔍 NEEDS_REVIEW | 🟢 LOW | لا | `/settlements-overview` | نظرة عامة عن تسويات منصات الدفع. |
| `/salla-settlements` | `external` | EXTERNAL | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | مزامنة تسويات سلة من API الخارجي. |
| `/settlements-overview` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | نظرة عامة شاملة (ledger-based). |
| `/reconciliation` | `mixed` | LEGACY | 🟠 DEPRECATE | 🚫 SAFE_TO_HIDE | 🟢 LOW | لا | `/accounting/reconciliation` | النسخة القديمة من المطابقة (يقرأ AT + current_balance). تم استبدالها بـ forensic ledger. |
| `/accounting/reconciliation` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | المطابقة القانونية (ledger-only). |

### المصروفات (`expenses`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/daily-costs` | `external` | EXTERNAL | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تكاليف يومية لتحليل P&L. كيان منفصل خارج الـ ledger. |
| `/expense-categories-tree` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | إدارة شجرة فئات المصروفات. |
| `/expense-reversals` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟡 MEDIUM | نعم | `—` | عكس قيد مصروف (admin). |

### التقارير (`reports`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/reports` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تقارير عامة قراءة فقط. |
| `/reports/ads` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تقرير الإعلانات. |
| `/reports/advertising-expenses` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تقرير مصروفات الإعلانات. |
| `/operational-reports` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تقارير تشغيلية مجمّعة. |
| `/legacy-usage-report` | `mixed` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص استخدام الصفحات القديمة (يساعد في تنظيف Iter-250). |

### الإعدادات والتشخيصات (`admin_diagnostics`)

| Route | المصدر | SSOT | تصنيف | Hide | Risk | يؤثر على الرصيد؟ | البديل | السبب |
|---|---|---|---|---|---|---|---|---|
| `/audit/ledger-health` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | صحة الـ ledger العامة. |
| `/audit/post-migration` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تدقيق بعد الـ migration. |
| `/settings/accounting-cutoffs` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تواريخ القطع المحاسبية. سيستخدمها Reset لاحقاً. |
| `/accounting/migration` | `general_ledger` | LEGACY | 🟠 DEPRECATE | 🔍 NEEDS_REVIEW | 🔴 HIGH | نعم | `—` | أداة هجرة استُخدمت مرة واحدة. خطر إعادة تشغيلها بالخطأ. يجب إخفاؤها من القائمة بعد Iter-250. |
| `/diagnostics/api-permissions` | `config_only` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تشخيص صلاحيات الـ API. |
| `/settings/operation-account-bindings` | `config_only` | CONFIG | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | ربط أنواع العمليات بالحسابات. |
| `/alerts` | `general_ledger` | DIAGNOSTIC | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | تنبيهات النظام. |
| `/operations-dashboard` | `general_ledger` | SSOT | 🟢 KEEP | ✅ KEEP_VISIBLE | 🟢 LOW | لا | `—` | لوحة تشغيلية رئيسية. |

---

*Generated by `/app/scripts/regen_inventory_doc.py` from `financial_pages_inventory_data.py`.*
