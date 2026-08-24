import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    CheckCircle,
    Clock,
    Ghost,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

function localDateInTimezone(timezone) {
    try {
        return new Intl.DateTimeFormat("en-CA", {
            timeZone: timezone || "Asia/Riyadh",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
        }).format(new Date());
    } catch {
        return new Date().toISOString().slice(0, 10);
    }
}

function localTimeInTimezone(timezone, nowMs) {
    try {
        return new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh",
            hour: "2-digit",
            minute: "2-digit",
            hour12: false,
        }).format(new Date(nowMs));
    } catch {
        return "—";
    }
}

function localHourInTimezone(timezone, nowMs) {
    try {
        const value = new Intl.DateTimeFormat("en-GB", {
            timeZone: timezone || "Asia/Riyadh",
            hour: "2-digit",
            hour12: false,
        }).format(new Date(nowMs));
        const hour = Number(value);
        return Number.isFinite(hour) ? hour : null;
    } catch {
        return null;
    }
}

function money(value, currency = "USD") {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    return `${amount.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ${currency}`;
}

function statusTone(status) {
    if (["complete", "healthy", "released"].includes(status)) return "text-emerald-700 bg-emerald-50 border-emerald-200";
    if (["partial", "running", "held", "pending"].includes(status)) return "text-amber-700 bg-amber-50 border-amber-200";
    return "text-slate-700 bg-slate-50 border-slate-200";
}

function isCampaignActive(campaign) {
    if (campaign?.active === true) return true;
    return ["ACTIVE", "RUNNING", "LIVE"].includes(String(campaign?.status || "").toUpperCase());
}

function displayHourStatus(row, selectedDate, timezone, nowMs) {
    const today = localDateInTimezone(timezone);
    const currentHour = localHourInTimezone(timezone, nowMs);
    const rowHour = Number(String(row?.local_hour || "").slice(0, 2));

    if (selectedDate > today) return "future";
    if (
        selectedDate === today
        && Number.isFinite(rowHour)
        && currentHour !== null
    ) {
        if (rowHour > currentHour) return "future";
        if (rowHour === currentHour) {
            return row?.spend_native == null ? "provisional_unavailable" : "provisional";
        }
    }

    const start = Date.parse(row?.hour_start_utc || "");
    const end = Date.parse(row?.hour_end_utc || "");
    if (!Number.isFinite(start) || !Number.isFinite(end)) return row?.status || "—";

    if (nowMs >= start && nowMs < end) {
        return row?.spend_native == null ? "provisional_unavailable" : "provisional";
    }
    if (nowMs < start) return "future";
    if (row?.status === "future") return "awaiting_refresh";
    return row?.status || "—";
}

export default function SnapchatV2Page() {
    const [status, setStatus] = useState(null);
    const [report, setReport] = useState(null);
    const [hourly, setHourly] = useState(null);
    const [campaigns, setCampaigns] = useState([]);
    const [date, setDate] = useState("");
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");
    const [clockNow, setClockNow] = useState(() => Date.now());

    const account = status?.selected_account || null;
    const accountId = account?.ad_account_id || "";
    const currency = account?.currency || report?.currency || "USD";
    const accountTimezone = account?.timezone || "America/Los_Angeles";

    const load = useCallback(async (requestedDate) => {
        setLoading(true);
        setError("");
        try {
            const { data: statusData } = await api.get(
                "/integrations-v2/snapchat-v2/status",
            );
            setStatus(statusData);

            const selectedDate = requestedDate || date || localDateInTimezone(
                statusData?.selected_account?.timezone || "America/Los_Angeles",
            );
            if (!date) setDate(selectedDate);

            const common = {
                action_report_time: "conversion",
                timezone: "account",
            };
            const [reportResult, hourlyResult, campaignsResult] = await Promise.all([
                api.get("/integrations-v2/snapchat-v2/report", {
                    params: { ...common, date_from: selectedDate, date_to: selectedDate },
                }),
                api.get("/integrations-v2/snapchat-v2/hourly", {
                    params: { ...common, report_date: selectedDate },
                }),
                api.get("/integrations-v2/snapchat-v2/campaigns", {
                    params: { ...common, date_from: selectedDate, date_to: selectedDate },
                }),
            ]);
            setReport(reportResult.data);
            setHourly(hourlyResult.data);
            setCampaigns(campaignsResult.data?.campaigns || []);
            setClockNow(Date.now());
        } catch (err) {
            const message = formatApiErrorDetail(err.response?.data?.detail)
                || "تعذر تحميل بيانات Snapchat V2";
            setError(message);
            toast.error(message);
        } finally {
            setLoading(false);
        }
    }, [date]);

    useEffect(() => {
        load();
        // Page cutover intentionally reads V2 only. Dashboard and AI remain unchanged.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        const timer = window.setInterval(() => setClockNow(Date.now()), 60_000);
        return () => window.clearInterval(timer);
    }, []);

    const syncDay = async () => {
        if (!date || !accountId) return;
        setSyncing(true);
        try {
            const { data } = await api.post("/integrations-v2/snapchat-v2/sync", {
                ad_account_id: accountId,
                date_from: date,
                date_to: date,
                action_report_time: "conversion",
                run_type: "manual",
            });
            if (data?.status === "skipped" && data?.reason === "lease_unavailable") {
                toast.warning("توجد مزامنة تلقائية قيد التشغيل الآن. أعد المحاولة بعد قليل.");
            } else if (data?.status === "complete") {
                toast.success("اكتملت مزامنة Snapchat V2");
            } else {
                toast.warning("اكتملت المزامنة مع ملاحظات جزئية، والبيانات المالية تبقى مستقلة.");
            }
            await load(date);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تشغيل مزامنة Snapchat V2");
        } finally {
            setSyncing(false);
        }
    };

    const knownHours = useMemo(
        () => (hourly?.hours || []).filter((row) => (
            row?.spend_native !== null
            && row?.spend_native !== undefined
            && Number.isFinite(Number(row.spend_native))
        )),
        [hourly],
    );
    const confirmedHours = useMemo(
        () => (hourly?.hours || []).filter((row) => row.status === "confirmed_data"),
        [hourly],
    );
    const maxHourSpend = useMemo(
        () => Math.max(1, ...knownHours.map((row) => Number(row.spend_native) || 0)),
        [knownHours],
    );
    const sortedCampaigns = useMemo(
        () => [...campaigns].sort((a, b) => {
            const activeDelta = Number(isCampaignActive(b)) - Number(isCampaignActive(a));
            if (activeDelta) return activeDelta;
            const spendDelta = Number(b?.spend_native || 0) - Number(a?.spend_native || 0);
            if (spendDelta) return spendDelta;
            return String(a?.name || "").localeCompare(String(b?.name || ""), "ar");
        }),
        [campaigns],
    );
    const financialDisplayStatus = (
        status?.financial_sync_status === "complete" || status?.last_success?.financial
    ) ? "complete" : (status?.financial_sync_status || "—");

    return (
        <div className="space-y-5" dir="rtl" data-testid="snapchat-v2-page">
            <header className="rounded-2xl border border-yellow-300 bg-gradient-to-br from-yellow-50 to-amber-50 p-5 sm:p-7">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <div className="mb-2 flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-2 rounded-full border border-yellow-300 bg-white px-3 py-1 text-xs font-black text-amber-800">
                                <Ghost size={16} weight="fill" /> Snapchat V2
                            </span>
                            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">
                                صفحة سناب على V2
                            </span>
                            <span className="rounded-full border border-slate-200 bg-white px-3 py-1 text-xs font-bold text-slate-600">
                                Dashboard و AI ما زالا على V1
                            </span>
                        </div>
                        <h1 className="text-3xl font-black tracking-tight">إعلانات سناب شات</h1>
                        <p className="mt-2 text-sm font-semibold text-slate-600">
                            المصدر المباشر: Snapchat Integration V2 · توقيت الحساب {accountTimezone} · الآن {localTimeInTimezone(accountTimezone, clockNow)}
                        </p>
                    </div>
                    <div className="flex flex-col gap-2 sm:flex-row">
                        <input
                            type="date"
                            value={date}
                            onChange={(event) => setDate(event.target.value)}
                            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-bold"
                            dir="ltr"
                        />
                        <button
                            type="button"
                            onClick={() => load(date)}
                            disabled={loading || !date}
                            className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-black disabled:opacity-50"
                        >
                            <ArrowsClockwise size={17} className={loading ? "animate-spin" : ""} /> تحديث العرض
                        </button>
                        <button
                            type="button"
                            onClick={syncDay}
                            disabled={syncing || !date || !accountId}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-yellow-500 px-4 py-2 text-sm font-black text-white disabled:opacity-50"
                        >
                            <ArrowsClockwise size={17} className={syncing ? "animate-spin" : ""} />
                            {syncing ? "جاري المزامنة" : "مزامنة V2"}
                        </button>
                    </div>
                </div>
            </header>

            {error && (
                <div className="flex items-center gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-700">
                    <WarningCircle size={22} weight="fill" /> {error}
                </div>
            )}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-500">الحساب المعتمد</div>
                    <div className="mt-2 text-lg font-black">{account?.display_name || "—"}</div>
                    <div className="mt-1 truncate text-xs text-slate-500" dir="ltr">{accountId || "—"}</div>
                </div>
                <div className="rounded-xl border border-yellow-200 bg-yellow-50 p-4">
                    <div className="text-xs font-bold text-amber-700">صرف اليوم المختار</div>
                    <div className="mt-2 text-3xl font-black text-amber-950">{money(report?.base_spend_native, currency)}</div>
                    <div className="mt-1 text-xs font-bold text-amber-700">{report?.projection_timezone || accountTimezone}</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-500">اكتمال المبلغ</div>
                    <div className="mt-2 flex items-center gap-2 text-xl font-black">
                        {report?.amount_complete ? <CheckCircle className="text-emerald-600" weight="fill" /> : <Clock className="text-amber-600" />}
                        {report?.amount_complete ? "مكتمل" : "قيد التحديث"}
                    </div>
                    <div className="mt-1 text-xs text-slate-500">{hourly?.coverage?.known_fact_hours ?? 0}/{hourly?.coverage?.expected_local_hours ?? 24} ساعة</div>
                </div>
                <div className="rounded-xl border border-slate-200 bg-white p-4">
                    <div className="text-xs font-bold text-slate-500">آخر مزامنة مالية ناجحة</div>
                    <div className={`mt-2 inline-flex rounded-full border px-3 py-1 text-sm font-black ${statusTone(financialDisplayStatus)}`}>
                        Financial: {financialDisplayStatus}
                    </div>
                    <div className="mt-2 text-xs text-slate-500">آخر نجاح: {status?.last_success?.financial?.finished_at || status?.last_success?.financial?.started_at || "—"}</div>
                </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <div className="mb-4 flex items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-black">الصرف بالساعة</h2>
                        <p className="text-xs font-semibold text-slate-500">حسب توقيت حساب Snapchat · الساعة الحالية تُحسب من UTC مع DST · الساعات المستقبلية لا تُعرض كصفر</p>
                    </div>
                    <div className="text-xs font-black text-slate-500">{confirmedHours.length} ساعة مؤكدة</div>
                </div>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
                    {(hourly?.hours || []).map((row) => {
                        const spend = Number(row.spend_native);
                        const known = row?.spend_native !== null
                            && row?.spend_native !== undefined
                            && Number.isFinite(spend);
                        const pct = known ? Math.max(4, (spend / maxHourSpend) * 100) : 0;
                        const effectiveStatus = displayHourStatus(row, date, accountTimezone, clockNow);
                        return (
                            <div key={row.local_hour} className="rounded-lg border border-slate-100 bg-slate-50 p-2">
                                <div className="text-xs font-black" dir="ltr">{row.local_hour}</div>
                                <div className="mt-2 flex h-16 items-end overflow-hidden rounded bg-white">
                                    {known && <div className="w-full rounded bg-yellow-400" style={{ height: `${pct}%` }} />}
                                </div>
                                <div className="mt-2 text-xs font-black" dir="ltr">{known ? money(spend, currency) : "—"}</div>
                                <div className="text-[10px] font-bold text-slate-400">{effectiveStatus}</div>
                            </div>
                        );
                    })}
                </div>
            </section>

            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <div className="mb-4 flex items-center justify-between gap-3">
                    <div>
                        <h2 className="text-lg font-black">الحملات</h2>
                        <p className="text-xs font-semibold text-slate-500">الحملات النشطة أولًا، ثم الأعلى صرفًا · قراءة من facts V2 لنفس التاريخ المختار</p>
                    </div>
                    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black">{campaigns.length} حملة</span>
                </div>
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[760px] text-sm">
                        <thead className="border-b border-slate-200 text-xs text-slate-500">
                            <tr>
                                <th className="p-2 text-right">الحملة</th>
                                <th className="p-2 text-right">الحالة</th>
                                <th className="p-2 text-right">الصرف</th>
                                <th className="p-2 text-right">المشاهدات</th>
                                <th className="p-2 text-right">المشتريات</th>
                                <th className="p-2 text-right">ROAS</th>
                            </tr>
                        </thead>
                        <tbody>
                            {sortedCampaigns.map((campaign) => (
                                <tr key={campaign.campaign_id} className="border-b border-slate-100 last:border-0">
                                    <td className="p-2 font-bold">{campaign.name}</td>
                                    <td className="p-2">{campaign.status || "—"}</td>
                                    <td className="p-2 font-black" dir="ltr">{money(campaign.spend_native, currency)}</td>
                                    <td className="p-2" dir="ltr">{Number(campaign.impressions || 0).toLocaleString("en-US")}</td>
                                    <td className="p-2" dir="ltr">{Number(campaign.purchases || 0).toLocaleString("en-US")}</td>
                                    <td className="p-2" dir="ltr">{campaign.roas == null ? "—" : Number(campaign.roas).toFixed(2)}</td>
                                </tr>
                            ))}
                            {!loading && campaigns.length === 0 && (
                                <tr><td colSpan={6} className="p-8 text-center font-bold text-slate-400">لا توجد حملات ذات facts في التاريخ المختار.</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </section>
        </div>
    );
}
