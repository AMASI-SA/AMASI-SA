# خطة القبول وأدلة الفحص

**لم تنفذ اختبارات قبول P02 أو واجهتها.** لا خادم محلي/Preview أو قاعدة بيانات شغلتها هذه المهمة. حالات JSON هي expected outputs مصطنعة للمراجعة، وليست service implementation ولا قيودًا مرحّلة.

## البيانات والقيود المتوقعة

`synthetic-cases.json` يصرح لكل حالة بـinitial_balances وexpected_journals (أرجل debit/credit مع هوية وتاريخ) وexpected_balances. الإشارة مدينة موجبة ودائنة سالبة. initial balances شروط اختبار لحسابات محددة، **ليست خطة إنشاء أرصدة افتتاحية**. كل حساب غير مذكور يظل دون تغيير. `core:AR` ذمة معترف بها مسبقًا من النواة، وليست clearing مخترعة لتجاوز غياب الاعتراف.

الملخص التالي يشرح oracle؛ التفاصيل الرقمية الكاملة في JSON. R=لنا عند الطرف، P=له علينا، B=تغير البنك. جميع SAR، وVAT مجرد فرضية مصطنعة مؤهلة لا اعتماد إنتاج.

| الحالات | المدخل/الفعل | الرصيد والقيد المتوقع |
|---|---|---|
| S01 | prepaid، مندوب بأجرة20 | Dr expense20 / Cr P20؛ R0، P20، B0 |
| S02 | مندوبان يجمعان100 و200، أجرة20 و25 | إعادة تصنيف AR لكل منهما، ثم استحقاق منفصل؛ D1 R100/P20/net80 وD2 R200/P25/net175 |
| S03 | تكرار حدث نجاح سبق ترحيله | صفر قيود جديدة؛ R100/P20 ثابتان؛ يرجع group السابق |
| S04 | price20 ثم25 بعد تأكيد شحنة قديمة | القديمة20 والجديدة25؛ expense45/P45؛ لا rewrite للسعر القديم |
| S05–S09 | حدود1000،1000.01،3000،3000.01،6000 | رسوم20/30/90/155/305 وضريبتها3/4.50/13.50/23.25/45.75؛ Dr fee+VAT / Cr P بالقيمة الإجمالية |
| S10–S11 | gap0.99 أو6000.01؛ حد مشترك inclusive/inclusive | review أو reject للقاعدة؛ صفر قيود وصفر تغيير، وليس fee=0 مع قبول |
| S12 | تحويل جزئي400 من R1000/P20 | Dr B400 / Cr R400؛ R600/P20/net580 |
| S13 | تحويل كامل1000 ثم دفع أجرة20 منفصلًا | Dr B1000 / Cr R1000؛ Dr P20 / Cr B20؛ R0/P0/B980، بلا expense إضافي |
| S14 | net980 مقابل COD1000 وأجرة مثبتة20 | Dr B980+P20 / Cr R1000؛ R0/P0؛ expense يبقى20 |
| S15 | كشف مقاصة19.55 وصافي بنك0 | Dr P19.55 / Cr R19.55؛ كلاهما0؛ **لا bank leg ولا receipt** |
| S16 | R115/P19.55 بتكلفة15+2.25 وعمولة2+0.30 مثبتة | Dr B95.45+P19.55 / Cr R115؛ expense17/VAT2.55 لا يتكرران |
| S17 | كشف مكرر بملف/طلب جديد | لا قيود؛ R115/P19.55 كما كانا؛ duplicate أو identity conflict |
| S18 | إثبات بنك مستخدم سابقًا/عبر قناة أخرى | لا قيود ولا استهلاك جديد؛ R115/P19.55 ثابتان |
| S19–S20 | عبور مالك أو سحب صلاحية بعد preview | رفض، دون أرجل أو claims مالية؛ R115/P19.55 ثابتان |
| S21 | تاريخ القيد في شهر مقفل | رفض409؛ لا إعادة تأريخ؛ R115/P19.55 ثابتان |
| S22 | pause ثم وصول حدث | durable pending evidence فقط؛ لا COD/fees/bank journals؛ الأرصدة ثابتة |
| S23 | نفس event identity بمبلغ مختلف | conflict للمراجعة؛ الأرصدة ثابتة |
| S24 | abort ثم retry ثم lost-response retry | مجموعتا custody/fee فقط مرة واحدة؛ R115/P17.25/expense15/VAT2.25؛ AR0؛ لا revenue/VAT بيع |
| S25 | شحنتان لطلب115، تحصيل70 و45 | R115 لا230؛ أجرتان20+20=P40؛ AR0؛ each shipment مستقل |
| S26 | return ثم reship prepaid | أجرة إضافية20 فقط؛ R115 السابق يبقى حتى دليل رد النقد؛ P20→40؛ لا إعادة البيع |
| S27 | سعر legacy_copy/unverified | blocked، لا قيود ولا أرصدة |
| S28 | القديم معطل وMZ2 verified متاح | استحقاق20 يعمل من MZ2؛ R0/P20؛ صفر legacy queries |
| S29 | القديم معطل وسعر MZ2 ناقص | needs_review، لا fallback ولا قيود |
| S30 | سطر نقل30+VAT4.50 موزع inbound20/outbound10 | P34.50 مرة، input VAT4.50 مرة، expense10، inventory20 بعد إطفاء acquisition clearing20؛ لا تكرار مع P03 |
| S31 | مقاصة بين D1 وD2 أو over-allocation | رفض بلا قيود؛ D1 R100 وD2 P20 باقيان |
| S32 | استهلاك خدمة17.25 سبق دفعها | Dr expense15+VAT2.25 / Cr prepayment17.25؛ لا P جديد ولا بنك جديد، الضريبة غير مثبتة سابقًا حسب فرضية الحالة |
| S33 | فاتورة تعدل base15 إلى16 | قيد فرق Dr expense1+VAT0.15 / Cr P1.15؛ النهائي expense16/VAT2.40/P18.40، snapshot الأصل محفوظ |

## مستويات التحقق بعد تفويض التنفيذ

1. **اختبارات عقد/وحدة:** حدود الشرائح وdecimal rounding، unverified rules، تاريخ price version، event hash conflict، supplier/driver identity، absence of duplicate sales/VAT. لا تسمى قبول واجهة.
2. **Mongo replica integration حقيقي معزول:** مالكان، موظفان بصلاحيات مختلفة، شركتان ومندوبان، نفس رقم الطلب في مالكين، بنك من مالك آخر. إثبات group+legs+claim+audit+allocation atomic. حقن abort بين الأرجل، crash قبل/بعد commit، duplicate requests متزامنة، double statement وbank receipt من قناتين. قارئ مستقل يجمع posted legs ويطابق كل oracle.
3. **الإقفال والإيقاف:** close/reopen بإذن مالك ودليل/revision؛ إقفال يتسابق مع posting؛ تاريخ boundary آخر يوم بالرياض مع UTC السابق؛ pause يتسابق مع delivery/settlement؛ pending event يبقى بعد restart؛ resume/replay لا يبدل التاريخ أو السعر، وإذا أصبحت الفترة مقفلة يظل pending/review. لا bypass بسبب stale permissions أو unknown result.
4. **استقلال القديم:** fixture MZ2 كاملة ومزود legacy يرمي خطأ لكل query؛ ضع في القديم أرصدة وأسعارًا وكميات متعمدة مختلفة ثم امنع الوصول بالكامل. تتبع الاستعلامات مثبتًا عدم لمس legacy settings/ledger/stock/AP/COD. S28 يعمل ويعرض نفس التقرير؛ S29 يفشل للمراجعة فقط. لا تخدع الاختبار باستثناء query إلى مجموعة مشتركة تضم التاريخ القديم: النواة تثبت storage/scope separation مع sentinel legacy rows لا تظهر.
5. **قبول واجهة لاحق:** محاسب يدخل من المسار المعتمد لمـيزان2، يختار driver قائمًا، يعتمد قاعدة مستقلة، يسند شحنتين حول تغير السعر، يوصل COD وprepaid، يفتح لنا/له/الصافي والقيد، يجرب gross/net/partial/zero settlement والملف الأصلي والمرجعين، ثم يعيد الإرسال ويجرب forbidden actor والفترة المقفلة/pause. توثق screenshots وHTTP وruntime SHA وقراءة DB المتطابقة، لا screenshot متخيل أو rendering design.
6. **قراءة أثر الفترة:** قبل التوصيل، بعد accrual، بعد partial ثم final، وعند as_of قبل/بعد المقاصة؛ الإجماليات تبقى محفوظة والتكاليف لا تتكرر. بعد reversal يظهر أثره في الفترة المعتمدة، لا حذف الأصل.
7. **إعدادات ودليل:** inclusive17.25 مقابل exclusive17.25، separate VAT commission/service، عدم أهلية input VAT، company prepaid/deferred، snapshot bank change، receipt missing/ambiguous/negative transfer، return/reship والمبالغ موزعة على shipment legs. الحالات ذات الدليل الناقص توقف ولا تولد قيمة افتراضية.

لا تجرى هذه الخطوات على النسخة المشتركة بمجرد انتهاء التحضير؛ يلزم الاتفاق على العقود والتفويض ثم بيئة قبول مستقلة محفوظة.

## ما تم فحصه فعليًا في هذا التحضير

- قراءة الملفات والعقود عند SHA المثبت؛ ربط صفحات الشحن بـbackend/data paths، وتحديد اعتماد settings وlegacy aggregates ومخاطر الإيراد/المصروف المتكرر.
- فحص الفروع/worktrees ومراجع GitHub قبل إنشاء فرع مستقل، وتثبيت base/P01 دون ضم مرشحات الإصدار.
- شُغّل `python -B docs/operations/MZ2-FIN-CUTOVER-001/parallel/shipping/verify-prep.py` باستخدام Python المرفق في Codex: exit0؛ **33 حالة تصميم و30 قيدًا متوازنًا متوقعًا**، مطابقة الأرصدة والحدود العشرية ونطاق الإضافات. هذا فحص حسابات fixtures وحدود الملفات فقط؛ لا يختبر التطبيق.
- شُغّل `git diff --check` ثم `git diff --cached --check` بعد staging: exit0. مقارنة additions-only مع base أظهرت سبعة ملفات جديدة داخل النطاق فقط، ولا ملفات إصدار/P01 ضمن الفرق.
- لا ادعاء جديد عن CI تطبيق أو اختبارات frontend/Mongo أو قبول UI. أرقام P01 المذكورة في README من تقرير عامله وليست نتائج هذه المهمة.
