/**
 * Iter-159h — Smart Settlement Alerts • Dedicated Page (/alerts)
 * --------------------------------------------------------------
 * Full alerts log with filters (status + severity + type) and bulk
 * actions.  Designed for power users who want to triage everything
 * outside the bell dropdown.
 */
import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { BellRinging, ArrowSquareOut, Check, Clock, Trash, ArrowsClockwise } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import { toast } from "sonner";


const SEV_BADGE = {
    critical: "bg-rose-100 text-rose-700 border-rose-300",
    warning: "bg-amber-100 text-amber-700 border-amber-300",
    info: "bg-sky-100 text-sky-700 border-sky-300",
};
const STATUS_LABEL = {
    new: "جديد",
    snoozed: "مؤجَّل",
    read: "مقروء",
    dismissed: "مرفوض",
};
const TYPE_LABEL = {
    overdue_bnpl: "تأخّر تسوية BNPL",
    amount_diff: "فرق في المبلغ",
    missing_salla: "فاتورة سلة مفقودة",
    high_courier_balance: "رصيد شحن مرتفع",
    unmatched_order: "طلب غير مطابق",
    high_ad_debt: "مديونية إعلانات مرتفعة",
    fulfillment_stop: "إيقاف تجهيز طلب",
};


export default function AlertsPage() {
    const [data, setData] = useState({ alerts: [], by_severity: {}, total: 0 });
    const [busy, setBusy] = useState(false);
    const [filter, setFilter] = useState({ status: "open", severity: "all", type: "all" });
    const navigate = useNavigate();

    const load = useCallback(async () => {
        setBusy(true);
        try {
            const params = new URLSearchParams();
            if (filter.status === "all") {
                // backend default returns new+snoozed; for "all" we ask explicitly per status
                params.set("limit", "300");
            } else if (filter.status === "open") {
                // backend default — leave empty
                params.set("limit", "300");
            } else {
                params.set("status", filter.status);
                params.set("limit", "300");
            }
            const { data } = await api.get(`/alerts?${params.toString()}`);
            setData(data);
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التحميل");
        } finally { setBusy(false); }
    }, [filter.status]);

    useEffect(() => { load(); }, [load]);

    const runRefresh = async () => {
        setBusy(true);
        try {
            const { data: r } = await api.post("/alerts/refresh");
            const total = Object.values(r.created || {}).reduce((s, n) => s + Math.max(n, 0), 0);
            toast.success(total > 0 ? `تم إنشاء/تحديث ${total} تنبيه` : "لا تنبيهات جديدة");
            await load();
        } catch (e) {
            toast.error(formatApiErrorDetail(e.response?.data?.detail) || "فشل التحديث");
        } finally { setBusy(false); }
    };

    const onAction = async (a, action, payload) => {
        try {
            await api.post(`/alerts/${a.id}/${action}`, payload || {});
            await load();
        } catch (e) {
            toast.error("فشل التنفيذ");
        }
    };

    const visible = data.alerts.filter((a) => {
        if (filter.severity !== "all" && a.severity !== filter.severity) return false;
        if (filter.type !== "all" && a.alert_type !== filter.type) return false;
        return true;
    });

    return (
        <div className="space-y-6" dir="rtl" data-testid="alerts-page">
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900 flex items-center gap-3">
                        <BellRinging size={28} weight="fill" className="text-rose-600" />
                        التنبيهات الذكية
                    </h1>
                    <p className="text-xs sm:text-sm text-slate-600 mt-1">
                        كل أحداث التسويات والمديونيات التي تحتاج انتباهك في مكان واحد.
                    </p>
                </div>
                <button
                    onClick={runRefresh}
                    disabled={busy}
                    className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm rounded-lg hover:bg-indigo-700 disabled:opacity-50 font-bold"
                    data-testid="alerts-page-refresh"
                >
                    <ArrowsClockwise size={16} weight="bold" /> إعادة الفحص الآن
                </button>
            </div>

            {/* Filters */}
            <div className="bg-white border border-slate-200 rounded-xl p-3 flex flex-wrap items-center gap-3 text-xs">
                <div className="flex items-center gap-1.5">
                    <label className="text-slate-600 font-bold">الحالة:</label>
                    <select
                        value={filter.status}
                        onChange={(e) => setFilter((f) => ({ ...f, status: e.target.value }))}
                        className="px-2 py-1 border border-slate-300 rounded text-xs"
                        data-testid="alerts-filter-status"
                    >
                        <option value="open">مفتوحة (جديد + مؤجَّل)</option>
                        <option value="new">جديدة</option>
                        <option value="snoozed">مؤجَّلة</option>
                        <option value="read">مقروءة</option>
                        <option value="dismissed">مرفوضة</option>
                    </select>
                </div>
                <div className="flex items-center gap-1.5">
                    <label className="text-slate-600 font-bold">الخطورة:</label>
                    <select
                        value={filter.severity}
                        onChange={(e) => setFilter((f) => ({ ...f, severity: e.target.value }))}
                        className="px-2 py-1 border border-slate-300 rounded text-xs"
                        data-testid="alerts-filter-severity"
                    >
                        <option value="all">الكل</option>
                        <option value="critical">حرج</option>
                        <option value="warning">تحذير</option>
                        <option value="info">معلومة</option>
                    </select>
                </div>
                <div className="flex items-center gap-1.5">
                    <label className="text-slate-600 font-bold">النوع:</label>
                    <select
                        value={filter.type}
                        onChange={(e) => setFilter((f) => ({ ...f, type: e.target.value }))}
                        className="px-2 py-1 border border-slate-300 rounded text-xs"
                        data-testid="alerts-filter-type"
                    >
                        <option value="all">كل الأنواع</option>
                        {Object.entries(TYPE_LABEL).map(([k, v]) => (
                            <option key={k} value={k}>{v}</option>
                        ))}
                    </select>
                </div>
                <div className="ms-auto text-slate-500">
                    {visible.length} / {data.total} تنبيه
                </div>
            </div>

            {/* List */}
            <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                {busy && visible.length === 0 ? (
                    <div className="p-8 text-center text-slate-400">جاري التحميل...</div>
                ) : visible.length === 0 ? (
                    <div className="p-12 text-center" data-testid="alerts-page-empty">
                        <div className="text-5xl mb-3">✨</div>
                        <div className="font-bold text-slate-800">لا توجد تنبيهات</div>
                        <div className="text-xs text-slate-500 mt-1">جرّب تغيير الفلتر أو اضغط «إعادة الفحص الآن».</div>
                    </div>
                ) : (
                    <ul className="divide-y divide-slate-100">
                        {visible.map((a) => (
                            <li key={a.id} className="p-4 hover:bg-slate-50 transition-colors" data-testid={`alerts-page-row-${a.id}`}>
                                <div className="flex items-start gap-3">
                                    <div className="flex-shrink-0">
                                        <span className={`inline-block px-2 py-0.5 text-[10px] font-bold rounded-full border ${SEV_BADGE[a.severity] || SEV_BADGE.info}`}>
                                            {a.severity === "critical" ? "حرج" : a.severity === "warning" ? "تحذير" : "معلومة"}
                                        </span>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-start justify-between gap-2">
                                            <div>
                                                <div className="text-sm font-extrabold text-slate-900">{a.title}</div>
                                                <div className="text-xs text-slate-600 mt-0.5">{a.message}</div>
                                                <div className="flex items-center gap-2 text-[10px] text-slate-400 mt-1">
                                                    <span>{TYPE_LABEL[a.alert_type] || a.alert_type}</span>
                                                    <span>·</span>
                                                    <span dir="ltr">{(a.created_at || "").slice(0, 16).replace("T", " ")}</span>
                                                    <span>·</span>
                                                    <span className={`font-bold ${a.status === "snoozed" ? "text-amber-600" : ""}`}>{STATUS_LABEL[a.status]}</span>
                                                </div>
                                            </div>
                                        </div>

                                        <div className="flex flex-wrap items-center gap-2 mt-3">
                                            {a.related_entity_url && (
                                                <button onClick={() => { onAction(a, "read"); navigate(a.related_entity_url); }} className="text-[11px] bg-indigo-50 text-indigo-700 hover:bg-indigo-100 px-2.5 py-1 rounded font-bold inline-flex items-center gap-1" data-testid={`alerts-page-goto-${a.id}`}>
                                                    <ArrowSquareOut size={11} weight="bold" /> فتح المعني
                                                </button>
                                            )}
                                            {a.status === "new" && (
                                                <button onClick={() => onAction(a, "read")} className="text-[11px] bg-slate-100 text-slate-700 hover:bg-slate-200 px-2.5 py-1 rounded font-bold inline-flex items-center gap-1" data-testid={`alerts-page-read-${a.id}`}>
                                                    <Check size={11} weight="bold" /> مقروء
                                                </button>
                                            )}
                                            {(a.status === "new" || a.status === "snoozed") && (
                                                <div className="inline-flex items-center gap-1 text-[11px] text-slate-600">
                                                    <Clock size={11} />
                                                    <span>تأجيل:</span>
                                                    <button onClick={() => onAction(a, "snooze", { hours: 1 })} className="hover:text-slate-900 hover:underline" data-testid={`alerts-page-snooze-1h-${a.id}`}>1س</button>
                                                    <span>·</span>
                                                    <button onClick={() => onAction(a, "snooze", { hours: 24 })} className="hover:text-slate-900 hover:underline" data-testid={`alerts-page-snooze-1d-${a.id}`}>1ي</button>
                                                    <span>·</span>
                                                    <button onClick={() => onAction(a, "snooze", { hours: 24 * 7 })} className="hover:text-slate-900 hover:underline" data-testid={`alerts-page-snooze-1w-${a.id}`}>1أ</button>
                                                </div>
                                            )}
                                            {a.status !== "dismissed" && (
                                                <button onClick={() => onAction(a, "dismiss")} className="text-[11px] text-rose-600 hover:text-rose-800 hover:underline inline-flex items-center gap-0.5 ms-auto" data-testid={`alerts-page-dismiss-${a.id}`}>
                                                    <Trash size={11} /> تجاهل نهائياً
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
        </div>
    );
}
