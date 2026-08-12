import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowClockwise,
  FileXls,
  MagnifyingGlass,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import api from "../lib/api";
import { todaySA } from "../lib/dates";

const QOYOD_BASE = "/integrations/qoyod/invoice-review";
const SYNC_START_DATE = "2026-07-01";
const PAGE_SIZE = 15;

const STATUS_LABELS = {
  paid: "مدفوعة",
  partial: "مدفوعة جزئياً",
  partially_paid: "مدفوعة جزئياً",
  "partially paid": "مدفوعة جزئياً",
  unpaid: "غير مدفوعة",
  approved: "معتمدة",
  draft: "مسودة",
  void: "ملغاة",
};

const STATUS_STYLES = {
  paid: "border-emerald-300 bg-emerald-50 text-emerald-800",
  partial: "border-amber-300 bg-amber-50 text-amber-800",
  partially_paid: "border-amber-300 bg-amber-50 text-amber-800",
  "partially paid": "border-amber-300 bg-amber-50 text-amber-800",
  unpaid: "border-rose-300 bg-rose-50 text-rose-800",
  approved: "border-sky-300 bg-sky-50 text-sky-800",
  draft: "border-slate-300 bg-slate-50 text-slate-700",
  void: "border-slate-300 bg-slate-50 text-slate-700",
};

const money = (value) => {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "—";
  return numeric.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
};

const moneyWithCurrency = (value, currency) => {
  const formatted = money(value);
  if (formatted === "—") return formatted;
  return currency ? `${formatted} ${currency}` : formatted;
};

const dateOnly = (value) => (value ? String(value).slice(0, 10) : "—");

function errorMessage(error, fallback) {
  const detail = error?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return detail?.message || error?.response?.data?.error || error?.message || fallback;
}

function SummaryCard({ label, value, hint, tone }) {
  const color = tone === "emerald"
    ? "border-emerald-200 bg-emerald-50 text-emerald-950"
    : "border-sky-200 bg-sky-50 text-sky-950";
  return (
    <div className={`rounded-2xl border p-4 ${color}`}>
      <div className="text-xs font-extrabold opacity-70">{label}</div>
      <div className="mt-2 text-3xl font-black" dir="ltr">{value ?? "—"}</div>
      <div className="mt-1 text-xs font-semibold opacity-70">{hint}</div>
    </div>
  );
}

export default function QoyodInvoiceReview() {
  const [fromDate, setFromDate] = useState(SYNC_START_DATE);
  const [toDate, setToDate] = useState(todaySA());
  const [searchInput, setSearchInput] = useState("");
  const [filters, setFilters] = useState({
    fromDate: SYNC_START_DATE,
    toDate: todaySA(),
    search: "",
  });
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");
  const [syncSummary, setSyncSummary] = useState(null);
  const requestSequence = useRef(0);

  const load = useCallback(async () => {
    const requestId = requestSequence.current + 1;
    requestSequence.current = requestId;
    setLoading(true);
    setError("");
    setData(null);
    try {
      const response = await api.get(QOYOD_BASE, {
        params: {
          from_date: filters.fromDate,
          to_date: filters.toDate,
          search: filters.search || undefined,
          page,
          page_size: PAGE_SIZE,
        },
      });
      if (requestSequence.current === requestId) setData(response.data);
    } catch (requestError) {
      if (requestSequence.current === requestId) {
        setData(null);
        setError(errorMessage(requestError, "تعذر تحميل فواتير قيود"));
      }
    } finally {
      if (requestSequence.current === requestId) setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    load();
  }, [load]);

  const applyFilters = () => {
    const nextSearch = searchInput.trim();
    if (fromDate < SYNC_START_DATE || toDate < fromDate) {
      setError("تحقق من نطاق التاريخ؛ البداية لا يمكن أن تكون قبل 2026-07-01");
      return;
    }
    const nextFilters = { fromDate, toDate, search: nextSearch };
    const unchanged = page === 1
      && filters.fromDate === nextFilters.fromDate
      && filters.toDate === nextFilters.toDate
      && filters.search === nextFilters.search;
    if (unchanged) {
      load();
      return;
    }
    setPage(1);
    setFilters(nextFilters);
  };

  const refreshFromQoyod = async () => {
    setSyncing(true);
    setError("");
    setSyncSummary(null);
    try {
      const response = await api.post(`${QOYOD_BASE}/sync`);
      const summary = response.data?.sync_summary || response.data;
      if (summary?.ok === false) {
        throw new Error(summary.error || "فشل تحديث فواتير قيود");
      }
      setSyncSummary(summary);
      toast.success(`تم تحديث ${summary?.in_scope ?? summary?.updated ?? 0} فاتورة من قيود`);
      if (page === 1) await load();
      else setPage(1);
    } catch (requestError) {
      setError(errorMessage(requestError, "تعذر تحديث الفواتير من قيود"));
    } finally {
      setSyncing(false);
    }
  };

  const exportExcel = async () => {
    setExporting(true);
    try {
      const response = await api.get(`${QOYOD_BASE}/export.xlsx`, {
        params: {
          from_date: fromDate,
          to_date: toDate,
          search: searchInput.trim() || undefined,
        },
        responseType: "blob",
      });
      const blob = new Blob([response.data], {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      });
      const url = window.URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `فواتير-قيود-${fromDate}-${toDate}.xlsx`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
      toast.success("تم تصدير تقرير فواتير قيود");
    } catch (requestError) {
      setError(errorMessage(requestError, "تعذر تصدير تقرير Excel"));
    } finally {
      setExporting(false);
    }
  };

  const items = data?.items || [];
  const summary = data?.summary || {};
  const total = Number(data?.total || 0);
  const pages = Math.max(1, Number(data?.pages || 1));
  const currentPage = Math.min(page, pages);
  const firstItem = total ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const lastItem = Math.min(currentPage * PAGE_SIZE, total);
  const lastSync = data?.last_sync_at
    || data?.summary?.latest_sync_at
    || syncSummary?.finished_at
    || data?.sync_summary?.finished_at;

  return (
    <div className="space-y-5" dir="rtl" data-testid="qoyod-invoice-review-page">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-black text-slate-950">فواتير قيود</h1>
          <p className="mt-1 max-w-3xl text-sm font-semibold text-slate-500">
            نسخة محفوظة داخل ميزان للمراجعة والبحث والمقارنة، تبدأ من {SYNC_START_DATE}.
            هذه الصفحة لا ترسل ولا تنشئ ولا تعدّل أي فاتورة في قيود، ولا تدخل ضمن شروط الإرسال.
          </p>
        </div>
        <button
          type="button"
          onClick={refreshFromQoyod}
          disabled={syncing || loading}
          className="inline-flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-sm font-extrabold text-white disabled:opacity-50"
          data-testid="qoyod-invoice-sync"
        >
          <ArrowClockwise size={18} className={syncing ? "animate-spin" : ""} />
          {syncing ? "جاري التحديث من قيود…" : "تحديث من قيود"}
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <SummaryCard
          label="طلبات سلة المؤهلة"
          value={data ? summary.eligible_salla_orders : undefined}
          hint={`من ${filters.fromDate} إلى ${filters.toDate}`}
          tone="sky"
        />
        <SummaryCard
          label="فواتير قيود"
          value={data ? summary.qoyod_invoices : undefined}
          hint="فواتير حقيقية محفوظة في ميزان ضمن الفترة"
          tone="emerald"
        />
      </div>

      <form
        onSubmit={(event) => { event.preventDefault(); applyFilters(); }}
        className="rounded-2xl border border-slate-200 bg-white p-4"
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-40 flex-1 text-xs font-extrabold text-slate-600">
            من تاريخ
            <input
              type="date"
              min={SYNC_START_DATE}
              max={toDate}
              value={fromDate}
              onChange={(event) => setFromDate(event.target.value || SYNC_START_DATE)}
              className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold"
              data-testid="qoyod-invoice-from"
            />
          </label>
          <label className="min-w-40 flex-1 text-xs font-extrabold text-slate-600">
            إلى تاريخ
            <input
              type="date"
              min={fromDate}
              max={todaySA()}
              value={toDate}
              onChange={(event) => setToDate(event.target.value || todaySA())}
              className="mt-1 block w-full rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold"
              data-testid="qoyod-invoice-to"
            />
          </label>
          <label className="min-w-64 flex-[2] text-xs font-extrabold text-slate-600">
            البحث
            <div className="mt-1 flex">
              <input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="رقم الطلب، رقم الفاتورة أو اسم العميل"
                className="w-full rounded-r-xl border border-slate-300 bg-white px-3 py-2 text-sm font-semibold"
                data-testid="qoyod-invoice-search"
              />
              <button
                type="submit"
                className="inline-flex items-center gap-1 rounded-l-xl bg-sky-700 px-4 py-2 text-sm font-extrabold text-white hover:bg-sky-600"
                data-testid="qoyod-invoice-search-submit"
              >
                <MagnifyingGlass size={17} />
                بحث
              </button>
            </div>
          </label>
          <button
            type="button"
            onClick={exportExcel}
            disabled={exporting || loading}
            className="inline-flex items-center gap-2 rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-2 text-sm font-extrabold text-emerald-800 disabled:opacity-50"
            data-testid="qoyod-invoice-export"
          >
            <FileXls size={19} weight="fill" />
            {exporting ? "جاري التصدير…" : "تصدير Excel"}
          </button>
        </div>
      </form>

      {error && (
        <div className="rounded-xl border border-rose-300 bg-rose-50 p-3 text-sm font-bold text-rose-800" data-testid="qoyod-invoice-error">
          {String(error)}
        </div>
      )}

      {syncSummary?.ran && (
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs font-semibold text-sky-900" data-testid="qoyod-invoice-sync-summary">
          آخر تحديث من قيود: {dateOnly(lastSync)} · تم جلب {syncSummary.fetched ?? 0} فاتورة، منها {syncSummary.in_scope ?? 0} منذ 2026-07-01.
        </div>
      )}

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
        <table className="min-w-[1180px] w-full text-sm" data-testid="qoyod-invoice-table">
          <thead className="bg-slate-50 text-xs font-extrabold text-slate-600">
            <tr>
              <th className="px-3 py-3 text-right">المرجع</th>
              <th className="px-3 py-3 text-right">فاتورة قيود</th>
              <th className="px-3 py-3 text-right">اسم العميل</th>
              <th className="px-3 py-3 text-right">تاريخ الإصدار</th>
              <th className="px-3 py-3 text-right">تاريخ الاستحقاق</th>
              <th className="px-3 py-3 text-right">القيمة الإجمالية</th>
              <th className="px-3 py-3 text-right">المدفوع</th>
              <th className="px-3 py-3 text-right">الرصيد</th>
              <th className="px-3 py-3 text-right">الحالة</th>
              <th className="px-3 py-3 text-right">مطابقة سلة</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={10} className="px-4 py-12 text-center font-semibold text-slate-400">جاري تحميل فواتير قيود…</td></tr>
            )}
            {!loading && !error && data && items.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-12 text-center text-slate-500" data-testid="qoyod-invoice-empty">
                  لا توجد فاتورة مطابقة بحسب آخر تحديث من قيود.
                </td>
              </tr>
            )}
            {!loading && items.map((invoice) => {
              const statusKey = String(invoice.status || "").toLowerCase();
              return (
                <tr key={invoice.qoyod_invoice_id} className="border-t border-slate-100 hover:bg-slate-50" data-testid={`qoyod-invoice-row-${invoice.qoyod_invoice_id}`}>
                  <td className="px-3 py-3 font-mono font-semibold" dir="ltr">{invoice.reference || invoice.salla_order_number || "—"}</td>
                  <td className="px-3 py-3 font-mono" dir="ltr">
                    <div>{invoice.invoice_number || invoice.qoyod_invoice_id || "—"}</div>
                    {invoice.invoice_number && String(invoice.invoice_number) !== String(invoice.qoyod_invoice_id) && (
                      <div className="text-[10px] text-slate-400">ID {invoice.qoyod_invoice_id}</div>
                    )}
                  </td>
                  <td className="px-3 py-3 font-semibold text-slate-700">{invoice.customer_name || "—"}</td>
                  <td className="px-3 py-3 font-mono text-xs" dir="ltr">{dateOnly(invoice.issue_date)}</td>
                  <td className="px-3 py-3 font-mono text-xs" dir="ltr">{dateOnly(invoice.due_date)}</td>
                  <td className="px-3 py-3 font-mono font-semibold" dir="ltr">{moneyWithCurrency(invoice.total, invoice.currency)}</td>
                  <td className="px-3 py-3 font-mono" dir="ltr">{money(invoice.paid_amount)}</td>
                  <td className={`px-3 py-3 font-mono ${Math.abs(Number(invoice.remaining || 0)) > 0.01 ? "font-bold text-amber-700" : "text-slate-600"}`} dir="ltr">{money(invoice.remaining)}</td>
                  <td className="px-3 py-3">
                    <span className={`inline-flex rounded-lg border px-2 py-1 text-xs font-extrabold ${STATUS_STYLES[statusKey] || "border-slate-300 bg-white text-slate-700"}`}>
                      {STATUS_LABELS[statusKey] || invoice.status || "—"}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    {invoice.exact_reference_match ? (
                      <span className="inline-flex rounded-full border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-extrabold text-emerald-800">مطابق برقم الطلب</span>
                    ) : (
                      <span className="inline-flex rounded-full border border-slate-300 bg-slate-50 px-2 py-1 text-xs font-bold text-slate-600">غير موجود في سلة</span>
                    )}
                    {invoice.salla_status && <div className="mt-1 text-[10px] text-slate-500">{invoice.salla_status}</div>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 bg-slate-50 px-4 py-3 text-sm" data-testid="qoyod-invoice-pagination">
          <div className="font-semibold text-slate-500">
            عرض <span dir="ltr" className="font-mono">{firstItem}–{lastItem}</span> من <span dir="ltr" className="font-mono">{total}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">في الصفحة <b dir="ltr">15</b></span>
            <button
              type="button"
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              disabled={currentPage <= 1 || loading}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold disabled:opacity-40"
              data-testid="qoyod-invoice-prev"
            >
              → السابق
            </button>
            <span className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 font-mono text-xs" dir="ltr">{currentPage} / {pages}</span>
            <button
              type="button"
              onClick={() => setPage((value) => Math.min(pages, value + 1))}
              disabled={currentPage >= pages || loading}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold disabled:opacity-40"
              data-testid="qoyod-invoice-next"
            >
              التالي ←
            </button>
          </div>
        </div>
      </div>

      <div className="text-xs font-semibold text-slate-400">
        آخر مزامنة محفوظة: <span dir="ltr" className="font-mono">{lastSync || "لم تُسجّل"}</span>
      </div>
    </div>
  );
}
