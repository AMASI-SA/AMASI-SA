import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CalendarBlank,
    CheckCircle,
    Clock,
    Gear,
    Play,
    SpinnerGap,
    Storefront,
    UserMinus,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    getMyPreparationWork,
    getPreparationManagerSummary,
    startPreparationFile,
} from "../../services/preparationWorkService";
import PreparationSupplierDispatchWorkspace from "./PreparationSupplierDispatchWorkspace";

const STATUS_LABELS = {
    assigned: "مسند ولم يبدأ",
    in_progress: "قيد التنفيذ",
    ready_for_employee_receipt: "جاهز للاستلام",
    received: "مستلم",
    blocked: "متوقف",
    cancelled: "ملغى",
};

export function riyadhDateInputValue(now = new Date()) {
    const parts = new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).formatToParts(now);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
}

export function filePieces(pieces = [], batchId = "") {
    return pieces.filter((piece) => String(piece?.batch_id || "") === String(batchId || ""));
}

export function fileEstimatedDueAt(pieces = [], batchId = "") {
    const timestamps = filePieces(pieces, batchId)
        .map((piece) => Date.parse(piece?.estimated_due_at || ""))
        .filter(Number.isFinite);
    return timestamps.length ? new Date(Math.max(...timestamps)).toISOString() : null;
}

export function filePiecesAreReady(file = {}) {
    const actual = Number(file?.piece_count || 0);
    const expected = Number(file?.expected_piece_count || 0);
    return expected > 0
        && actual === expected
        && file?.piece_registry_status !== "recovery_required";
}

function formatDateTime(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA", {
        timeZone: "Asia/Riyadh",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

function SummaryCard({ value, label, tone = "slate" }) {
    const styles = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-4 ${styles[tone]}`}>
            <div className="text-3xl font-black tabular-nums">{Number(value || 0)}</div>
            <div className="mt-1 text-xs font-extrabold">{label}</div>
        </div>
    );
}

function PieceServiceSummary({ piece }) {
    const services = Array.isArray(piece?.services) ? piece.services : [];
    if (!services.length) {
        return <span className="text-xs font-bold text-slate-500">لا توجد خدمات خارجية مطلوبة</span>;
    }
    return (
        <div className="flex flex-wrap gap-1.5">
            {services.map((service) => (
                <span
                    key={`${piece.piece_id}-${service.service_id}`}
                    className={`rounded-full border px-2 py-1 text-[11px] font-extrabold ${service.status === "completed" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`}
                >
                    {service.service_name || service.service_code || "خدمة"}
                </span>
            ))}
        </div>
    );
}

function MyWorkView({ work, loading, error, onRefresh, onStart, startingFile }) {
    const files = Array.isArray(work?.files) ? work.files : [];
    const pieces = Array.isArray(work?.pieces) ? work.pieces : [];
    const summary = work?.summary || {};
    const materializationWarnings = Array.isArray(work?.materialization_warnings)
        ? work.materialization_warnings
        : [];

    if (loading && !files.length) {
        return (
            <div className="flex min-h-48 items-center justify-center gap-2 text-violet-700">
                <SpinnerGap size={24} className="animate-spin" />
                <span className="font-extrabold">جارٍ تحميل منتجاتك…</span>
            </div>
        );
    }

    return (
        <div className="space-y-5" data-testid="preparation-my-work-view">
            <div className="grid gap-3 grid-cols-2 xl:grid-cols-4">
                <SummaryCard value={summary.assigned} label="مسند ولم يبدأ" tone="violet" />
                <SummaryCard value={summary.in_progress} label="قيد التنفيذ" tone="amber" />
                <SummaryCard value={summary.ready} label="جاهز تلقائيًا" tone="emerald" />
                <SummaryCard value={summary.remaining} label="المتبقي الحالي" />
            </div>

            <div className="flex items-center justify-between gap-3">
                <div>
                    <h2 className="text-lg font-black text-slate-950">ملفات التجهيز المسندة إليّ</h2>
                    <p className="mt-1 text-sm font-semibold text-slate-500">الملف يبقى مسندًا ولا يدخل قيد التنفيذ قبل بدء العمل فعليًا.</p>
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={loading}
                    className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 disabled:opacity-60"
                >
                    <ArrowClockwise size={17} className={loading ? "animate-spin" : ""} />
                    تحديث
                </button>
            </div>

            {error && (
                <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">
                    <WarningCircle size={20} className="mt-0.5 shrink-0" />
                    {error}
                </div>
            )}

            {materializationWarnings.length > 0 && (
                <div className="rounded-2xl border border-rose-300 bg-rose-50 p-4 text-rose-950" data-testid="preparation-piece-recovery-warning">
                    <div className="flex items-start gap-2">
                        <WarningCircle size={22} weight="fill" className="mt-0.5 shrink-0" />
                        <div>
                            <div className="font-black">تعذّر تجهيز سجلات القطع لبعض الملفات</div>
                            <p className="mt-1 text-xs font-bold leading-6">لا تبدأ هذه الملفات حتى يكتمل الاسترداد. اضغط تحديث لإعادة المحاولة.</p>
                            <div className="mt-2 space-y-1 text-xs font-bold" dir="ltr">
                                {materializationWarnings.map((warning) => (
                                    <div key={`${warning.file_number}-${warning.error_code || warning.error_type}`}>
                                        {warning.file_number}: {warning.error_code || warning.error_type || "materialization_failed"}
                                    </div>
                                ))}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {!files.length && !error ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center">
                    <CheckCircle size={34} className="mx-auto text-slate-400" />
                    <div className="mt-3 font-black text-slate-800">لا توجد ملفات مسندة إليك حاليًا</div>
                </div>
            ) : (
                <div className="space-y-4">
                    {files.map((file) => {
                        const related = filePieces(pieces, file.batch_id);
                        const automaticDue = fileEstimatedDueAt(pieces, file.batch_id);
                        const required = file.schedule_mode === "required";
                        const dueAt = required ? file.required_due_at : automaticDue;
                        const piecesReady = filePiecesAreReady(file);
                        const canStart = piecesReady
                            && ["assigned", "not_started", ""].includes(file.execution_status || "");
                        return (
                            <article key={file.file_number} className={`overflow-hidden rounded-2xl border bg-white shadow-sm ${required ? "border-rose-300" : "border-slate-200"}`}>
                                <header className={`p-4 sm:p-5 ${required ? "bg-rose-50" : "bg-slate-50"}`}>
                                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                                        <div>
                                            <div className="flex flex-wrap items-center gap-2">
                                                <h3 className="text-lg font-black text-slate-950">{file.file_title || file.file_name || file.file_number}</h3>
                                                <span className="rounded-full border border-violet-200 bg-white px-2.5 py-1 text-xs font-black text-violet-700">{file.file_number}</span>
                                                <span className={`rounded-full border px-2.5 py-1 text-xs font-black ${file.execution_status === "in_progress" ? "border-amber-200 bg-amber-50 text-amber-800" : "border-violet-200 bg-violet-50 text-violet-800"}`}>
                                                    {file.execution_status === "in_progress" ? "قيد التنفيذ" : "مسند ولم يبدأ"}
                                                </span>
                                            </div>
                                            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs font-bold text-slate-600">
                                                <span>{file.piece_count} قطعة</span>
                                                <span>{file.remaining_count} متبقية</span>
                                                <span>{file.ready_count} جاهزة تلقائيًا</span>
                                                <span>المسؤول: {file.responsible_employee_name || "—"}</span>
                                            </div>
                                        </div>
                                        <div className={`rounded-xl border px-3 py-2 text-xs font-extrabold ${required ? "border-rose-200 bg-white text-rose-800" : "border-slate-200 bg-white text-slate-700"}`}>
                                            <div className="flex items-center gap-1.5">
                                                {required ? <WarningCircle size={17} weight="fill" /> : <Clock size={17} />}
                                                {required ? "موعد إجباري" : "وقت متوقع تلقائي"}
                                            </div>
                                            <div className="mt-1">{formatDateTime(dueAt)}</div>
                                        </div>
                                    </div>
                                    {canStart && (
                                        <button
                                            type="button"
                                            onClick={() => onStart(file.file_number)}
                                            disabled={startingFile === file.file_number}
                                            className="mt-4 inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-60"
                                            data-testid={`start-preparation-file-${file.file_number}`}
                                        >
                                            {startingFile === file.file_number ? <SpinnerGap size={19} className="animate-spin" /> : <Play size={19} weight="fill" />}
                                            بدء التنفيذ
                                        </button>
                                    )}
                                    {!piecesReady && (
                                        <div className="mt-4 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900">
                                            سجلات القطع غير مكتملة ({file.piece_count || 0}/{file.expected_piece_count || 0}) — بدء التنفيذ متوقف حتى الاسترداد.
                                        </div>
                                    )}
                                </header>
                                <div className="divide-y divide-slate-100">
                                    {related.map((piece) => (
                                        <div key={piece.piece_id} className="grid gap-3 p-4 sm:grid-cols-[72px_minmax(0,1fr)_auto] sm:items-start">
                                            {piece.selected_image_url ? (
                                                <img src={piece.selected_image_url} alt="" className="h-16 w-16 rounded-xl border border-slate-200 object-cover" />
                                            ) : (
                                                <div className="flex h-16 w-16 items-center justify-center rounded-xl bg-slate-100 text-slate-400"><Gear size={23} /></div>
                                            )}
                                            <div className="min-w-0">
                                                <div className="font-black text-slate-900">{piece.product_name || "منتج"}</div>
                                                <div className="mt-1 text-xs font-bold text-slate-500">طلب {piece.order_number} · قطعة {piece.unit_index} · {piece.sku || "بدون SKU"}</div>
                                                <div className="mt-2"><PieceServiceSummary piece={piece} /></div>
                                            </div>
                                            <span className="w-fit rounded-full border border-slate-200 bg-slate-50 px-2.5 py-1 text-xs font-extrabold text-slate-700">
                                                {STATUS_LABELS[piece.status] || piece.status || "مسند"}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </article>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

function EmployeeManagementView({ data, loading, error, date, onDateChange, onRefresh }) {
    const items = Array.isArray(data?.items) ? data.items : [];
    return (
        <div className="space-y-5" data-testid="preparation-employee-management-view">
            <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:flex-row sm:items-end sm:justify-between">
                <label className="text-sm font-extrabold text-slate-800">
                    اليوم المطلوب
                    <span className="mt-1 flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3">
                        <CalendarBlank size={19} className="text-violet-700" />
                        <input type="date" value={date} onChange={(event) => onDateChange(event.target.value)} className="min-h-11 bg-transparent font-bold outline-none" />
                    </span>
                </label>
                <button type="button" onClick={onRefresh} disabled={loading} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-slate-950 px-4 text-sm font-black text-white disabled:opacity-60">
                    <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} />
                    تحديث التقرير
                </button>
            </div>

            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {loading && !items.length ? (
                <div className="flex min-h-44 items-center justify-center gap-2 text-violet-700"><SpinnerGap size={24} className="animate-spin" />جارٍ تحميل أداء الموظفين…</div>
            ) : !items.length && !error ? (
                <div className="rounded-2xl border border-dashed border-slate-300 p-8 text-center font-bold text-slate-600">لا توجد بيانات تجهيز في التاريخ المحدد.</div>
            ) : (
                <div className="grid gap-4 xl:grid-cols-2">
                    {items.map((employee) => (
                        <article key={employee.employee_id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                            <div className="flex items-center gap-3">
                                <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-100 text-violet-700"><UsersThree size={24} weight="duotone" /></span>
                                <div>
                                    <h3 className="font-black text-slate-950">{employee.employee_name || employee.employee_id}</h3>
                                    <p className="mt-1 text-xs font-bold text-slate-500">إجمالي القطع الحالية: {employee.total_current}</p>
                                </div>
                            </div>
                            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
                                <SummaryCard value={employee.assigned_on_date} label="رُفع له في اليوم" tone="violet" />
                                <SummaryCard value={employee.started_on_date} label="بدأ في اليوم" tone="amber" />
                                <SummaryCard value={employee.completed_on_date} label="اكتمل في اليوم" tone="emerald" />
                                <SummaryCard value={employee.remaining_current} label="المتبقي لديه الآن" />
                                <SummaryCard value={employee.ready_current} label="جاهز للاستلام" tone="emerald" />
                            </div>
                        </article>
                    ))}
                </div>
            )}
        </div>
    );
}

export default function PreparationWorkDashboard({ initialView = "my-work", standalone = false }) {
    const [activeView, setActiveView] = useState(initialView);
    const [work, setWork] = useState(null);
    const [workLoading, setWorkLoading] = useState(true);
    const [workError, setWorkError] = useState("");
    const [startingFile, setStartingFile] = useState("");
    const [managerData, setManagerData] = useState(null);
    const [managerLoading, setManagerLoading] = useState(false);
    const [managerError, setManagerError] = useState("");
    const [managerAllowed, setManagerAllowed] = useState(true);
    const [date, setDate] = useState(() => riyadhDateInputValue());

    const loadWork = useCallback(async () => {
        setWorkLoading(true);
        setWorkError("");
        try {
            setWork(await getMyPreparationWork({ limit: 100 }));
        } catch (error) {
            setWorkError(error.message || "تعذّر تحميل منتجاتك.");
        } finally {
            setWorkLoading(false);
        }
    }, []);

    const loadManager = useCallback(async () => {
        setManagerLoading(true);
        setManagerError("");
        try {
            setManagerData(await getPreparationManagerSummary({ date }));
            setManagerAllowed(true);
        } catch (error) {
            if (error.forbidden) {
                setManagerAllowed(false);
            } else {
                setManagerError(error.message || "تعذّر تحميل التقرير.");
            }
        } finally {
            setManagerLoading(false);
        }
    }, [date]);

    useEffect(() => { loadWork(); }, [loadWork]);
    useEffect(() => {
        loadManager();
    }, [loadManager]);

    const startFile = async (fileNumber) => {
        if (!fileNumber || startingFile) return;
        setStartingFile(fileNumber);
        setWorkError("");
        try {
            await startPreparationFile(fileNumber);
            await Promise.all([loadWork(), managerAllowed ? loadManager() : Promise.resolve()]);
        } catch (error) {
            setWorkError(error.message || "تعذّر بدء الملف.");
        } finally {
            setStartingFile("");
        }
    };

    const tabs = useMemo(() => [
        { id: "my-work", label: "تفاصيل القطع", Icon: Storefront, visible: true },
        { id: "unassigned", label: "منتجات غير مسندة", Icon: UserMinus, visible: managerAllowed },
        { id: "employees", label: "إدارة منتجات الموظفين", Icon: UsersThree, visible: managerAllowed },
    ].filter((item) => item.visible), [managerAllowed]);

    const reloadOperationalViews = useCallback(async () => {
        await Promise.all([
            loadWork(),
            managerAllowed ? loadManager() : Promise.resolve(),
        ]);
    }, [loadManager, loadWork, managerAllowed]);

    if (standalone || activeView === "my-products") {
        return (
            <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm sm:p-5" dir="rtl" data-testid="preparation-work-dashboard">
                <PreparationSupplierDispatchWorkspace view="my-products" onDataChanged={reloadOperationalViews} />
            </section>
        );
    }

    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" dir="rtl" data-testid="preparation-work-dashboard">
            <header className="border-b border-slate-100 bg-slate-50 p-4 sm:p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                    <div>
                        <h2 className="text-xl font-black text-slate-950">قيد التنفيذ</h2>
                        <p className="mt-1 text-sm font-semibold leading-6 text-slate-600">تبدأ هذه المرحلة من «إدارة منتجاتي» عند وصول ملف التجهيز إلى حساب الموظف.</p>
                    </div>
                    <nav className="flex flex-wrap gap-2" aria-label="نوافذ قيد التنفيذ">
                        {tabs.map(({ id, label, Icon }) => (
                            <button key={id} type="button" onClick={() => setActiveView(id)} className={`inline-flex min-h-10 items-center gap-2 rounded-xl px-4 text-sm font-black ${activeView === id ? "bg-violet-700 text-white" : "border border-slate-200 bg-white text-slate-700"}`}>
                                <Icon size={18} />
                                {label}
                            </button>
                        ))}
                    </nav>
                </div>
            </header>
            <div className="p-4 sm:p-5">
                {activeView === "employees" && managerAllowed ? (
                    <EmployeeManagementView data={managerData} loading={managerLoading} error={managerError} date={date} onDateChange={setDate} onRefresh={loadManager} />
                ) : activeView === "unassigned" && managerAllowed ? (
                    <PreparationSupplierDispatchWorkspace view={activeView} onDataChanged={reloadOperationalViews} />
                ) : (
                    <MyWorkView work={work} loading={workLoading} error={workError} onRefresh={loadWork} onStart={startFile} startingFile={startingFile} />
                )}
            </div>
        </section>
    );
}
