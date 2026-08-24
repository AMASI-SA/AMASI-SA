import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowsClockwise, CheckCircle, Clock, Ghost, WarningCircle } from "@phosphor-icons/react";
import { toast } from "sonner";

import SnapchatCampaignManagementPanel from "../components/marketing/SnapchatCampaignManagementPanel";
import UnifiedMarketingEntityTable from "../components/marketing/UnifiedMarketingEntityTable";
import UnifiedMarketingOrdersPanel from "../components/marketing/UnifiedMarketingOrdersPanel";
import api, { formatApiErrorDetail } from "../lib/api";

const ENTITY_TABS = [
    { id: "campaign", label: "الحملات" },
    { id: "ad_group", label: "Ad Squads" },
    { id: "ad", label: "Ads" },
];

function localDateInTimezone(timezone) {
    try {
        return new Intl.DateTimeFormat("en-CA", {
            timeZone: timezone || "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit",
        }).format(new Date());
    } catch {
        return new Date().toISOString().slice(0, 10);
    }
}

function localTimeInTimezone(timezone, nowMs) {
    try {
        return new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh", hour: "2-digit", minute: "2-digit", hour12: false,
        }).format(new Date(nowMs));
    } catch {
        return "—";
    }
}

function localHourInTimezone(timezone, nowMs) {
    try {
        const value = new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh", hour: "2-digit", hour12: false,
        }).format(new Date(nowMs));
        const hour = Number(value);
        return Number.isFinite(hour) ? hour : null;
    } catch {
        return null;
    }
}

function money(value, currency = "USD") {
    if (value === null || value === undefined || value === "") return "—";
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function contractMoney(value) {
    return money(value?.amount, value?.currency || "");
}

function number(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}

function statusTone(status) {
    if (["complete", "healthy", "released"].includes(status)) return "text-emerald-700 bg-emerald-50 border-emerald-200";
    if (["partial", "running", "held", "pending"].includes(status)) return "text-amber-700 bg-amber-50 border-amber-200";
    return "text-slate-700 bg-slate-50 border-slate-200";
}

function displayHourStatus(row, selectedDate, timezone, nowMs) {
    const today = localDateInTimezone(timezone);
    const currentHour = localHourInTimezone(timezone, nowMs);
    const rowHour = Number(String(row?.local_hour || "").slice(0, 2));
    if (selectedDate > today) return "future";
    if (selectedDate === today && Number.isFinite(rowHour) && currentHour !== null) {
        if (rowHour > currentHour) return "future";
        if (rowHour === currentHour) return row?.spend_native == null ? "provisional_unavailable" : "provisional";
    }
    const start = Date.parse(row?.hour_start_utc || "");
    const end = Date.parse(row?.hour_end_utc || "");
    if (!Number.isFinite(start) || !Number.isFinite(end)) return row?.status || "—";
    if (nowMs >= start && nowMs < end) return row?.spend_native == null ? "provisional_unavailable" : "provisional";
    if (nowMs < start) return "future";
    if (row?.status === "future") return "awaiting_refresh";
    return row?.status || "—";
}

function managementLevel(level) {
    if (level === "ad_group") return "ad_squads";
    if (level === "ad") return "ads";
    return "campaigns";
}

export default function SnapchatV2Page() {
    const [status, setStatus] = useState(null);
    const [report, setReport] = useState(null);
    const [hourly, setHourly] = useState(null);
    const [campaignContract, setCampaignContract] = useState(null);
    const [childContract, setChildContract] = useState(null);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [appliedRange, setAppliedRange] = useState(null);
    const [entityLevel, setEntityLevel] = useState("campaign");
    const [selectedCampaign, setSelectedCampaign] = useState(null);
    const [selectedAdGroup, setSelectedAdGroup] = useState(null);
    const [managementTarget, setManagementTarget] = useState(null);
    const [loading, setLoading] = useState(true);
    const [entityLoading, setEntityLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");
    const [clockNow, setClockNow] = useState(() => Date.now());

    const account = status?.selected_account || null;
    const accountId = account?.ad_account_id || "";
    const currency = account?.currency || report?.currency || "USD";
    const accountTimezone = account?.timezone || "America/Los_Angeles";
    const activeContract = entityLevel === "campaign" ? campaignContract : childContract;

    const load = useCallback(async (requestedRange = null) => {
        setLoading(true);
        setError("");
        try {
            const { data: statusData } = await api.get("/integrations-v2/snapchat-v2/status");
            setStatus(statusData);
            const timezone = statusData?.selected_account?.timezone || "America/Los_Angeles";
            const today = localDateInTimezone(timezone);
            const range = requestedRange || appliedRange || { dateFrom: today, dateTo: today };
            if (!dateFrom) setDateFrom(range.dateFrom);
            if (!dateTo) setDateTo(range.dateTo);
            setAppliedRange(range);
            const common = { action_report_time: "conversion", timezone: "account" };
            const [reportResult, hourlyResult, campaignsResult] = await Promise.all([
                api.get("/integrations-v2/snapchat-v2/report", { params: { ...common, date_from: range.dateFrom, date_to: range.dateTo } }),
                api.get("/integrations-v2/snapchat-v2/hourly", { params: { ...common, report_date: range.dateTo } }),
                api.get("/integrations-v2/snapchat-v2/campaigns", { params: { ...common, date_from: range.dateFrom, date_to: range.dateTo } }),
            ]);
            setReport(reportResult.data);
            setHourly(hourlyResult.data);
            setCampaignContract(campaignsResult.data?.unified || null);
            setChildContract(null);
            setSelectedCampaign(null);
            setSelectedAdGroup(null);
            setManagementTarget(null);
            setEntityLevel("campaign");
            setClockNow(Date.now());
        } catch (requestError) {
            const message = formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تحميل بيانات Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            setLoading(false);
        }
    }, [appliedRange, dateFrom, dateTo]);

    useEffect(() => {
        load();
        // Reads Snapchat V2 through its unified adapter. Dashboard, AI, and V1 remain untouched.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const timer = window.setInterval(() => setClockNow(Date.now()), 60_000);
        return () => window.clearInterval(timer);
    }, []);

    async function openChildren(row) {
        if (!appliedRange) return;
        setEntityLoading(true);
        setError("");
        try {
            const common = {
                date_from: appliedRange.dateFrom,
                date_to: appliedRange.dateTo,
                timezone: "account",
                action_report_time: "conversion",
                include_stale: true,
            };
            if (row.entity.level === "campaign") {
                const { data } = await api.get("/integrations-v2/snapchat-v2/ad-squads", { params: { ...common, campaign_id: row.entity.id } });
                setSelectedCampaign(row);
                setSelectedAdGroup(null);
                setManagementTarget(null);
                setChildContract(data?.unified || null);
                setEntityLevel("ad_group");
            } else if (row.entity.level === "ad_group") {
                const campaignId = row.entity.campaign_id || selectedCampaign?.entity?.id;
                const { data } = await api.get("/integrations-v2/snapchat-v2/ads", {
                    params: { ...common, campaign_id: campaignId, ad_squad_id: row.entity.id },
                });
                setSelectedAdGroup(row);
                setManagementTarget(null);
                setChildContract(data?.unified || null);
                setEntityLevel("ad");
            }
        } catch (requestError) {
            const message = formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تحميل المستوى التفصيلي من Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            setEntityLoading(false);
        }
    }

    async function returnToAdGroups() {
        if (selectedCampaign) await openChildren(selectedCampaign);
    }

    function manageEntity(row) {
        setManagementTarget(row);
        window.setTimeout(() => {
            document.querySelector('[data-testid="snapchat-campaign-management-panel"]')
                ?.scrollIntoView({ behavior: "smooth", block: "start" });
        }, 0);
    }

    function applyRange(event) {
        event.preventDefault();
        if (!dateFrom || !dateTo || dateTo < dateFrom) {
            toast.error("تحقق من فترة التقرير قبل المتابعة.");
            return;
        }
        load({ dateFrom, dateTo });
    }

    async function syncRange() {
        if (!appliedRange || !accountId) return;
        setSyncing(true);
        try {
            const { data } = await api.post("/integrations-v2/snapchat-v2/sync", {
                ad_account_id: accountId,
                date_from: appliedRange.dateFrom,
                date_to: appliedRange.dateTo,
                action_report_time: "conversion",
                run_type: "manual",
            });
            if (data?.status === "skipped" && data?.reason === "lease_unavailable") {
                toast.warning("توجد مزامنة تلقائية قيد التشغيل الآن. أعد المحاولة بعد قليل.");
            } else if (data?.status === "complete") {
                toast.success("اكتملت مزامنة Snapchat V2 بكل مستويات التقرير");
            } else {
                toast.warning("اكتملت المزامنة مع مستوى تفصيلي جزئي؛ المالي يبقى مستقلًا ومؤكدًا.");
            }
            await load(appliedRange);
        } catch (requestError) {
            toast.error(formatApiErrorDetail(requestError.response?.data?.detail) || "تعذر تشغيل مزامنة Snapchat V2");
        } finally {
            setSyncing(false);
        }
    }

    const knownHours = useMemo(() => (hourly?.hours || []).filter((row) => (
        row?.spend_native !== null && row?.spend_native !== undefined && Number.isFinite(Number(row.spend_native))
    )), [hourly]);
    const confirmedHours = useMemo(() => (hourly?.hours || []).filter((row) => row.status === "confirmed_data"), [hourly]);
    const maxHourSpend = useMemo(() => Math.max(1, ...knownHours.map((row) => Number(row.spend_native) || 0)), [knownHours]);
    const financialDisplayStatus = status?.financial_sync_status === "complete" || status?.last_success?.financial
        ? "complete"
        : (status?.financial_sync_status || "—");
    const totals = campaignContract?.totals || null;
    const sallaTotals = totals?.commerce_outcomes || {};
    const managementCampaign = managementTarget?.entity?.level === "campaign"
        ? managementTarget
        : selectedCampaign;
    const managementAdGroup = managementTarget?.entity?.level === "ad_group"
        ? managementTarget
        : selectedAdGroup;
    const managementAction = managementTarget
        ? `${managementTarget.entity.provider_level}.update`
        : null;

    return (
        <div className="space-y-5" dir="rtl" data-testid="snapchat-v2-page">
            <header className="rounded-2xl border border-yellow-300 bg-gradient-to-br from-yellow-50 to-amber-50 p-5 sm:p-7">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <div>
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-2 rounded-full border border-yellow-300 bg-white px-3 py-1 text-xs font-black text-amber-800"><Ghost size={16} weight="fill" /> Snapchat V2</span>
                            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">Unified Marketing Adapter</span>
                            <span className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-black text-amber-800">Decision Intelligence غير مربوط</span>
                        </div>
                        <h1 className="text-3xl font-black tracking-tight">إعلانات سناب شات</h1>
                        <p className="mt-2 text-sm font-semibold text-slate-600">Snapchat V2 · توقيت الحساب {accountTimezone} · الآن {localTimeInTimezone(accountTimezone, clockNow)}</p>
                    </div>
                    <form onSubmit={applyRange} className="flex flex-wrap items-end gap-2">
                        <label className="text-xs font-black text-slate-600">من<input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} className="mt-1 block rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-bold" dir="ltr" /></label>
                        <label className="text-xs font-black text-slate-600">إلى<input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} className="mt-1 block rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-bold" dir="ltr" /></label>
                        <button type="submit" disabled={loading} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-4 text-sm font-black disabled:opacity-50"><ArrowsClockwise size={17} className={loading ? "animate-spin" : ""} /> تطبيق الفترة</button>
                        <button type="button" onClick={syncRange} disabled={syncing || !appliedRange || !accountId} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-yellow-500 px-4 text-sm font-black text-white disabled:opacity-50"><ArrowsClockwise size={17} className={syncing ? "animate-spin" : ""} />{syncing ? "جاري المزامنة" : "مزامنة V2"}</button>
                    </form>
                </div>
            </header>

            {error && <div className="flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-700"><WarningCircle size={22} weight="fill" /> {error}</div>}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs font-bold text-slate-500">الحساب المعتمد</div><div className="mt-2 text-lg font-black">{account?.display_name || "—"}</div><div className="mt-1 truncate text-xs text-slate-500" dir="ltr">{accountId || "—"}</div></div>
                <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4"><div className="text-xs font-bold text-amber-700">صرف الفترة</div><div className="mt-2 text-2xl font-black text-amber-950">{totals ? contractMoney(totals.delivery.spend) : money(report?.base_spend_native, currency)}</div><div className="mt-1 text-xs font-bold text-amber-700">{appliedRange?.dateFrom || "—"} — {appliedRange?.dateTo || "—"}</div></div>
                <div className="rounded-xl border border-violet-200 bg-violet-50 p-4"><div className="text-xs font-bold text-violet-700">نتائج Snapchat</div><div className="mt-2 text-2xl font-black text-violet-950">{number(totals?.platform_outcomes?.conversions)}</div><div className="mt-1 text-xs font-bold text-violet-700">قيمة {contractMoney(totals?.platform_outcomes?.revenue)} · ROAS {number(totals?.platform_outcomes?.roas)}</div></div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4"><div className="text-xs font-bold text-emerald-700">نتائج سلة المطابقة</div><div className="mt-2 text-2xl font-black text-emerald-950">{sallaTotals.status === "complete" ? number(sallaTotals.orders) : "—"}</div><div className="mt-1 text-xs font-bold text-emerald-700">مبيعات {contractMoney(sallaTotals.revenue)} · ROAS {number(sallaTotals.roas)}</div></div>
                <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="text-xs font-bold text-slate-500">حالة المزامنة</div><div className="mt-2 flex items-center gap-2 text-xl font-black">{report?.amount_complete ? <CheckCircle className="text-emerald-600" weight="fill" /> : <Clock className="text-amber-600" />}{report?.amount_complete ? "مكتمل" : "قيد التحديث"}</div><div className={`mt-2 inline-flex rounded-full border px-2 py-1 text-xs font-black ${statusTone(financialDisplayStatus)}`}>Financial: {financialDisplayStatus}</div></div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <div className="mb-4 flex items-center justify-between gap-3"><div><h2 className="text-lg font-black">الصرف بالساعة — {appliedRange?.dateTo || "—"}</h2><p className="text-xs font-semibold text-slate-500">حسب توقيت حساب Snapchat · الساعة الحالية provisional · الساعات المستقبلية لا تُعرض كصفر</p></div><div className="text-xs font-black text-slate-500">{confirmedHours.length} ساعة مؤكدة</div></div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
                    {(hourly?.hours || []).map((row) => {
                        const spend = Number(row.spend_native);
                        const known = row?.spend_native !== null && row?.spend_native !== undefined && Number.isFinite(spend);
                        const pct = known ? Math.max(4, (spend / maxHourSpend) * 100) : 0;
                        const effectiveStatus = displayHourStatus(row, appliedRange?.dateTo || "", accountTimezone, clockNow);
                        return <div key={row.local_hour} className="rounded-lg border border-slate-100 bg-slate-50 p-2"><div className="text-xs font-black" dir="ltr">{row.local_hour}</div><div className="mt-2 flex h-16 items-end overflow-hidden rounded bg-white">{known && <div className="w-full rounded bg-yellow-400" style={{ height: `${pct}%` }} />}</div><div className="mt-2 text-xs font-black" dir="ltr">{known ? money(spend, currency) : "—"}</div><div className="text-[10px] font-bold text-slate-400">{effectiveStatus}</div></div>;
                    })}
                </div>
            </section>

            <SnapchatCampaignManagementPanel
                key={`${managementTarget?.entity?.provider_level || "create"}:${managementTarget?.entity?.id || entityLevel}`}
                accountId={accountId}
                entityLevel={managementLevel(entityLevel)}
                initialAction={managementAction}
                selectedCampaign={managementCampaign ? { campaign_id: managementCampaign.entity.id, campaign_name: managementCampaign.entity.name } : null}
                selectedAdSquad={managementAdGroup ? { ad_squad_id: managementAdGroup.entity.id, ad_squad_name: managementAdGroup.entity.name } : null}
                selectedAd={managementTarget?.entity?.level === "ad" ? { ad_id: managementTarget.entity.id, ad_name: managementTarget.entity.name, ad_squad_id: managementTarget.entity.ad_group_id } : null}
                onChanged={() => { toast.success("تم التحقق من تغيير Snapchat؛ سيظهر في V2 بعد تحديث كتالوج المزامنة."); load(appliedRange); }}
            />

            <section>
                <nav className="flex min-h-14 items-end gap-6 overflow-x-auto rounded-t-2xl border border-b-0 border-slate-200 bg-white px-4">
                    {ENTITY_TABS.map((tab) => {
                        const enabled = tab.id === "campaign" || (tab.id === "ad_group" && selectedCampaign) || (tab.id === "ad" && selectedAdGroup);
                        return <button key={tab.id} type="button" disabled={!enabled} onClick={() => { if (tab.id === "campaign") { setEntityLevel("campaign"); setChildContract(null); setSelectedCampaign(null); setSelectedAdGroup(null); setManagementTarget(null); } else if (tab.id === "ad_group") returnToAdGroups(); }} className={`relative shrink-0 px-1 pb-3 pt-4 text-sm font-black ${entityLevel === tab.id ? "text-slate-950" : enabled ? "text-slate-600" : "text-slate-300"}`}>{tab.label}{entityLevel === tab.id && <span className="absolute inset-x-0 bottom-0 h-0.5 bg-amber-400" />}</button>;
                    })}
                    <span className="mr-auto pb-3 text-[11px] font-bold text-slate-400">قراءة V2 موحدة · إدارة Snapchat محكومة</span>
                </nav>
                {(selectedCampaign || selectedAdGroup) && <div className="flex flex-wrap items-center gap-2 border-x border-b border-slate-200 bg-slate-50 px-4 py-3 text-xs font-bold text-slate-600"><button type="button" onClick={() => { setEntityLevel("campaign"); setChildContract(null); setSelectedCampaign(null); setSelectedAdGroup(null); setManagementTarget(null); }} className="rounded-lg bg-white px-3 py-1.5 text-emerald-700 shadow-sm">كل الحملات</button>{selectedCampaign && <><span>/</span><button type="button" onClick={returnToAdGroups} className="rounded-lg px-2 py-1.5 hover:bg-white">{selectedCampaign.entity.name}</button></>}{selectedAdGroup && <><span>/</span><span className="rounded-lg bg-violet-50 px-2 py-1.5 text-violet-700">{selectedAdGroup.entity.name}</span></>}</div>}
                <UnifiedMarketingEntityTable report={activeContract} loading={loading || entityLoading} onOpenChildren={openChildren} onManageEntity={manageEntity} />
            </section>

            <UnifiedMarketingOrdersPanel report={campaignContract} campaignId={selectedCampaign?.entity?.id || null} />
        </div>
    );
}
