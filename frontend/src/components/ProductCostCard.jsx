// Iteration 26: Product Cost Card on Dashboard.
// Fetches /api/product-costs/summary and displays today/month totals
// alongside linked vs missing product counts. Auto-refreshes when the
// merchant's filters change (parent triggers via the `refreshKey` prop).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Package, ArrowsClockwise, Lightning } from "@phosphor-icons/react";
import { toast } from "sonner";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

export default function ProductCostCard({ refreshKey = 0 }) {
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [recomputing, setRecomputing] = useState(false);
    const [updatedAt, setUpdatedAt] = useState(null);

    const load = async () => {
        try {
            const { data } = await api.get("/product-costs/summary");
            setSummary(data);
            setUpdatedAt(new Date());
        } catch { /* silent — card just stays empty */ }
        finally { setLoading(false); }
    };

    // Iteration 27: manual "recompute now" trigger — re-enriches the last
    // 2 days of orders with the latest product_costs catalogue and reloads
    // the summary. Useful when:
    //   • the merchant just uploaded an Excel of costs and wants the
    //     dashboard to refresh BEFORE the next webhook arrives
    //   • the deployed environment is older than iteration 26 and lacks
    //     the auto-recompute hook (the underlying /recompute endpoint
    //     has shipped since iteration 19, so this button works anywhere).
    const recomputeNow = async () => {
        setRecomputing(true);
        try {
            const { data } = await api.post("/product-costs/recompute?days=2");
            await load();
            toast.success(
                `تم تحديث تكلفة ${data.orders_updated || 0} طلب من آخر ${data.window_days || 2} يوم`,
                { duration: 5000 },
            );
        } catch (err) {
            toast.error("تعذر تحديث التكلفة — حاول مرة أخرى");
        } finally { setRecomputing(false); }
    };

    useEffect(() => { load(); /* eslint-disable-next-line */ }, [refreshKey]);

    return (
        <div
            className="rounded-2xl border-2 border-emerald-200 bg-gradient-to-br from-emerald-50/40 to-white p-5 shadow-sm"
            data-testid="dashboard-product-cost-card"
        >
            <div className="flex items-center justify-between gap-2 mb-4 flex-wrap">
                <div className="flex items-center gap-2">
                    <div className="w-9 h-9 rounded-lg bg-emerald-100 flex items-center justify-center">
                        <Package size={20} weight="duotone" className="text-emerald-700" />
                    </div>
                    <div>
                        <div className="text-base font-extrabold leading-tight">تكلفة المنتجات</div>
                        <div className="text-[10px] text-muted-foreground">
                            من جدول product_costs • SKU/Product ID
                        </div>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {/* Iteration 27: prominent CTA — manual recompute of last 2 days. */}
                    <button
                        type="button"
                        onClick={recomputeNow}
                        disabled={recomputing}
                        className="px-3 py-1.5 rounded-lg bg-emerald-700 hover:bg-emerald-800 disabled:bg-emerald-700/60 text-white text-xs font-bold flex items-center gap-1.5 transition-colors"
                        title="إعادة احتساب تكلفة طلبات آخر يومين بناءً على الكاتالوج الحالي"
                        data-testid="dashboard-product-cost-recompute-btn"
                    >
                        <Lightning size={14} weight="fill" className={recomputing ? "animate-pulse" : ""} />
                        {recomputing ? "جارٍ التحديث…" : "تحديث التكلفة الآن"}
                    </button>
                    <button
                        type="button"
                        onClick={load}
                        className="p-1.5 rounded-lg hover:bg-emerald-100 text-muted-foreground"
                        title="إعادة قراءة الإجماليات فقط (بدون إعادة حساب الطلبات)"
                        data-testid="dashboard-product-cost-refresh-btn"
                    >
                        <ArrowsClockwise size={14} className={loading ? "animate-spin" : ""} />
                    </button>
                </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <Cell
                    label="اليوم"
                    value={loading ? "…" : formatMoney(summary?.today_total)}
                    suffix={!loading && "ر.س"}
                    accent="emerald"
                    testid="product-cost-card-today"
                />
                <Cell
                    label="الشهر"
                    value={loading ? "…" : formatMoney(summary?.month_total)}
                    suffix={!loading && "ر.س"}
                    accent="emerald"
                    testid="product-cost-card-month"
                />
                <Cell
                    label="مرتبط"
                    value={loading ? "…" : formatInt(summary?.linked_products_count)}
                    suffix={!loading && "منتج"}
                    accent="blue"
                    testid="product-cost-card-linked"
                />
                <Cell
                    label="بدون تكلفة"
                    value={loading ? "…" : formatInt(summary?.missing_products_count)}
                    suffix={!loading && "منتج"}
                    accent={summary?.missing_products_count > 0 ? "amber" : "neutral"}
                    testid="product-cost-card-missing"
                    href={summary?.missing_products_count > 0 ? "/product-costs?tab=missing" : null}
                />
            </div>

            {updatedAt && (
                <div className="mt-3 text-[10px] text-muted-foreground text-end">
                    آخر تحديث: {updatedAt.toLocaleTimeString("ar-SA", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                </div>
            )}
        </div>
    );
}

const ACCENT_CLASSES = {
    emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
    blue: "bg-blue-50 border-blue-200 text-blue-900",
    amber: "bg-amber-50 border-amber-300 text-amber-900",
    neutral: "bg-accent/30 border-border text-foreground",
};

function Cell({ label, value, suffix, accent, testid, href }) {
    const content = (
        <div
            className={`rounded-xl border p-3 ${ACCENT_CLASSES[accent] || ACCENT_CLASSES.neutral} transition-shadow hover:shadow-sm h-full`}
            data-testid={testid}
        >
            <div className="text-[11px] font-bold opacity-70 mb-1">{label}</div>
            <div className="flex items-baseline gap-1 flex-wrap">
                <div className="text-lg sm:text-xl font-extrabold tabular-nums leading-none">{value}</div>
                {suffix && <span className="text-[10px] opacity-70">{suffix}</span>}
            </div>
        </div>
    );
    if (href) {
        return (
            <Link to={href} className="block" data-testid={`${testid}-link`}>
                {content}
            </Link>
        );
    }
    return content;
}
