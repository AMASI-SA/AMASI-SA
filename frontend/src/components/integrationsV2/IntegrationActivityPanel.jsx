import { CheckCircle, Clock, WarningCircle } from "@phosphor-icons/react";
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

function RunItem({ item }) {
    const ok = ["passed", "success", "succeeded", "healthy", "completed"].includes(item.status);
    const Icon = ok ? CheckCircle : Clock;
    const runLabel = item.run_type === "local_connection_test"
        ? "اختبار اتصال محلي آمن"
        : item.run_type || item.kind || item.check_type || "فحص التكامل";
    const summary = typeof item.summary === "string"
        ? item.summary
        : item.summary?.message || item.message;
    return (
        <div className="flex items-start gap-3 rounded-xl border border-slate-100 p-3">
            <ProviderMark provider={item.provider} size="sm" />
            <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-bold text-slate-800">
                        {runLabel}
                    </div>
                    <span className={`inline-flex items-center gap-1 text-xs font-bold ${ok ? "text-emerald-600" : "text-slate-500"}`}>
                        <Icon size={15} weight="fill" />
                        {item.status || "unknown"}
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

function ErrorItem({ item }) {
    return (
        <div className="flex items-start gap-3 rounded-xl border border-rose-100 bg-rose-50/50 p-3">
            <WarningCircle size={22} className="mt-1 shrink-0 text-rose-600" weight="fill" />
            <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs font-extrabold text-rose-800">
                        {item.provider || "integration"}
                    </span>
                    <span className="rounded-full bg-white px-2 py-0.5 font-mono text-[10px] text-rose-600">
                        {item.code || item.category || "integration_error"}
                    </span>
                </div>
                <div className="mt-1 break-words text-xs leading-5 text-rose-800">
                    {item.safe_message || item.message || "تم تسجيل خطأ دون تفاصيل حساسة."}
                </div>
                <div className="mt-1 text-[11px] text-rose-400">
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
                <h2 className="mb-4 text-base font-extrabold text-slate-900">سجل الأخطاء الآمن</h2>
                <div className="space-y-2">
                    {(errors || []).length
                        ? errors.slice(0, 20).map((item, index) => (
                            <ErrorItem key={item.error_id || `${item.provider}-${index}`} item={item} />
                        ))
                        : <Empty>لا توجد أخطاء مسجلة في الطبقة الجديدة.</Empty>}
                </div>
            </section>
        </div>
    );
}
