# MZ2-FIN-CUTOVER-001-INV-PREP — التسليم الأول
Date: 2026-09-20. **Preparation only; P01 IN_PROGRESS/open; P02/P03 LOCKED.**

## العزل والمراجع المجمدة
- Repository: AMASI-SA/AMASI-SA.
- Branch: `codex/mz2-inventory-prep-20260920`; separate worktree, no edits to the dirty original checkout.
- `base_sha = ebf92e6606a810a1e3f0a0e2026984a5482f71bc` from freshly fetched `origin/hotfix/prod-snap-meta-final`. Branch-name collision check returned no local/remote match.
- P01 contract reference: `e9d05b2be67a5ca54eb4e1b99a3a2a6fb16cf203`, [Draft #1106](https://github.com/AMASI-SA/AMASI-SA/pull/1106). Read directly with git show; not merged/cherry-picked into this branch. This is a candidate contract, not released authority or a promise it will remain unchanged.
- Latest relevant Issue #1006 report read: [5750134296](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5750134296). It reports 22 isolated Mongo checks in P01, not P03 acceptance. Earlier [Preview gaps](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5749976153) explain the period/report/advance dependencies. Later changes require a contract diff review, not an automatic rebase or Preview change.
- Read: repository AGENTS.md, operation master document, phase README/STATUS, PHASE-03-INVENTORY-PURCHASES.md and P01 candidate contracts. This task's explicit preparation scope permits these new documents while phase implementation remains locked.
- All source references in INVENTORY.md use base_sha unless marked P01. Issue comments are historical observations, not new authorization.

## المخرجات
1. [INVENTORY.md](INVENTORY.md): الموجود، الملفات والدوال والمجموعات والتدفقات، والفجوات وإعادة الاستخدام.
2. [DESIGN.md](DESIGN.md): فصل أحداث الفاتورة والاستلام والسداد، المتوسط المرجح، وتصنيع محدود حسب الطلب.
3. [CONTRACTS.md](CONTRACTS.md): الهويات، الذرية، النواة، الشحن، الحدود والقرارات المطلوبة.
4. [ACCEPTANCE.md](ACCEPTANCE.md): كميات وقيم وقيود متوقعة؛ المخطط مقابل المنفذ وHTTP مقابل UI والتكامل.
5. [synthetic-fixtures.json](synthetic-fixtures.json) و[verify-preparation.py](verify-preparation.py): بيانات مصطنعة وفحص حسابي/تشخيصي محلي بلا تطبيق أو شبكة أو قاعدة بيانات.

## الخلاصة
لا نحتاج إعادة بناء المورد والمنتج والخيارات والمواقع وسير تجهيز القطع من الصفر. الموجود لا يكفي لدفتر مخزون محاسبي: تحديث تكلفة الفاتورة ليس تقييمًا على الاستلام، والاستلام التشغيلي لا يثبت قيمة محاسبية، ومسار مورد V2 يثبت تكلفة في حساب مصروف ويجمع الاستلام والفاتورة. يلزم مستند مشتريات مستقل وأحداث استلام/دفع/مرتجع منفصلة، وربطها بالنواة الوحيدة مع سجل كمية وقيمة قابل للمطابقة.

المتوسط المرجح هدف P03 المعتمد. المواد والبضائع المتجانسة تقيم من الاستلامات؛ القطعة المصنّعة حسب الطلب تجمع المواد المصروفة والتكاليف المباشرة الفعلية على الطلب/القطعة. بيانات المكونات الحالية تصلح لتعريفات واقتراح كميات، ولا تثبت الاستهلاك أو WIP.

## حدود الكتابة والتسليم
كل الإضافات داخل `docs/operations/MZ2-FIN-CUTOVER-001/parallel/inventory/` فقط. لا تعديل Runtime أو المنتجات أو تكلفة الطلبات أو الصلاحيات المشتركة أو STATUS.json أو release. لا تغيير بيانات أو فهارس أو إعدادات أو أرصدة؛ JSON أمثلة محلية مصطنعة فقط. لا اتصال بقاعدة بيانات ولا Preview ولا ترحيل ولا دمج ولا نشر ولا lease.

Draft PR للمراجعة، وليس إعلان اكتمال P03. PR head SHA يسجل في وصف PR وتسليم المحادثة لتجنب SHA ذاتي داخل commit. الخطوة التالية: مراجعة الجرد والعقود والقرارات مع النواة والشحن، ثم تفويض فتح البوابة قبل أي توصيل تشغيلي. افتتاحيات المورد والكمية والقيمة تبقى P07.
