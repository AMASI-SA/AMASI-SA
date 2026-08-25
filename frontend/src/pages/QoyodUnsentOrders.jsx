import { useEffect, useState } from "react";
import api from "../lib/api";

const QOYOD_BASE = "/integrations/qoyod";
const PAYMENT_RECHECK_CHUNK_SIZE = 10;

const STATUS_STYLE = {
  "أُرسل":    "bg-emerald-100 text-emerald-800 border-emerald-300",
  "لم يُرسل": "bg-amber-100 text-amber-800 border-amber-300",
  "فشل":      "bg-red-100 text-red-700 border-red-300",
  "مكرر":     "bg-slate-200 text-slate-700 border-slate-300",
};
const TABS = ["الكل", "لم يُرسل", "فشل", "مكرر", "أُرسل"];

function CountCard({ label, value, active, onClick }) {
  return (
    <button onClick={onClick} data-testid={`unsent-count-${label}`}
      className={`rounded-xl border px-4 py-3 text-right transition-colors ${
        active ? "border-slate-800 bg-slate-900 text-white"
               : "border-slate-200 bg-white hover:bg-slate-50"}`}>
      <div className="text-xs opacity-70">{label}</div>
      <div className="text-2xl font-bold">{value ?? 0}</div>
    </button>
  );
}

export default function QoyodUnsentOrders() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("لم يُرسل");
  const [days, setDays] = useState(30);
  const [error, setError] = useState(null);
  const [sallaStatus, setSallaStatus] = useState("");
  const [search, setSearch] = useState("");
  const [recoveryRunning, setRecoveryRunning] = useState(false);
  const [recoveryResults, setRecoveryResults] = useState([]);
  const [recoveryTotal, setRecoveryTotal] = useState(0);
  const [recoveryOpen, setRecoveryOpen] = useState(
    () => new URLSearchParams(window.location.search).get("recovery") === "1",
  );
  const [retryConfirmOrder, setRetryConfirmOrder] = useState(null);
  const [retryingOrder, setRetryingOrder] = useState(null);
  const [retryNotice, setRetryNotice] = useState(null);
  const [paymentCheckRunning, setPaymentCheckRunning] = useState(false);
  const [paymentCheckOrder, setPaymentCheckOrder] = useState(null);
  const [paymentCheckResults, setPaymentCheckResults] = useState([]);
  const unifiedReadModel = data?.source_authority === "unified_orders";
  const verifiedReadyOrderNumbers = Array.from(new Set(
    paymentCheckResults
      .filter((result) => result.outcome === "ready")
      .map((result) => String(result.order_number || "").trim())
      .filter((value) => /^\d+$/.test(value)),
  ));

  const allUnsentOrderNumbers = Array.from(new Set(
    (data?.orders || [])
      .filter((order) => order.status === "لم يُرسل")
      .map((order) => String(order.order_number || "").trim())
      .filter((value) => /^\d+$/.test(value)),
  ));
  const recoveryOrderNumbers = unifiedReadModel
    ? verifiedReadyOrderNumbers
    : allUnsentOrderNumbers;
  const bulkRecoveryAvailable = Boolean(data)
    && (!unifiedReadModel || verifiedReadyOrderNumbers.length > 0);
  const paymentCheckOrderNumbers = Array.from(new Set(
    (data?.orders || [])
      .filter((order) => ["لم يُرسل", "فشل"].includes(order.status))
      .map((order) => String(order.order_number || "").trim())
      .filter((value) => /^\d+$/.test(value)),
  )).slice(0, 100);

  const runPaymentCheck = async (orderNumber) => {
    setPaymentCheckOrder(orderNumber);
    setRetryNotice(null);
    try {
      const { data: result } = await api.post(
        `${QOYOD_BASE}/manual/recheck-payment/${encodeURIComponent(orderNumber)}`,
      );
      setPaymentCheckResults([result]);
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      setPaymentCheckResults([{
        order_number: orderNumber,
        outcome: "error",
        message: typeof detail === "string"
          ? detail : (detail?.message || "تعذر فحص الدفع من سلة"),
      }]);
    } finally {
      setPaymentCheckOrder(null);
    }
  };

  const runPaymentCheckBatch = async () => {
    if (!paymentCheckOrderNumbers.length) return;
    setPaymentCheckRunning(true);
    setPaymentCheckResults([]);
    setRetryNotice(null);
    setError(null);
    const collected = [];
    for (let offset = 0; offset < paymentCheckOrderNumbers.length;
      offset += PAYMENT_RECHECK_CHUNK_SIZE) {
      const orderNumbers = paymentCheckOrderNumbers.slice(
        offset,
        offset + PAYMENT_RECHECK_CHUNK_SIZE,
      );
      try {
        const { data: result } = await api.post(
          `${QOYOD_BASE}/manual/recheck-payment-bulk`,
          { order_numbers: orderNumbers },
        );
        collected.push(...(result?.results || []));
      } catch (requestError) {
        const detail = requestError?.response?.data?.detail;
        const message = typeof detail === "string"
          ? detail : (detail?.message || "تعذر فحص هذه الدفعة من سلة");
        collected.push(...orderNumbers.map((orderNumber) => ({
          order_number: orderNumber,
          outcome: "error",
          message,
        })));
      }
      setPaymentCheckResults([...collected]);
    }
    setPaymentCheckRunning(false);
  };

  const runRecoveryBatch = async () => {
    if (!recoveryOrderNumbers.length) {
      setError("لا توجد طلبات مصنفة «لم يُرسل» ضمن الفترة والفلاتر الحالية");
      return;
    }

    setRecoveryRunning(true);
    setRecoveryResults([]);
    setRecoveryTotal(recoveryOrderNumbers.length);
    setError(null);
    const next = [];

    for (const orderNumber of recoveryOrderNumbers) {
      try {
        const { data: sent } = await api.post(
          `${QOYOD_BASE}/manual/send/${encodeURIComponent(orderNumber)}`,
        );
        next.push({
          orderNumber,
          outcome: "sent",
          invoiceId: sent?.invoice_id || sent?.invoice_number || "—",
          paymentId: sent?.payment_id || null,
          invoiceOnly: !!sent?.invoice_only,
        });
      } catch (requestError) {
        const detail = requestError?.response?.data?.detail || {};
        const code = detail?.code || "unexpected_error";
        next.push({
          orderNumber,
          outcome: "review",
          code,
          message: detail?.message || String(detail || "فشل الإرسال"),
        });
        setRecoveryResults([...next]);
      }
      setRecoveryResults([...next]);
    }

    setRecoveryRunning(false);
    await fetchAll(days, sallaStatus, search);
  };

  const retryFailedOrder = async (orderNumber) => {
    setRetryingOrder(orderNumber);
    setRetryNotice(null);
    setError(null);
    try {
      const { data: result } = await api.post(
        `${QOYOD_BASE}/manual/retry-failed/${encodeURIComponent(orderNumber)}`,
      );
      const alreadySent = result?.retry_outcome === "already_sent";
      setRetryNotice({
        type: "success",
        message: alreadySent
          ? `تم التحقق من الطلب ${orderNumber}: الفاتورة موجودة مسبقاً في قيود، ولم تُنشأ فاتورة مكررة.`
          : `تم فحص الطلب ${orderNumber} من سلة وإرساله إلى قيود بنجاح.`,
      });
      setRetryConfirmOrder(null);
      await fetchAll(days, sallaStatus, search);
    } catch (requestError) {
      const detail = requestError?.response?.data?.detail;
      const message = typeof detail === "string"
        ? detail
        : (detail?.message || "تعذر إعادة فحص الطلب وإرساله");
      setRetryNotice({ type: "error", message });
    } finally {
      setRetryingOrder(null);
    }
  };

  const fetchAll = async (d = days, ss = sallaStatus, q = search) => {
    setLoading(true);
    try {
      const params = { days: d, limit: 1000 };
      if (ss) params.salla_status = ss;
      if (q && q.trim()) params.search = q.trim();
      const res = await api.get(`${QOYOD_BASE}/unsent-orders`, { params });
      setData(res.data);
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || "تعذر تحميل البيانات");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { fetchAll(); /* eslint-disable-next-line */ }, [days, sallaStatus]);

  const orders = (data?.orders || []).filter(
    (o) => tab === "الكل" || o.status === tab);
  const counts = data?.counts || {};

  return (
    <div className="space-y-6" dir="rtl" data-testid="qoyod-unsent-orders-page">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            طلبات لم تُرسل إلى قيود
          </h1>
          <p className="text-sm text-slate-500 mt-1">
            يعرض فقط الطلبات المؤهلة للفوترة في قيود. حالات انتظار المراجعة
            أو الدفع والطلبات الملغاة لا تُحتسب ضمن «لم يُرسل».
            {data?.from_date && data?.to_date && (
              <span className="mr-2 text-xs text-slate-400" data-testid="unsent-sync-start-note">
                (الفترة الفعلية: {data.from_date} إلى {data.to_date})
              </span>
            )}
            {unifiedReadModel && (
              <span className="mr-2 text-xs text-amber-700" data-testid="unsent-unified-readonly-note">
                (المصدر: الطلبات الموحدة؛ إعادة الإرسال الجماعي متوقفة حتى موافقة منفصلة)
              </span>
            )}
            {(data?.excluded_not_eligible ?? 0) > 0 && (
              <span className="mr-2 text-xs text-slate-400" data-testid="unsent-noneligible-note">
                (مستبعد غير مؤهل: {data.excluded_not_eligible})
              </span>
            )}
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <input value={search} dir="ltr"
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") fetchAll(days, sallaStatus, search); }}
            placeholder="بحث برقم الطلب…"
            data-testid="unsent-search-input"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm w-40" />
          <button onClick={() => fetchAll(days, sallaStatus, search)}
            data-testid="unsent-search-btn"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50">
            بحث
          </button>
          <select value={sallaStatus}
            onChange={(e) => setSallaStatus(e.target.value)}
            data-testid="unsent-salla-status-select"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value="">حالة سلة: الكل</option>
            {Object.entries(data?.salla_status_counts || {}).map(([s, n]) => (
              <option key={s} value={s}>{s} ({n})</option>
            ))}
          </select>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}
            data-testid="unsent-days-select"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm">
            <option value={7}>آخر 7 أيام</option>
            <option value={30}>آخر 30 يوم</option>
            <option value={90}>آخر 90 يوم</option>
          </select>
          <button onClick={() => fetchAll()} data-testid="unsent-refresh-btn"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700">
            تحديث
          </button>
          <button
            type="button"
            onClick={runPaymentCheckBatch}
            disabled={paymentCheckRunning || !paymentCheckOrderNumbers.length}
            data-testid="qoyod-payment-recheck-bulk"
            className="rounded-lg border border-sky-300 bg-sky-50 px-4 py-2 text-sm font-bold text-sky-800 hover:bg-sky-100 disabled:opacity-50">
            {paymentCheckRunning
              ? "جاري فحص الدفع فقط…"
              : `فحص الدفع فقط (${paymentCheckOrderNumbers.length})`}
          </button>
          {bulkRecoveryAvailable && (
            <button
              type="button"
              onClick={() => setRecoveryOpen((open) => !open)}
              aria-expanded={recoveryOpen}
              aria-controls="qoyod-recovery-panel"
              data-testid="qoyod-recovery-toggle"
              className="rounded-lg border border-rose-300 bg-rose-50 px-4 py-2 text-sm font-bold text-rose-800 hover:bg-rose-100">
              {recoveryOpen
                ? "إغلاق إعادة الإرسال"
                : unifiedReadModel
                  ? `إرسال المفحوصة الجاهزة (${recoveryOrderNumbers.length})`
                  : `إعادة إرسال الكل (${recoveryOrderNumbers.length})`}
            </button>
          )}
        </div>
      </div>

      {bulkRecoveryAvailable && recoveryOpen && (
        <div id="qoyod-recovery-panel"
             className="rounded-xl border border-rose-300 bg-rose-50 p-4 space-y-3"
             data-testid="qoyod-recovery-panel">
          <div>
            <h2 className="font-bold text-rose-950">
              {unifiedReadModel
                ? "إرسال الطلبات المفحوصة والجاهزة فقط"
                : "إعادة إرسال جميع الطلبات غير المرسلة"}
            </h2>
            <p className="mt-1 text-xs leading-5 text-rose-800">
              {unifiedReadModel
                ? `اختير فقط ${recoveryOrderNumbers.length} طلبًا أعاد فحص الدفع ووسمها جاهزة في هذه الجلسة. `
                : `سيأخذ النظام تلقائيًا كل الطلبات المصنفة «لم يُرسل» ضمن الفترة والفلاتر الحالية، وعددها ${recoveryOrderNumbers.length}. `}
              لا تحتاج
              إلى إدخال أرقام الطلبات. يعيد الخادم قراءة كل طلب من سلة، ولا يقبل
              إلا «تم التنفيذ» أو «جاري التوصيل» أو «تم التوصيل»، ثم يطبق حواجز
              التكرار والمبلغ. «تم التجهيز» يُقبل فقط عندما تصنّفه سلة بالحالة
              الموثوقة completed. إذا تعذر طلب واحد يبقى «لم يُرسل» ويواصل
              النظام بقية الطلبات.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="min-w-72 flex-1 rounded-lg border border-rose-200 bg-white px-3 py-2 text-sm text-rose-900"
                 data-testid="qoyod-recovery-selection-count">
              سيتم إرسال {recoveryOrderNumbers.length} طلب إلى قيود تلقائيًا
              {recoveryOrderNumbers.length === 1000 && (
                <span className="mr-1 text-xs text-rose-700">
                  (الحد الظاهر 1000؛ حدّث الصفحة بعد الانتهاء لإرسال أي دفعة متبقية)
                </span>
              )}
            </div>
            <button
              onClick={runRecoveryBatch}
              disabled={recoveryRunning || !recoveryOrderNumbers.length}
              data-testid="qoyod-recovery-send"
              className="rounded-lg bg-rose-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
              {recoveryRunning
                ? `جاري الإرسال ${recoveryResults.length} من ${recoveryTotal}…`
                : `تأكيد وإرسال ${recoveryOrderNumbers.length} طلب`}
            </button>
          </div>
          {recoveryRunning && (
            <div className="text-xs font-medium text-rose-800"
                 data-testid="qoyod-recovery-progress">
              تمت معالجة {recoveryResults.length} من {recoveryTotal}. يمكنك متابعة
              النتيجة لكل طلب في الجدول أدناه.
            </div>
          )}
          {recoveryResults.length > 0 && (
            <div className="overflow-x-auto rounded-lg border border-rose-200 bg-white">
              <table className="w-full text-xs" data-testid="qoyod-recovery-results">
                <thead className="bg-rose-100 text-rose-950">
                  <tr>
                    <th className="px-2 py-2 text-right">الطلب</th>
                    <th className="px-2 py-2 text-right">النتيجة</th>
                    <th className="px-2 py-2 text-right">فاتورة قيود</th>
                    <th className="px-2 py-2 text-right">سند القبض / السبب</th>
                  </tr>
                </thead>
                <tbody>
                  {recoveryResults.map((result) => (
                    <tr key={result.orderNumber} className="border-t border-rose-100">
                      <td className="px-2 py-2 font-mono">{result.orderNumber}</td>
                      <td className="px-2 py-2">
                        {result.outcome === "sent" ? "تم الإرسال"
                          : "بقي لم يُرسل — يحتاج مراجعة"}
                      </td>
                      <td className="px-2 py-2 font-mono">{result.invoiceId || "—"}</td>
                      <td className="px-2 py-2">
                        {result.outcome === "sent"
                          ? (result.invoiceOnly ? "فاتورة فقط (COD)"
                            : (result.paymentId || "سند قبض غير ظاهر"))
                          : `${result.code}: ${result.message}`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {["لم يُرسل", "فشل", "مكرر", "أُرسل"].map((s) => (
          <CountCard key={s} label={s} value={counts[s]}
            active={tab === s} onClick={() => setTab(s)} />
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        {TABS.map((t) => (
          <button key={t} onClick={() => setTab(t)}
            data-testid={`unsent-tab-${t}`}
            className={`rounded-full border px-4 py-1.5 text-sm ${
              tab === t ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"}`}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
             data-testid="unsent-error">{String(error)}</div>
      )}

      {retryNotice && (
        <div
          className={`rounded-lg border p-3 text-sm ${
            retryNotice.type === "success"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-red-200 bg-red-50 text-red-700"
          }`}
          data-testid="qoyod-failed-retry-notice">
          {retryNotice.message}
        </div>
      )}

      {paymentCheckResults.length > 0 && (
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-4"
             data-testid="qoyod-payment-recheck-results">
          <div className="mb-2 text-sm font-bold text-sky-950">
            نتيجة قراءة سلة فقط — لم تُرسل أي فاتورة ولم تُحفظ تغييرات
          </div>
          <div className="max-h-64 overflow-auto space-y-1 text-xs">
            {paymentCheckResults.map((result) => (
              <div key={result.order_number}
                   className="rounded border border-sky-100 bg-white px-3 py-2">
                <span className="font-mono">{result.order_number}</span>
                <span className="mx-2 font-bold">{result.outcome}</span>
                <span>{result.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white overflow-x-auto"
           data-testid="unsent-orders-table">
        {loading ? (
          <div className="p-6 text-sm text-slate-500">جاري التحميل…</div>
        ) : orders.length === 0 ? (
          <div className="p-6 text-sm text-slate-500" data-testid="unsent-empty">
            {tab === "لم يُرسل"
              ? "✅ لا توجد طلبات بانتظار الإرسال في هذه الفترة."
              : "لا توجد طلبات بهذه الحالة في الفترة المحددة."}
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 text-right">رقم الطلب</th>
                <th className="px-3 py-2 text-right">التاريخ</th>
                <th className="px-3 py-2 text-right">المبلغ</th>
                <th className="px-3 py-2 text-right">طريقة الدفع</th>
                <th className="px-3 py-2 text-right">حالة سلة</th>
                <th className="px-3 py-2 text-right">الحالة</th>
                <th className="px-3 py-2 text-right">السبب</th>
                <th className="px-3 py-2 text-right">فاتورة قيود</th>
                <th className="px-3 py-2 text-right">الإجراء</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((o, i) => (
                <tr key={`${o.trace_id}-${i}`}
                    className="border-t border-slate-100 hover:bg-slate-50"
                    data-testid={`unsent-row-${o.order_number}`}>
                  <td className="px-3 py-2 font-medium">{o.order_number || "—"}</td>
                  <td className="px-3 py-2 text-slate-500" dir="ltr">
                    {(o.order_date || o.received_at)
                      ? String(o.order_date || o.received_at).slice(0, 16).replace("T", " ")
                      : "—"}
                  </td>
                  <td className="px-3 py-2">{o.total_amount != null ? `${o.total_amount} ر.س` : "—"}</td>
                  <td className="px-3 py-2">{o.payment_method || "—"}</td>
                  <td className="px-3 py-2" data-testid={`unsent-salla-status-${o.order_number}`}>
                    {o.salla_status || o.salla_status_slug ? (
                      <span className="inline-block rounded-full border border-sky-200 bg-sky-50 px-2.5 py-0.5 text-xs text-sky-800">
                        {o.salla_status || o.salla_status_slug}
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <span className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[o.status] || ""}`}>
                      {o.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-slate-600 max-w-md">{o.reason}</td>
                  <td className="px-3 py-2" dir="ltr">{o.qoyod_invoice_id || "—"}</td>
                  <td className="px-3 py-2 min-w-48">
                    <button
                      type="button"
                      onClick={() => runPaymentCheck(o.order_number)}
                      disabled={paymentCheckOrder === o.order_number}
                      data-testid={`qoyod-payment-recheck-${o.order_number}`}
                      className="mb-1 rounded-lg border border-sky-300 bg-sky-50 px-3 py-1.5 text-xs font-bold text-sky-800 hover:bg-sky-100 disabled:opacity-50">
                      {paymentCheckOrder === o.order_number
                        ? "جاري الفحص فقط…" : "فحص الدفع فقط"}
                    </button>
                    {o.retry_allowed === true ? (
                      retryConfirmOrder === o.order_number ? (
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => retryFailedOrder(o.order_number)}
                            disabled={retryingOrder === o.order_number}
                            data-testid={`qoyod-failed-retry-confirm-${o.order_number}`}
                            className="rounded-lg bg-red-700 px-3 py-1.5 text-xs font-bold text-white hover:bg-red-800 disabled:opacity-50">
                            {retryingOrder === o.order_number
                              ? "جاري الفحص والإرسال…"
                              : "تأكيد الإرسال"}
                          </button>
                          <button
                            onClick={() => setRetryConfirmOrder(null)}
                            disabled={retryingOrder === o.order_number}
                            data-testid={`qoyod-failed-retry-cancel-${o.order_number}`}
                            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs text-slate-700 disabled:opacity-50">
                            إلغاء
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => {
                            setRetryConfirmOrder(o.order_number);
                            setRetryNotice(null);
                          }}
                          data-testid={`qoyod-failed-retry-${o.order_number}`}
                          className="rounded-lg border border-red-300 bg-red-50 px-3 py-1.5 text-xs font-bold text-red-800 hover:bg-red-100">
                          إعادة الفحص والإرسال
                        </button>
                      )
                    ) : "—"}
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
