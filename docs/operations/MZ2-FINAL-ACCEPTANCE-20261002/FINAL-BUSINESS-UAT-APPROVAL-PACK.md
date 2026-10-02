# Final Business UAT preparation pack

**حزمة اعتماد تجاري للمراجعة والتعبئة فقط — لا تفويض تنفيذ قائم.**

اعتمد المستخدم مصفوفة التحضير السابقة، ولم يعتمد بذلك أرقامًا أو أسماء أو مصادر
غير مقدمة، ولم يفوض أي حركة مالية. كل قيمة غير متوفرة أدناه تحمل حرفيًا
`PENDING_OWNER_INPUT`. رموز النماذج تنظيمية لهذه الوثيقة وليست هويات مالية.

| المرجع المثبت | القيمة |
|---|---|
| Repository | AMASI-SA/AMASI-SA |
| PR1240 | OPEN / Draft |
| Integration HEAD | `88cc9131027fd6783a46e2e00fc6aaec4788b8fa` |
| Integration TREE | `57483f44efa381ca872262f5a8bc61d2a87cae93` |
| Production / rollback reference | `83363097d48e034dc7140a60c290efc684e1ffde` |
| حالة C3/C4/C5 | تنفيذ الكود مكتمل وفق قرار المستخدم؛ C4 Acceptance فقط وC5 Setup فقط؛ لا إغلاق Business UAT ولا إعادة تنفيذ |
| Setup Acceptance | 16/16 PASS فقط |
| Smoke B | PASS في Acceptance فقط |
| Full Business UAT | NOT PASS |
| Release Readiness | NO |
| Production financial writes بواسطة هذه المهمة | 0 |
| Write-control | UNCHANGED |
| Opening / Activation / inventory initialization / live reconciliation | NO |
| Production mutation / backfill / release lease / Merge / Deploy | NO |
| Code / guards / writers / C3 / Track F changes | NONE |

## طريقة تعبئة الحزمة

1. تُراجع وتُعبأ خارج Production. لا ترفع هذه النماذج إلى API مالي ولا تنفذها.
2. يكرر نموذج السطر لكل صنف/موقع/طرف/رصيد/مستند فعلي؛ غياب السطر ليس صفرًا.
3. الصفر أو عدم الانطباق قرار صريح بدليل وصاحب اعتماد؛ لا يُستنتج من خانة فارغة.
4. تُربط المصادر الأصلية بهويتها ونسختها وبصمتها؛ لا اعتماد لرصيد Legacy كمرجع مالي.
5. توقيع المصادر أو Stage15 ليس إذن Opening أو تهيئة مخزون أو Activation.
   التفويض التنفيذي اللاحق يجب أن يكون مستقلًا ومحددًا، ولا يصدر تلقائيًا من تعبئة الحزمة.
6. تبقى سجلات النتائج الفعلية غير منفذة. لا تُملأ من fixtures أو نتائج API الاصطناعية.
7. تحفظ المستندات والأسماء والبيانات الحساسة في مستودع الأدلة المعتمد، ولا تُنشر
   في GitHub/Issue. النسخة الموجودة في فرع التوثيق قالب فارغ؛ لا ترسل أسرارًا أو بيانات بنكية كاملة.

### غلاف الاعتماد التجاري

| الحقل | القيمة المطلوب تعبئتها |
|---|---|
| المنشأة / نطاق الأعمال | PENDING_OWNER_INPUT |
| owner_id الموجود | PENDING_OWNER_INPUT |
| مرجع الحزمة التجارية ونسختها | PENDING_OWNER_INPUT |
| مستودع/مجلد الأدلة المعتمد | PENDING_OWNER_INPUT |
| اسم مالك الاعتماد وهويته | PENDING_OWNER_INPUT |
| اسم منسق تجميع الحزمة | PENDING_OWNER_INPUT |
| المحاسب/المراجع المسؤول وهويته | PENDING_OWNER_INPUT |
| قرار اعتماد الحزمة التجارية | PENDING_OWNER_INPUT |
| تاريخ/وقت القرار ومرجع التوقيع | PENDING_OWNER_INPUT |
| نطاق القرار والاستثناءات | PENDING_OWNER_INPUT |

## 1. نموذج Cutover date/time

| الحقل | القيمة |
|---|---|
| التاريخ المحلي YYYY-MM-DD | PENDING_OWNER_INPUT |
| الساعة المحلية HH:MM:SS | PENDING_OWNER_INPUT |
| المنطقة الزمنية في العقد الحالي | Asia/Riyadh |
| الإزاحة | +03:00 |
| cutover_at الكامل timezone-aware | PENDING_OWNER_INPUT |
| UTC المقابل للتحقق | PENDING_OWNER_INPUT |
| مرجع جلسة القطع الموجودة، إن وجدت | PENDING_OWNER_INPUT |
| قاعدة فصل الأحداث قبل/بعد القطع | PENDING_OWNER_INPUT |
| ورقة توقيت القطع الموقعة | PENDING_OWNER_INPUT |
| نسخة الورقة وSHA256 | PENDING_OWNER_INPUT |
| قائمة كشوف المصادر عند اللحظة نفسها | PENDING_OWNER_INPUT |
| مرجع جسر الحركات لأي كشف/عد من وقت مختلف | PENDING_OWNER_INPUT |
| صاحب الاعتماد وهويته | PENDING_OWNER_INPUT |
| قرار الاعتماد | PENDING_OWNER_INPUT |
| تاريخ/وقت الاعتماد ومرجع التوقيع | PENDING_OWNER_INPUT |

قائمة تحقق قبل التوقيع:

- [ ] كل رصيد يصف لحظة القطع نفسها أو يصل إليها بجسر حركات موثق.
- [ ] المصدر الأصلي لكل بنك/صندوق/مزود/شحن/مخزون/طرف/التزام موجود ومطابق للمالك.
- [ ] إدخال الرياض وUTC يصفان اللحظة نفسها؛ لا تاريخ افتراضي من الاختبارات.
- [ ] يعاد عرض التوقيت والمصادر للمراجع قبل قفل Stage15؛ لا تغيير صامت بعد الاعتماد.

## 2. نموذج Physical inventory

### غلاف ورقة الجرد

| الحقل | القيمة |
|---|---|
| مرجع قائمة الأصناف/ورقة الجرد الأصلية | PENDING_OWNER_INPUT |
| نسخة الورقة وبصمتها SHA256 | PENDING_OWNER_INPUT |
| تاريخ/وقت العد ومنطقته الزمنية | PENDING_OWNER_INPUT |
| مرجع Cutover المعتمد | PENDING_OWNER_INPUT |
| نطاق المستودعات والمواقع المشمولة | PENDING_OWNER_INPUT |
| عدد الأصناف/الخيارات/المكونات والمواقع | PENDING_OWNER_INPUT |
| مسؤول العد: الاسم والهوية والتوقيع | PENDING_OWNER_INPUT |
| مراجع المحاسبة: الاسم والهوية والتوقيع | PENDING_OWNER_INPUT |
| مالك اعتماد حقائق الجرد: الاسم والهوية | PENDING_OWNER_INPUT |
| قرار اعتماد حقائق الجرد ووقته وتوقيعه | PENDING_OWNER_INPUT |
| قائمة الفروق والاستثناءات غير المحسومة | PENDING_OWNER_INPUT |

### سجل الأصناف — يكرر لكل هوية/خيار

| الحقل | القيمة |
|---|---|
| مرجع السطر في ورقة الجرد | PENDING_OWNER_INPUT |
| اسم الصنف ووصفه | PENDING_OWNER_INPUT |
| النوع PRODUCT أو STOCK_COMPONENT طبقًا للمصدر | PENDING_OWNER_INPUT |
| الهوية القائمة: product_id + variant_id، أو resource_id + category_id | PENDING_OWNER_INPUT |
| الوحدة | PENDING_OWNER_INPUT |
| الكمية المعدودة فعليًا | PENDING_OWNER_INPUT |
| كمية المصدر المقارن عند التوقيت نفسه | PENDING_OWNER_INPUT |
| الفرق = الفعلي ناقص المصدر المقارن | PENDING_OWNER_INPUT |
| الكمية المعتمدة بعد مراجعة الفرق | PENDING_OWNER_INPUT |
| تكلفة الوحدة المعتمدة وعملتها | PENDING_OWNER_INPUT |
| مصدر التكلفة وتاريخه ومرجعه | PENDING_OWNER_INPUT |
| قيمة السطر المحسوبة بالسياسة القائمة | PENDING_OWNER_INPUT |
| inventory_account_id الموجود | PENDING_OWNER_INPUT |
| تفسير الفرق ودليل إعادة العد/المراجعة | PENDING_OWNER_INPUT |
| قرار الفرق ومراجعه ووقت القرار | PENDING_OWNER_INPUT |

### توزيع المواقع — يكرر لكل تخصيص من سطر الصنف

| الحقل | القيمة |
|---|---|
| مرجع سطر الصنف | PENDING_OWNER_INPUT |
| warehouse_id / location_id الموجودان | PENDING_OWNER_INPUT |
| باركود الموقع المتحقق منه | PENDING_OWNER_INPUT |
| الكمية في الموقع | PENDING_OWNER_INPUT |
| سعة الموقع وحالته/غرضه الدائم | PENDING_OWNER_INPUT |
| حالة التجهيز والمواصفات الموجودة | PENDING_OWNER_INPUT |
| مراجع التتبع/الدفعات/الأدلة السابقة إن وجدت | PENDING_OWNER_INPUT |
| المراجع وتاريخ/وقت الفحص | PENDING_OWNER_INPUT |

قائمة تحقق واعتماد:

- [ ] العد الفعلي موثق وموقع؛ البصمة تثبت سلامة الملف ولا تثبت صحة العد وحدها.
- [ ] لكل خيار صف مستقل؛ مجموع كميات المواقع يساوي كمية الهوية؛ لا مطابقة بالاسم أو SKU وحده.
- [ ] لا موقع موجب أو كمية قائمة مفقودة؛ الفروق موثقة ولا تسوية تلقائية لها.
- [ ] الكمية × تكلفة الوحدة تطابق قيمة السطر، وإجمالي السطور يطابق تقييم كل حساب.
- [ ] توجد مصادر تكلفة صريحة؛ لا استنتاج من آخر شراء أو قيمة Legacy.
- [ ] جسر العد إلى لحظة القطع موجود إذا اختلف التوقيت.
- [ ] الاعتماد هنا لحقائق الجرد فقط؛ اعتماد G47 الذي يكتب الكميات مؤجل إلى Stage16-C.

## 3. نموذج Opening balances

### سجل اكتمال المصادر — لا مبالغ مفترضة

الانطباق والرصيد والمرجع في هذا الجدول لم يعتمدها المالك بعد. يُكرر نموذج تفصيل
الرصيد أدناه لكل حساب/طرف؛ لا يُستخدم صافي إجمالي لإخفاء ذمم متقابلة.

| مجموعة الأرصدة المطلوبة | الفصل الواجب حفظه ضمن العقد القائم | الانطباق/قرار الصفر | مرجع سجل الأرصدة/المصدر | صاحب الاعتماد |
|---|---|---|---|---|
| البنوك | كل هوية حساب وعملة؛ overdraft منفصل | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الصناديق | كل صندوق وعده الفعلي؛ لا رصيد نقدي سالب مفترض | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| مزودو الدفع | ذمة كل مزود والبنك المرتبط والتسويات المعلقة | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| شركات الشحن/COD | COD receivable منفصل عن الأجرة/الرسوم payable | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| المندوبون | ذمة COD لكل مندوب منفصلة عن أجرته | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| POS عند انطباقه | ذمة موثقة بهوية other_receivable؛ ليست Bank ولا Driver COD | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الموردون | payable منفصل عن supplier_advance | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| العملاء/الحقوق | customer_receivable أو هوية حق موثقة ضمن التصنيف القائم | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الموظفون | salary_payable وadvance وcustody مستقلة | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| المخزون | inventory_asset لكل حساب مطابق للجرد | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الإعلانات | ad_prepaid_wallet وad_payable منفصلان | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| المدفوع مقدمًا | prepaid_expense مع الفترة والمبلغ المتبقي المثبت | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الالتزامات المستحقة | accrued_expense أو other_payable موثق وفق العقد | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الأرصدة الضريبية | sales_vat_payable وinput_vat منفصلان | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| حقوق أخرى | other_receivable بهوية ودليل مطابقين | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| حقوق الملكية/فرق المعاينة | مستندات المصدر وشرح فرق الافتتاح الذي ينتجه العقد القائم؛ لا حساب جديد | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| أي رصيد آخر مطلوب | يثبت عقده/تصنيفه الموجود أولًا؛ لا تصنيف بديل بالتخمين | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |

أي دفعة عميل/تأمين/وديعة أو التزام آخر لا يثبت له تصنيف افتتاحي مناسب في العقد
الموجود يبقى فجوة واضحة؛ لا يحوّل تلقائيًا إلى customer_receivable أو other_payable.
إدراجه للمراجعة ليس إثبات دعم محاسبي جديد.

### نموذج تفصيل كل رصيد

| الحقل | القيمة |
|---|---|
| المجموعة ومرجع سطر الكشف | PENDING_OWNER_INPUT |
| اسم الحساب/الطرف والهوية القائمة | PENDING_OWNER_INPUT |
| owner_id ونطاق المنشأة | PENDING_OWNER_INPUT |
| category / entity_type / entity_id / sub_account المعتمدة | PENDING_OWNER_INPUT |
| طبيعة الرصيد: لنا/علينا/صفر صريح | PENDING_OWNER_INPUT |
| المبلغ والعملة | PENDING_OWNER_INPUT |
| مصدر FX وتاريخه وبصمته إن لزم | PENDING_OWNER_INPUT |
| المقابل بالريال حسب العقد القائم | PENDING_OWNER_INPUT |
| تاريخ/وقت الرصيد | PENDING_OWNER_INPUT |
| Cutover المعتمد وجسر الحركات إليه | PENDING_OWNER_INPUT |
| الأصل الداعم، المرجع، النسخة وSHA256 | PENDING_OWNER_INPUT |
| الرصيد المقترح للافتتاح وفرق المطابقة المفسر | PENDING_OWNER_INPUT |
| مراجع المحاسبة وهويته | PENDING_OWNER_INPUT |
| صاحب الاعتماد، القرار، الوقت والتوقيع | PENDING_OWNER_INPUT |

إجمالي المدين: `PENDING_OWNER_INPUT`؛ إجمالي الدائن: `PENDING_OWNER_INPUT`؛
فرق المعاينة: `PENDING_OWNER_INPUT`؛ مصدر/شرح الفرق واعتماده: `PENDING_OWNER_INPUT`.

- [ ] جميع الهويات الفعلية مغطاة، وأدلة الصفر/عدم الانطباق صريحة.
- [ ] كل أصل من مصدر مستقل مقبول ومربوط بالمالك والقطع نفسه؛ لا Legacy financial source.
- [ ] COD والأجرة، سلف المورد ومستحقاته، أرصدة الموظف، ومحفظة الإعلان ومستحقاته منفصلة.
- [ ] لا صف غير موثق أو فرق غير مفسر أو هوية مفترضة جاهز للترحيل.

## 4. Supplier / Employee / Payment-provider / Advertising / Shipping evidence

### سجل الاعتماد المختصر — يكرر لكل طرف ومستند

| المجال | المصدر الأصلي | الرصيد التفصيلي/مرجعه | تاريخ القطع | المرجع والنسخة/البصمة | صاحب الاعتماد وقرارُه |
|---|---|---|---|---|---|
| Supplier | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Employee | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Payment-provider | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Advertising | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Shipping / Driver | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |

أصول الإثبات المطلوبة لكل مجال:

- Supplier: الفواتير المفتوحة وأرقامها وتواريخها، المدفوع والمتبقي، السلف، كشف المورد/مطابقته، وهوية C1/C2 القائمة.
- Employee: العقود، الاستحقاق المكتسب غير المدفوع حتى القطع، المدفوعات، السلف والعهد، وهوية كل موظف.
- Payment-provider: كشف رسمي لكل مزود منطبق، المعاملات والتسويات والاستردادات/الرسوم المعلقة، البنك canonical ودليل المطابقة.
- Advertising: كشف المنصة والفواتير والتمويل/السداد السابق المثبت، هوية Track E، رصيد المحفظة والمستحقات منفصلان؛ لا تفويض إنفاق جديد.
- Shipping / Driver: كشوف الشحنات/التوصيلات والتحصيلات وفواتير الأجرة والرسوم، دليل التسليم المالي المعتمد، أدلة C3 والتوريد القائمة؛ لا تعديل للسجل المختوم. POS يحتاج دليله ومراجعته وهويته القائمة ولا يحتسب بنكًا.

لكل أصل تسجل هذه البطاقة: الجهة المصدرة، owner/party ID، الفترة، وقت الرصيد،
نوع الحركة، رقم المستند، `source_file_id` إن وجد، SHA256، رابط السطر في نموذج3،
المراجع/وقته، قرار القبول/صاحبه/وقته. قيم جميع هذه الحقول غير المقدمة:
`PENDING_OWNER_INPUT`. الأصل الناقص لا يُستبدل بإقرار عام بأن الإجمالي صحيح.

## 5. Stage 15 final review

هذه ورقة توقيع على snapshot محدد، وليست طلبًا لتنفيذ مراجعة أو كتابة عبر Production.

| الحقل | القيمة |
|---|---|
| مرجع snapshot وملفه الأصلي | PENDING_OWNER_INPUT |
| owner_id / session_id / draft_id الموجود إن وجد | PENDING_OWNER_INPUT |
| version للجلسة/المسودة | PENDING_OWNER_INPUT |
| preview_id / preview_hash | PENDING_OWNER_INPUT |
| بصمة snapshot وبصمات ملفات المصادر | PENDING_OWNER_INPUT |
| بصمات الهويات/الحسابات والكتالوج حسب العقد | PENDING_OWNER_INPUT |
| مرجع نسخة الجرد وتقييم الحسابات | PENDING_OWNER_INPUT |
| مجموع المدين / مجموع الدائن / الفرق | PENDING_OWNER_INPUT |
| قائمة coverage: كل هوية وصفر وعدم انطباق | PENDING_OWNER_INPUT |
| الاستثناءات وقراراتها وأدلتها | PENDING_OWNER_INPUT |
| reviewer: الاسم والهوية ودليل صلاحية المراجعة | PENDING_OWNER_INPUT |
| قرار review: قبول / رفض / إرجاء | PENDING_OWNER_INPUT |
| approval التجاري: المرجع والمالك والوقت والتوقيع | PENDING_OWNER_INPUT |
| approval_hash الصادر من المسار القائم إن وجد | PENDING_OWNER_INPUT |

- [ ] كل رقم في المعاينة مرتبط بأصل مقبول؛ كل هوية مطابقة لنطاق المالك.
- [ ] الكشوف والجرد تغطي وقت القطع، والفروق معللة ومعتمدة دون تغيير اقتصادي.
- [ ] المدين يساوي الدائن حسب المعاينة القائمة؛ لا تعديل رقم لإجبار التوازن.
- [ ] لا مصدر/هوية/قيمة تغيرت بعد snapshot؛ التغيير يحتاج إعادة مراجعة ولا يعيد استخدام توقيع قديم.
- [ ] صلاحية review مستقلة عن post؛ التوقيع هنا لا يغير write-control ولا production_verified.
- [ ] لم يُنسخ snapshot أو hash من جلسة الاختبار بوصفه اعتماد الأعمال الحقيقي.

## 6. Stage 16 — خمس بوابات مستقلة

الحالة التنفيذية لكل B/C/D/E: **NOT_EXECUTED**. نموذج A لم يُفوض بعد.
الحالة العامة: **PRODUCTION AUTHORIZATION DEPENDENCY**.

### A. Approval to execute Opening — نموذج تفويض مستقل مؤجل

| الحقل | القيمة |
|---|---|
| مرجع قرار المالك الصريح المنفصل | PENDING_OWNER_INPUT |
| صاحب التفويض وهويته ووقت/مرجع التوقيع | PENDING_OWNER_INPUT |
| البيئة والمالك ونطاق العملية المسموح | PENDING_OWNER_INPUT |
| source/deployment identity المعتمدة للتنفيذ لاحقًا | PENDING_OWNER_INPUT |
| مرجع Stage15 وsnapshot/version/preview_hash/approval_hash | PENDING_OWNER_INPUT |
| Cutover الذي يشمله التفويض | PENDING_OWNER_INPUT |
| المنفذ المسمى وصلاحية post القائمة | PENDING_OWNER_INPUT |
| نافذة التنفيذ وحدود الإذن وشروط التوقف | PENDING_OWNER_INPUT |
| مرجع بوابة التنفيذ العامة المراجعة | PENDING_OWNER_INPUT |
| قرار Opening execution authorization | PENDING_OWNER_INPUT |
| السماح بالتنفيذ الآن | NO |

اعتماد الحزمة التجارية لا يملأ هذا التفويض. وحتى بعد تفويض مستقل لاحق، لا
تُستدعى محركات داخلية لتجاوز409. المسار العام الحالي محجوب عمدًا؛ البيانات
والصلاحيات وحدها لا تفتحانه. لا تعديل للبوابة في هذه المهمة.

### B. Actual Opening execution — سجل دليل لاحق، ليس أمر تنفيذ

| الحقل | القيمة |
|---|---|
| مرجع التفويض A وتحقق شروطه | PENDING_OWNER_INPUT |
| دليل هوية Runtime والتحقق من النسخة المنشورة | PENDING_OWNER_INPUT |
| دليل انتقال الكاتب القائم المصرح إلى V2 قبل post | PENDING_OWNER_INPUT |
| دليل الفترة والصلاحيات وحواجز الكتابة السارية | PENDING_OWNER_INPUT |
| request/idempotency ID والإصدار الفعلي | PENDING_OWNER_INPUT |
| المنفذ ووقت الطلب ونتيجته الفعلية | PENDING_OWNER_INPUT |
| txn_group_id والختم والتوازن وأطراف القيد | PENDING_OWNER_INPUT |
| دليل عدم ازدواج الافتتاح وقراءة MZ2 | PENDING_OWNER_INPUT |
| دليل zero-only القائم بدل مجموعة غير صفرية إن انطبق حقيقةً | PENDING_OWNER_INPUT |
| نتيجة تحقق القيد و12/12 وsafe_active | PENDING_OWNER_INPUT |
| المراجع وتوقيع نتيجة التنفيذ | PENDING_OWNER_INPUT |
| التنفيذ الحالي | NOT_EXECUTED |

### C. Inventory initialization — تفويض ودليل منفصلان

| الحقل | القيمة |
|---|---|
| قرار المالك المستقل الذي يسمح بتهيئة المخزون | PENDING_OWNER_INPUT |
| مالك التنفيذ وهويته ودليل accounting.opening_balances.approve | PENDING_OWNER_INPUT |
| نسخة الجرد المعتمدة ومرجعها وSHA256 | PENDING_OWNER_INPUT |
| opening_txn_group_id المتحقق ومرجع القطع المطابق | PENDING_OWNER_INPUT |
| import_id / source_file_id من المسار القائم | PENDING_OWNER_INPUT |
| evidence_sha256 / baseline_sha256 | PENDING_OWNER_INPUT |
| دليل مطابقة قيمة كل حساب/موقع وغياب نشاط يمنع التهيئة | PENDING_OWNER_INPUT |
| وقت التنفيذ/الفاعل ونتيجة approved الفعلية | PENDING_OWNER_INPUT |
| receipt_ids وسجلات الكميات والتكلفة والمطابقة اللاحقة | PENDING_OWNER_INPUT |
| دليل بقاء القيد الافتتاحي دون تغيير وعدم مضاعفة الكميات | PENDING_OWNER_INPUT |
| مراجع النتيجة ووقت/مرجع توقيعه | PENDING_OWNER_INPUT |
| التنفيذ الحالي | NOT_EXECUTED |

اعتماد حقائق الجرد في نموذج2 لا يساوي هذا الإذن. G47 يكتب كمية/تكلفة/إيصالات
حتى دون قيد جديد؛ لذلك يبقى ممنوعًا الآن. شرطه مالك الحساب مع الصلاحية، لا
محاسب يحمل الصلاحية وحدها. لا يُنفذ بعد نشاط يجعل التهيئة أثرًا رجعيًا غير مسموح.

### D. Activation — تفويض التشغيل منفصل عن انتقال الكاتب

| الحقل | القيمة |
|---|---|
| قرار Activation authorization الصريح ومالكه ووقته | PENDING_OWNER_INPUT |
| البيئة والهوية المنشورة المتحقق منها | PENDING_OWNER_INPUT |
| نطاق المسارات التي يشملها التفعيل | PENDING_OWNER_INPUT |
| المنفذ والصلاحيات والنافذة وشروط التوقف | PENDING_OWNER_INPUT |
| دليل اكتمال B وC عند لزومها وP07/12of12/safe_active | PENDING_OWNER_INPUT |
| مرجع بوابة التفعيل المسموح بها بعد مراجعتها | PENDING_OWNER_INPUT |
| سجل التفعيل الفعلي: الحالة/الإصدار/الفاعل/الوقت | PENDING_OWNER_INPUT |
| صاحب قبول النتيجة ومرجع توقيعه | PENDING_OWNER_INPUT |
| التنفيذ الحالي | NOT_EXECUTED |

انتقال الكاتب `v2_active` شرط سابق لـOpening post وليس هو تفعيل التشغيل P08.
الحجر العام لهذا الانتقال409 قائم أيضًا. لا يُغيّر transition أو write-control
بسبب توقيع الحزمة، ولا يتضمن تفويض Release وحده إذن Activation.

### E. Operational verification P08 — خطة ودليل قبول لاحق

| الحقل | القيمة |
|---|---|
| نطاق وسيناريوهات P08 المعتمدة أو عدم الانطباق الموثق | PENDING_OWNER_INPUT |
| صاحب قبول Business UAT النهائي وهويته | PENDING_OWNER_INPUT |
| مرجع تفويض السيناريوهات المالية/المطابقة الحية | PENDING_OWNER_INPUT |
| بداية نافذة الاستقرار ونهايتها والمنطقة الزمنية | PENDING_OWNER_INPUT |
| المدة وحجم العينة/عدد الأحداث المتفق عليه | PENDING_OWNER_INPUT |
| حدود الاستثناءات وشروط النجاح/التوقف | PENDING_OWNER_INPUT |
| دليل النسخة المنشورة والقيد الافتتاحي ونطاق التفعيل | PENDING_OWNER_INPUT |
| سجل السيناريوهات والنتائج والأحداث والقيود | PENDING_OWNER_INPUT |
| نتائج مطابقة البنك/المزودين/COD/المخزون/الموردين/الموظفين | PENDING_OWNER_INPUT |
| دليل SSOT والصلاحيات وعدم الازدواج والأحداث اليتيمة | PENDING_OWNER_INPUT |
| الاستثناءات المفتوحة/المغلقة وأصحابها وأدلتها | PENDING_OWNER_INPUT |
| قرار الإغلاق النهائي ومالكه ووقته ومرجع توقيعه | PENDING_OWNER_INPUT |
| التنفيذ الحالي | NOT_EXECUTED |

لكل سيناريو قائم منطبق يكرر سجل: المصدر وهوية الحدث والمالك والفاعل، المبلغ
والأطراف المتوقعة مستقلًا، مفتاح idempotency، نتيجة فعلية، مرجع القيد/المستند،
دليل المطابقة، قرار المراجع ووقته. قيمه غير المتوفرة كلها `PENDING_OWNER_INPUT`.
تغطي القائمة المعتمدة المسارات القائمة المطلوبة من الموردين والموظفين والمزودين
والشحن/المندوب/POS والإعلانات والعملاء والحركات اليومية دون اختراع حدث أو عقد.
لا تستبدل نتائج regression سجل التنفيذ الحي، ولا تصبح Acceptance Smoke دليل Production.

## Production dependencies وRemaining blockers

| الاعتماد/المانع | التصنيف | الدليل أو القرار الناقص | الحالة |
|---|---|---|---|
| أسماء المسؤولين، تاريخ القطع، الجرد والأرصدة والمصادر | BUSINESS APPROVAL | النماذج1–4 وتوقيعاتها | PENDING_OWNER_INPUT |
| snapshot/version/fingerprints ومراجعة الأعمال الفعلية | BUSINESS APPROVAL | نموذج5 محدد النسخة وموقع | PENDING_OWNER_INPUT |
| البوابة العامة الإيجابية للـOpening وانتقال الكاتب | SEPARATE GATE CONTRACT DECISION | عقد/ربط مراجَع بتفويض مستقل؛ لا تجاوز409 ولا Writer جديد | PENDING_OWNER_INPUT |
| نشر النسخة والتحقق من هوية Runtime وإثبات Production المطلوب | PRODUCTION AUTHORIZATION DEPENDENCY | تفويض Release منفصل وأدلة التنفيذ والتحقق الفعلية | PENDING_OWNER_INPUT |
| انتقال الكاتب القائم المصرح قبل post | PRODUCTION AUTHORIZATION DEPENDENCY | مرجع القرار والبوابة والصلاحية والإصدار وعزل Legacy | PENDING_OWNER_INPUT |
| تنفيذ الافتتاح | PRODUCTION AUTHORIZATION DEPENDENCY | A ثم سجل B متحقق، دون ازدواج | NOT_EXECUTED |
| تهيئة المخزون | PRODUCTION AUTHORIZATION DEPENDENCY | تفويض C، افتتاح متحقق، جرد وبصمات متطابقة | NOT_EXECUTED |
| تفعيل التشغيل | PRODUCTION AUTHORIZATION DEPENDENCY | تفويض D وسجل نتيجته | NOT_EXECUTED |
| المطابقة التشغيلية ونافذة الاستقرار | PRODUCTION AUTHORIZATION DEPENDENCY | تفويض E، سجلات السيناريوهات، قرار الإغلاق | NOT_EXECUTED |

لا بند أعلاه يمنح إذن تغيير409/423/write-control/production_verified، أو تعديل
writers/C3/Track F. ترتيب التبعيات لا يجيز تنفيذ أي خطوة. Full Business UAT
يبقى NOT PASS وRelease Readiness NO حتى إثبات الشروط الفعلية وفق العقد المعتمد.

مراجع الحزمة: [المصفوفة التجارية التي اعتمدها المستخدم](BUSINESS-UAT-PREPARATION.md)،
[إثبات Acceptance](gate409/MATRIX.md)، [حدود409 والبوابة العامة](gate409/RESULT.md).
إعداد هذه النسخة اقتصر على التوثيق؛ لا اختبارات أو خدمات أو API مالي أو وصول
لتطبيق/قاعدة بيانات Production. **NO CODE CHANGES؛ Production financial writes=0.**

## OWNER APPROVAL REQUIRED

| القرار الذي يحتاج موافقتك الصريحة | القيمة/النطاق الذي ستقره | قرارك ومرجع/وقت الاعتماد |
|---|---|---|
| Cutover date/time | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Physical inventory source | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Opening balances source | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Inventory approval: اعتماد حقائق الجرد ونسخته والفروق | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| أسماء مسؤول العد ومراجع المحاسبة ومالك الاعتماد والمنفذين لاحقًا | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Stage15: اعتماد snapshot/version/fingerprints المحددة | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| عقد بوابة التنفيذ العامة المحجوبة، بتفويض مستقل دون تجاوز الحواجز | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Release/Production verification: نطاق الإذن المنفصل للنشر والتحقق لاحقًا | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| الانتقال القائم للكاتب إلى V2 قبل post: تفويض مستقل ومحدد | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Opening execution authorization | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Inventory initialization authorization: منفصل عن اعتماد الجرد | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| Activation authorization | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| P08: نطاق السيناريوهات وإذن التشغيل/المطابقة الحية وصاحب قبول النتائج | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
| P08 stabilization window | PENDING_OWNER_INPUT | PENDING_OWNER_INPUT |
