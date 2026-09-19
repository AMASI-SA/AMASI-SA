import { useEffect, useState } from "react";
import api from "../../lib/api";

const BASE = "/integrations/qoyod/manual/recovery-404";
const LABELS = {
  not_prepared: "لم يُجهّز النطاق", prepared: "جاهز للمراجعة والتفعيل",
  active: "التعافي يعمل", paused: "متوقف للمراجعة", review_complete: "انتهت مراجعة المجموعة — راجع النتائج",
  pending: "بانتظار المعالجة", running: "جارٍ التحقق والمعالجة",
  excluded_completed: "مكتمل سابقًا — مستبعد من الإرسال",
  verified_sent: "أُرسل وتحققنا من الفاتورة والسداد",
  verified_existing: "صُولح بفاتورة موجودة",
  verified_audit: "تم التحقق بعد انقطاع الاستجابة",
  blocked: "مانع يحتاج مراجعة", unknown: "نتيجة غير محسومة — لا يُعاد الإرسال",
  review: "يحتاج مراجعة", disabled: "التفعيل متوقف",
};
const REASONS = {
  awaiting_activation: "بانتظار تفعيل المجموعة", refreshing_salla: "تحديث بيانات سلة",
  previously_verified_do_not_resend: "مكتمل سابقًا ولا يُعاد إرساله",
  invoice_amount_settlement_and_marker_verified: "الفاتورة والمبلغ والسداد وحالة ميزان متحققة",
  cod_deferred: "الدفع عند الاستلام مؤجل", missing_sku_deferred: "منتج بلا SKU — مؤجل",
  ineligible_status: "حالة سلة الحالية غير مؤهلة", payment_ineligible: "الدفع الحالي غير مؤهل",
  provider_amount_mismatch: "مبلغ الفاتورة لا يطابق سلة", provider_settlement_incomplete: "سداد الفاتورة غير مكتمل",
  duplicate_provider_invoices: "أكثر من فاتورة بنفس المرجع — يلزم مراجعة",
  outcome_unknown: "تعذر حسم نتيجة العملية — لا يُعاد الإرسال",
  submitted_invoice_not_found_do_not_retry: "لم تُثبت الفاتورة بعد المحاولة — لا يُعاد الإرسال",
  not_proven_old_product_404: "سبب الحجز الحالي خارج خطأ صفحات المنتجات المعتمد",
  live_total_changed_before_send: "تغير مبلغ سلة قبل الإرسال",
  mezan_marker_unverified: "علامة المصالحة في ميزان لم تُتحقق",
  campaign_not_prepared: "يجب تجهيز النطاق أولًا",
  campaign_active: "أوقف التعافي قبل التدقيق",
  operation_in_progress: "توجد عملية بحجز سارٍ؛ انتظر انتهاءها ثم حدّث الحالة",
};

export default function Qoyod404Recovery() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [refs, setRefs] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  async function refresh() {
    try { setData((await api.get(BASE)).data); setError(""); }
    catch (e) { setError(e?.response?.data?.detail || "تعذر قراءة حالة التعافي"); }
  }
  useEffect(() => { refresh(); const timer = setInterval(refresh, 15000); return () => clearInterval(timer); }, []);
  useEffect(() => { setConfirmed(false); }, [data?.fingerprint]);
  async function action(name, payload = {}) {
    setBusy(true); setError("");
    try { setData((await api.post(`${BASE}/${name}`, payload)).data); setConfirmed(false); }
    catch (e) {
      setError(e?.response?.data?.detail || "لم تصل نتيجة العملية؛ حدّث الحالة قبل تكرارها");
    } finally { setBusy(false); }
  }
  async function importCohort(event) {
    try {
      const file = event.target.files?.[0];
      if (!file) return;
      const json = JSON.parse(await file.text());
      if (!Array.isArray(json.order_numbers) || json.order_numbers.length !== 199) throw new Error();
      setRefs(json.order_numbers); setError("");
    } catch { setRefs(null); setError("اختر ملف المجموعة الأصلية الذي يحتوي على 199 رقم طلب"); }
  }
  const unresolved = ["running", "unknown", "review"].some(key => data?.counts?.[key] > 0);
  const activatable = ["prepared", "paused"].includes(data?.state) && !data?.busy && !unresolved;
  return <section className="rounded-xl border p-4 space-y-3" dir="rtl" data-testid="recovery-404">
    <h2 className="text-lg font-bold">تعافي مجموعة أخطاء 404</h2>
    <p>المجموعة الأصلية 199 طلبًا. الطلبان المكتملان مستبعدان من إعادة الإرسال. COD والمنتجات بلا SKU مؤجلة. يُحدّث العامل بيانات سلة قبل كل تقييم، ويعالج طلبًا واحدًا في كل دورة.</p>
    <p data-testid="recovery-counts">المكتمل: {data?.verified ?? 2} / {data?.total ?? 199} — المتبقي: {data?.remaining ?? 197}</p>
    <p>الحالة: {LABELS[data?.state] || data?.state || "جارٍ التحميل"}</p>
    {error && <p role="alert">{String(error)}</p>}
    {data?.state === "not_prepared" && <>
      <label>ملف المجموعة الأصلية <input type="file" accept="application/json,.json" onChange={importCohort} /></label>
      <button disabled={!refs || busy} onClick={() => action("prepare", { order_numbers: refs })}>تجهيز النطاق للمراجعة — دون إرسال</button>
    </>}
    {data?.fingerprint && <>
      <p>النطاق: {data.from_date} إلى {data.to_date} — المستبعد المكتمل: {data.excluded?.join("، ")}</p>
      {activatable && <>
        <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> أوافق على معالجة الطلبات المتبقية من هذه المجموعة فقط، مع التوقف عند نتيجة غير محسومة.</label>
        <button disabled={!confirmed || busy} onClick={() => action("activate", { fingerprint: data.fingerprint, confirmation: "ACTIVATE_REVIEWED_404_COHORT" })}>تفعيل التعافي التلقائي للنطاق المحدد</button>
      </>}
      <button disabled={busy || data.state !== "active"} onClick={() => action("pause")}>إيقاف التعافي</button>
      <button disabled={busy || data.can_audit !== true} onClick={() => action("audit")}>التحقق من المحاولات غير المحسومة — دون إرسال</button>
      {data.audit_block_reason && <p>{REASONS[data.audit_block_reason] || data.audit_block_reason}</p>}
    </>}
    <button disabled={busy} onClick={refresh}>تحديث حالة التعافي</button>
    {data?.results?.length > 0 && <details><summary>نتيجة كل طلب ({data.results.length})</summary>
      <table className="w-full"><thead><tr><th>الطلب</th><th>النتيجة</th><th>السبب</th><th>الفاتورة</th></tr></thead>
        <tbody>{data.results.map(row => <tr key={row.reference}><td>{row.reference}</td><td>{LABELS[row.state] || row.state}</td><td>{REASONS[row.reason] || row.reason}</td><td>{row.invoice_id || "—"}</td></tr>)}</tbody>
      </table></details>}
  </section>;
}
