import { useEffect, useState, useCallback } from "react";
import api from "../lib/api";

const BASE = "/integrations/qoyod/manual";

const AUTO_CANARY_ORDERS = [
  "273274882",
  "271235259",
  "272982420",
  "272809621",
];
const AUTO_CANARY_CONFIRMATION = "تشغيل تجربة الأربعة";

const STATUS_TABS = [
  { key: "completed",   label: "تم التنفيذ",   icon: "✅" },
  { key: "in_delivery", label: "جاري التوصيل", icon: "🚚" },
  { key: "delivered",   label: "تم التوصيل",   icon: "📦" },
];

function formatMoney(v, currency = "SAR") {
  if (v == null || v === "") return "—";
  const num = Number(v);
  if (Number.isNaN(num)) return String(v);
  return `${num.toFixed(2)} ${currency === "SAR" ? "ر.س" : currency}`;
}

function formatDate(iso) {
  if (!iso) return "—";
  return String(iso).slice(0, 16).replace("T", " ");
}

function extractDetail(err) {
  const d = err?.response?.data?.detail;
  if (!d) return err?.response?.data?.message || err?.message || "خطأ غير معروف";
  if (typeof d === "string") return d;
  return d.message || d.code || JSON.stringify(d);
}

function fmt(v, digits = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return n.toFixed(digits);
}

function TotalsBreakdown({ detail }) {
  const b = detail?.breakdown || {};
  const items = b.items || [];
  const ship = b.shipping;
  const cod = b.cod_fee;
  const difference = Math.abs(Number(detail?.difference || 0));
  const withinTolerance = difference <= 0.01;


  return (
    <div
      dir="rtl"
      data-testid="totals-mismatch-breakdown"
      className={`mt-3 rounded-lg border bg-white p-3 text-xs text-slate-700 ${
        withinTolerance ? "border-emerald-200" : "border-red-200"
      }`}
    >
      <div className="mb-2 font-semibold text-slate-900">
        تفاصيل حساب الإجمالي (RCA)
      </div>
      <div className="mb-2 text-slate-600">
        نسبة الضريبة:{" "}
        <span dir="ltr" className="font-mono">
          {fmt(b.tax_percent, 2)}%
        </span>{" "}
        · إجمالي سلة:{" "}
        <span dir="ltr" className="font-mono">
          {fmt(detail.salla_total, 2)}
        </span>{" "}
        · إجمالي قيود المتوقع:{" "}
        <span dir="ltr" className="font-mono">
          {fmt(detail.expected_qoyod_total, 2)}
        </span>{" "}
        · الفرق:{" "}
        <span
          dir="ltr"
          className={`font-mono font-bold ${
            Math.abs(Number(detail.difference)) > 0.01
              ? "text-red-700"
              : "text-emerald-700"
          }`}
        >
          {fmt(detail.difference, 2)}
        </span>
      </div>
      {b.difference_source_hint && (
        <div className="mb-2 rounded bg-amber-50 p-2 text-amber-900">
          💡 مصدر الفرق: {b.difference_source_hint}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-[11px]">
          <thead className="bg-slate-100 text-slate-600">
            <tr>
              <th className="border p-1 text-right">SKU</th>
              <th className="border p-1 text-right">الوصف</th>
              <th className="border p-1 text-right">الكمية</th>
              <th className="border p-1 text-right">سعر الوحدة سلة</th>
              <th className="border p-1 text-right">سعر الوحدة قيود</th>
              <th className="border p-1 text-right">الخصم</th>
              <th className="border p-1 text-right">صافي بعد الخصم</th>
              <th className="border p-1 text-right">ضريبة 15%</th>
              <th className="border p-1 text-right">إجمالي قيود</th>
              <th className="border p-1 text-right">إجمالي سلة</th>
              <th className="border p-1 text-right">الفرق</th>
            </tr>
          </thead>
          <tbody>
            {items.map((it, i) => (
              <tr key={i}>
                <td dir="ltr" className="border p-1 font-mono">
                  {it.sku}
                </td>
                <td className="border p-1">{it.description}</td>
                <td dir="ltr" className="border p-1 text-center">
                  {it.quantity}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.salla_unit_price)}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.qoyod_unit_price)}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.computed_discount)}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.line_net_after_discount)}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.line_tax_15pct)}
                </td>
                <td dir="ltr" className="border p-1 text-left font-semibold">
                  {fmt(it.line_gross_after_tax)}
                </td>
                <td dir="ltr" className="border p-1 text-left">
                  {fmt(it.salla_line_total)}
                </td>
                <td
                  dir="ltr"
                  className={`border p-1 text-left font-mono ${
                    Math.abs(Number(it.delta_vs_salla_line)) > 0.01
                      ? "bg-red-50 text-red-800"
                      : ""
                  }`}
                >
                  {fmt(it.delta_vs_salla_line)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {b.rounding_adjustment &&
        (b.rounding_adjustment.applied || !withinTolerance) && (
        <div
          className={`mt-2 rounded border p-2 ${
            b.rounding_adjustment.applied
              ? "border-emerald-300 bg-emerald-50 text-emerald-900"
              : "border-red-300 bg-red-50 text-red-900"
          }`}
          data-testid="breakdown-rounding-adjustment"
        >
          <div className="font-semibold">
            {b.rounding_adjustment.applied ? "✅" : "⚠️"} تسوية فرق التقريب
            مع سلة:
          </div>
          {b.rounding_adjustment.applied ? (
            <div>
              قيمة السطر:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(b.rounding_adjustment.amount)}
              </span>{" "}
              (بدون ضريبة) · معرف المنتج في قيود:{" "}
              <span dir="ltr" className="font-mono">
                {b.rounding_adjustment.product_id}
              </span>
              . {b.rounding_adjustment.note}
            </div>
          ) : (
            <div>
              قيمة التسوية المطلوبة:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(b.rounding_adjustment.would_be_amount)}
              </span>{" "}
              — {b.rounding_adjustment.reason ===
                "rounding_adjustment_product_missing"
                ? "لم يُعرَّف rounding_adjustment_product_id في إعدادات قيود. أنشئ منتج تسوية في قيود واضبط معرّفه لتفعيل الإرسال التلقائي."
                : b.rounding_adjustment.reason}
            </div>
          )}
        </div>
      )}
      {ship && (
        <div
          className={`mt-2 rounded border p-2 ${
            ship.included
              ? "border-slate-200 bg-slate-50"
              : "border-red-300 bg-red-50 text-red-900"
          }`}
          data-testid="breakdown-shipping"
        >
          <div className="font-semibold">
            الشحن:{" "}
            {ship.included
              ? "مُدرج في الحساب"
              : "❗ مُهمَل — سبب رئيسي للفرق"}
          </div>
          {ship.included ? (
            <div>
              قيمة الشحن قبل الضريبة من سلة:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(ship.salla_declared_amount)}
              </span>{" "}
              · حصة الشحن من إجمالي سلة:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(ship.salla_declared_gross ?? ship.salla_declared_amount)}
              </span>{" "}
              · إجمالي قيود:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(ship.qoyod_gross_after_tax)}
              </span>{" "}
              · الفرق:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(ship.delta_vs_salla)}
              </span>
            </div>
          ) : (
            <div>
              مبلغ سلة:{" "}
              <span dir="ltr" className="font-mono">
                {fmt(ship.salla_declared_amount)}
              </span>{" "}
              — {ship.reason}
            </div>
          )}
        </div>
      )}
      {cod && (
        <div
          className={`mt-2 rounded border p-2 ${
            cod.included
              ? "border-slate-200 bg-slate-50"
              : "border-red-300 bg-red-50 text-red-900"
          }`}
          data-testid="breakdown-cod"
        >
          <div className="font-semibold">
            رسوم COD:{" "}
            {cod.included
              ? "مُدرج في الحساب"
              : "❗ مُهمَل — سبب رئيسي للفرق"}
          </div>
          <div>
            مبلغ سلة:{" "}
            <span dir="ltr" className="font-mono">
              {fmt(cod.salla_declared_amount)}
            </span>
            {cod.included && (
              <>
                {" "}· الفرق:{" "}
                <span dir="ltr" className="font-mono">
                  {fmt(cod.delta_vs_salla)}
                </span>
              </>
            )}
            {!cod.included && <> — {cod.reason}</>}
          </div>
        </div>
      )}
    </div>
  );
}

function ResultBanner({ result, onDismiss }) {
  if (!result) return null;
  const ok = result.ok;
  return (
    <div
      dir="rtl"
      data-testid="manual-send-result"
      className={`rounded-xl border p-4 ${
        ok
          ? "border-emerald-200 bg-emerald-50 text-emerald-800"
          : "border-red-200 bg-red-50 text-red-800"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          {ok ? (
            result.invoice_only ? (
              <>
                <div className="font-semibold">
                  ✅ تم إنشاء فاتورة الدفع عند الاستلام بنجاح
                  {result.order_number ? ` — الطلب #${result.order_number}` : ""}
                </div>
                <div className="mt-1 text-sm">
                  رقم الفاتورة في قيود:{" "}
                  <span className="font-mono">
                    {result.invoice_number || result.invoice_id}
                  </span>
                </div>
                <div className="mt-1 text-xs opacity-75">
                  إجمالي الفاتورة: {formatMoney(result.expected_total)}
                  {" "}— الفرق: {result.difference}
                </div>
                <div className="mt-2 rounded-lg border border-emerald-200 bg-white/70 p-2 text-sm">
                  لا يتم إنشاء سند الآن. ستبقى الفاتورة غير مدفوعة حتى تحصيل
                  المبلغ من شركة الشحن.
                </div>
                {result.send_date && (
                  <div className="mt-1 text-xs opacity-75">
                    تاريخ إنشاء الفاتورة في قيود (توقيت السعودية):{" "}
                    <span dir="ltr" className="font-mono">
                      {result.send_date}
                    </span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="font-semibold">
                  ✅ تم إرسال الطلب #{result.order_number} إلى قيود
                </div>
                <div className="mt-1 text-sm">
                  رقم الفاتورة في قيود:{" "}
                  <span className="font-mono">
                    {result.invoice_number || result.invoice_id}
                  </span>{" "}
                  — رقم السداد:{" "}
                  <span className="font-mono">{result.payment_id ?? "—"}</span>
                </div>
                <div className="mt-1 text-xs opacity-75">
                  إجمالي سلة: {formatMoney(result.salla_total)} — إجمالي قيود
                  المتوقع: {formatMoney(result.expected_total)} — قيمة السداد:{" "}
                  {formatMoney(result.payment_amount ?? result.expected_total)} —
                  الفرق: {result.difference}
                </div>
                {result.send_date && (
                  <div className="mt-1 text-xs opacity-75">
                    تاريخ إنشاء الفاتورة والسداد في قيود (توقيت السعودية):{" "}
                    <span dir="ltr" className="font-mono">
                      {result.send_date}
                    </span>
                  </div>
                )}
              </>
            )
          ) : (
            <>
              <div className="font-semibold">
                ❌ فشل الإرسال
                {result.order_number ? ` — الطلب #${result.order_number}` : ""}
              </div>
              <div className="mt-1 text-sm">
                <span className="font-mono">{result.code}</span> —{" "}
                {result.message}
              </div>
              {result.code === "totals_mismatch" &&
                result.detail?.breakdown && (
                  <TotalsBreakdown detail={result.detail} />
                )}
              {result.detail &&
                result.code !== "totals_mismatch" && (
                  <pre
                    dir="ltr"
                    className="mt-2 max-h-48 overflow-auto rounded bg-white p-2 text-xs text-slate-700 border border-red-100"
                  >
                    {JSON.stringify(result.detail, null, 2)}
                  </pre>
                )}
            </>
          )}
        </div>
        <button
          type="button"
          onClick={onDismiss}
          data-testid="manual-send-result-dismiss"
          className="text-slate-500 hover:text-slate-800"
        >
          ✕
        </button>
      </div>
    </div>
  );
}

function CanaryResultBanner({ result, onDismiss }) {
  if (!result) return null;
  return (
    <div
      dir="rtl"
      data-testid="qoyod-auto-canary-result"
      className={`rounded-xl border p-4 ${
        result.ok
          ? "border-emerald-300 bg-emerald-50 text-emerald-900"
          : "border-red-300 bg-red-50 text-red-900"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="font-semibold">
            {result.ok
              ? "✅ اكتملت تجربة الإرسال الآلي المغلقة"
              : "🛑 توقفت التجربة عند أول خطأ لحماية بقية الطلبات"}
          </div>
          <div className="mt-1 text-xs opacity-80">
            أُرسل الآن: {result.sent_count ?? 0} · موجود مسبقًا: {result.already_sent_count ?? 0}
            {" · "}فواتير بدون سند (COD): {result.invoice_only_count ?? 0}
            {" · "}فشل: {result.failed_count ?? 0}
          </div>
          <div className="mt-3 overflow-x-auto rounded-lg border border-current/20 bg-white/70">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-current/20">
                  <th className="px-3 py-2 text-right">الطلب</th>
                  <th className="px-3 py-2 text-right">النتيجة</th>
                  <th className="px-3 py-2 text-right">فاتورة قيود</th>
                  <th className="px-3 py-2 text-right">السداد</th>
                </tr>
              </thead>
              <tbody>
                {(result.results || []).map((row) => (
                  <tr key={row.order_number} className="border-b border-current/10 last:border-0">
                    <td dir="ltr" className="px-3 py-2 font-mono text-right">
                      {row.order_number}
                    </td>
                    <td className="px-3 py-2">
                      {row.outcome === "sent"
                        ? "تم الإرسال"
                        : row.outcome === "already_sent"
                          ? "موجود مسبقًا — لم يُكرر"
                          : `${row.code || "failed"} — ${row.message || "توقف"}`}
                    </td>
                    <td dir="ltr" className="px-3 py-2 font-mono text-right">
                      {row.invoice_number || row.invoice_id || "—"}
                    </td>
                    <td className="px-3 py-2">
                      {row.invoice_only
                        ? "بدون سند (COD)"
                        : row.payment_id || row.outcome === "already_sent"
                          ? row.payment_id || "موجود مسبقًا"
                          : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {result.remaining_count > 0 && (
            <div className="mt-2 text-sm font-medium">
              لم تُلمس الطلبات المتبقية: {result.remaining_count}
            </div>
          )}
          {result.run_id && (
            <div dir="ltr" className="mt-2 text-left font-mono text-[11px] opacity-60">
              {result.run_id}
            </div>
          )}
        </div>
        <button type="button" onClick={onDismiss} className="text-slate-500 hover:text-slate-800">
          ✕
        </button>
      </div>
    </div>
  );
}

export default function QoyodManualSend() {
  const PAGE_SIZE = 15;
  const [health, setHealth] = useState(null);
  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState(null);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(60);
  const [search, setSearch] = useState("");
  const [error, setError] = useState(null);
  const [sendingFor, setSendingFor] = useState(null);
  const [result, setResult] = useState(null);
  const [floorDate, setFloorDate] = useState(null);
  const [page, setPage] = useState(1);
  const [statusTab, setStatusTab] = useState("completed");
  const [diagnoseFor, setDiagnoseFor] = useState(null);
  const [diagnoseResult, setDiagnoseResult] = useState(null);
  const [diagnoseLoading, setDiagnoseLoading] = useState(false);
  const [canarySending, setCanarySending] = useState(false);
  const [canaryResult, setCanaryResult] = useState(null);

  const loadHealth = useCallback(async () => {
    try {
      const res = await api.get(`${BASE}/health`);
      setHealth(res.data);
    } catch (e) {
      setHealth(null);
    }
  }, []);

  const loadRows = useCallback(
    async (d = days, q = search, s = statusTab) => {
      setLoading(true);
      setError(null);
      try {
        const params = { days: d, limit: 500, status: s };
        if (q && q.trim()) params.search = q.trim();
        const res = await api.get(`${BASE}/pending-orders`, { params });
        setRows(res.data?.orders || []);
        setCounts(res.data?.counts || null);
        setFloorDate(res.data?.floor_date || null);
        setPage(1);
      } catch (e) {
        setError(extractDetail(e));
        setRows([]);
        setCounts(null);
      } finally {
        setLoading(false);
      }
    },
    [days, search, statusTab]
  );

  useEffect(() => {
    loadHealth();
    loadRows();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadRows(days, search, statusTab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days, statusTab]);

  const handleSend = async (orderNumber) => {
    const confirmed = window.confirm(
      `سيتم إرسال الطلب رقم ${orderNumber} إلى قيود.\n\n` +
        `الخطوات الأربعة:\n` +
        `  1. البحث عن العميل / إنشاؤه\n` +
        `  2. البحث عن المنتجات بالـ SKU / إنشاؤها\n` +
        `  3. إنشاء الفاتورة\n` +
        `  4. تسجيل السداد\n\n` +
        `هل تريد المتابعة؟`
    );
    if (!confirmed) return;
    setSendingFor(orderNumber);
    setResult(null);
    try {
      const res = await api.post(`${BASE}/send/${orderNumber}`);
      setResult(res.data);
      // Remove the row on success (or replace with an already-sent marker).
      setRows((prev) => prev.filter((r) => r.order_number !== orderNumber));
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail && typeof detail === "object") {
        setResult({
          ok: false,
          order_number: orderNumber,
          code: detail.code || "error",
          message: detail.message || extractDetail(e),
          detail: detail.detail || null,
        });
      } else {
        setResult({
          ok: false,
          order_number: orderNumber,
          code: "http_error",
          message: extractDetail(e),
        });
      }
    } finally {
      setSendingFor(null);
    }
  };

  const handleDiagnose = async (orderNumber) => {
    setDiagnoseFor(orderNumber);
    setDiagnoseResult(null);
    try {
      const res = await api.get(`${BASE}/diagnose/${orderNumber}`);
      setDiagnoseResult({ orderNumber, data: res.data });
    } catch (e) {
      setDiagnoseResult({
        orderNumber,
        data: { ok: false, code: "http_error", message: extractDetail(e) },
      });
    } finally {
      setDiagnoseFor(null);
    }
  };


  const handleAutoCanary = async () => {
    const confirmed = window.confirm(
      "سيتم تشغيل إرسال آلي متسلسل على أربعة طلبات فقط:\n\n" +
        AUTO_CANARY_ORDERS.join("\n") +
        "\n\nيتوقف الاختبار عند أول خطأ حقيقي، ولا يكرر أي فاتورة موجودة. " +
        "الدفع عند الاستلام يُنشئ فاتورة فقط بدون سند. هل تريد المتابعة؟"
    );
    if (!confirmed) return;

    setCanarySending(true);
    setCanaryResult(null);
    try {
      const res = await api.post(`${BASE}/auto-canary`, {
        confirmation: AUTO_CANARY_CONFIRMATION,
      });
      setCanaryResult(res.data);
      const completed = new Set(
        (res.data?.results || [])
          .filter((row) => row.ok)
          .map((row) => row.order_number)
      );
      setRows((prev) => prev.filter((row) => !completed.has(row.order_number)));
    } catch (e) {
      const detail = e?.response?.data?.detail;
      setCanaryResult({
        ok: false,
        status: "request_failed",
        sent_count: 0,
        already_sent_count: 0,
        invoice_only_count: 0,
        failed_count: 1,
        remaining_count: AUTO_CANARY_ORDERS.length,
        results: [{
          order_number: "—",
          outcome: "failed",
          code: detail?.code || "http_error",
          message: detail?.message || extractDetail(e),
        }],
      });
    } finally {
      setCanarySending(false);
    }
  };
  return (
    <div
      className="space-y-6"
      dir="rtl"
      data-testid="qoyod-manual-send-page"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            إرسال إلى قيود (خطة B)
          </h1>
          <p className="mt-1 text-sm text-slate-500 max-w-2xl">
            طلبات مكتملة (تم التنفيذ) بتاريخ ≥ {floorDate || "2026-07-01"} ولم
            تُرسل إلى قيود بعد. الإرسال اليدوي مستمر، وتجربة الإرسال الآلي
            الأولى مقيدة بأربعة طلبات فقط.
          </p>
          <p className="mt-1 text-xs text-slate-400 max-w-2xl">
            ملاحظة: عمود &quot;تاريخ الطلب في سلة&quot; أدناه للعرض فقط. تاريخ
            فاتورة قيود وتاريخ السداد سيكونان يوم الضغط على &quot;إرسال إلى
            قيود&quot; بتوقيت السعودية.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            dir="ltr"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") loadRows(days, search, statusTab);
            }}
            placeholder="بحث برقم الطلب…"
            data-testid="manual-send-search-input"
            className="w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => loadRows(days, search, statusTab)}
            data-testid="manual-send-search-btn"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
          >
            بحث
          </button>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            data-testid="manual-send-days-select"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value={7}>آخر 7 أيام</option>
            <option value={30}>آخر 30 يوم</option>
            <option value={60}>آخر 60 يوم</option>
            <option value={90}>آخر 90 يوم</option>
            <option value={180}>آخر 180 يوم</option>
            <option value={365}>آخر سنة</option>
          </select>
          <button
            type="button"
            onClick={() => {
              loadHealth();
              loadRows(days, search, statusTab);
            }}
            data-testid="manual-send-refresh-btn"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700"
          >
            تحديث
          </button>
        </div>
      </div>

      {/* Freeze / mapping banner */}
      <div
        className={`rounded-xl border p-4 text-sm ${
          health?.legacy_pipeline_frozen
            ? "border-emerald-300 bg-emerald-50 text-emerald-900"
            : "border-amber-300 bg-amber-50 text-amber-900"
        }`}
        data-testid="manual-send-freeze-banner"
      >
        <div className="flex flex-wrap items-center gap-4">
          <span className="font-semibold text-base">
            {health?.legacy_pipeline_frozen ? "🛑" : "⚠️"} حالة تجميد
            المسار القديم:{" "}
            <span
              dir="ltr"
              className="font-mono text-lg"
              data-testid="manual-send-freeze-value"
            >
              {health?.legacy_pipeline_frozen ? "true" : "false"}
            </span>
          </span>
          {!health?.legacy_pipeline_frozen && (
            <span className="text-xs opacity-75">
              يجب تفعيل التجميد قبل إرسال أي طلب من Plan B.
            </span>
          )}
          <button
            type="button"
            onClick={async () => {
              const target = !health?.legacy_pipeline_frozen;
              const confirmText = target
                ? "هل تريد تفعيل تجميد المسار القديم (Rev32→Rev48)؟\n\n" +
                  "بعد التفعيل: الـ worker يتوقف عن تحريك أي صف في " +
                  "integration_inbox. الملفات القديمة تبقى كما هي (لا " +
                  "حذف). Plan B يبقى مسار الإرسال الوحيد."
                : "هل تريد إلغاء تجميد المسار القديم؟\n\n" +
                  "بعد الإلغاء: الـ worker سيعود لتحريك الصفوف تلقائياً " +
                  "وقد يرسل طلبات إلى قيود بدون تدخّل يدوي.";
              if (!window.confirm(confirmText)) return;
              try {
                await api.post(`${BASE}/freeze-legacy-pipeline`, {
                  enabled: target,
                });
                await loadHealth();
              } catch (e) {
                window.alert(
                  "تعذّر تحديث حالة التجميد: " + extractDetail(e)
                );
              }
            }}
            data-testid="manual-send-freeze-toggle"
            className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
              health?.legacy_pipeline_frozen
                ? "bg-slate-200 text-slate-700 hover:bg-slate-300"
                : "bg-emerald-600 text-white hover:bg-emerald-700"
            }`}
          >
            {health?.legacy_pipeline_frozen
              ? "إلغاء التجميد"
              : "تفعيل التجميد الآن"}
          </button>
          <span className="ml-auto text-xs opacity-75">
            روابط الدفع المُعرّفة:{" "}
            <span dir="ltr" className="font-mono">
              {health?.payment_method_mapping_count ?? "—"}
            </span>
          </span>
        </div>
        {health?.legacy_pipeline_frozen_updated_at && (
          <div
            className="mt-2 text-xs opacity-75"
            data-testid="manual-send-freeze-audit"
          >
            آخر تعديل:{" "}
            <span dir="ltr" className="font-mono">
              {String(health.legacy_pipeline_frozen_updated_at).slice(
                0,
                19
              )}
            </span>{" "}
            — بواسطة:{" "}
            <span dir="ltr" className="font-mono">
              {health.legacy_pipeline_frozen_actor || "—"}
            </span>
          </div>
        )}
      </div>

      {statusTab === "completed" && (
        <div
          data-testid="qoyod-auto-canary-panel"
          className="rounded-xl border border-violet-300 bg-violet-50 p-4 text-violet-950"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-semibold">🧪 تجربة الإرسال الآلي المغلقة</div>
              <div className="mt-1 text-xs text-violet-800">
                أربعة طلبات محددة فقط؛ الطلب الملغي 273187928 مستبعد. التنفيذ
                متسلسل ويتوقف عند أول خطأ.
              </div>
              <div dir="ltr" className="mt-2 font-mono text-xs text-violet-700">
                {AUTO_CANARY_ORDERS.join(" · ")}
              </div>
            </div>
            <button
              type="button"
              onClick={handleAutoCanary}
              disabled={canarySending || !health?.legacy_pipeline_frozen}
              data-testid="qoyod-auto-canary-btn"
              className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                canarySending || !health?.legacy_pipeline_frozen
                  ? "cursor-not-allowed bg-slate-300 text-slate-600"
                  : "bg-violet-700 text-white hover:bg-violet-800"
              }`}
            >
              {canarySending ? "جارٍ الاختبار…" : "تشغيل الاختبار الآلي للأربعة"}
            </button>
          </div>
        </div>
      )}

      <CanaryResultBanner
        result={canaryResult}
        onDismiss={() => setCanaryResult(null)}
      />

      <ResultBanner result={result} onDismiss={() => setResult(null)} />

      {diagnoseResult && (
        <div
          dir="rtl"
          data-testid="diagnose-panel"
          className="rounded-xl border border-slate-300 bg-white p-4"
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <div className="font-semibold text-slate-900">
              🔍 تشخيص حساب الطلب #{diagnoseResult.orderNumber}
            </div>
            <button
              type="button"
              onClick={() => setDiagnoseResult(null)}
              data-testid="diagnose-close"
              className="text-slate-500 hover:text-slate-800"
            >
              ✕
            </button>
          </div>
          {diagnoseResult.data?.ok ? (
            <>
              <div className="mb-2 text-sm">
                {diagnoseResult.data.within_tolerance ? (
                  <span className="rounded bg-emerald-100 px-2 py-0.5 font-medium text-emerald-800">
                    ✅ ضمن حد التسامح (حتى 0.01 ريال) — الإرسال سيمر
                  </span>
                ) : (
                  <span className="rounded bg-red-100 px-2 py-0.5 font-medium text-red-800">
                    ❌ خارج حد التسامح — الإرسال سيتوقف
                  </span>
                )}
                <span className="mx-3 text-xs text-slate-500">
                  إجمالي سلة:{" "}
                  <span dir="ltr" className="font-mono">
                    {fmt(diagnoseResult.data.salla_total)}
                  </span>{" "}
                  · إجمالي قيود المتوقع:{" "}
                  <span dir="ltr" className="font-mono">
                    {fmt(diagnoseResult.data.expected_qoyod_total)}
                  </span>{" "}
                  · الفرق:{" "}
                  <span dir="ltr" className="font-mono font-bold">
                    {fmt(diagnoseResult.data.difference)}
                  </span>
                </span>
              </div>
              <TotalsBreakdown
                detail={{
                  salla_total: diagnoseResult.data.salla_total,
                  expected_qoyod_total:
                    diagnoseResult.data.expected_qoyod_total,
                  difference: diagnoseResult.data.difference,
                  breakdown: diagnoseResult.data.breakdown,
                }}
              />
            </>
          ) : (
            <div className="rounded bg-red-50 p-3 text-sm text-red-700">
              <span className="font-mono">
                {diagnoseResult.data?.code || "error"}
              </span>{" "}
              — {diagnoseResult.data?.message || "خطأ غير معروف"}
            </div>
          )}
        </div>
      )}

      {error && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          data-testid="manual-send-error"
        >
          {String(error)}
        </div>
      )}

      {counts && (
        <>
          {/* Status tabs — user directive 2026-07-08 */}
          <div
            className="flex flex-wrap gap-2 border-b border-slate-200"
            data-testid="manual-send-status-tabs"
          >
            {STATUS_TABS.map((tab) => {
              const active = statusTab === tab.key;
              return (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setStatusTab(tab.key)}
                  data-testid={`manual-send-tab-${tab.key}`}
                  className={`-mb-px rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition ${
                    active
                      ? "border-emerald-600 bg-white text-emerald-700"
                      : "border-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-700"
                  }`}
                >
                  <span className="ml-2">{tab.icon}</span>
                  {tab.label}
                  {active && counts && (
                    <span
                      dir="ltr"
                      className="mr-2 inline-block rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-mono text-emerald-800"
                    >
                      {counts.returned ?? 0}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
          {[
            ["ظاهرة الآن", counts.returned],
            ["فُحص من الاستلام", counts.scanned_inbox_rows],
            ["مستبعد قبل تاريخ التكامل", counts.excluded_pre_floor],
            ["مستبعد (بدون تاريخ سلة)", counts.excluded_no_salla_date],
            ["مستبعد (مرسل مسبقاً)", counts.excluded_already_sent],
          ].map(([label, value]) => (
            <div
              key={label}
              data-testid={`manual-send-count-${label}`}
              className="rounded-xl border border-slate-200 bg-white px-4 py-3"
            >
              <div className="text-xs text-slate-500">{label}</div>
              <div className="text-2xl font-semibold text-slate-900">
                {value ?? 0}
              </div>
            </div>
          ))}
          </div>
        </>
      )}

      <div
        className="overflow-x-auto rounded-xl border border-slate-200 bg-white"
        data-testid="manual-send-orders-table"
      >
        {loading ? (
          <div className="p-6 text-sm text-slate-500">جاري التحميل…</div>
        ) : rows.length === 0 ? (
          <div
            className="p-6 text-sm text-slate-500"
            data-testid="manual-send-empty"
          >
            ✅ لا توجد طلبات مؤهلة في تبويب &quot;
            {STATUS_TABS.find((t) => t.key === statusTab)?.label ||
              statusTab}
            &quot; خلال هذه الفترة.
          </div>
        ) : (
          (() => {
            const totalPages = Math.max(
              1,
              Math.ceil(rows.length / PAGE_SIZE)
            );
            const currentPage = Math.min(page, totalPages);
            const start = (currentPage - 1) * PAGE_SIZE;
            const pageRows = rows.slice(start, start + PAGE_SIZE);
            return (
              <>
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="px-3 py-2 text-right">رقم الطلب</th>
                      <th className="px-3 py-2 text-right">
                        تاريخ الطلب في سلة
                      </th>
                      <th className="px-3 py-2 text-right">العميل</th>
                      <th className="px-3 py-2 text-right">المبلغ</th>
                      <th className="px-3 py-2 text-right">طريقة الدفع</th>
                      <th className="px-3 py-2 text-right">حالة سلة</th>
                      <th className="px-3 py-2 text-right">إجراء</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((o) => (
                      <tr
                        key={o.order_number}
                        className="border-t border-slate-100 hover:bg-slate-50"
                        data-testid={`manual-send-row-${o.order_number}`}
                      >
                        <td className="px-3 py-2 font-medium">
                          {o.order_number}
                        </td>
                        <td
                          className="px-3 py-2 text-slate-500"
                          dir="ltr"
                          data-testid={`manual-send-salla-date-${o.order_number}`}
                          title="تاريخ إنشاء الطلب في سلة — للعرض فقط"
                        >
                          {o.order_date || formatDate(o.received_at)}
                        </td>
                        <td className="px-3 py-2">
                          {o.customer_name || "—"}
                        </td>
                        <td className="px-3 py-2">
                          {formatMoney(o.total_amount, o.currency)}
                        </td>
                        <td className="px-3 py-2 text-slate-600">
                          {o.receiving_bank_name
                            ? `تحويل بنكي — ${o.receiving_bank_name}`
                            : (o.payment_method_native ||
                              o.payment_method ||
                              "—")}
                        </td>
                        <td className="px-3 py-2">
                          <span className="inline-block rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-xs text-sky-800">
                            {o.salla_status || "—"}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <button
                              type="button"
                              onClick={() => handleSend(o.order_number)}
                              disabled={sendingFor === o.order_number}
                              data-testid={`manual-send-btn-${o.order_number}`}
                              className={`rounded-lg px-3 py-1.5 text-xs font-medium ${
                                sendingFor === o.order_number
                                  ? "bg-slate-300 text-slate-600"
                                  : "bg-emerald-600 text-white hover:bg-emerald-700"
                              }`}
                            >
                              {sendingFor === o.order_number
                                ? "جارٍ الإرسال…"
                                : "إرسال إلى قيود"}
                            </button>
                            <button
                              type="button"
                              onClick={() => handleDiagnose(o.order_number)}
                              disabled={diagnoseFor === o.order_number}
                              data-testid={`manual-send-diagnose-btn-${o.order_number}`}
                              title="عرض تفصيل حساب الإجمالي (بدون إرسال)"
                              className={`rounded-lg border px-2.5 py-1.5 text-xs font-medium ${
                                diagnoseFor === o.order_number
                                  ? "border-slate-300 bg-slate-100 text-slate-500"
                                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                              }`}
                            >
                              🔍 تشخيص
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div
                  className="flex items-center justify-between gap-3 border-t border-slate-100 bg-slate-50 px-3 py-2 text-sm"
                  data-testid="manual-send-pagination"
                >
                  <div className="text-slate-500">
                    عرض{" "}
                    <span dir="ltr" className="font-mono">
                      {start + 1}–{Math.min(start + PAGE_SIZE, rows.length)}
                    </span>{" "}
                    من{" "}
                    <span dir="ltr" className="font-mono">
                      {rows.length}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                      disabled={currentPage <= 1}
                      data-testid="manual-send-prev-page"
                      className={`rounded-lg border px-3 py-1.5 text-xs ${
                        currentPage <= 1
                          ? "border-slate-200 bg-slate-100 text-slate-400"
                          : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                      }`}
                    >
                      → السابق
                    </button>
                    <span
                      dir="ltr"
                      className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-mono"
                      data-testid="manual-send-page-indicator"
                    >
                      {currentPage} / {totalPages}
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setPage((p) => Math.min(totalPages, p + 1))
                      }
                      disabled={currentPage >= totalPages}
                      data-testid="manual-send-next-page"
                      className={`rounded-lg border px-3 py-1.5 text-xs ${
                        currentPage >= totalPages
                          ? "border-slate-200 bg-slate-100 text-slate-400"
                          : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
                      }`}
                    >
                      التالي ←
                    </button>
                  </div>
                </div>
              </>
            );
          })()
        )}
      </div>
    </div>
  );
}
