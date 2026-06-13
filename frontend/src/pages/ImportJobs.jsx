import { useEffect, useState, useRef } from "react";
import { Link } from "react-router-dom";
import {
    FileXls, ClockClockwise, CheckCircle, XCircle, Spinner, Warning, Trash, ArrowRight, ArrowsClockwise,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";

const fmtInt = (v) => Number(v || 0).toLocaleString("en-US");
const fmtDateTime = (s) =>
    s ? new Date(s).toLocaleString("en-US", { hour12: false }) : "—";

const STATUS_META = {
    queued:     { label: "في الانتظار",  cls: "bg-slate-100 text-slate-800 border-slate-300", Icon: ClockClockwise },
    processing: { label: "جاري المعالجة", cls: "bg-sky-100 text-sky-900 border-sky-300", Icon: Spinner },
    completed:  { label: "مكتمل",         cls: "bg-emerald-100 text-emerald-900 border-emerald-300", Icon: CheckCircle },
    failed:     { label: "فشل",           cls: "bg-rose-100 text-rose-900 border-rose-300", Icon: XCircle },
};

function StatusPill({ status }) {
    const m = STATUS_META[status] || STATUS_META.queued;
    const Icon = m.Icon;
    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-bold border ${m.cls}`} data-testid={`status-${status}`}>
            <Icon size={14} weight={status === "processing" ? "duotone" : "bold"} className={status === "processing" ? "animate-spin" : ""} />
            {m.label}
        </span>
    );
}

function ProgressBar({ processed, total }) {
    const pct = total > 0 ? Math.round((processed / total) * 100) : 0;
    return (
        <div className="w-full">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                <span className="num">{fmtInt(processed)} / {fmtInt(total)}</span>
                <span className="num">{pct}%</span>
            </div>
            <div className="w-full h-1.5 bg-slate-200 rounded-full overflow-hidden">
                <div className="h-full bg-brand transition-all duration-500" style={{ width: `${pct}%` }} />
            </div>
        </div>
    );
}

export default function ImportJobs() {
    const [jobs, setJobs] = useState([]);
    const [selected, setSelected] = useState(null);
    const [loading, setLoading] = useState(true);
    const pollRef = useRef(null);

    const fetchJobs = async () => {
        try {
            const { data } = await api.get("/import-jobs", { params: { limit: 30 } });
            setJobs(data.items || []);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحميل المهام");
        } finally {
            setLoading(false);
        }
    };

    const fetchOne = async (id) => {
        try {
            const { data } = await api.get(`/import-jobs/${id}`);
            setSelected(data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    const deleteJob = async (id) => {
        if (!window.confirm("هل تريد حذف هذه المهمة من السجل؟")) return;
        try {
            await api.delete(`/import-jobs/${id}`);
            toast.success("تم الحذف");
            if (selected?.id === id) setSelected(null);
            fetchJobs();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        }
    };

    // Poll every 2s while ANY job is queued or processing.
    useEffect(() => {
        fetchJobs();
        pollRef.current = setInterval(() => {
            const hasActive = jobs.some(j => j.status === "queued" || j.status === "processing");
            if (hasActive || selected?.status === "queued" || selected?.status === "processing") {
                fetchJobs();
                if (selected?.id) fetchOne(selected.id);
            }
        }, 2000);
        return () => clearInterval(pollRef.current);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jobs.length, selected?.status]);

    return (
        <div className="space-y-6" data-testid="import-jobs-page">
            <header className="flex items-start justify-between gap-3">
                <div>
                    <h1 className="text-3xl sm:text-4xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>
                        حالة استيراد الملفات
                    </h1>
                    <p className="text-muted-foreground mt-1 text-sm">
                        ملفات Excel تُعالَج في الخلفية. أثناء المعالجة، استقبال webhooks من Make يستمر بدون توقف.
                    </p>
                </div>
                <button onClick={fetchJobs} className="inline-flex items-center gap-1.5 px-3 py-2 text-sm bg-white border border-border rounded-lg hover:bg-accent" data-testid="refresh-btn">
                    <ArrowsClockwise size={16} /> تحديث
                </button>
            </header>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* Jobs list */}
                <div className="lg:col-span-2 bg-white rounded-xl border border-border overflow-hidden" data-testid="jobs-list">
                    {loading ? (
                        <div className="p-10 text-center text-muted-foreground">جاري التحميل...</div>
                    ) : jobs.length === 0 ? (
                        <div className="p-10 text-center text-muted-foreground">
                            لا توجد عمليات استيراد بعد. <Link to="/upload" className="text-brand font-bold hover:underline">ارفع ملف Excel الآن</Link>
                        </div>
                    ) : (
                        <table className="mezan-table w-full text-sm">
                            <thead className="bg-slate-50 text-muted-foreground text-xs">
                                <tr>
                                    <th className="text-right px-3 py-2 font-bold">الملف</th>
                                    <th className="text-right px-3 py-2 font-bold">الحالة</th>
                                    <th className="text-right px-3 py-2 font-bold">التقدم</th>
                                    <th className="text-right px-3 py-2 font-bold">جديد / محدّث / فشل</th>
                                    <th className="text-right px-3 py-2 font-bold">إجراء</th>
                                </tr>
                            </thead>
                            <tbody>
                                {jobs.map((j) => (
                                    <tr key={j.id} className={`border-t border-border hover:bg-accent/30 cursor-pointer ${selected?.id === j.id ? "bg-accent/40" : ""}`} onClick={() => fetchOne(j.id)} data-testid={`job-row-${j.id}`}>
                                        <td className="px-3 py-2.5">
                                            <div className="flex items-center gap-2">
                                                <FileXls size={20} className="text-emerald-600 shrink-0" weight="duotone" />
                                                <div className="min-w-0">
                                                    <div className="font-bold truncate max-w-[200px]" title={j.filename}>{j.filename}</div>
                                                    <div className="text-[10px] text-muted-foreground">{fmtDateTime(j.created_at)}</div>
                                                </div>
                                            </div>
                                        </td>
                                        <td className="px-3 py-2.5"><StatusPill status={j.status} /></td>
                                        <td className="px-3 py-2.5 min-w-[140px]">
                                            <ProgressBar processed={j.processed_rows} total={j.total_rows} />
                                        </td>
                                        <td className="px-3 py-2.5 num text-xs">
                                            <span className="text-emerald-700 font-bold">{fmtInt(j.created_count)}</span>
                                            <span className="text-muted-foreground"> / </span>
                                            <span className="text-sky-700 font-bold">{fmtInt(j.updated_count)}</span>
                                            <span className="text-muted-foreground"> / </span>
                                            <span className="text-rose-700 font-bold">{fmtInt(j.error_count)}</span>
                                        </td>
                                        <td className="px-3 py-2.5">
                                            <div className="flex items-center gap-1">
                                                {j.status === "completed" && j.analysis_id && (
                                                    <Link to={`/analyses/${j.analysis_id}`} className="inline-flex items-center gap-1 text-xs text-brand font-bold hover:underline" onClick={e => e.stopPropagation()}>
                                                        التحليل <ArrowRight size={12} />
                                                    </Link>
                                                )}
                                                {(j.status === "completed" || j.status === "failed") && (
                                                    <button onClick={(e) => { e.stopPropagation(); deleteJob(j.id); }} className="p-1 text-rose-600 hover:bg-rose-50 rounded" title="حذف" data-testid={`delete-${j.id}`}>
                                                        <Trash size={14} />
                                                    </button>
                                                )}
                                            </div>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>

                {/* Detail panel */}
                <aside className="bg-white rounded-xl border border-border p-4 space-y-3 lg:sticky lg:top-4 h-fit" data-testid="job-detail">
                    {!selected ? (
                        <div className="text-sm text-muted-foreground text-center py-8">اختر مهمة لرؤية تفاصيلها وسجل الأخطاء.</div>
                    ) : (
                        <>
                            <div className="flex items-center justify-between">
                                <h3 className="font-bold text-sm truncate">{selected.filename}</h3>
                                <StatusPill status={selected.status} />
                            </div>
                            <div className="grid grid-cols-2 gap-2 text-xs">
                                <div className="rounded bg-slate-50 p-2 border border-slate-200">
                                    <div className="text-muted-foreground">إجمالي الصفوف</div>
                                    <div className="num font-bold">{fmtInt(selected.total_rows)}</div>
                                </div>
                                <div className="rounded bg-slate-50 p-2 border border-slate-200">
                                    <div className="text-muted-foreground">المعالَج</div>
                                    <div className="num font-bold">{fmtInt(selected.processed_rows)}</div>
                                </div>
                                <div className="rounded bg-emerald-50 p-2 border border-emerald-200">
                                    <div className="text-emerald-900">طلبات جديدة</div>
                                    <div className="num font-bold text-emerald-900">{fmtInt(selected.created_count)}</div>
                                </div>
                                <div className="rounded bg-sky-50 p-2 border border-sky-200">
                                    <div className="text-sky-900">طلبات محدّثة</div>
                                    <div className="num font-bold text-sky-900">{fmtInt(selected.updated_count)}</div>
                                </div>
                                <div className="rounded bg-amber-50 p-2 border border-amber-200">
                                    <div className="text-amber-900">صفوف متخطّاة</div>
                                    <div className="num font-bold text-amber-900">{fmtInt(selected.skipped_count)}</div>
                                </div>
                                <div className="rounded bg-rose-50 p-2 border border-rose-200">
                                    <div className="text-rose-900">صفوف فشلت</div>
                                    <div className="num font-bold text-rose-900">{fmtInt(selected.error_count)}</div>
                                </div>
                            </div>
                            <div className="text-[11px] text-muted-foreground space-y-0.5">
                                <div><span className="font-bold">بدأت:</span> {fmtDateTime(selected.started_at)}</div>
                                <div><span className="font-bold">انتهت:</span> {fmtDateTime(selected.completed_at)}</div>
                            </div>
                            {selected.error_message && (
                                <div className="rounded bg-rose-50 border border-rose-200 p-2 text-xs text-rose-900 flex items-start gap-1.5">
                                    <Warning size={14} className="shrink-0 mt-0.5" />
                                    <div className="break-words">{selected.error_message}</div>
                                </div>
                            )}
                            {selected.errors?.length > 0 && (
                                <details className="text-xs" data-testid="error-list">
                                    <summary className="cursor-pointer font-bold text-rose-700">سجل الأخطاء ({selected.errors.length})</summary>
                                    <div className="mt-1 max-h-60 overflow-y-auto space-y-1">
                                        {selected.errors.slice(-20).reverse().map((e, i) => (
                                            <div key={i} className="rounded bg-rose-50 border border-rose-200 p-1.5 text-[11px]" dir="ltr" style={{ textAlign: "right" }}>
                                                <div className="font-bold">{e.order_number || `Row #${e.row_index}`}</div>
                                                <div className="text-rose-800">{e.error}</div>
                                            </div>
                                        ))}
                                    </div>
                                </details>
                            )}
                            {selected.status === "completed" && selected.analysis_id && (
                                <Link to={`/analyses/${selected.analysis_id}`} className="block w-full text-center px-3 py-2 bg-brand text-white font-bold rounded-lg bg-brand-hover text-sm" data-testid="view-analysis-btn">
                                    عرض التحليل المرتبط
                                </Link>
                            )}
                        </>
                    )}
                </aside>
            </div>

            <div className="bg-sky-50/60 border border-sky-200 rounded-lg p-3 text-xs text-sky-900 flex items-start gap-2">
                <Warning size={18} className="text-sky-700 shrink-0 mt-0.5" weight="duotone" />
                <div>
                    أثناء معالجة أي ملف Excel هنا، يستمر استقبال webhooks من Make.com بدون أي تأخير. لا يحجز أي مصدر النظام عن الآخر، وكل طلب له قفل مستقل لمنع التعارض.
                </div>
            </div>
        </div>
    );
}
