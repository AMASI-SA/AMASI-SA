import { CheckCircle, Clock, WarningCircle, XCircle } from "@phosphor-icons/react";
import ProviderMark from "./ProviderMark";

function formatDate(value) {
    if (!value) return "وقت غير معروف";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "وقت غير معروف";
    return new Intl.DateTimeFormat("ar-SA", {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

function Empty({ children }) {
    return (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center text-sm text-slate-500">
            {children}
        </div>
    );
}

function runLabel(item) {
    const labels = {
        local_connection_test: "اختبار اتصال محلي آمن",
        analytics_refresh: "مزامنة الحملات والمصروفات",
        analytics_refresh_async: "مهمة مزامنة Snapchat الخلفية",
        snapchat_account_selection: "تحديث الحسابات المحددة للمزامنة",
        tracking_diagnostics: "تشخيص Pixel",
        snapchat_oauth_discovery: "اكتشاف حسابات Snapchat",
    };
    return labels[item.run_type]
        || item.run_type
        || item.kind
        || item.check_type
        || "فحص التكامل";
}

function runStatus(item) {
    const status = String(item.status || "unknown").toLowerCase();
    if (["passed", "success", "succeeded", "healthy", "completed", "complete"].includes(status)) {
        return {
            Icon: CheckCircle,
            label: "مكتمل",
            className: "text-emerald-600",
        };
    }
    if (status === "partial") {
        return {
            Icon: WarningCircle,
            label: "جزئي",
            className: "text-amber-600",
        };
    }
    if (["failed", "error", "unhealthy"].includes(status)) {
        return {
            Icon: XCircle,
            label: "فشل",
            className: "text-rose-600",
        };
    }
    return {
        Icon: Clock,
        label: status === "running" ? "قيد التشغيل" : status === "queued" ? "في الانتظار" : status,
        className: "text-slate-500",
    };
}

function RunItem({ item }) {
    const status = runStatus(item);
    const Icon = status.Icon;
    const summary = typeof item.summary === "string"
        ? item.summary
        : item.summary?.message || item.message;
    return (
        <div className="flex items-start gap-3 rounded-xl border border-slate-100 p-3">
            <ProviderMark provider={item.provider} size="sm" />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-bold text-slate-800">
                        {runLabel(item)}
                    </div>
                    <span className={`inline-flex items-center gap-1 text-xs font-bold ${status.className}`}>
                        <Icon size={15} weight="fill" />
                        {status.label}
                    </span>
                </div>
                <div className="mt-1 text-xs text-slate-400">
                    {formatDate(item.finished_at || item.started_at || item.checked_at)}
                </div>
                {summary && (
                    <div className="mt-2 text-xs leading-5 text-slate-600">
                        {summary}
                    </div>
                )}
            </div>
        </div>
    );
}

function isTrackingDiagnostic(item) {
    return String(item?.code || "").toLowerCase().startsWith("snapchat_tracking_");
}

function trackingMessage(item) {
    const code = String(item?.code || "").toLowerCase();
    if (code === "snapchat_tracking_diagnostics_partial") {
        return "اكتملت مزامنة الحملات والمصروفات، لكن بعض نقاط تشخيص Pixel غير متاحة. لم يحوّل ميزان القيم غير المعروفة إلى صفر.";
    }
    if (code === "snapchat_tracking_http_400") {
        return "تعذر فحص إحدى نقاط Pixel لهذا الحساب. لا يؤثر ذلك في مزامنة الحملات أو المصروفات.";
    }
    return "ملاحظة محدودة في تشخيص Pixel، وهي مستقلة عن مزامنة الحملات والمصروفات.";
}

function ErrorItem({ item }) {
    const diagnostic = isTrackingDiagnostic(item);
    const palette = diagnostic
        ? {
            wrapper: "border-amber-200 bg-amber-50/70",
            icon: "text-amber-600",
            provider: "text-amber-900",
            code: "text-amber-700",
            message: "text-amber-900",
            date: "text-amber-500",
        }
        : {
            wrapper: "border-rose-100 bg-rose-50/50",
            icon: "text-rose-600",
            provider: "text-rose-800",
            code: "text-rose-600",
            message: "text-rose-800",
            date: "text-rose-400",
        };
    return (
        <div
            className={`flex items-start gap-3 rounded-xl border p-3 ${palette.wrapper}`}
            data-testid={diagnostic ? "tracking-diagnostic-notice" : "integration-error-item"}
        >
            <WarningCircle size={22} className={`mt-1 shrink-0 ${palette.icon}`} weight="fill" />
            <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                    <span className={`font-mono text-xs font-extrabold ${palette.provider}`}>
                        {item.provider || "integration"}
                    </span>
                    <span className={`rounded-full bg-white px-2 py-0.5 font-mono text-[10px] ${palette.code}`}>
                        {diagnostic ? "ملاحظة تشخيص Pixel" : item.code || item.category || "integration_error"}
                    </span>
                </div>
                <div className={`mt-1 break-words text-xs leading-5 ${palette.message}`}>
                    {diagnostic
                        ? trackingMessage(item)
                        : item.safe_message || item.message || "تم تسجيل خطأ دون تفاصيل حساسة."}
                </div>
                {diagnostic && (
                    <div className="mt-1 font-mono text-[10px] text-amber-700">
                        {item.code}
                    </div>
                )}
                <div className={`mt-1 text-[11px] ${palette.date}`}>
                    {formatDate(item.occurred_at || item.last_seen_at)}
                </div>
            </div>
        </div>
    );
}

export default function IntegrationActivityPanel({ runs, errors, loading = false }) {
    if (loading) {
        return (
            <div className="grid gap-4 lg:grid-cols-2">
                {[0, 1].map((key) => (
                    <div key={key} className="h-56 animate-pulse rounded-xl border border-slate-100 bg-white" />
                ))}
            </div>
        );
    }

    return (
        <div className="grid gap-4 lg:grid-cols-2" data-testid="integration-activity-panel">
            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <h2 className="mb-4 text-base font-extrabold text-slate-900">سجل المزامنة والفحوصات</h2>
                <div className="space-y-2">
                    {(runs || []).length
                        ? runs.slice(0, 20).map((item, index) => (
                            <RunItem key={item.run_id || `${item.provider}-${index}`} item={item} />
                        ))
                        : <Empty>لم تُسجل فحوصات V2 بعد.</Empty>}
                </div>
            </section>
            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <h2 className="mb-1 text-base font-extrabold text-slate-900">سجل الملاحظات والأخطاء الآمن</h2>
                <p className="mb-4 text-xs leading-5 text-slate-500">
                    ملاحظات تشخيص Pixel تظهر باللون الأصفر، بينما تبقى أخطاء التشغيل الفعلية باللون الأحمر.
                </p>
                <div className="space-y-2">
                    {(errors || []).length
                        ? errors.slice(0, 20).map((item, index) => (
                            <ErrorItem key={item.error_id || `${item.provider}-${index}`} item={item} />
                        ))
                        : <Empty>لا توجد أخطاء أو ملاحظات مسجلة في الطبقة الجديدة.</Empty>}
                </div>
            </section>
        </div>
    );
}
