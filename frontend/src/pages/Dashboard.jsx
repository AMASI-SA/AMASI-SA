import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    Coins,
    ShoppingBag,
    Receipt,
    Truck,
    Megaphone,
    Package,
    TrendUp,
    CaretLeft,
    UploadSimple,
    Percent,
    CalendarBlank,
    Wallet,
    Bank,
    ArrowsClockwise,
    EyeSlash,
    GearSix,
} from "@phosphor-icons/react";import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { toast } from "sonner";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import { useAuth } from "../context/AuthContext";
import { ALL_KPI_CARDS } from "../lib/dashboardCards";

function formatRelative(ms) {
    if (ms < 5_000) return "الآن";
    if (ms < 60_000) return `قبل ${Math.floor(ms / 1000)} ثانية`;
    if (ms < 3_600_000) return `قبل ${Math.floor(ms / 60_000)} دقيقة`;
    return `قبل ${Math.floor(ms / 3_600_000)} ساعة`;
}

function Kpi({ icon: Icon, label, value, hint, accent = false, testid, onHide }) {
    return (
        <div
            className={[
                "group relative rounded-xl border border-border p-5 bg-white transition-shadow hover:shadow-sm",
                accent ? "border-brand/40 bg-accent" : "",
            ].join(" ")}
            data-testid={testid}
        >
            {onHide && (
                <button
                    type="button"
                    onClick={onHide}
                    className="absolute top-2 start-2 p-1 rounded hover:bg-red-50 text-muted-foreground hover:text-red-600 opacity-0 group-hover:opacity-100 transition-opacity"
                    title="إخفاء هذه البطاقة (يمكن إعادتها من الإعدادات)"
                    data-testid={`${testid}-hide-btn`}
                >
                    <EyeSlash size={16} weight="bold" />
                </button>
            )}
            <div className="flex items-center justify-between mb-3">
                <div className={`w-10 h-10 rounded-lg ${accent ? "bg-brand text-white" : "bg-accent text-brand"} flex items-center justify-center`}>
                    <Icon size={22} weight="duotone" />
                </div>
                {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
            </div>
            <div className="text-sm text-muted-foreground mb-1">{label}</div>
            <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{value}</div>
        </div>
    );
}

import AdvancedFilters, { filtersToQueryString, defaultFilters } from "../components/AdvancedFilters";

export default function Dashboard() {
    const { user } = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState(defaultFilters());
    const [reprocessingId, setReprocessingId] = useState(null);
    const [hiddenCards, setHiddenCards] = useState([]);
    const [lastUpdated, setLastUpdated] = useState(null);
    const [nowTick, setNowTick] = useState(Date.now());
    const [snapSummary, setSnapSummary] = useState(null);
    const [metaSummary, setMetaSummary] = useState(null);
    const fileInputRef = useRef(null);
    const pendingReprocessId = useRef(null);

    const fetchSnapSummary = async () => {
        try {
            const { data } = await api.get("/dashboard/snapchat-summary");
            setSnapSummary(data);
        } catch {
            /* non-critical */
        }
    };

    const fetchMetaSummary = async () => {
        try {
            const { data } = await api.get("/dashboard/meta-summary");
            setMetaSummary(data);
        } catch {
            /* non-critical */
        }
    };

    // Best-effort daily auto-sync — fires once on dashboard mount.
    // Idempotent: the backend skips if the last sync was within 23h.
    const autoSyncMetaIfStale = async () => {
        try {
            const { data } = await api.post("/meta/auto-sync-if-stale");
            if (data?.synced && data?.upserted) {
                fetchMetaSummary();  // pull the freshly-synced data
            }
        } catch {
            /* not connected → silently skip */
        }
    };

    const fetchDashboard = async (f = filters) => {
        setLoading(true);
        try {
            const qs = filtersToQueryString(f);
            const { data } = await api.get(`/dashboard${qs ? "?" + qs : ""}`);
            setData(data);
            setLastUpdated(Date.now());
            fetchSnapSummary();  // refresh Snapchat card alongside
            fetchMetaSummary();
        } finally {
            setLoading(false);
        }
    };

    // Silent refresh — same fetch but without flicker (no loading state).
    const refreshSilently = async () => {
        try {
            const qs = filtersToQueryString(filters);
            const { data } = await api.get(`/dashboard${qs ? "?" + qs : ""}`);
            setData(data);
            setLastUpdated(Date.now());
            fetchSnapSummary();
            fetchMetaSummary();
        } catch {
            /* swallow; next tick will retry */
        }
    };

    // Auto-poll every 60 seconds when the tab is active. This keeps the
    // dashboard in sync with new Make.com webhook orders without requiring
    // the user to refresh manually.
    useEffect(() => {
        const id = setInterval(() => {
            if (document.visibilityState === "visible") {
                refreshSilently();
            }
        }, 60_000);
        return () => clearInterval(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters]);

    // Update the "آخر تحديث: قبل Xث" label once a second.
    useEffect(() => {
        const id = setInterval(() => setNowTick(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);

    // Also refresh when the user returns to the tab (after switching apps).
    useEffect(() => {
        const onVis = () => {
            if (document.visibilityState === "visible") refreshSilently();
        };
        document.addEventListener("visibilitychange", onVis);
        return () => document.removeEventListener("visibilitychange", onVis);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters]);

    // Load the user's card-visibility preferences once on mount.
    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/settings");
                setHiddenCards(data.dashboard_hidden_cards || []);
            } catch {
                /* fall back to all-visible if settings unreachable */
            }
        })();
    }, []);

    const hideCard = async (id) => {
        const next = Array.from(new Set([...hiddenCards, id]));
        setHiddenCards(next);  // optimistic
        try {
            // Read current settings, merge hidden cards, write back. We have
            // to send payment_methods/shipping_companies because the PUT model
            // requires them.
            const { data: s } = await api.get("/settings");
            await api.put("/settings", {
                payment_methods: s.payment_methods,
                shipping_companies: s.shipping_companies,
                shipping_approved_statuses: s.shipping_approved_statuses,
                cod_approved_statuses: s.cod_approved_statuses,
                report_included_statuses: s.report_included_statuses,
                dashboard_hidden_cards: next,
            });
            toast.success("تم إخفاء البطاقة. يمكنك إعادتها من الإعدادات.");
        } catch {
            setHiddenCards(hiddenCards);  // revert on failure
            toast.error("تعذّر حفظ التفضيل");
        }
    };

    useEffect(() => { fetchDashboard(filters); /* eslint-disable-next-line */ }, [filters]);
    // Trigger Meta auto-sync once on mount (idempotent — backend skips if recent)
    useEffect(() => { autoSyncMetaIfStale(); /* eslint-disable-next-line */ }, []);

    const openReprocessPicker = (id) => {
        pendingReprocessId.current = id;
        if (fileInputRef.current) {
            fileInputRef.current.value = "";
            fileInputRef.current.click();
        }
    };

    const handleReprocessFile = async (e) => {
        const f = e.target.files?.[0];
        const id = pendingReprocessId.current;
        pendingReprocessId.current = null;
        if (!f || !id) return;
        setReprocessingId(id);
        try {
            const fd = new FormData();
            fd.append("file", f);
            const { data: res } = await api.post(`/analyses/${id}/reprocess`, fd, {
                headers: { "Content-Type": "multipart/form-data" },
            });
            toast.success(`تمت إعادة المعالجة: ${res.orders_imported} طلب جديد، ${res.orders_updated} مُحدَّث`);
            await fetchDashboard(filters);
        } catch (err) {
            const msg = err?.response?.data?.detail || "تعذرت إعادة المعالجة";
            toast.error(typeof msg === "string" ? msg : "تعذرت إعادة المعالجة");
        } finally {
            setReprocessingId(null);
        }
    };

    const totals = data?.totals || {};
    const monthly = data?.monthly || [];
    const recent = data?.recent_analyses || [];

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="dashboard-page">
            <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls,.xlsm"
                onChange={handleReprocessFile}
                className="hidden"
                data-testid="reprocess-file-input"
            />
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <div className="text-sm text-muted-foreground mb-1">مرحباً، {user?.name || "ضيف"}</div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground" style={{ fontFamily: "Tajawal" }}>
                        لوحة التحكم
                    </h1>
                    <p className="text-muted-foreground mt-2 text-base">
                        {(filters.from || filters.to)
                            ? `عرض البيانات من ${filters.from || "البداية"} إلى ${filters.to || "الآن"}`
                            : "نظرة شاملة على أدائك المالي عبر جميع التحاليل المحفوظة."}
                    </p>
                </div>
                <Link
                    to="/upload"
                    className="inline-flex items-center gap-2 px-5 py-3 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors"
                    data-testid="dashboard-upload-btn"
                >
                    <UploadSimple size={20} weight="bold" />
                    تحليل ملف جديد
                </Link>
            </div>

            {/* Advanced filters: date preset + payment + shipping */}
            <div className="flex items-stretch gap-2">
                <div className="flex-1">
                    <AdvancedFilters value={filters} onChange={setFilters} />
                </div>
                <button
                    type="button"
                    onClick={() => fetchDashboard(filters)}
                    disabled={loading}
                    title="تحديث البيانات الآن (يحدّث تلقائياً كل دقيقة)"
                    className="px-3 py-2 rounded-lg border border-border bg-white font-bold text-sm hover:bg-accent transition-colors disabled:opacity-50 inline-flex items-center gap-1.5"
                    data-testid="dashboard-refresh-btn"
                >
                    <ArrowsClockwise size={16} weight="bold" className={loading ? "animate-spin" : ""} />
                    تحديث
                </button>
            </div>
            {lastUpdated && (
                <div className="text-xs text-muted-foreground -mt-2" data-testid="dashboard-last-updated">
                    آخر تحديث: {formatRelative(nowTick - lastUpdated)} • يحدِّث تلقائياً كل دقيقة
                </div>
            )}

            {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                        <div key={i} className="h-32 rounded-xl bg-white border border-border animate-pulse" />
                    ))}
                </div>
            ) : (
                <>
                    {/* KPI grid (config-driven, supports per-card hide) */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                        {ALL_KPI_CARDS
                            .filter((c) => !hiddenCards.includes(c.id))
                            .map((c) => {
                                const rawVal = c.value(totals);
                                const display = c.money ? formatMoney(rawVal) : (c.isInt ? formatInt(rawVal) : String(rawVal));
                                const label = c.money ? `${c.label} (ر.س)` : c.label;
                                return (
                                    <Kpi
                                        key={c.id}
                                        icon={c.icon}
                                        label={label}
                                        value={display}
                                        hint={c.hint}
                                        accent={c.accent}
                                        testid={`kpi-${c.id}`}
                                        onHide={() => hideCard(c.id)}
                                    />
                                );
                            })}
                        {ALL_KPI_CARDS.length === hiddenCards.length && (
                            <div className="col-span-full text-center py-10 text-muted-foreground border border-dashed border-border rounded-xl">
                                لقد أخفيت كل البطاقات. <Link to="/settings" className="text-brand font-bold hover:underline">أعدها من الإعدادات</Link>.
                            </div>
                        )}
                    </div>
                    {hiddenCards.length > 0 && (
                        <Link
                            to="/settings"
                            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-brand"
                            data-testid="dashboard-customize-link"
                        >
                            <GearSix size={14} />
                            عدد البطاقات المخفية: {hiddenCards.length} — إدارة من الإعدادات
                        </Link>
                    )}

                    {/* Snapchat Ads dedicated section — always visible so the
                        "تحديث صرف الشهر" button is reachable even when daily_costs
                        are empty (e.g. before the first Snapchat fetch). */}
                    {snapSummary && (
                        <div
                            className="rounded-xl border-2 border-yellow-300 p-4 sm:p-6"
                            style={{ background: "linear-gradient(135deg,#FFFCEA 0%,#fff 60%,#FFFAE0 100%)" }}
                            data-testid="snapchat-ads-section"
                        >
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
                                <div className="flex items-center gap-3">
                                    <div className="w-11 h-11 rounded-xl bg-yellow-400 text-black flex items-center justify-center font-extrabold text-lg flex-shrink-0" style={{ fontFamily: "Tajawal" }}>
                                        👻
                                    </div>
                                    <div className="min-w-0">
                                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>Snapchat Ads</h2>
                                        <p className="text-xs text-muted-foreground">
                                            {snapSummary.source === "snapchat_pixel"
                                                ? "الطلبات والعائد من Snapchat Pixel — تحديث تلقائي مع زر التحديث"
                                                : "الطلبات والعائد من المتجر (لا يوجد Pixel نشط بعد) — اضغط تحديث لجلب بيانات Snapchat"}
                                            {snapSummary.last_fetched_at && (
                                                <span className="ms-2 text-yellow-700">• آخر جلب: {new Date(snapSummary.last_fetched_at).toLocaleString("ar-SA", { dateStyle: "short", timeStyle: "short" })}</span>
                                            )}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={async () => {
                                        try {
                                            // Use Riyadh-based today from backend summary so the
                                            // refresh always targets the same date the dashboard reads.
                                            const todayStr = snapSummary.today.date;
                                            toast.loading("جاري جلب صرف Snapchat لليوم…", { id: "snap-fetch" });
                                            const { data } = await api.post("/snapchat/daily-spend/bulk", {
                                                from_date: todayStr,
                                                to_date: todayStr,
                                            });
                                            toast.success(`تم تحديث صرف اليوم (${data.saved || 0} سجل)`, { id: "snap-fetch" });
                                            fetchSnapSummary();
                                            refreshSilently();
                                        } catch (e) {
                                            const msg = e?.response?.data?.detail || e.message || "فشل الجلب";
                                            toast.error(msg, { id: "snap-fetch" });
                                        }
                                    }}
                                    className="flex items-center justify-center gap-2 px-4 py-2 bg-yellow-400 hover:bg-yellow-500 text-black rounded-lg font-bold text-sm transition-colors w-full sm:w-auto"
                                    style={{ fontFamily: "Tajawal" }}
                                    data-testid="snap-refresh-today-btn"
                                >
                                    <ArrowsClockwise size={16} weight="bold" />
                                    تحديث فوري للصرف اليوم
                                </button>
                            </div>

                            {/* Today + Month sections */}
                            <div className="space-y-4">
                                {/* Today */}
                                <div>
                                    <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                        اليوم ({snapSummary.today.date})
                                    </div>
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-spend-today">
                                            <div className="text-xs text-muted-foreground mb-1">صرف اليوم (ر.س)</div>
                                            <div className="num text-2xl font-extrabold text-yellow-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(snapSummary.today.spend)}</div>
                                        </div>
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-orders-today">
                                            <div className="text-xs text-muted-foreground mb-1">طلبات اليوم</div>
                                            <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(snapSummary.today.orders)}</div>
                                        </div>
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-revenue-today">
                                            <div className="text-xs text-muted-foreground mb-1">مبيعات اليوم (ر.س)</div>
                                            <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(snapSummary.today.revenue)}</div>
                                        </div>
                                        <div className={`border-2 rounded-lg p-4 ${snapSummary.today.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="snap-roas-today">
                                            <div className="text-xs text-muted-foreground mb-1">ROAS اليوم</div>
                                            <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: snapSummary.today.roas >= 2 ? "#047857" : "#B45309" }}>
                                                {snapSummary.today.spend > 0 ? `${snapSummary.today.roas}x` : "—"}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Month */}
                                <div>
                                    <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                        هذا الشهر (منذ {snapSummary.month.start})
                                    </div>
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-spend-month">
                                            <div className="text-xs text-muted-foreground mb-1">الصرف الشهري (ر.س)</div>
                                            <div className="num text-2xl font-extrabold text-yellow-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(snapSummary.month.spend)}</div>
                                        </div>
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-orders-month">
                                            <div className="text-xs text-muted-foreground mb-1">طلبات الشهر</div>
                                            <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(snapSummary.month.orders)}</div>
                                        </div>
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-revenue-month">
                                            <div className="text-xs text-muted-foreground mb-1">مبيعات الشهر (ر.س)</div>
                                            <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(snapSummary.month.revenue)}</div>
                                        </div>
                                        <div className={`border-2 rounded-lg p-4 ${snapSummary.month.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="snap-roas-month">
                                            <div className="text-xs text-muted-foreground mb-1">ROAS الشهر</div>
                                            <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: snapSummary.month.roas >= 2 ? "#047857" : "#B45309" }}>
                                                {snapSummary.month.spend > 0 ? `${snapSummary.month.roas}x` : "—"}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Tiny 30-day spend trend */}
                            {snapSummary.history && snapSummary.history.some(h => h.spend > 0) && (
                                <div className="mt-5 pt-4 border-t border-yellow-200">
                                    <div className="text-xs text-muted-foreground mb-2" style={{ fontFamily: "Tajawal" }}>
                                        صرف آخر 30 يوم — الإجمالي: {formatMoney(snapSummary.last_30d.spend)} ر.س
                                    </div>
                                    <div className="h-12 flex items-end gap-0.5">
                                        {snapSummary.history.map((d, i) => {
                                            const maxSpend = Math.max(1, ...snapSummary.history.map(h => h.spend));
                                            const height = Math.max(2, (d.spend / maxSpend) * 100);
                                            return (
                                                <div
                                                    key={i}
                                                    className="flex-1 bg-yellow-400 rounded-sm hover:bg-yellow-500 transition-colors cursor-help"
                                                    style={{ height: `${height}%` }}
                                                    title={`${d.date}: ${formatMoney(d.spend)} ر.س`}
                                                />
                                            );
                                        })}
                                    </div>
                                </div>
                            )}

                            {/* Footer: link to detailed report */}
                            <div className="mt-4 flex justify-end">
                                <Link
                                    to="/reports/ads"
                                    className="text-xs font-semibold text-yellow-800 hover:text-yellow-900 hover:underline inline-flex items-center gap-1"
                                    data-testid="snap-card-details-link"
                                    style={{ fontFamily: "Tajawal" }}
                                >
                                    التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←
                                </Link>
                            </div>
                        </div>
                    )}

                    {/* TikTok Ads dedicated section (pushed via Make.com webhook) */}
                    {(totals.tiktok_spend > 0 || totals.tiktok_revenue > 0 || totals.tiktok_purchases > 0) && (
                        <div
                            className="rounded-xl border-2 border-pink-200 p-4 sm:p-6"
                            style={{ background: "linear-gradient(135deg,#fff7fb 0%,#fff 50%,#f0fcff 100%)" }}
                            data-testid="tiktok-ads-section"
                        >
                            <div className="flex items-center gap-3 mb-5">
                                <div className="w-11 h-11 rounded-xl bg-black text-white flex items-center justify-center font-extrabold text-lg flex-shrink-0" style={{ fontFamily: "Tajawal" }}>
                                    TT
                                </div>
                                <div className="min-w-0">
                                    <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>TikTok Ads</h2>
                                    <p className="text-xs text-muted-foreground">
                                        البيانات تأتي من Make.com — تحديث تلقائي كل دقيقة
                                    </p>
                                </div>
                            </div>
                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-spend-card">
                                    <div className="text-xs text-muted-foreground mb-1">التكلفة (ر.س)</div>
                                    <div className="num text-2xl font-extrabold text-pink-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(totals.tiktok_spend)}</div>
                                </div>
                                <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-purchases-card">
                                    <div className="text-xs text-muted-foreground mb-1">عدد الطلبات</div>
                                    <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(totals.tiktok_purchases)}</div>
                                </div>
                                <div className="bg-white border border-cyan-100 rounded-lg p-4" data-testid="tiktok-revenue-card">
                                    <div className="text-xs text-muted-foreground mb-1">العائد (ر.س)</div>
                                    <div className="num text-2xl font-extrabold text-cyan-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(totals.tiktok_revenue)}</div>
                                </div>
                                <div className={`border-2 rounded-lg p-4 ${totals.tiktok_roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="tiktok-roas-card">
                                    <div className="text-xs text-muted-foreground mb-1">ROAS (العائد ÷ التكلفة)</div>
                                    <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: totals.tiktok_roas >= 2 ? "#047857" : "#B45309" }}>
                                        {totals.tiktok_spend > 0 ? `${totals.tiktok_roas}x` : "—"}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {/* Meta Ads (Facebook + Instagram) dedicated section — Direct Marketing API integration */}
                    {metaSummary && (
                        <div
                            className="rounded-xl border-2 border-blue-300 p-4 sm:p-6"
                            style={{ background: "linear-gradient(135deg,#EFF6FF 0%,#fff 60%,#F3E8FF 100%)" }}
                            data-testid="meta-ads-section"
                        >
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
                                <div className="flex items-center gap-3">
                                    <div className="w-11 h-11 rounded-xl bg-blue-600 text-white flex items-center justify-center font-extrabold text-lg flex-shrink-0" style={{ fontFamily: "Tajawal" }}>
                                        f
                                    </div>
                                    <div className="min-w-0">
                                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>Meta Ads (Facebook + Instagram)</h2>
                                        <p className="text-xs text-muted-foreground">
                                            ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري لصرف اليوم
                                            {metaSummary.last_sync_at && (
                                                <span className="ms-2 text-blue-700">• آخر مزامنة: {new Date(metaSummary.last_sync_at).toLocaleString("ar-SA", { dateStyle: "short", timeStyle: "short" })}</span>
                                            )}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={async () => {
                                        try {
                                            toast.loading("جاري تحديث صرف Meta لليوم…", { id: "meta-sync" });
                                            const { data } = await api.post("/meta/sync", { days: 1 });
                                            toast.success(`تم تحديث صرف اليوم (${data.upserted || 0} صف)`, { id: "meta-sync" });
                                            fetchMetaSummary();
                                        } catch (e) {
                                            // Backend now returns detail as a structured object:
                                            // { message: "<friendly Arabic>", status: "expired"|"error"|..., raw: "..." }
                                            // Older paths return detail as a plain string. Handle both
                                            // and NEVER show raw JSON to the user.
                                            const detail = e?.response?.data?.detail;
                                            let msg;
                                            let isExpired = false;
                                            if (detail && typeof detail === "object") {
                                                msg = detail.message || "تعذّرت المزامنة مع Meta.";
                                                isExpired = detail.status === "expired";
                                            } else if (typeof detail === "string") {
                                                msg = detail;
                                            } else {
                                                msg = "تعذّرت المزامنة مع Meta. تواصل مع الدعم إذا استمرت المشكلة.";
                                            }
                                            toast.error(msg, { id: "meta-sync", duration: 8000 });
                                            // Refresh card so the expired banner appears immediately.
                                            if (isExpired || e?.response?.status === 401) {
                                                fetchMetaSummary();
                                            }
                                        }
                                    }}
                                    className="flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-bold text-sm transition-colors w-full sm:w-auto"
                                    style={{ fontFamily: "Tajawal" }}
                                    data-testid="meta-sync-now-btn"
                                >
                                    <ArrowsClockwise size={16} weight="bold" />
                                    تحديث فوري للصرف اليوم
                                </button>
                            </div>

                            {/* Expired-token banner — shown above content so the merchant
                                can fix the link without losing visibility of historical data. */}
                            {metaSummary.connection_status === "expired" && (
                                <div
                                    className="bg-red-50 border-2 border-red-300 rounded-lg p-4 mb-4 flex flex-col sm:flex-row sm:items-center gap-3"
                                    data-testid="meta-expired-banner"
                                >
                                    <div className="flex-1 min-w-0">
                                        <div className="text-sm font-bold text-red-900 mb-1" style={{ fontFamily: "Tajawal" }}>
                                            ⚠️ الربط منتهي الصلاحية
                                        </div>
                                        <div className="text-xs text-red-800 leading-relaxed">
                                            {metaSummary.last_error_message || "انتهت صلاحية ربط Meta Ads، يرجى تحديث Access Token من الإعدادات."}
                                        </div>
                                    </div>
                                    <Link
                                        to="/settings"
                                        className="inline-flex items-center justify-center gap-1.5 px-4 py-2 bg-red-600 hover:bg-red-700 text-white rounded-md font-bold text-xs transition-colors flex-shrink-0"
                                        data-testid="meta-update-link-btn"
                                    >
                                        تحديث الربط ←
                                    </Link>
                                </div>
                            )}

                            {/* Non-expired errors (rate-limit, permission, etc) — softer banner */}
                            {metaSummary.connection_status && metaSummary.connection_status !== "expired" && metaSummary.connection_status !== "ok" && metaSummary.last_error_message && (
                                <div
                                    className="bg-amber-50 border border-amber-300 rounded-lg p-3 mb-4 text-xs text-amber-900 leading-relaxed"
                                    data-testid="meta-warn-banner"
                                >
                                    ⚠️ {metaSummary.last_error_message}
                                </div>
                            )}

                            {metaSummary.last_30d.spend === 0 && metaSummary.last_30d.orders === 0 ? (
                                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 text-sm text-blue-800 leading-relaxed" data-testid="meta-empty-state">
                                    لا توجد بيانات Meta Ads بعد. اربط حسابك مع Meta Marketing API مباشرةً من صفحة الإعدادات (App ID + App Secret + Access Token) ثم اضغط <strong>"تحديث فوري للصرف اليوم"</strong> لجلب البيانات.
                                    <div className="mt-3">
                                        <Link to="/settings" className="inline-flex items-center gap-1 px-3 py-1.5 bg-blue-600 text-white rounded-md font-bold text-xs hover:bg-blue-700 transition-colors" data-testid="meta-go-settings-btn">
                                            الذهاب إلى الإعدادات ←
                                        </Link>
                                    </div>
                                </div>
                            ) : (
                                <>
                                    {/* Today */}
                                    <div className="space-y-4">
                                        <div>
                                            <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                                اليوم ({metaSummary.today.date})
                                            </div>
                                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-spend-today">
                                                    <div className="text-xs text-muted-foreground mb-1">صرف اليوم (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-blue-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(metaSummary.today.spend)}</div>
                                                </div>
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-orders-today">
                                                    <div className="text-xs text-muted-foreground mb-1">طلبات اليوم</div>
                                                    <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(metaSummary.today.orders)}</div>
                                                </div>
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-revenue-today">
                                                    <div className="text-xs text-muted-foreground mb-1">مبيعات اليوم (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(metaSummary.today.revenue)}</div>
                                                </div>
                                                <div className={`border-2 rounded-lg p-4 ${metaSummary.today.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="meta-roas-today">
                                                    <div className="text-xs text-muted-foreground mb-1">ROAS اليوم</div>
                                                    <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: metaSummary.today.roas >= 2 ? "#047857" : "#B45309" }}>
                                                        {metaSummary.today.spend > 0 ? `${metaSummary.today.roas}x` : "—"}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Month */}
                                        <div>
                                            <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                                هذا الشهر (منذ {metaSummary.month.start})
                                            </div>
                                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-spend-month">
                                                    <div className="text-xs text-muted-foreground mb-1">الصرف الشهري (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-blue-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(metaSummary.month.spend)}</div>
                                                </div>
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-orders-month">
                                                    <div className="text-xs text-muted-foreground mb-1">طلبات الشهر</div>
                                                    <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(metaSummary.month.orders)}</div>
                                                </div>
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-revenue-month">
                                                    <div className="text-xs text-muted-foreground mb-1">مبيعات الشهر (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(metaSummary.month.revenue)}</div>
                                                </div>
                                                <div className={`border-2 rounded-lg p-4 ${metaSummary.month.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="meta-roas-month">
                                                    <div className="text-xs text-muted-foreground mb-1">ROAS الشهر</div>
                                                    <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: metaSummary.month.roas >= 2 ? "#047857" : "#B45309" }}>
                                                        {metaSummary.month.spend > 0 ? `${metaSummary.month.roas}x` : "—"}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>

                                        {/* 30-day sparkline */}
                                        {metaSummary.history && metaSummary.history.some(h => h.spend > 0) && (
                                            <div className="pt-3 border-t border-blue-200">
                                                <div className="text-xs text-muted-foreground mb-2" style={{ fontFamily: "Tajawal" }}>
                                                    صرف آخر 30 يوم — الإجمالي: {formatMoney(metaSummary.last_30d.spend)} ر.س
                                                </div>
                                                <div className="h-12 flex items-end gap-0.5">
                                                    {metaSummary.history.map((d, i) => {
                                                        const maxSpend = Math.max(1, ...metaSummary.history.map(h => h.spend));
                                                        const height = Math.max(2, (d.spend / maxSpend) * 100);
                                                        return (
                                                            <div
                                                                key={i}
                                                                className="flex-1 bg-blue-500 rounded-sm hover:bg-blue-600 transition-colors cursor-help"
                                                                style={{ height: `${height}%` }}
                                                                title={`${d.date}: ${formatMoney(d.spend)} ر.س`}
                                                            />
                                                        );
                                                    })}
                                                </div>
                                            </div>
                                        )}

                                        {/* Footer: link to detailed report */}
                                        <div className="flex justify-end">
                                            <Link
                                                to="/reports/ads"
                                                className="text-xs font-semibold text-blue-800 hover:text-blue-900 hover:underline inline-flex items-center gap-1"
                                                data-testid="meta-card-details-link"
                                                style={{ fontFamily: "Tajawal" }}
                                            >
                                                التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←
                                            </Link>
                                        </div>
                                    </div>
                                </>
                            )}
                        </div>
                    )}

                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-4 sm:p-6">
                        <div className="flex items-center justify-between mb-6">
                            <div>
                                <h2 className="text-xl sm:text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>الأداء الشهري</h2>
                                <p className="text-sm text-muted-foreground mt-1">المبيعات والأرباح الصافية عبر الأشهر</p>
                            </div>
                        </div>
                        {monthly.length === 0 ? (
                            <div className="text-center py-16 text-muted-foreground">
                                لا توجد بيانات بعد. قم برفع ملف Excel لبدء التحليل.
                            </div>
                        ) : (
                            <div className="h-72" data-testid="monthly-chart">
                                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                    <LineChart data={monthly} margin={{ top: 8, right: 20, left: 20, bottom: 8 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                        <XAxis dataKey="month" tick={{ fontSize: 12, fontFamily: "Cairo" }} reversed />
                                        <YAxis tick={{ fontSize: 12, fontFamily: "Cairo" }} orientation="right" />
                                        <Tooltip contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                        <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
                                        <Line type="monotone" dataKey="sales" name="المبيعات" stroke="#0A3622" strokeWidth={2.5} dot={{ r: 4 }} />
                                        <Line type="monotone" dataKey="profit" name="صافي الربح" stroke="#D4AF37" strokeWidth={2.5} dot={{ r: 4 }} />
                                    </LineChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </div>

                    {/* Recent analyses */}
                    <div className="rounded-xl border border-border bg-white p-4 sm:p-6">
                        <div className="flex items-center justify-between mb-2">
                            <h2 className="text-xl sm:text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>آخر التحاليل</h2>
                            <Link to="/history" className="text-brand text-sm font-semibold hover:underline inline-flex items-center gap-1" data-testid="see-all-history">
                                عرض الكل <CaretLeft size={16} />
                            </Link>
                        </div>
                        <p className="text-xs text-muted-foreground mb-5">
                            * صافي ربح كل تحليل محسوب قبل خصم التكاليف اليومية (تكاليف الإعلانات والمنتجات اليومية تُخصم على مستوى الفترة في بطاقة "صافي الربح النهائي").
                        </p>
                        {recent.length === 0 ? (
                            <div className="text-center py-10 text-muted-foreground">لم تقم بإجراء أي تحليل بعد.</div>
                        ) : (
                            <div className="overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
                                <table className="w-full text-right min-w-[640px]" data-testid="recent-analyses-table">
                                    <thead>
                                        <tr className="text-sm text-muted-foreground border-b border-border">
                                            <th className="pb-3 font-semibold">الاسم</th>
                                            <th className="pb-3 font-semibold">التاريخ</th>
                                            <th className="pb-3 font-semibold">الطلبات</th>
                                            <th className="pb-3 font-semibold">المبيعات</th>
                                            <th className="pb-3 font-semibold">صافي ربح التحليل *</th>
                                            <th className="pb-3 font-semibold"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {recent.map((a) => (
                                            <tr key={a.id} className="border-b border-border last:border-0">
                                                <td className="py-3 font-medium">{a.name}</td>
                                                <td className="py-3 num text-muted-foreground">{a.date}</td>
                                                <td className="py-3 num">{formatInt(a.total_orders)}</td>
                                                <td className="py-3 num font-semibold">{formatMoney(a.total_sales)}</td>
                                                <td className={`py-3 num font-semibold ${a.net_profit >= 0 ? "text-green-700" : "text-red-700"}`}>
                                                    {formatMoney(a.net_profit)}
                                                </td>
                                                <td className="py-3">
                                                    <div className="inline-flex items-center gap-3">
                                                        <Link to={`/analyses/${a.id}`} className="text-brand font-semibold hover:underline" data-testid={`view-analysis-${a.id}`}>
                                                            تفاصيل
                                                        </Link>
                                                        {a.is_legacy && (
                                                            <button
                                                                type="button"
                                                                onClick={() => openReprocessPicker(a.id)}
                                                                disabled={reprocessingId === a.id}
                                                                title="أعد رفع نفس ملف Excel لاستخراج تواريخ الطلبات الفردية وتفعيل فلتر تاريخ إنشاء الطلب"
                                                                className="inline-flex items-center gap-1 text-xs font-semibold text-amber-700 hover:text-amber-900 disabled:opacity-50"
                                                                data-testid={`reprocess-analysis-${a.id}`}
                                                            >
                                                                <ArrowsClockwise size={14} weight="bold" />
                                                                {reprocessingId === a.id ? "جاري..." : "إعادة معالجة"}
                                                            </button>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
