# عقود التكامل وحدود الملكية — proposed, not installed

## استقلال ميزان 2 الملزم
كل التطوير المستقبلي ميزان 2 فقط. تعطيل القديم يجب ألا يعطل inventory/purchases/shipping/accounting reports. لا قراءة legacy balances/journals/payables/stock ولا fallback إذا نقصت بيانات MZ2. المشترك هو نواة P01 المعتمدة، لا خدمة قيود منافسة. لا يكفي تغيير اسم collection أو نسخ route قديم.

| مرشح إعادة الاستخدام | دليل الاستقلال من فحص الكود | حدود الاستنتاج |
|---|---|---|
| product_inventory_rules canonical_specifications/build_inventory_configuration_key | pure functions؛ لا db أو provider reads | يعاد استخدام canonicalization فقط؛ مفتاحه SKU/state/specs ليس وحده هوية محاسبية |
| inventory_receipt_service.place_inventory_receipt | قراءة/كتابة LOCATIONS فقط، scoped user_id؛ receipt_id لمنع التكرار | metadata المكان تعاد الاستفادة منها؛ occupancy تاريخي غير مقبول كقيمة/كمية افتتاحية. الدالة الحالية ليست atomically integrated مع P01 |
| mezan_suppliers_v2 directory والـIDs | make_mezan_supplier_management_router يستعمل مجموعات V2 والخدمات وGL لا suppliers/counterparties/liabilities؛ لا تعتمد الاستقلال على flag legacy_dependency وحده | GL يجب أن يقرأ أحداث MZ2 المقبولة بعد القطع فقط؛ reader الحالي لا يكفي لإثبات فصل كل journal تاريخي. wrapper الخاص بالصيانة مستبعد |
| mezan_products_v2 + resources/bindings | product_option_cost_routes يقرأ PRODUCTS/RESOURCES/BINDINGS/PRODUCT_RESOURCE_BINDINGS داخل user_id | إعادة استخدام هويات وحقائق MZ2؛ لا اختيار legacy cost fallback أو اعتبار current unit_cost تكلفة دفتر. فحص end-to-end ما زال مطلوبًا |
| stock preparation / supplier dispatch | الاحتفاظ بهويات القطعة/الطلب/الخدمة ومراحلها | stock_preparation_order_routes يعرف SUPPLIERS="suppliers"؛ فجوة legacy صريحة؛ لا إعادة استخدام route كاملًا حتى إزالة اعتماد البيانات |
| product_inventory_receipt_routes | source يقرأ PURCHASE_INVOICES="purchase_invoices" | فجوة legacy مؤكدة؛ P03 يحتاج مستندات MZ2 مستقلة ومراجع متحققة، لا يعتمد عليه كما هو |
| old supplier_pay / purchase invoice / invoice-cost helper | counterparties/suppliers/liabilities/products ظاهرة في lookup | ممنوع تشغيلها أو استدعاؤها من P03 أو fallback إليها |

النتيجة: إثبات مصدر محدود لكل primitive مرشح، **ليس إثبات استقلال النظام الجاري كله**. اختبار تعطيل القديم الإلزامي في ACCEPTANCE لم ينفذ. خدمات النواة وgeneral_ledger مشتركة لكن القراءة الجديدة scoped owner/source/event/approved cutover، لا رصيد قديم. الأصل القديم يظل archive بلا اتصال حرج.

نسخ البيانات التعريفية/الإعدادات فقط مقترح لمرة واحدة؛ إذا اعتمد لاحقًا يحفظ في MZ2: source_kind=legacy_copy, verification_status=unverified, approved_by=null, approved_at=null، مع المصدر وcopy batch؛ لا journal ولا quantity/value/payable. لا مزامنة ولا قراءة ديناميكية لاحقة. النسخ نفسه لم ينفذ.

## الهوية والحدود
- owner مشتق server-side من fresh persisted actor ثم accounting_owner_id، employee عبر created_by؛ لا tenant من payload ولا global SKU lookup.
- supplier_id = mezan_suppliers_v2.id داخل owner. لا auto bridge إلى counterparties.
- product = mezan_products_v2.mezan_product_id؛ salla_product_id alias موثق داخل المالك فقط؛ variant id وoption_id/value_id من المنتج نفسه. resource_id = mezan_cost_resources_v2.id مع kind stockable_material/service.
- inventory identity = owner + item_kind + canonical product/resource ID + variant + canonical selected options/customization + base UOM + pool. لا تقليلها إلى الاسم أو SKU أو legacy product_id. المكونات المشتركة تحتفظ بهويتها ولا تجمع قماشًا مختلفًا لأن اسمه متشابه.
- job = owner/order_number/canonical order_item_id/piece_id مع origin revision. لا تغيير stable ready IDs أو خوارزمية هوية الطلب القائمة.

## وثائق ومجموعات مقترحة فقط
أسماء logical، ليست migration: mz2_purchase_documents (invoice/credit), mz2_goods_receipts, mz2_supplier_payments, mz2_supplier_returns, mz2_inventory_events, mz2_job_cost_events, mz2_purchase_allocations. يمكن توحيد الجداول وفق رأي النواة؛ لا تنشأ الآن.
كل سجل: owner, id, source_document_id/line_id, event_type, event_id, version, payload_hash, currency=SAR, occurred_at, accounting_at timezone-aware, recorded_at, actor, evidence_refs/hashes, policy_version, txn_group_id, reversed_event_id عند الحاجة.
قيم المورد من general_ledger؛ status/payment totals derived projections لا حقول يدويًا. stock projection قابلة لإعادة البناء من الأحداث المعتمدة، ليست خدمة GL ثانية.

### منع التكرار
Invoice business unique key=(owner,supplier_id,document_kind,normalized_supplier_invoice_number). normalization versioned: Unicode NFC + trim + Arabic/Persian digits→ASCII؛ لا إزالة شرطات/أصفار لها معنى بلا قرار. اسم/مسار المرفق ليس مفتاحًا، hash الملف دليل مساعد فقط. نفس الرقم وsupplier مع محتوى مختلف = conflict/needs_review لا overwrite. رقم مفقود يمنع الترحيل حتى مرجع معتمد؛ إعادة استعمال المورد للأرقام يحتاج policy exception موثقة، لا إضافة التاريخ تلقائيًا لتجاوز الفهرس.
كل receipt/payment/return/credit/advance allocation له ID اقتصادي مستقل حتى لو جزئي من نفس الفاتورة.
Unique event=(owner,event_type,event_id)، client idempotency key مرتبط payload_hash. نفس المفتاح ونفس payload يعيد original result/group؛ مختلف=409. تغيير المفتاح لا يتجاوز invoice business uniqueness أو bank execution/evidence allocation cap. document line cumulative bounds تتفحص داخل owner transaction؛ لا read-then-write خارجها.
المرفقات owner-scoped immutable bytes + content hash، permission في read/download؛ تغيير الاسم لا يغير identity.

## استخدام نواة P01 وحدها
مرجع المصدر المجمد e9d05b2be67a5ca54eb4e1b99a3a2a6fb16cf203:
- backend/accounting_module_contract.py: accounting_owner_id, require_accounting_permission؛ existing accounting.inventory.view, accounting.drafts.create, accounting.purchases.post.
- backend/accounting_write_control.py: fresh_actor, protect_accounting_routes, write_state؛ صلاحية حديثة داخل الترحيل، لا stale browser grant.
- backend/accounting_atomic.py: atomic_owner → _owner_transaction؛ SessionDatabase يربط كل عمليات callback بجلسة واحدة، owner barrier وpause وفحص توازن وappend-only.
- backend/accounting_periods.py: assert_open_journal_periods; accounting_report_dates.py للتاريخ؛ إقفال الشهر serialized مع نفس حاجز الكتابة. لا period service أخرى.
- general_ledger + accounting_audit_log + canonical journal/event mapping؛ حسابات inventory/WIP/GRNI/return clearing/input VAT تحتاج اعتماد النواة، لا اختراع chart عبر legacy writer.
- accounting_sales_tax(_service).py سياسة ضريبة المبيعات لا مصدر ضريبة شراء. invoice supplier snapshot + eligibility/evidence + accountant policy مطلوب؛ نستخدم عقد ضريبة مشترك يعتمد من P01، لا engine موازٍ ولا افتراض استرداد كامل.

في التنفيذ القادم callback واحد يشمل event claim، caps، snapshot، quantity/value movement، cost projection، كل legs، audit، document/allocation state. غياب replica transaction = fail closed؛ لا fallback للقديم. service provider/bank HTTP ليس داخل transaction ولا يرسل سدادًا من هذا التصميم.
response unknown بعد commit → read same event outcome ثم replay آمن؛ failure قبل commit → لا جزء مرئي. outbox تشغيلي عند الحاجة ينشأ في المعاملة ويطبق مرة واحدة بعد commit، مع reconciliation؛ لا stock projection وGL مستقلان بلا recovery contract.
pause يمنع الكاتب الجديد والدفع/الصرف/replay؛ القراءة متاحة. incoming evidence يمكن عزله غير مالي وفق P01. resume owner-only ثم replay مراجَع بنفس الهويات؛ لا automatic catch-up لقديم.
posted snapshot محمي؛ مراجعات لاحقة append-only adjustment بتاريخ مفتوح مع رابط أصل، لا حذف/rebuild للتاريخ.

تنسيق أرسل إلى عامل P01 في task المحاسبه 20\\9 بتاريخ 2026-09-20 لطلب مراجعة هذه seams/account mappings/permissions. ذلك طلب مراجعة عقد لا طلب تغيير المصدر، ولا موافقة مستنتجة من الإرسال. قبول النواة موثق لاحقًا إذا وصل.

## عقد النقل مع مهمة الشحن
تأكيد مقابل من عامل الشحن 2026-09-20: موافق على الحد التالي؛ المرجع المخطط
`parallel/shipping/INTEGRATION-CONTRACTS.md` على `codex/mz2-shipping-prep-20260920`، نفس base وP01 contract SHA. هذا اتفاق تحضيري، لا تنفيذ.

cost_origin_key ثابت=(owner, transport evidence issuer, original document, original line). purpose=inbound_acquisition/outbound_customer_delivery/needs_review حصري. اسم شركة النقل لا يحدد الغرض.
- الشحن يوثق **سطر النقل فقط**: دليل/التزام/دفع، والنواة تملك القيد الوحيد. لا تملك مهمة الشحن كل تكاليف المورد.
- P03 يستهلك allocation معتمدًا إلى استلامات فعلية. إذا سجلت النواة Dr acquisition clearing / Cr transport payable، فرسملة P03 هي Dr inventory / Cr clearing فقط. لا تكرر supplier credit أو bank أو input VAT.
- mixed line: child allocations sum exactly to original net/tax/gross (integer halalas، rounding residue صريح). كل allocation claimed مرة واحدة في النواة. outbound ينتهي بمصروف توصيل؛ inbound أصل مؤهل؛ unknown review.
- debit inventory/clearing route vs expense delivery mutually exclusive لنفس الجزء؛ reject dual consumer/conflicting purpose. نفس دليل الضريبة يستخدم eligible allocation لا full VAT عند كل مستهلك.
- نقل متأخر بعد البيع يتطلب adjustment traced بين on-hand/WIP/COGS وفق السياسة المعتمدة؛ لا تغيير snapshots.
- اختبار proof: line net100+eligibleVAT15 split inbound60/outbound40 → Dr acquisition clearing60 + Dr delivery expense40 + Dr VAT15 / Cr transport payable115؛ allocation receipt Dr inventory60 / Cr clearing60. دفع115 = Dr payable115 / Cr bank115 فقط.

## القرارات والتبعيات قبل التنفيذ
| ID | القرار المطلوب | الجهة/الحالة |
|---|---|---|
| D01 | توقيت ملكية/التزام فاتورة قبل الاستلام وحساب clearing/GRNI | محاسب + P01، unresolved |
| D02 | eligibility input VAT، مستندات، nonrecoverable/advance tax وتاريخها | سياسة شراء معتمدة + P01، unresolved؛ معدلات fixture ليست إعدادًا |
| D03 | UOM ودقة القماش وتحويل الرول، نطاق weighted pool بين الفروع | التشغيل/المحاسب، unresolved؛ المتوسط المرجح نفسه محسوم |
| D04 | receipt-first provisional price وفروق لاحقة بعد الصرف/فترات مقفلة | محاسب + P01، unresolved |
| D05 | مرتجع عند اختلاف average عن الأصل، credit-before-return، فروق/رسوم الإرجاع | محاسب، unresolved؛ لا سعر خفي |
| D06 | normal/abnormal waste وunused material/finished returns/rework | تشغيل + محاسب، unresolved |
| D07 | recognition/COGS event للقطعة وcost_pending gating | P01، unresolved؛ ممنوع تغيير order costs هنا |
| D08 | service completion الكاتب الوحيد والتزام مورد V2 الموجود | P01 + تشغيل المورد، unresolved؛ منع double-post ضروري |
| D09 | فصل receipt/post/pay/return authorities داخل shared permissions | P01، unresolved؛ لا صلاحيات مبتكرة مفعلة |
| D10 | incoming freight claims + allocation basis + evidence timing | shipping + P01، boundary agreed, policy/interface implementation pending |
| D11 | physical legacy occupancy vs MZ2 new receipts وقواعد reconciliation دون استيراد | P07 + تشغيل، unresolved؛ لا افتتاحيات قبل بوابة P07 |

## ملكية الملفات
هذه المهمة تكتب ملفات جديدة في parallel/inventory فقط؛ الشحن parallel/shipping، P01 يملك النواة والعقود المشتركة. أي تغيير لاحق للـbackend أو UI أو shared permissions أو STATUS أو release يحتاج نطاقًا وتنسيقًا جديدين. الملفات المفحوصة ليست قائمة ملفات مأذون بتعديلها.
