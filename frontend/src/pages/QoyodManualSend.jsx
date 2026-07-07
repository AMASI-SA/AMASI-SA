import { useEffect, useState, useCallback } from "react";
import api from "../lib/api";

const BASE = "/integrations/qoyod/manual";

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
                المتوقع: {formatMoney(result.expected_total)} — الفرق:{" "}
                {result.difference}
              </div>
            </>
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
              {result.detail && (
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

export default function QoyodManualSend() {
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

  const loadHealth = useCallback(async () => {
    try {
      const res = await api.get(`${BASE}/health`);
      setHealth(res.data);
    } catch (e) {
      setHealth(null);
    }
  }, []);

  const loadRows = useCallback(
    async (d = days, q = search) => {
      setLoading(true);
      setError(null);
      try {
        const params = { days: d, limit: 500 };
        if (q && q.trim()) params.search = q.trim();
        const res = await api.get(`${BASE}/pending-orders`, { params });
        setRows(res.data?.orders || []);
        setCounts(res.data?.counts || null);
        setFloorDate(res.data?.floor_date || null);
      } catch (e) {
        setError(extractDetail(e));
        setRows([]);
        setCounts(null);
      } finally {
        setLoading(false);
      }
    },
    [days, search]
  );

  useEffect(() => {
    loadHealth();
    loadRows();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadRows(days, search);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [days]);

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

  return (
    <div
      className="space-y-6"
      dir="rtl"
      data-testid="qoyod-manual-send-page"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            إرسال يدوي إلى قيود (خطة B)
          </h1>
          <p className="mt-1 text-sm text-slate-500 max-w-2xl">
            طلبات مكتملة (تم التنفيذ) بتاريخ ≥ {floorDate || "2026-07-01"} ولم
            تُرسل إلى قيود بعد. الإرسال يدوي طلب بطلب — لا إرسال تلقائي.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            dir="ltr"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") loadRows(days, search);
            }}
            placeholder="بحث برقم الطلب…"
            data-testid="manual-send-search-input"
            className="w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={() => loadRows(days, search)}
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
              loadRows(days, search);
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
        className={`rounded-xl border p-3 text-sm ${
          health?.legacy_pipeline_frozen
            ? "border-emerald-200 bg-emerald-50 text-emerald-800"
            : "border-amber-200 bg-amber-50 text-amber-900"
        }`}
        data-testid="manual-send-freeze-banner"
      >
        <div className="flex flex-wrap items-center gap-4">
          <span className="font-medium">
            حالة تجميد المسار القديم:{" "}
            <span dir="ltr" className="font-mono">
              {health?.legacy_pipeline_frozen ? "true" : "false"}
            </span>
          </span>
          <span className="text-xs opacity-75">
            (تفعّل من إعدادات قيود ← `legacy_pipeline_frozen=true` لإيقاف
            المسار القديم بدون حذفه)
          </span>
          <span className="ml-auto text-xs opacity-75">
            روابط الدفع المُعرّفة:{" "}
            <span dir="ltr" className="font-mono">
              {health?.payment_method_mapping_count ?? "—"}
            </span>
          </span>
        </div>
      </div>

      <ResultBanner result={result} onDismiss={() => setResult(null)} />

      {error && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          data-testid="manual-send-error"
        >
          {String(error)}
        </div>
      )}

      {counts && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            ["ظاهرة الآن", counts.returned],
            ["فُحص من الاستلام", counts.scanned_inbox_rows],
            ["مستبعد قبل تاريخ التكامل", counts.excluded_pre_floor],
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
            ✅ لا توجد طلبات مؤهلة للإرسال اليدوي في هذه الفترة.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 text-right">رقم الطلب</th>
                <th className="px-3 py-2 text-right">تاريخ الإنشاء</th>
                <th className="px-3 py-2 text-right">العميل</th>
                <th className="px-3 py-2 text-right">المبلغ</th>
                <th className="px-3 py-2 text-right">طريقة الدفع</th>
                <th className="px-3 py-2 text-right">حالة سلة</th>
                <th className="px-3 py-2 text-right">إجراء</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr
                  key={o.order_number}
                  className="border-t border-slate-100 hover:bg-slate-50"
                  data-testid={`manual-send-row-${o.order_number}`}
                >
                  <td className="px-3 py-2 font-medium">{o.order_number}</td>
                  <td className="px-3 py-2 text-slate-500" dir="ltr">
                    {o.order_date || formatDate(o.received_at)}
                  </td>
                  <td className="px-3 py-2">{o.customer_name || "—"}</td>
                  <td className="px-3 py-2">
                    {formatMoney(o.total_amount, o.currency)}
                  </td>
                  <td className="px-3 py-2 text-slate-600">
                    {o.payment_method_native || o.payment_method || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <span className="inline-block rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-xs text-sky-800">
                      {o.salla_status || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-2">
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
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
