# Iter-205 — Ad Spend SSOT Production Verification Runbook

## ما يفعله السكربت

ينفذ 12 فحصاً متسلسلاً على الحساب الإعلاني الذي تختاره (أو أكثر الحسابات صرفاً تلقائياً إن لم تحدد)، ويعطي تقرير PASS/FAIL واضح. الفحوصات:

| # | الفحص |
|---|---|
| 0 | تسجيل الدخول |
| 1 | اختيار الحساب الإعلاني |
| 2 | لقطة قبل المزامنة (snapshot) |
| 3 | تشغيل `POST /api/ad-accounts/sync-all` |
| 4 | ظهور قيد `expense.advertising` (DEBIT) |
| 5 | استخدام أحد جانبي CREDIT (`ad_account.balance` أو `debt`) |
| 6 | توازن مجموع المدين/الدائن لكل `txn_type=ad_account_spend` |
| 7 | تشغيل المزامنة مرة ثانية |
| 8 | عدم تكرار `expense.advertising` (إجمالي ثابت) |
| 9 | عدم تكرار عدد صفوف الـ ledger (الـ idempotency شغّال) |
| 10 | تطابق `ad_account.balance` (Ledger) مع صفحة الأصول |
| 11 | تطابق `ad_account.debt` (Ledger) مع صفحة الأصول |
| 12 | تطابق تقرير المصروفات الإعلانية مع الـ ledger |

## الأمان

- **مرة واحدة فقط** يكتب السكربت إلى قاعدة البيانات: عبر `POST /api/ad-accounts/sync-all` (وهو ما طلبتَه أنت كجزء من الاختبار).
- باقي الـ 11 فحص قراءة محضة (GET).
- لا يحذف، لا يعدل قيوداً قائمة، لا يلامس البنوك.
- لو لم ترد تشغيل المزامنة أصلاً مرر `DRY_RUN=1` وسيتجاهل القسمين 3 و 7.

## التشغيل

```bash
# 1) Production — بعد أن تنشر Iter-205 إلى mezansalla.com
API="https://mezansalla.com" \
EMAIL="<your prod email>" \
PASSWORD="<your prod password>" \
python3 /app/scripts/verify_ad_spend_ssot_iter205.py

# 2) (اختياري) استهدف حساباً إعلانياً بعينه
AD_ACCOUNT_ID="<cp_id>" \
API="https://mezansalla.com" EMAIL="..." PASSWORD="..." \
python3 /app/scripts/verify_ad_spend_ssot_iter205.py

# 3) Dry-run بدون كتابة (للتأكد فقط من القراءات)
DRY_RUN=1 API="https://mezansalla.com" EMAIL="..." PASSWORD="..." \
python3 /app/scripts/verify_ad_spend_ssot_iter205.py
```

## تفسير النتائج

- **🎉 جميع الفحوصات نجحت** → Spend SSOT مغلق بأمان على Production.
- **❌ فشل القسم 4 (لا يوجد expense.advertising)** → التغييرات لم تُنشر بعد، أو لا يوجد صرف في آخر 14 يوماً.
- **❌ فشل القسم 6 (مجموع غير متوازن)** → خلل جسيم، أوقف الاستخدام واتصل بنا.
- **❌ فشل القسم 8-9 (التكرار)** → الـ idempotency معطّل — قم بحذف Index `gl_user_idem` وأعد إنشاءه.
- **❌ فشل القسم 10-11 (انحراف Ledger ↔ Assets)**:
  - **متوقع** لو الحساب عليه صرف قديم قبل Iter-205 (الفجوة لا تكبر بعد Iter-205).
  - **مشكلة** لو الانحراف يكبر بعد كل تشغيل للمزامنة.
- **❌ فشل القسم 12** → تقرير المصروفات الإعلانية لا يقرأ من الـ ledger بشكل صحيح.

## مثال نتيجة ناجحة

```
━━━ الخلاصة ━━━
  المجموع: 12   نجح: 12   فشل: 0

🎉 جميع الفحوصات نجحت — Spend SSOT يعمل على هذه البيئة.
```

## شارك معي

بعد تشغيل السكربت على Production، أرسل لي:
1. سطر الخلاصة فقط (نجح/فشل).
2. أي قسم فشل + التفاصيل تحته.
3. لو نجح الكل، ننتقل للمرحلة التالية (`PUT /topup/{id}` + `PUT /opening`).
