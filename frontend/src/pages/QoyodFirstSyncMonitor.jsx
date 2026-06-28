/**
 * Qoyod First-Sync Monitor — operational diagnostic page.
 *
 * Purpose
 * ───────
 * Before flipping Dry Run off, the operator wants ONE page that shows
 * — for the latest N orders — exactly what happened end-to-end:
 *
 *   • Make raw webhook body
 *   • Canonical DTO after normalization
 *   • Each of the 4 Qoyod POSTs: payload sent → response received → ID
 *   • Status badge per step + per-step duration
 *   • stage_history timeline
 *
 * READ-ONLY. No mutations, no retry triggers. Pure diagnostic.
 */
import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";


function StatusBadge({ status, testid }) {
  const map = {
    success: { bg: "bg-emerald-100", text: "text-emerald-800",
               border: "border-emerald-300", label: "✓ نجح" },
    failed:  { bg: "bg-rose-100",    text: "text-rose-800",
               border: "border-rose-300",    label: "✗ فشل" },
    pending: { bg: "bg-slate-100",   text: "text-slate-700",
               border: "border-slate-300",   label: "⋯ بانتظار" },
    skipped: { bg: "bg-amber-100",   text: "text-amber-800",
               border: "border-amber-300",   label: "تخطّى" },
  };
  const m = map[status] || map.pending;
  return (
    <span data-testid={testid}
          className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-bold border ${m.bg} ${m.text} ${m.border}`}>
      {m.label}
    </span>
  );
}


function JSONBlock({ data, testid, label }) {
  if (data === null || data === undefined) {
    return <div className="text-[11px] text-slate-400 italic">لا توجد بيانات</div>;
  }
  const text = (typeof data === "string")
    ? data : JSON.stringify(data, null, 2);
  return (
    <div data-testid={testid}>
      {label && <div className="text-[10px] font-bold text-slate-500 mb-1">{label}</div>}
      <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-slate-900 text-slate-100 rounded p-2 max-h-72 overflow-auto"
           dir="ltr">{text}</pre>
    </div>
  );
}


function StepCard({ step, index }) {
  const [open, setOpen] = useState(step.status === "failed");
  return (
    <div className={`rounded-xl border ${
      step.status === "failed" ? "border-rose-300 bg-rose-50/30"
      : step.status === "success" ? "border-emerald-300 bg-emerald-50/30"
      : "border-slate-200 bg-white"} p-3`}
         data-testid={`step-${step.key}`}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex-1">
          <div className="text-[11px] font-mono text-slate-500">
            خطوة {index + 1} · {step.stage}
          </div>
          <div className="text-sm font-extrabold text-slate-800">{step.title}</div>
        </div>
        <div className="flex items-center gap-2">
          {step.duration_ms != null && (
            <span className="text-[11px] font-mono text-slate-500"
                  data-testid={`step-${step.key}-duration`}>
              {step.duration_ms} ms
            </span>
          )}
          <StatusBadge status={step.status} testid={`step-${step.key}-status`} />
          <button type="button"
                  onClick={() => setOpen((v) => !v)}
                  className="text-[11px] font-bold text-sky-700 hover:underline"
                  data-testid={`step-${step.key}-toggle`}>
            {open ? "إخفاء" : "تفاصيل"}
          </button>
        </div>
      </div>
      {open && (
        <div className="grid md:grid-cols-2 gap-2 mt-3">
          <JSONBlock data={step.payload}  testid={`step-${step.key}-payload`}
                     label="📤 الإرسال إلى Qoyod" />
          <JSONBlock data={step.response} testid={`step-${step.key}-response`}
                     label="📥 الرد من Qoyod" />
        </div>
      )}
    </div>
  );
}


function StageHistoryTimeline({ history }) {
  if (!history || history.length === 0) {
    return <div className="text-[11px] text-slate-400 italic">لا توجد تحولات مسجّلة</div>;
  }
  return (
    <ol className="relative border-r-2 border-slate-200 pr-4 space-y-2"
        data-testid="stage-history-list">
      {history.map((h, i) => (
        <li key={i} className="relative">
          <div className="absolute -right-[9px] top-1.5 w-3 h-3 rounded-full
                          bg-sky-500 border-2 border-white" />
          <div className="text-[12px]">
            <span className="font-mono font-bold text-slate-700">{h.to_stage}</span>
            <span className="text-slate-400 mx-1">←</span>
            <span className="font-mono text-slate-500">{h.from_stage}</span>
          </div>
          <div className="text-[10px] text-slate-500 font-mono">
            {h.at} · actor={h.actor}
          </div>
          {h.note && (
            <div className="text-[11px] text-slate-700 mt-0.5">{h.note}</div>
          )}
          {h.error && (
            <pre className="text-[10px] mt-1 bg-rose-50 border border-rose-200
                            text-rose-800 rounded p-1.5 whitespace-pre-wrap"
                 dir="ltr">{JSON.stringify(h.error, null, 2)}</pre>
          )}
        </li>
      ))}
    </ol>
  );
}


function RowCard({ row, expanded, onToggle, onAdvanceNow, advancing }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 mb-4"
         data-testid={`monitor-row-${row.trace_id}`}>
      <button type="button" onClick={onToggle}
              className="w-full flex items-center justify-between gap-3 text-right"
              data-testid={`monitor-toggle-${row.trace_id}`}>
        <div className="flex-1">
          <div className="text-[10px] font-mono text-slate-400">
            trace_id: {row.trace_id} · {row.received_at}
          </div>
          <div className="text-sm font-extrabold text-slate-800 mt-0.5">
            طلب #{row.order_summary?.order_number || row.order_summary?.order_id || "—"}
            <span className="font-normal text-slate-500 mr-2">·</span>
            <span className="font-normal text-slate-600">
              {row.order_summary?.customer_name || "—"}
            </span>
          </div>
          <div className="text-[11px] text-slate-500 mt-0.5">
            {row.order_summary?.total_amount?.toLocaleString("ar-SA") || 0}
            {" "}{row.order_summary?.currency || "SAR"}
            {" · "}{row.order_summary?.items_count || 0} عنصر
            {row.order_summary?.payment_method
              && ` · ${row.order_summary.payment_method}`}
            {row.dry_run && (
              <span className="mr-2 inline-block px-1.5 py-0.5 rounded bg-amber-100
                                text-amber-800 font-extrabold text-[10px]">
                DRY-RUN
              </span>
            )}
            {row.stuck && (
              <span className="mr-2 inline-block px-1.5 py-0.5 rounded bg-rose-100
                                text-rose-800 font-extrabold text-[10px]"
                    data-testid={`row-stuck-${row.trace_id}`}>
                ⏳ بانتظار العامل ({row.stuck.waited_seconds}s)
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {row.pipeline_duration_ms != null && (
            <span className="text-[11px] font-mono text-slate-500">
              {row.pipeline_duration_ms} ms
            </span>
          )}
          <span className="text-[11px] font-mono font-bold text-slate-700">
            {row.pipeline_stage}
          </span>
          <span className="text-slate-400 text-sm">{expanded ? "▲" : "▼"}</span>
        </div>
      </button>

      {row.stuck && (
        <div className="mt-3 rounded-lg border border-rose-300 bg-rose-50 p-3"
             data-testid={`stuck-banner-${row.trace_id}`}>
          <div className="flex items-center justify-between gap-3">
            <div className="flex-1">
              <div className="text-sm font-extrabold text-rose-900">
                ⏳ الطلب بانتظار {row.stuck.waited_seconds} ثانية
                — العامل الخلفي قد يكون متأخراً
              </div>
              <div className="text-[12px] text-rose-700 mt-0.5">
                توقّف عند <code className="font-mono">{row.stuck.stage}</code>.
                اضغط الزر يميناً لتشغيل دفعة واحدة الآن.
              </div>
            </div>
            <button
              type="button"
              onClick={onAdvanceNow}
              disabled={advancing}
              data-testid={`btn-advance-now-${row.trace_id}`}
              className="px-3 py-2 text-xs font-extrabold rounded-lg bg-rose-600 text-white hover:bg-rose-700 disabled:opacity-50">
              {advancing ? "جاري التشغيل…" : "▶️ تشغيل الآن"}
            </button>
          </div>
        </div>
      )}

      {expanded && (
        <div className="mt-4 space-y-3">
          {/* Qoyod step cards */}
          <div className="space-y-2">
            {(row.qoyod_steps || []).map((step, i) => (
              <StepCard key={step.key} step={step} index={i} />
            ))}
          </div>

          {/* Make raw + canonical DTO side-by-side */}
          <div className="grid md:grid-cols-2 gap-3">
            <JSONBlock data={row.make_raw_payload}
                       testid={`raw-${row.trace_id}`}
                       label="📨 Make.com Raw Payload" />
            <JSONBlock data={row.canonical_dto}
                       testid={`canonical-${row.trace_id}`}
                       label="🔄 Canonical DTO (After Normalization)" />
          </div>

          {/* Business rules + preflight */}
          {(row.business_rules_decision || row.preflight) && (
            <div className="grid md:grid-cols-2 gap-3">
              {row.business_rules_decision && (
                <JSONBlock data={row.business_rules_decision}
                           testid={`rules-${row.trace_id}`}
                           label="⚖️ Business Rules Decision" />
              )}
              {row.preflight && (
                <JSONBlock data={row.preflight}
                           testid={`preflight-${row.trace_id}`}
                           label="✈️ Preflight Checklist" />
              )}
            </div>
          )}

          {/* Stage history timeline */}
          <div className="rounded-lg border border-slate-200 p-3 bg-slate-50/40">
            <div className="text-xs font-bold text-slate-700 mb-2">
              📜 Stage History ({(row.stage_history || []).length})
            </div>
            <StageHistoryTimeline history={row.stage_history} />
          </div>
        </div>
      )}
    </div>
  );
}


export default function QoyodFirstSyncMonitor() {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [limit, setLimit] = useState(5);
  const [workerStatus, setWorkerStatus] = useState(null);
  const [advancing, setAdvancing] = useState(false);
  const [stats, setStats] = useState(null);

  // Archive-failed-tests modal state
  const [archiveOpen, setArchiveOpen] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState("");
  const [archiving, setArchiving] = useState(false);
  const [archiveResult, setArchiveResult] = useState(null);

  // One-Shot Reprocess modal state
  const [reprocOpen, setReprocOpen] = useState(false);
  const [reprocOrderNo, setReprocOrderNo] = useState("");
  const [reprocConfirm, setReprocConfirm] = useState("");
  const [reprocTraceId, setReprocTraceId] = useState("");
  const [reproc, setReproc] = useState(false);
  const [reprocResult, setReprocResult] = useState(null);
  const [reprocError, setReprocError] = useState(null);

  // Last malformed-JSON receipts from Make.com (helps debug Iterator /
  // Create JSON misconfiguration BEFORE the row ever reaches the inbox).
  const [parseFails, setParseFails] = useState([]);
  const [parseFailsOpen, setParseFailsOpen] = useState(false);

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [rowsRes, wsRes, statsRes, parseFailsRes] = await Promise.all([
        axios.get(`${API}/integrations/qoyod/first-sync-monitor?limit=${limit}`),
        axios.get(`${API}/integrations/qoyod/worker/status`).catch(() => ({ data: null })),
        axios.get(`${API}/integrations/qoyod/first-sync-monitor/stats/summary`)
          .catch(() => ({ data: null })),
        axios.get(`${API}/integrations/qoyod/admin/webhook-parse-failures?limit=5`)
          .catch(() => ({ data: null })),
      ]);
      setRows(rowsRes.data?.rows || []);
      setWorkerStatus(wsRes.data?.worker || null);
      setStats(statsRes.data?.stats || null);
      setParseFails(parseFailsRes.data?.rows || []);
      if (!expandedId && (rowsRes.data?.rows || []).length > 0) {
        setExpandedId(rowsRes.data.rows[0].trace_id);
      }
    } catch (e) {
      if (!silent) toast.error("تعذّر تحميل المراقب");
    } finally {
      setLoading(false);
    }
  };

  const advanceNow = async () => {
    setAdvancing(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/worker/run-now`);
      const r = data?.result || {};
      const n = r.normalized?.processed || 0;
      const c = r.customer_resolved?.processed || 0;
      toast.success(`تم — ${n} normalized + ${c} customer_resolved`);
      await load(true);
    } catch (e) {
      toast.error(e.response?.data?.detail || "فشل التشغيل");
    } finally {
      setAdvancing(false);
    }
  };

  const runArchive = async () => {
    if (archiveConfirm.trim() !== "CLEAN") {
      toast.error('أدخل كلمة "CLEAN" للتأكيد');
      return;
    }
    setArchiving(true);
    try {
      const { data } = await axios.post(
        `${API}/integrations/qoyod/first-sync-monitor/archive-failed-tests`,
        { confirm: "CLEAN" });
      setArchiveResult(data);
      toast.success(
        `تمت الأرشفة — ${data.archived} سجل تم نقله، ${data.deleted} حذف من القائمة الحية`);
      await load(true);
    } catch (e) {
      const detail = e.response?.data?.detail;
      toast.error(detail?.message || detail || "فشلت الأرشفة");
    } finally {
      setArchiving(false);
    }
  };

  const expectedReprocToken = (reprocOrderNo || "").trim()
    ? `REPROCESS-${reprocOrderNo.trim()}`
    : "";

  const runReprocess = async () => {
    const orderNo = (reprocOrderNo || "").trim();
    const confirmTok = (reprocConfirm || "").trim();
    if (!orderNo) {
      toast.error("أدخل رقم الطلب أولاً");
      return;
    }
    if (confirmTok !== expectedReprocToken) {
      toast.error(`أدخل التأكيد بالضبط: ${expectedReprocToken}`);
      return;
    }
    setReproc(true);
    setReprocError(null);
    setReprocResult(null);
    try {
      const body = { order_number: orderNo, confirm: confirmTok };
      const traceTrim = (reprocTraceId || "").trim();
      if (traceTrim) body.trace_id = traceTrim;
      const { data } = await axios.post(
        `${API}/integrations/qoyod/admin/one-shot-reprocess`, body);
      setReprocResult(data);
      if (data?.outcome === "COMPLETED") {
        toast.success(`نجحت إعادة معالجة الطلب ${orderNo}`);
      } else if (data?.outcome === "ALREADY_COMPLETED") {
        toast.info(`الطلب ${orderNo} مكتمل سابقاً`);
      } else {
        toast.error(`فشل: ${data?.outcome || "غير معروف"}`);
      }
      await load(true);
    } catch (e) {
      const detail = e.response?.data?.detail;
      setReprocError(detail || { code: "request_failed", message: e.message });
      toast.error(detail?.message || detail?.code || "فشل الطلب");
    } finally {
      setReproc(false);
    }
  };

  useEffect(() => { load(); }, [limit]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = setInterval(() => load(true), 5000);
    return () => clearInterval(id);
  }, [autoRefresh, limit]);

  return (
    <div dir="rtl" className="max-w-6xl mx-auto p-4 md:p-6"
         data-testid="qoyod-first-sync-monitor-page">
      <header className="mb-4">
        <h1 className="text-2xl md:text-3xl font-extrabold text-slate-900">
          🩺 مراقب أول مزامنة — Qoyod
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          آخر الطلبات التي مرّت عبر خط معالجة قيود. يعرض الـ payload المُرسَل
          والرد المُستلَم لكل خطوة (عميل ← منتجات ← فاتورة ← سند قبض)
          مع المدة الزمنية وحالة كل خطوة.
        </p>
      </header>

      {/* Status counter badges (Iter — sidebar integration) */}
      {stats && (
        <div className="mb-4 grid grid-cols-2 md:grid-cols-4 gap-2"
             data-testid="monitor-stats-badges">
          <div className="rounded-xl border border-sky-200 bg-sky-50 p-3"
               data-testid="stat-processing">
            <div className="text-[11px] font-bold text-sky-700">قيد المعالجة</div>
            <div className="text-2xl font-extrabold text-sky-900 num">
              {stats.processing || 0}
            </div>
          </div>
          <div className={`rounded-xl border p-3 ${
                  (stats.failed || 0) > 0
                    ? "border-rose-300 bg-rose-50"
                    : "border-slate-200 bg-slate-50"}`}
               data-testid="stat-failed">
            <div className={`text-[11px] font-bold ${
                  (stats.failed || 0) > 0 ? "text-rose-700" : "text-slate-600"}`}>
              فشل (DEAD_LETTER + PARTIAL)
            </div>
            <div className={`text-2xl font-extrabold num ${
                  (stats.failed || 0) > 0 ? "text-rose-900" : "text-slate-700"}`}>
              {stats.failed || 0}
            </div>
          </div>
          <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3"
               data-testid="stat-success">
            <div className="text-[11px] font-bold text-emerald-700">ناجحة (COMPLETED)</div>
            <div className="text-2xl font-extrabold text-emerald-900 num">
              {stats.success || 0}
            </div>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3"
               data-testid="stat-skipped">
            <div className="text-[11px] font-bold text-amber-700">متخطّاة (SKIPPED)</div>
            <div className="text-2xl font-extrabold text-amber-900 num">
              {stats.skipped || 0}
            </div>
          </div>
        </div>
      )}

      {/* Archive failed dry-run tests — surfaced only when there's
          something to clean. Strict safety: archive (not delete) + a
          typed "CLEAN" confirmation token. */}
      {stats && (stats.dry_failed || 0) > 0 && (
        <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 p-3 flex items-center justify-between gap-3"
             data-testid="archive-failed-tests-banner">
          <div className="flex-1">
            <div className="text-sm font-extrabold text-amber-900">
              🗂️ يوجد {stats.dry_failed} سجل اختبار فاشل من Dry Run
            </div>
            <div className="text-[12px] text-amber-800 mt-0.5">
              يمكنك أرشفتها لتنظيف لوحة المراقبة.
              <strong className="mx-1">لن يتم حذف أي بيانات من قيود</strong>
              ولن تُمسّ السجلات الناجحة (COMPLETED) ولا السجلات الإنتاجية —
              فقط <code className="font-mono">DEAD_LETTER</code> +
              <code className="font-mono"> PARTIAL_FAILURE</code> في وضع Dry Run.
            </div>
          </div>
          <button
            type="button"
            onClick={() => { setArchiveOpen(true); setArchiveConfirm(""); setArchiveResult(null); }}
            data-testid="btn-open-archive-modal"
            className="px-3 py-2 text-xs font-extrabold rounded-lg bg-amber-600 text-white hover:bg-amber-700 whitespace-nowrap"
          >
            🗂️ أرشفة فشل الاختبار القديم
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div className="rounded-xl border border-slate-200 bg-white p-3 mb-4
                      flex flex-wrap items-center gap-3"
           data-testid="monitor-toolbar">
        <button onClick={() => load()} disabled={loading}
                data-testid="btn-refresh"
                className="px-3 py-1.5 text-xs font-bold rounded bg-slate-900 text-white hover:bg-black disabled:opacity-50">
          {loading ? "تحديث…" : "🔄 تحديث"}
        </button>
        <label className="flex items-center gap-1.5 text-xs text-slate-700">
          <input type="checkbox" checked={autoRefresh}
                 onChange={(e) => setAutoRefresh(e.target.checked)}
                 data-testid="toggle-auto-refresh"
                 className="h-4 w-4 accent-sky-600" />
          تحديث تلقائي كل 5 ثوانٍ
        </label>
        <label className="flex items-center gap-1.5 text-xs text-slate-700">
          آخر
          <select value={limit}
                  onChange={(e) => setLimit(parseInt(e.target.value, 10))}
                  data-testid="select-limit"
                  className="px-2 py-1 border border-slate-300 rounded text-xs">
            {[1, 3, 5, 10, 25].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          طلب
        </label>
        <button type="button"
                onClick={() => {
                  setReprocOpen(true);
                  setReprocOrderNo("");
                  setReprocConfirm("");
                  setReprocTraceId("");
                  setReprocResult(null);
                  setReprocError(null);
                }}
                data-testid="btn-open-one-shot-reprocess"
                className="px-3 py-1.5 text-xs font-bold rounded bg-indigo-600 text-white hover:bg-indigo-700">
          🎯 إعادة معالجة طلب واحد
        </button>
        <div className="text-[11px] text-slate-500 ms-auto flex items-center gap-3"
             data-testid="monitor-count">
          {workerStatus && (
            <span data-testid="worker-status"
                  className={`px-2 py-0.5 rounded-full font-bold border
                    ${workerStatus.running && workerStatus.last_run_ok
                      ? "bg-emerald-50 text-emerald-800 border-emerald-300"
                      : "bg-rose-50 text-rose-800 border-rose-300"}`}>
              العامل: {workerStatus.running
                       ? (workerStatus.last_run_ok ? "✓ يعمل" : "⚠ خطأ")
                       : "✗ متوقف"}
            </span>
          )}
          <span>{rows.length} طلب معروض</span>
        </div>
      </div>

      {loading && rows.length === 0 && (
        <div className="p-8 text-center text-slate-500">جاري التحميل…</div>
      )}

      {/* Webhook parse failures — Make.com sending broken JSON ──────── */}
      {parseFails.length > 0 && (
        <div className="rounded-xl border-2 border-rose-300 bg-rose-50 p-4"
             data-testid="parse-failures-banner">
          <button type="button"
                  onClick={() => setParseFailsOpen(!parseFailsOpen)}
                  className="w-full flex items-center justify-between text-right">
            <div className="text-sm font-extrabold text-rose-900">
              🛑 آخر {parseFails.length} طلب رفضناهم بسبب JSON غير صالح من Make
            </div>
            <div className="text-xs text-rose-700 font-bold">
              {parseFailsOpen ? "إخفاء ▲" : "عرض ▼"}
            </div>
          </button>
          <div className="text-[11px] text-rose-700 mt-1">
            هذه الطلبات لم تصل حتى للـ inbox — Make يُرسل body غير قابل للقراءة
            (عادةً <code className="font-mono bg-rose-100 px-1">items: [object Object]</code>).
            راجع: <code className="font-mono">docs/integrations/make-runbook-build-items-array.md</code>
          </div>
          {parseFailsOpen && (
            <div className="mt-3 space-y-2" data-testid="parse-failures-list">
              {parseFails.map((pf, idx) => (
                <details key={idx} className="bg-white rounded border border-rose-200 p-2">
                  <summary className="cursor-pointer text-[12px] text-rose-900">
                    <span className="font-mono">{pf.occurred_at}</span>
                    {" · "}
                    <span className="font-bold">{pf.parser_error}</span>
                    {pf.token_prefix && (
                      <span className="text-rose-600">
                        {" · token="}<code className="font-mono">{pf.token_prefix}</code>
                      </span>
                    )}
                  </summary>
                  <div className="mt-2 text-[10px] font-mono text-slate-500">
                    {pf.content_type} · {pf.content_length} bytes · ip={pf.ip || "—"}
                  </div>
                  <pre className="mt-1 bg-slate-900 text-slate-100 p-2 rounded text-[10px] font-mono whitespace-pre-wrap break-words max-h-48 overflow-auto"
                       dir="ltr"
                       data-testid={`parse-failure-body-${idx}`}>
{pf.body_preview}
                  </pre>
                </details>
              ))}
            </div>
          )}
        </div>
      )}

      {!loading && rows.length === 0 && (
        <div className="rounded-xl border-2 border-dashed border-slate-300 bg-white p-8 text-center"
             data-testid="monitor-empty">
          <div className="text-base font-bold text-slate-700 mb-1">
            لم يصل أي طلب بعد إلى خط المعالجة
          </div>
          <p className="text-xs text-slate-500 leading-relaxed">
            أرسل أول Test Payload من Make.com وسيظهر هنا فوراً.
            هذه الصفحة تتحديث تلقائياً إن فعّلت الخيار من شريط الأدوات.
          </p>
        </div>
      )}

      {rows.map((row) => (
        <RowCard key={row.trace_id} row={row}
                 expanded={expandedId === row.trace_id}
                 onToggle={() =>
                   setExpandedId(expandedId === row.trace_id
                     ? null : row.trace_id)}
                 onAdvanceNow={advanceNow}
                 advancing={advancing} />
      ))}

      {/* Archive confirm modal — requires typing "CLEAN" */}
      {archiveOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
             data-testid="archive-modal-backdrop"
             onClick={() => !archiving && setArchiveOpen(false)}>
          <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full p-6"
               data-testid="archive-modal"
               onClick={(e) => e.stopPropagation()}>
            <h2 className="text-xl font-extrabold text-slate-900 mb-2">
              🗂️ أرشفة فشل الاختبار القديم
            </h2>
            <p className="text-sm text-slate-700 leading-relaxed">
              ستتم أرشفة كل سجل بمرحلة
              <code className="font-mono mx-1 text-rose-700">DEAD_LETTER</code>
              أو <code className="font-mono mx-1 text-rose-700">PARTIAL_FAILURE</code>
              تم إنشاؤه في وضع <strong>Dry Run</strong> فقط.
            </p>
            <ul className="text-[12px] text-slate-600 list-disc pe-5 mt-2 space-y-1">
              <li>السجلات تُنقَل إلى مجموعة الأرشيف (<code className="font-mono">integration_inbox_archive</code>) — قابلة للاسترجاع.</li>
              <li>لن يُمسّ أي سجل <code className="font-mono">COMPLETED</code>.</li>
              <li>لن يتم حذف أي بيانات من قيود نفسه.</li>
              <li>لن يتم حذف السجلات الإنتاجية الفاشلة (إن وُجدت).</li>
            </ul>

            {archiveResult ? (
              <div className="mt-4 rounded-lg border border-emerald-300 bg-emerald-50 p-3"
                   data-testid="archive-result">
                <div className="text-sm font-extrabold text-emerald-900">
                  ✓ تمت الأرشفة بنجاح
                </div>
                <div className="text-[12px] text-emerald-800 mt-1 num">
                  مطابق: {archiveResult.matched} · مؤرشف: {archiveResult.archived} ·
                  محذوف من القائمة الحية: {archiveResult.deleted}
                </div>
              </div>
            ) : (
              <>
                <label className="block mt-4 text-[12px] font-bold text-slate-700">
                  للتأكيد، اكتب <code className="font-mono text-rose-700">CLEAN</code> في الحقل أدناه:
                </label>
                <input
                  type="text"
                  value={archiveConfirm}
                  onChange={(e) => setArchiveConfirm(e.target.value)}
                  dir="ltr"
                  placeholder="CLEAN"
                  data-testid="archive-confirm-input"
                  autoFocus
                  className="mt-1 w-full px-3 py-2 border-2 border-slate-300 rounded-lg font-mono text-sm focus:border-amber-500 outline-none"
                />
              </>
            )}

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setArchiveOpen(false)}
                disabled={archiving}
                data-testid="btn-archive-cancel"
                className="px-4 py-2 text-sm font-bold rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {archiveResult ? "إغلاق" : "إلغاء"}
              </button>
              {!archiveResult && (
                <button
                  type="button"
                  onClick={runArchive}
                  disabled={archiving || archiveConfirm.trim() !== "CLEAN"}
                  data-testid="btn-archive-confirm"
                  className="px-4 py-2 text-sm font-extrabold rounded-lg bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-40"
                >
                  {archiving ? "جاري الأرشفة…" : "🗂️ تنفيذ الأرشفة"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* One-Shot Reprocess modal — single order, REPROCESS-<order_number> */}
      {reprocOpen && (
        <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4"
             data-testid="reproc-modal-backdrop"
             onClick={() => !reproc && setReprocOpen(false)}>
          <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full p-6 max-h-[90vh] overflow-y-auto"
               data-testid="reproc-modal"
               onClick={(e) => e.stopPropagation()}>
            <h2 className="text-xl font-extrabold text-slate-900 mb-2">
              🎯 إعادة معالجة طلب واحد فقط
            </h2>
            <p className="text-sm text-slate-700 leading-relaxed">
              تستهدف <strong>طلباً واحداً فقط</strong> وتُرسله إلى Qoyod الإنتاجي.
              لا تُشغّل Backfill ولا تُلامس أي سجل آخر.
            </p>
            <ul className="text-[12px] text-slate-600 list-disc pe-5 mt-2 space-y-1">
              <li>إذا وجد أي <code className="font-mono">DRY:</code> في contact_id أو product_id يتم إيقاف الإرسال فوراً.</li>
              <li>الخرائط (mappings) القديمة بـ <code className="font-mono">DRY:*</code> يتم عزلها قبل إعادة المعالجة.</li>
              <li>عند الفشل: لا إعادة محاولة. سيظهر <code className="font-mono">request_body_json</code> + المرحلة التي فشلت عندها فقط.</li>
            </ul>

            {!reprocResult && !reprocError && (
              <div className="mt-4 space-y-3">
                <div>
                  <label className="block text-[12px] font-bold text-slate-700 mb-1">
                    رقم الطلب (Salla Order Number)
                  </label>
                  <input
                    type="text"
                    value={reprocOrderNo}
                    onChange={(e) => setReprocOrderNo(e.target.value)}
                    dir="ltr"
                    placeholder="268670571"
                    data-testid="reproc-order-no-input"
                    autoFocus
                    className="w-full px-3 py-2 border-2 border-slate-300 rounded-lg font-mono text-sm focus:border-indigo-500 outline-none"
                  />
                </div>

                <div>
                  <label className="block text-[12px] font-bold text-slate-700 mb-1">
                    Trace ID <span className="text-slate-400 font-normal">(اختياري — لتمييز السجلات إذا تكرّر نفس الطلب)</span>
                  </label>
                  <input
                    type="text"
                    value={reprocTraceId}
                    onChange={(e) => setReprocTraceId(e.target.value)}
                    dir="ltr"
                    placeholder="optional"
                    data-testid="reproc-trace-id-input"
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg font-mono text-xs"
                  />
                </div>

                <div>
                  <label className="block text-[12px] font-bold text-slate-700 mb-1">
                    للتأكيد، اكتب: <code className="font-mono text-rose-700">{expectedReprocToken || "REPROCESS-<order_number>"}</code>
                  </label>
                  <input
                    type="text"
                    value={reprocConfirm}
                    onChange={(e) => setReprocConfirm(e.target.value)}
                    dir="ltr"
                    placeholder={expectedReprocToken || "REPROCESS-..."}
                    data-testid="reproc-confirm-input"
                    className="w-full px-3 py-2 border-2 border-slate-300 rounded-lg font-mono text-sm focus:border-indigo-500 outline-none"
                  />
                </div>
              </div>
            )}

            {/* Success / failure result */}
            {reprocResult && (
              <div className="mt-4" data-testid="reproc-result">
                <div className={`rounded-lg border p-3 ${
                       reprocResult.outcome === "COMPLETED"
                         ? "border-emerald-300 bg-emerald-50"
                         : reprocResult.outcome === "ALREADY_COMPLETED"
                           ? "border-sky-300 bg-sky-50"
                           : "border-rose-300 bg-rose-50"}`}>
                  <div className="text-sm font-extrabold mb-1">
                    {reprocResult.outcome === "COMPLETED" && "✓ تم بنجاح"}
                    {reprocResult.outcome === "ALREADY_COMPLETED" && "ℹ مكتمل سابقاً"}
                    {!["COMPLETED", "ALREADY_COMPLETED"].includes(reprocResult.outcome) && `✗ فشل: ${reprocResult.outcome}`}
                  </div>
                  {reprocResult.trace_id && (
                    <div className="text-[11px] font-mono text-slate-600">
                      trace_id: {reprocResult.trace_id}
                    </div>
                  )}
                  {reprocResult.stage_sequence_observed && (
                    <div className="text-[12px] text-slate-700 mt-1"
                         data-testid="reproc-stage-sequence">
                      <strong>المراحل التي اجتازها:</strong>{" "}
                      {reprocResult.stage_sequence_observed.join(" → ") || "—"}
                    </div>
                  )}
                  {reprocResult.qoyod_invoice_id && (
                    <div className="text-[12px] font-mono text-slate-800 mt-1">
                      Invoice: {reprocResult.qoyod_invoice_id} ·
                      Invoice Payment: {reprocResult.qoyod_invoice_payment_id || "—"}
                      {reprocResult.qoyod_receipt_id && (
                        <> · Legacy Receipt: {reprocResult.qoyod_receipt_id}</>
                      )}
                    </div>
                  )}
                  {reprocResult.error && (
                    <div className="mt-2 text-[12px] text-rose-800">
                      <strong>{reprocResult.error.code}</strong>: {reprocResult.error.message}
                    </div>
                  )}
                  {reprocResult.failed_at_stage && (
                    <div className="text-[12px] text-slate-700 mt-1">
                      <strong>المرحلة التي فشل عندها:</strong>{" "}
                      <code className="font-mono">{reprocResult.failed_at_stage}</code>
                    </div>
                  )}
                </div>

                {/* Final invoice payload — labelled conditionally
                    based on whether the POST actually reached Qoyod.
                    Iter-290h.4 — the new payment-link diagnostics
                    carry `request_sent_to_qoyod` so we no longer
                    mislabel a real 422 from قيود as "halted". */}
                {(reprocResult.invoice_payload || reprocResult.request_body_json) && (
                  <div className="mt-3">
                    <div className="text-[11px] font-bold text-slate-600 mb-1">
                      {reprocResult.outcome === "COMPLETED"
                        ? "📦 جسم فاتورة قيود المُرسل (POST /invoices)"
                        : reprocResult.request_sent_to_qoyod
                          ? "📦 جسم الطلب المُرسل لقيود — قيود رفضه"
                          : "📦 جسم الطلب الذي تم إيقافه قبل إرسالها لقيود"}
                    </div>
                    <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-slate-900 text-slate-100 rounded p-2 max-h-72 overflow-auto"
                         dir="ltr"
                         data-testid="reproc-request-body-json">
{JSON.stringify(reprocResult.invoice_payload || reprocResult.request_body_json, null, 2)}
                    </pre>
                    {/* Iter-290h.6 — On the COMPLETED success path,
                        also render the /invoice_payments payload +
                        قيود response so the operator can verify the
                        payment-link actually landed (not just that
                        the invoice was created). Closes the loophole
                        the user spotted on 2026-06-28 where success
                        was attributed solely to the invoice POST. */}
                    {reprocResult.outcome === "COMPLETED"
                      && reprocResult.invoice_payment_payload && (
                      <div className="mt-3"
                           data-testid="reproc-invoice-payment-block">
                        <div className="text-[11px] font-bold text-emerald-700 mb-1">
                          💰 جسم سداد الفاتورة المُرسل (POST /invoice_payments) — نجح
                        </div>
                        <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-emerald-950 text-emerald-100 rounded p-2 max-h-72 overflow-auto"
                             dir="ltr"
                             data-testid="reproc-invoice-payment-body">
{JSON.stringify(reprocResult.invoice_payment_payload, null, 2)}
                        </pre>
                        {reprocResult.invoice_payment_response != null && (
                          <>
                            <div className="text-[11px] font-bold text-emerald-700 mt-2 mb-1">
                              📥 رد قيود على السداد
                            </div>
                            <pre className="text-[11px] font-mono whitespace-pre-wrap break-words bg-slate-100 text-slate-900 rounded p-2 max-h-48 overflow-auto"
                                 dir="ltr"
                                 data-testid="reproc-invoice-payment-response">
{JSON.stringify(reprocResult.invoice_payment_response, null, 2)}
                            </pre>
                          </>
                        )}
                      </div>
                    )}
                    {/* Iter-290h.4 — explicit diagnostics for the
                        payment-link step. Surfaces exactly what
                        قيود returned so the operator no longer has
                        to guess whether a request was sent at all. */}
                    {(reprocResult.failed_at_stage === "PAYMENT_LINK_FAILED"
                      || reprocResult.failed_at_stage === "PAYMENT_METHOD_MAPPING_MISSING") && (
                      <div className="mt-2 text-[11px] grid grid-cols-2 gap-1 bg-slate-50 dark:bg-slate-900 p-2 rounded border border-slate-200 dark:border-slate-700"
                           data-testid="reproc-payment-link-diagnostics">
                        <div className="font-bold text-slate-600">
                          هل تمت محاولة الإرسال؟
                        </div>
                        <div className={reprocResult.payment_post_attempted
                          ? "text-emerald-700" : "text-rose-700"}>
                          {reprocResult.payment_post_attempted ? "نعم" : "لا"}
                        </div>
                        <div className="font-bold text-slate-600">
                          هل وصل الطلب إلى قيود؟
                        </div>
                        <div className={reprocResult.request_sent_to_qoyod
                          ? "text-emerald-700" : "text-amber-700"}>
                          {reprocResult.request_sent_to_qoyod ? "نعم" : "لا"}
                        </div>
                        {reprocResult.qoyod_status_code != null && (
                          <>
                            <div className="font-bold text-slate-600">رمز الاستجابة من قيود</div>
                            <div className="font-mono">{reprocResult.qoyod_status_code}</div>
                          </>
                        )}
                        {reprocResult.qoyod_response && (
                          <>
                            <div className="font-bold text-slate-600">رد قيود</div>
                            <div className="font-mono text-rose-700 break-all">
                              {typeof reprocResult.qoyod_response === "string"
                                ? reprocResult.qoyod_response
                                : JSON.stringify(reprocResult.qoyod_response)}
                            </div>
                          </>
                        )}
                        {reprocResult.skip_reason && (
                          <>
                            <div className="font-bold text-slate-600">سبب التخطي</div>
                            <div className="text-amber-700">{reprocResult.skip_reason}</div>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}

                {/* Totals Guard refusal (Iter-273) — line item / total mismatch */}
                {reprocResult.totals_guard && (
                  <div className="mt-3 rounded-lg border border-orange-300 bg-orange-50 p-3"
                       data-testid="reproc-totals-guard">
                    <div className="text-sm font-extrabold text-orange-900 mb-1">
                      ⚠ Totals Guard أوقف الإرسال (لم يُلامس قيود)
                    </div>
                    <div className="text-[12px] font-mono text-orange-900 mb-2">
                      {reprocResult.totals_guard.code}
                    </div>
                    <div className="text-[12px] text-orange-900 mb-2">
                      {reprocResult.totals_guard.message}
                    </div>
                    {reprocResult.totals_guard.details && (
                      <ul className="text-[12px] space-y-0.5 text-orange-900 list-disc pe-5">
                        {Object.entries(reprocResult.totals_guard.details)
                          .filter(([k]) => k !== "parsed_items")
                          .map(([k, v]) => (
                            <li key={k}>
                              <strong>{k}:</strong>{" "}
                              <code className="font-mono">{
                                typeof v === "object" && v !== null
                                  ? JSON.stringify(v)
                                  : String(v)
                              }</code>
                            </li>
                          ))}
                      </ul>
                    )}
                    {reprocResult.totals_guard.details?.parsed_items && (
                      <details className="mt-2 text-[11px]">
                        <summary className="cursor-pointer font-bold text-orange-900">
                          parsed_items (ما وصلنا منه من Make)
                        </summary>
                        <pre className="mt-1 bg-slate-900 text-slate-100 p-2 rounded font-mono whitespace-pre-wrap break-words max-h-48 overflow-auto"
                             dir="ltr">
{JSON.stringify(reprocResult.totals_guard.details.parsed_items, null, 2)}
                        </pre>
                      </details>
                    )}
                    <div className="mt-2 text-[11px] text-orange-800">
                      الحل: راجع Make.com Runbook — يجب أن يُرسل
                      <code className="font-mono mx-1">data.items[]</code>
                      كاملاً وليس بنداً واحداً.
                    </div>
                  </div>
                )}

                {/* Stage-specific diagnostic — product_create failures */}
                {reprocResult.product_create && (
                  <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3"
                       data-testid="reproc-product-create-diagnostic">
                    <div className="text-sm font-extrabold text-amber-900 mb-1">
                      📦 تشخيص إنشاء المنتج في قيود (FAILED_PRODUCT)
                    </div>
                    <div className="text-[12px] font-mono text-amber-900 mb-2" dir="ltr">
                      {reprocResult.product_create.endpoint}
                      {reprocResult.product_create.status_code != null && (
                        <span> · HTTP {reprocResult.product_create.status_code}</span>
                      )}
                    </div>

                    {/* Sale-price fix verdict — most important line */}
                    <div className={`text-[12px] font-bold rounded p-2 ${
                          reprocResult.product_create.deploy_carries_full_fix
                            ? "bg-emerald-100 text-emerald-900 border border-emerald-300"
                            : "bg-rose-100 text-rose-900 border border-rose-300"
                        }`}
                        data-testid="reproc-sale-price-verdict">
                      {reprocResult.product_create.deploy_carries_full_fix
                        ? "✓ النشر يستخدم الإصلاح الكامل: selling_price + is_sold:true"
                        : "⚠ النشر ناقص — يحتاج: selling_price + is_sold:true"}
                    </div>

                    <ul className="mt-2 text-[12px] space-y-0.5 text-amber-900">
                      <li>
                        <strong>SKU:</strong>{" "}
                        <code className="font-mono">{reprocResult.product_create.sku_in_request_body || "—"}</code>
                        {" "}(من canonical:{" "}
                        <code className="font-mono">{reprocResult.product_create.expected_from_canonical?.sku || "—"}</code>)
                      </li>
                      <li>
                        <strong>selling_price field present (يجب true):</strong>{" "}
                        <code className={`font-mono ${reprocResult.product_create.selling_price_field_present ? "text-emerald-700" : "text-rose-700"}`}>
                          {String(reprocResult.product_create.selling_price_field_present)}
                        </code>
                      </li>
                      <li>
                        <strong>sale_price field present (يجب false — اسم خاطئ):</strong>{" "}
                        <code className={`font-mono ${reprocResult.product_create.sale_price_field_present ? "text-rose-700" : "text-emerald-700"}`}>
                          {String(reprocResult.product_create.sale_price_field_present)}
                        </code>
                      </li>
                      <li>
                        <strong>is_sold flag (يجب true):</strong>{" "}
                        <code className={`font-mono ${reprocResult.product_create.is_sold_flag === true ? "text-emerald-700" : "text-rose-700"}`}>
                          {String(reprocResult.product_create.is_sold_flag)}
                        </code>
                      </li>
                      <li>
                        <strong>selling_price المُرسل:</strong>{" "}
                        <code className="font-mono">{String(reprocResult.product_create.selling_price_in_request_body ?? "null")}</code>
                        {" "}(المتوقع:{" "}
                        <code className="font-mono">{String(reprocResult.product_create.expected_from_canonical?.selling_price_we_would_send ?? "null")}</code>)
                      </li>
                    </ul>

                    {reprocResult.product_create.request_body && (
                      <details className="mt-2 text-[11px]">
                        <summary className="cursor-pointer font-bold text-amber-900">
                          product_create_request_body (الـ payload الفعلي المُرسل لقيود)
                        </summary>
                        <pre className="mt-1 bg-slate-900 text-slate-100 p-2 rounded text-[11px] font-mono whitespace-pre-wrap break-words max-h-72 overflow-auto"
                             dir="ltr">
{JSON.stringify(reprocResult.product_create.request_body, null, 2)}
                        </pre>
                      </details>
                    )}

                    {reprocResult.product_create.response_excerpt && (
                      <details className="mt-2 text-[11px]" open>
                        <summary className="cursor-pointer font-bold text-amber-900">
                          product_create_response_body (ردّ قيود)
                        </summary>
                        <pre className="mt-1 bg-slate-900 text-amber-200 p-2 rounded text-[11px] font-mono whitespace-pre-wrap break-words max-h-48 overflow-auto"
                             dir="ltr">
{typeof reprocResult.product_create.response_excerpt === "string"
  ? reprocResult.product_create.response_excerpt
  : JSON.stringify(reprocResult.product_create.response_excerpt, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                )}

                {/* Dry-leak audit */}
                {reprocResult.dry_leaks_in_final_payload != null && (
                  <div className="mt-2 text-[12px]"
                       data-testid="reproc-dry-leak-audit">
                    <strong>فحص DRY: في الـ payload النهائي:</strong>{" "}
                    {reprocResult.dry_leaks_in_final_payload.length === 0
                      ? <span className="text-emerald-700 font-bold">✓ نظيف (0)</span>
                      : <span className="text-rose-700 font-bold">⚠ {reprocResult.dry_leaks_in_final_payload.join(", ")}</span>}
                  </div>
                )}

                {reprocResult.quarantine_summary && (
                  <details className="mt-2 text-[11px] text-slate-600">
                    <summary className="cursor-pointer font-bold">سجل العزل (DRY mappings)</summary>
                    <pre className="mt-1 bg-slate-100 p-2 rounded" dir="ltr">
{JSON.stringify(reprocResult.quarantine_summary, null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            )}

            {reprocError && (
              <div className="mt-4 rounded-lg border border-rose-300 bg-rose-50 p-3"
                   data-testid="reproc-error">
                <div className="text-sm font-extrabold text-rose-900">
                  ✗ الطلب مرفوض
                </div>
                <div className="text-[12px] font-mono text-rose-800 mt-1">
                  {reprocError.code}
                </div>
                <div className="text-[12px] text-rose-800 mt-1">
                  {reprocError.message}
                </div>
                {reprocError.expected_confirm_token && (
                  <div className="text-[11px] text-rose-700 mt-2">
                    تأكيد متوقّع: <code className="font-mono">{reprocError.expected_confirm_token}</code>
                  </div>
                )}
                {reprocError.candidates && (
                  <details className="mt-2 text-[11px]">
                    <summary className="cursor-pointer font-bold text-rose-800">
                      السجلات المطابقة ({reprocError.candidates.length})
                    </summary>
                    <ul className="mt-1 space-y-1 font-mono">
                      {reprocError.candidates.map((c, i) => (
                        <li key={i} className="text-rose-700">
                          {c.trace_id} · {c.pipeline_stage}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
                {reprocError.traceback_tail && (
                  <details className="mt-2 text-[11px]" data-testid="reproc-traceback">
                    <summary className="cursor-pointer font-bold text-rose-800">
                      Traceback (للتشخيص)
                    </summary>
                    <pre className="mt-1 bg-slate-900 text-slate-100 p-2 rounded text-[10px] font-mono whitespace-pre-wrap break-words max-h-72 overflow-auto"
                         dir="ltr">
{reprocError.traceback_tail}
                    </pre>
                  </details>
                )}
              </div>
            )}

            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                onClick={() => setReprocOpen(false)}
                disabled={reproc}
                data-testid="btn-reproc-cancel"
                className="px-4 py-2 text-sm font-bold rounded-lg border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {(reprocResult || reprocError) ? "إغلاق" : "إلغاء"}
              </button>
              {!reprocResult && !reprocError && (
                <button
                  type="button"
                  onClick={runReprocess}
                  disabled={reproc ||
                            !(reprocOrderNo || "").trim() ||
                            (reprocConfirm || "").trim() !== expectedReprocToken}
                  data-testid="btn-reproc-confirm"
                  className="px-4 py-2 text-sm font-extrabold rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40"
                >
                  {reproc ? "جاري المعالجة…" : "🎯 تنفيذ إعادة المعالجة"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
