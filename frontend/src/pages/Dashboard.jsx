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
    Percent,
    CalendarBlank,
    Wallet,
    Bank,
    ArrowsClockwise,
    EyeSlash,
    GearSix,
    MagnifyingGlass,
    X,
    Warning,
    CheckCircle,
    Equals,
    Info,
} from "@phosphor-icons/react";import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { toast } from "sonner";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import { todaySA } from "../lib/dates";
import { SalaryAccrualSummaryCard } from "../components/EmployeeBalanceCard";
import { useAuth } from "../context/AuthContext";
import { ALL_KPI_CARDS } from "../lib/dashboardCards";
import { buildMissingMezanCostHref } from "../lib/mezanV2CostLinks";
import ProductCostCard from "../components/ProductCostCard";
import ProfitSummaryCard from "../components/ProfitSummaryCard";
import SnapchatOfficialCard from "../components/SnapchatOfficialCard";
import SnapchatAccountsCards from "../components/SnapchatAccountsCards";
import AlertsCard from "../components/AlertsCard";
import ElectronicNetDebugModal from "../components/ElectronicNetDebugModal";

function formatRelative(ms) {
    if (ms < 5_000) return "الآن";
    if (ms < 60_000) return `قبل ${Math.floor(ms / 1000)} ثانية`;
    if (ms < 3_600_000) return `قبل ${Math.floor(ms / 60_000)} دقيقة`;
    return `قبل ${Math.floor(ms / 3_600_000)} ساعة`;
}

function KpiInfoTooltip({ explanation, testid }) {
    // iter-48 — hover/tap-to-reveal explanation for each KPI card.
    // Uses React state (not CSS-only) so it works on touch devices via
    // explicit click, and dismisses cleanly on mouseleave/blur.
    const [open, setOpen] = useState(false);
    const ref = useRef(null);

    // Close when the user taps anywhere else (mobile/touch UX).
    useEffect(() => {
        if (!open) return;
        const onDocClick = (e) => {
            if (!ref.current?.contains(e.target)) setOpen(false);
        };
        const onEsc = (e) => { if (e.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", onDocClick);
        document.addEventListener("keydown", onEsc);
        return () => {
            document.removeEventListener("mousedown", onDocClick);
            document.removeEventListener("keydown", onEsc);
        };
    }, [open]);

    if (!explanation) return null;
    return (
        <span ref={ref} className="relative inline-flex items-center">
            <button
                type="button"
                onMouseEnter={() => setOpen(true)}
                onMouseLeave={() => setOpen(false)}
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(v => !v); }}
                onFocus={() => setOpen(true)}
                onBlur={() => setOpen(false)}
                className="p-0.5 rounded-full text-muted-foreground/60 hover:text-brand hover:bg-brand/10 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand/40"
                aria-label="كيف تم احتساب هذه البطاقة؟"
                data-testid={testid}
            >
                <Info size={14} weight="bold" />
            </button>
            {open && (
                <div
                    role="tooltip"
                    className="absolute z-40 top-full mt-2 start-0 sm:start-auto sm:end-0 w-72 sm:w-80 rounded-lg bg-slate-900 text-white text-[11px] leading-relaxed px-3 py-2.5 shadow-xl pointer-events-none whitespace-pre-line text-start"
                    style={{ fontFamily: "Tajawal" }}
                    data-testid={`${testid}-content`}
                >
                    <div className="font-bold text-[11px] mb-1 text-amber-200 flex items-center gap-1">
                        <Info size={12} weight="bold" />
                        طريقة الاحتساب
                    </div>
                    {explanation}
                    {/* little arrow pointing up to the ⓘ icon */}
                    <span className="absolute -top-1 start-3 w-2 h-2 bg-slate-900 rotate-45" />
                </div>
            )}
        </span>
    );
}

function Kpi({ icon: Icon, label, value, hint, accent = false, testid, onHide, onDetails, explanation }) {
    return (
        <div
            className={[
                "group relative rounded-xl border border-border p-3 sm:p-5 bg-white transition-shadow hover:shadow-sm",
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
            <div className="text-sm text-muted-foreground mb-1 flex items-center gap-1.5">
                <span>{label}</span>
                <KpiInfoTooltip explanation={explanation} testid={`${testid}-info-btn`} />
            </div>
            <div className="flex items-end justify-between gap-2">
                <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{value}</div>
                {onDetails && (
                    <button
                        type="button"
                        onClick={onDetails}
                        className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-bold bg-slate-50 hover:bg-brand/10 text-slate-600 hover:text-brand border border-slate-200 hover:border-brand/30 transition-colors"
                        data-testid={`${testid}-details-btn`}
                        title="عرض تفاصيل الحساب ومطابقتها مع سلة"
                    >
                        <MagnifyingGlass size={12} weight="bold" />
                        تفاصيل
                    </button>
                )}
            </div>
        </div>
    );
}

import AdvancedFilters, { filtersToQueryString, defaultFilters } from "../components/AdvancedFilters";
import UnifiedPaymentGatewaysCard from "../components/UnifiedPaymentGatewaysCard";
import PendingOrdersCard from "../components/PendingOrdersCard";

export default function Dashboard({ sourceMode = "legacy" }) {
    const { user } = useAuth();
    const isMezanV2 = sourceMode === "mezan_v2";
    const dashboardEndpoint = isMezanV2 ? "/dashboard-v2" : "/dashboard";
    const providerEndpoint = (provider) => isMezanV2
        ? `/dashboard-v2/${provider}-summary`
        : `/dashboard/${provider}-summary`;
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState(defaultFilters("today"));
    const [reprocessingId, setReprocessingId] = useState(null);
    const [hiddenCards, setHiddenCards] = useState([]);
    const [lastUpdated, setLastUpdated] = useState(null);
    const [nowTick, setNowTick] = useState(Date.now());
    const [snapSummary, setSnapSummary] = useState(null);
    const [snapDayInfo, setSnapDayInfo] = useState(null); // {tz, startRiyadh, endRiyadh}
    // iter-45 — Electronic Net debug modal trigger
    const [showElectronicNetDebug, setShowElectronicNetDebug] = useState(false);
    // Per-account today-spend breakdown (iteration 18). Lets the dashboard
    // Snapchat card display each enabled ad account's TODAY spend on its
    // own card, replacing the previous "this-month" summary block.
    const [snapAccountsBreakdown, setSnapAccountsBreakdown] = useState(null);
    const [metaSummary, setMetaSummary] = useState(null);
    const [tiktokSummary, setTiktokSummary] = useState(null);
    const [adsAutoSyncStatus, setAdsAutoSyncStatus] = useState(null);
    const [refreshingAll, setRefreshingAll] = useState(false);
    // Iter-204 — bump this counter to force the per-account ad cards
    // to refetch their data without a full page reload.
    const [adCardsRefreshSignal, setAdCardsRefreshSignal] = useState(0);
    const fileInputRef = useRef(null);
    const pendingReprocessId = useRef(null);

    const fetchSnapSummary = async () => {
        try {
            const { data } = await api.get(providerEndpoint("snapchat"));
            setSnapSummary(data);
        } catch {
            /* non-critical */
        }
    };

    // Per-account today-spend (iteration 18). Loaded in parallel with the
    // cross-account snapchat-summary so the card can render one cell per
    // enabled ad account (replacing the "monthly" block per merchant
    // request).
    const fetchSnapAccountsBreakdown = async () => {
        if (isMezanV2) {
            setSnapAccountsBreakdown(null);
            return;
        }
        try {
            const { data } = await api.get("/snapchat/accounts-summary");
            setSnapAccountsBreakdown(data);
        } catch {
            /* non-critical — card falls back gracefully */
        }
    };

    const fetchMetaSummary = async () => {
        try {
            const { data } = await api.get(providerEndpoint("meta"));
            setMetaSummary(data);
        } catch {
            /* non-critical */
        }
    };

    const fetchTiktokSummary = async () => {
        try {
            const { data } = await api.get(providerEndpoint("tiktok"));
            setTiktokSummary(data);
        } catch {
            /* non-critical */
        }
    };

    const fetchAdsAutoSyncStatus = async () => {
        if (!isMezanV2) return;
        try {
            const { data } = await api.get("/integrations-v2/ads-auto-sync/status");
            setAdsAutoSyncStatus(data);
        } catch {
            /* the dashboard remains usable if scheduler status is unavailable */
        }
    };

    // ── One-button refresh for ALL ad platforms ───────────────────────────
    // Fans out to Snap / Meta / TikTok in parallel, reports per-platform
    // success/failure, NEVER clears existing data on failure. Each platform
    // is independent so a Meta token expiry doesn't block a successful
    // Snap sync.
    const refreshAllAds = async () => {
        if (refreshingAll) return;
        setRefreshingAll(true);
        toast.loading("جاري تحديث جميع منصات الإعلانات…", { id: "refresh-all" });
        if (isMezanV2) {
            await Promise.all([
                fetchSnapSummary(), fetchMetaSummary(), fetchTiktokSummary(),
                refreshSilently(),
            ]);
            setAdCardsRefreshSignal((n) => n + 1);
            setRefreshingAll(false);
            toast.success("تمت إعادة قراءة بيانات الإعلانات من ميزان 2", {
                id: "refresh-all",
            });
            return;
        }
        const todayStr = snapSummary?.today?.date
            || metaSummary?.today?.date
            || tiktokSummary?.today?.date
            || todaySA();

        // Each task returns {platform, ok, msg} so the consolidated toast
        // can show exactly what worked.
        const runSnap = async () => {
            if (!snapSummary) return { platform: "Snapchat", ok: false, msg: "غير مفعّل" };
            try {
                // Mirror the proven DailyCosts flow: single-date GET + manual upsert.
                const { data } = await api.get(
                    `/snapchat/daily-spend?date=${encodeURIComponent(todayStr)}`);
                const amount = Number(data?.spend || 0);
                let existing = {};
                try {
                    const list = await api.get("/daily-costs");
                    existing = (list.data || []).find(r => r.date === todayStr) || {};
                } catch { /* row may not exist */ }
                await api.post("/daily-costs", {
                    date: todayStr,
                    snapchat_ads: amount,
                    snapchat_ads_2: Number(existing.snapchat_ads_2 || 0),
                    tiktok_ads: Number(existing.tiktok_ads || 0),
                    instagram_ads: Number(existing.instagram_ads || 0),
                    google_ads: Number(existing.google_ads || 0),
                    product_costs: Number(existing.product_costs || 0),
                    notes: existing.notes || "auto from Snapchat",
                });
                return { platform: "Snapchat", ok: true, msg: `${amount.toFixed(2)} ر.س` };
            } catch (e) {
                const d = e?.response?.data?.detail;
                return { platform: "Snapchat", ok: false,
                    msg: (typeof d === "object" ? d.message : d) || "غير مربوط" };
            }
        };

        const runMeta = async () => {
            if (!metaSummary) return { platform: "Meta", ok: false, msg: "غير مفعّل" };
            try {
                const { data } = await api.post("/meta/sync", { days: 1 });
                return { platform: "Meta", ok: true, msg: `${data.upserted || 0} صف` };
            } catch (e) {
                const d = e?.response?.data?.detail;
                return { platform: "Meta", ok: false,
                    msg: (typeof d === "object" ? d.message : d) || "غير مربوط" };
            }
        };

        const runTiktok = async () => {
            // No external API yet — re-aggregate local webhook data.
            try {
                await api.get(providerEndpoint("tiktok"));
                return { platform: "TikTok", ok: true, msg: "تم تحديث البيانات المحلية" };
            } catch (e) {
                return { platform: "TikTok", ok: false, msg: "فشل" };
            }
        };

        const results = await Promise.all([runSnap(), runMeta(), runTiktok()]);
        // Iter-204 — Trigger backend half-hour ad-account sync NOW
        // (force=true bypasses the per-day idempotency guard so today's
        // freshly arrived spend rows are picked up). This updates
        // ad_account balance + debt + cumulative spend used by the
        // Dashboard per-account cards AND the AdAccounts page.
        try {
            const today = (snapSummary?.today?.date
                || metaSummary?.today?.date
                || tiktokSummary?.today?.date
                || todaySA());
            await api.post("/ad-accounts/sync-all", {
                from_date: today, to_date: today, force: true,
            });
        } catch { /* non-blocking; per-platform pulls already done */ }
        // Re-read everything so totals + cards reflect new spend.
        await Promise.all([fetchSnapSummary(), fetchSnapAccountsBreakdown(),
                           fetchMetaSummary(),
                           fetchTiktokSummary(), refreshSilently()]);
        // Iter-204 — kick the per-account cards to refetch.
        setAdCardsRefreshSignal((n) => n + 1);

        const okCount = results.filter(r => r.ok).length;
        const summary = results.map(r =>
            `${r.ok ? "✓" : "✗"} ${r.platform}: ${r.msg}`).join("  •  ");
        if (okCount === results.length) {
            toast.success(`تم تحديث جميع المنصات (${okCount}/${results.length})  •  ${summary}`,
                { id: "refresh-all", duration: 7000 });
        } else if (okCount === 0) {
            toast.error(`فشل التحديث  •  ${summary}`,
                { id: "refresh-all", duration: 9000 });
        } else {
            toast.warning(`تحديث جزئي (${okCount}/${results.length})  •  ${summary}`,
                { id: "refresh-all", duration: 9000 });
        }
        setRefreshingAll(false);
    };

    // Best-effort daily auto-sync — fires once on dashboard mount.
    // Idempotent: the backend skips if the last sync was within 23h.
    const autoSyncMetaIfStale = async () => {
        if (isMezanV2) return;
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
            const { data } = await api.get(`${dashboardEndpoint}${qs ? "?" + qs : ""}`);
            setData(data);
            setLastUpdated(Date.now());
            fetchSnapSummary();  // refresh Snapchat card alongside
            fetchSnapAccountsBreakdown();
            fetchMetaSummary();
            fetchTiktokSummary();
            fetchAdsAutoSyncStatus();
        } finally {
            setLoading(false);
        }
    };

    // Silent refresh — same fetch but without flicker (no loading state).
    const refreshSilently = async () => {
        try {
            const qs = filtersToQueryString(filters);
            const { data } = await api.get(`${dashboardEndpoint}${qs ? "?" + qs : ""}`);
            setData(data);
            setLastUpdated(Date.now());
            fetchSnapSummary();
            fetchSnapAccountsBreakdown();
            fetchMetaSummary();
            fetchTiktokSummary();
            fetchAdsAutoSyncStatus();
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
    useEffect(() => { if (!isMezanV2) autoSyncMetaIfStale(); /* eslint-disable-next-line */ }, [isMezanV2]);

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
    const missingMezanCostHref = isMezanV2
        ? buildMissingMezanCostHref(data?.product_cost_v2, filters)
        : "/product-costs?tab=missing";
    const missingMezanProducts = data?.product_cost_v2?.missing_products || [];
    const sallaFallbackProducts = Number(
        data?.product_cost_v2?.salla_fallback_products_count || 0,
    );
    const opensSingleMissingMezanProduct = missingMezanCostHref.includes("product=");

    return (
        <div className="space-y-6 sm:space-y-8 animate-fade-in-up" data-testid="dashboard-page">
            <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls,.xlsm"
                onChange={handleReprocessFile}
                className="hidden"
                data-testid="reprocess-file-input"
            />
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3 md:gap-4">
                <div className="min-w-0">
                    <div className="text-xs sm:text-sm text-muted-foreground mb-1">مرحباً، {user?.name || "ضيف"}</div>
                    <h1 className="text-2xl sm:text-3xl lg:text-4xl xl:text-5xl font-extrabold tracking-tight text-foreground" style={{ fontFamily: "Tajawal" }}>
                        لوحة التحكم
                    </h1>
                    {isMezanV2 && (
                        <div className="mt-2 inline-flex rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-bold text-violet-800" data-testid="dashboard-v2-source-badge">
                            مصادر الطلبات والمنتجات والإعلانات: ميزان 2
                        </div>
                    )}
                    <p className="text-muted-foreground mt-1.5 text-xs sm:text-sm md:text-base leading-relaxed">
                        {(filters.from || filters.to)
                            ? `عرض البيانات من ${filters.from || "البداية"} إلى ${filters.to || "الآن"}`
                            : "نظرة شاملة على أدائك المالي عبر جميع التحاليل المحفوظة."}
                    </p>
                </div>
            </div>

            {/* Iter-159h — Smart Settlement Alerts card */}
            <AlertsCard />

            {/* Advanced filters: date preset + payment + shipping */}
            <div className="flex flex-col sm:flex-row sm:items-stretch gap-2">
                <div className="flex-1 min-w-0">
                    <AdvancedFilters value={filters} onChange={setFilters} defaultPreset="today" />
                </div>
                <div className="flex gap-2 flex-wrap">
                    <button
                        type="button"
                        onClick={refreshAllAds}
                        disabled={refreshingAll}
                        title="تحديث صرف اليوم من جميع منصات الإعلانات (Snapchat + Meta + TikTok) في خطوة واحدة"
                        className="flex-1 sm:flex-initial px-3 py-2 rounded-lg bg-gradient-to-r from-yellow-400 via-pink-500 to-blue-600 text-white font-bold text-xs sm:text-sm hover:opacity-90 transition-opacity disabled:opacity-50 inline-flex items-center justify-center gap-1.5 whitespace-nowrap"
                        data-testid="refresh-all-ads-btn"
                    >
                        <ArrowsClockwise size={16} weight="bold" className={refreshingAll ? "animate-spin" : ""} />
                        <span className="hidden sm:inline">{refreshingAll ? "جاري التحديث…" : "تحديث جميع الإعلانات"}</span>
                        <span className="sm:hidden">{refreshingAll ? "جاري…" : "كل الإعلانات"}</span>
                    </button>
                    <button
                        type="button"
                        onClick={() => fetchDashboard(filters)}
                        disabled={loading}
                        title="تحديث البيانات الآن (يحدّث تلقائياً كل دقيقة)"
                        className="px-3 py-2 rounded-lg border border-border bg-white font-bold text-xs sm:text-sm hover:bg-accent transition-colors disabled:opacity-50 inline-flex items-center justify-center gap-1.5"
                        data-testid="dashboard-refresh-btn"
                    >
                        <ArrowsClockwise size={16} weight="bold" className={loading ? "animate-spin" : ""} />
                        تحديث
                    </button>
                </div>
            </div>
            {lastUpdated && (
                <div className="-mt-2 space-y-1 text-xs text-muted-foreground">
                    <div data-testid="dashboard-last-updated">
                        آخر تحديث للعرض: {formatRelative(nowTick - lastUpdated)} • يحدِّث تلقائياً كل دقيقة
                    </div>
                    {isMezanV2 && adsAutoSyncStatus && (
                        <div className={adsAutoSyncStatus.enabled ? "text-emerald-700" : "text-rose-700"} data-testid="dashboard-v2-ads-auto-sync-status">
                            {adsAutoSyncStatus.enabled
                                ? `مزامنة الإعلانات من الخادم كل ${adsAutoSyncStatus.interval_minutes || 5} دقائق حتى والمتصفح مغلق • TikTok عبر Webhook`
                                : "مزامنة الإعلانات التلقائية متوقفة في إعدادات الخادم"}
                        </div>
                    )}
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
                    {/* Iter-138 — Unified salary accrual block (same
                        data as المركز المالي / مركز الإدخال المالي /
                        لوحة العمليات).  Hide-able from Settings ↦
                        بطاقات خاصة via 'salary_accrual_card'. */}
                    {!isMezanV2 && !hiddenCards.includes("salary_accrual_card") && (
                        <section
                            className="rounded-xl border-2 border-emerald-200 bg-emerald-50/30 p-4"
                            data-testid="dashboard-salary-accrual-section"
                        >
                            <h2 className="text-sm font-extrabold text-slate-800 mb-3 flex items-center gap-2">
                                <span>👤</span>
                                صافي مستحقات الموظفين الحالية
                                <span className="text-[10px] font-normal text-slate-500">
                                    (نفس أرقام «المركز المالي» — مصدر موحَّد)
                                </span>
                            </h2>
                            <SalaryAccrualSummaryCard />
                        </section>
                    )}

                    {/* Missing product costs alert (iteration 19) — surface
                        any unmatched SKUs from the current dashboard window
                        so the merchant knows real profit may be off. */}
                    {totals.missing_product_cost_count > 0 && (
                        <Link
                            to={missingMezanCostHref}
                            className="block rounded-xl border-2 border-amber-300 bg-amber-50 p-3 text-sm hover:bg-amber-100 transition-colors"
                            data-testid="dashboard-missing-product-costs-alert"
                        >
                            <div className="flex items-center gap-2 flex-wrap text-amber-900">
                                <span className="text-base">⚠️</span>
                                <strong>
                                    {totals.missing_product_cost_count} منتج
                                    {isMezanV2 ? " مباع بدون تكلفة ميزان" : " بدون تكلفة"}
                                </strong>
                                <span className="text-amber-800/80">
                                    {isMezanV2 && sallaFallbackProducts > 0
                                        ? `— ${sallaFallbackProducts} منها محسوب مؤقتًا بتكلفة سلة؛ أضف تكلفة ميزان لاعتماد الربح.`
                                        : "— صافي الربح غير دقيق حتى تُحدِّد تكلفة هذه المنتجات."}
                                </span>
                                <span className="ms-auto text-xs font-bold text-amber-800 underline">
                                    {isMezanV2 && opensSingleMissingMezanProduct
                                        ? "فتح المنتج وإضافة التكلفة ←"
                                        : "عرض المنتجات الناقصة ←"}
                                </span>
                            </div>
                        </Link>
                    )}
                    {/* Iteration 24: Excel-without-products alert. Orders
                        coming from Excel uploads (no products[] array)
                        cannot have their product cost auto-computed. */}
                    {totals.excel_no_products_count > 0 && (
                        <div
                            className="rounded-xl border-2 border-orange-300 bg-orange-50 p-3 text-sm text-orange-900"
                            data-testid="dashboard-excel-no-products-alert"
                        >
                            <div className="flex items-start gap-2 flex-wrap">
                                <span className="text-base">📦</span>
                                <div className="flex-1">
                                    <strong>{totals.excel_no_products_count} طلب من Excel بدون تفاصيل منتجات</strong>
                                    <span className="text-orange-800/80"> — تكلفة المنتجات لهذه الطلبات غير محسوبة (ربح غير مكتمل). يُنصح بربط Make.com لاستلام الطلبات مع تفاصيل المنتجات تلقائياً.</span>
                                </div>
                            </div>
                        </div>
                    )}
                    {/* Iteration 26: Product Cost Card — visible at the
                        top of the KPI region so the merchant always sees
                        today/month product cost + linked vs missing counts.
                        Refreshes whenever filter range changes.
                        iter-54: hideable via Settings → "بطاقات خاصة". */}
                    {!hiddenCards.includes("product_cost_card") && (
                        <ProductCostCard
                            refreshKey={`${filters.from || ""}-${filters.to || ""}`}
                            endpoint={isMezanV2 ? "/dashboard-v2/product-cost-summary" : "/product-costs/summary"}
                            recomputeEndpoint={isMezanV2 ? null : "/product-costs/recompute"}
                            sourceLabel={isMezanV2
                                ? "ميزان 2 + المكونات والخدمات • سلة احتياطياً"
                                : "من جدول product_costs • SKU/Product ID"}
                            missingHref={isMezanV2
                                ? (summary) => buildMissingMezanCostHref(
                                    summary,
                                    {
                                        from: summary?.period?.from,
                                        to: summary?.period?.to,
                                    },
                                )
                                : "/product-costs?tab=missing"}
                            missingLabel={isMezanV2 ? "بدون تكلفة ميزان" : "بدون تكلفة"}
                        />
                    )}
                    {/* iter-56 — Salla wallet reconciliation alert: if the
                        merchant's reference (entered manually from Salla's
                        "غير المفوّترة" screen) is off from the system net
                        and we have recorded settlements that exactly explain
                        the gap, show a helpful banner instead of letting them
                        think it's a bug. */}
                    {(() => {
                        const t = totals || {};
                        const ref = Number(t.salla_electronic_net_reference || 0);
                        const sysNet = Number(t.electronic_net || 0);
                        const settles = Number(t.settlements_by_provider?.salla?.total_adjustment || 0);
                        if (!ref || settles <= 0) return null;
                        const diff = Math.abs(sysNet - ref);
                        const explainsRoughly = Math.abs(diff - settles) < 0.5;
                        if (!explainsRoughly) return null;
                        return (
                            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3" data-testid="salla-wallet-reconcile-alert">
                                <div className="w-9 h-9 rounded-lg bg-amber-100 text-amber-700 flex items-center justify-center shrink-0">
                                    <span className="font-extrabold">!</span>
                                </div>
                                <div className="flex-1 text-sm">
                                    <div className="font-bold text-amber-900 mb-0.5">الفرق مع محفظة سلة مفسَّر</div>
                                    <div className="text-amber-800">
                                        فرق <span className="num font-extrabold">{diff.toFixed(2)}</span> ر.س بين رقم سلة المرجعي ورقم النظام يطابق إجمالي{" "}
                                        <Link to="/settlements" className="font-bold underline">تسويات سلة المسجَّلة</Link>{" "}
                                        لهذه الفترة (<span className="num">{settles.toFixed(2)}</span> ر.س). هذا ليس خطأ — هكذا تعمل دورة 14 يوم.
                                    </div>
                                </div>
                            </div>
                        );
                    })()}
                    {/* iter-49 — Executive profit summary (sales → deductions → net) */}
                    <ProfitSummaryCard
                        totals={totals}
                        shippingBreakdown={data?.shipping_breakdown || []}
                        fromDate={filters.from}
                        toDate={filters.to}
                        productCostBreakdown={isMezanV2 ? data?.product_cost_v2 : null}
                        adsBreakdownEndpoint={isMezanV2
                            ? "/dashboard-v2/ads-cost-breakdown"
                            : "/dashboard/ads-cost-breakdown"}
                    />
                    {/* KPI grid (config-driven, supports per-card hide) */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3 sm:gap-4">
                        {ALL_KPI_CARDS
                            .filter((c) => !hiddenCards.includes(c.id))
                            .map((c) => {
                                const rawVal = c.value(totals);
                                // iter-44 — `format` overrides the built-in
                                // money/int formatters; we also treat null
                                // as "—" for money KPIs so ROAS/CPA don't
                                // render "0 ر.س" when there's no ad spend.
                                let display;
                                if (typeof c.format === "function") {
                                    display = c.format(rawVal);
                                } else if (c.money) {
                                    display = rawVal == null ? "—" : formatMoney(rawVal);
                                } else if (c.isInt) {
                                    display = formatInt(rawVal);
                                } else {
                                    display = String(rawVal);
                                }
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
                                        onDetails={c.id === "electronic_net"
                                            ? () => setShowElectronicNetDebug(true)
                                            : undefined}
                                        explanation={c.explanation}
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

                    {/* Iter-81 — Central Payment Gateways breakdown. Reads
                        from /api/payment-gateway-metrics so Dashboard shows
                        IDENTICAL numbers to Reports / Accounts / Reconciliation. */}
                    <UnifiedPaymentGatewaysCard
                        qs={filtersToQueryString(filters)}
                        testid="dashboard-unified-gateways"
                        fromDate={filters.from}
                        toDate={filters.to}
                    />

                    {/* Iter-83 — Pending orders callout (separate from net). */}
                    <PendingOrdersCard
                        qs={filtersToQueryString(filters)}
                        testid="dashboard-pending-orders"
                    />

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
                                            {isMezanV2
                                                ? "قراءة مباشرة من حسابات Snapchat المحددة داخل ميزان 2"
                                                : snapSummary.source === "snapchat_pixel"
                                                ? "الطلبات والعائد من Snapchat Pixel — تحديث تلقائي مع زر التحديث"
                                                : "الطلبات والعائد من المتجر (لا يوجد Pixel نشط بعد) — اضغط تحديث لجلب بيانات Snapchat"}
                                            {snapSummary.last_fetched_at && (
                                                <span className="ms-2 text-yellow-700">• آخر جلب: {new Date(snapSummary.last_fetched_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}</span>
                                            )}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={async () => {
                                        if (isMezanV2) {
                                            await fetchSnapSummary();
                                            toast.success("تمت إعادة قراءة بيانات Snapchat من ميزان 2");
                                            return;
                                        }
                                        try {
                                            // Use the EXACT same flow as DailyCosts page's
                                            // working "جلب من سناب" button: hit the single-date
                                            // GET endpoint (proven reliable — returns spend only,
                                            // no conversion-metric attribution-window pitfalls).
                                            const todayStr = snapSummary.today.date;
                                            toast.loading("جاري جلب صرف Snapchat لليوم…", { id: "snap-fetch" });
                                            const { data } = await api.get(
                                                `/snapchat/daily-spend?date=${encodeURIComponent(todayStr)}`
                                            );
                                            const amount = Number(data?.spend || 0);
                                            const native = data?.native_currency;
                                            const fx = Number(data?.fx_rate || 1);

                                            // Cache TZ info for the persistent banner.
                                            if (data?.ad_account_timezone) {
                                                setSnapDayInfo({
                                                    tz: data.ad_account_timezone,
                                                    startRiyadh: data.snap_day_start_riyadh,
                                                    endRiyadh: data.snap_day_end_riyadh,
                                                });
                                            }

                                            // Persist into daily_costs so the dashboard totals + cards
                                            // pick it up. We preserve any other fields on the existing
                                            // row (snapchat_ads_2, tiktok, etc.) by reading-then-merging.
                                            let existing = {};
                                            try {
                                                const list = await api.get("/daily-costs");
                                                existing = (list.data || []).find(r => r.date === todayStr) || {};
                                            } catch { /* row may not exist yet */ }
                                            await api.post("/daily-costs", {
                                                date: todayStr,
                                                snapchat_ads: amount,
                                                snapchat_ads_2: Number(existing.snapchat_ads_2 || 0),
                                                tiktok_ads: Number(existing.tiktok_ads || 0),
                                                instagram_ads: Number(existing.instagram_ads || 0),
                                                google_ads: Number(existing.google_ads || 0),
                                                product_costs: Number(existing.product_costs || 0),
                                                notes: native && native !== "SAR"
                                                    ? `auto from Snapchat (${native}→SAR ×${fx})`
                                                    : "auto from Snapchat",
                                            });

                                            // Friendly success message — distinguishes between
                                            // "fetched, spend > 0", "fetched but zero" (no campaigns
                                            // or TZ-edge), and includes FX info when relevant.
                                            if (amount === 0) {
                                                const startR = data?.snap_day_start_riyadh;
                                                const endR = data?.snap_day_end_riyadh;
                                                const boundary = startR && endR
                                                    ? `يوم ${todayStr} بتوقيت الرياض (${startR} → ${endR})`
                                                    : `يوم ${todayStr} بتوقيت الرياض`;
                                                toast(`تم الجلب — صرف ${boundary} = 0.00 ر.س. تأكد من وجود حملات نشطة أو انتظر بدء صرف اليوم.`,
                                                    { id: "snap-fetch", duration: 10000, icon: "ℹ️" });
                                            } else if (native && native !== "SAR" && fx !== 1) {
                                                toast.success(`تم تحديث صرف اليوم: ${amount.toFixed(2)} ر.س (${data.spend_native} ${native} × ${fx})`,
                                                    { id: "snap-fetch" });
                                            } else {
                                                toast.success(`تم تحديث صرف اليوم: ${amount.toFixed(2)} ر.س`,
                                                    { id: "snap-fetch" });
                                            }
                                            fetchSnapSummary();
                                            refreshSilently();
                                        } catch (e) {
                                            // Friendly Arabic translation for known Snap errors
                                            // (mirrors the bulk-button error handling).
                                            const detail = e?.response?.data?.detail;
                                            const raw = (typeof detail === "object" ? detail.message : detail) || e.message || "";
                                            const lower = String(raw).toLowerCase();
                                            let friendly;
                                            if (lower.includes("unsupported stats query")) {
                                                friendly = "هذا الاستعلام غير مدعوم — قد يكون Snap Pixel غير مفعّل أو المقياس غير متاح لحساب الإعلانات.";
                                            } else if (lower.includes("invalid_token") || lower.includes("401") || lower.includes("authorization")) {
                                                friendly = "انتهت صلاحية ربط Snapchat — أعد الربط من الإعدادات.";
                                            } else if (lower.includes("permission") || lower.includes("403")) {
                                                friendly = "التوكن لا يملك صلاحيات قراءة الحساب الإعلاني.";
                                            } else if (lower.includes("اختر حساب") || lower.includes("سناب أولاً")) {
                                                friendly = "اربط Snapchat من الإعدادات أولاً.";
                                            } else {
                                                friendly = String(raw).slice(0, 200) || "تعذّر جلب صرف Snapchat";
                                            }
                                            toast.error(friendly, { id: "snap-fetch", duration: 9000 });
                                        }
                                    }}
                                    className="flex items-center justify-center gap-2 px-4 py-2 bg-yellow-400 hover:bg-yellow-500 text-black rounded-lg font-bold text-sm transition-colors w-full sm:w-auto"
                                    style={{ fontFamily: "Tajawal" }}
                                    data-testid="snap-refresh-today-btn"
                                >
                                    <ArrowsClockwise size={16} weight="bold" />
                                    {isMezanV2 ? "إعادة القراءة من ميزان 2" : "تحديث فوري للصرف اليوم"}
                                </button>
                            </div>

                            {/* Snap-day boundary info banner — shown after the first refresh.
                                We ALWAYS aggregate in Riyadh time (per merchant requirement),
                                regardless of the ad-account's native TZ. This banner just
                                confirms that fact so the merchant doesn't wonder why our
                                "today" might differ from Snapchat's UI (which uses PT). */}
                            {snapDayInfo && snapDayInfo.tz && snapDayInfo.startRiyadh && (
                                <div
                                    className="mb-4 bg-emerald-50/70 border border-emerald-200 rounded-lg px-3 py-2 text-xs text-emerald-900 leading-relaxed"
                                    data-testid="snap-day-info-banner"
                                >
                                    <strong>✓ يتم احتساب اليوم حسب توقيت السعودية</strong> ({snapDayInfo.startRiyadh} → {snapDayInfo.endRiyadh})
                                    <span className="mx-2">•</span>
                                    <span className="text-emerald-800/80">TZ حساب الإعلانات على Snap: <code className="bg-white/60 px-1.5 py-0.5 rounded text-[11px]" dir="ltr">{snapDayInfo.tz}</code> — لكننا نجمع الصرف ساعةً بساعة لتغطية يوم الرياض كاملاً (00:00 → 23:59).</span>
                                </div>
                            )}

                            {/* Today + Month sections */}
                            <div className="space-y-4">
                                {/* Today */}
                                <div>
                                    <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                        اليوم ({snapSummary.today.date})
                                    </div>
                                    <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
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
                                        <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-cpo-today" title={`متوسط تكلفة الطلب = ${formatMoney(snapSummary.today.spend)} ÷ ${formatInt(snapSummary.today.orders)}`}>
                                            <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                            <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                                {snapSummary.today.cost_per_order != null ? formatMoney(snapSummary.today.cost_per_order) : "—"}
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                {/* Per-account TODAY breakdown (iteration 18 — replaced the
                                    "Monthly" block per merchant request to surface each
                                    connected ad account's today spend). Shows one card per
                                    enabled Snapchat ad account; falls back to the monthly
                                    block when only 0/1 account is connected. */}
                                {snapAccountsBreakdown && snapAccountsBreakdown.count >= 2 ? (
                                    <div data-testid="snap-per-account-breakdown">
                                        <div className="text-xs text-muted-foreground font-bold mb-2 flex items-center gap-2 flex-wrap" style={{ fontFamily: "Tajawal" }}>
                                            <span>صرف اليوم — لكل حساب إعلاني</span>
                                            <span className="text-[10px] px-1.5 py-0.5 bg-yellow-100 text-yellow-800 rounded-full font-bold">
                                                {snapAccountsBreakdown.count} حسابات
                                            </span>
                                            <span className="text-[10px] px-1.5 py-0.5 bg-blue-100 text-blue-800 rounded-full font-bold">
                                                Asia/Riyadh
                                            </span>
                                        </div>
                                        <div className={`grid grid-cols-1 sm:grid-cols-2 ${snapAccountsBreakdown.count >= 3 ? "lg:grid-cols-3" : "lg:grid-cols-2"} gap-3`}>
                                            {snapAccountsBreakdown.accounts.map((acc) => (
                                                <div
                                                    key={acc.ad_account_id}
                                                    className="bg-white border border-yellow-200 rounded-lg p-4"
                                                    data-testid={`snap-account-today-card-${acc.ad_account_id}`}
                                                >
                                                    <div className="text-xs text-muted-foreground mb-1 truncate flex items-center gap-2 flex-wrap" style={{ fontFamily: "Tajawal" }}>
                                                        <span className="font-semibold text-foreground truncate" title={acc.name}>{acc.name || acc.ad_account_id}</span>
                                                        {acc.currency_native && acc.currency_native !== "SAR" && (
                                                            <span className="text-[10px] px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded-full font-bold whitespace-nowrap">
                                                                {acc.currency_native}→SAR
                                                            </span>
                                                        )}
                                                    </div>
                                                    <div className="num text-2xl font-extrabold text-yellow-700 tabular-nums" style={{ fontFamily: "Tajawal" }}>
                                                        {formatMoney(acc.today?.spend_sar || 0)} <span className="text-sm font-bold text-muted-foreground">ر.س</span>
                                                    </div>
                                                    {acc.currency_native && acc.currency_native !== "SAR" && acc.today?.spend_native > 0 && (
                                                        <div className="text-[11px] text-amber-700 mt-0.5 tabular-nums">
                                                            ≈ {formatMoney(acc.today.spend_native)} {acc.currency_native}
                                                        </div>
                                                    )}
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ) : (
                                    /* Fallback: original "Month" block when only 0/1 account
                                       is connected — preserves prior UX for single-account
                                       merchants. */
                                    <div>
                                        <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                            هذا الشهر (منذ {snapSummary.month.start})
                                        </div>
                                        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
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
                                            <div className="bg-white border border-yellow-200 rounded-lg p-4" data-testid="snap-cpo-month" title={`متوسط تكلفة الطلب = ${formatMoney(snapSummary.month.spend)} ÷ ${formatInt(snapSummary.month.orders)}`}>
                                                <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                                <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                                    {snapSummary.month.cost_per_order != null ? formatMoney(snapSummary.month.cost_per_order) : "—"}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                )}
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

                            {/* Snapchat Official (PDT) — read-only reference card.
                                Pulls official Snapchat numbers in the ad account's
                                NATIVE timezone (PDT/PT) into an isolated collection
                                `snapchat_reference_stats`. Never feeds any system
                                calculation — purely for visual comparison. */}
                            {!isMezanV2 && <SnapchatOfficialCard />}

                            {/* Iter-159j — Per-Snapchat-ad-account cards.
                                Only renders when the user has ≥2 Snapchat accounts.
                                Shows month spend / orders / avg cost / sales /
                                credit-limit per account. */}
                            <SnapchatAccountsCards
                                refreshSignal={adCardsRefreshSignal}
                                endpoint={isMezanV2
                                    ? "/dashboard-v2/snapchat-accounts-summary"
                                    : "/dashboard/snapchat-accounts-summary"}
                            />

                            {/* Footer: link to detailed report */}
                            <div className="mt-4 flex justify-end">
                                <Link
                                    to={isMezanV2 ? "/ads-manager" : "/reports/ads"}
                                    className="text-xs font-semibold text-yellow-800 hover:text-yellow-900 hover:underline inline-flex items-center gap-1"
                                    data-testid="snap-card-details-link"
                                    style={{ fontFamily: "Tajawal" }}
                                >
                                    التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←
                                </Link>
                            </div>
                        </div>
                    )}

                    {/* TikTok Ads dedicated section (pushed via Make.com webhook).
                        Card is ALWAYS visible (matches Snap/Meta UX) — empty state
                        is informative rather than hidden. */}
                    {tiktokSummary && (
                        <div
                            className="rounded-xl border-2 border-pink-200 p-4 sm:p-6"
                            style={{ background: "linear-gradient(135deg,#fff7fb 0%,#fff 50%,#f0fcff 100%)" }}
                            data-testid="tiktok-ads-section"
                        >
                            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
                                <div className="flex items-center gap-3">
                                    <div className="w-11 h-11 rounded-xl bg-black text-white flex items-center justify-center font-extrabold text-lg flex-shrink-0" style={{ fontFamily: "Tajawal" }}>
                                        TT
                                    </div>
                                    <div className="min-w-0">
                                        <h2 className="text-xl sm:text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>TikTok Ads</h2>
                                        <p className="text-xs text-muted-foreground">
                                            البيانات تأتي من Make.com webhook (ربط مباشر مع TikTok API قادم لاحقاً)
                                            {tiktokSummary.last_fetched_at && (
                                                <span className="ms-2 text-pink-700">• آخر تحديث: {new Date(tiktokSummary.last_fetched_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}</span>
                                            )}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={async () => {
                                        toast.loading("جاري تحديث TikTok…", { id: "tt-refresh" });
                                        await fetchTiktokSummary();
                                        await refreshSilently();
                                        toast.success("تم تحديث بيانات TikTok", { id: "tt-refresh" });
                                    }}
                                    className="flex items-center justify-center gap-2 px-4 py-2 bg-black hover:bg-gray-800 text-white rounded-lg font-bold text-sm transition-colors w-full sm:w-auto"
                                    style={{ fontFamily: "Tajawal" }}
                                    data-testid="tiktok-refresh-btn"
                                >
                                    <ArrowsClockwise size={16} weight="bold" />
                                    تحديث TikTok
                                </button>
                            </div>

                            {!tiktokSummary.has_data && (
                                <div className="bg-pink-50 border border-pink-200 rounded-lg p-4 text-sm text-pink-900 leading-relaxed mb-4" data-testid="tiktok-empty-state">
                                    لا توجد بيانات TikTok بعد. اربط TikTok Ads مع Make.com webhook من صفحة <Link to="/make-webhook" className="font-bold underline">ربط Make.com</Link> ليتم تدفّق صرف الإعلانات تلقائياً.
                                </div>
                            )}

                            {/* TODAY block */}
                            <div className="mb-4">
                                <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                    اليوم ({tiktokSummary.today.date})
                                </div>
                                <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-spend-today">
                                        <div className="text-xs text-muted-foreground mb-1">صرف اليوم (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-pink-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(tiktokSummary.today.spend)}</div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-orders-today">
                                        <div className="text-xs text-muted-foreground mb-1">طلبات اليوم</div>
                                        <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(tiktokSummary.today.orders)}</div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-revenue-today">
                                        <div className="text-xs text-muted-foreground mb-1">مبيعات اليوم (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(tiktokSummary.today.revenue)}</div>
                                    </div>
                                    <div className={`border-2 rounded-lg p-4 ${tiktokSummary.today.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="tiktok-roas-today">
                                        <div className="text-xs text-muted-foreground mb-1">ROAS اليوم</div>
                                        <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: tiktokSummary.today.roas >= 2 ? "#047857" : "#B45309" }}>
                                            {tiktokSummary.today.spend > 0 ? `${tiktokSummary.today.roas}x` : "—"}
                                        </div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-cpo-today" title={`متوسط تكلفة الطلب = ${formatMoney(tiktokSummary.today.spend)} ÷ ${formatInt(tiktokSummary.today.orders)}`}>
                                        <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                            {tiktokSummary.today.cost_per_order != null ? formatMoney(tiktokSummary.today.cost_per_order) : "—"}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* MONTH block */}
                            <div>
                                <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                    هذا الشهر (منذ {tiktokSummary.month.start})
                                </div>
                                <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-spend-month">
                                        <div className="text-xs text-muted-foreground mb-1">الصرف الشهري (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-pink-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(tiktokSummary.month.spend)}</div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-orders-month">
                                        <div className="text-xs text-muted-foreground mb-1">طلبات الشهر</div>
                                        <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{formatInt(tiktokSummary.month.orders)}</div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-revenue-month">
                                        <div className="text-xs text-muted-foreground mb-1">مبيعات الشهر (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-emerald-700" style={{ fontFamily: "Tajawal" }}>{formatMoney(tiktokSummary.month.revenue)}</div>
                                    </div>
                                    <div className={`border-2 rounded-lg p-4 ${tiktokSummary.month.roas >= 2 ? "border-emerald-300 bg-emerald-50" : "border-amber-300 bg-amber-50"}`} data-testid="tiktok-roas-month">
                                        <div className="text-xs text-muted-foreground mb-1">ROAS الشهر</div>
                                        <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal", color: tiktokSummary.month.roas >= 2 ? "#047857" : "#B45309" }}>
                                            {tiktokSummary.month.spend > 0 ? `${tiktokSummary.month.roas}x` : "—"}
                                        </div>
                                    </div>
                                    <div className="bg-white border border-pink-100 rounded-lg p-4" data-testid="tiktok-cpo-month" title={`متوسط تكلفة الطلب = ${formatMoney(tiktokSummary.month.spend)} ÷ ${formatInt(tiktokSummary.month.orders)}`}>
                                        <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                        <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                            {tiktokSummary.month.cost_per_order != null ? formatMoney(tiktokSummary.month.cost_per_order) : "—"}
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* 30-day sparkline */}
                            {tiktokSummary.history && tiktokSummary.history.some(h => h.spend > 0) && (
                                <div className="mt-5 pt-4 border-t border-pink-200">
                                    <div className="text-xs text-muted-foreground mb-2" style={{ fontFamily: "Tajawal" }}>
                                        صرف آخر 30 يوم — الإجمالي: {formatMoney(tiktokSummary.last_30d.spend)} ر.س
                                    </div>
                                    <div className="h-12 flex items-end gap-0.5">
                                        {tiktokSummary.history.map((d, i) => {
                                            const maxSpend = Math.max(1, ...tiktokSummary.history.map(h => h.spend));
                                            const height = Math.max(2, (d.spend / maxSpend) * 100);
                                            return (
                                                <div
                                                    key={i}
                                                    className="flex-1 bg-pink-400 rounded-sm hover:bg-pink-500 transition-colors cursor-help"
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
                                    to={isMezanV2 ? "/ads-manager" : "/reports/ads"}
                                    className="text-xs font-semibold text-pink-800 hover:text-pink-900 hover:underline inline-flex items-center gap-1"
                                    data-testid="tiktok-card-details-link"
                                    style={{ fontFamily: "Tajawal" }}
                                >
                                    التفاصيل (CPC / CPM / CTR / الحملات) في تقرير الإعلانات الموحَّد ←
                                </Link>
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
                                            {isMezanV2
                                                ? "قراءة مباشرة من حسابات Meta المحددة داخل ميزان 2"
                                                : "ربط مباشر مع Meta Marketing API — اضغط الزر للتحديث الفوري لصرف اليوم"}
                                            {metaSummary.last_sync_at && (
                                                <span className="ms-2 text-blue-700">• آخر مزامنة: {new Date(metaSummary.last_sync_at).toLocaleString("en-US", { dateStyle: "short", timeStyle: "short" })}</span>
                                            )}
                                        </p>
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={async () => {
                                        if (isMezanV2) {
                                            await fetchMetaSummary();
                                            toast.success("تمت إعادة قراءة بيانات Meta من ميزان 2");
                                            return;
                                        }
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
                                    {isMezanV2 ? "إعادة القراءة من ميزان 2" : "تحديث فوري للصرف اليوم"}
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
                                            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
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
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-cpo-today" title={`متوسط تكلفة الطلب = ${formatMoney(metaSummary.today.spend)} ÷ ${formatInt(metaSummary.today.orders)}`}>
                                                    <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                                        {metaSummary.today.cost_per_order != null ? formatMoney(metaSummary.today.cost_per_order) : "—"}
                                                    </div>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Month */}
                                        <div>
                                            <div className="text-xs text-muted-foreground font-bold mb-2" style={{ fontFamily: "Tajawal" }}>
                                                هذا الشهر (منذ {metaSummary.month.start})
                                            </div>
                                            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
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
                                                <div className="bg-white border border-blue-200 rounded-lg p-4" data-testid="meta-cpo-month" title={`متوسط تكلفة الطلب = ${formatMoney(metaSummary.month.spend)} ÷ ${formatInt(metaSummary.month.orders)}`}>
                                                    <div className="text-xs text-muted-foreground mb-1">⚡ متوسط تكلفة الطلب (ر.س)</div>
                                                    <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                                                        {metaSummary.month.cost_per_order != null ? formatMoney(metaSummary.month.cost_per_order) : "—"}
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

                    {/* The monthly Excel-analysis chart and recent analysis
                        history belong to the legacy dashboard only. Mezan 2
                        is driven by live unified orders and provider facts. */}
                    {!isMezanV2 && (<>
                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-4 sm:p-6" data-testid="dashboard-monthly-performance-section">
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
                    <div className="rounded-xl border border-border bg-white p-4 sm:p-6" data-testid="dashboard-recent-analyses-section">
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
                                <table className="mezan-table w-full text-right min-w-[640px]" data-testid="recent-analyses-table">
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
                    </>)}
                </>
            )}
            {/* iter-45 — Electronic Net audit modal */}
            <ElectronicNetDebugModal
                open={showElectronicNetDebug}
                onClose={() => setShowElectronicNetDebug(false)}
                filters={filters}
            />
        </div>
    );
}
