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

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const [rowsRes, wsRes] = await Promise.all([
        axios.get(`${API}/integrations/qoyod/first-sync-monitor?limit=${limit}`),
        axios.get(`${API}/integrations/qoyod/worker/status`).catch(() => ({ data: null })),
      ]);
      setRows(rowsRes.data?.rows || []);
      setWorkerStatus(wsRes.data?.worker || null);
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
    </div>
  );
}
