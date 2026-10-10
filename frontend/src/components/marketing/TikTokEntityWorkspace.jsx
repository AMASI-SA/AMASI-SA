import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";
import { syncTikTokReporting } from "../../services/tiktokIntegrationsV2";
import TikTokCampaignAnalysis from "./TikTokCampaignAnalysis";
import TikTokCampaignControls from "./TikTokCampaignControls";

const LEVELS = [["campaign", "الحملات"], ["adgroup", "المجموعات الإعلانية"], ["ad", "الإعلانات"]];
function number(value, digits = 0) {
    return value === null || value === undefined ? "—"
        : Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
}

export default function TikTokEntityWorkspace({ dateFrom, dateTo, query = "", onSynced }) {
    const [kind, setKind] = useState("campaign");
    const [page, setPage] = useState(1);
    const [parent, setParent] = useState({});
    const [accountId, setAccountId] = useState("");
    const [accounts, setAccounts] = useState([]);
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const [revision, setRevision] = useState(0);
    const [analysis, setAnalysis] = useState(null);
    const [management, setManagement] = useState(null);
    const analysisKey = JSON.stringify([dateFrom, dateTo, query, kind, page, accountId, revision]);
    useEffect(() => { setAnalysis(null); }, [dateFrom, dateTo, query, kind, page, accountId, revision]);
    useEffect(() => { setManagement(null); }, [dateFrom, dateTo, query, kind, page, accountId, revision]);
    const sequence = useRef(0);
    const onSyncedRef = useRef(onSynced);
    useEffect(() => { onSyncedRef.current = onSynced; }, [onSynced]);
    const rangeKey = `${dateFrom}:${dateTo}:${query}`;
    const previousRange = useRef(rangeKey);
    useEffect(() => {
        let active = true;
        const controller = new AbortController();
        const request = ++sequence.current;
        setLoading(true); setError(""); setReport(null);
        api.get("/integrations-v2/tiktok_ads/workspace", { signal: controller.signal, params: {
            from_date: dateFrom, to_date: dateTo, entity_type: kind, page, limit: 25,
            campaign_query: query, campaign_id: parent.campaign_id, adgroup_id: parent.adgroup_id,
            account_id: accountId || undefined,
        } }).then(({ data }) => {
            if (active && request === sequence.current) { setReport(data); setAccounts(data.accounts || []); }
        }).catch((failure) => {
            if (active && request === sequence.current) setError(failure?.response?.data?.detail?.message || "تعذر تحميل بيانات TikTok.");
        }).finally(() => {
            if (active && request === sequence.current) setLoading(false);
        });
        return () => { active = false; controller.abort(); };
    }, [dateFrom, dateTo, query, kind, page, parent, accountId, revision]);
    useEffect(() => {
        if (previousRange.current !== rangeKey) {
            previousRange.current = rangeKey; setPage(1); setParent({});
        }
    }, [rangeKey]);
    async function sync() {
        setSyncing(true); setError(""); setMessage("");
        try {
            const result = await syncTikTokReporting({ days: 30 });
            const counts = result.hierarchy?.entity_counts || {};
            const range = result.date_from && result.date_to ? ` · ${result.date_from} ← ${result.date_to}` : "";
            setMessage(`${result.status === "complete" ? "اكتملت المزامنة" : "مزامنة جزئية"} · ${counts.campaign || 0} حملة · ${counts.adgroup || 0} مجموعة · ${counts.ad || 0} إعلان/تصميم · ${result.errors_count} ملاحظة${range}`);
            setRevision((current) => current + 1); onSyncedRef.current?.();
        } catch (failure) { setError(failure.message || "تعذرت المزامنة."); }
        finally { setSyncing(false); }
    }
    const pagination = report?.campaign_pagination || {};
    const selectedAccount = accounts.find((account) => account.account_id === accountId);
    function completed(result) {
        const id = result.created_campaign_id || result.campaign_id;
        setMessage(`ثبت تنفيذ TikTok · ${result.campaign_name || id} · ${id}${result.action === "create" ? " · موقوفة" : ""}`);
        if (kind === "campaign" && result.verified === true) {
            setReport((current) => current && ({ ...current, entities: (current.entities || []).map((row) =>
                row.account_id === result.account_id && row.entity_id === id ? { ...row,
                    entity_name: result.after?.campaign_name ?? row.entity_name,
                    status: result.after?.operation_status ?? row.status,
                    delivery_status: result.after?.secondary_status ?? row.delivery_status,
                    budget_native: result.after?.budget ?? row.budget_native,
                    budget_mode: result.after?.budget_mode ?? row.budget_mode,
                } : row) }));
        }
    }
    return <section className="space-y-3" data-testid="tiktok-native-entity-workspace" dir="rtl">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white p-4">
            <div><h2 className="font-black">حملات TikTok المباشرة</h2><p className="text-xs text-slate-500">الحساب ← الحملة ← المجموعة الإعلانية ← الإعلان. إجمالي الحساب مستقل عن تفاصيل الحملات.</p></div>
            <button type="button" onClick={sync} disabled={syncing} className="rounded-xl bg-emerald-700 px-4 py-3 text-sm font-black text-white disabled:opacity-50">{syncing ? "جاري مزامنة الحملات والتقارير…" : "مزامنة الحملات والتقارير · 30 يوم"}</button>
        </div>
        <div className="flex flex-wrap items-center gap-3"><button type="button" disabled={!selectedAccount || loading || syncing} onClick={() => { setAnalysis(null); setManagement({ mode: "create", accountId, accountName: selectedAccount.account_name, currency: selectedAccount.currency, selectionKey: analysisKey }); }} className="rounded-xl bg-emerald-700 px-4 py-3 text-sm font-bold text-white disabled:opacity-40">إنشاء حملة موقوفة</button><span className="text-xs text-slate-500">اختر حسابًا محددًا لإنشاء الحملة. تُراجع الإعدادات قبل التنفيذ.</span></div>
        {(error || message) && <div role={error ? "alert" : "status"} className={`rounded-xl p-3 text-sm font-bold ${error ? "bg-rose-50 text-rose-800" : "bg-emerald-50 text-emerald-800"}`}>{error || message}</div>}
        <div className="flex flex-wrap gap-2" role="group" aria-label="مستويات TikTok">
            <select aria-label="حساب TikTok" value={accountId} onChange={(event) => { setAccountId(event.target.value); setPage(1); setParent({}); }} className="rounded-xl border border-slate-200 bg-white px-3 py-2">
                <option value="">كل الحسابات</option>{accounts.map((account) => <option key={account.account_id} value={account.account_id}>{account.account_name}</option>)}
            </select>
            {LEVELS.map(([id, label]) => <button key={id} type="button" aria-pressed={kind === id} onClick={() => { setKind(id); setPage(1); setParent({}); }} className={`rounded-xl px-4 py-3 font-bold ${kind === id ? "bg-slate-950 text-white" : "bg-white text-slate-700"}`}>{label}</button>)}
        </div>
        {parent.campaign_id && <div className="flex items-center gap-3 rounded-xl bg-slate-100 p-3 text-sm"><button type="button" onClick={() => { setKind("campaign"); setPage(1); setParent({}); }}>كل الحملات</button><span> / {parent.name || parent.campaign_id}</span></div>}
        {analysis?.selectionKey === analysisKey && <TikTokCampaignAnalysis campaign={analysis} dateFrom={dateFrom} dateTo={dateTo} onClose={() => setAnalysis(null)} />}
        {management?.selectionKey === analysisKey && <TikTokCampaignControls selection={management} onClose={() => setManagement(null)} onCompleted={completed} />}
        <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white">
            <table className="w-full min-w-[950px] text-right text-sm" data-testid="tiktok-native-entities-table"><thead className="bg-slate-50"><tr>{["الاسم والحساب", "الحالة", "الصرف · ر.س", "تحويلات TikTok", "الظهور", "النقرات", "CTR", "التفاصيل"].map((label) => <th key={label} className="p-3">{label}</th>)}</tr></thead>
                <tbody>{(report?.entities || []).map((row) => <tr key={`${row.account_id}:${row.entity_id}`} className="border-t border-slate-100">
                    <td className="p-3"><div className="font-bold">{row.entity_name}</div><div className="text-xs text-slate-500">{row.account_name}</div>
                        {kind === "ad" && row.identity_level === "creative" && <div className="text-xs font-bold text-violet-700">تصميم Smart+</div>}
                        <div className="font-mono text-[11px] text-slate-400">{kind === "ad" ? `${row.identity_level === "creative" ? "معرّف التصميم" : row.identity_level === "ad" ? "معرّف الإعلان" : "معرّف API"}: ` : ""}{row.entity_id}</div>
                        {kind === "ad" && row.identity_level === "creative" && <div className="font-mono text-[11px] text-slate-500">معرّف الإعلان: {row.platform_ad_id}</div>}
                        {kind !== "campaign" && <div className="font-mono text-[11px] text-slate-400">الحملة: {row.campaign_id}{row.adgroup_id ? ` · المجموعة: ${row.adgroup_id}` : ""}</div>}</td>
                    <td className="p-3"><div>{String(row.delivery_status || "").includes("DELETE") ? "محذوفة" : row.status === "ENABLE" ? "مفعّل" : row.status === "DISABLE" ? "متوقف" : row.status}</div><div className="text-xs text-slate-500">{row.delivery_status || ""}</div></td>
                    <td className="p-3 font-mono">{number(row.spend_sar, 2)}</td><td className="p-3 font-mono">{number(row.conversions, 2)}</td><td className="p-3 font-mono">{number(row.impressions)}</td><td className="p-3 font-mono">{number(row.clicks)}</td><td className="p-3 font-mono">{number(row.ctr_pct, 2)}{row.ctr_pct !== null ? "%" : ""}</td>
                    <td className="p-3"><div className="flex flex-wrap gap-2">{kind !== "ad" && <button type="button" className="rounded-lg bg-violet-50 px-3 py-2 font-bold text-violet-700" onClick={() => { setAccountId(row.account_id); setParent({ campaign_id: row.campaign_id, adgroup_id: kind === "adgroup" ? row.entity_id : undefined, name: row.entity_name }); setKind(kind === "campaign" ? "adgroup" : "ad"); setPage(1); }}>{kind === "campaign" ? "عرض المجموعات" : "عرض الإعلانات"}</button>}
                        {kind === "campaign" && <><button type="button" aria-controls="tiktok-native-ai-panel" disabled={!row.data_complete || loading || syncing} onClick={() => { setManagement(null); setAnalysis({ accountId: row.account_id, campaignId: row.entity_id, name: row.entity_name, selectionKey: analysisKey }); }} className="rounded-lg bg-violet-700 px-3 py-2 font-bold text-white disabled:opacity-40">تحليل بالذكاء</button><button type="button" aria-controls="tiktok-native-management-panel" disabled={loading || syncing || String(row.delivery_status || "").includes("DELETE") || row.status === "DELETE"} onClick={() => { setAnalysis(null); setManagement({ mode: "edit", accountId: row.account_id, campaignId: row.entity_id, name: row.entity_name, accountName: row.account_name, currency: row.currency, selectionKey: analysisKey }); }} className="rounded-lg bg-emerald-50 px-3 py-2 font-bold text-emerald-800 disabled:opacity-40">إدارة الحملة</button></>}</div></td>
                </tr>)}</tbody></table>
            {loading && <div role="status" className="p-8 text-center">جاري تحميل البيانات…</div>}
            {!loading && !report?.entities?.length && <div className="p-8 text-center text-slate-500">لا توجد عناصر متزامنة لهذه الفترة. ابدأ بمزامنة الحملات والتقارير.</div>}
        </div>
        <div className="flex items-center justify-between gap-3 text-sm"><span>{pagination.total || 0} عنصر · صفحة {pagination.page || 1} من {pagination.pages || 1}</span><div className="flex gap-2"><button type="button" disabled={loading || page <= 1} onClick={() => setPage((current) => current - 1)} className="rounded-lg border bg-white px-4 py-2 disabled:opacity-40">السابق</button><button type="button" disabled={loading || page >= (pagination.pages || 1)} onClick={() => setPage((current) => current + 1)} className="rounded-lg border bg-white px-4 py-2 disabled:opacity-40">التالي</button></div></div>
        <p className="text-xs text-slate-500">التحويلات أحداث من TikTok، وقد تختلف عن طلبات ومبيعات سلة. لا تُجمع مستويات الحملات والمجموعات والإعلانات معًا. في Smart+ يعرض هذا المستوى أداء التصاميم ومعرّف الإعلان الذي تتبع له.</p>
    </section>;
}

