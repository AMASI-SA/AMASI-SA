/**
 * Iter-159h — Smart Settlement Alerts • Notification Bell
 * --------------------------------------------------------
 * Floating bell with unread-count badge.  Clicking opens a dropdown
 * listing the latest unread/snoozed alerts.  Each row supports:
 *   • تعليم كمقروء (mark read)
 *   • تأجيل ساعة / يوم / أسبوع (snooze)
 *   • تجاهل (dismiss)
 *   • فتح صفحة الكيان المعني
 *
 * Polls /api/alerts/unread-count every 60s; on open also refreshes
 * the full list.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, Check, Clock, ArrowSquareOut, Trash } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { toast } from "sonner";


const SEV_STYLE = {
    critical: "bg-rose-50 border-rose-300 text-rose-900",
    warning: "bg-amber-50 border-amber-300 text-amber-900",
    info: "bg-sky-50 border-sky-300 text-sky-900",
};
const SEV_DOT = {
    critical: "bg-rose-500",
    warning: "bg-amber-500",
    info: "bg-sky-500",
};


export default function NotificationBell() {
    const [open, setOpen] = useState(false);
    const [count, setCount] = useState(0);
    const [data, setData] = useState({ alerts: [], by_severity: {} });
    const [busy, setBusy] = useState(false);
    const wrapRef = useRef(null);
    const navigate = useNavigate();

    const refreshCount = useCallback(async () => {
        try {
            const { data } = await api.get("/alerts/unread-count");
            setCount(data.count || 0);
        } catch (_) { /* ignore — silent background poll */ }
    }, []);

    const loadList = useCallback(async () => {
        setBusy(true);
        try {
            const { data } = await api.get("/alerts?limit=20");
            setData(data);
            setCount(data.by_severity?.critical + data.by_severity?.warning + data.by_severity?.info || 0);
        } catch (e) {
            // silent
        } finally {
            setBusy(false);
        }
    }, []);

    const runRefresh = async () => {
        setBusy(true);
        try {
            await api.post("/alerts/refresh");
            await loadList();
            toast.success("تم تحديث التنبيهات");
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التحديث");
        } finally { setBusy(false); }
    };

    // Initial + 60s polling for the count badge (no list load).
    useEffect(() => {
        refreshCount();
        const t = setInterval(refreshCount, 60_000);
        return () => clearInterval(t);
    }, [refreshCount]);

    // Close on outside click
    useEffect(() => {
        if (!open) return;
        const handler = (e) => {
            if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
        };
        document.addEventListener("mousedown", handler);
        return () => document.removeEventListener("mousedown", handler);
    }, [open]);

    const onOpen = async () => {
        const next = !open;
        setOpen(next);
        if (next) await loadList();
    };

    const markRead = async (a) => {
        try {
            await api.post(`/alerts/${a.id}/read`);
            await loadList();
        } catch (e) {
            toast.error("فشل التعليم");
        }
    };

    const snooze = async (a, hours) => {
        try {
            await api.post(`/alerts/${a.id}/snooze`, { hours });
            await loadList();
            toast.success(`تم تأجيل التنبيه ${hours >= 24 ? `${Math.round(hours / 24)} يوم` : `${hours} ساعة`}`);
        } catch (e) {
            toast.error("فشل التأجيل");
        }
    };

    const dismiss = async (a) => {
        try {
            await api.post(`/alerts/${a.id}/dismiss`);
            await loadList();
        } catch (e) {
            toast.error("فشل التجاهل");
        }
    };

    const goTo = (a) => {
        if (!a.related_entity_url) return;
        markRead(a); // mark as read on click-through
        setOpen(false);
        navigate(a.related_entity_url);
    };

    const markAllRead = async () => {
        try {
            await api.post("/alerts/read-all");
            await loadList();
            toast.success("تم تعليم كل التنبيهات كمقروءة");
        } catch (e) {
            toast.error("فشل التعليم");
        }
    };

    return (
        <div ref={wrapRef} className="relative" data-testid="notification-bell-wrap">
            <button
                onClick={onOpen}
                className="relative w-10 h-10 inline-flex items-center justify-center rounded-full bg-white border border-slate-200 hover:border-indigo-300 hover:bg-indigo-50 transition-colors shadow-sm"
                data-testid="notification-bell-btn"
                aria-label="التنبيهات"
            >
                <Bell size={20} weight={count > 0 ? "fill" : "regular"} className={count > 0 ? "text-rose-600" : "text-slate-600"} />
                {count > 0 && (
                    <span
                        className="absolute -top-1 -end-1 min-w-[18px] h-[18px] px-1 bg-rose-600 text-white text-[10px] font-extrabold rounded-full inline-flex items-center justify-center border-2 border-white num"
                        data-testid="notification-bell-count"
                    >
                        {count > 99 ? "99+" : count}
                    </span>
                )}
            </button>

            {open && (
                <div
                    className="absolute end-0 mt-2 w-[360px] sm:w-[420px] bg-white border border-slate-200 rounded-xl shadow-2xl z-50 overflow-hidden"
                    data-testid="notification-bell-panel"
                >
                    <div className="flex items-center justify-between p-3 border-b border-slate-200 bg-slate-50">
                        <div className="font-bold text-sm text-slate-800">🔔 التنبيهات</div>
                        <div className="flex items-center gap-2">
                            <button
                                onClick={runRefresh}
                                disabled={busy}
                                className="text-[11px] text-indigo-600 hover:text-indigo-800 disabled:text-slate-400 font-bold"
                                data-testid="notification-refresh-btn"
                            >
                                {busy ? "..." : "تحديث"}
                            </button>
                            {data.alerts.some(a => a.status === "new") && (
                                <button
                                    onClick={markAllRead}
                                    className="text-[11px] text-slate-600 hover:text-slate-900 font-bold"
                                    data-testid="notification-mark-all-btn"
                                >
                                    تعليم الكل كمقروء
                                </button>
                            )}
                        </div>
                    </div>

                    <div className="max-h-[420px] overflow-y-auto">
                        {busy && data.alerts.length === 0 ? (
                            <div className="p-6 text-center text-sm text-slate-400">جاري التحميل...</div>
                        ) : data.alerts.length === 0 ? (
                            <div className="p-8 text-center" data-testid="notification-empty">
                                <div className="text-4xl mb-2">✨</div>
                                <div className="text-sm font-bold text-slate-700">لا توجد تنبيهات</div>
                                <div className="text-xs text-slate-400 mt-1">كل شيء على ما يرام!</div>
                                <button onClick={runRefresh} className="mt-3 text-xs text-indigo-600 hover:underline">إعادة الفحص الآن</button>
                            </div>
                        ) : (
                            data.alerts.map((a) => {
                                const sev = SEV_STYLE[a.severity] || SEV_STYLE.info;
                                const dot = SEV_DOT[a.severity] || SEV_DOT.info;
                                const isSnoozed = a.status === "snoozed";
                                return (
                                    <div
                                        key={a.id}
                                        className={`p-3 border-b border-slate-100 ${isSnoozed ? "opacity-60" : ""}`}
                                        data-testid={`notification-row-${a.id}`}
                                    >
                                        <div className="flex items-start gap-2">
                                            <span className={`mt-1.5 w-2 h-2 rounded-full ${dot} flex-shrink-0`}></span>
                                            <div className="flex-1 min-w-0">
                                                <div className={`text-xs font-extrabold ${sev.split(" ")[2]}`}>{a.title}</div>
                                                <div className="text-[11px] text-slate-600 mt-0.5 leading-snug">{a.message}</div>
                                                {isSnoozed && (
                                                    <div className="text-[10px] text-amber-700 mt-1 font-bold">⏰ مؤجَّل حتى {(a.snoozed_until || "").slice(0, 16).replace("T", " ")}</div>
                                                )}

                                                <div className="flex items-center gap-2 mt-2 flex-wrap">
                                                    {a.related_entity_url && (
                                                        <button onClick={() => goTo(a)} className="text-[10px] bg-indigo-50 text-indigo-700 hover:bg-indigo-100 px-2 py-0.5 rounded font-bold inline-flex items-center gap-1" data-testid={`notification-goto-${a.id}`}>
                                                            <ArrowSquareOut size={10} weight="bold" /> فتح
                                                        </button>
                                                    )}
                                                    {a.status === "new" && (
                                                        <button onClick={() => markRead(a)} className="text-[10px] bg-slate-100 text-slate-700 hover:bg-slate-200 px-2 py-0.5 rounded font-bold inline-flex items-center gap-1" data-testid={`notification-read-${a.id}`}>
                                                            <Check size={10} weight="bold" /> مقروء
                                                        </button>
                                                    )}
                                                    <div className="inline-flex items-center gap-1">
                                                        <Clock size={10} className="text-slate-400" />
                                                        <button onClick={() => snooze(a, 1)} className="text-[10px] text-slate-600 hover:text-slate-900 hover:underline" data-testid={`notification-snooze-1h-${a.id}`}>1س</button>
                                                        <span className="text-slate-300">·</span>
                                                        <button onClick={() => snooze(a, 24)} className="text-[10px] text-slate-600 hover:text-slate-900 hover:underline" data-testid={`notification-snooze-1d-${a.id}`}>1ي</button>
                                                        <span className="text-slate-300">·</span>
                                                        <button onClick={() => snooze(a, 24 * 7)} className="text-[10px] text-slate-600 hover:text-slate-900 hover:underline" data-testid={`notification-snooze-1w-${a.id}`}>1أ</button>
                                                    </div>
                                                    <button onClick={() => dismiss(a)} className="text-[10px] text-rose-600 hover:text-rose-800 hover:underline inline-flex items-center gap-0.5 ms-auto" data-testid={`notification-dismiss-${a.id}`} title="تجاهل نهائياً">
                                                        <Trash size={10} /> تجاهل
                                                    </button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>

                    {data.alerts.length > 0 && (
                        <div className="p-2 border-t border-slate-200 bg-slate-50 text-center">
                            <button
                                onClick={() => { setOpen(false); navigate("/alerts"); }}
                                className="text-xs text-indigo-700 hover:text-indigo-900 font-bold"
                                data-testid="notification-view-all"
                            >
                                عرض كل التنبيهات →
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
