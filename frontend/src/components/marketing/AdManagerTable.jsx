import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    ChartBar,
    Check,
    DownloadSimple,
    FilmStrip,
    MagnifyingGlass,
} from "@phosphor-icons/react";

import {
    CAMPAIGN_ACCOUNT_EVENT,
    CAMPAIGN_REPORT_UPDATED_EVENT,
    getCampaignReportSnapshot,
    snapchatSelectedAccountId,
} from "../../marketingCampaignResultSource";
import { ADS_DATE_RANGE_APPLIED_EVENT } from "./ArabicDateRangePicker";
import {
    getSnapchatAdPerformance,
    SNAPCHAT_ENTITY_PAGE_SIZE,
} from "../../services/snapchatAdPerformance";
import InfiniteScrollSentinel from "./InfiniteScrollSentinel";
import {
    infinitePaginationState,
    mergePaginatedRows,
} from "./infiniteScrollPagination";

const AUTO_REFRESH_MS = 60_000;
const NAME_WIDTH = 330;

export const AD_MANAGER_NATIVE_COLUMN_ORDER = Object.freeze([
    "name",
    "status",
    "review",
    "delivery",
    "ad_squad",
    "campaign",
    "orders",
    "cpa",
    "roas",
    "spend",
    "sales",
    "impressions",
    "clicks",
    "ctr",
]);

const STATUS_LABELS = Object.freeze({
    ACTIVE: "نشط",
    ENABLED: "نشط",
    PAUSED: "متوقف",
    ARCHIVED: "مؤرشف",
    DELETED: "محذوف",
    UNKNOWN: "غير محسوم",
});

const REVIEW_LABELS = Object.freeze({
    APPROVED: "مقبول",
    PENDING: "قيد المراجعة",
    PENDING_REVIEW: "قيد المراجعة",
    IN_REVIEW: "قيد المراجعة",
    REJECTED: "مرفوض",
    DISAPPROVED: "مرفوض",
});

function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function money(value) {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function number(value) {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return parsed.toLocaleString("en-US", {
        maximumFractionDigits: Number.isInteger(parsed) ? 0 : 2,
    });
}

function ratio(value, suffix = "") {
    const parsed = finite(value);
    if (parsed === null) return "—";
    return `${parsed.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}${suffix}`;
}

function activeStatus(value) {
    return ["ACTIVE", "ENABLED"].includes(String(value || "").toUpperCase());
}

function statusLabel(value) {
    const key = String(value || "UNKNOWN").toUpperCase();
    return STATUS_LABELS[key] || key;
}

function reviewLabel(value) {
    const key = String(value || "").toUpperCase();
    return REVIEW_LABELS[key] || (key || "غير متاح");
}

function currentRange() {
    const fromInput = document.querySelector('[data-mezan-native-date="from"]');
    const toInput = document.querySelector('[data-mezan-native-date="to"]');
    const snapshot = getCampaignReportSnapshot("snapchat") || {};
    return {
        dateFrom: String(fromInput?.value || snapshot.date_from || "").trim(),
        dateTo: String(toInput?.value || snapshot.date_to || "").trim(),
    };
}

function Metric({ primary, secondary = "" }) {
    return (
        <div className="font-mono">
            <div className="text-base font-black text-slate-900">{primary}</div>
            {secondary && <div className="mt-1 text-[11px] font-bold text-slate-400">{secondary}</div>}
        </div>
    );
}

function StatusCell({ ad }) {
    const active = activeStatus(ad.status);
    return (
        <div className="flex items-center gap-2">
            <span className={`relative inline-flex h-5 w-9 rounded-full ${active ? "bg-emerald-500" : "bg-slate-300"}`}>
                <span className={`absolute top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-white shadow-sm ${active ? "right-[18px]" : "right-0.5"}`}>
                    {active && <Check size={10} weight="bold" className="text-emerald-600" />}
                </span>
            </span>
            <span className="font-black text-slate-700">{statusLabel(ad.status)}</span>
        </div>
    );
}

function DeliveryCell({ ad }) {
    const delivering = ad.delivery_state === "DELIVERING";
    const pending = ad.delivery_state === "PENDING";
    return (
        <div className="flex items-start gap-2">
            <span className={`mt-1 h-2.5 w-2.5 rounded-full ${delivering ? "bg-emerald-500 ring-2 ring-emerald-100" : pending ? "bg-amber-500 ring-2 ring-amber-100" : "bg-slate-300"}`} />
            <div>
                <div className="max-w-[240px] font-black text-slate-700">
                    {ad.delivery_status || (delivering ? "يتم التسليم" : "غير نشط")}
                </div>
                {ad.delivery_inherited_from_ad_squad && (
                    <div className="mt-1 text-[11px] font-bold text-slate-400">
                        السبب موروث من المجموعة الإعلانية
                    </div>
                )}
            </div>
        </div>
    );
}

function csvEscape(value) {
    return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function exportCsv(rows) {
    if (!rows.length) return;
    const headers = [
        "اسم الإعلان", "معرف الإعلان", "الحالة", "المراجعة", "حالة التسليم",
        "المجموعة الإعلانية", "الحملة", "المشتريات", "CPA", "ROAS", "الصرف",
        "المبيعات", "الظهور", "النقرات", "CTR", "الإبداع", "نوع الإبداع",
    ];
    const body = rows.map((ad) => [
        ad.ad_name,
        ad.ad_id,
        statusLabel(ad.status),
        reviewLabel(ad.review_status),
        ad.delivery_status,
        ad.ad_squad_name,
        ad.campaign_name,
        ad.orders,
        ad.cpa_sar,
        ad.roas,
        ad.spend_sar,
        ad.sales_sar,
        ad.impressions,
        ad.swipes,
        ad.ctr_pct,
        ad.creative_name || ad.creative_id,
        ad.creative_type,
    ]);
    const content = [headers, ...body]
        .map((row) => row.map(csvEscape).join(","))
        .join("\n");
    const blob = new Blob(["\ufeff", content], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mezan-snapchat-ads.csv";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
}

function sortableValue(ad, key) {
    if (key === "name") return String(ad.ad_name || "");
    if (key === "status") return activeStatus(ad.status) ? 1 : 0;
    return finite(ad[key]);
}

export const AD_MANAGER_SORT_OPTIONS = Object.freeze([
    { id: "orders", label: "الأكثر طلبًا" },
    { id: "spend", label: "الأكثر صرفًا" },
    { id: "newest", label: "الأحدث أولًا" },
]);

export default function AdManagerTable({
    activeCampaignsOnly = true,
    actionReportTime = "conversion",
    campaignId = null,
    adSquadId = null,
}) {
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [page, setPage] = useState(1);
    const [sort, setSort] = useState({ key: null, direction: "desc" });
    const [serverSort, setServerSort] = useState("orders");
    const loadSequenceRef = useRef(0);

    const load = useCallback(async ({ silent = false } = {}) => {
        const requestId = ++loadSequenceRef.current;
        const requestPage = page;
        if (!silent) setLoading(true);
        setError("");
        const accountId = snapchatSelectedAccountId();
        const range = currentRange();
        if (!accountId || !range.dateFrom || !range.dateTo) {
            setLoading(false);
            return;
        }
        try {
            const result = await getSnapchatAdPerformance({
                accountId,
                dateFrom: range.dateFrom,
                dateTo: range.dateTo,
                query: appliedQuery,
                campaignId,
                adSquadId,
                page,
                limit: SNAPCHAT_ENTITY_PAGE_SIZE,
                activeCampaignsOnly,
                sortBy: serverSort,
                actionReportTime,
            });
            if (requestId !== loadSequenceRef.current) return;
            setReport((current) => {
                if (requestPage <= 1 || !current) return result;
                return {
                    ...result,
                    ads: mergePaginatedRows(
                        current.ads,
                        result.ads,
                        (ad) => `${ad?.account_id || "unknown"}:${ad?.ad_id || "unknown"}`,
                    ),
                    pagination: {
                        ...result.pagination,
                        page: requestPage,
                    },
                };
            });
        } catch (loadError) {
            if (requestId !== loadSequenceRef.current) return;
            const detail = loadError?.response?.data?.detail;
            setError(
                typeof detail === "string"
                    ? detail
                    : detail?.message || "تعذر تحميل إعلانات Snapchat.",
            );
        } finally {
            if (requestId === loadSequenceRef.current) {
                setLoading(false);
            }
        }
    }, [
        actionReportTime,
        activeCampaignsOnly,
        adSquadId,
        appliedQuery,
        campaignId,
        page,
        serverSort,
    ]);

    useEffect(() => {
        load();
    }, [load]);

    useEffect(() => {
        setPage(1);
    }, [actionReportTime, activeCampaignsOnly, campaignId, adSquadId]);

    useEffect(() => {
        const refresh = () => {
            loadSequenceRef.current += 1;
            if (page !== 1) {
                setPage(1);
                return;
            }
            setPage(1);
            window.setTimeout(() => load({ silent: true }), 80);
        };
        window.addEventListener(CAMPAIGN_ACCOUNT_EVENT, refresh);
        window.addEventListener(CAMPAIGN_REPORT_UPDATED_EVENT, refresh);
        window.addEventListener(ADS_DATE_RANGE_APPLIED_EVENT, refresh);
        const timer = window.setInterval(() => {
            if (document.visibilityState === "visible") load({ silent: true });
        }, AUTO_REFRESH_MS);
        return () => {
            window.removeEventListener(CAMPAIGN_ACCOUNT_EVENT, refresh);
            window.removeEventListener(CAMPAIGN_REPORT_UPDATED_EVENT, refresh);
            window.removeEventListener(ADS_DATE_RANGE_APPLIED_EVENT, refresh);
            window.clearInterval(timer);
        };
    }, [load, page]);

    const rows = useMemo(() => {
        if (!sort.key) return [...(report?.ads || [])];
        const direction = sort.direction === "asc" ? 1 : -1;
        return [...(report?.ads || [])].sort((left, right) => {
            const a = sortableValue(left, sort.key);
            const b = sortableValue(right, sort.key);
            if (a === null && b === null) return 0;
            if (a === null) return 1;
            if (b === null) return -1;
            if (typeof a === "number" && typeof b === "number") {
                return (a - b) * direction;
            }
            return String(a).localeCompare(String(b), "ar", { numeric: true }) * direction;
        });
    }, [report, sort]);

    function sortBy(key) {
        setSort((current) => ({
            key,
            direction: current.key === key && current.direction === "desc" ? "asc" : "desc",
        }));
    }

    function submitSearch(event) {
        event.preventDefault();
        setPage(1);
        setAppliedQuery(query.trim());
    }

    const pagination = report?.pagination || {};
    const totals = report?.totals || {};
    const paginationState = infinitePaginationState({
        pagination,
        requestedPage: page,
        loaded: report?.ads?.length || 0,
    });

    return (
        <section
            className="overflow-hidden rounded-b-2xl border border-t-0 border-slate-200 bg-white shadow-sm"
            data-testid="ad-manager-table"
            data-native-column-layout="true"
            dir="rtl"
        >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-3">
                <form onSubmit={submitSearch} className="flex min-w-[280px] flex-1 items-center gap-2">
                    <span className="relative block min-w-0 flex-1">
                        <MagnifyingGlass size={18} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="اسم الإعلان أو المجموعة أو الحملة أو رقمها"
                            className="h-10 w-full rounded-xl border border-slate-200 bg-slate-50 pr-10 pl-3 text-sm font-bold outline-none focus:border-emerald-400 focus:bg-white"
                        />
                    </span>
                    <button type="submit" className="h-10 rounded-xl bg-slate-950 px-4 text-sm font-black text-white">بحث</button>
                </form>
                <div className="flex flex-wrap items-center gap-2">
                    <div className="flex flex-wrap gap-1" data-testid="ad-server-sort-controls">
                        {AD_MANAGER_SORT_OPTIONS.map((option) => (
                            <button
                                key={option.id}
                                type="button"
                                onClick={() => {
                                    setPage(1);
                                    setSort({ key: null, direction: "desc" });
                                    setServerSort(option.id);
                                }}
                                aria-pressed={serverSort === option.id}
                                className={`rounded-lg px-3 py-2 text-xs font-black ${serverSort === option.id ? "bg-slate-950 text-white" : "border border-slate-200 bg-white text-slate-700"}`}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                    <span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-black text-blue-700">
                        نتائج Snapchat على مستوى الإعلان · قراءة فقط
                    </span>
                    <button type="button" onClick={() => load()} className="rounded-lg p-2 text-slate-600 hover:bg-slate-100" aria-label="تحديث الإعلانات">
                        <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} />
                    </button>
                    <button type="button" onClick={() => exportCsv(rows)} disabled={!rows.length} className="inline-flex items-center gap-1 rounded-lg px-3 py-2 text-xs font-black text-slate-600 hover:bg-slate-100 disabled:opacity-35">
                        <DownloadSimple size={17} /> تنزيل
                    </button>
                </div>
            </div>

            {error && <div className="border-b border-rose-200 bg-rose-50 px-4 py-3 text-sm font-black text-rose-800">{error}</div>}

            <div className="overflow-x-auto">
                <table className="w-max min-w-full border-separate border-spacing-0 text-right text-sm">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="sticky right-0 z-30 min-w-[330px] border-b border-l border-slate-200 bg-slate-50 px-5 py-4" data-column-id="name"><button type="button" onClick={() => sortBy("name")} className="font-black">الإعلان والإبداع</button></th>
                            <th className="sticky z-30 min-w-[120px] border-b border-l border-slate-200 bg-slate-50 px-5 py-4" style={{ right: NAME_WIDTH }} data-column-id="status"><button type="button" onClick={() => sortBy("status")} className="font-black">الحالة</button></th>
                            <th className="min-w-[135px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="review">المراجعة</th>
                            <th className="min-w-[250px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="delivery">حالة التسليم</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="ad_squad">المجموعة الإعلانية</th>
                            <th className="min-w-[220px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="campaign">الحملة</th>
                            <th className="min-w-[120px] border-b border-slate-200 px-5 py-4" data-column-id="orders"><button type="button" onClick={() => sortBy("orders")} className="font-black">النتائج</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-5 py-4" data-column-id="cpa"><button type="button" onClick={() => sortBy("cpa_sar")} className="font-black">تكلفة الشراء</button></th>
                            <th className="min-w-[120px] border-b border-slate-200 px-5 py-4" data-column-id="roas"><button type="button" onClick={() => sortBy("roas")} className="font-black">ROAS</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-5 py-4" data-column-id="spend"><button type="button" onClick={() => sortBy("spend_sar")} className="font-black">المبلغ المصروف</button></th>
                            <th className="min-w-[145px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="sales">المبيعات</th>
                            <th className="min-w-[140px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="impressions">مرات الظهور</th>
                            <th className="min-w-[110px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="clicks">النقرات</th>
                            <th className="min-w-[100px] border-b border-slate-200 px-5 py-4 font-black" data-column-id="ctr">CTR</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((ad) => (
                            <tr key={`${ad.account_id}:${ad.ad_id}`} className="group bg-white hover:bg-slate-50">
                                <td className="sticky right-0 z-10 border-b border-l border-slate-100 bg-white px-5 py-5 group-hover:bg-slate-50" data-column-id="name">
                                    <div className="flex items-start gap-3">
                                        <span className="rounded-xl bg-violet-50 p-2 text-violet-700"><FilmStrip size={22} weight="duotone" /></span>
                                        <div className="min-w-0">
                                            <div className="max-w-[260px] truncate text-base font-black text-slate-950" title={ad.ad_name}>{ad.ad_name}</div>
                                            <div className="mt-1 font-mono text-[11px] text-slate-400">{ad.ad_id}</div>
                                            <div className="mt-2 text-xs font-bold text-slate-500">{ad.creative_name || ad.creative_type || "إبداع مرتبط"}</div>
                                            {ad.creative_id && <div className="mt-1 font-mono text-[10px] text-slate-400">{ad.creative_id}</div>}
                                        </div>
                                    </div>
                                </td>
                                <td className="sticky z-10 border-b border-l border-slate-100 bg-white px-5 py-5 group-hover:bg-slate-50" style={{ right: NAME_WIDTH }} data-column-id="status"><StatusCell ad={ad} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="review"><span className={`rounded-full px-3 py-1 text-xs font-black ${String(ad.review_status || "").toUpperCase().includes("REJECT") ? "bg-rose-50 text-rose-700" : String(ad.review_status || "").toUpperCase().includes("PEND") ? "bg-amber-50 text-amber-700" : "bg-emerald-50 text-emerald-700"}`}>{reviewLabel(ad.review_status)}</span></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="delivery"><DeliveryCell ad={ad} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="ad_squad"><div className="max-w-[190px] truncate font-black text-slate-700">{ad.ad_squad_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{ad.ad_squad_id || "—"}</div></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="campaign"><div className="max-w-[190px] truncate font-black text-slate-700">{ad.campaign_name}</div><div className="mt-1 font-mono text-[10px] text-slate-400">{ad.campaign_id || "—"}</div></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="orders"><Metric primary={number(ad.orders)} secondary="مشتريات" /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="cpa"><Metric primary={money(ad.cpa_sar)} secondary="لكل عملية شراء" /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="roas"><Metric primary={ratio(ad.roas, "×")} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="spend"><Metric primary={money(ad.spend_sar)} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="sales"><Metric primary={money(ad.sales_sar)} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="impressions"><Metric primary={number(ad.impressions)} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="clicks"><Metric primary={number(ad.swipes)} /></td>
                                <td className="border-b border-slate-100 px-5 py-5" data-column-id="ctr"><Metric primary={ratio(ad.ctr_pct, "%")} /></td>
                            </tr>
                        ))}
                        {!rows.length && (
                            <tr><td colSpan={14} className="px-6 py-16 text-center"><ChartBar size={40} weight="duotone" className="mx-auto text-slate-300" /><div className="mt-3 font-black text-slate-600">{loading ? "جاري تحميل الإعلانات…" : "لا توجد إعلانات موثقة ضمن الفترة أو البحث."}</div></td></tr>
                        )}
                    </tbody>
                    {!!rows.length && (
                        <tfoot className="bg-slate-50 font-black text-slate-900">
                            <tr>
                                <td className="sticky right-0 z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-5 py-4" data-column-id="name">إجمالي الفترة</td>
                                <td className="sticky z-10 border-l border-t-2 border-slate-300 bg-slate-50 px-5 py-4" style={{ right: NAME_WIDTH }} data-column-id="status" />
                                <td colSpan={4} className="border-t-2 border-slate-300" />
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{number(totals.orders)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{money(totals.cpa_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{ratio(totals.roas, "×")}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{money(totals.spend_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{money(totals.sales_sar)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{number(totals.impressions)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{number(totals.swipes)}</td>
                                <td className="border-t-2 border-slate-300 px-5 py-4 font-mono">{ratio(totals.ctr_pct, "%")}</td>
                            </tr>
                        </tfoot>
                    )}
                </table>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 px-4 py-3">
                <span className="text-xs font-bold text-slate-500">
                    نتائج الإعلانات من Snapchat فقط؛ نتائج سلة تبقى على مستوى الحملة حتى تتوفر هوية إعلان مؤكدة.
                </span>
                <div className="flex items-center gap-2 text-xs font-black text-slate-600">
                    <span>تم عرض {number(report?.ads?.length || 0)} من {number(paginationState.total)} إعلان</span>
                </div>
            </div>
            <InfiniteScrollSentinel
                hasMore={paginationState.hasMore}
                loading={loading}
                loaded={report?.ads?.length || 0}
                total={paginationState.total}
                entityLabel="إعلان"
                onLoadMore={() => setPage(paginationState.page + 1)}
                testId="ad-infinite-scroll-sentinel"
            />
        </section>
    );
}
