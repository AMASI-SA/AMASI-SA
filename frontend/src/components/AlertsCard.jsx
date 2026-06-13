/**
 * Iter-159h — Smart Settlement Alerts • Dashboard Card
 * ----------------------------------------------------
 * Collapsible card showing the top N (≤5) active alerts inline on the
 * dashboard.  Collapsed state is persisted in localStorage.
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { CaretDown, CaretUp, BellRinging, ArrowSquareOut, Check } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { toast } from "sonner";


const LS_KEY = "mezan.dashboard.alerts.collapsed";
const SEV_BADGE = {
    critical: "bg-rose-100 text-rose-700 border-rose-300",
    warning: "bg-amber-100 text-amber-700 border-amber-300",
    info: "bg-sky-100 text-sky-700 border-sky-300",
};
const SEV_DOT = {
    critical: "bg-rose-500",
    warning: "bg-amber-500",
    info: "bg-sky-500",
};


export default function AlertsCard() {
    const [collapsed, setCollapsed] = useState(() => {
        try { return localStorage.getItem(LS_KEY) === "1"; } catch { return false; }
    });
    const [data, setData] = useState({ alerts: [], by_severity: { critical: 0, warning: 0, info: 0 } });
    const [busy, setBusy] = useState(false);
    const navigate = useNavigate();

    const load = useCallback(async () => {
        setBusy(true);
        try {
            const { data } = await api.get("/alerts?limit=5");
            setData(data);
        } catch (e) {
            // silent
        } finally { setBusy(false); }
    }, []);

    useEffect(() => { load(); }, [load]);

    const toggle = () => {
        setCollapsed((c) => {
            const next = !c;
            try { localStorage.setItem(LS_KEY, next ? "1" : "0"); } catch {}
            return next;
        });
    };

    const runRefresh = async () => {
        setBusy(true);
        try {
            await api.post("/alerts/refresh");
            await load();
            toast.success("تم تحديث التنبيهات");
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التحديث");
        } finally { setBusy(false); }
    };

    const markRead = async (a) => {
        try {
            await api.post(`/alerts/${a.id}/read`);
            await load();
        } catch (e) { /* silent */ }
    };

    const goTo = (a) => {
        if (!a.related_entity_url) return;
        markRead(a);
        navigate(a.related_entity_url);
    };

    const total = data.alerts.length;
    const sev = data.by_severity || {};

    return (
        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden" data-testid="dashboard-alerts-card">
            {/* Header — always visible, click to toggle */}
            <button
                onClick={toggle}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-50 transition-colors"
                data-testid="dashboard-alerts-toggle"
            >
                <div className="flex items-center gap-3">
                    <div className={`w-9 h-9 rounded-lg inline-flex items-center justify-center ${total > 0 ? "bg-rose-50" : "bg-emerald-50"}`}>
                        <BellRinging size={20} weight="fill" className={total > 0 ? "text-rose-600" : "text-emerald-600"} />
                    </div>
                    <div className="text-right">
                        <div className="text-sm font-extrabold text-slate-900">التنبيهات الذكية</div>
                        <div className="text-[11px] text-slate-500">
                            {total === 0
                                ? "كل شيء على ما يرام"
                                : `${total} تنبيه نشط${sev.critical ? ` • ${sev.critical} حرج` : ""}${sev.warning ? ` • ${sev.warning} تحذير` : ""}`}
                        </div>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    {/* Severity counts as pills */}
                    {sev.critical > 0 && (
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-bold ${SEV_BADGE.critical}`} data-testid="dashboard-alerts-critical-count">
                            {sev.critical} حرج
                        </span>
                    )}
                    {sev.warning > 0 && (
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border font-bold ${SEV_BADGE.warning}`} data-testid="dashboard-alerts-warning-count">
                            {sev.warning} تحذير
                        </span>
                    )}
                    {collapsed ? <CaretDown size={18} className="text-slate-400" /> : <CaretUp size={18} className="text-slate-400" />}
                </div>
            </button>

            {!collapsed && (
                <div className="border-t border-slate-200">
                    <div className="px-4 py-2 bg-slate-50 flex items-center justify-between text-[11px]">
                        <button
                            onClick={runRefresh}
                            disabled={busy}
                            className="text-indigo-700 hover:text-indigo-900 font-bold disabled:text-slate-400"
                            data-testid="dashboard-alerts-refresh"
                        >
                            {busy ? "جاري الفحص..." : "🔄 إعادة الفحص الآن"}
                        </button>
                        <button
                            onClick={() => navigate("/alerts")}
                            className="text-slate-600 hover:text-slate-900 font-bold"
                            data-testid="dashboard-alerts-view-all"
                        >
                            عرض الكل →
                        </button>
                    </div>

                    {data.alerts.length === 0 ? (
                        <div className="px-4 py-8 text-center" data-testid="dashboard-alerts-empty">
                            <div className="text-3xl mb-2">✨</div>
                            <div className="text-sm font-bold text-slate-700">لا توجد تنبيهات نشطة</div>
                            <div className="text-xs text-slate-400 mt-1">سنعلمك فور حدوث أي شيء يحتاج انتباهك</div>
                        </div>
                    ) : (
                        <ul className="divide-y divide-slate-100" data-testid="dashboard-alerts-list">
                            {data.alerts.slice(0, 5).map((a) => (
                                <li key={a.id} className="px-4 py-3 hover:bg-slate-50 transition-colors" data-testid={`dashboard-alert-${a.id}`}>
                                    <div className="flex items-start gap-2.5">
                                        <span className={`mt-1.5 w-2 h-2 rounded-full ${SEV_DOT[a.severity] || SEV_DOT.info} flex-shrink-0`}></span>
                                        <div className="flex-1 min-w-0">
                                            <div className="text-xs font-extrabold text-slate-900">{a.title}</div>
                                            <div className="text-[11px] text-slate-600 mt-0.5 leading-snug">{a.message}</div>
                                            <div className="flex items-center gap-2 mt-2">
                                                {a.related_entity_url && (
                                                    <button onClick={() => goTo(a)} className="text-[10px] bg-indigo-50 text-indigo-700 hover:bg-indigo-100 px-2 py-0.5 rounded font-bold inline-flex items-center gap-1" data-testid={`dashboard-alert-goto-${a.id}`}>
                                                        <ArrowSquareOut size={10} weight="bold" /> فتح
                                                    </button>
                                                )}
                                                {a.status === "new" && (
                                                    <button onClick={() => markRead(a)} className="text-[10px] bg-slate-100 text-slate-700 hover:bg-slate-200 px-2 py-0.5 rounded font-bold inline-flex items-center gap-1" data-testid={`dashboard-alert-read-${a.id}`}>
                                                        <Check size={10} weight="bold" /> مقروء
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
}
