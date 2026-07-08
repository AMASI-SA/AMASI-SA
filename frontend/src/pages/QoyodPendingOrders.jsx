/**
 * Plan-B Diagnostic — orders MISSING from Plan B pending.
 *
 * Historical note
 * ────────────────
 * This route used to render the legacy Rev32 "Qoyod Pending Orders"
 * screen. Since the legacy pipeline is now frozen, the URL has been
 * repurposed (per user directive 2026-02) into a READ-ONLY diagnostic
 * page that answers a SINGLE question for every Salla order:
 *
 *     "Why is this order NOT showing up in Plan B pending?"
 *
 * NO send button. NO approve/preview. Sending stays exclusive to
 * /admin/qoyod-manual-send.
 *
 * Server contract
 * ───────────────
 * GET /api/integrations/qoyod/manual/missing-from-plan-b
 *      ?days=90&limit=1000&include_already_sent=true
 * See backend/integrations/qoyod_manual/missing_diagnostics.py
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import api from "../lib/api";

const BASE = "/integrations/qoyod/manual";

// ── Missing-stage → Arabic label + badge colour ────────────────────
const STAGE_META = {
  already_sent_plan_b: {
    label: "أُرسل عبر Plan B",
    tone: "emerald",
  },
  already_sent_legacy: {
    label: "أُرسل عبر المسار القديم",
    tone: "sky",
  },
  already_in_qoyod: {
    label: "موجود في قيود (بدون علامة في ميزان)",
    tone: "amber",
  },
  missing_from_unified_orders: {
    label: "مفقود من unified_orders",
    tone: "orange",
  },
  missing_from_integration_inbox: {
    label: "مفقود من integration_inbox",
    tone: "rose",
  },
  filtered_by_policy: {
    label: "مستبعد بسبب سياسة",
    tone: "slate",
  },
  missing_from_plan_b_pending: {
    label: "لم يظهر في Plan B رغم توفّر الشروط",
    tone: "red",
  },
  unknown: {
    label: "غير معروف",
    tone: "purple",
  },
};

const REASON_LABELS = {
  before_floor_date:              "قبل تاريخ التكامل (< 2026-07-01)",
  no_salla_order_date:            "لا يوجد تاريخ إنشاء في سلة",
  already_sent:                   "أُرسل مسبقاً",
  duplicate_invoice_in_qoyod:     "توجد فاتورة في قيود",
  missing_from_unified_orders:    "غير موجود في unified_orders",
  missing_from_integration_inbox: "غير موجود في integration_inbox",
  status_not_supported_by_plan_b: "الحالة غير مدعومة في Plan B",
  unknown_reason:                 "سبب غير معروف — يحتاج مراجعة",
};

const TONE_CLASSES = {
  emerald: "bg-emerald-100 text-emerald-800 border-emerald-300",
  sky:     "bg-sky-100 text-sky-800 border-sky-300",
  amber:   "bg-amber-100 text-amber-900 border-amber-300",
  orange:  "bg-orange-100 text-orange-800 border-orange-300",
  rose:    "bg-rose-100 text-rose-800 border-rose-300",
  slate:   "bg-slate-100 text-slate-700 border-slate-300",
  red:     "bg-red-100 text-red-800 border-red-300",
  purple:  "bg-purple-100 text-purple-800 border-purple-300",
};

function fmtMoney(v, currency = "SAR") {
  if (v == null || v === "") return "—";
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  return `${n.toFixed(2)} ${currency === "SAR" ? "ر.س" : currency}`;
}

function extractDetail(err) {
  const d = err?.response?.data?.detail;
  if (!d) return err?.response?.data?.message || err?.message || "خطأ غير معروف";
  if (typeof d === "string") return d;
  return d.message || d.code || JSON.stringify(d);
}

function StageBadge({ stage }) {
  const meta = STAGE_META[stage] || STAGE_META.unknown;
  const cls = TONE_CLASSES[meta.tone] || TONE_CLASSES.slate;
  return (
    <span
      data-testid={`missing-stage-badge-${stage}`}
      className={`inline-block rounded-full border px-2.5 py-0.5 text-[11px] font-medium ${cls}`}
      title={stage}
    >
      {meta.label}
    </span>
  );
}

function YesNo({ value, testid }) {
  return (
    <span
      data-testid={testid}
      className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-mono ${
        value
          ? "bg-emerald-100 text-emerald-800"
          : "bg-rose-100 text-rose-800"
      }`}
    >
      {value ? "نعم" : "لا"}
    </span>
  );
}

export default function QoyodPendingOrders() {
  const PAGE_SIZE = 25;

  const [rows, setRows] = useState([]);
  const [counts, setCounts] = useState(null);
  const [byStage, setByStage] = useState({});
  const [byReason, setByReason] = useState({});
  const [floorDate, setFloorDate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Filters
  const [days, setDays] = useState(90);
  const [search, setSearch] = useState("");
  const [includeAlreadySent, setIncludeAlreadySent] = useState(true);
  const [stageFilter, setStageFilter] = useState("all");

  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = {
        days,
        limit: 2000,
        include_already_sent: includeAlreadySent,
      };
      if (search && search.trim()) params.search = search.trim();
      const res = await api.get(`${BASE}/missing-from-plan-b`, { params });
      setRows(res.data?.orders || []);
      setCounts(res.data?.counts || null);
      setByStage(res.data?.by_stage || {});
      setByReason(res.data?.by_reason || {});
      setFloorDate(res.data?.floor_date || null);
      setPage(1);
    } catch (e) {
      setError(extractDetail(e));
      setRows([]);
      setCounts(null);
      setByStage({});
      setByReason({});
    } finally {
      setLoading(false);
    }
  }, [days, search, includeAlreadySent]);

  useEffect(() => {
    load();
  }, [days, includeAlreadySent, load]);

  const filteredRows = useMemo(() => {
    if (stageFilter === "all") return rows;
    return rows.filter((r) => r.missing_stage === stageFilter);
  }, [rows, stageFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const start = (currentPage - 1) * PAGE_SIZE;
  const pageRows = filteredRows.slice(start, start + PAGE_SIZE);

  const stageChips = useMemo(() => {
    const keys = Object.keys(byStage);
    keys.sort((a, b) => (byStage[b] || 0) - (byStage[a] || 0));
    return keys;
  }, [byStage]);

  return (
    <div
      className="space-y-6"
      dir="rtl"
      data-testid="qoyod-pending-orders-page"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">
            🩺 تشخيص الطلبات غير الظاهرة في Plan B
          </h1>
          <p className="mt-1 text-sm text-slate-600 max-w-3xl">
            صفحة قراءة فقط: تعرض كل طلبات سلة (تم التنفيذ / جاري التوصيل / تم
            التوصيل) بتاريخ ≥ {floorDate || "2026-07-01"} التي{" "}
            <b>لا تظهر</b> في صفحة الإرسال اليدوي — مع بيان{" "}
            <b>مرحلة الاختفاء</b> والسبب المحدد لكل طلب.
          </p>
          <p className="mt-1 text-xs text-slate-500 max-w-3xl">
            🛑 لا زر إرسال في هذه الصفحة. الإرسال يبقى فقط من صفحة{" "}
            <span className="font-mono">/admin/qoyod-manual-send</span>.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            dir="ltr"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && load()}
            placeholder="بحث برقم الطلب…"
            data-testid="missing-search-input"
            className="w-44 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={load}
            data-testid="missing-search-btn"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
          >
            بحث
          </button>
          <select
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            data-testid="missing-days-select"
            className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value={30}>آخر 30 يوم</option>
            <option value={60}>آخر 60 يوم</option>
            <option value={90}>آخر 90 يوم</option>
            <option value={180}>آخر 180 يوم</option>
            <option value={365}>آخر سنة</option>
          </select>
          <label
            className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs"
            data-testid="missing-include-sent-toggle"
          >
            <input
              type="checkbox"
              checked={includeAlreadySent}
              onChange={(e) => setIncludeAlreadySent(e.target.checked)}
            />
            إظهار &quot;مُرسل&quot;
          </label>
          <button
            type="button"
            onClick={load}
            data-testid="missing-refresh-btn"
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-700"
          >
            تحديث
          </button>
        </div>
      </div>

      {/* Counters */}
      {counts && (
        <div
          className="grid grid-cols-2 gap-3 sm:grid-cols-4"
          data-testid="missing-counters"
        >
          {[
            ["الطلبات المفحوصة", counts.scanned ?? counts.universe_total],
            ["ظاهر في Plan B", counts.visible_in_plan_b],
            ["الظاهر هنا (غير ظاهر في Plan B)", counts.returned],
            ["الحد الأدنى للتاريخ", floorDate || "—"],
          ].map(([label, value]) => (
            <div
              key={label}
              className="rounded-xl border border-slate-200 bg-white px-4 py-3"
              data-testid={`missing-counter-${label}`}
            >
              <div className="text-xs text-slate-500">{label}</div>
              <div className="text-2xl font-semibold text-slate-900">
                {value ?? 0}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Stage histogram / filter chips */}
      {stageChips.length > 0 && (
        <div
          className="rounded-xl border border-slate-200 bg-white p-3"
          data-testid="missing-stage-histogram"
        >
          <div className="mb-2 text-xs font-semibold text-slate-600">
            🔎 تصفية حسب مرحلة الاختفاء:
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => setStageFilter("all")}
              data-testid="missing-stage-filter-all"
              className={`rounded-full border px-3 py-1 text-xs ${
                stageFilter === "all"
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-100"
              }`}
            >
              الكل ({rows.length})
            </button>
            {stageChips.map((s) => {
              const meta = STAGE_META[s] || STAGE_META.unknown;
              const active = stageFilter === s;
              const tone = TONE_CLASSES[meta.tone] || TONE_CLASSES.slate;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => setStageFilter(s)}
                  data-testid={`missing-stage-filter-${s}`}
                  className={`rounded-full border px-3 py-1 text-xs ${
                    active
                      ? "border-slate-900 bg-slate-900 text-white"
                      : tone
                  }`}
                >
                  {meta.label}
                  <span dir="ltr" className="ms-1 font-mono">
                    ({byStage[s] || 0})
                  </span>
                </button>
              );
            })}
          </div>
          {Object.keys(byReason).length > 0 && (
            <div className="mt-3 border-t border-slate-100 pt-2 text-[11px] text-slate-600">
              <div className="mb-1 font-semibold">توزيع الأسباب:</div>
              <div
                className="flex flex-wrap gap-2"
                data-testid="missing-reason-histogram"
              >
                {Object.entries(byReason)
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, v]) => (
                    <span
                      key={k}
                      className="rounded border border-slate-200 bg-slate-50 px-2 py-0.5"
                    >
                      {REASON_LABELS[k] || k}
                      <span dir="ltr" className="ms-1 font-mono">
                        ({v})
                      </span>
                    </span>
                  ))}
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700"
          data-testid="missing-error"
        >
          {String(error)}
        </div>
      )}

      {/* Table */}
      <div
        className="overflow-x-auto rounded-xl border border-slate-200 bg-white"
        data-testid="missing-orders-table"
      >
        {loading ? (
          <div className="p-6 text-sm text-slate-500">جاري التحميل…</div>
        ) : filteredRows.length === 0 ? (
          <div
            className="p-6 text-sm text-slate-500"
            data-testid="missing-empty"
          >
            ✅ لا توجد طلبات مفقودة ضمن هذه المعايير.
          </div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-slate-600">
                <tr>
                  <th className="px-3 py-2 text-right">رقم الطلب</th>
                  <th className="px-3 py-2 text-right">حالة سلة</th>
                  <th className="px-3 py-2 text-right">
                    تاريخ إنشاء الطلب في سلة
                  </th>
                  <th className="px-3 py-2 text-right">طريقة الدفع</th>
                  <th className="px-3 py-2 text-right">مبلغ سلة</th>
                  <th className="px-3 py-2 text-right">فاتورة قيود؟</th>
                  <th className="px-3 py-2 text-right">
                    ظاهر في Plan B؟
                  </th>
                  <th className="px-3 py-2 text-right">
                    مرحلة الاختفاء
                  </th>
                  <th className="px-3 py-2 text-right">السبب</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((o) => (
                  <tr
                    key={`${o.order_number}-${o.missing_stage}`}
                    className="border-t border-slate-100 hover:bg-slate-50"
                    data-testid={`missing-row-${o.order_number}`}
                  >
                    <td className="px-3 py-2 font-medium">
                      <div>{o.order_number}</div>
                      {o.trace_id && (
                        <div
                          dir="ltr"
                          className="text-[10px] font-mono text-slate-400"
                        >
                          {String(o.trace_id).slice(0, 12)}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <span className="inline-block rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-xs text-sky-800">
                        {o.salla_status || "—"}
                      </span>
                    </td>
                    <td
                      className="px-3 py-2 text-slate-600 font-mono"
                      dir="ltr"
                      data-testid={`missing-salla-date-${o.order_number}`}
                    >
                      {o.salla_created_date || "—"}
                    </td>
                    <td className="px-3 py-2 text-slate-700">
                      {o.payment_method || "—"}
                    </td>
                    <td className="px-3 py-2 font-mono" dir="ltr">
                      {fmtMoney(o.total_amount, o.currency)}
                    </td>
                    <td
                      className="px-3 py-2 text-center"
                      data-testid={`missing-has-invoice-${o.order_number}`}
                    >
                      {o.has_qoyod_invoice ? (
                        <span
                          className="inline-block rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-mono text-emerald-800"
                          title={o.qoyod_invoice_id || ""}
                        >
                          نعم
                          {o.qoyod_invoice_number && (
                            <span className="ms-1 text-emerald-700">
                              #{o.qoyod_invoice_number}
                            </span>
                          )}
                        </span>
                      ) : (
                        <span className="inline-block rounded bg-slate-100 px-1.5 py-0.5 text-[11px] font-mono text-slate-500">
                          لا
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-center">
                      <YesNo
                        value={o.visible_in_plan_b}
                        testid={`missing-visible-${o.order_number}`}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <StageBadge stage={o.missing_stage} />
                      <div className="mt-1 flex flex-wrap gap-1 text-[10px] text-slate-500">
                        <span className="rounded bg-slate-100 px-1.5 py-0.5">
                          unified:{" "}
                          {o.in_unified_orders ? "✓" : "✗"}
                        </span>
                        <span className="rounded bg-slate-100 px-1.5 py-0.5">
                          inbox:{" "}
                          {o.in_integration_inbox ? "✓" : "✗"}
                        </span>
                        {o.marker_source && o.marker_source !== "none" && (
                          <span className="rounded bg-slate-100 px-1.5 py-0.5">
                            marker: {o.marker_source}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="px-3 py-2 text-slate-700 text-xs">
                      <div>{REASON_LABELS[o.reason] || o.reason}</div>
                      <div
                        dir="ltr"
                        className="mt-0.5 font-mono text-[10px] text-slate-400"
                      >
                        {o.reason}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div
              className="flex items-center justify-between gap-3 border-t border-slate-100 bg-slate-50 px-3 py-2 text-sm"
              data-testid="missing-pagination"
            >
              <div className="text-slate-500">
                عرض{" "}
                <span dir="ltr" className="font-mono">
                  {start + 1}–{Math.min(start + PAGE_SIZE, filteredRows.length)}
                </span>{" "}
                من{" "}
                <span dir="ltr" className="font-mono">
                  {filteredRows.length}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={currentPage <= 1}
                  data-testid="missing-prev-page"
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
                  data-testid="missing-page-indicator"
                >
                  {currentPage} / {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={currentPage >= totalPages}
                  data-testid="missing-next-page"
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
        )}
      </div>
    </div>
  );
}
