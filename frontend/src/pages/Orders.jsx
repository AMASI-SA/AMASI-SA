/**
 * Orders Explorer (Iter-85)
 * -------------------------
 * • Status summary cards: 4 category buckets + per-status breakdown chips
 * • Filters: date range, status, search
 * • Paginated table with category badges
 */
import { useEffect, useMemo, useState } from "react";
import {
    Package, MagnifyingGlass, FunnelSimple, X, CaretLeft, CaretRight,
    CheckCircle, Hourglass, ArrowUUpLeft, XCircle, DownloadSimple,
} from "@phosphor-icons/react";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import { toast } from "sonner";

const CAT_META = {
    confirmed: { label: "مؤكدة",   Icon: CheckCircle,   color: "emerald" },
    pending:   { label: "معلّقة",  Icon: Hourglass,     color: "amber"   },
    refunded:  { label: "مسترجعة", Icon: ArrowUUpLeft,  color: "rose"    },
    cancelled: { label: "ملغاة",   Icon: XCircle,       color: "slate"   },
};
const CAT_BG = {
    emerald: "bg-emerald-50 text-emerald-900 border-emerald-200 hover:border-emerald-400",
    amber:   "bg-amber-50 text-amber-900 border-amber-200 hover:border-amber-400",
    rose:    "bg-rose-50 text-rose-900 border-rose-200 hover:border-rose-400",
    slate:   "bg-slate-50 text-slate-700 border-slate-200 hover:border-slate-300",
};
const CAT_BG_ACTIVE = {
    emerald: "bg-emerald-600 text-white border-emerald-700 shadow-lg",
    amber:   "bg-amber-600 text-white border-amber-700 shadow-lg",
    rose:    "bg-rose-600 text-white border-rose-700 shadow-lg",
    slate:   "bg-slate-700 text-white border-slate-800 shadow-lg",
};
const STATUS_CHIP = {
    confirmed: "bg-emerald-100 text-emerald-800 border-emerald-200",
    pending:   "bg-amber-100 text-amber-800 border-amber-200",
    refunded:  "bg-rose-100 text-rose-800 border-rose-200",
    cancelled: "bg-slate-200 text-slate-700 border-slate-300",
};

function fmtDate(d) {
    if (!d) return "—";
    try {
        return new Date(d).toLocaleDateString("en-GB", {
            day: "2-digit", month: "2-digit", year: "numeric",
        });
    } catch { return d; }
}

export default function Orders() {
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState({
        from_date: "",
        to_date: "",
        status: "",
        category: "",
        search: "",
    });
    const [searchInput, setSearchInput] = useState("");
    const [page, setPage] = useState(1);
    const [data, setData] = useState({ items: [], total: 0, pages: 1 });
    const [listLoading, setListLoading] = useState(false);

    // Build query string
    const qs = useMemo(() => {
        const p = new URLSearchParams();
        if (filters.from_date) p.set("from_date", filters.from_date);
        if (filters.to_date)   p.set("to_date", filters.to_date);
        if (filters.status)    p.set("status", filters.status);
        if (filters.category)  p.set("category", filters.category);
        if (filters.search)    p.set("search", filters.search);
        return p.toString();
    }, [filters]);

    // Load summary
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            try {
                const dateQs = new URLSearchParams();
                if (filters.from_date) dateQs.set("from_date", filters.from_date);
                if (filters.to_date)   dateQs.set("to_date", filters.to_date);
                const { data } = await api.get(
                    `/orders/status-summary${dateQs.toString() ? "?" + dateQs.toString() : ""}`
                );
                if (!cancelled) setSummary(data);
            } catch {
                if (!cancelled) setSummary(null);
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [filters.from_date, filters.to_date]);

    // Load list
    useEffect(() => {
        let cancelled = false;
        (async () => {
            setListLoading(true);
            try {
                const u = qs ? `?${qs}&page=${page}&limit=50` : `?page=${page}&limit=50`;
                const { data } = await api.get(`/orders${u}`);
                if (!cancelled) setData(data);
            } catch {
                if (!cancelled) setData({ items: [], total: 0, pages: 1 });
            } finally {
                if (!cancelled) setListLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [qs, page]);

    const clearFilters = () => {
        setFilters({ from_date: "", to_date: "", status: "", category: "", search: "" });
        setSearchInput("");
        setPage(1);
    };

    const onSearchSubmit = (e) => {
        e.preventDefault();
        setFilters((f) => ({ ...f, search: searchInput.trim() }));
        setPage(1);
    };

    const summaryRows = summary?.rows || [];
    const byCat = summary?.by_category || {};
    const grandTotal = summary?.totals?.orders_count || 0;

    const [exporting, setExporting] = useState(false);
    const exportXlsx = async () => {
        setExporting(true);
        try {
            const u = qs ? `/orders/export.xlsx?${qs}` : "/orders/export.xlsx";
            const resp = await api.get(u, { responseType: "blob" });
            const blob = new Blob([resp.data], {
                type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            });
            const ts = new Date().toISOString().slice(0, 16).replace(/[T:]/g, "-");
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `mezan_orders_${ts}.xlsx`;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            toast.success(`تم تصدير ${formatInt(data.total)} طلب`);
        } catch {
            toast.error("تعذّر تصدير الملف");
        } finally {
            setExporting(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="orders-page">
            <header className="flex items-start justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground flex items-center gap-3" style={{ fontFamily: "Tajawal" }}>
                        <Package size={32} className="text-brand" weight="duotone" />
                        الطلبات
                    </h1>
                    <p className="text-muted-foreground mt-1 text-sm">
                        استعرض جميع الطلبات حسب الحالة، الفئة، التاريخ. المجموع الكلي:{" "}
                        <span className="font-bold num text-foreground">{formatInt(grandTotal)}</span> طلب.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={exportXlsx}
                    disabled={exporting || data.total === 0}
                    className="px-4 py-2 rounded-lg bg-brand text-white text-sm font-bold inline-flex items-center gap-2 hover:bg-brand-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
                    data-testid="orders-export-btn"
                    title={`تصدير ${formatInt(data.total)} طلب وفق الفلاتر الحالية`}
                >
                    <DownloadSimple size={16} weight="bold" />
                    {exporting ? "جارٍ التصدير…" : `تصدير إلى Excel (${formatInt(data.total)})`}
                </button>
            </header>

            {/* 4 category cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3" data-testid="orders-category-cards">
                {Object.entries(CAT_META).map(([k, meta]) => {
                    const stats = byCat[k] || { count: 0, amount: 0 };
                    const active = filters.category === k;
                    const cls = active ? CAT_BG_ACTIVE[meta.color] : CAT_BG[meta.color];
                    const Icon = meta.Icon;
                    return (
                        <button
                            key={k}
                            type="button"
                            onClick={() => { setFilters((f) => ({ ...f, category: active ? "" : k, status: "" })); setPage(1); }}
                            className={`text-right p-4 rounded-xl border-2 transition-all ${cls}`}
                            data-testid={`orders-cat-${k}`}
                            aria-pressed={active}
                        >
                            <div className="flex items-center justify-between gap-2 mb-1">
                                <Icon size={20} weight="duotone" />
                                <span className="text-xs font-bold opacity-80">{meta.label}</span>
                            </div>
                            <div className="num text-2xl font-extrabold">{formatInt(stats.count)}</div>
                            <div className="num text-[11px] opacity-70 mt-1">{formatMoney(stats.amount)} ر.س</div>
                        </button>
                    );
                })}
            </div>

            {/* Per-status chips */}
            <div className="rounded-xl border border-border bg-white p-4">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                    <h2 className="text-base font-bold flex items-center gap-2" style={{ fontFamily: "Tajawal" }}>
                        <FunnelSimple size={16} weight="bold" />
                        كل حالات الطلبات
                        {filters.status && (
                            <button type="button" onClick={() => { setFilters((f) => ({ ...f, status: "" })); setPage(1); }} className="text-xs text-rose-700 inline-flex items-center gap-1 ms-2" data-testid="orders-clear-status">
                                <X size={12} /> إزالة فلتر الحالة
                            </button>
                        )}
                    </h2>
                    {loading && <span className="text-xs text-muted-foreground">جاري التحميل…</span>}
                </div>
                <div className="flex flex-wrap gap-2" data-testid="orders-status-chips">
                    {summaryRows.map((r) => {
                        const active = filters.status === r.status;
                        const chipCls = STATUS_CHIP[r.category] || "bg-slate-100 text-slate-700 border-slate-200";
                        return (
                            <button
                                key={r.status}
                                type="button"
                                onClick={() => { setFilters((f) => ({ ...f, status: active ? "" : r.status, category: "" })); setPage(1); }}
                                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full border text-xs font-bold transition-all ${chipCls} ${active ? "ring-2 ring-offset-1 ring-current scale-105" : "hover:scale-105"}`}
                                data-testid={`orders-status-chip-${r.status}`}
                                title={`${r.display} — ${r.orders_count} طلب`}
                            >
                                <span>{r.display}</span>
                                <span className="num bg-white/60 px-1.5 rounded-full text-[10px]">{formatInt(r.orders_count)}</span>
                            </button>
                        );
                    })}
                    {summaryRows.length === 0 && !loading && (
                        <div className="text-sm text-muted-foreground">لا توجد بيانات.</div>
                    )}
                </div>
            </div>

            {/* Filter bar */}
            <div className="rounded-xl border border-border bg-white p-4" data-testid="orders-filter-bar">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    <div>
                        <label className="block text-[11px] font-bold text-muted-foreground mb-1">من تاريخ</label>
                        <input
                            type="date"
                            value={filters.from_date}
                            onChange={(e) => { setFilters((f) => ({ ...f, from_date: e.target.value })); setPage(1); }}
                            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="orders-from-date"
                        />
                    </div>
                    <div>
                        <label className="block text-[11px] font-bold text-muted-foreground mb-1">إلى تاريخ</label>
                        <input
                            type="date"
                            value={filters.to_date}
                            onChange={(e) => { setFilters((f) => ({ ...f, to_date: e.target.value })); setPage(1); }}
                            className="w-full px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                            data-testid="orders-to-date"
                        />
                    </div>
                    <form onSubmit={onSearchSubmit} className="sm:col-span-2">
                        <label className="block text-[11px] font-bold text-muted-foreground mb-1">بحث (رقم، اسم العميل، الجوّال)</label>
                        <div className="relative">
                            <MagnifyingGlass size={16} className="absolute top-1/2 -translate-y-1/2 right-3 text-muted-foreground" />
                            <input
                                type="text"
                                value={searchInput}
                                onChange={(e) => setSearchInput(e.target.value)}
                                placeholder="ابحث…"
                                className="w-full ps-3 pe-9 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                                data-testid="orders-search-input"
                            />
                            {searchInput && (
                                <button type="button" onClick={() => { setSearchInput(""); setFilters((f) => ({ ...f, search: "" })); setPage(1); }} className="absolute top-1/2 -translate-y-1/2 left-2 text-muted-foreground hover:text-rose-700" data-testid="orders-search-clear">
                                    <X size={14} />
                                </button>
                            )}
                        </div>
                    </form>
                </div>
                {(filters.from_date || filters.to_date || filters.status || filters.category || filters.search) && (
                    <button
                        type="button"
                        onClick={clearFilters}
                        className="mt-3 px-3 py-1.5 rounded-lg text-xs font-bold text-rose-700 bg-rose-50 hover:bg-rose-100 inline-flex items-center gap-1"
                        data-testid="orders-clear-all"
                    >
                        <X size={12} /> مسح كل الفلاتر
                    </button>
                )}
            </div>

            {/* Table */}
            <div className="rounded-xl border border-border bg-white overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <h2 className="text-sm font-bold" style={{ fontFamily: "Tajawal" }}>
                        النتائج: <span className="num font-extrabold">{formatInt(data.total)}</span> طلب
                    </h2>
                    {listLoading && <span className="text-xs text-muted-foreground">جاري التحميل…</span>}
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" data-testid="orders-table">
                        <thead className="text-xs text-muted-foreground bg-slate-50">
                            <tr>
                                <th className="text-right px-3 py-2 font-semibold">رقم الطلب</th>
                                <th className="text-right px-3 py-2 font-semibold">التاريخ</th>
                                <th className="text-right px-3 py-2 font-semibold">العميل</th>
                                <th className="text-right px-3 py-2 font-semibold">طريقة الدفع</th>
                                <th className="text-right px-3 py-2 font-semibold">الإجمالي</th>
                                <th className="text-right px-3 py-2 font-semibold">الحالة</th>
                                <th className="text-right px-3 py-2 font-semibold">الفئة</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.items.map((o, idx) => {
                                const catMeta = CAT_META[o.category] || CAT_META.pending;
                                return (
                                    <tr key={`${o.order_number || "x"}-${o.order_date || idx}`} className="border-t border-border hover:bg-accent/10" data-testid={`orders-row-${o.order_number}`}>
                                        <td className="px-3 py-2 font-bold num">{o.order_number || "—"}</td>
                                        <td className="px-3 py-2 num text-[11px]">{fmtDate(o.order_date)}</td>
                                        <td className="px-3 py-2 truncate max-w-[200px]" title={o.customer_name}>{o.customer_name || "—"}</td>
                                        <td className="px-3 py-2 text-[11px] text-muted-foreground">{o.payment_method || "—"}</td>
                                        <td className="px-3 py-2 num font-bold">{formatMoney(o.total_amount || 0)}</td>
                                        <td className="px-3 py-2">
                                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[10px] font-bold ${STATUS_CHIP[o.category] || STATUS_CHIP.pending}`}>
                                                {o.order_status || "—"}
                                            </span>
                                        </td>
                                        <td className="px-3 py-2 text-[10px] font-bold">{catMeta.label}</td>
                                    </tr>
                                );
                            })}
                            {data.items.length === 0 && !listLoading && (
                                <tr>
                                    <td colSpan="7" className="text-center py-12 text-muted-foreground text-sm">
                                        لا توجد طلبات مطابقة للفلاتر.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>

                {/* Pagination */}
                {data.pages > 1 && (
                    <div className="flex items-center justify-between px-4 py-3 border-t border-border bg-slate-50/60" data-testid="orders-pagination">
                        <div className="text-xs text-muted-foreground">
                            صفحة {page} من {data.pages} — إجمالي {formatInt(data.total)} طلب
                        </div>
                        <div className="flex items-center gap-2">
                            <button
                                type="button"
                                onClick={() => setPage((p) => Math.max(1, p - 1))}
                                disabled={page <= 1}
                                className="p-1.5 rounded-lg border border-border bg-white text-foreground disabled:opacity-40 hover:bg-accent/30"
                                data-testid="orders-prev"
                                aria-label="السابق"
                            >
                                <CaretRight size={16} weight="bold" />
                            </button>
                            <button
                                type="button"
                                onClick={() => setPage((p) => Math.min(data.pages, p + 1))}
                                disabled={page >= data.pages}
                                className="p-1.5 rounded-lg border border-border bg-white text-foreground disabled:opacity-40 hover:bg-accent/30"
                                data-testid="orders-next"
                                aria-label="التالي"
                            >
                                <CaretLeft size={16} weight="bold" />
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
