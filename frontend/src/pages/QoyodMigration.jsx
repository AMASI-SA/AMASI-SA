/**
 * Qoyod Existing-Data Migration — read-only reconciliation UI.
 *
 *   POST /api/integrations/qoyod/migration/run
 *   GET  /api/integrations/qoyod/migration/status
 *   GET  /api/integrations/qoyod/migration/report
 *   GET  /api/integrations/qoyod/migration/{kind}
 *   POST /api/integrations/qoyod/migration/{kind}/confirm
 *   GET  /api/integrations/qoyod/migration/{kind}/export.csv
 *
 * Strictly read-only on Qoyod. Renders the matching report and lets
 * the user manually confirm `candidate_match` rows before any Dry Run.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";

const API = process.env.REACT_APP_BACKEND_URL + "/api";
const M_API = `${API}/integrations/qoyod/migration`;

const STATUS_META = {
  auto_mapped:         { label: "مربوط تلقائياً",   tone: "emerald" },
  mapped_with_warning: { label: "مربوط مع تنبيه",   tone: "amber"   },
  candidate_match:     { label: "يحتاج مراجعة",     tone: "rose"    },
  unmapped:            { label: "غير مربوط",         tone: "slate"   },
};

function StatCard({ label, value, tone = "slate", testid, sub }) {
  const palette =
    tone === "emerald" ? "text-emerald-700 bg-emerald-50 border-emerald-200" :
    tone === "amber"   ? "text-amber-800 bg-amber-50 border-amber-200"       :
    tone === "rose"    ? "text-rose-800 bg-rose-50 border-rose-200"          :
    tone === "blue"    ? "text-blue-800 bg-blue-50 border-blue-200"          :
                         "text-slate-800 bg-white border-slate-200";
  return (
    <div className={`flex flex-col gap-1 p-3 rounded-lg border ${palette}`}
         data-testid={testid}>
      <span className="text-[11px] font-bold opacity-75">{label}</span>
      <span className="text-2xl font-extrabold tabular-nums">{value ?? "—"}</span>
      {sub && <span className="text-[10px] opacity-70">{sub}</span>}
    </div>
  );
}

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || { label: status, tone: "slate" };
  const cls =
    meta.tone === "emerald" ? "bg-emerald-100 text-emerald-800 border-emerald-300" :
    meta.tone === "amber"   ? "bg-amber-100 text-amber-800 border-amber-300"       :
    meta.tone === "rose"    ? "bg-rose-100 text-rose-800 border-rose-300"          :
                              "bg-slate-100 text-slate-700 border-slate-300";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full
                      text-[11px] font-bold border ${cls}`}
          data-testid={`status-badge-${status}`}>
      {meta.label}
    </span>
  );
}

function ReportPanel({ report, runId, finishedAt }) {
  if (!report) {
    return (
      <div className="p-4 rounded-lg border border-slate-200 bg-slate-50 text-slate-600 text-sm"
           data-testid="migration-report-empty">
        لم يتم تنفيذ أي مزامنة قراءة بعد. ابدأ بزر &quot;تشغيل المزامنة&quot; أعلاه.
      </div>
    );
  }
  const totalReview = report.needs_manual_review || 0;
  const blockingForGoLive =
    (report.products_unmapped || 0) +
    (report.customers_unmapped || 0) +
    totalReview;
  return (
    <div className="space-y-4" data-testid="migration-report">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-sm text-slate-600">
          آخر تشغيل: <span className="font-mono">{runId}</span>
          {finishedAt && (
            <> • انتهى: {new Date(finishedAt).toLocaleString("ar-SA")}</>
          )}
        </div>
        <div className="text-sm font-bold">
          {blockingForGoLive === 0
            ? <span className="text-emerald-700">جاهز للـ Dry Run ✓</span>
            : <span className="text-rose-700">
                {blockingForGoLive} عنصر يحتاج مراجعة قبل Dry Run
              </span>}
        </div>
      </div>

      <div>
        <div className="text-xs font-bold text-slate-500 mb-2 uppercase tracking-wide">
          المنتجات
        </div>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          <StatCard testid="stat-products-mapped"
            label="مربوط" tone="emerald"
            value={report.products_mapped} />
          <StatCard testid="stat-products-warning"
            label="مربوط مع تنبيه" tone="amber"
            value={report.products_mapped_with_warning} />
          <StatCard testid="stat-products-candidate"
            label="يحتاج مراجعة" tone="rose"
            value={report.products_candidate} />
          <StatCard testid="stat-products-unmapped"
            label="غير مربوط" tone="slate"
            value={report.products_unmapped} />
          <StatCard testid="stat-products-sku-mismatch"
            label="اختلاف بيانات SKU" tone="amber"
            value={report.products_sku_mismatch_warnings}
            sub="ضمن المربوط مع تنبيه" />
        </div>
      </div>

      <div>
        <div className="text-xs font-bold text-slate-500 mb-2 uppercase tracking-wide">
          العملاء
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <StatCard testid="stat-customers-mapped"
            label="مربوط" tone="emerald"
            value={report.customers_mapped} />
          <StatCard testid="stat-customers-candidate"
            label="يحتاج مراجعة" tone="rose"
            value={report.customers_candidate} />
          <StatCard testid="stat-customers-unmapped"
            label="غير مربوط" tone="slate"
            value={report.customers_unmapped} />
          <StatCard testid="stat-needs-manual-review"
            label="إجمالي مراجعة يدوية" tone="rose"
            value={totalReview}
            sub="منتجات + عملاء" />
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 pt-1">
        <StatCard testid="stat-qoyod-products"
          label="منتجات قيود المستوردة" tone="blue"
          value={report.qoyod_products_imported} />
        <StatCard testid="stat-qoyod-customers"
          label="عملاء قيود المستوردين" tone="blue"
          value={report.qoyod_customers_imported} />
        <StatCard testid="stat-mezan-products"
          label="منتجات ميزان المميزة" tone="slate"
          value={report.mezan_products_distinct} />
        <StatCard testid="stat-mezan-customers"
          label="عملاء ميزان المميزون" tone="slate"
          value={report.mezan_customers_distinct} />
      </div>
    </div>
  );
}

function MappingTable({ kind, version }) {
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [confirmingKey, setConfirmingKey] = useState(null);
  const pageSize = 50;

  const load = async () => {
    setLoading(true);
    try {
      const params = { page, page_size: pageSize };
      if (statusFilter) params.status = statusFilter;
      if (search)       params.search = search;
      const r = await axios.get(`${M_API}/${kind}`, { params });
      setRows(r.data?.rows || []);
      setTotal(r.data?.total || 0);
    } catch (e) {
      toast.error("تعذّر تحميل جدول المطابقة");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); },
           [kind, page, statusFilter, version]);

  const onSearch = (e) => { e.preventDefault(); setPage(1); load(); };

  const onConfirm = async (row) => {
    const candidate = row.candidate_qoyod_id;
    if (!candidate) { toast.error("لا يوجد مرشّح لتأكيده"); return; }
    setConfirmingKey(row.mezan_key);
    try {
      await axios.post(`${M_API}/${kind}/confirm`, {
        mezan_key: row.mezan_key, qoyod_id: candidate,
      });
      toast.success("تم تأكيد الربط");
      await load();
    } catch (e) {
      toast.error("تعذّر تأكيد الربط");
    } finally {
      setConfirmingKey(null);
    }
  };

  const exportUrl = useMemo(() => {
    const u = new URL(`${M_API}/${kind}/export.csv`);
    if (statusFilter) u.searchParams.set("status", statusFilter);
    return u.toString();
  }, [kind, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="space-y-3" data-testid={`mapping-table-${kind}`}>
      <div className="flex flex-wrap gap-2 items-center">
        <select
          className="px-2 py-1.5 text-sm border border-slate-300 rounded-md bg-white"
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          data-testid={`filter-status-${kind}`}
        >
          <option value="">كل الحالات</option>
          <option value="auto_mapped">مربوط تلقائياً</option>
          <option value="mapped_with_warning">مربوط مع تنبيه</option>
          <option value="candidate_match">يحتاج مراجعة</option>
          <option value="unmapped">غير مربوط</option>
        </select>
        <form onSubmit={onSearch} className="flex gap-1">
          <input
            value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder={kind === "products" ? "بحث SKU / اسم" : "بحث اسم / هاتف / إيميل"}
            className="px-2 py-1.5 text-sm border border-slate-300 rounded-md w-64"
            data-testid={`search-${kind}`}
          />
          <button type="submit"
                  className="px-3 py-1.5 text-sm bg-slate-900 text-white rounded-md hover:bg-slate-800"
                  data-testid={`search-btn-${kind}`}>
            بحث
          </button>
        </form>
        <div className="flex-1" />
        <a href={exportUrl} target="_blank" rel="noreferrer"
           className="px-3 py-1.5 text-sm border border-slate-300 rounded-md bg-white hover:bg-slate-50"
           data-testid={`export-csv-${kind}`}>
          ⬇ تصدير CSV
        </a>
        <div className="text-xs text-slate-500">
          {loading ? "جاري التحميل…" : `${total} عنصر`}
        </div>
      </div>

      <div className="overflow-auto border border-slate-200 rounded-lg">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-700 text-xs">
            <tr>
              {kind === "products" ? (
                <>
                  <th className="text-right p-2">SKU (ميزان)</th>
                  <th className="text-right p-2">الاسم (ميزان)</th>
                  <th className="text-right p-2">السعر</th>
                  <th className="text-right p-2">التكرار</th>
                  <th className="text-right p-2">الحالة</th>
                  <th className="text-right p-2">معرّف قيود</th>
                  <th className="text-right p-2">الاسم في قيود</th>
                  <th className="text-right p-2">إجراء</th>
                </>
              ) : (
                <>
                  <th className="text-right p-2">الاسم (ميزان)</th>
                  <th className="text-right p-2">الهاتف</th>
                  <th className="text-right p-2">الإيميل</th>
                  <th className="text-right p-2">التكرار</th>
                  <th className="text-right p-2">الحالة</th>
                  <th className="text-right p-2">معرّف قيود</th>
                  <th className="text-right p-2">الاسم في قيود</th>
                  <th className="text-right p-2">إجراء</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const snap = r.qoyod_snapshot || {};
              const cid  = kind === "products" ? r.qoyod_product_id : r.qoyod_customer_id;
              const isCand = r.status === "candidate_match" && r.candidate_qoyod_id;
              return (
                <tr key={`${r.mezan_key}-${i}`}
                    className="border-t border-slate-100 hover:bg-slate-50/50"
                    data-testid={`row-${kind}-${i}`}>
                  {kind === "products" ? (
                    <>
                      <td className="p-2 font-mono">{r.mezan_sku || "—"}</td>
                      <td className="p-2">{r.mezan_name || "—"}</td>
                      <td className="p-2 tabular-nums">{r.mezan_unit_price ?? "—"}</td>
                      <td className="p-2 tabular-nums">{r.occurrences ?? 0}</td>
                      <td className="p-2">
                        <StatusBadge status={r.status} />
                        {(r.warnings || []).length > 0 && (
                          <div className="text-[10px] text-amber-700 mt-1">
                            {r.warnings.join("، ")}
                          </div>
                        )}
                      </td>
                      <td className="p-2 font-mono text-xs">{cid || (isCand ? `(مرشّح: ${r.candidate_qoyod_id})` : "—")}</td>
                      <td className="p-2 text-xs">{snap.name || "—"}</td>
                    </>
                  ) : (
                    <>
                      <td className="p-2">{r.mezan_name || "—"}</td>
                      <td className="p-2 font-mono text-xs">{r.mezan_phone || "—"}</td>
                      <td className="p-2 text-xs">{r.mezan_email || "—"}</td>
                      <td className="p-2 tabular-nums">{r.occurrences ?? 0}</td>
                      <td className="p-2">
                        <StatusBadge status={r.status} />
                        {(r.warnings || []).length > 0 && (
                          <div className="text-[10px] text-amber-700 mt-1">
                            {r.warnings.join("، ")}
                          </div>
                        )}
                      </td>
                      <td className="p-2 font-mono text-xs">{cid || (isCand ? `(مرشّح: ${r.candidate_qoyod_id})` : "—")}</td>
                      <td className="p-2 text-xs">{snap.name || "—"}</td>
                    </>
                  )}
                  <td className="p-2">
                    {isCand && (
                      <button
                        onClick={() => onConfirm(r)}
                        disabled={confirmingKey === r.mezan_key}
                        className="px-2 py-1 text-[11px] bg-emerald-600 text-white rounded hover:bg-emerald-700 disabled:opacity-50"
                        data-testid={`confirm-${kind}-${i}`}>
                        {confirmingKey === r.mezan_key ? "..." : "تأكيد الربط"}
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && !loading && (
              <tr><td colSpan={8} className="p-6 text-center text-slate-500">
                لا توجد نتائج لهذه الفلاتر.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-xs">
        <button
          onClick={() => setPage((p) => Math.max(1, p - 1))}
          disabled={page <= 1}
          className="px-3 py-1.5 border border-slate-300 rounded-md bg-white disabled:opacity-40"
          data-testid={`prev-page-${kind}`}>السابق</button>
        <span>صفحة {page} من {totalPages}</span>
        <button
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          disabled={page >= totalPages}
          className="px-3 py-1.5 border border-slate-300 rounded-md bg-white disabled:opacity-40"
          data-testid={`next-page-${kind}`}>التالي</button>
      </div>
    </div>
  );
}

export default function QoyodMigration() {
  const [report,     setReport]     = useState(null);
  const [runId,      setRunId]      = useState(null);
  const [finishedAt, setFinishedAt] = useState(null);
  const [running,    setRunning]    = useState(false);
  const [loading,    setLoading]    = useState(true);
  const [tab,        setTab]        = useState("products");
  const [dataVersion, setDataVersion] = useState(0); // bumped to refresh tables

  const loadStatus = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${M_API}/status`);
      const run = r.data?.run;
      setReport(run?.summary || null);
      setRunId(run?.run_id || null);
      setFinishedAt(run?.finished_at || null);
    } catch (e) {
      // no run yet — silent
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadStatus(); }, []);

  const onRun = async () => {
    setRunning(true);
    try {
      const r = await axios.post(`${M_API}/run`, {});
      if (r.data?.ok) {
        toast.success("اكتملت قراءة قيود ومطابقة البيانات");
        setReport(r.data.summary || null);
        setRunId(r.data.run_id || null);
        setFinishedAt(r.data.finished_at || null);
        setDataVersion((v) => v + 1);
      } else {
        toast.error(`فشل: ${r.data?.error?.message || "خطأ غير معروف"}`);
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      toast.error(detail?.message || "تعذّر بدء المزامنة");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="max-w-7xl mx-auto p-4 sm:p-6 space-y-6"
         data-testid="qoyod-migration-page">
      <div>
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">
              مرحلة الانتقال — قراءة بيانات قيود ومطابقتها
            </h1>
            <p className="text-sm text-slate-600 mt-1">
              قراءة فقط من قيود. لا يتم إنشاء أي منتج أو عميل جديد في هذه المرحلة.
              نقوم فقط ببناء جدول ربط محلي وتقرير اختلافات قبل أي Dry Run.
            </p>
          </div>
          <div className="flex gap-2">
            <Link to="/integrations/qoyod/settings"
                  className="px-3 py-2 text-sm border border-slate-300 rounded-md bg-white hover:bg-slate-50"
                  data-testid="link-qoyod-settings">إعدادات قيود</Link>
            <Link to="/integrations/qoyod/go-live"
                  className="px-3 py-2 text-sm border border-slate-300 rounded-md bg-white hover:bg-slate-50"
                  data-testid="link-qoyod-go-live">جاهزية الإنتاج</Link>
            <button
              onClick={onRun} disabled={running}
              className="px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-md hover:bg-black disabled:opacity-50"
              data-testid="btn-run-migration">
              {running ? "جاري التنفيذ…" : "▶ تشغيل المزامنة (قراءة)"}
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-4">
        {loading
          ? <div className="text-sm text-slate-500" data-testid="loading-report">
              جاري التحميل…
            </div>
          : <ReportPanel report={report} runId={runId}
                         finishedAt={finishedAt} />}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white">
        <div className="flex border-b border-slate-200">
          <button
            onClick={() => setTab("products")}
            className={`px-4 py-3 text-sm font-bold border-b-2 ${
              tab === "products"
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
            data-testid="tab-products">
            المنتجات
          </button>
          <button
            onClick={() => setTab("customers")}
            className={`px-4 py-3 text-sm font-bold border-b-2 ${
              tab === "customers"
                ? "border-slate-900 text-slate-900"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
            data-testid="tab-customers">
            العملاء
          </button>
        </div>
        <div className="p-4">
          {tab === "products"
            ? <MappingTable kind="products"  version={dataVersion} />
            : <MappingTable kind="customers" version={dataVersion} />}
        </div>
      </div>

      <div className="text-[11px] text-slate-500 leading-relaxed">
        قاعدة هذه المرحلة: قراءة فقط من قيود — لا يتم إنشاء أو تعديل أي كيان في قيود.
        يتم بناء جدول mapping محلي فقط. بعد اعتماد التقرير ننتقل إلى Dry Run على البيانات الحقيقية.
      </div>
    </div>
  );
}
