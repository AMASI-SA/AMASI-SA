/**
 * Iter-290h.2 — Qoyod Terminology / Display Layer (Arabic UI)
 *
 * SINGLE source of truth for translating Qoyod / pipeline / webhook
 * technical identifiers into operator-facing Arabic labels + tooltip
 * explanations.
 *
 * Strict separation of concerns
 * ─────────────────────────────
 *   • Backend stage names, API field names, and database identifiers
 *     remain UNCHANGED. This file ONLY remaps for display.
 *   • Every consumer of a stage/event/reason/code must route through
 *     `labelFor(...)` / `descriptionFor(...)` / the <Term> component.
 *   • If you add a new state in the backend, add its Arabic label
 *     here too — never inline strings in pages.
 */

// ──────────────────────────────────────────────────────────────────
// Generic vocabulary — used in copy throughout the Qoyod surfaces.
// Backend identifiers are the KEYS; never displayed raw to users.
// ──────────────────────────────────────────────────────────────────
export const TERMINOLOGY = {
  // Deployment + environment
  Deploy:          { label: "نشر التحديث",
                     description: "رفع آخر التعديلات إلى النسخة المنشورة (mezansalla.com)." },
  Production:      { label: "بيئة الإنتاج",
                     description: "النسخة الحقيقية التي تؤثر على بيانات قيود وسلة." },
  Preview:         { label: "بيئة المعاينة",
                     description: "نسخة للتجربة الآمنة قبل النشر — لا تؤثر على البيانات الحقيقية." },

  // Documents in Qoyod
  Invoice:         { label: "فاتورة",
                     description: "مستند بيع في قيود يحدد المبلغ المطلوب من العميل." },
  InvoicePayment:  { label: "سداد الفاتورة",
                     description: "عملية تربط مبلغ الدفع بالفاتورة في قيود حتى تصبح مدفوعة." },
  Receipt:         { label: "سند قبض",
                     description: "إيصال استلام مبلغ في قيود — قد يكون مرتبطاً بفاتورة أو غير مربوط." },
  UnallocatedReceipt: {
    label: "سند قبض غير مربوط",
    description: "سند موجود في قيود لكنه غير مرتبط بأي فاتورة — يظهر في قيود بحالة \"غير مستعمل\"." },
  Paid:            { label: "مدفوعة",
                     description: "الفاتورة استلمت السداد الكامل، الرصيد المتبقي = 0." },
  Balance:         { label: "الرصيد المتبقي",
                     description: "المبلغ غير المدفوع بعد من الفاتورة." },

  // Pipeline & observability
  Trace:           { label: "سجل التتبع",
                     description: "معرّف فريد يربط كل خطوات معالجة الطلب من سلة إلى قيود." },
  Stage:           { label: "المرحلة",
                     description: "الخطوة الحالية للطلب في خط معالجة قيود." },
  Webhook:         { label: "حدث وارد من Make",
                     description: "إشعار يصل تلقائياً من Make.com عند حصول حدث في سلة (طلب جديد، تحديث حالة، إلخ)." },
  RequestBody:     { label: "جسم الطلب المرسل",
                     description: "البيانات الفعلية التي أرسلها ميزان إلى قيود." },
  QoyodResponse:   { label: "رد قيود",
                     description: "الرد الذي وصل من API قيود — يساعد على تشخيص سبب النجاح أو الفشل." },
  ReadOnly:        { label: "قراءة فقط",
                     description: "هذه الصفحة لا تنفذ أي تعديل على قيود — للعرض والمراجعة فقط." },
  AutoAllocatePreview: {
    label: "معاينة ربط تلقائي بدون تنفيذ",
    description: "محاكاة ما سيحدث لو تم ربط السندات تلقائياً — لا تغيير فعلي على قيود." },

  // Webhook outcomes
  Accepted:        { label: "مقبول",
                     description: "الحدث وصل وتم تطبيعه وحفظه بنجاح." },
  Skipped:         { label: "متجاهل",
                     description: "تم تجاهل الحدث لأنه لا يستوفي شروط الإرسال إلى قيود." },
  ParseFailed:     { label: "فشل تحليل البيانات",
                     description: "وصل الحدث لكن البيانات داخله غير صالحة — راجع جسم الطلب." },
  Duplicate:       { label: "حدث مكرر تم تجاهله",
                     description: "وصل نفس الحدث سابقاً، وتم منعه لحماية الفواتير من التكرار." },
  Idempotency:     { label: "منع التكرار",
                     description: "آلية تضمن أن نفس الحدث لا يُعالج مرتين — تحمي قيود من فواتير مكررة." },
};

// ──────────────────────────────────────────────────────────────────
// Stage labels — keys are the EXACT pipeline_stage values used in
// the backend. Never edit a key without coordinating with the
// state machine (`integrations/qoyod/state_machine.py`).
// ──────────────────────────────────────────────────────────────────
export const STAGE_LABELS = {
  NEW:                          { label: "جديد", description: "تم استلام الحدث للتو." },
  RECEIVED:                     { label: "تم الاستلام", description: "تم حفظ البيانات الخام والـ headers." },
  VALIDATED:                    { label: "تم التحقق", description: "اجتاز فحوصات التوقيع والبنية." },
  NORMALIZED:                   { label: "تم التطبيع", description: "تم تحويل البيانات إلى صيغة موحدة." },
  RULES_APPLIED:                { label: "تم تطبيق القواعد", description: "تم تحديد ما إذا كان الطلب مؤهلاً للإرسال إلى قيود." },
  CUSTOMER_RESOLVED:            { label: "تم تحديد العميل", description: "تم العثور على العميل في قيود أو إنشاؤه." },
  PRODUCT_RESOLVED:             { label: "تم تحديد المنتجات", description: "تم العثور على كل المنتجات في قيود أو إنشاؤها." },
  INVOICE_CREATED:              { label: "تم إنشاء الفاتورة", description: "الفاتورة موجودة الآن في قيود." },
  INVOICE_PAYMENT_CREATED:      { label: "تم تسجيل سداد الفاتورة", description: "تم ربط مبلغ السداد بالفاتورة، الرصيد = 0." },
  RECEIPT_CREATED:              { label: "تم إنشاء سند قبض (قديم)", description: "سند قبض قديم من قبل اعتماد Invoice Payment — قد يكون غير مربوط." },
  COMPLETED:                    { label: "اكتمل بنجاح", description: "الطلب وصل إلى قيود بفاتورة مدفوعة بالكامل." },
  SKIPPED:                      { label: "متجاهل", description: "قواعد العمل قررت عدم إرسال هذا الطلب إلى قيود." },
  RETRYING:                     { label: "إعادة محاولة", description: "النظام يحاول معالجة الطلب من جديد." },
  PARTIAL_FAILURE:              { label: "نجاح جزئي", description: "الفاتورة أُنشئت لكن خطوة لاحقة فشلت — يحتاج مراجعة." },
  NEEDS_ENRICHMENT:             { label: "بحاجة لإثراء البيانات", description: "البيانات ناقصة — النظام يحاول جلبها من سلة." },
  // Failure stages
  FAILED_VALIDATION:            { label: "فشل التحقق", description: "البيانات الواردة غير صالحة — راجع جسم الطلب." },
  FAILED_NORMALIZATION:         { label: "فشل التطبيع", description: "تعذّر تحويل البيانات إلى الصيغة الموحدة." },
  FAILED_ENRICHMENT:            { label: "فشل إثراء البيانات", description: "تعذّر جلب البيانات الناقصة من سلة." },
  FAILED_CUSTOMER:              { label: "فشل تحديد العميل", description: "تعذّر العثور على العميل في قيود أو إنشاؤه." },
  FAILED_PRODUCT:               { label: "فشل تحديد المنتجات", description: "تعذّر العثور على منتج أو إنشاؤه في قيود." },
  FAILED_INVOICE:               { label: "فشل إنشاء الفاتورة", description: "تعذّر إنشاء الفاتورة في قيود — راجع رد قيود." },
  FAILED_RECEIPT:               { label: "فشل سند القبض (قديم)", description: "الفاتورة أُنشئت لكن سند القبض فشل — تدفق قديم." },
  PAYMENT_LINK_FAILED:          { label: "فشل ربط السداد بالفاتورة", description: "تم إنشاء الفاتورة في قيود، لكن لم يتم تسجيل السداد عليها. راجع رد قيود." },
  PAYMENT_METHOD_MAPPING_MISSING: { label: "طريقة الدفع غير مُهيّأة", description: "لم يتم ربط طريقة الدفع المستخدمة بحساب قيود في الإعدادات." },
  DEAD_LETTER:                  { label: "تجاوز الحد الأقصى للمحاولات", description: "تم استنفاد كل المحاولات — يحتاج تدخل يدوي." },
};

// ──────────────────────────────────────────────────────────────────
// Skip / error reason labels — extra granularity beyond stage names.
// Used in webhook activity rows and error banners.
// ──────────────────────────────────────────────────────────────────
export const REASON_LABELS = {
  duplicate_idempotency_key:    { label: "حدث مكرر تم تجاهله", description: "وصل نفس الحدث سابقاً، وتم منعه لحماية الفواتير من التكرار." },
  already_sent:                 { label: "أُرسل مسبقاً", description: "تم إرسال هذا الطلب إلى قيود في وقت سابق." },
  not_in_trigger_statuses:      { label: "حالة الطلب غير مؤهلة", description: "حالة الطلب في سلة ليست ضمن الحالات التي تُحوَّل إلى فواتير." },
  cancelled_order:              { label: "طلب ملغى", description: "الطلب ملغى في سلة — لا يُرسل إلى قيود." },
  payment_method_mapping_missing: { label: "طريقة الدفع غير مُهيّأة في الإعدادات", description: "افتح إعدادات قيود وحدد حساب قيود المناسب لكل طريقة دفع." },
  product_payload_invalid_id_shape: { label: "بيانات منتج غير صالحة", description: "أحد إعدادات المنتج (الضريبة/الفئة/الوحدة) يحوي قيمة غير صالحة — راجع الإعدادات." },
  qoyod_api_key_missing:        { label: "مفتاح API غير مُهيّأ", description: "لم يتم حفظ مفتاح API قيود في إعدادات هذا الحساب." },
  qoyod_unauthorized:           { label: "مفتاح API غير صالح أو منتهي", description: "قيود رفض مفتاح API — تأكد من صلاحيته في الإعدادات." },
  qoyod_validation_error:       { label: "قيود رفض البيانات", description: "قيود وجد خللاً في البيانات المرسلة — راجع رد قيود لمعرفة الحقل." },
  qoyod_server_error:           { label: "خطأ مؤقت من قيود", description: "قيود لم يستجب بشكل صحيح — قد ينجح إعادة المحاولة بعد قليل." },
  skipped_pending_payment:      { label: "بانتظار اكتمال الدفع", description: "حالة الطلب انتقالية (waiting) — سينتظر النظام تأكيد السداد قبل الإرسال." },
};

// ──────────────────────────────────────────────────────────────────
// Match-reason labels (Iter-290h.1) — chips on the unallocated
// receipts table.
// ──────────────────────────────────────────────────────────────────
export const MATCH_REASON_LABELS = {
  reference: { label: "رقم المرجع", description: "السند والفاتورة يحملان نفس رقم المرجع/الطلب." },
  amount:    { label: "المبلغ",     description: "مبلغا السند والفاتورة متطابقان." },
  customer:  { label: "العميل",     description: "السند والفاتورة لنفس العميل." },
  date:      { label: "التاريخ",    description: "تاريخا السند والفاتورة متقاربان." },
};

// ──────────────────────────────────────────────────────────────────
// Confidence labels
// ──────────────────────────────────────────────────────────────────
export const CONFIDENCE_LABELS = {
  high:   { label: "ثقة مرتفعة", description: "اقتراح موثوق — مطابقة قوية في رقم المرجع." },
  medium: { label: "ثقة متوسطة", description: "اقتراح محتمل — مطابقة في المبلغ والعميل/التاريخ." },
  low:    { label: "ثقة منخفضة", description: "اقتراح ضعيف — مطابقة جزئية فقط." },
  none:   { label: "بدون اقتراح", description: "لم يُعثر على فاتورة مطابقة — راجع يدوياً." },
};

// ──────────────────────────────────────────────────────────────────
// Public helpers
// ──────────────────────────────────────────────────────────────────
const _TABLES = {
  stage:    STAGE_LABELS,
  reason:   REASON_LABELS,
  match:    MATCH_REASON_LABELS,
  confidence: CONFIDENCE_LABELS,
  general:  TERMINOLOGY,
};

/** Look up an Arabic label by code. Falls back to the raw key + `?`
 *  marker so missing translations are visually obvious in QA. */
export function labelFor(code, kind = "stage") {
  if (!code) return "—";
  const table = _TABLES[kind] || STAGE_LABELS;
  const entry = table[code];
  if (!entry) return `${code}`;       // raw fallback — never hide missing data
  return entry.label;
}

/** Look up the tooltip-friendly explanation. */
export function descriptionFor(code, kind = "stage") {
  if (!code) return "";
  const table = _TABLES[kind] || STAGE_LABELS;
  return table[code]?.description || "";
}

/** Convenience: return `{label, description}` together. */
export function termFor(code, kind = "stage") {
  if (!code) return { label: "—", description: "" };
  const table = _TABLES[kind] || STAGE_LABELS;
  return table[code] || { label: String(code), description: "" };
}
