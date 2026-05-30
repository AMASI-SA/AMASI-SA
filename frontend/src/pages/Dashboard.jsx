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
    const fileInputRef = useRef(null);
    const pendingReprocessId = useRef(null);

    const fetchDashboard = async (f = filters) => {
        setLoading(true);
        try {
            const qs = filtersToQueryString(f);
            const { data } = await api.get(`/dashboard${qs ? "?" + qs : ""}`);
            setData(data);
            setLastUpdated(Date.now());
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

                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-6">
                            <div>
                                <h2 className="text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>الأداء الشهري</h2>
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
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-2">
                            <h2 className="text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>آخر التحاليل</h2>
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
                            <div className="overflow-x-auto">
                                <table className="w-full text-right" data-testid="recent-analyses-table">
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
