# عقود مطلوبة من P01 والنطاق المشترك

الحالة: طلب توافق، لا واجهة منفذة ولا التزام بتعديل ملفات P01. مرجع المراجعة `e9d05b2be67a5ca54eb4e1b99a3a2a6fb16cf203` / #1106. لا يلزم عامل P01 بإيقاف إصلاحاته؛ أي تغير لاحق يراجع كـcontract delta قبل التنفيذ.

## عقد القيد الواحد

1. الخادم يجلب actor حديثًا (`fresh_actor`/`fresh_accounting_user`) ويستعمل `accounting_owner_id` وصلاحية العملية. owner ليس من payload، ولا تستعمل employee.id بدل created_by. افصل `accounting.shipping.view` عن `accounting.rules.manage` و`accounting.settlements.post`. تسجيل المندوب حدثًا تشغيليًا لا يعطيه سلطة اعتماد تسوية أو قاعدة سعر.
2. كل أثر مالي يستعمل `atomic_owner` ثم خدمة النواة `post_txn_group` على scoped DB نفسه. تضم المعاملة claims للحدث/الأصل/allocations، كل الأرجل، audit، idempotency، نتيجة التسوية وحالة processed. لا loop مستقل ولا ledger آخر ولا كتابة مباشرة من shipment webhook.
3. callback قد يعاد من Mongo: لا network/provider side effects داخله. replica set إلزامي؛ لا standalone fallback. permission، pause، الفترة، available balances، rule/draft version، owner وhash تفحص داخل المعاملة قبل commit، لا اعتماد على preview قديم.
4. economic key: owner+operation+source namespace+shipment/attempt+event kind+canonical event ID. request_id هو مفتاح نقل إضافي وليس بديلًا. duplicate بنفس economic hash يعيد group السابق؛ نفس الهوية بحقائق مختلفة يتوقف للمراجعة. provider retries وUI double-click ومصدران لنفس الحدث يخضعون لنفس القيد الاقتصادي.
5. `metadata.accounting_at` صريح timezone-aware لكل حدث؛ received_at/posted_at حقول تدقيق منفصلة. الإقفال عبر `accounting_periods.assert_open_journal_periods` داخل owner transaction؛ لا تحريك التاريخ لليوم للهروب من شهر مقفل. backdated معتبر فقط ضمن قطع/فترة/صلاحية معتمدة. التقارير تستعمل التاريخ نفسه.
6. write pause هو barrier `mz2_atomic_owners`، وليس flag محلي. أثناء pause يُسمح durable non-financial ingress فقط؛ لا fee/COD/settlement journal. resume ثم replay يحافظان على original event identity/date/snapshot ويعيدان period/permission checks. لا scan/backfill لطلبات قديمة لتعويض النقص. `accounting_ingress` الحالي لا يدعم shipping kinds: إضافة لاحقة يملكها P01 بالاتفاق، لا وصلة في هذا PR.
7. journal append-only، reversal/adjustment له هوية ودليل ويرتبط بالأصل؛ لا mutation/delete للقيد المنشور. يحفظ amount as decimal/minor units، والاتزان الدقيق دون تسامح يخفي halala.

## الحد الفاصل للاعتراف والتكلفة

تملك النواة sale/VAT/advance/refund recognition. يطلب الشحن عقدًا يعيد `recognition_event_key`, `txn_group_id`, recognized amount/currency، open collectible allocation، وtarget receivable. إما أن تسجل النواة COD receivable للطرف مرة واحدة ضمن الاعتراف، أو يعيد الشحن تصنيف الذمة الأصلية المثبتة مرة واحدة. لا كلاهما، ولا إعادة البيع/الضريبة عند التوصيل أو التسوية. فقدان أصل الاعتراف = needs_review، لا قيد مقابل revenue لتوازن المبلغ.

تكلفة الشحن/الأجرة/عمولة COD يعرّفها charge identity/version مرتبطة بالدليل والشحنة. إذا كانت مثبتة سابقًا تستهلك التسوية payable lot؛ لا تعيد expense. فرق الكشف حدث adjustment منفصل. تعتمد النواة تقسيم VAT وrecoverability، وليس calculator legacy وحده.

## عقد البنك والكشف

`accounting_courier_bank_routes.py` **ملف واجهة مملوك للعمل الجاري في P01؛ لا يُعدل هنا**. يعاد استخدام provider alias وbank ownership ومعنى current binding: البنك الحالي يطبق على غير المرحل بعد مراجعة حديثة، والمرحل يحتفظ snapshot البنك. تغير البنك بعد preview يلغي اعتماد preview حتى إعادة المراجعة. لا نضيف فترات بنك تخالف العقد الحالي.

مطلوب من P01 قبل تنفيذ الشحن:

| المطلوب | الموجود | الامتداد/الدليل المطلوب |
|---|---|---|
| independent company definitions | `_catalog` يقرأ settings/defaults | تعريف MZ2 مستقل مع provenance؛ query spy يثبت عدم قراءة legacy runtime |
| receipt للواصل الحقيقي | create_receipt whitelist + amount>0 | alias shipping بعد تفويض، owner/currency/bank/evidence claims نفسها؛ لا zero receipt |
| zero net offset | receipt_reasons يطلب bank receipt | حالة settlement_type=explicit_offset بدليل كشف واعتماد، صفر bank legs، دون اختلاق وصول؛ تُضاف للنواة المتفق عليها |
| partial settlement | ربط إيصال/كشف one-to-one | allocations محفوظة مع residuals وموانع over-consumption عبر جميع مصادر الإدخال، لا حذف unique index لمجرد تجاوز القيد |
| independent statement/bank identity | identities/source files وreceipt موجودة | whole file hash + logical statement identity + canonical line identity + bank evidence identity؛ alias request لا يسمح بالتكرار |
| shipment-level matching | مسارات قائمة تتمركز حول الطلب | canonical shipment/attempt/type مع parent order؛ ambiguity review، لا heuristic auto-link |
| historical balances | operation/date readers موجودة جزئيًا | قارئ النواة المعتمد للعملية فقط، والافتتاح المعتمد + أحداث ما بعد القطع؛ لا current_balance أو legacy aggregates |
| storage isolation | ledger_core يصل إلى general_ledger في SHA المراجع | تأكيد معماري/اختبار للعزل عن بيانات القديم؛ لا اعتبار نفس الاسم أو مشاركة ملف دليل استقلال |

## ما يمكن إعادة استخدامه دون إدخال بيانات القديم

| الكود / العقد | سبب صلاحية إعادة الاستخدام | شرط الفصل واختباره |
|---|---|---|
| normalize_shipping_company | تحويل نص نقي، لا استعلام أو أرصدة | تعطيه نسخة اسم MZ2 مستقلة؛ unknown لا يعتمد؛ لا default-config fetch |
| validate_courier_cod_fee_tiers + calculator | حساب نقي بلا IO | إدخال version MZ2 verified كامل وCOD VAT صريح؛ لا flat fallback لقيم مفقودة، ولا قراءة settings القديم |
| assignment_snapshot وdriver_earning | دالتان تعملان على payload معروف | operational driver identity مسموحة، لكن fee snapshot يأتي من قاعدة MZ2 المعتمدة؛ تعريف المندوب ليس ذمته القديمة |
| bank binding DTO/owner permission | هوية بنك ومالك/صلاحية، لا يحتاج رصيدًا قديمًا | bank definition MZ2، catalog مستقل؛ القديم `_catalog` فجوة، لا ندعي استقلاله الآن |
| atomic_owner/post_txn_group/audit/date/period/write control | نواة مشتركة لازمة؛ لا خدمة منافسة | تستخدم DB context والقراء المعتمدين لمـيزان2 فقط؛ اختبار تعطيل legacy APIs ورفض legacy datasets مع استمرار السيناريوهات |
| operational shipment evidence | shipment ID وassignment ID دليل واقعة لا رصيد | لا حساب COD من historical ledger أو stock؛ لا جلب fallback قديم إذا غاب الأصل أو إذا كانت الهوية غامضة |

لم ينفذ هذا PR إعادة استخدام تشغيلية. الإثبات الحالي **فحص مصدر** للدوال النقية وتوثيق الاستدعاءات؛ إثبات استقلال النظام الفعلي اختبار مخطط في ACCEPTANCE، ولا ندعي نجاحه.

## التنسيق مع تحضير المخزون P03

جرى تبادل حد مقترح مع مهمة INV-PREP (base وP01 المرجع نفسهما). وصل تسليمها [Draft #1107](https://github.com/AMASI-SA/AMASI-SA/pull/1107)، SHA `45d8f9499cf3218e61a6ef6898bff55adcdba57c`، ومرجع [CONTRACTS.md](https://github.com/AMASI-SA/AMASI-SA/blob/45d8f9499cf3218e61a6ef6898bff55adcdba57c/docs/operations/MZ2-FIN-CUTOVER-001/parallel/inventory/CONTRACTS.md). هذا تثبيت لمرجع تنسيق المهمة الأخرى، لا مراجعة مستقلة لكل PR المخزون. لا ملف مشترك يُكتب من عاملين.

- `cost_origin_key` ثابت scoped بالمالك والدليل الأصلي وسطر تكلفة النقل. لا يتغير بتغيير اسم الملف أو إعادة الاستيراد.
- `purpose`: `inbound_acquisition` أو `outbound_customer_delivery` أو `needs_review`. لا يحمل السطر كامل المبلغ في الغرضين.
- سطر مختلط يقسم allocations موثقة مجموعها مبلغ الأصل وVAT الخاص به، مع claim ذري يمنع الاستخدام الزائد. الغموض لا يرسمل تلقائيًا.
- الشحن يملك دليل تكلفة **النقل** والتزامها/سدادها، لا كل فواتير الموردين. P03 يستهلك تخصيص اقتناء معتمدًا إلى استلام فعلي؛ لا يعيد Cr payable أو Cr bank أو input VAT. قيد النواة الوحيد يملك تقسيم expense مقابل acquisition clearing/capitalization بعد اعتماد السياسة.
- مثال مصطنع: نقل30 + VAT4.50؛ inbound20/outbound10. Dr acquisition clearing20 + Dr delivery expense10 + Dr input VAT4.50 / Cr carrier payable34.50؛ تخصيص P03 اللاحق Dr inventory20 / Cr acquisition clearing20. لا مصروف شحن30 إضافي ولا input VAT ثانٍ. عدم أهلية الضريبة أو اختلاف السياسة يتطلب مراجعة قبل هذه المعالجة.
- لا نقل كميات/قيمة مخزون من القديم، ولا افتتاحيات. P02/P03 مقفلتان؛ هذا حد تصميم لا تشغيل.

## طلب الحسم قبل التنفيذ

المطلوب من P01: مرجع عقد نهائي للنواة والعزل، owner/date/period/pause/ingress seam، recognition handoff، evidence allocations/zero-offset type، وملكية تعديلات الملفات لاحقًا. أُرسل تنسيق محدود إلى مهمة P01، دون تعديل كودها أو مطالبتها بتبديل Preview. يبقى أي رد أو PR أحدث دليلًا إضافيًا، لا موافقة على التنفيذ.
