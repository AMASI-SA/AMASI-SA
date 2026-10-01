import {
    CheckCircle,
    ClipboardText,
    LockKey,
    ShieldCheck,
    UserGear,
    WarningCircle,
} from "@phosphor-icons/react";
import { AccountingPageHeader, AccountingSkeleton, formatAccountingMoney } from "./AccountingUI";

export function formatMoney(value) {
    return formatAccountingMoney(value);
}

export function LoadingBlock({ label = "جاري تحميل المحاسبة…" }) {
    return <AccountingSkeleton label={label} />;
}

export function AccessDenied({ page }) {
    return (
        <div className="mx-auto max-w-2xl rounded-2xl border border-amber-200 bg-amber-50 p-8 text-center" data-testid="accounting-permission-denied">
            <LockKey size={42} weight="duotone" className="mx-auto text-amber-700" />
            <h1 className="mt-3 text-xl font-black text-amber-950">لا تملك صلاحية {page.label}</h1>
            <p className="mt-2 text-sm font-semibold leading-6 text-amber-800">
                صفحات المحاسبة وصلاحيات الترحيل مستقلة ومغلقة افتراضيًا. اطلب من مالك ميزان منحك الصفحة أو الإجراء المطلوب فقط.
            </p>
        </div>
    );
}

export function AccountingHeader({ page, canManagePermissions, onOpenPermissions }) {
    return <div className="space-y-3" data-testid="accounting-module-header">
        <AccountingPageHeader level={1} title={page.label} description="سجّل العملية الفعلية وراجع دليلها. الحفظ والمراجعة والترحيل مراحل مستقلة حسب صلاحيتك.">
            {canManagePermissions && <button type="button" onClick={onOpenPermissions} className="inline-flex min-h-11 items-center gap-2 rounded-xl border border-slate-200 px-4 text-sm font-semibold text-slate-700" data-testid="accounting-open-permissions"><UserGear size={20} /> صلاحيات المحاسبة</button>}
        </AccountingPageHeader>
        <p className="text-xs leading-6 text-slate-500">لا يُعرض أو يُرحّل أي رصيد افتتاحي قبل اعتماد توقيت القطع وورقة الأدلة والمعاينة المتوازنة.</p>
    </div>;
}

export function SummaryCard({ label, value, hint, Icon, tone = "slate", testid }) {
    const tones = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-800",
        sky: "border-sky-200 bg-sky-50 text-sky-800",
        amber: "border-amber-200 bg-amber-50 text-amber-800",
        rose: "border-rose-200 bg-rose-50 text-rose-800",
        slate: "border-slate-200 bg-slate-50 text-slate-800",
    };
    return (
        <article className={`rounded-2xl border p-4 ${tones[tone]}`} data-testid={testid}>
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="text-xs font-extrabold opacity-80">{label}</div>
                    <div className="mt-2 break-words font-mono text-xl font-bold sm:text-2xl" dir="ltr">{value}</div>
                </div>
                <span className="rounded-xl bg-white/70 p-2"><Icon size={23} weight="duotone" /></span>
            </div>
            <p className="mt-2 text-[11px] font-semibold leading-5 opacity-75">{hint}</p>
        </article>
    );
}

export function ReadinessPanel({ status }) {
    const checks = status?.readiness || [];
    const complete = checks.filter((item) => item.complete).length;
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="accounting-readiness">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h2 className="text-lg font-black text-slate-950">جاهزية المركز المالي</h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">لا تتحول الحالة إلى جاهز من بيانات ناقصة أو من ميزان القديم.</p>
                </div>
                <span className={`rounded-full border px-3 py-1 text-xs font-extrabold ${status?.cutover?.safe_active ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
                    {status?.cutover?.safe_active ? "القطع موثق ونشط" : `${complete}/${checks.length} مكتمل`}
                </span>
            </div>
            {status?.cutover?.unsafe_activation_detected && (
                <div className="mt-4 flex gap-2 rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs font-extrabold leading-5 text-rose-900">
                    <WarningCircle size={20} weight="fill" className="shrink-0" />
                    توجد إشارة تفعيل قطع، لكن الأدلة أو المعاينة أو الاعتماد غير مكتملة. أرقام الأرصدة محجوبة احترازيًا.
                </div>
            )}
            <div className="mt-4 grid gap-2 md:grid-cols-2">
                {checks.map((item) => (
                    <div key={item.id} className={`flex gap-3 rounded-xl border p-3 ${item.complete ? "border-emerald-100 bg-emerald-50" : "border-slate-200 bg-slate-50"}`}>
                        {item.complete
                            ? <CheckCircle size={20} weight="fill" className="shrink-0 text-emerald-700" />
                            : <WarningCircle size={20} weight="duotone" className="shrink-0 text-amber-700" />}
                        <div>
                            <div className="text-xs font-extrabold text-slate-900">{item.label}</div>
                            <div className="mt-1 text-[11px] font-semibold leading-5 text-slate-500">{item.detail}</div>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}

export function ImplementationNotice({ page }) {
    const statusLabels = {
        implemented: "منفذة",
        partial_existing_workflows: "منفذة جزئيًا",
        blocked_not_implemented: "محجوبة حتى اكتمال القطع",
    };
    return (
        <div className="flex items-start gap-3 rounded-2xl border border-sky-200 bg-sky-50 p-4 text-sky-950" data-testid={`accounting-partial-${page.id}`}>
            <ShieldCheck size={24} weight="duotone" className="shrink-0 text-sky-700" />
            <div>
                <div className="text-sm font-black">حالة التنفيذ: {statusLabels[page.implementationStatus] || page.implementationStatus}</div>
                <p className="mt-1 text-xs font-semibold leading-5 text-sky-800">
                    جُمّع الوصول تحت «المحاسبة»، لكن هذه الصفحة لم تكتمل بعد وفق الواجهة المعتمدة. القدرات الحالية لا تعني اكتمال مسار المسودة والمراجعة والترحيل.
                </p>
            </div>
        </div>
    );
}

export { ClipboardText };
