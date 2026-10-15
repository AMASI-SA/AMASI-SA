import {
    CheckCircle,
    Eye,
    LockKey,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";

const STATUS_STYLES = {
    proposed: "border-amber-200 bg-amber-50 text-amber-800",
    suggested_preview: "border-amber-200 bg-amber-50 text-amber-800",
    detected: "border-violet-200 bg-violet-50 text-violet-800",
    collecting: "border-blue-200 bg-blue-50 text-blue-800",
    suggested: "border-violet-200 bg-violet-50 text-violet-800",
    potential: "border-amber-200 bg-amber-50 text-amber-800",
    mentioned_once: "border-slate-200 bg-slate-50 text-slate-700",
    potential_repeated: "border-amber-200 bg-amber-50 text-amber-800",
    review_required: "border-rose-200 bg-rose-50 text-rose-800",
    qualified: "border-emerald-200 bg-emerald-50 text-emerald-800",
    needs_information: "border-blue-200 bg-blue-50 text-blue-800",
    needs_review: "border-amber-200 bg-amber-50 text-amber-800",
    needs_reply: "border-amber-200 bg-amber-50 text-amber-800",
    follow_up: "border-blue-200 bg-blue-50 text-blue-800",
    human_review: "border-rose-200 bg-rose-50 text-rose-800",
    resolved: "border-emerald-200 bg-emerald-50 text-emerald-800",
    blocked: "border-rose-200 bg-rose-50 text-rose-800",
    draft_preview: "border-slate-200 bg-slate-50 text-slate-700",
    preview_approved: "border-emerald-200 bg-emerald-50 text-emerald-800",
    demo_approved: "border-emerald-200 bg-emerald-50 text-emerald-800",
    approved_preview: "border-emerald-200 bg-emerald-50 text-emerald-800",
    mock_provider: "border-violet-200 bg-violet-50 text-violet-800",
    not_connected_here: "border-slate-200 bg-slate-50 text-slate-700",
    simulation: "border-violet-200 bg-violet-50 text-violet-800",
    preview_only: "border-violet-200 bg-violet-50 text-violet-800",
    preview_not_contactable: "border-rose-200 bg-rose-50 text-rose-800",
    needs_human_review: "border-amber-200 bg-amber-50 text-amber-800",
    open: "border-emerald-200 bg-emerald-50 text-emerald-800",
    needs_human: "border-amber-200 bg-amber-50 text-amber-800",
    follow_up_due: "border-blue-200 bg-blue-50 text-blue-800",
    closed: "border-slate-200 bg-slate-50 text-slate-700",
};

const STATUS_LABELS = {
    proposed: "مقترحة",
    suggested_preview: "متابعة مقترحة",
    detected: "مكتشفة",
    collecting: "قيد التجميع",
    suggested: "مقترحة",
    potential: "منافس محتمل",
    mentioned_once: "ذُكر مرة",
    potential_repeated: "منافس محتمل متكرر",
    review_required: "يتطلب مراجعة",
    qualified: "مؤهلة",
    needs_information: "تحتاج معلومات",
    needs_review: "بانتظار المراجعة",
    needs_reply: "تحتاج ردًا",
    follow_up: "متابعة",
    human_review: "مراجعة بشرية",
    resolved: "مغلقة",
    blocked: "متوقفة",
    draft_preview: "مسودة وهمية",
    preview_approved: "معتمد للمعاينة",
    demo_approved: "معتمد تجريبيًا",
    approved_preview: "معرفة معتمدة تجريبيًا",
    mock_provider: "مزود وهمي",
    not_connected_here: "غير متصل للتنفيذ",
    simulation: "محاكاة",
    preview_only: "بيانات معاينة",
    preview_not_contactable: "غير قابل للتواصل",
    needs_human_review: "تحتاج مراجعة بشرية",
    open: "مفتوحة",
    needs_human: "تحتاج موظفًا",
    follow_up_due: "متابعة مستحقة",
    closed: "مغلقة",
};

const METRIC_TONES = {
    emerald: "border-emerald-100 bg-emerald-50 text-emerald-800",
    amber: "border-amber-100 bg-amber-50 text-amber-800",
    violet: "border-violet-100 bg-violet-50 text-violet-800",
    blue: "border-blue-100 bg-blue-50 text-blue-800",
    rose: "border-rose-100 bg-rose-50 text-rose-800",
    slate: "border-slate-200 bg-slate-50 text-slate-700",
};

export function PreviewModeBanner({ compact = false }) {
    return (
        <div
            className={`flex items-start gap-3 rounded-xl border border-violet-200 bg-violet-50 text-violet-950 ${compact ? "p-3" : "p-4"}`}
            data-testid="customer-intelligence-preview-banner"
        >
            <Eye size={compact ? 20 : 24} weight="duotone" className="mt-0.5 shrink-0 text-violet-700" />
            <div className="min-w-0">
                <div className="font-extrabold">معاينة المالك · بيانات مصطنعة فقط</div>
                <p className="mt-1 text-xs leading-5 text-violet-800">
                    لا تمثل هذه الأسماء أو الرسائل أو النتائج عملاء حقيقيين، ولا ينفّذ المركز
                    أي إجراء خارجي.
                </p>
            </div>
        </div>
    );
}

export function WriteLockBanner({ locked }) {
    return (
        <div
            className={`flex items-start gap-3 rounded-xl border p-4 ${
                locked
                    ? "border-emerald-200 bg-emerald-50 text-emerald-950"
                    : "border-rose-300 bg-rose-50 text-rose-950"
            }`}
            data-testid="customer-intelligence-write-lock"
        >
            {locked ? (
                <LockKey size={23} weight="duotone" className="mt-0.5 shrink-0 text-emerald-700" />
            ) : (
                <WarningCircle size={23} weight="fill" className="mt-0.5 shrink-0 text-rose-700" />
            )}
            <div>
                <div className="font-extrabold">
                    {locked ? "كل الكتابات والإرسال مقفلة" : "تحذير: عقد الأمان غير متوافق"}
                </div>
                <p className="mt-1 text-xs leading-5">
                    {locked
                        ? "لا واتساب، لا إنشاء طلب أو خصم أو رابط دفع، ولا تعديل منتج أو حملة. المعروض اقتراحات وتمثيلات واجهة فقط."
                        : "لن تظهر إجراءات تنفيذ حتى تعود السياسة إلى observe_only وتصبح جميع صلاحيات الكتابة false."}
                </p>
            </div>
        </div>
    );
}

export function MetricCard({ metric }) {
    const tone = METRIC_TONES[metric?.tone] || METRIC_TONES.slate;
    const value = metric?.value ?? "—";
    return (
        <article
            className={`rounded-xl border p-4 ${tone}`}
            data-testid={`customer-intelligence-metric-${metric?.key || "unknown"}`}
        >
            <div className="text-xs font-extrabold opacity-80">{metric?.label || "مؤشر"}</div>
            <div className="mt-2 font-mono text-3xl font-black">{value}</div>
            <div className="mt-2 text-[11px] font-semibold leading-5 opacity-75">
                {metric?.hint || "لا توجد تفاصيل"}
            </div>
        </article>
    );
}

export function Panel({
    title,
    subtitle,
    Icon = ShieldCheck,
    children,
    testid,
    actions,
}) {
    return (
        <section
            className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
            data-testid={testid}
        >
            <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 bg-slate-50 px-4 py-4 sm:px-5">
                <div className="flex min-w-0 items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-violet-100 text-violet-700">
                        <Icon size={21} weight="duotone" />
                    </span>
                    <div className="min-w-0">
                        <h2 className="font-black text-slate-950">{title}</h2>
                        {subtitle && (
                            <p className="mt-1 text-xs leading-5 text-slate-500">{subtitle}</p>
                        )}
                    </div>
                </div>
                {actions}
            </header>
            <div className="p-4 sm:p-5">{children}</div>
        </section>
    );
}

export function StatusPill({ status, label }) {
    const tone = STATUS_STYLES[status] || STATUS_STYLES.preview_only;
    return (
        <span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-extrabold ${tone}`}>
            {label || STATUS_LABELS[status] || status || "غير محدد"}
        </span>
    );
}

export function Confidence({ value, label = "درجة الثقة" }) {
    const numeric = Number(value);
    const valid = Number.isFinite(numeric);
    const percent = valid ? Math.max(0, Math.min(100, Math.round(numeric * 100))) : 0;
    return (
        <div data-testid="customer-intelligence-confidence">
            <div className="mb-1 flex items-center justify-between gap-2 text-[11px] font-bold text-slate-500">
                <span>{label}</span>
                <span className="num">{valid ? `${percent}%` : "غير متاحة"}</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                <div
                    className="h-full rounded-full bg-violet-600"
                    style={{ width: `${percent}%` }}
                    aria-hidden="true"
                />
            </div>
        </div>
    );
}

export function EmptyState({ title = "لا توجد بيانات", detail }) {
    return (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
            <CheckCircle size={36} weight="duotone" className="mx-auto text-slate-400" />
            <div className="mt-3 font-extrabold text-slate-700">{title}</div>
            {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
        </div>
    );
}

export function SafetyChecklist({ policy, keys }) {
    return (
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            {keys.map((key) => {
                const locked = policy?.[key] === false;
                return (
                    <div
                        key={key}
                        className={`flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold ${
                            locked
                                ? "border-emerald-100 bg-emerald-50 text-emerald-800"
                                : "border-rose-200 bg-rose-50 text-rose-800"
                        }`}
                    >
                        {locked ? (
                            <LockKey size={16} weight="duotone" />
                        ) : (
                            <WarningCircle size={16} weight="fill" />
                        )}
                        <span className="truncate" dir="ltr">{key}</span>
                    </div>
                );
            })}
        </div>
    );
}
