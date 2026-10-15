import { useEffect, useState } from "react";
import {
    ArrowClockwise,
    CheckCircle,
    LockKey,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    getMetaManagementHierarchy,
    getMetaManagementReadiness,
} from "../../services/metaIntegrationsV2";
import { useOptionalAuth } from "../../context/AuthContext";
import MetaEntityActions from "./MetaEntityActions";

const CAPABILITY_LABELS = {
    campaign_status_update: "تشغيل وإيقاف الحملة",
    adset_status_update: "تشغيل وإيقاف المجموعة",
    ad_status_update: "تشغيل وإيقاف الإعلان",
    campaign_budget_update: "تعديل ميزانية الحملة",
    adset_budget_update: "تعديل ميزانية المجموعة",
    adset_bid_update: "تعديل تكلفة التحويل Bid/Cost Cap",
    campaign_create: "إنشاء حملة جديدة",
    campaign_clone: "نسخ حملة قديمة",
};

export default function MetaManagementReadiness() {
    const { user } = useOptionalAuth() || {};
    const reviewer = user?.role === "meta_reviewer";
    const [result, setResult] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [hierarchy, setHierarchy] = useState(null);
    const [hierarchyLoading, setHierarchyLoading] = useState(false);

    async function inspect() {
        setLoading(true);
        setError("");
        try {
            setResult(await getMetaManagementReadiness());
        } catch (requestError) {
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "string" ? detail : detail?.message)
                || "تعذر التحقق المباشر من جاهزية إدارة Meta.",
            );
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { inspect(); }, []);

    async function loadHierarchy(accountId) {
        setHierarchyLoading(true);
        setError("");
        try {
            setHierarchy(await getMetaManagementHierarchy(accountId));
        } catch (requestError) {
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "string" ? detail : detail?.message)
                || "تعذر قراءة حملات Meta.",
            );
        } finally {
            setHierarchyLoading(false);
        }
    }

    function moneyMinor(value) {
        if (value === null || value === undefined || value === "") return "—";
        const parsed = Number(value);
        if (!Number.isFinite(parsed)) return "—";
        return `${(parsed / 100).toFixed(2)} ر.س`;
    }

    return (
        <section
            className="rounded-xl border border-violet-200 bg-violet-50/50 p-4"
            data-testid="meta-management-readiness"
        >
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div className="flex items-center gap-2 text-sm font-extrabold text-slate-900">
                        <LockKey size={19} weight="duotone" className="text-violet-700" />
                        جاهزية إدارة حملات Meta
                    </div>
                    <p className="mt-1 text-[11px] leading-5 text-slate-500">
                        فحص مباشر وآمن للتوكن ودور الحساب. لا يغيّر حملة أو ميزانية ولا يبدأ صرفًا.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={inspect}
                    disabled={loading}
                    className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-violet-200 bg-white px-3 text-xs font-extrabold text-violet-800 disabled:opacity-50"
                    data-testid="meta-management-readiness-refresh"
                >
                    {loading ? <SpinnerGap size={16} className="animate-spin" /> : <ArrowClockwise size={16} />}
                    إعادة الفحص
                </button>
            </div>

            {loading && !result ? (
                <div className="mt-3 flex items-center gap-2 text-xs text-slate-500">
                    <SpinnerGap size={16} className="animate-spin" />
                    جاري التحقق من Meta…
                </div>
            ) : error ? (
                <div className="mt-3 flex items-start gap-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
                    <WarningCircle size={17} className="shrink-0" weight="fill" />
                    {error}
                </div>
            ) : result && (
                <>
                    <div className={`mt-3 flex items-center gap-2 rounded-lg border p-3 text-xs font-extrabold ${
                        result.write_ready
                            ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                            : "border-amber-200 bg-amber-50 text-amber-800"
                    }`}>
                        {result.write_ready
                            ? <CheckCircle size={18} weight="fill" />
                            : <WarningCircle size={18} weight="fill" />}
                        {result.write_ready
                            ? "الصلاحيات ودور الحساب جاهزان لبناء أوامر الإدارة."
                            : "لم تكتمل الجاهزية؛ لن يسمح ميزان بتنفيذ أي تعديل."}
                    </div>

                    <div className="mt-3 grid gap-2 sm:grid-cols-2">
                        {Object.entries(CAPABILITY_LABELS).map(([key, label]) => (
                            <div key={key} className="flex items-center justify-between gap-2 rounded-lg border border-white bg-white/80 px-3 py-2 text-xs">
                                <span className="font-bold text-slate-700">{label}</span>
                                <span className={`font-extrabold ${result.capabilities[key] ? "text-emerald-700" : "text-amber-700"}`}>
                                    {result.capabilities[key] ? "متاح" : "غير متاح"}
                                </span>
                            </div>
                        ))}
                    </div>

                    <div className="mt-3 space-y-2">
                        {result.accounts.map((account) => (
                            <div key={account.account_id} className="rounded-lg border border-violet-100 bg-white p-3 text-xs">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="font-extrabold text-slate-800">{account.display_name || account.account_id}</div>
                                        <div className="mt-0.5 font-mono text-[10px] text-slate-400">{account.account_id}</div>
                                    </div>
                                    <span className={`rounded-full px-2 py-1 text-[10px] font-extrabold ${account.ready ? "bg-emerald-100 text-emerald-700" : "bg-amber-100 text-amber-800"}`}>
                                        {account.ready ? "جاهز" : "يحتاج مراجعة"}
                                    </span>
                                </div>
                                <div className="mt-2 text-[11px] text-slate-500">
                                    مهام الحساب: {account.tasks.length ? account.tasks.join("، ") : "لم تثبت من Meta"}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => loadHierarchy(account.account_id)}
                                    disabled={!account.readable || hierarchyLoading}
                                    className="mt-2 inline-flex min-h-8 items-center gap-2 rounded-md border border-violet-200 px-2.5 text-[11px] font-extrabold text-violet-800 disabled:opacity-50"
                                    data-testid={`meta-management-open-${account.account_id}`}
                                >
                                    {hierarchyLoading ? <SpinnerGap size={14} className="animate-spin" /> : null}
                                    عرض الحملات والمجموعات والإعلانات
                                </button>
                            </div>
                        ))}
                    </div>

                    {hierarchy && (
                        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-3" data-testid="meta-management-hierarchy">
                            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                                <div className="text-xs font-extrabold text-slate-800">الهيكل المباشر من Meta</div>
                                <div className="font-mono text-[10px] text-slate-400">
                                    {hierarchy.campaigns.length} حملة · {hierarchy.adsets.length} مجموعة · {hierarchy.ads.length} إعلان
                                </div>
                            </div>
                            <div className="max-h-[32rem] space-y-2 overflow-y-auto">
                                {hierarchy.campaigns.map((campaign) => {
                                    const adsets = hierarchy.adsets.filter((row) => row.campaign_id === campaign.id);
                                    return (
                                        <div key={campaign.id} className="rounded-lg border border-slate-200 p-3">
                                            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                                                <div>
                                                    <div className="font-extrabold text-slate-900">{campaign.name || campaign.id}</div>
                                                    <div className="font-mono text-[10px] text-slate-400">{campaign.id}</div>
                                                </div>
                                                <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-700">
                                                    {campaign.effective_status || campaign.status || "UNKNOWN"}
                                                </span>
                                            </div>
                                            <div className="mt-2 text-[11px] text-slate-500">
                                                ميزانية الحملة: {moneyMinor(campaign.daily_budget || campaign.lifetime_budget)}
                                            </div>
                                            <MetaEntityActions
                                                accountId={hierarchy.account_id}
                                                entity={campaign}
                                                entityType="campaign"
                                                reviewer={reviewer}
                                                onExecuted={() => loadHierarchy(hierarchy.account_id)}
                                            />
                                            <div className="mt-2 space-y-2 border-r-2 border-violet-100 pr-3">
                                                {adsets.map((adset) => {
                                                    const ads = hierarchy.ads.filter((row) => row.adset_id === adset.id);
                                                    return (
                                                        <div key={adset.id} className="rounded-md bg-slate-50 p-2 text-[11px]">
                                                            <div className="font-extrabold text-slate-800">المجموعة: {adset.name || adset.id}</div>
                                                            <div className="mt-1 grid gap-1 sm:grid-cols-2 text-slate-500">
                                                                <span>الميزانية: {moneyMinor(adset.daily_budget || adset.lifetime_budget)}</span>
                                                                <span>استراتيجية المزايدة: {adset.bid_strategy || "—"}</span>
                                                                <span>تكلفة التحويل/Bid: {moneyMinor(adset.bid_amount)}</span>
                                                                <span>التحسين: {adset.optimization_goal || "—"}</span>
                                                            </div>
                                                            <MetaEntityActions
                                                                accountId={hierarchy.account_id}
                                                                entity={adset}
                                                                entityType="adset"
                                                                reviewer={reviewer}
                                                                onExecuted={() => loadHierarchy(hierarchy.account_id)}
                                                            />
                                                            <div className="mt-2 space-y-1">
                                                                {ads.map((ad) => (
                                                                    <div key={ad.id} className="rounded border border-slate-200 bg-white px-2 py-1.5">
                                                                        <div className="flex items-center justify-between gap-2">
                                                                            <span className="font-bold text-slate-700">الإعلان: {ad.name || ad.id}</span>
                                                                            <span className="text-[10px] text-slate-500">{ad.effective_status || ad.status || "UNKNOWN"}</span>
                                                                        </div>
                                                                        <MetaEntityActions
                                                                            accountId={hierarchy.account_id}
                                                                            entity={ad}
                                                                            entityType="ad"
                                                                            reviewer={reviewer}
                                                                            onExecuted={() => loadHierarchy(hierarchy.account_id)}
                                                                        />
                                                                    </div>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {result.missing_scopes.length > 0 && (
                        <div className="mt-3 text-xs font-bold text-amber-800">
                            الصلاحيات الناقصة: {result.missing_scopes.join("، ")}
                        </div>
                    )}
                </>
            )}
        </section>
    );
}
