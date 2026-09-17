import { useEffect, useState } from "react";
import api from "../../lib/api";

const money = (value) => value !== null && value !== undefined && value !== ""
  && Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)} ر.س` : "غير متاح";

export default function QoyodTotalsDiagnosis({ orderNumber, onClose }) {
  const [state, setState] = useState({ loading: true });

  useEffect(() => {
    let active = true;
    setState({ loading: true });
    api.get(`/integrations/qoyod/manual/diagnose/${encodeURIComponent(orderNumber)}`)
      .then(({ data }) => {
        if (!active) return;
        if (data?.ok !== true || String(data.order_number) !== String(orderNumber)) {
          setState({ error: data?.message || "تعذر الحصول على تشخيص مكتمل لهذا الطلب" });
          return;
        }
        setState({ data });
      })
      .catch((error) => {
        if (!active) return;
        const detail = error?.response?.data?.detail;
        setState({ error: typeof detail === "string" ? detail
          : detail?.message || "تعذر قراءة التشخيص؛ لم تُغيّر الفاتورة" });
      });
    return () => { active = false; };
  }, [orderNumber]);

  const data = state.data;
  const hasTotals = data && [data.salla_total, data.expected_qoyod_total, data.difference]
    .every((value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)));
  const matches = hasTotals && data.within_tolerance === true && Math.abs(Number(data.difference)) <= 0.01;
  const fee = data?.breakdown?.cod_fee;
  const settings = data?.settings_used;
  const productKnown = settings && Object.prototype.hasOwnProperty.call(settings, "default_cod_fee_product_id");
  const product = productKnown
    ? (settings.default_cod_fee_product_id || "غير مربوط") : "غير متاح";

  return (
    <section className="rounded-xl border border-sky-200 bg-sky-50 p-4 space-y-3"
      data-testid="qoyod-totals-diagnosis" aria-label={`تفصيل مبلغ الطلب ${orderNumber}`}>
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-bold">تفصيل مبلغ الطلب {orderNumber}</h2>
        <button type="button" onClick={onClose} className="rounded border px-3 py-1">إغلاق</button>
      </div>
      <p className="text-xs text-slate-600">
        قراءة فقط للحسبة من البيانات المحفوظة. لا ينشئ هذا الفحص فاتورة أو سند قبض،
        ولا يرفع الحجز عن الطلب. نجاح الحسبة لا يعني أن الفاتورة أُرسلت.
      </p>
      {state.loading && <p role="status">جاري قراءة تفصيل المبلغ…</p>}
      {state.error && <p role="alert" className="text-red-700">{state.error}</p>}
      {data && <>
        <p className="font-semibold">
          {matches ? "المبلغ مطابق ضمن هللة واحدة" : "المبلغ غير مطابق أو الأدلة غير مكتملة — يحتاج مراجعة"}
        </p>
        <table className="w-full text-sm bg-white">
          <tbody>
            <tr><th className="p-2 text-right">إجمالي سلة</th><td>{money(data.salla_total)}</td></tr>
            <tr><th className="p-2 text-right">إجمالي فاتورة قيود المتوقع</th><td>{money(data.expected_qoyod_total)}</td></tr>
            <tr><th className="p-2 text-right">الفرق: قيود ناقص سلة</th><td dir="ltr">{money(data.difference)}</td></tr>
            <tr><th className="p-2 text-right">رسوم الدفع عند الاستلام شامل الضريبة</th><td>{money(data.canonical_summary?.cod_fee_amount)}</td></tr>
            <tr><th className="p-2 text-right">بند رسوم الدفع عند الاستلام في قيود</th><td>{String(product)}</td></tr>
            <tr><th className="p-2 text-right">دخول الرسوم في الفاتورة</th><td>{fee?.included === true
              ? "مضاف" : fee?.included === false ? "غير مضاف" : "غير متاح"}</td></tr>
          </tbody>
        </table>
        {fee?.reason && <p className="text-amber-900">{fee.reason}</p>}
        {data.difference_source_hint && <p className="text-sm">{data.difference_source_hint}</p>}
        {productKnown && !settings.default_cod_fee_product_id && Number(data.canonical_summary?.cod_fee_amount) > 0 && (
          <a className="inline-block text-sky-800 underline" href="/integrations-v2/qoyod?tab=settings">
            ضبط بند رسوم الدفع عند الاستلام في إعدادات قيود
          </a>
        )}
      </>}
    </section>
  );
}
