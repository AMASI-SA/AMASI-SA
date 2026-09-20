# خطة القبول مقابل الأدلة المنفذة
كل أرقام الأمثلة SAR ومصطنعة. tax15% في بعض الأمثلة **fixture فقط** مع فرض فاتورة مؤهلة كاملة؛ ليست سياسة Production. owner=synthetic-owner-A/B، supplier=synthetic-supplier. يبدأ كل سيناريو مستقلًا بلا أرصدة قديمة؛ bank delta فقط دون اختراع opening bank. Data in synthetic-fixtures.json.

## القيم والقيود المتوقعة
| ID | الرحلة/الحالة | كمية/قيمة/قيد متوقع |
|---|---|---|
| A01 | draft فاتورة10×20 + VAT30 | quantity0, inventory0, AP0, journal0 |
| A02 | اعتماد invoice200+30 قبل receipt؛ ملف ثان مختلف الاسم | Dr purchase clearing200 + VAT30 / Cr AP230؛ الثانية duplicate بنفس doc/group بلا قيد، حتى بمفتاح طلب جديد؛ different content conflict |
| A03 | receipt4 من10 ثم6 | الأول Q4,V80, clearing120؛ Dr inventory80 / Cr clearing80. الثاني Q10,V200, clearing0؛ Dr inventory120/Cr clearing120. الاستلام الأول partial |
| A04 | receipt7 بعد4، أو طلبان متزامنان يتجاوزان10 | 409؛ لا كمية/قيمة/GL/audit مالي جزئي، يبقى4/80 |
| A05 | ضياع response لاستلام4 ثم retry نفسه | receipt واحد وجروب واحد وQ4/V80. changed payload same key409. توقف قبل commit يعيد Q0، restart+retry يثبت مرة واحدة |
| A06 | دفع100 بعد invoice230 قبل أي receipt | Dr AP100/Cr bank100؛ AP130,Q0,V0, tax cumulative30 فقط، no new purchase. دفع130 لاحقًا AP0 |
| A07 | متوسط من receipts وصرف بينهما | receive10×20 → Q10/V200/avg20؛ issue4 → Dr WIP80/Cr materials80، Q6/V120؛ receive10×30 → Q16/V420/avg26.25. متوسط فواتير العمر25 هنا **خطأ** |
| A08 | receipt-first3×40 بتكلفة موثقة | Dr inventory120/Cr GRNI120؛ invoice120+VAT18 → Dr GRNI120+VAT18/Cr AP138؛ Q3/V120، لا receipt ثانية |
| A09 | مقدم60 ثم invoice115 ثم receipt5×20 | Dr advance60/Cr bank60؛ invoice Dr clearing100+VAT15/Cr AP115؛ apply Dr AP60/Cr advance60؛ receive Dr inventory100/Cr clearing100؛ AP55,Q5/V100 |
| A10 | return2 من receipt5×20 قبل الدفع | Dr return clearing40/Cr inventory40 → Q3/V60. credit46: Dr AP46/Cr clearing40+VAT6؛ AP69، cumulative eligibleVAT9. retry لا تغيير |
| A11 | نفس الأصل مدفوع بالكامل قبل المرتجع | return مثل A10؛ credit Dr refund receivable46/Cr clearing40+VAT6؛ refund Dr bank46/Cr refund receivable46. AP0، لا شراء أو VAT عند refund |
| A12 | نفس SKU بخيارين | blue Q2×20=40، black Q3×30=90؛ pool منفصل، لا avg26 عابر للخيارات؛ خطأ variant→product409/404 بلا writes |
| A13 | manufactured-to-order | الرحلة أدناه: materials Q6.6/V165، WIP0، FG0 بعد التسليم، COGS105، loss20، AP250، direct payable40؛ bank delta0 |
| A14 | نقل100+15 مقسم60/40 | قيد مصدر Dr clearing60 + delivery expense40 + VAT15 / Cr freight AP115؛ P03 Dr inventory60/Cr clearing60 مرة؛ دفع115 لا cost/tax جديدة |
| A15 | فاتورة بلا أهلية tax مثبتة | draft/needs_review بلا claimed input VAT؛ لا اعتماد full recovery. بعد سياسة nonrecoverable معتمدة فقط تستبعد eligible tax وتضاف تكلفة مؤهلة حسب allocation |
| A16 | تغير سعر مادة/خدمة لاحقًا | لا تغيير posted issue80 في A07 أو COGS105 في A13 ولا original tax. فرق مثبت بمستند جديد لا $set للتاريخ |
| A17 | SAR فقط | USD أو currency missing/conflicting حسب schema يرفض قبل ترحيل؛ لا FX ولا تحويل ضمني |
| A18 | فاتورة قبل الاستلام وpartial payment/receipt بترتيبات مختلفة | A02→A06→A03 أو A02→A03→A06 له نفس journal effects نهائيًا؛ state متعامد، لا دفع=استلام |
| A19 | credit without stock return / price variance after issue | policy absent=needs_review بلا تعديل. بعد اعتماد policy اختبار allocation trace للموجود/WIP/COGS وقيد adjustment منفصل ومتوازن، الأصل ثابت |

### A13 — قماش وتصنيع قطعة حسب الطلب
فرض المثال غير الضريبي: invoice/receive10m×25=250؛ القماش ملك المنشأة والتكلفة موثقة؛ service40 فاتورة مؤهلة بلا ضريبة في fixture. لا يفترض الواقع كذلك.
1. invoice Dr clearing250/Cr AP250؛ receipt Dr materials250/Cr clearing250 →10m/V250.
2. issue4m Dr WIP100/Cr materials100 →6m/V150,WIP100.
3. return unused0.6m Dr materials15/Cr WIP15 →6.6m/V165,WIP85.
4. approve abnormal waste0.8m Dr waste loss20/Cr WIP20؛ WIP65 (المتبقي المستخدم2.6m). كمية0.8 خرجت أصلًا ضمن4 فلا تخفض warehouse ثانية.
5. direct manufacturing service40 Dr WIP40/Cr direct payable40 →WIP105.
6. completion1 custom unit Dr FG105/Cr WIP105 →FG1/V105,WIP0.
7. delivery/recognition core-approved Dr COGS105/Cr FG105 →FG0,COGS105.
8. issue separate job1.6m Dr WIP40/Cr materials40 ثم documented unused return1.6m Dr materials40/Cr WIP40 لا يؤثر final. هذا خارج fixture الرئيسي؛ اختبار planned للعودة الكاملة.
Final principal fixture before optional step8: materials **6.6m/V165**، WIP0,FG0, COGS105,waste20, AP250,direct payable40. لا ready opening ولا zero COGS بسبب unlimited.
Conservation: 250+40=165+105+20. Gross issued4=2.6 used+0.6 returned+0.8 waste.

## اختبارات الحماية والتعافي (كلها مخططة)
- tenant isolation لكل invoice/receipt/payment/resource/warehouse/job/attachment/report؛ ownerB لا يرى ownerA (404 للموارد)، actor بلا حق403؛ linked employee owner resolution؛ إزالة الصلاحية بعد فتح النموذج ترفض commit.
- separate receive/post/pay privileges وفق D09، لا منح تلقائي لصاحب draft أو operator.
- closed month: الفاتورة والاستلام والدفع والمرتجع والتكلفة المتأخرة والصرف تفشل409 بلا تغيير التاريخ أو أرجل جزئية؛ سباق إقفال/ترحيل على حاجز P01. period reopen owner-only audited.
- pause423 يمنع new financial/stock-value writes وreplay؛ reads مسموحة. pause أثناء in-flight يثبت all-or-none. resume مع إعادة نفس event لا duplication.
- real Mongo replica crash before/after claim/stock update/GL/commit، duplicate concurrent request، restarted client؛ Q/value/AP/group counts والaudit كلها exact.
- reconcile inventory value = GL inventory subledger، WIP by job=GL WIP، AP statements=GL AP مع payment allocations؛ GRNI/clearing discrepancies ظاهرة لا تصفير صامت.
- frozen snapshots وhistorical as-of تقارن قبل/بعد supplier price edit، invoice correction، tax policy edit؛ adjustment event واضح بتاريخ مفتوح.
- legacy-off test: deny reads/writes/network to legacy suppliers/counterparties/liabilities/products/purchase_invoices and legacy balance/report services; seed conflicting legacy values، ثم run A01–A14 عبر MZ2. same results with legacy absent or corrupt. Allowed shared GL restricted approved MZ2 event sources; لا fallback أو dynamic copy. Missing MZ2 material = needs_review لا legacy replacement.
- attachment changed filename duplicate؛ credit note not confused with invoice (document_kind), missing invoice number blocks posting، supplier/owner same number distinct، same supplier number changed date still duplicate.
- no negative stock/value، unit conversion drift، allocation sum/tax rounding (halalas)؛ over-return and double-claim freight reject.
- receiving of actual goods with missing cost records valuation_pending visibly; final COGS blocked until resolved, no implicit zero. not infinite accounting quantity.

## طبقات التنفيذ المستقبلية — لا تختلط
| الطبقة | المطلوب | حالة هذا التسليم |
|---|---|---|
| Pure/contract | identities, decimals, journal previews, caps, examples | فحص fixture arithmetic وتشخيص helper فقط منفذ أدناه؛ ليس تنفيذ عقد P03 |
| HTTP | authenticated ASGI/live isolated endpoints، duplicate409/pause423/permission403/owner404، payload/schema/readback | NOT RUN؛ لا endpoints P03 موصولة |
| Isolated integration | dedicated throwaway Mongo replica، الحقيقي P01 atomic boundary/events/GL/counters/fault injection + legacy denylist | NOT RUN؛ لا DB اتصال |
| Frontend component | separate actions/state، permission stale، original attachment، partial receipt/payment rendering | NOT RUN |
| UI browser acceptance | run A01→partial receive/pay→complete→duplicate→supplier statement/journal/attachment وA13؛ capture source SHA/role/owner | NOT RUN؛ لم نستخدم أو نبدل Preview |
| Runtime/production | بوابات منفصلة بعد تفويض | NOT AUTHORIZED؛ P03 LOCKED |

## الأدلة المنفذة في هذه المهمة
- قراءة static للملفات في INVENTORY وP01 contract بواسطة git show؛ لا اختبار تشغيلي.
- `verify-preparation.py` يستخدم Python standard library، يتحقق بيانات JSON وتوازن كل قيد ومجاميع السيناريو، ويستخرج AST للدوال المحددة من financial_movements_routes.py فقط إلى in-memory fake products. لا import للتطبيق/.env/Mongo، ولا شبكة أو database. Probe يبين cost_avg=6.5 بعد invoices بلا receipts، وإعادة helper تضيف history مرة أخرى؛ تشخيص محدود لا اختبار production route.
- النتائج والأوامر الفعلية تسجل في VERIFICATION.md بعد التشغيل. ملفات tests الموجودة جرى فحصها فقط، ومنها test_iter250b_phase4_product_cost_update.py؛ لا نسب نتائجها إلى هذه المهمة أو P03.
