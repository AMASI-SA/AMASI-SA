# MEZAN — Smart E-commerce Accounting App (PRD)

## Original Problem Statement
بناء تطبيق محاسبي ذكي للتجارة الإلكترونية (MEZAN). يحلّل ملفات Excel من سلة،
يستقبل بيانات من Make.com، يتتبّع التسويات، يدير الأصول والالتزامات، ويحسب المركز المالي.

## Critical Accounting Rules
- **Iter-120 — Refund-Date Aggregation**: Sales by `created_at_provider`, refunds by `refunded_at`.
- **Iter-121 — Weekday-Based Settlement Cycle**: `invoice_weekdays` + `transfer_weekdays`.
- **Iter-122 — Strict Issue-vs-Transfer Separation**: Only `invoice_weekdays` creates settlements.
- **Iter-123 — Period Start Convention**: `invoice_weekday` is the FIRST day of a period. Period spans `[invoice_weekday, next_invoice_weekday - 1]`. Issue date = next invoice_weekday (when provider generates statement).
- **Iter-130 — Asia/Riyadh Local Time Window**: Saudi local YYYY-MM-DD is converted to a UTC ISO window (`-3h` on each side) before filtering Mongo. Matches how Tabby/Tamara cut off invoices at Saudi midnight.

## User Profile
- Arabic merchant (عرفات — amasi.jewelery@gmail.com).
- Tests on production (mezansalla.com).
- Always remind: **Save to Github → Redeploy** before testing.

## Architecture
- React + Tailwind frontend (RTL Arabic)
- FastAPI backend (motor / async MongoDB)
- Strict double-entry accounting
- Background asyncio tasks (BNPL hourly auto-sync)
- SSOT Balance service

## Key Modules
- **BNPL Suite**: clients, auto-sync, refund audit, weekly settlements (refund-date + weekday cycle + period-start), SSOT balances, auto-matching, period drill-down.
- **Financial Input Hub**: search-based counterparty + cumulative balance.
- **Reconciliation + Accounts + Transfers + المركز المالي**: bound to BNPL SSOT.

## Completed Work
- **Iter-159p (Feb 13 2026)**: 🛡 تعديل سلوك «تصفير المديونيات» — حفظ الرصيد والتعبئات.
  - **User clarification**: "عدّل الزر الحالي ليصفّر المديونيات فقط (يبقي الرصيد والتعبئات)."
  - **Backend**: تعديل `/api/ad-accounts/{cp_id}/reset-debt`:
    - يحذف `liabilities` (kind=ad_account, counterparty_id=cp_id) ✅
    - يحذف `ad_account_ledger` **فقط من نوع `type=spend`** ✅
    - **يحتفظ بـ** `ad_account_ledger` نوع `topup` ✅ (تمثل أصل: مبالغ شحنتها من البنك)
    - **لا يصفّر** `counterparty.balance` ✅
    - يمسح فقط markers المزامنة (`last_auto_sync_date`, `last_yesterday_synced_for`)
    - الـ response يتضمن `balance_preserved` للشفافية
  - **Frontend**: تحديث رسالة التأكيد لتوضّح ما سيُحذف وما سيُحفظ. زر «🗑 تصفير المديونيات».
  - **Test** (`test_reset_debt_iter159o.py` — passed): seed بـ 2 spend + 1 topup + 1 liability + balance=1500 → بعد reset: liab=0, spend=0, **topup=1 محفوظ**, **balance=1500 محفوظ** ✅


- **Iter-159o (Feb 13 2026)**: 🗑 زر «تصفير المديونية» للحسابات الإعلانية.
  - **User request**: "أبغى أصفّر المديونيات في الحسابات الإعلانية والمديونية أضيفها من جديد عبر إضافة المديونيات التاريخية. يوجد لخبطة في الأرصدة."
  - **Backend**: endpoint جديد `POST /api/ad-accounts/{cp_id}/reset-debt`:
    - يحذف كل `liabilities` (kind=ad_account) للحساب
    - يحذف كل `ad_account_ledger` للحساب
    - يصفّر `counterparty.balance` ويمسح markers المزامنة (`last_auto_sync_date`, `last_yesterday_synced_for`)
    - يُرجع counts لما تم حذفه
  - **Frontend**: زر «🗑 تصفير» (أحمر) بجانب «🔧 إعادة احتساب» في كل بطاقة حساب إعلاني، مع تحذير قبل التنفيذ.
  - **Workflow الموصى به**: تصفير ← ثم استخدام «ترحيل المديونيات التاريخية» لإعادة البناء من API بشكل نظيف.
  - **Test** (`test_reset_debt_iter159o.py` — passed): 3 ledger rows + 2 liabilities + balance=999 ← بعد reset: 0/0/0 ✅


- **Iter-159n (Feb 13 2026)**: 🔧 زر «إعادة احتساب المديونية» في صفحة الحسابات الإعلانية.
  - **User report**: "المديونية في META = 8,784.09 رغم أن الحقيقية 2,977.99 والباقي مسدد. الأرقام مكررة وليست حقيقية."
  - **Root cause**: قبل Iter-159m، عمليات الترحيل التاريخية المتكررة كانت تستدعي `_apply_uncovered` التي تضيف للـ liability المفتوحة الموجودة (سواء كانت بمصدر cron أو migration). خطوة الـ Reversal كانت تبحث فقط عن source=ad_account_migration ← لا تستطيع التراجع عن الإضافات على liabilities الـ cron ← تضخّم الـ liability تراكمياً.
  - **Fix**:
    - **Backend**: endpoint جديد `POST /api/ad-accounts/{cp_id}/recompute-debt` يحسب المديونية الصحيحة من قاعدتي حساب صلبة:
      - `Σ uncovered من ledger.spend` (إجمالي الصرف الذي تحوّل لدين فعلاً)
      - `Σ paid_amount من liabilities` (إجمالي المسدد)
      - المديونية الصحيحة = `max(uncovered - paid, 0)`
    - يُغلق كل الـ liabilities المفتوحة (`status: paid`, `rebuild_note`) ويُنشئ liability واحدة جديدة بالمصدر `ad_account_recompute`.
    - **Frontend**: مكوّن `RecomputeDebtButton` في كل بطاقة حساب إعلاني: زر «🔧 إعادة احتساب» مع `confirm()` ثم عرض النتيجة (قبل/بعد + إجمالي الصرف والمسدد).
  - **Test** (`test_recompute_debt_iter159n.py`): سيناريو واقعي بـ ledger=3000، paid=1000، liability مضخّمة بـ 6500 ← بعد الـ recompute = liability واحدة بـ 2000. ✅
  - **Use**: المستخدم يضغط زرّ «إعادة احتساب» على كل حساب إعلاني (META, Snapchat A, Snapchat B) → يتم تصحيح المديونية تلقائياً من واقع البيانات.


- **Iter-159m (Feb 13 2026)**: 🔧 إصلاح تكرار سجلات الصرف عند ترحيل المديونيات التاريخية.
  - **User report**: "ترحيل المديونيات التاريخية يتم إضافتها كصرف ولا يتم تحديث المديونيات المضافة سابقاً. كل حساب إعلاني يحوي سجل صرف واحد باليوم. المفروض يحدث الصرفيات السابقة تحديث وليس يضيف سجل جديد. تاريخ 12 يونيو مضاف من قبل وتم إضافته من جديد عند الترحيل."
  - **Root cause**: في `ad_account_routes.py::migration_apply` mode='daily'، الكود كان يستدعي `ad_account_ledger.insert_one()` لكل يوم بدون فحص وجود سجل سابق ← انتهاك قاعدة «سجل واحد لكل (حساب، يوم)».
  - **Fix**:
    1. **Find-then-update**: قبل الإدراج، البحث عن جميع سجلات `type=spend` لنفس (counterparty, date) — auto_cron أو migration.
    2. **Delta calculation**: `delta = platform_total - prior_applied`. إذا `delta <= 0` ← no-op كامل (لا rollback آلي للديون السابقة).
    3. **Apply delta only**: تطبيق الـ delta فقط على balance + liability (تجنّب double-counting).
    4. **Collapse duplicates**: إذا وُجد > 1 سجل قديم ← الإبقاء على الأقدم وحذف الباقي تلقائياً (دفاعي ضد البيانات السابقة للإصلاح).
    5. **Update with cumulative**: السجل المتبقي يُحدَّث بـ `amount = platform_total` (تراكمي صحيح).
  - **Tests** (`test_migration_no_duplicate_iter159m.py`):
    - Pre-existing auto_cron row بـ 200، ترحيل يجلب 350 ← سجل واحد يصبح 350، liability يصبح 350 (delta=150) ✅
    - 3 سجلات مكررة (100+50+50=200) ← تنطوي إلى سجل واحد عند الترحيل ✅ (passes individually)


- **Iter-159k (Feb 13 2026)**: ✅ اختبارات شاملة + مزامنة "اليوم السابق" مرة واحدة.
  - **User request**: "اختبار إضافة تكلفة الإعلانات اليومية تلقائي من الحسابات الإعلانية كمديونية. كل حساب يضاف له صف واحد باليوم. تحديث تراكمي. بدون تكرار. مزامنة كل نصف ساعة. مزامنة اليوم السابق مرة واحدة للتأكد من تسجيل أمس كاملاً."
  - **Backend**: 
    - دالة جديدة `run_yesterday_final_sync(db)` في `ad_account_routes.py` ← تدير `_run_sync_for_all` لتاريخ الأمس مرة واحدة لكل مستخدم، تستخدم marker `last_yesterday_synced_for` على counterparty لمنع التكرار في نفس اليوم الشمسي.
    - الـ scheduler في `server.py` يستدعي الآن `run_yesterday_final_sync` بعد كل دورة نصف ساعة (idempotent لذا آمن للتشغيل بشكل متكرر).
  - **Tests** (`test_ad_account_accounting_iter159k.py` — 5/5 passed ✅):
    1. **One ledger row per account per day** — حسابان منفصلان × 5 passes نصف ساعية = صفّان فقط بمبلغ صحيح لكل حساب (100, 50).
    2. **Cumulative update** — 100→150→150 (no-op)→200 = صف نهائي بمبلغ 200 وliability واحد بقيمة 200.
    3. **Balance covers spend then overflow** — حساب رصيده 60، صرف 100 ← يستهلك الـ 60 ثم ينشئ دين 40 = توازن محاسبي سليم.
    4. **Yesterday final sync runs once per day** — 3 استدعاءات متتالية = اسـترداد واحد فقط لـ this user (marker stable).
    5. **No duplicate liability across passes** — 10 passes بمبالغ متزايدة (50→140) = liability واحد بمبلغ 140 وledger واحد.


- **Iter-159j (Feb 13 2026)**: 👻 بطاقات منفصلة لكل حساب سناب شات في لوحة التحكم.
  - **User request**: "فصل بطاقة الحسابات الإعلانية سناب شات في لوحة التحكم كل حساب يكون مستقل: الصرف، الطلبات، متوسط تكلفة الطلب، المبيعات، الصرف خلال الشهر الحالي. بدون التعديل على البطاقة الحالية".
  - **Backend** — endpoint جديد `GET /api/dashboard/snapchat-accounts-summary`:
    - يجلب كل حسابات سناب الإعلانية (`counterparties.ad_provider=snapchat`).
    - **الصرف**: من `ad_account_ledger.type=spend` للشهر الحالي مجمَّعاً لكل counterparty.
    - **الطلبات والمبيعات**: من `snapchat_daily_stats` (Pixel) أو fallback لـ store attribution، مع **تقسيم proportional حسب نسبة صرف كل حساب** (لأن Pixel لا يعطي per-account breakdown).
    - **متوسط تكلفة الطلب**: spend ÷ orders.
    - **ROAS**: revenue ÷ spend.
    - **المديونية والحد الائتماني**: من `liabilities` و `counterparties.credit_limit`.
  - **Frontend** — مكوّن `SnapchatAccountsCards.jsx`:
    - بطاقة مستقلة في الـ Dashboard **بعد** `SnapchatOfficialCard` (بدون لمسها).
    - تظهر فقط عند وجود ≥ 2 حسابات سناب (تجنّب التكرار).
    - Grid بـ 2 أعمدة، كل بطاقة فيها: اسم الحساب، نسبة الصرف (badge)، 4 إحصائيات ملوّنة (الصرف/الطلبات/متوسط تكلفة الطلب/المبيعات+ROAS)، شريط مديونية ملوّن، رابط «إدارة الحساب →».
  - **التحقق**:
    - Snap A: spend=1500 (65.2%) → orders=15, CPO=100, revenue=3000, ROAS=2.0× ✅
    - Snap B: spend=800 (34.8%) → orders=8, CPO=100, revenue=1600, ROAS=2.0× ✅
    - Test pytest نجح بـ split 75/25 لمدخلات spend=1500+500=2000 و orders=20 ✅
    - اللقطة من Preview تؤكد ظهور البطاقتين بـ البطاقة الأصلية فوقها سليمة.


- **Iter-159i (Feb 13 2026)**: 💳 حد المديونية ونسبة الصرف لكل حساب إعلاني.
  - **User request**: "لكل حساب إعلاني — حد المديونية الحد المسموح مبلغ ونسبة الصرف التي يظهر بعدها الإشعار بأن المديونية على وشك النفاذ".
  - **Backend**:
    - حقلان جديدان في `counterparties`: `credit_limit` (SAR) و `alert_threshold_pct` (0-100).
    - Endpoint جديد `PUT /api/ad-accounts/{cp_id}/credit-limit` مع Pydantic validation (ge=0, le=100). يدعم إرسال أي حقل وحده أو كليهما، رفض الـ body الفارغ.
    - مولّد `_gen_high_ad_debt` في `alerts_routes.py` أُعيد كتابته: إذا كان `credit_limit > 0` يستخدم نسبة (debt/credit_limit) ويقارنها بـ `alert_threshold_pct` (افتراضي 80%). عند ≥95% → `critical`، وإلا → `warning`. عناوين التنبيهات: «مديونية {الاسم} على وشك النفاذ» مع رسالة تشرح المبلغ والنسبة والسقف.
    - السلوك القديم (نسبة من balance+debt) محفوظ كـ fallback للحسابات التي لم تُضبط لها حدود.
    - `_summarise` يُرجع الآن `credit_limit` و `alert_threshold_pct`.
  - **Frontend** (`AdAccounts.jsx` — مكوّن `CreditLimitPanel`):
    - بطاقة جديدة أسفل صف الإحصائيات (الرصيد/المديونية/الصرف) في كل بطاقة حساب إعلاني.
    - عرض السقف + نسبة التنبيه + **progress bar ملوّن** (أخضر عادي، أصفر فوق العتبة، أحمر فوق الحد).
    - رسائل تحذير مدمجة: «⚠ على وشك النفاذ — بلغت X%» / «⚠ تجاوزت الحد!».
    - زر «ضبط الحد» يفتح فورم بحقلين، حفظ بـ PUT ثم إعادة تحميل القائمة. validation كامل في الواجهة.
  - **Tests** (`test_ad_credit_limit_iter159i.py` — passed):
    - 500/1000 = 50% (تحت 60%) ⇒ لا تنبيه ✅
    - 700/1000 = 70% (فوق 60%) ⇒ warning ✅
    - 980/1000 = 98% ⇒ critical مع كلمة «النفاذ» في العنوان ✅
    - Validation: حد سلبي / نسبة > 100 / body فارغ ⇒ 422/400 ✅


- **Iter-159h (Feb 13 2026)**: 🔔 إشعارات/تنبيهات التسويات الذكية.
  - **User selection**: g (كل الأنواع الستة) + a (7 أيام لـ BNPL) + a (5% للفرق) + a (جرس) + b (بطاقة في الداشبورد قابلة للطي) + d (كل التصرفات).
  - **Backend** (`alerts_routes.py`): 6 مولّدات تنبيهات idempotent عبر `fingerprint = type:entity:id`:
    1. `overdue_bnpl` — طلبات tabby/tamara > 7 أيام بلا تسوية
    2. `amount_diff` — الفعلي ≠ المتوقع بأكثر من 5%
    3. `missing_salla` — لا فاتورة سلة منذ 14 يوم
    4. `high_courier_balance` — رصيد شركة شحن > 5000 ر.س
    5. `unmatched_order` — طلبات بدون مطابقة منذ 10 أيام
    6. `high_ad_debt` — مديونية حساب إعلاني > 50% من السقف
  - **APIs** (8 endpoints): `/alerts/refresh`, `/alerts`, `/alerts/unread-count`, `/alerts/{id}/{read|snooze|dismiss}`, `/alerts/read-all`, `/alerts/settings` (GET/PATCH).
  - **DB**: collections `settlement_alerts` (indexed on user+status, user+fingerprint+status) و `alert_settings`. Auto-expire للـ snoozed alerts عند انتهاء المدة.
  - **Frontend**:
    1. **🔔 NotificationBell** — جرس عائم أعلى يسار الصفحة (desktop + mobile) مع شارة عداد و polling كل 60 ثانية. لوحة منسدلة بأزرار: فتح، مقروء، تأجيل (1س/1ي/1أ)، تجاهل.
    2. **📊 AlertsCard** — بطاقة قابلة للطي في لوحة التحكم تعرض أهم 5 تنبيهات + pills بعدد كل خطورة (حرج/تحذير/معلومة). حالة الطي محفوظة في localStorage.
    3. **📋 /alerts** — صفحة كاملة مع فلاتر (حالة + خطورة + نوع) وكل التصرفات على كل تنبيه.
  - **Tests** (`test_alerts_iter159h.py` — passed): دورة حياة كاملة (refresh → list → idempotency على re-refresh → mark-read → snooze → settings).


- **Iter-159g (Feb 13 2026)**: 📅 طلب تاريخ إصدار الفاتورة عند رفع ملف سلة.
  - **User confirmation**: "تاريخ التحويل في جدول التسويات = تاريخ إصدار الفاتورة من المنصة".
  - **Root cause**: ملفات سلة (Excel) لا تحتوي على تاريخ إصدار الفاتورة، فالنظام كان يستخدم تاريخ الرفع كبديل.
  - **Backend**: حقل اختياري جديد `invoice_date` في `POST /api/payment-settlements/upload` يُحفظ في `header.settlement_date` بعد التحقق من الصيغة `YYYY-MM-DD`. يظهر مباشرة في `unified-overview` بـ `source=manual`.
  - **Frontend** (`SallaSettlements.jsx`): قبل بدء الرفع، يظهر `window.prompt` يطلب التاريخ (افتراضي: اليوم). إلغاء الـ prompt يلغي الرفع. صيغة غير صحيحة تظهر toast خطأ.
  - **Test** (`test_settlement_upload_invoice_date_iter159g.py` — passed): رفع ملف سلة بـ `invoice_date=2026-05-20` ← `header.settlement_date=2026-05-20`, الجدول الموحَّد يعرضه بـ `source=manual` ✅


- **Iter-159f (Feb 13 2026)**: 🧹 سجل تراكمي واحد يومياً لمديونية الإعلانات (إصلاح تكرار النصف ساعة).
  - **User feedback**: "عند إضافة مديونية الإعلانات كل نصف ساعة يتم إضافته تراكمي وليس كل طول اليوم يضيف سجلات جديدة. كل حساب إعلاني لديه باليوم سجل واحد تراكمي".
  - **Root cause**: `_run_sync_for_all` في `ad_account_routes.py` كان يستدعي `ad_account_ledger.insert_one(...)` في كل مزامنة (≈48 مرة يومياً) ← سجل جديد بكل نصف ساعة.
  - **Fix**:
    1. **Find-then-update**: قبل الإدراج، البحث عن سجل `auto_cron` لنفس (counterparty, date). إن وُجد ← تحديث `amount` ليصير الإجمالي التراكمي للمنصة + دمج `breakdown` (from_balance, uncovered, delta_applied, last_sync_at).
    2. **Self-healing collapse**: إن وُجدت سجلات قديمة مكررة من قبل الإصلاح (find يُرجع >1) ← الإبقاء على الأقدم وحذف الباقي.
  - **Tests** (`tests/test_ad_account_cumulative_iter159f.py`):
    - مزامنتان متتاليتان في نفس اليوم → سطر واحد فقط بالمجموع التراكمي ✅
    - زرع 3 سجلات مكررة قديمة (20+30+30) → بعد المزامنة التالية تنطوي إلى سطر واحد (amount=100) ✅
  - **Idempotency**: المزامنة الثالثة بنفس البيانات = no-op (delta=0 لا يُنشئ شيئاً).


- **Iter-159e (Feb 13 2026)**: 📅 تاريخ التحويل الفعلي في "جميع التسويات الموحَّدة".
  - **User feedback**: "تاريخ التحويل يكون تاريخ التسوية نفسه مو تاريخ إضافة الفاتورة".
  - **Backend**: aggregation على `settlement_entries` لاستخراج `MAX(settlement_date)` لكل ملف. تم تعريف ترتيب أولوية واضح: header.settlement_date (manual override) → max من الصفوف → header.transfer_date → uploaded_at. حقل جديد `settlement_date_source` في الاستجابة (`manual` / `file_rows` / `uploaded_at`).
  - **PATCH endpoint جديد** `/api/payment-settlements/{file_id}/settlement-date` — تعديل/مسح يدوي مع validation للصيغة YYYY-MM-DD.
  - **Frontend**: التاريخ في الجدول قابل للنقر يفتح prompt للتعديل. Badge 🟦 "يدوي" أو ⚠ "رفع" حسب المصدر.

- **Iter-159d (Feb 13 2026)**: 🔎 Drawer تفاصيل الجهة عند النقر على اسم المستفيد.
  - **User request**: "اجعل اسم المستفيد قابلاً للنقر فيفتح صفحة تفاصيل ذلك المورد/الموظف".
  - **Backend**: endpoint جديد `GET /api/parties/{party_id}/details` يبحث في `counterparties` ثم `operating_salaries` ويُرجع:
    - بيانات الجهة (الاسم، النوع، الفئة، الراتب الشهري للموظفين، الحالة، الملاحظات)
    - الإجماليات: owed_to_party / owed_from_party / net_balance
    - كل الالتزامات (مفتوحة + مغلقة) مرتبة من الأحدث
    - كل الحركات البنكية المرتبطة عبر `peer_liability_id`
    - تاريخ آخر نشاط + counts
  - **Frontend**: drawer جانبي (max-w-2xl) ينفتح من اليمين عند النقر على اسم المستفيد في جدول العمليات. يعرض 3 بطاقات إجماليات + meta info + جدولين (الالتزامات والحركات البنكية).


- **Iter-159c (Feb 13 2026)**: 👤 عمود "المستفيد" — اسم الجهة الفعلي.
  - **User feedback**: "اسم الشخص صاحب العملية الموظف مثلاً لا يظهر، تظهر اسم عملية الإدخال (تسوية سلفة)... أبغى أضيف عمود يظهر اسم المستفيد من هذي العملية".
  - **Root cause**: لرواتب الموظفين والسلف، الحقل `description` كان يحتوي وصف العملية ("تسوية سلفة"، "راتب يونيو") بدلاً من اسم الموظف.
  - **Backend**: `Step 2b` جديدة تستعلم `counterparties` و `operating_salaries` بـ batch مرة واحدة لبناء `party_id → name` map. كل عملية تحصل على `beneficiary_name` بالاسم الكنوني للجهة (المورد أو الموظف).
  - **Frontend**: عمود جديد بلون نيلي بين "المورد/الموظف/الحساب" و "المبلغ" يعرض اسم المستفيد. `data-testid=hub-recent-beneficiary-{id}`.
  - **التحقق**: راتب موظف "محمد العامل" يعرض الآن بشكل صحيح في عمود "المستفيد" مع بقاء الوصف "راتب يونيو" في عمود المورد/الموظف.

- **Iter-159b (Feb 13 2026)**: 📊 إجماليات المركز الصافي أسفل الجدول.
  - **User follow-up**: "نعم" (وافق على اقتراح إضافة الإجماليات).
  - **Backend**: استجابة `/financial-input-hub/recent` الآن تتضمن `totals: { owed_to_party, owed_from_party, net_balance, unique_parties }` محسوبة عبر الـ feed المفلتر بأكمله (وليس فقط الصفحة المعروضة)، مع احتساب كل جهة مرة واحدة لتجنّب التكرار.
  - **Frontend**: ثلاث بطاقات ملوّنة تحت الجدول — 🔴 إجمالي «كم له» / 🟢 إجمالي «كم عليه» / ⚪ صافي المركز (مع عدد الجهات الفريدة). تتفاعل مع البحث والفلتر تلقائياً.

- **Iter-159 (Feb 13 2026)**: 📊 Recent Entries — Directional Balance Columns.
  - **User request**: "إضافة عمود في جدول آخر عمليات الإدخال: اسم الموظف/الحساب/المورد + كم له + كم عليه".
  - **Backend** (`GET /api/financial-input-hub/recent`): for every fed item, enriched with three new fields — `party_id`, `owed_to_party` (ما علينا لها), `owed_from_party` (ما لنا عليها) — computed via a single grouped aggregation over `liabilities` (status ∈ unpaid/partial). Kinds bucketed as `supplier|salary|ad_account` → "كم له" and `salary_advance|receivable` → "كم عليه". Net `party_open_balance` kept for backward-compat.
  - **Frontend** (`FinancialInputHub.jsx` → `RecentEntriesTable`): replaced single "الرصيد المفتوح" column with two color-coded columns — red **كم له** + green **كم عليه**. Header expanded to "المورد / الموظف / الحساب". Empty values show muted "—".
  - **Bug-fix during build**: outer `total = len(feed)` was being shadowed by an inner aggregation loop variable, returning bogus `total_pages`. Renamed inner to `sub`. Verified via direct curl: supplier with 1500+500 correctly aggregates to `owed_to_party=2000` while pagination `total=2`.


- **Iter-157b (Feb 13 2026)**: 🔍 Recent Entries — Expanded coverage + search + operation filter.
  - **User request follow-up**: "الجدول يحوي على آخر تعليمات الإدخال وتسديد الرواتب وديون الرواتب جميع العمليات المضافة في مركز الإدخال تظهر بالجدول حسب الأحدث. مع إضافة اقتراح خيار البحث كامل".
  - **Expanded transaction coverage**: The recent endpoint now surfaces ALL hub-originated operations — added `expense`, `expense_payment`, `deposit`, `withdrawal`, `courier_transfer`, `cod_transfer`, `receivable_collect`, `shipping_payment`, `topup` to the `account_transactions` filter alongside the original four. Each gets a clear Arabic operation label (مصروف يومي، إيداع، تحويل شركة شحن، تحصيل من عميل، …).
  - **Search** (`?q=...`): case-insensitive substring match against `party_name` OR `operation` label.
  - **Operation filter** (`?op_filter=...`): values `create` (liability creations), `pay` (سداد/تسوية), `advance` (سلفة), `expense` (مصاريف), or `all`.
  - **Frontend**: New search bar above the table with a text input + dropdown (`كل العمليات` / إنشاء / سداد / سلفة / مصاريف). Both controls debounce 250ms and auto-reload via `useEffect`. Pagination buttons now propagate the active filters.
  - **Tests**: 6/6 pytest (`test_financial_input_hub_recent_iter157.py`): empty feed, single liability, 15-item pagination split, amount edit round-trip, **search filter by party name**, **op_filter=create scoping**. All green.

- **Iter-157 (Feb 13 2026)**: 📋 Financial Input Hub — Recent Entries table with pagination + amount edit.
  - **User request**: "مركز الإدخال المالي — عرض جدول عمليات يعرض آخر 10 عمليات إدخال مع خيار تنقل بين باقي صفحات الجدول أسفل الجدول. العملية، اسم المورد/الموظف، المبلغ، كم له كم عليه سابق. مع إمكانية التعديل على المبلغ المدخل."
  - **Backend** (`GET /api/financial-input-hub/recent?page=N&page_size=10`): Unifies merchant-initiated entries from `liabilities` (excluding auto_generated salary rows) AND `account_transactions` (debt_payment, salary_advance, ad_account_topup, salary_settlement). Returns sorted feed with operation label, party name, amount, current open balance for the party (computed via aggregation), created_at, and `editable` flag. Default page_size=10, max=50.
  - **Frontend** (`/app/frontend/src/pages/FinancialInputHub.jsx`): New `RecentEntriesTable` component rendered below all tabs. Columns: العملية, المورد/الموظف, المبلغ, الرصيد المفتوح, التاريخ, + ✎ edit button. Pagination footer shows "صفحة X من Y · Z عملية" with «السابق» / «التالي» nav. Click ✎ → window.prompt for new amount → PUT `/liabilities/{id}` → toast + auto-reload. Posted bank transactions are surfaced read-only (editable=false) to avoid balance-corruption risk.
  - **Tests**: 4/4 backend pytest (`test_financial_input_hub_recent_iter157.py`): empty feed, single liability listing, 15-item pagination split (10+5, no dupes), amount edit round-trip via existing PUT endpoint.

- **Iter-156 (Feb 13 2026)**: 🟧 Salla Settlements — Dedicated page (mirror of Tabby/Tamara) with per-payment-method analytics.
  - **User request**: "تسويات سله مثل صفحة تسوية تمارا وتابي" — wants Excel + API support, per-method commissions, expected-vs-actual comparison with mismatch alerts.
  - **What was already built**: The Salla parser (`/app/backend/settlements_import/parsers/salla.py`) is comprehensive — it handles all payment methods (mada / credit card / Apple Pay / STC Pay / Google Pay), refunds (full vs partial detection), wallet recharge ("مشتريات سله"), Arabic diacritics, invoice number extraction from sheet title. It writes to `settlement_files` + `settlement_entries`.
  - **New backend** (`/api/payment-settlements/_analytics/salla`): Returns `files[]`, `per_method[]` (aggregated counts/gross/fees/vat/net/refunds + effective fee rate per method via MongoDB aggregation pipeline), and `totals`. User-scoped, includes only files where `provider='salla'`.
  - **Fix to `utils.py`**: Added Arabic payment-method aliases — "أبل باي" → `apple_pay`, "أس تي سي باي" → `stc_pay`, "جوجل باي" → `google_pay`. Renamed canonical keys to use snake_case (`apple_pay`, `stc_pay`) for consistency.
  - **New frontend** (`/app/frontend/src/pages/SallaSettlements.jsx`): Pattern-matched on BnplSettlements. Drag-and-drop Excel upload (calls `/payment-settlements/upload` with `provider_hint=salla`), per-method breakdown table with colored badges and effective fee rate, file list with delete action, refund totals when present. Route: `/salla-settlements`. Sidebar item: "تسويات سلة 🟧" under العمليات المالية.
  - **Tests**: 3/3 backend pytest (`test_salla_settlements_iter156.py`): empty analytics, upload + per-method aggregation across mada/credit_card/apple_pay/refunds, provider scoping.
  - **NOT YET DONE (Phase 2 — surfaced in roadmap)**: Expected-vs-actual commission comparison with mismatch alerts (needs per-method configurable commission rates in settings, which already exists in payment_methods.py but isn't wired here yet). Salla API auto-sync (needs an OAuth flow with Salla Merchant API).

- **Iter-155 (Feb 13 2026)**: 🐛 Shipping Companies Settings — Save bug + Add/Remove capability.
  - **User feedback**: "عند حفظ إعدادات شركات الشحن لا يتم حفظ المعلومات المضافه".
  - **Root cause**: The backend `ShippingCompany` Pydantic model (`/app/backend/server.py` line 289) declared only 4 fields (`name`, `cost_per_order`, `vat_percent`, `is_deferred`). The new ShippingCompanySettings UI was sending `cod_fee_percent` and `cod_fee_fixed_per_order` too — but Pydantic v2's default `ignore` extras policy silently dropped them on round-trip. Additionally, the page had no UI to add/remove companies.
  - **Backend fix**: Added `cod_fee_percent: Optional[float] = Field(default=0.0, ge=0, le=1)` and `cod_fee_fixed_per_order: Optional[float] = Field(default=0.0, ge=0)` to the model.
  - **Frontend fix** (`/app/frontend/src/pages/ShippingCompanySettings.jsx`):
    - **➕ "إضافة شركة جديدة"** button (`data-testid="add-shipping-company"`): prompts for the name and inserts a row with defaults (deferred=true, vat=15%).
    - **"إجراءات" column** with × button per row to remove a company from settings (`data-testid="remove-{name}"`).
    - Layout reflowed so save + add are side-by-side.
  - **Tests**: 5/5 backend pytest (`test_shipping_settings_iter155.py`): GET works, COD fields persist round-trip, can add new company, can remove company, legacy fields still work.

- **Iter-154 (Feb 13 2026)**: 🔗 Unified Employee Settlement — Merged "Pay Liability" + "Salary Advance" workflows.
  - **User feedback**: "تسديد راتب الموظف التراكمي او اضافه مديونيه تكون مدمجه بصفحه واحده ... اذا كان المبلغ المدخل أكبر من رصيد الموظف يقوم النظام بتسديد المبلغ المتراكم وباقي المبلغ يسجل ك سلفه ع الموظف".
  - **Backend** (`POST /api/liabilities/employee-settlement`): Inputs `employee_salary_id`, `amount`, `paid_from_account_id`, `payment_date`, `notes`. Computes live `net_due` from the accrual aggregator and intelligently splits: `salary_part = min(amount, net_due)` is paid against the largest open salary liability (or a fresh topup row with a unique period_key), and `advance_part = amount − salary_part` is recorded as an open `kind=salary_advance` liability. Both halves post a single bank debit each. Returns a structured response with `salary_part`, `advance_part`, `paid_liability_id`, `advance_liability_id`, and a friendly Arabic `message`.
  - **Frontend** (`/app/frontend/src/pages/FinancialInputHub.jsx`):
    - Pay-Liability tab renamed to **"سداد التزام / تسوية موظف"** with a hint explaining the auto-split behaviour.
    - Submit handler now branches: when `selected.kind === 'salary'` AND `selected.employee_salary_id`, calls the new endpoint; otherwise the legacy `/pay` endpoint with the over-amount cap (preserved for supplier/ad_account).
    - **Live split-preview banner** (indigo, `data-testid='pay-liab-split-preview'`) appears once the entered amount > net_due, displaying both halves in side-by-side cards before submission.
    - Standalone **"سلفة موظف"** tab now shows an informational banner (`data-testid='adv-merged-banner'`) pointing merchants to the unified flow. It still works for pure-advance recording.
  - **Tests**:
    - 7/7 backend pytest (`test_employee_settlement_iter154.py`): exact-match payment, partial, overpay-with-split, pure advance (future start date), insufficient bank, suspended-employee compatibility, household rejection.
    - End-to-end testing_agent run (iteration_54.json) — **100% (3/3 scenarios)**: virtual-badge surfaces accrued, split-preview renders, overpay submits with combined toast, advance tab banner links back.

- **Iter-153 (Feb 13 2026)**: 👁️ Suspended Employees — Selectable in Pay-Liability + Advance + Search with visual warning.
  - **User feedback**: "عندما يكون الموظف موقوف لا أستطيع البحث عنه وإضافة مديونيه أو سداد التزام له — أبغى يكون مسموح مع التنبيه".
  - **Frontend change** (`/app/frontend/src/pages/FinancialInputHub.jsx`, line 1549): Removed the `e.status === "active"` filter — `employees` now includes both active and suspended (kept the `category === "employee"` filter to still exclude household/charity rows).
  - **Visual warnings**:
    - PayLiabilityForm dropdown already shows the `موظف موقوف` badge (existing logic).
    - AdvanceForm dropdown now shows a `⚠ موقوف` slate badge next to each suspended employee (`data-testid="adv-emp-suspended-{id}"`).
    - AdvanceForm displays a prominent amber warning above the EmployeeBalanceCard when a suspended employee is selected: "هذا الموظف موقوف — لكنه يمكن أن يكون لديه التزامات معلّقة أو تسويات نهائية".
  - **Backend untouched**: `/operating-expenses/salaries`, `/liabilities/salary-accrual-summary`, `/liabilities/{id}/pay`, and `/liabilities` (kind=salary_advance) already accept suspended employees — only the frontend filter was the gate.
  - **Tests**: 4/4 backend pytest (`test_suspended_employee_visibility_iter153.py`) confirm: salaries list returns both, accrual summary includes suspended, can pay existing liability for suspended employee, can record salary advance for suspended employee.

- **Iter-152 (Feb 13 2026)**: 🛡️ Shipping Courier Transfers — Validation guardrails.
  - **What changed (backend `/api/shipping-accounts/transfers`)**:
    1. **courier_to_bank**: rejects if `amount > net_balance` (the courier's open balance with us). Clear Arabic error: "المبلغ أكبر من المستحق على «X» (Y ر.س)". Also rejects when there is NO outstanding balance to receive against.
    2. **bank_to_courier**: rejects when the selected bank's `current_balance < amount`. Clear error: "رصيد الحساب البنكي «X» غير كافٍ".
    3. **bank_to_courier over-payment**: when `amount > what we owe the courier`, the transfer SUCCEEDS but the response includes `overpayment` (delta) and `overpayment_note` (Arabic, ready for toast).
    4. Unknown/non-deferred companies skip validation (preserves existing behavior).
  - **What changed (frontend `/app/frontend/src/pages/ShippingTransfers.jsx`)**:
    - Live hint pills under the form: "المستحق علينا/لنا" for the selected company + "رصيد البنك المختار".
    - Inline warning ⛔ (red, blocks submit button) for hard-rejects; ⚠️ (amber, allows submit) for over-payment notice.
    - Submit button is `disabled` when a blocking condition exists.
    - On successful over-payment, the success toast shows the `overpayment_note` for ~7s.
  - **Tests**: 8/8 pytest (`test_shipping_transfer_validation_iter152.py`) — covers all four rules plus an "unknown company" preservation test.

- **Iter-151d (Feb 13 2026)**: 🧹 Data Hygiene — One-click "Clean up stale partial liabilities".
  - **Why**: The Iter-151c shortfall-virtual-entry logic LAYERS AROUND stale `partial`-but-fully-paid rows. To eliminate the root cause once and for all, the merchant now has a self-service button.
  - **Endpoint** (`POST /api/liabilities/admin/cleanup-stale-partial`): User-scoped. Finds rows with `status='partial'` AND `paid_amount + 0.01 >= expected_amount`, flips them to `status='paid'`, and returns a sample of fixed rows for audit. Supports `?dry_run=true` to show the count first without mutating.
  - **UI** (`/app/frontend/src/components/EmployeeBalanceCard.jsx` → `SalaryAccrualSummaryCard`): Discreet underlined link **"🔧 تنظيف بيانات الالتزامات القديمة"** under the four-box totals. Click → dry-run shows count → confirm dialog → real cleanup → toast + auto-refresh.
  - **Tests**: 4/4 backend pytest (`test_cleanup_stale_partial_iter151d.py`): dry-run preserves state, real run fixes stale + leaves healthy partial rows alone, returns 0/0 when no candidates, and is strictly user-scoped (never touches another tenant's data).
  - **Visible on**: Operating Expenses page (`/operating-expenses`) → الرواتب الشهرية tab. (The card is shared across Dashboard + Reports, so the button appears anywhere `SalaryAccrualSummaryCard` is rendered.)

- **Iter-151c (Feb 13 2026)**: 🐛 Pay-Liability Search — "shortfall virtual entry" for stale partial rows.
  - **Bug**: User reported on production: شهاب التراكمي 4200 ر.س لكن الزر يرفض السداد بـ "المبلغ أكبر من المتبقي (0.00)". تشخيص iter-151b كان جزئياً.
  - **Real root cause**: Even after the iter-151b fixes, if the employee had an EXISTING open salary liability with `remaining=0` (e.g. stale `partial` row with paid==expected from a prior advance-offset round), the virtual-entry branch was SKIPPED because `groups.has(empKey)` was already true. The merchant got stuck on the zero-remain row.
  - **Fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`): Reworked the virtual-entry loop to ALWAYS surface a virtual rep whenever `existingGroup.sumRemaining < employee.net_due`. The virtual covers the **shortfall** (= net_due − existingRemain) and is promoted to `representative`, so clicks route through the salary-topup flow regardless of stale data. The dropdown's `onClick` now short-circuits to `pickLiability(g.representative)` when `g.virtual` is true.
  - **Backend behavior** (`liabilities_routes.py::create_salary_topup`): When the merchant has a stale partial row counted in `paid_amount`, the `_aggregate_salary_accrual` aggregator already subtracts it from `accrued` → returns the correct outstanding `net_due`. The topup endpoint then caps at this net_due, so over-payment is impossible (correct accounting invariant).
  - **Tests**: 8/8 backend pytest (`test_pay_liability_search_iter151.py`) + end-to-end Playwright in iteration_53.json — all green. The bug is NOT reproducible on Preview after the fix; redeploy required to mirror on production.

- **Iter-151b (Feb 13 2026)**: 🐛 Pay-Liability Search — "المديونيه 0 ريال رغم أن البحث يظهر 4200" Fix.
  - **Bug**: User reported (Arabic): for employee "شهاب", search dropdown showed 4200 SAR but clicking + submitting returned the error "المبلغ أكبر من المتبقي (0.00)".
  - **Root cause (two distinct issues)**:
    1. When a counterparty group in the search contained MULTIPLE open liabilities and at least one had `remaining_amount = 0` (e.g. a `partial` row whose paid_amount equals expected after an advance was deducted), the GROUP's `representative` could resolve to the zero-remain row — group's `sumRemaining=4200` shown in dropdown, but the selected liability had `remaining=0`.
    2. When the Iter-151 salary-topup endpoint created a fresh row for an employee with an open advance ≥ expected_amount, `_apply_open_advances_to_salary` immediately offset the topup → `paid=expected`, `status=paid`, `remaining_amount=0`. Frontend showed the topup as selected but submit failed with the same "0.00" error.
  - **Fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`):
    - Group representative selection now prefers rows with `remaining > 0` (lines 361-377). When a group mixes paid and unpaid rows, the unpaid one is picked.
    - The dropdown's `onClick` handler explicitly picks the first `g.items` row with `_liabRemaining > 0` (lines 600-610) — defensive against legacy data with stale `partial` zero-rem rows.
  - **Fix** (`/app/backend/liabilities_routes.py`): `salary-topup` endpoint now flags `fully_offset_by_advance: true` with a clear Arabic `message` field when the open advance fully covers the topup. The frontend displays a friendly `toast.info` instead of attempting a zero-SAR payment.
  - **Tests**: 8/8 backend pytest tests in `/app/backend/tests/test_pay_liability_search_iter151.py` (including new `test_salary_topup_fully_offset_by_advance_returns_clear_flag`).

- **Iter-151 (Feb 13 2026)**: 🐛 Financial Input Hub — Pay-Liability Search: Employee re-appears after full payment.
  - **Bug**: After fully paying an employee's salary liability (e.g. "جمال"), the next search in the Pay-Liability dropdown showed only `ابو جمال` (different employee whose name CONTAINS "جمال"). The just-paid employee disappeared even though he's still an active employee with continued daily/monthly salary accrual.
  - **Root cause**: The search dropdown was built strictly from `openLiabilities` (status ∈ unpaid/partial). Once an employee's only open salary row was fully paid, the row left the list and the employee became invisible — even when `accrualMap[empId].net_due > 0`.
  - **Frontend fix** (`/app/frontend/src/pages/FinancialInputHub.jsx`): Added **virtual search entries** for active employees with `net_due > 0` but no open salary liability. They render with a distinctive `إنشاء التزام تلقائي` badge (`data-testid="pay-liability-virtual-badge"`). On click, the form POSTs to the new `/api/liabilities/salary-topup` endpoint and stores the just-created row in a local `pendingTopup` state so the `selected` getter resolves immediately (without waiting for the parent's `openLiabilities` refetch).
  - **Backend fix** (`/app/backend/liabilities_routes.py`): New `POST /api/liabilities/salary-topup` endpoint creates an ad-hoc salary liability with a UNIQUE `period_key` (`YYYY-MM-topup-<8hex>`) — bypassing the per-month `(user, kind=salary, employee, period)` unique index that prevented `generate-salaries` from creating a second row for the same month. Amount defaults to and is capped at the employee's current `net_due` from the accrual aggregator. Open advances are automatically applied via `_apply_open_advances_to_salary`.
  - **Tests**: 7 backend pytest tests in `/app/backend/tests/test_pay_liability_search_iter151.py` (all pass). End-to-end Playwright test via testing_agent_v3_fork (iteration_52.json) confirms ALL 4 verification points pass: virtual badge appears, click creates topup, selected card populates from pendingTopup, submit fires POST `/api/liabilities/{id}/pay` and form resets.

- **Iter-150 (Feb 13 2026)**: 🐛 Ad Account Sync — Paid Liability No-Recreation Fix.
  - **Bug**: When user paid off a cron-created ad-account liability, the next force=True sync (half-hour cadence) recreated a fresh liability for the full day's spend — undoing the payment.
  - **Root cause**: Pre-Iter-150 `_run_sync_for_all` used a "drop + recreate" pattern. The reverse step looked for liabilities with `status in [unpaid, partial]`. After payoff, status became `paid`, so the reverse found nothing — then the apply step created a new liability for the full daily total.
  - **Fix**: Switched to a **delta-based** approach in `/app/backend/ad_account_routes.py::_run_sync_for_all`. Each force-sync computes `delta = platform_total − sum(prev auto_cron amounts today)` and only applies the delta. Re-runs with no new platform spend are a genuine no-op (no DB writes, no liability touched).
  - **Response shape additions** (backward compatible — only ADD): `delta_applied`, `prev_total_applied`, `no_op`. The frontend's existing `debt_created` reading semantics now reflect "debt added in THIS sync" (so a no-op re-sync shows 0).
  - **Negative delta (rare correction)**: if platform reports LESS than what was already applied today, the abs(delta) is refunded to the prepaid balance and a correction ledger row is written. Liabilities are NEVER auto-reduced — user must adjust manually.
  - **Tests**: 3 regression tests in `/app/backend/tests/test_ad_account_sync_paid_no_recreate_iter150.py` covering (a) paid-off liability not recreated, (b) new spend after payoff creates a NEW liability for the delta only, (c) 5x repeated force-sync with no spend change is a pure no-op (no ledger rows, no liability writes). All 3 pass. Existing Iter-110 tests adjusted to new delta semantics — all 9 in that file still pass. Wider Ad Account regression: 49/49 affected tests pass.

- **Iter-144 (Feb 12 2026 — this session)**: 🚚 شركات الشحن — قسم مستقلّ مع unified courier ledger.
  - **Sidebar restructure**: new top-level section `🚚 شركات الشحن` housing 4 pages: حسابات الشحن الآجلة (existing), أرصدة شركات الشحن (new), تحويلات شركات الشحن (new), إعدادات شركات الشحن (new).  Old `/shipping-accounts` link removed from `إدارة التشغيل`.  COD-settlements page intentionally deferred until SMSA / iMile integration lands.
  - **New backend endpoints** (in `shipping_accounts.py`):
    - `GET /api/shipping-accounts/ledger` — per-deferred-company unified balance. Returns `{companies: [{name, cod_approved, cod_pending, shipping_cost, cod_fee, courier_to_bank, bank_to_courier, net_balance, interpretation, ...}], totals: {...}}`. Hard rule: only `cod_approved_statuses`-matched orders count for ANY money figure (delivered-only), and only `is_deferred=True` companies are listed. Immediate companies excluded entirely.
    - `GET/POST/DELETE /api/shipping-accounts/transfers` — new `courier_transfers` Mongo collection supporting two directions: `courier_to_bank` and `bank_to_courier`. POST also posts a linked `account_transactions` row when a bank account is selected (in/out) and recomputes the bank balance.
  - **New frontend pages**: `/shipping/ledger` (data-testid `shipping-ledger-page`), `/shipping/transfers`, `/shipping/settings`. The settings page exposes the new fields `cod_fee_percent` and `cod_fee_fixed_per_order` (disabled in UI for Immediate companies) plus a clean Deferred/Immediate radio toggle that maps to the existing `is_deferred` field.
  - **Financial Position** — new mini-card `kpi-shipping-ledger-net` showing net + breakdown (لنا / علينا / COD معلَّق). Existing `shipping_unpaid` and `cod_balance` KPIs untouched so the merchant can cross-check during transition (per the user's explicit "no destructive changes" rule for this iter).
  - **Net formula** (applied uniformly across backend + frontend):
    ```
    net = cod_approved_delivered
        − shipping_cost_delivered_with_vat
        − cod_fees (% + fixed/order, delivered only)
        − Σ courier_to_bank
        + Σ bank_to_courier
    ```
  - 6/6 pytest in `test_shipping_ledger_iter144.py` (endpoint shape, CRUD round-trip, direction validation, amount validation, immediate-excluded, per-row net formula).
- **Iter-143 (Feb 12 2026 — this session)**: Searchable shipping-company picker with live balance in FinancialInputHub.
  - **Problem**: When recording a payment under `دفعة شركة شحن`, the picker was a static HTML `<datalist>` listing 5 hardcoded names — the merchant couldn't tell which company they owed money to or which was already paid in full BEFORE choosing.
  - **Fix**: Replaced the datalist with a searchable dropdown identical in pattern to the employee picker:
    - Fetches `/api/shipping-accounts` on mount, sorts companies by `remaining` desc.
    - Each row shows: bold company name + colored balance badge (rose=`مستحق عليك`, emerald=`لك عنده`, slate=`مسدَّد بالكامل`) + sub-line with orders_count / total_owed / total_paid.
    - On pick, the input is filled AND a `ship-company-balance-card` appears below the picker re-stating the remaining balance prominently.
    - Empty-state hint `ship-company-empty` lets merchants register payments under brand-new (unseen) company names — free-text submission still works.
    - Post-submit, the companies list is refreshed so subsequent picks see the updated balance.
  - **Verification (testing agent Iter-143, 100% PASS)**: Empty-state + populated-state both verified — badges, balance card, free-text submission, list refresh after POST all work.  Match against `/api/shipping-accounts.remaining` exact.
- **Iter-142 (Feb 12 2026 — this session)**: Employee search dropdowns in FinancialInputHub now show CUMULATIVE balance instead of monthly salary.
  - **Problem**: When the merchant searched an employee in `سداد التزام` or `سلفة موظف`, the dropdown rows displayed only `monthly_amount` (e.g. "8000 ر.س/شهر").  That number doesn't tell the merchant the ACTUAL current obligation — they had to pick the employee first to see `net_due` in the post-pick card.
  - **Fix**:
    - Both `PayLiabilityForm` (line ~256) and `AdvanceForm` (line ~821) now fetch `/api/liabilities/salary-accrual-summary` on mount and build an `accrualMap` keyed by employee id.
    - **AdvanceForm dropdown**: each employee row now shows a colored badge `صافي مستحق: {net_due} ر.س` (rose if > 0, emerald otherwise) PLUS sub-line with monthly salary + outstanding advances.
    - **PayLiabilityForm dropdown**: employee rows now show `صافي مستحق تراكمي` label with net_due, plus a sub-line `({days_worked} يوم · متراكم {accrued})` and a `موظف نشط / موقوف` chip.  Non-employee rows (suppliers / ad-accounts) keep the legacy `g.sumRemaining` display untouched.
  - **Verification (testing agent Iter-142, 100% PASS)**: For all 4 active employees, the net_due shown in (a) advance dropdown badge, (b) pay-liability tracking line, (c) post-pick employee-balance-card matches the API exactly — three surfaces, one source of truth (`/salary-accrual-summary`).
  - **Tech debt noted by testing agent**: same endpoint is called up to 3 times per Hub session (PayLiabilityForm + AdvanceForm + EmployeeBalanceCard after pick).  Fine today (4 employees, instant response), worth deduping later via SWR/Context.
- **Iter-141 (Feb 12 2026 — this session)**: Sidebar page-visibility now syncs across every merchant device.
  - **Problem**: Hiding a sidebar page on phone A didn't propagate to phone B / desktop / tablet — the list lived in `localStorage` only (Iter-124 design).  The merchant wanted central control: hide once, hidden everywhere.
  - **Fix**:
    - Backend: `users.settings.sidebar_hidden_pages: List[str]` (same shape as `dashboard_hidden_cards`).  Added to `SettingsIn` model, GET response, and PUT validator (blank strings stripped).
    - Frontend `lib/sidebarVisibility.js`: rewritten to read/write through `/api/settings`, with `localStorage` retained ONLY as an offline cache so the sidebar can paint instantly before the API call returns.  Optimistic save with automatic rollback on failure.  Legacy `mezan.sidebar.hidden_pages` key auto-migrated to the user settings doc on first login then removed.
    - `Sidebar.jsx` now calls `refreshHiddenPagesFromServer()` once on mount so a list hidden on another device appears immediately.
  - **Live verification on preview**: PUT `sidebar_hidden_pages=['nav-tabby','nav-tamara']` → GET round-trips exactly → reset to `[]` works.  4/4 pytest in `test_sidebar_visibility_iter141.py` pass.
- **Iter-140 (Feb 12 2026 — this session)**: Backend Asia/Riyadh date helper — fixes the silent "yesterday" bug on every aggregated page during 00:00–03:00 KSA.
  - **Problem**: Server runs in UTC.  All backend `date.today()` calls returned UTC's date, which during the first 3 hours of every Saudi day (21:00–24:00 UTC of the previous day) silently shifted daily expense / financial position / salary accrual / ad-cron aggregates back one day.  The merchant saw entries logged with the wrong date and "today's spend" missing during that window.
  - **Fix**: New helper module `/app/backend/tz_utils.py` exposing `riyadh_now()`, `riyadh_today()`, `riyadh_today_iso()`.  Replaced ALL `date.today()` calls in business paths:
    - `liabilities_routes.py` × 6 (`_today_str`, `_compute_employee_accrual`, `_aggregate_salary_accrual`, `generate_salaries`, salary-status, salary days-worked).
    - `bnpl/settlements_service.py` × 2 (default `date_to` for weekly settlements).
    - `ad_account_routes.py` × 1 (`run_daily_cron` target date — used by the new half-hour sync from Iter-139).
    - `webhook_routes.py` × 2 (Meta + TikTok recent-N-days cutoffs).
  - **Live verification at UTC 23:23 (= Riyadh 02:23 AM)**: `GET /api/liabilities/salary-accrual-summary` returned `end_date=2026-06-12` (Riyadh today) — UTC was still on 2026-06-11.  Before the fix the merchant would have seen "yesterday" until 03:00 KSA.
  - 5/5 pytest in `test_riyadh_tz_iter140.py` (helper offset, ISO format, no leftover `date.today()` in business paths, public API stable).
- **Iter-139 (Feb 12 2026 — this session)**: Replaced the 23:55 daily ad-account cron with a half-hour realtime sync.
  - **Problem**: Ad-account spend was synced once per day at 23:55. The merchant wanted near-realtime updates so today's ad-balance + ad-liability reflect ongoing spend without waiting until end-of-day.
  - **Fix**:
    1. Deleted `_ad_account_daily_cron` in `server.py` entirely (no more 23:55 wake-up).
    2. New `_ad_account_halfhour_sync` background task — runs every 30 minutes (`AD_ACCOUNT_SYNC_INTERVAL_SECONDS = 30 * 60`) starting 90s after boot.
    3. Each pass calls `run_daily_cron(db)` for TODAY's date only (Asia/Riyadh).
    4. `run_daily_cron` updated to invoke `_run_sync_for_all(..., force=True)` so re-running on the same day reverses prior cron rows (Iter-110 fix B) and re-applies fresh totals — no double-counting.
    5. `cron_runs` collection tags entries with `type=ad_account_halfhour_sync` so older `ad_account_daily_sync` rows stay identifiable in diagnostics.
  - **Live verification on preview**: first pass after deploy completed in 461ms across 241 users. Logs: `iter-139: ad-account half-hour sync done — 241 users processed (today=2026-06-11)`.
  - 5/5 pytest in `test_ad_account_iter139_halfhour_sync.py` (old symbol gone, new symbol present, interval = 30 min, force=True wired, cron_runs type renamed).
- **Iter-138 (Feb 12 2026 — this session)**: Unified the existing cumulative salary system across every page that touches employee compensation.
  - **Problem**: The salary accrual engine already existed (`_compute_employee_accrual` / `_aggregate_salary_accrual` from Iter-115) and the canonical endpoint `GET /api/liabilities/salary-accrual-summary` returned per-employee + aggregate numbers, but only `FinancialPosition.jsx` was actually consuming them. The other 5 pages either showed a flat `operating_salaries_total` (monthly stipend, not accrued/net_due) or recomputed advance balances locally.
  - **Fix**: NEW reusable React component `/app/frontend/src/components/EmployeeBalanceCard.jsx` exposing two named exports:
    1. `EmployeeBalanceCard({employeeId})` — full or compact card for ONE employee (name, status badge, monthly_amount, accrued / outstanding_advance / paid / net_due).
    2. `SalaryAccrualSummaryCard({showEmployeeTable})` — 4 aggregate KPI tiles + optional per-employee table.
  - **Wired into 6 pages**:
    1. `Dashboard.jsx` (main `/`) — top-of-page section `dashboard-salary-accrual-section` (hide-able via `salary_accrual_card`).
    2. `OperationsDashboard.jsx` (`/operations-dashboard`) — bottom section `ops-salary-accrual-section` with per-employee table.
    3. `OperationalReports.jsx` (`/operational-reports`) — section `opreport-salary-accrual` shown on monthly + yearly views (intentionally hidden on `daily`).
    4. `OperatingExpenses.jsx` (`/operating-expenses` → الرواتب الشهرية) — block `oe-salary-accrual-block` above the existing CRUD table.
    5. `FinancialInputHub.jsx` → `سداد التزام` tab — `employee-balance-card` rendered when the picked liability has `employee_salary_id`.
    6. `FinancialInputHub.jsx` → `سلفة موظف` tab — `employee-balance-card` rendered above the legacy `adv-cumulative-card`.
  - **Verification (testing agent Iter-138)**: 100% PASS. For the 4 active employees on preview (عرفات / خالد / عزوز / ابو جمال) the same `accrued` / `outstanding_advance` / `paid` / `net_due` values appear in all 5 places — exact match to the API response, no discrepancies. Aggregates (accrued_total=25,966.67, net_due=25,966.67, advances=0, paid=0) identical on Dashboard, OperationsDashboard, OperationalReports, OperatingExpenses, FinancialPosition.
  - **No backend changes** — Iter-115 logic untouched.
- **Iter-137 (Feb 12 2026 — this session)**: Root-cause fix for the 12.34 SAR Tabby gap after Iter-134 redeploy.
  - **Problem**: After Iter-134 was deployed, the merchant's settlement still showed +12.34 SAR off (14,730.14 vs Tabby's actual 14,717.80) for the May 4-10 invoice.
  - **Root cause**: Two parts.
    1. `DEFAULT_FEE_RATES` in `settlements_service.py` was out of sync with `DEFAULTS` in `config_store.py` (commission_pct 5.00 vs 6.99 ; settlement_fee 5.0 vs 6.0 ; missing refundable_commission_pct).
    2. The line `rates.setdefault("refundable_commission_pct", commission_pct)` defaulted refundable rebate to FULL MDR (6.99%) for any bnpl_settings doc that predated Iter-134 — over-rebating every refund by 2.00 percentage points and inflating net_payable.
  - **Fix**:
    1. Synced `DEFAULT_FEE_RATES` with config_store.DEFAULTS (canonical Tabby: 6.99% / 4.99% / 1.00 / 6.00 / 15% / VAT-on-fee true).
    2. Fallback now uses per-provider canonical refundable_commission_pct (Tabby = 4.99%, Tamara = 7%) instead of full MDR.
    3. NEW: when `commission_mode == 'auto'` (default for all users), the engine DELIBERATELY ignores stored fee values in both `payment_methods` and `bnpl_settings` and uses the canonical defaults instead. This means any future Tabby contract changes reach every merchant on next sync without requiring them to re-save.
    4. Switching to `commission_mode = 'manual'` honours the saved values for the 6 lockable fields.
  - **Verification (live API on preview)**: GET `/api/bnpl/settlements/summary?provider=tabby` now returns `commission_pct=6.99`, `refundable_commission_pct=4.99`, `settlement_fee_per_invoice=6.0`, `fee_source=auto_canonical_defaults` — even though merchant's bnpl_settings doc still has stale 5.00 / 5.0 values from before Iter-134.
  - **Math validation**: Replaying the May 4-10 invoice (69 orders × 16,646.29 SAR, 534.72 SAR refunds) now yields net_payable 14,717.88 SAR vs Tabby's 14,717.80 → diff +0.08 SAR (within rounding tolerance).
  - Regression suite: 5 new pytest in `test_bnpl_iter137_refundable_fallback.py` + updated iter-126 tests for the new auto-mode contract. 20/20 BNPL pytest pass.
- **Iter-136 (Feb 12 2026 — this session)**: Admin purge endpoint for BNPL historical cleanup.
  - New endpoint `POST /api/bnpl/{provider}/admin/purge-before?cutoff=YYYY-MM-DD&dry_run=true`. Deletes rows STRICTLY before the Asia/Riyadh cutoff across `payment_transactions`, `payment_refunds`, `bnpl_settlements` and `unified_orders` (filtered by `source` or `payment_provider` = provider, AND user_id = caller).
  - Riyadh-local midnight is converted to UTC ISO upper bound (same Iter-130 convention).
  - Always defaults to dry_run=true; supports `dry_run=false` for real deletion.
  - Preview DB sanity check: 0 Tabby docs (all real data lives on production mezansalla.com). User must redeploy + call endpoint on production for any actual deletion.
  - 4/4 pytest in `test_bnpl_iter136_admin_purge.py` pass (dry-run counts, unknown provider 404, bad date 400/422, requires auth 401/403).
- **Iter-135 (Feb 12 2026 — this session)**: Asia/Riyadh default-dates across every input form.
  - New helper `/app/frontend/src/lib/dates.js` exposing `todaySA()` + `monthStartSA()` that add the +3h Riyadh offset before slicing the ISO date — eliminates the off-by-one-day bug between 00:00 and 03:00 KSA.
  - Replaced `new Date().toISOString().slice(0, 10)` and the legacy `monthStart` UTC slicing in 14 page components: AccountDetails, Accounts, Advances, AdAccounts (helper + 2× monthStart), BnplDiagnostics, BnplIntegrations, Dashboard, FinancialInputHub, OrdersDiagnostics, PurchaseInvoices, Receivables, SallaSourceComparison (`todayISO` + `daysAgoISO`), Settlements (modal + table), Transfers.
  - `format.js::todayISO()` had already been patched for Riyadh in a previous iter — leaving it as the back-compat wrapper.
  - Validated by testing agent on the preview URL: 7/7 hub date inputs, transfers modal, accounts add-account modal all prefill with the Riyadh date.
- **Iter-134-Auto (Feb 12 2026 — this session)**: BNPL commission_mode Auto/Manual toggle.
  - `bnpl_settings.commission_mode` ∈ {auto, manual}, default 'auto'. Persisted via `PUT /api/bnpl/settings/{provider}`; invalid values are dropped server-side.
  - `BnplIntegrations.jsx` new toggle row inside the "إعدادات الرسوم العقدية" card with `bnpl-{provider}-mode-auto` / `-mode-manual` testids. Two emerald/amber colored states + contextual hint banner.
  - On "Auto": frontend applies vendor-canonical `AUTO_PRESETS` to all 5 rate inputs (Tabby: 0.0699/1/0.15/0.0499/6 ; Tamara: 0.07/0/0.15/0.07/0) and disables them. On "Manual": inputs re-enable so the merchant can override per their specific contract.
  - Tabby preset MDR bumped from 0.06 → 0.0699 (matches the official Tabby invoice this merchant signed for).
  - 5/5 unit tests in `test_bnpl_iter134_auto_manual_mode.py` + 8/8 live API round-trip tests pass (verified by testing agent).
- **Iter-134 (Feb 11 2026 — previous session)**: Per-order commission + KSA VAT on settlement fee → MEZAN matches Tabby invoice to the cent (±0.05 SAR vs the ±13.29 SAR before).
  - Root cause of the residual 13.29 SAR gap was three things stacked: (1) aggregate commission rounding vs Tabby's per-order rounding; (2) Tabby refunds only the *refundable* slice of the commission on returns, not the full MDR; (3) Tabby's 6 SAR settlement transfer fee carries 15% KSA VAT that MEZAN never deducted.
  - Backend `settlements_service.py::compute_settlement_for_provider`: now iterates raw `payment_transactions` + `payment_refunds`, computing commission & VAT PER ROW with 2-dp rounding at every step. Added `settlement_fee_vat` line item to totals.
  - New settings on `bnpl_settings`: `refundable_commission_percent` (default 0.0499 for tabby, 0.07 for tamara) and `settlement_fee_vat_applicable` (default true).
  - `config_store.py` reads + persists the new fields; `_merchant_fee_rates` plumbs them to the settlement engine.
  - UI: new "🔬 الدقة المحاسبية المتقدمة (Iter-134)" section in `BnplIntegrations.jsx` with both editable fields; new "ض. رسوم التسوية" column in the `BnplSettlements.jsx` weekly table.
  - Simulated against the official Tabby May-4-10 Excel: 14,717.85 SAR vs actual bank deposit 14,717.80 SAR → diff 0.05 SAR (99.9997% accuracy).
  - 5/5 new pytest in `test_bnpl_iter134_per_order_commission.py` + 59/59 cumulative BNPL pytest pass without regression.
- **Iter-133b (Feb 11 2026)**: One-shot cleanup endpoint for the duplications that accumulated BEFORE Iter-133.
  - New `POST /api/ad-accounts/migration/cleanup-duplicates?dry_run=…`
  - Pass A: per (counterparty, date), keep ONLY the newest `breakdown.migration=True` ledger row; reverse impact (restore balance, shrink/delete migration liability by `uncovered`) of every older copy.
  - Pass B: per counterparty, MERGE duplicate open `source=ad_account_migration` liabilities into the newest (sum expected + paid).
  - Partially-paid liabilities: clamp `expected` to `paid` + status `paid` — no audit loss.
  - UI: new pink "🧹 تنظيف الترحيلات المُكرّرة" button in AdAccounts header (next to ترحيل المديونيات). First click → dry-run + confirm dialog. Confirm → apply.
  - 5/5 pytest in `test_ad_account_cleanup_iter133b.py` (dry-run, ledger pass, liability pass, partial-paid clamp, clean account).
- **Iter-133 (Feb 11 2026)**: Idempotent historical migration for ad-account debts.
  - Bug: re-running "ترحيل المديونيات التاريخية" on the same date range stacked duplicate ledger rows and liabilities → spend & debt doubled each retry.
  - Fix in `/app/backend/ad_account_routes.py::migration_apply`: before posting new rows, REVERSE any prior `breakdown.migration=True` ledger rows in the same range — restore the consumed `from_balance`, shrink (or delete) the open `source=ad_account_migration` liability by the prior `uncovered`, drop the old ledger rows. Same exact pattern as the existing `force=True` auto-sync reversal.
  - Liabilities that were partially paid off in the meantime are preserved: `expected_amount` is clamped to `paid_amount` and status flips to `paid` — no audit history is lost.
  - API now returns `reversed_prior_rows` per account; UI shows an amber notice + a "سُحب سابق" column in the result table.
  - Explainer card text updated to declare idempotency.
  - 2/2 new pytest in `test_ad_account_migration_iter133_idempotent.py` + 44/44 cumulative ad-account pytest still pass.
- **Iter-131 (Feb 11 2026)**: Weekly table المُحوَّل/المتبقّي show the matched bank transfer (not the cumulative 14-day window).
  - Problem: row showed `transferred_amount = 27,141.91` (cumulative window) while the actual single matched transfer was 14,717.80 → confused users into thinking there's a 12k overpayment.
  - Fix in `BnplSettlements.jsx`: when `matchByInv[invoice_no].match_status === 'matched'`, override `transferred_amount` with `matched_transfer.amount`, recompute remaining as `net_payable - matched_transfer.amount`. Totals row sums `matchTotals.matched_amount` instead of the backend's cumulative `totals.transferred_amount`.
  - Added `data-testid` attributes: `bnpl-weekly-transferred-{n}`, `bnpl-weekly-remaining-{n}`, `bnpl-weekly-transferred-total`, `bnpl-weekly-remaining-total`.
  - Verified: page renders, all test-ids present in DOM, no lint issues.
- **Iter-130 (Feb 11 2026)**: Asia/Riyadh timezone fix for settlements.
  - Reproduced production discrepancy: Tabby invoice (4-10 May) gross = 16,646.29 / 69 orders, MEZAN showed 16,232.46 / 68 orders → exact gap = 413.83 SAR.
  - Root cause: `settlements_service` filtered Mongo `created_at_provider` / `refunded_at` strings with the raw user date treated as UTC midnight, dropping orders captured in the last 3 UTC hours of the prior Saudi day.
  - Added `_local_date_window_utc(date_from, date_to)` helper (`-3h` on each side, no DST).
  - Applied to `_compute_provider_totals` (sales + refunds) and `_compute_period_items` (sales + refunds).
  - Simulation against the official Tabby Excel: 69 sales / 16,646.29 SAR / 4 refunds / 534.72 SAR — matches official invoice **exactly**.
  - 9/9 new pytest in `test_bnpl_iter130_riyadh_timezone.py` + 54/54 cumulative BNPL pytest still pass.
  - Awaiting user redeploy on `mezansalla.com`.
- **Iter-123 (Feb 2026)**: Period Start Convention.
  - Period now spans `[invoice_weekday, next_invoice_weekday - 1]` (Mon→Sun for default Tabby/Tamara).
  - New row field `issue_date` = next invoice_weekday (the day provider generates the statement file).
  - `expected_transfer_date` is computed from `issue_date`, not `period_end`.
  - 4/4 new pytest in `test_bnpl_iter123_period_start.py`.
  - 22/22 cumulative pytest passes (Iter-120 + 121 + 122 + 123).
  - Frontend: new "تاريخ الإصدار" column in BnplSettlements weekly table.
- **Iter-122 (Feb 2026)**: Strict separation + empty-list bug fix.
- **Iter-121 (Feb 2026)**: Weekday-based settlement cycle.
- **Iter-120 (Feb 2026)**: Refund-Date-Based Aggregation + period drill-down.
- **Iter-119 (Feb 2026)**: BNPL SSOT + auto-matching engine.
- **Iter-149 v3 (Feb 2026)**: Extended cutoff to shipping ledger. `shipping_accounts.shipping_ledger` now filters `unified_orders` by `cod` cutoff (`received_at >= cutoff`) + skips `is_pre_accounting=true` rows. `courier_transfers` filtered by `bank_transfer` cutoff on `transfer_date`. So pre-accounting orders no longer inflate courier COD totals or shipping-cost liabilities.
- **Iter-149 v2 (Feb 2026)**: Extended accounting cutoff to financial position + liabilities + bank balances.
- **Iter-149 (Feb 2026)**: Per-provider accounting cutoff dates.
- **Iter-148 (Feb 2026)**: Diagnostic + cleanup for duplicate ad-account topup rows.
- **Iter-147 v3 (Feb 2026)**: Settlement file totals override computed totals.
- **Iter-147 (Feb 2026)**: Tamara settlement-attribution priority. Added `provider_settlement_id` / `provider_invoice_id` / `provider_settlement_date` / `provider_payout_date` / `settlement_source` / `effective_settlement_date` on `payment_transactions`. Tamara settlement-file import now propagates the official attribution. First-stamp-wins (re-import never clobbers). Audit log `tamara_attribution_log`. Endpoints: `GET/POST /api/bnpl/tamara/attribution/{status,recompute,log}`. Daily cron `_tamara_attribution_daily_sweep` re-derives attribution. Startup one-shot migration backfills legacy rows. Settlements engine groups by `effective_settlement_date`.
- **Iter-146 (Feb 2026)**: Tamara `billing_eligible_at` settlement-cycle rule. Sales enter the weekly Tamara invoice on the week the order first reaches a billable status (تم التنفيذ / جاري التوصيل / تم التوصيل / تم التجهيز / تم الشحن — plus Tamara API equivalents fully_captured/shipped/partially_refunded). Stamp is idempotent (first-stamp wins). Backfill endpoint + status endpoint added. Refunds keep Iter-120 `refunded_at` rule.
- **Iter-145 (Feb 2026)**: BNPL Settlements UI — show transferred amount for near-miss (over/under) invoices, not only auto-matched. Totals row reflects all surfaced transfers.
- Iter-118: Search-based counterparty + cumulative balance.
- Iter-117: BNPL SSOT unification.
- Iter-116: Phase 4 weekly settlements UI.
- Iter-115: Configurable `settlement_fee_per_invoice`.
- Iter-114: Tabby MDR 5% + 1 SAR fixed fee.
- Iter-113: Refund Audit module.
- Iter-112: Hourly auto-sync.

## Outstanding User Notes
- **Tabby actual MDR for this merchant = 6.99%** (confirmed via real Tabby invoice). Default in code = 5%.
- **Tabby Payout fee = 6 SAR** per invoice. Default in code = 5 SAR.

## Pending / Roadmap

### P1
- Build "Provider Invoice Comparison Tool" — paste Tabby/Tamara invoice numbers → system shows per-field diff + suggests setting corrections.
- Apply Iter-120/121/122/123 rules to other settlement engines (Salla/Mada/Apple Pay/STC Pay/إمكان/Bank/COD).
- Iter-119 Phase 4-C: persist matches in `bnpl_settlement_matches`.
- Iter-99 Phase 3: per-counterparty balance in dropdowns.
- Iter-99 Phase 4: migrate legacy string-based supplier names → `counterparty_id`.

### P2
- Unify payment-methods commission settings UI.
- Smart Settlement Alerts.
- "الطلبات غير المتطابقة" page.

### P3
- Source priority matching rules.
- Import actual Tamara / Tabby settlement files for secondary verification.

## Critical Notes for Next Agent
- **Language**: respond in Arabic.
- **BNPL balances**: ALWAYS via `get_bnpl_provider_balance` (SSOT).
- **Refunds**: from `payment_refunds.refunded_at`, NEVER `payment_transactions.refunded_amount`.
- **Settlement creation**: driven SOLELY by `invoice_weekdays`. `transfer_weekdays` is metadata.
- **Period convention** (Iter-123): `invoice_weekday` is the START of the period. `to` is the day BEFORE the next invoice weekday. `issue_date` is the next invoice weekday after `to`.
- **Timezone** (Iter-130): All BNPL date-window filters go through `_local_date_window_utc()`. Inputs are Saudi-local YYYY-MM-DD; outputs are UTC ISO. **Never** filter `created_at_provider` / `refunded_at` with raw YYYY-MM-DD again.
- **Empty list vs absent in DB**: `[]` = user cleared. `key missing` = use defaults.
- **Cloudflare 524**: wrap long-running endpoints in `try/except` returning JSON.
- **Production access**: agent edits Preview only. User redeploys.
