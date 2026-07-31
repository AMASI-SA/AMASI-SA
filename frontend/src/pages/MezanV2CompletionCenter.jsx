import { useMemo, useState } from "react";
import {
    ArrowLeft,
    ArrowsClockwise,
    CheckCircle,
    Circle,
    ClockCountdown,
    GitBranch,
    Hourglass,
    ListChecks,
    LockKey,
    RoadHorizon,
    ShieldCheck,
    Sparkle,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    COMPLETION_RULES,
    LEGACY_MIGRATION_GROUPS,
    MEZAN_V2_WORKSTREAMS,
    NEXT_RECOMMENDED_STEP,
    PARALLEL_WORKSTREAMS,
    PLAN_LAST_REVIEWED_AT,
    STATUS_META,
    getCompletionSummary,
} from "../data/mezanV2CompletionPlan";

const STATUS_STYLES = {
    completed: {
        badge: "border-emerald-200 bg-emerald-50 text-emerald-800",
        icon: "text-emerald-600",
        row: "border-emerald-100 bg-emerald-50/45",
        Icon: CheckCircle,
    },
    in_progress: {
        badge: "border-amber-200 bg-amber-50 text-amber-800",
        icon: "text-amber-600",
        row: "border-amber-100 bg-amber-50/45",
        Icon: ArrowsClockwise,
    },
    pending: {
        badge: "border-slate-200 bg-slate-50 text-slate-700",
        icon: "text-slate-400",
        row: "border-slate-200 bg-white",
        Icon: Circle,
    },
    waiting: {
        badge: "border-rose-200 bg-rose-50 text-rose-800",
        icon: "text-rose-600",
        row: "border-rose-100 bg-rose-50/45",
        Icon: Hourglass,
    },
    deferred: {
        badge: "border-violet-200 bg-violet-50 text-violet-800",
        icon: "text-violet-600",
        row: "border-violet-100 bg-violet-50/45",
        Icon: ClockCountdown,
    },
};

const MIGRATION_META = {
    redirected: {
        label: "تم الدمج والتحويل",
        className: "border-emerald-200 bg-emerald-50 text-emerald-800",
    },
    merge_remaining: {
        label: "قدرات متبقية للنقل",
        className: "border-amber-200 bg-amber-50 text-amber-800",
    },
    keep_now: {
        label: "يبقى حاليًا",
        className: "border-sky-200 bg-sky-50 text-sky-800",
    },
    embed_later: {
        label: "يُدمج لاحقًا داخل مالكه",
        className: "border-violet-200 bg-violet-50 text-violet-800",
    },
};

const TABS = [
    { id: "core", label: "نواة ميزان 2", Icon: ListChecks },
    { id: "future", label: "بعد اكتمال النواة", Icon: Sparkle },
    { id: "migration", label: "نقل صفحات ميزان", Icon: ArrowLeft },
    { id: "parallel", label: "العمل المتوازي", Icon: GitBranch },
];

function StatusBadge({ status }) {
    const meta = STATUS_META[status];
    const style = STATUS_STYLES[status];
    const Icon = style.Icon;
    return (
        <span className={`inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-extrabold ${style.badge}`}>
            <Icon size={15} weight={status === "completed" ? "fill" : "bold"} />
            {meta.shortLabel}
        </span>
    );
}

function SummaryCard({ label, value, tone, hint }) {
    const tones = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        slate: "border-slate-200 bg-slate-50 text-slate-900",
        rose: "border-rose-200 bg-rose-50 text-rose-900",
    };
    return (
        <div className={`rounded-2xl border p-4 ${tones[tone]}`}>
            <div className="text-3xl font-black tabular-nums">{value}</div>
            <div className="mt-1 text-sm font-extrabold">{label}</div>
            {hint && <div className="mt-1 text-xs font-semibold opacity-70">{hint}</div>}
        </div>
    );
}

function WorkstreamCard({ workstream, statusFilter }) {
    const visibleTasks = statusFilter === "all"
        ? workstream.tasks
        : workstream.tasks.filter((task) => task.status === statusFilter);
    if (!visibleTasks.length) return null;
    const completed = workstream.tasks.filter((task) => task.status === "completed").length;
    const applicable = workstream.tasks.filter((task) => task.status !== "deferred").length;
    const percent = applicable ? Math.round((completed / applicable) * 100) : 0;
    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid={`completion-workstream-${workstream.id}`}>
            <div className="border-b border-slate-100 bg-slate-50/70 p-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                        <h2 className="text-lg font-black text-slate-950">{workstream.title}</h2>
                        <p className="mt-1 text-sm font-semibold leading-6 text-slate-600">{workstream.description}</p>
                    </div>
                    <div className="shrink-0 text-start sm:text-end">
                        <div className="text-2xl font-black text-violet-800 tabular-nums">{percent}%</div>
                        <div className="text-xs font-bold text-slate-500">{completed} من {applicable} مكتملة</div>
                    </div>
                </div>
                <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-200" aria-label={`نسبة إنجاز ${workstream.title}`}>
                    <div className="h-full rounded-full bg-emerald-500 transition-all" style={{ width: `${percent}%` }} />
                </div>
            </div>
            <div className="space-y-2 p-4 sm:p-5">
                {visibleTasks.map((task) => {
                    const style = STATUS_STYLES[task.status];
                    const Icon = style.Icon;
                    return (
                        <article key={task.id} className={`rounded-xl border p-3.5 ${style.row}`} data-testid={`completion-task-${task.id}`}>
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                <div className="flex min-w-0 gap-3">
                                    <Icon className={`mt-0.5 shrink-0 ${style.icon}`} size={22} weight={task.status === "completed" ? "fill" : "bold"} />
                                    <div>
                                        <div className="flex flex-wrap items-center gap-2">
                                            <h3 className="font-extrabold leading-6 text-slate-900">{task.title}</h3>
                                            {task.next && (
                                                <span className="rounded-full bg-violet-700 px-2 py-0.5 text-[11px] font-black text-white">الخطوة التالية</span>
                                            )}
                                        </div>
                                        {task.evidence && <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">الدليل: {task.evidence}</p>}
                                    </div>
                                </div>
                                <StatusBadge status={task.status} />
                            </div>
                        </article>
                    );
                })}
            </div>
        </section>
    );
}

function RoadmapView({ core }) {
    const [statusFilter, setStatusFilter] = useState("all");
    const workstreams = MEZAN_V2_WORKSTREAMS.filter((workstream) => workstream.core === core);
    const filters = ["all", "completed", "in_progress", "pending", "waiting", "deferred"];
    return (
        <div className="space-y-5">
            <div className="flex flex-wrap gap-2" aria-label="تصفية حالة المهام">
                {filters.map((status) => {
                    const label = status === "all" ? "الكل" : STATUS_META[status].shortLabel;
                    const active = statusFilter === status;
                    return (
                        <button
                            key={status}
                            type="button"
                            onClick={() => setStatusFilter(status)}
                            className={`rounded-full border px-3 py-1.5 text-xs font-extrabold transition ${active ? "border-violet-700 bg-violet-700 text-white" : "border-slate-200 bg-white text-slate-700 hover:border-violet-300 hover:bg-violet-50"}`}
                        >
                            {label}
                        </button>
                    );
                })}
            </div>
            {workstreams.map((workstream) => (
                <WorkstreamCard key={workstream.id} workstream={workstream} statusFilter={statusFilter} />
            ))}
        </div>
    );
}

function MigrationView() {
    return (
        <div className="space-y-4" data-testid="legacy-migration-register">
            <div className="rounded-2xl border border-sky-200 bg-sky-50 p-5">
                <div className="flex gap-3">
                    <RoadHorizon className="mt-0.5 shrink-0 text-sky-700" size={24} weight="duotone" />
                    <div>
                        <h2 className="font-black text-sky-950">قاعدة النقل</h2>
                        <p className="mt-1 text-sm font-semibold leading-6 text-sky-900">ننقل القدرة وليس شكل الصفحة. وبعد تكافؤ الوظيفة والصلاحيات واختبار الإنتاج نضع تحويلًا للمسار القديم، ثم نحذفه فقط عندما يثبت أن استخدامه أصبح صفرًا.</p>
                    </div>
                </div>
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                {LEGACY_MIGRATION_GROUPS.map((item) => {
                    const decision = MIGRATION_META[item.decision];
                    return (
                        <article key={item.id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid={`migration-item-${item.id}`}>
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                                <div>
                                    <h3 className="font-black text-slate-950">{item.sourceLabel}</h3>
                                    <code className="mt-1 block whitespace-normal break-words text-xs font-bold text-slate-500">{item.source}</code>
                                </div>
                                <span className={`inline-flex w-fit shrink-0 rounded-full border px-2.5 py-1 text-xs font-extrabold ${decision.className}`}>{decision.label}</span>
                            </div>
                            <div className="mt-4 rounded-xl bg-violet-50 p-3">
                                <div className="text-xs font-black text-violet-700">الوجهة</div>
                                <div className="mt-1 text-sm font-extrabold text-violet-950">{item.destination}</div>
                            </div>
                            <dl className="mt-4 space-y-3 text-sm leading-6">
                                <div>
                                    <dt className="font-black text-slate-800">ما الذي يُنقل؟</dt>
                                    <dd className="font-semibold text-slate-600">{item.move}</dd>
                                </div>
                                <div>
                                    <dt className="font-black text-slate-800">متى نغلق القديم؟</dt>
                                    <dd className="font-semibold text-slate-600">{item.retireWhen}</dd>
                                </div>
                            </dl>
                        </article>
                    );
                })}
            </div>
        </div>
    );
}

function ParallelView() {
    const ready = PARALLEL_WORKSTREAMS.filter((item) => item.canStart);
    const later = PARALLEL_WORKSTREAMS.filter((item) => !item.canStart);
    return (
        <div className="space-y-5" data-testid="parallel-workstreams">
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5">
                <div className="flex gap-3">
                    <WarningCircle className="mt-0.5 shrink-0 text-amber-700" size={24} weight="fill" />
                    <div>
                        <h2 className="font-black text-amber-950">قواعد العمل بأكثر من محادثة</h2>
                        <p className="mt-1 text-sm font-semibold leading-6 text-amber-900">كل محادثة لها فرع وملكية ملفات واضحة. لا تعمل محادثتان على App.js أو Layout أو Sidebar في الوقت نفسه، ولا يدمج أي فرع قبل تحديثه من main ونجاح اختباراته.</p>
                    </div>
                </div>
            </div>
            <div>
                <h2 className="mb-3 text-lg font-black text-slate-950">يمكن أن تبدأ الآن — {ready.length} مسارات</h2>
                <div className="grid gap-4 xl:grid-cols-2">
                    {ready.map((item) => <ParallelCard key={item.id} item={item} />)}
                </div>
            </div>
            {later.length > 0 && (
                <div>
                    <h2 className="mb-3 text-lg font-black text-slate-950">بعد تثبيت النواة</h2>
                    <div className="grid gap-4 xl:grid-cols-2">
                        {later.map((item) => <ParallelCard key={item.id} item={item} />)}
                    </div>
                </div>
            )}
        </div>
    );
}

function ParallelCard({ item }) {
    return (
        <article className={`rounded-2xl border bg-white p-5 shadow-sm ${item.canStart ? "border-emerald-200" : "border-violet-200"}`} data-testid={`parallel-item-${item.id}`}>
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className={`text-xs font-black ${item.canStart ? "text-emerald-700" : "text-violet-700"}`}>{item.canStart ? `المسار ${item.rank}` : "بعد النواة"}</div>
                    <h3 className="mt-1 font-black text-slate-950">{item.title}</h3>
                </div>
                {item.canStart ? <CheckCircle className="shrink-0 text-emerald-600" size={24} weight="fill" /> : <ClockCountdown className="shrink-0 text-violet-600" size={24} weight="duotone" />}
            </div>
            <p className="mt-3 text-sm font-semibold leading-6 text-slate-700">{item.scope}</p>
            <div className="mt-4 rounded-xl bg-slate-950 p-3 text-start" dir="ltr">
                <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400">branch</div>
                <code className="mt-1 block break-all text-xs font-bold text-emerald-300">{item.branch}</code>
            </div>
            <div className="mt-4 space-y-3 text-sm leading-6">
                <div className="flex gap-2">
                    <GitBranch className="mt-1 shrink-0 text-slate-500" size={17} />
                    <p className="font-semibold text-slate-600">{item.dependencies}</p>
                </div>
                <div className="flex gap-2">
                    <LockKey className="mt-1 shrink-0 text-rose-500" size={17} />
                    <p className="font-semibold text-slate-600">{item.protected}</p>
                </div>
            </div>
        </article>
    );
}

export function MezanV2CompletionCenterView() {
    const [activeTab, setActiveTab] = useState("core");
    const summary = useMemo(() => getCompletionSummary(), []);
    return (
        <div className="space-y-6" dir="rtl" data-testid="mezan-v2-completion-center">
            <header className="overflow-hidden rounded-3xl border border-violet-200 bg-gradient-to-br from-slate-950 via-violet-950 to-violet-800 p-6 text-white shadow-xl sm:p-8">
                <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
                    <div className="max-w-3xl">
                        <div className="inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3 py-1.5 text-xs font-extrabold text-violet-100">
                            <ShieldCheck size={17} weight="duotone" />
                            المرجع الرسمي لاكتمال ميزان 2
                        </div>
                        <h1 className="mt-4 text-3xl font-black leading-tight sm:text-4xl">خطة اكتمال ميزان 2</h1>
                        <p className="mt-3 max-w-2xl text-sm font-semibold leading-7 text-violet-100 sm:text-base">كل علامة خضراء تعني أن المهمة نُفذت واختُبرت. القاعدة لا تحتسب أفكار السنوات القادمة ضمن نسبة اكتمال النواة، حتى نعرف متى يصبح ميزان 2 جاهزًا فعلًا للعمل اليومي.</p>
                    </div>
                    <div className="min-w-[190px] rounded-2xl border border-white/15 bg-white/10 p-5 text-center backdrop-blur">
                        <div className="text-5xl font-black tabular-nums">{summary.percent}%</div>
                        <div className="mt-1 text-sm font-extrabold text-violet-100">اكتمال النواة</div>
                        <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/20">
                            <div className="h-full rounded-full bg-emerald-400" style={{ width: `${summary.percent}%` }} />
                        </div>
                        <div className="mt-3 text-xs font-bold text-violet-200">آخر مراجعة: {PLAN_LAST_REVIEWED_AT}</div>
                    </div>
                </div>
            </header>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="ملخص اكتمال النواة">
                <SummaryCard label="مكتمل ومتحقق" value={summary.completed} tone="emerald" hint="علامة خضراء" />
                <SummaryCard label="قيد التنفيذ" value={summary.inProgress} tone="amber" />
                <SummaryCard label="متبقٍ" value={summary.pending} tone="slate" />
                <SummaryCard label="بانتظار تحقق أو اعتماد" value={summary.waiting} tone="rose" />
            </section>

            <section className="rounded-2xl border border-violet-200 bg-violet-50 p-5 shadow-sm" data-testid="next-recommended-step">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
                    <div className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-violet-700 text-white">
                        <RoadHorizon size={27} weight="duotone" />
                    </div>
                    <div className="flex-1">
                        <div className="text-xs font-black text-violet-700">الخطوة التالية المعتمدة</div>
                        <h2 className="mt-1 text-xl font-black text-violet-950">{NEXT_RECOMMENDED_STEP.title}</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-violet-900">{NEXT_RECOMMENDED_STEP.reason}</p>
                        <div className="mt-4 grid gap-3 lg:grid-cols-2">
                            <div className="rounded-xl border border-violet-200 bg-white p-3 text-sm font-semibold leading-6 text-slate-700"><strong className="font-black text-slate-950">أول تسليم:</strong> {NEXT_RECOMMENDED_STEP.firstDelivery}</div>
                            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-semibold leading-6 text-rose-900"><strong className="font-black">لا نخلط معها:</strong> {NEXT_RECOMMENDED_STEP.doNotMix}</div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                <div className="flex gap-3">
                    <CheckCircle className="mt-0.5 shrink-0 text-emerald-600" size={24} weight="fill" />
                    <div>
                        <h2 className="font-black text-slate-950">متى تحصل المهمة على العلامة الخضراء؟</h2>
                        <ul className="mt-2 grid gap-2 text-sm font-semibold leading-6 text-slate-600 lg:grid-cols-2">
                            {COMPLETION_RULES.map((rule) => <li key={rule} className="flex gap-2"><span className="text-emerald-600">•</span><span>{rule}</span></li>)}
                        </ul>
                    </div>
                </div>
            </section>

            <nav className="grid grid-cols-2 gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm lg:grid-cols-4" aria-label="أقسام خطة اكتمال ميزان 2">
                {TABS.map(({ id, label, Icon }) => {
                    const active = activeTab === id;
                    return (
                        <button key={id} type="button" onClick={() => setActiveTab(id)} className={`inline-flex items-center justify-center gap-2 rounded-xl px-3 py-3 text-sm font-extrabold transition ${active ? "bg-violet-700 text-white shadow" : "text-slate-700 hover:bg-violet-50 hover:text-violet-800"}`} data-testid={`completion-tab-${id}`}>
                            <Icon size={19} weight="duotone" />
                            {label}
                        </button>
                    );
                })}
            </nav>

            {activeTab === "core" && <RoadmapView core />}
            {activeTab === "future" && <RoadmapView core={false} />}
            {activeTab === "migration" && <MigrationView />}
            {activeTab === "parallel" && <ParallelView />}
        </div>
    );
}

export default function MezanV2CompletionCenter() {
    return <MezanV2CompletionCenterView />;
}
