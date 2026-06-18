// Iter-246k — Suppliers analytical report.
// Reads `/api/reports/suppliers` and lays out a sortable table with
// every column the merchant requested + filter controls.

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";

const errMsg = (e, fb) =>
  e?.response?.data?.detail || e?.message || fb;

const fmt = (n) =>
  Number(n || 0).toLocaleString("en-US",
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function fmtDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toISOString().slice(0, 10);
  } catch {
    return iso;
  }
}

export default function SuppliersReportPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [withDebtOnly, setWithDebtOnly] = useState(false);
  const [categoryId, setCategoryId] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [categories, setCategories] = useState([]);

  async function load() {
    setLoading(true);
    try {
      const params = {};
      if (q) params.q = q;
      if (status && status !== "all") params.status = status;
      if (withDebtOnly) params.with_debt_only = "true";
      if (categoryId) params.category_id = categoryId;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;
      const { data } = await api.get("/reports/suppliers", { params });
      setData(data);
    } catch (e) {
      toast.error(errMsg(e, "فشل تحميل التقرير"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);
  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get(
          "/expense-category-tree",
          { params: { movement_type: "supplier_invoice",
                      include_inactive: "false" } });
        setCategories(data.items || []);
      } catch { /* non-blocking */ }
    })();
  }, []);

  const totals = data?.totals;
  const rows = data?.suppliers || [];

  const summary = useMemo(() => [
    { label: "عدد الموردين", value: totals?.suppliers_count ?? 0,
      tone: "slate", testid: "report-total-suppliers" },
    { label: "إجمالي الفواتير", value: fmt(totals?.invoices_total),
      tone: "indigo", suffix: "ر.س",
      testid: "report-total-invoices" },
    { label: "إجمالي المدفوع", value: fmt(totals?.paid_total),
      tone: "emerald", suffix: "ر.س",
      testid: "report-total-paid" },
    { label: "إجمالي المستحق", value: fmt(totals?.outstanding_debt),
      tone: "rose", suffix: "ر.س",
      testid: "report-total-outstanding" },
  ], [totals]);

  return (
    <div className="space-y-5 p-2" dir="rtl"
         data-testid="suppliers-report-page">
      <header className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">🏷️ تقرير الموردين التفصيلي</h1>
          <p className="text-sm text-gray-600 mt-1 leading-7">
            مصدر البيانات: <code>financial_movements</code> +{" "}
            <code>general_ledger</code> + <code>counterparties</code>.{" "}
            المستحق محسوب من قيد المورد في الـ Ledger ليطابق دفتر المورد
            وصفحة سداد المورد.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="bg-gray-100 hover:bg-gray-200 px-4 py-2 rounded text-sm font-bold"
          data-testid="report-refresh">
          {loading ? "..." : "تحديث"}
        </button>
      </header>

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {summary.map((c) => (
          <div key={c.label} data-testid={c.testid}
               className={`rounded-lg border p-4 bg-${c.tone}-50 text-${c.tone}-900`}>
            <p className="text-[11px] font-bold opacity-80">{c.label}</p>
            <p className="text-2xl font-extrabold num mt-1">
              {c.value}
              {c.suffix && <span className="text-xs mr-1 opacity-70">{c.suffix}</span>}
            </p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-6 gap-2 border rounded-lg p-3 bg-white">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="بحث بالاسم..."
          className="border rounded px-3 py-2 text-sm md:col-span-2"
          data-testid="report-filter-q" />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="report-filter-status">
          <option value="all">كل الحالات</option>
          <option value="active">نشط</option>
          <option value="inactive">موقوف</option>
        </select>
        <select
          value={categoryId}
          onChange={(e) => setCategoryId(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="report-filter-cat">
          <option value="">كل التصنيفات</option>
          {categories.map((c) => (
            <option key={c.id} value={c.id}>
              {(c.path || [c.name]).join(" › ")}
            </option>
          ))}
        </select>
        <input
          type="date"
          dir="ltr"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="report-filter-from"
          placeholder="من" />
        <input
          type="date"
          dir="ltr"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="border rounded px-3 py-2 text-sm"
          data-testid="report-filter-to"
          placeholder="إلى" />
        <label className="flex items-center gap-2 text-xs col-span-1 md:col-span-1">
          <input
            type="checkbox"
            checked={withDebtOnly}
            onChange={(e) => setWithDebtOnly(e.target.checked)}
            data-testid="report-filter-debt" />
          مستحق فقط
        </label>
        <button
          type="button"
          onClick={load}
          className="bg-emerald-600 hover:bg-emerald-700 text-white px-3 py-2 rounded text-xs font-bold col-span-1 md:col-span-1"
          data-testid="report-apply">
          تطبيق
        </button>
      </div>

      {/* Table */}
      <div className="border rounded-lg overflow-x-auto bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-50 text-right">
            <tr>
              <th className="p-2">المورد</th>
              <th className="p-2">الجوال</th>
              <th className="p-2">الحالة</th>
              <th className="p-2 text-center">عدد الفواتير</th>
              <th className="p-2 text-end">إجمالي</th>
              <th className="p-2 text-end">مدفوع</th>
              <th className="p-2 text-end">المتبقي</th>
              <th className="p-2 text-end">المستحق (Ledger)</th>
              <th className="p-2">آخر فاتورة</th>
              <th className="p-2">آخر حركة</th>
              <th className="p-2">التصنيفات</th>
              <th className="p-2"></th>
            </tr>
          </thead>
          <tbody data-testid="report-table-body">
            {rows.length === 0 && (
              <tr><td colSpan={12} className="p-6 text-center text-slate-500">
                لا توجد بيانات تطابق الفلاتر.
              </td></tr>
            )}
            {rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-amber-50"
                  data-testid={`report-row-${r.id}`}>
                <td className="p-2 font-bold">{r.name}</td>
                <td className="p-2 font-mono text-xs" dir="ltr">{r.phone || "—"}</td>
                <td className="p-2">
                  <span className={
                    "text-[10px] font-bold px-1.5 py-0.5 rounded "
                    + (r.status === "active"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-gray-200 text-gray-700")
                  }>
                    {r.status === "active" ? "نشط" : "موقوف"}
                  </span>
                </td>
                <td className="p-2 text-center font-bold">{r.invoices_count}</td>
                <td className="p-2 text-end num">{fmt(r.invoices_total)}</td>
                <td className="p-2 text-end num text-emerald-700">{fmt(r.paid_total)}</td>
                <td className="p-2 text-end num text-amber-700">{fmt(r.remaining_total)}</td>
                <td className="p-2 text-end num font-bold text-rose-700"
                    data-testid={`report-outstanding-${r.id}`}>
                  {fmt(r.outstanding_debt)}
                </td>
                <td className="p-2 text-xs">
                  {fmtDate(r.last_invoice_date)}
                  {r.last_invoice_doc_number && (
                    <span className="block text-[10px] text-slate-500">
                      #{r.last_invoice_doc_number}
                    </span>
                  )}
                </td>
                <td className="p-2 text-xs">{fmtDate(r.last_activity)}</td>
                <td className="p-2 text-[10px]">
                  {(r.categories || []).slice(0, 3).map((c) => (
                    <span key={c.id}
                          className="inline-block bg-violet-50 text-violet-800 px-1.5 py-0.5 rounded ms-1 mb-1">
                      {c.name}
                    </span>
                  ))}
                  {(r.categories || []).length > 3 && (
                    <span className="text-slate-500">
                      +{r.categories.length - 3}
                    </span>
                  )}
                </td>
                <td className="p-2">
                  <Link
                    to={r.ledger_url}
                    className="text-xs text-blue-700 hover:underline"
                    data-testid={`report-ledger-link-${r.id}`}>
                    دفتر المورد ↗
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
