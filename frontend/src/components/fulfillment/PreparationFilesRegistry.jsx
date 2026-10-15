import { useCallback, useEffect, useState } from "react";
import {
    ArrowClockwise,
    DownloadSimple,
    FilePdf,
    SpinnerGap,
    Wrench,
    WarningCircle,
} from "@phosphor-icons/react";

import {
    listPreparationFiles,
    recoverStalePreparationFiles,
    repairPreparationBatchCustomerOptions,
    reviewedPreparationBatchPdfUrl,
} from "../../services/orderReviewEngine";
import { preparationFileRecordLabel } from "../../preparationFileRegistryUi";

function fileMeta(file = {}) {
    const parts = [];
    const quantity = Number(file.allocated_quantity || 0);
    const orderCount = Number(file.order_count || 0);
    if (quantity > 0) parts.push(`${quantity} قطعة`);
    if (orderCount > 0) parts.push(`${orderCount} طلب`);
    parts.push(file.file_date_display || file.file_date || "تاريخ غير متاح");
    parts.push(`المسؤول: ${file.responsible_employee_name || "—"}`);
    return parts.join(" • ");
}

export default function PreparationFilesRegistry() {
    const [files, setFiles] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [recovering, setRecovering] = useState(false);
    const [repairingId, setRepairingId] = useState("");
    const [notice, setNotice] = useState("");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setError("");
        try {
            const response = await listPreparationFiles({ limit: 100 });
            setFiles(response.items || []);
        } catch (loadError) {
            setError(loadError.message || "تعذّر تحميل سجل ملفات التجهيز.");
        } finally {
            if (!silent) setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
        const refresh = () => load({ silent: true });
        window.addEventListener("mezan:preparation-file-created", refresh);
        return () => window.removeEventListener("mezan:preparation-file-created", refresh);
    }, [load]);

    const recover = async () => {
        if (recovering) return;
        setRecovering(true);
        setError("");
        setNotice("");
        try {
            const result = await recoverStalePreparationFiles();
            const restored = Number(result?.restored_order_count || 0);
            const recovered = Number(result?.recovered_count || 0);
            setNotice(restored > 0
                ? `تمت استعادة ${restored} طلب متعثر إلى مرحلة تم المراجعة.`
                : recovered > 0
                    ? `تم تنظيف ${recovered} محاولة ملف متعثرة دون فقد أي قطعة.`
                    : "لا توجد محاولات تجهيز متعثرة تحتاج إلى استعادة.");
            await load({ silent: true });
        } catch (recoverError) {
            setError(recoverError.message || "تعذّرت استعادة ملفات التجهيز المتعثرة.");
        } finally {
            setRecovering(false);
        }
    };

    const repairOptions = async (file) => {
        if (!file?.batch_id || repairingId) return;
        setRepairingId(file.batch_id);
        setError("");
        setNotice("");
        try {
            const result = await repairPreparationBatchCustomerOptions(file.batch_id);
            setNotice(`تم إصلاح خيارات العميل في ${file.file_number || "ملف التجهيز"} (${Number(result?.repaired_line_count || 0)} بطاقة).`);
            await load({ silent: true });
        } catch (repairError) {
            setError(repairError.message || "تعذّر إصلاح خيارات العميل في الملف.");
        } finally {
            setRepairingId("");
        }
    };

    return (
        <section
            id="mezan-preparation-file-history"
            className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
            dir="rtl"
            data-testid="preparation-files-registry-window"
            data-view="preparation-files-registry"
        >
            <header className="border-b border-slate-100 bg-slate-50 px-4 py-4 sm:px-5">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex items-start gap-3">
                        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-violet-100 text-violet-700">
                            <FilePdf size={24} weight="duotone" />
                        </span>
                        <div>
                            <h2 className="text-xl font-black text-slate-950">سجل ملفات التجهيز</h2>
                            <p className="mt-1 text-sm text-slate-500">
                                الملفات السابقة وأرقامها والموظف المسؤول وإعادة تحميل PDF.
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <span className="rounded-full border border-violet-200 bg-white px-3 py-1.5 text-xs font-black text-violet-700">
                            {files.length} ملف
                        </span>
                        <button
                            type="button"
                            onClick={() => load()}
                            disabled={loading}
                            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 transition hover:border-violet-300 hover:text-violet-800 disabled:opacity-60"
                            data-testid="refresh-preparation-files-registry"
                        >
                            <ArrowClockwise size={17} weight="bold" className={loading ? "animate-spin" : ""} />
                            تحديث
                        </button>
                        <button
                            type="button"
                            onClick={recover}
                            disabled={recovering}
                            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3 text-xs font-extrabold text-emerald-800 transition hover:bg-emerald-100 disabled:opacity-60"
                            data-testid="recover-stale-preparation-files"
                        >
                            <ArrowClockwise size={17} weight="bold" className={recovering ? "animate-spin" : ""} />
                            {recovering ? "جاري الاستعادة…" : "استعادة الملفات المتعثرة"}
                        </button>
                    </div>
                </div>
            </header>

            <div className="p-4 sm:p-5">
                {error && (
                    <div className="mb-4 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-800">
                        <WarningCircle size={20} className="mt-0.5 shrink-0" weight="fill" />
                        <span>{error}</span>
                    </div>
                )}

                {notice && (
                    <div className="mb-4 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-900" data-testid="preparation-files-operation-notice">
                        {notice}
                    </div>
                )}

                {loading ? (
                    <div className="flex min-h-56 items-center justify-center text-violet-700">
                        <SpinnerGap size={34} className="animate-spin" />
                    </div>
                ) : files.length === 0 ? (
                    <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-10 text-center">
                        <FilePdf size={38} className="mx-auto text-slate-300" weight="duotone" />
                        <div className="mt-3 font-extrabold text-slate-700">لا توجد ملفات تجهيز محفوظة حتى الآن.</div>
                        <p className="mt-1 text-sm text-slate-500">
                            أنشئ الملف من نافذة «تم المراجعة»، وسيظهر هنا تلقائيًا.
                        </p>
                    </div>
                ) : (
                    <div className="grid gap-3" data-testid="preparation-files-registry-list">
                        {files.map((file, index) => {
                            const key = file.file_number || file.batch_id || `${file.file_name || "file"}-${index}`;
                            return (
                                <article
                                    key={key}
                                    className="grid gap-3 rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center"
                                    data-testid="preparation-file-record"
                                    data-file-number={file.file_number || ""}
                                >
                                    <div className="min-w-0">
                                        <div className="break-words text-sm font-black text-slate-950 sm:text-base">
                                            {preparationFileRecordLabel(file)}
                                        </div>
                                        <div className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                                            {fileMeta(file)}
                                        </div>
                                    </div>
                                    <div className="flex flex-col gap-2 sm:flex-row">
                                        <button
                                            type="button"
                                            onClick={() => repairOptions(file)}
                                            disabled={!file.batch_id || Boolean(repairingId)}
                                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-amber-300 bg-amber-50 px-4 text-sm font-extrabold text-amber-900 transition hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
                                            data-testid="repair-preparation-file-customer-options"
                                        >
                                            {repairingId === file.batch_id
                                                ? <SpinnerGap size={19} className="animate-spin" />
                                                : <Wrench size={19} weight="bold" />}
                                            {repairingId === file.batch_id ? "جاري الإصلاح…" : "إصلاح خيارات العميل"}
                                        </button>
                                        {file.batch_id ? (
                                            <a
                                                href={reviewedPreparationBatchPdfUrl(file.batch_id)}
                                                download={file.file_name || `preparation-${file.batch_id}.pdf`}
                                                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-extrabold text-white transition hover:bg-violet-800"
                                                data-testid="download-preparation-file-pdf"
                                            >
                                                <DownloadSimple size={19} weight="bold" />
                                                تحميل PDF
                                            </a>
                                        ) : (
                                            <button
                                                type="button"
                                                disabled
                                                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-extrabold text-white opacity-50"
                                                data-testid="download-preparation-file-pdf"
                                            >
                                                <DownloadSimple size={19} weight="bold" />
                                                تحميل PDF
                                            </button>
                                        )}
                                    </div>
                                </article>
                            );
                        })}
                    </div>
                )}
            </div>
        </section>
    );
}
