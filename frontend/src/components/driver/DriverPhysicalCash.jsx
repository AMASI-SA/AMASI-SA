import { useEffect, useState } from "react";
import api from "../../lib/api";

const money = value => new Intl.NumberFormat("en-US", { style: "currency", currency: "SAR" }).format(Number(value));
const fields = { confirmed_cash: "النقد المؤكد", eligible_confirmed_cash: "المؤهل للمطابقة", expected_cod: "المتوقع للطلبات المسجلة", variance: "الفرق عن المتوقع", matched_handover: "التوريد المطابق", confirmed_cash_remaining: "النقد المؤكد المتبقي" };

export default function DriverPhysicalCash({ endpoint = "/store-delivery/app/accounts/physical-cash", refreshKey = 0 }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setData(null); setError("");
    api.get(endpoint).then(({ data: result }) => {
      if (result?.schema !== "mz2.driver.physical_cash.v1" || result.scope !== "captured_delivered_cash_only"
        || !Array.isArray(result.items) || !Array.isArray(result.reconciliations)
        || typeof result.coverage?.complete !== "boolean" || !Array.isArray(result.coverage.missing_confirmation_collection_ids)
        || Object.keys(fields).some(key => !/^-?\d+(?:\.\d{1,2})?$/.test(String(result.totals?.[key] ?? "")))) throw new Error("physical_cash_contract_unavailable");
      if (active) setData(result);
    }).catch(() => { if (active) setError("تعذر التحقق من سجل النقد الفعلي؛ الرصيد غير معلوم."); });
    return () => { active = false; };
  }, [revision, endpoint, refreshKey]);
  return <section className="space-y-3 rounded-2xl border bg-white p-4" dir="rtl">
    <h2 className="font-black">النقد الفعلي المؤكد عند التسليم</h2>
    <p className="text-sm">يشمل الإقرارات المسجلة لهذه الطلبات فقط؛ لا يشمل نقد البداية أو كامل السجل التاريخي، ولا يمثل الرصيد المحاسبي.</p>
    <button onClick={() => setRevision(value => value + 1)} className="rounded-xl border p-2">تحديث سجل النقد</button>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>جارٍ التحقق من الإقرارات…</p>}
    {data && <>
      {!data.coverage.complete && <p role="alert">التغطية غير مكتملة؛ توجد إقرارات ناقصة أو توريدات غير مطابقة أو تغيرات في المصدر. التحصيلات دون إقرار نقدي: {data.coverage.missing_confirmation_collection_ids.length}. لا يمكن افتراض قيمتها صفرًا.</p>}
      <dl className="grid grid-cols-2 gap-3">{Object.entries(fields).map(([key, label]) => <div key={key}><dt>{label}</dt><dd dir="ltr">{money(data.totals[key])}</dd></div>)}</dl>
      {data.items.map((item, index) => <article key={item.id || index} className="space-y-2 break-words rounded-xl border p-3">
        <p>الطلب: {item.order_number || item.order_id || "غير متاح"}</p>
        <p>المتوقع: {item.cod_amount == null ? "غير معلوم" : money(item.cod_amount)} · النقد الفعلي: {item.physical_cash_amount == null ? "غير معلوم" : money(item.physical_cash_amount)}</p>
        <p>الفرق: {item.variance == null ? "غير معلوم" : money(item.variance)} {Number(item.variance) < 0 ? "— نقص" : Number(item.variance) > 0 ? "— زيادة" : item.variance != null ? "— مطابق" : ""}</p>
        <p>{item.eligible_for_reconciliation ? "مؤهل للمطابقة" : "غير مؤهل للمطابقة؛ تحقق من تغير المصدر أو حالة الطلب"} · الحالة الحالية: {item.current_status || "غير معلومة"}</p>
        {item.reconciliation_reason && <p className="text-amber-900">سبب عدم الأهلية: {item.reconciliation_reason}</p>}
        <p>تأكيد الموصل: {item.confirmed_at || "غير متاح"} · {item.confirmation_actor || "غير متاح"}</p>
        <p>المطابق: {item.allocated_amount == null ? "غير معلوم" : money(item.allocated_amount)} · المتبقي: {item.remaining_amount == null ? "غير معلوم" : money(item.remaining_amount)}</p>
        <details><summary>مراجع الإقرار والطلب</summary><p>الإقرار: {item.id || "غير متاح"}</p><p>الطلب: {item.order_id} · الإسناد: {item.assignment_id}</p><p>إثبات التسليم: {item.delivery_proof_reference}</p><p>بصمة الإقرار: {item.seal}</p></details>
      </article>)}
      {!data.items.length && <p>لا توجد إقرارات نقدية مسجلة ضمن هذه التغطية.</p>}
      <h3 className="font-bold">مطابقات التوريد المسجلة</h3>
      {data.reconciliations.map((row, index) => <article key={row.id || index} className="space-y-2 break-words rounded-xl border p-3">
        <p>المصدر: {row.source?.source_type || "غير معلوم"} · {row.source?.source_id}</p>
        <p>مبلغ المصدر: {row.source?.amount == null ? "غير معلوم" : money(row.source.amount)}</p>
        <p>{row.source?.txn_group_id ? "مرجع قيد التوريد: " + row.source.txn_group_id : "مصدر تشغيلي؛ لا يثبت قيد توريد مالي"}{row.source?.journal_reversed && " — القيد معكوس"}</p>
        <p>هذه مطابقة للإقرارات ولا تنشئ قيدًا ماليًا.</p>
        {row.allocations?.map(allocation => <p key={allocation.collection_id}>الإقرار {allocation.collection_id}: {money(allocation.amount)}</p>)}
        <p>{row.reason} · {row.recorded_by} · {row.recorded_at}</p>
      </article>)}
      {!data.reconciliations.length && <p>لا توجد مطابقات توريد مسجلة.</p>}
    </>}
  </section>;
}
