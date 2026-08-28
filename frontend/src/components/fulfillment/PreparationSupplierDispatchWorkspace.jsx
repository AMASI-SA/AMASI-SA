import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    ArrowRight,
    Buildings,
    Camera,
    CheckCircle,
    ClipboardText,
    DotsThreeVertical,
    Factory,
    FileText,
    MagnifyingGlass,
    Package,
    Printer,
    SpinnerGap,
    Storefront,
    UserSwitch,
    WarningCircle,
    X,
} from "@phosphor-icons/react";

import {
    getPreparationSupplierWorkspace,
    getUnassignedPreparationPieces,
    newPreparationDispatchRequestId,
    reassignPreparationPieces,
    rejectPreparationPieces,
    sendPreparationPiecesToSupplier,
} from "../../services/preparationSupplierDispatch";
import { printSupplierDispatch } from "./supplierDispatchPrint";
import CustomerServiceInstructionBanner from "./CustomerServiceInstructionBanner";

const SupplierReceivingWorkspace = lazy(() => import("./SupplierReceivingWorkspace"));

export function dispatchSelections(products = [], selected = {}) {
    return products
        .map((product) => ({
            group_key: String(product?.group_key || ""),
            quantity: Math.max(0, Math.min(
                Number(product?.available_quantity || 0),
                Number(selected?.[product?.group_key] || 0),
            )),
        }))
        .filter((row) => row.group_key && row.quantity > 0);
}

export function selectedFileDispatches(files = [], selected = {}) {
    return files
        .map((file) => ({
            file_number: String(file?.file_number || ""),
            selections: dispatchSelections(
                file?.products || [],
                selected?.[file?.file_number] || {},
            ),
        }))
        .filter((file) => file.file_number && file.selections.length > 0);
}

export function applySupplierDispatchToWorkspaceData(
    data,
    fileDispatches = [],
    dispatch = {},
) {
    if (!data || typeof data !== "object") return data;
    const selectionsByFile = new Map(
        fileDispatches.map((file) => [
            String(file?.file_number || ""),
            new Map((file?.selections || []).map((selection) => [
                String(selection?.group_key || ""),
                Math.max(0, Number(selection?.quantity || 0)),
            ])),
        ]),
    );
    const completedFiles = new Set(
        Array.isArray(dispatch?.completed_source_file_numbers)
            ? dispatch.completed_source_file_numbers.map(String)
            : [],
    );
    const files = (data.files || []).map((file) => {
        const fileNumber = String(file?.file_number || "");
        const selections = selectionsByFile.get(fileNumber);
        if (!selections) return file;
        let dispatchedQuantity = 0;
        const products = (file.products || []).map((product) => {
            const selectedQuantity = selections.get(String(product?.group_key || "")) || 0;
            if (!selectedQuantity) return product;
            const availableQuantity = Math.max(0, Number(product?.available_quantity || 0));
            const movedQuantity = Math.min(availableQuantity, selectedQuantity);
            dispatchedQuantity += movedQuantity;
            return {
                ...product,
                available_quantity: availableQuantity - movedQuantity,
                sent_quantity: Math.max(0, Number(product?.sent_quantity || 0)) + movedQuantity,
            };
        });
        return {
            ...file,
            products,
            available_quantity: Math.max(
                0,
                Number(file?.available_quantity || 0) - dispatchedQuantity,
            ),
            sent_quantity: Math.max(0, Number(file?.sent_quantity || 0)) + dispatchedQuantity,
            execution_status: completedFiles.has(fileNumber)
                ? "in_progress"
                : file.execution_status,
            is_new: products.some((product) => Number(product?.available_quantity || 0) > 0),
        };
    });
    const summary = {
        ...(data.summary || {}),
        new_files: files.filter((file) => file.is_new).length,
        available_to_send: files.reduce(
            (total, file) => total + Number(file?.available_quantity || 0),
            0,
        ),
        sent: files.reduce(
            (total, file) => total + Number(file?.sent_quantity || 0),
            0,
        ),
        waiting_review_pieces: files.reduce(
            (total, file) => total + Number(file?.available_quantity || 0),
            0,
        ),
        in_progress_pieces: files.reduce(
            (total, file) => total
                + Number(file?.sent_quantity || 0)
                + Number(file?.ready_quantity || 0),
            0,
        ),
        waiting_review_products: files.reduce(
            (total, file) => total + (file.products || []).filter(
                (product) => Number(product?.available_quantity || 0) > 0,
            ).length,
            0,
        ),
        in_progress_products: files.reduce(
            (total, file) => total + (file.products || []).filter(
                (product) => (
                    Number(product?.sent_quantity || 0)
                    + Number(product?.ready_quantity || 0)
                ) > 0,
            ).length,
            0,
        ),
    };
    return { ...data, files, summary };
}

export function supplierDispatchForPrint(dispatch = {}, suppliers = [], supplierId = "") {
    const selectedSupplier = suppliers.find(
        (supplier) => String(supplier?.id || "") === String(supplierId || ""),
    );
    return {
        ...dispatch,
        supplier_name: String(
            dispatch?.supplier_name || selectedSupplier?.company_name || "",
        ).trim() || "مورد غير محدد",
    };
}

function formatRiyadhDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}

function SummaryCard({ value, label, detail, tone = "slate", onClick }) {
    const styles = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        slate: "border-slate-200 bg-slate-50 text-slate-950",
    };
    const Tag = onClick ? "button" : "div";
    return (
        <Tag
            type={onClick ? "button" : undefined}
            onClick={onClick}
            className={`min-h-[112px] rounded-2xl border p-3 text-right ${styles[tone]} ${onClick ? "transition hover:-translate-y-0.5 hover:shadow-md" : ""}`}
        >
            <div className="flex items-start justify-between gap-2">
                <div className="text-3xl font-black tabular-nums">{Number(value || 0)}</div>
                {onClick && <ArrowRight size={18} className="rotate-180 opacity-60" />}
            </div>
            <div className="mt-1 text-xs font-black">{label}</div>
            {detail && <div className="mt-1 text-[10px] font-bold leading-5 opacity-70">{detail}</div>}
        </Tag>
    );
}

export function productImageUrl(product = {}) {
    return String(
        product?.selected_image_url
        || product?.resolved_image_url
        || product?.image_url
        || "",
    ).trim();
}

function ProductImage({ product, compact = false }) {
    const size = compact ? "h-12 w-12" : "aspect-square w-full";
    const imageUrl = productImageUrl(product);
    return imageUrl ? (
        <img
            src={imageUrl}
            alt={product?.product_name || "صورة المنتج"}
            className={`${size} rounded-xl border border-slate-200 bg-white ${compact ? "object-cover" : "object-contain"}`}
            data-testid={compact ? undefined : "dispatch-product-image"}
        />
    ) : (
        <div className={`flex ${size} items-center justify-center rounded-xl bg-slate-100 text-slate-400`}><Package size={24} /></div>
    );
}

function ProductOptionsMenu({ product, onReturn }) {
    return (
        <details className="group absolute right-2 top-2 z-20" data-testid="product-options-menu">
            <summary className="flex h-10 w-10 cursor-pointer list-none items-center justify-center rounded-full border border-slate-200 bg-white text-slate-900 shadow-md transition hover:bg-slate-50 [&::-webkit-details-marker]:hidden" aria-label={`خيارات ${product?.product_name || "المنتج"}`}>
                <DotsThreeVertical size={25} weight="bold" />
            </summary>
            <div className="absolute right-0 top-11 z-30 min-w-44 rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl">
                <button type="button" onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); onReturn(); }} className="flex min-h-10 w-full items-center gap-2 rounded-lg px-3 text-right text-xs font-black text-rose-700 transition hover:bg-rose-50">
                    <UserSwitch size={17} />
                    إرجاع الإسناد
                </button>
            </div>
        </details>
    );
}

export function dispatchSelectionState(product, value) {
    const available = Number(product?.available_quantity || 0);
    const current = Math.max(0, Math.min(available, Number(value || 0)));
    if (current <= 0 || available <= 0) return "unselected";
    return current >= available ? "full" : "partial";
}

export function toggledDispatchQuantity(product, value) {
    return dispatchSelectionState(product, value) === "unselected"
        ? Math.max(0, Number(product?.available_quantity || 0))
        : 0;
}

function ProductSelectionButton({ product, value, onChange }) {
    const state = dispatchSelectionState(product, value);
    const selected = state !== "unselected";
    const tone = state === "partial"
        ? "border-amber-500 bg-amber-400 text-white shadow-amber-200"
        : selected
            ? "border-emerald-600 bg-emerald-600 text-white shadow-emerald-200"
            : "border-slate-300 bg-white text-transparent shadow-slate-200";
    return (
        <button
            type="button"
            onClick={() => onChange(toggledDispatchQuantity(product, value))}
            aria-label={selected ? `إلغاء تحديد ${product?.product_name || "المنتج"}` : `تحديد ${product?.product_name || "المنتج"}`}
            aria-pressed={selected}
            className={`absolute left-2 top-2 z-20 flex h-10 w-10 items-center justify-center rounded-full border-2 shadow-md transition ${tone}`}
            data-testid="dispatch-product-selector"
        >
            <CheckCircle size={25} weight="fill" />
        </button>
    );
}

function SectionHeader({ title, description, onBack, onRefresh, loading }) {
    return (
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
                {onBack && <button type="button" onClick={onBack} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-slate-200 bg-white" aria-label="العودة إلى إدارة منتجاتي"><ArrowRight size={19} /></button>}
                <div className="min-w-0"><h3 className="text-lg font-black text-slate-950">{title}</h3><p className="mt-1 text-xs font-bold leading-5 text-slate-500">{description}</p></div>
            </div>
            {onRefresh && <button type="button" onClick={onRefresh} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black"><ArrowClockwise className={loading ? "animate-spin" : ""} />تحديث</button>}
        </div>
    );
}

export function orderSearchValueFromBarcode(rawValue) {
    const value = String(rawValue || "").trim();
    if (!value) return "";
    const numberGroups = value.match(/[0-9]{5,}/g) || [];
    if (!numberGroups.length) return value;
    return numberGroups.sort((left, right) => right.length - left.length)[0];
}

export function OrderBarcodeCameraScanner({ onDetected, onClose }) {
    const videoRef = useRef(null);
    const [cameraError, setCameraError] = useState("");
    const [cameraReady, setCameraReady] = useState(false);
    const [cameraEngine, setCameraEngine] = useState("");

    useEffect(() => {
        let stopped = false;
        let stream;
        let animationFrame;
        let zxingControls;

        const finish = (rawValue) => {
            const value = orderSearchValueFromBarcode(rawValue);
            if (!value || stopped) return;
            stopped = true;
            onDetected(value);
        };

        const createNativeDetector = async () => {
            if (!globalThis.BarcodeDetector) return null;
            try {
                const supported = typeof globalThis.BarcodeDetector.getSupportedFormats === "function"
                    ? await globalThis.BarcodeDetector.getSupportedFormats()
                    : [];
                const formats = ["qr_code", "code_128"].filter((format) => !supported.length || supported.includes(format));
                return new globalThis.BarcodeDetector(formats.length ? { formats } : undefined);
            } catch {
                return null;
            }
        };

        async function startCamera() {
            if (!navigator.mediaDevices?.getUserMedia) {
                setCameraError("هذا الجهاز لا يتيح الكاميرا للمتصفح. افتح ميزان عبر HTTPS واسمح باستخدام الكاميرا.");
                return;
            }
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    video: {
                        facingMode: { ideal: "environment" },
                        width: { ideal: 1280 },
                        height: { ideal: 720 },
                    },
                    audio: false,
                });
                if (stopped || !videoRef.current) {
                    for (const track of stream?.getTracks?.() || []) track.stop();
                    return;
                }
                videoRef.current.srcObject = stream;
                await videoRef.current.play();
                if (stopped) return;
                setCameraReady(true);

                const detector = await createNativeDetector();
                if (detector) {
                    setCameraEngine("native");
                    const detectFrame = async () => {
                        if (stopped || !videoRef.current) return;
                        try {
                            const rows = await detector.detect(videoRef.current);
                            finish(rows?.[0]?.rawValue);
                        } catch {
                            // Moving frames commonly contain no readable barcode.
                        }
                        if (!stopped) animationFrame = requestAnimationFrame(detectFrame);
                    };
                    animationFrame = requestAnimationFrame(detectFrame);
                    return;
                }

                setCameraEngine("zxing");
                const { BarcodeFormat, BrowserMultiFormatReader } = await import("@zxing/browser");
                const reader = new BrowserMultiFormatReader(undefined, {
                    delayBetweenScanAttempts: 180,
                    delayBetweenScanSuccess: 500,
                });
                reader.possibleFormats = [BarcodeFormat.QR_CODE, BarcodeFormat.CODE_128];
                zxingControls = await reader.decodeFromVideoElement(videoRef.current, (result) => {
                    if (result) finish(result.getText());
                });
                if (stopped) zxingControls?.stop?.();
            } catch (cameraStartError) {
                const messages = {
                    NotAllowedError: "اسمح لميزان باستخدام الكاميرا من إعدادات المتصفح ثم حاول مرة أخرى.",
                    NotFoundError: "لم يتم العثور على كاميرا في هذا الجهاز.",
                    NotReadableError: "الكاميرا مستخدمة في تطبيق آخر. أغلقه ثم حاول مرة أخرى.",
                    SecurityError: "تشغيل الكاميرا يحتاج فتح ميزان عبر اتصال آمن HTTPS.",
                };
                setCameraError(messages[cameraStartError?.name] || "تعذّر تشغيل الكاميرا أو قراءة الباركود.");
            }
        }

        startCamera();
        return () => {
            stopped = true;
            if (animationFrame) cancelAnimationFrame(animationFrame);
            zxingControls?.stop?.();
            for (const track of stream?.getTracks?.() || []) track.stop();
        };
    }, [onDetected]);

    return (
        <div className="fixed inset-0 z-[120] flex items-center justify-center bg-slate-950/90 p-3" role="dialog" aria-modal="true" aria-label="مسح باركود رقم الطلب" dir="rtl" data-testid="my-products-order-camera-dialog">
            <div className="w-full max-w-xl overflow-hidden rounded-3xl bg-white shadow-2xl">
                <header className="flex items-center justify-between gap-3 bg-emerald-800 p-4 text-white">
                    <div><h3 className="flex items-center gap-2 text-lg font-black"><Camera size={23} /> مسح باركود الطلب</h3><p className="mt-1 text-xs font-bold text-emerald-100">وجّه الكاميرا إلى QR أو Code 128 الخاص بالطلب.</p></div>
                    <button type="button" onClick={onClose} className="flex h-10 w-10 items-center justify-center rounded-xl bg-white/15" aria-label="إغلاق الكاميرا"><X size={20} /></button>
                </header>
                <div className="p-4">
                    {cameraError ? <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-black leading-6 text-amber-900">{cameraError}</div> : (
                        <div className="relative aspect-[4/3] overflow-hidden rounded-2xl bg-black" data-camera-engine={cameraEngine || undefined}>
                            <video ref={videoRef} muted playsInline className="h-full w-full object-cover" />
                            {!cameraReady && <div className="absolute inset-0 flex items-center justify-center gap-2 text-sm font-black text-white"><SpinnerGap className="animate-spin" /> جارٍ تشغيل الكاميرا…</div>}
                            {cameraReady && <div className="pointer-events-none absolute inset-[18%] rounded-2xl border-2 border-emerald-400 shadow-[0_0_0_999px_rgba(0,0,0,0.28)]" />}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}

function ReturnAssignmentDialog({ target, reason, onReasonChange, busy, onCancel, onConfirm }) {
    if (!target) return null;
    const valid = String(reason || "").trim().length >= 3;
    return (
        <div className="fixed inset-0 z-[120] flex items-end justify-center bg-slate-950/60 p-3 sm:items-center" role="dialog" aria-modal="true" aria-label="إرجاع إسناد المنتج للمدير">
            <div className="w-full max-w-lg rounded-2xl bg-white p-5 shadow-2xl" dir="rtl">
                <div className="flex items-start justify-between gap-3"><div><h3 className="text-lg font-black text-slate-950">تأكيد إرجاع الإسناد</h3><p className="mt-1 text-xs font-bold leading-5 text-slate-500">بعد التأكيد ينتقل كامل المتبقي من {target.product.product_name} وعدده {target.product.available_quantity} قطعة إلى «منتجات غير مسندة» لدى المدير.</p></div><button type="button" onClick={onCancel} disabled={busy} className="flex h-9 w-9 items-center justify-center rounded-xl bg-slate-100"><X size={18} /></button></div>
                <label className="mt-4 block text-sm font-black text-slate-800">ملاحظة سبب إرجاع الإسناد <span className="text-rose-600">*</span><textarea value={reason} onChange={(event) => onReasonChange(event.target.value)} rows={4} maxLength={1000} placeholder="اكتب السبب بوضوح ليتمكن المدير من إعادة إسناده للموظف المناسب" className="mt-2 w-full resize-none rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-rose-500" /></label>
                {!valid && <div className="mt-1 text-xs font-bold text-rose-600">كتابة السبب إلزامية.</div>}
                <div className="mt-4 grid grid-cols-2 gap-2"><button type="button" onClick={onCancel} disabled={busy} className="min-h-11 rounded-xl border border-slate-200 font-black">إلغاء</button><button type="button" onClick={onConfirm} disabled={!valid || busy} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-rose-700 font-black text-white disabled:opacity-50">{busy ? <SpinnerGap className="animate-spin" /> : <UserSwitch size={19} />}تأكيد الإرجاع</button></div>
            </div>
        </div>
    );
}

export function WaitingReviewView({
    data,
    loading,
    error,
    onRefresh,
    onChanged,
    onDispatchSaved = () => {},
    onBack,
    title = "بانتظار المراجعة",
    description = "القطع المسندة إليك؛ كل بطاقة تمثل قطعة واحدة لم تُرسل إلى مورد بعد.",
}) {
    const files = (data?.files || []).filter((file) => file.available_quantity > 0);
    const suppliers = Array.isArray(data?.suppliers) ? data.suppliers : [];
    const [selected, setSelected] = useState({});
    const [supplierId, setSupplierId] = useState("");
    const [busyFile, setBusyFile] = useState("");
    const [actionError, setActionError] = useState("");
    const [notice, setNotice] = useState("");
    const [returnTarget, setReturnTarget] = useState(null);
    const [returnReason, setReturnReason] = useState("");

    const fileSelection = (file) => dispatchSelections(file.products, selected[file.file_number] || {});
    const setQuantity = (fileNumber, groupKey, quantity) => setSelected((current) => ({ ...current, [fileNumber]: { ...(current[fileNumber] || {}), [groupKey]: quantity } }));
    const selectedFiles = selectedFileDispatches(files, selected);
    const selectedQuantity = selectedFiles.reduce(
        (total, file) => total + file.selections.reduce(
            (fileTotal, selection) => fileTotal + selection.quantity,
            0,
        ),
        0,
    );

    const send = async () => {
        if (!selectedFiles.length || !supplierId || busyFile) return;
        const printWindow = globalThis.window?.open?.("", "_blank") || null;
        const savedSelections = selectedFiles.map((file) => ({
            ...file,
            selections: file.selections.map((selection) => ({ ...selection })),
        }));
        let shouldRefresh = false;
        setBusyFile("supplier-file");
        setActionError("");
        setNotice("");
        try {
            const response = await sendPreparationPiecesToSupplier({
                client_request_id: newPreparationDispatchRequestId(),
                supplier_id: supplierId,
                files: selectedFiles,
                note: null,
            });
            const dispatch = supplierDispatchForPrint(
                response.dispatch,
                suppliers,
                supplierId,
            );
            shouldRefresh = true;
            onDispatchSaved(savedSelections, dispatch);
            let printed = false;
            try {
                printed = printSupplierDispatch(dispatch, printWindow);
            } catch {
                printWindow?.close?.();
            }
            const completedFiles = Array.isArray(dispatch?.completed_source_file_numbers)
                ? dispatch.completed_source_file_numbers
                : [];
            setSelected({});
            setSupplierId("");
            const completionNotice = completedFiles.length
                ? ` اكتمل رفع ${completedFiles.length} ملف تجهيز وتحولت حالته إلى «قيد التنفيذ».`
                : " بقيت حالة الملف بانتظار اكتمال رفع جميع منتجاته.";
            setNotice(
                `${printed ? "تم حفظ ملف المورد وفتح نافذة الطباعة." : "تم حفظ ملف المورد، لكن المتصفح منع نافذة الطباعة. يمكنك إعادة طباعته من قيد التنفيذ."}${completionNotice}`,
            );
        } catch (sendError) {
            printWindow?.close?.();
            setActionError(sendError.message || "تعذّر حفظ ملف المورد.");
        } finally {
            setBusyFile("");
            if (shouldRefresh) {
                void Promise.resolve()
                    .then(() => onChanged())
                    .catch((refreshError) => {
                        setActionError(
                            refreshError?.message
                            || "تم حفظ ملف المورد، لكن تعذّر تحديث القائمة تلقائيًا. اضغط تحديث.",
                        );
                    });
            }
        }
    };

    const confirmReturn = async () => {
        if (!returnTarget || String(returnReason).trim().length < 3 || busyFile) return;
        const { file, product } = returnTarget;
        setBusyFile(file.file_number);
        setActionError("");
        try {
            await rejectPreparationPieces({
                client_request_id: newPreparationDispatchRequestId("preparation-return"),
                file_number: file.file_number,
                selections: [{ group_key: product.group_key, quantity: Number(product.available_quantity || 0) }],
                reason: returnReason.trim(),
            });
            setReturnTarget(null);
            setReturnReason("");
            await onChanged();
        } catch (rejectError) {
            setActionError(rejectError.message || "تعذّر إرجاع الإسناد للمدير.");
        } finally {
            setBusyFile("");
        }
    };

    return (
        <div className="space-y-5" data-testid="preparation-waiting-review-products">
            <SectionHeader title={title} description={description} onBack={onBack} onRefresh={onRefresh} loading={loading} />
            {(error || actionError) && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{actionError || error}</div>}
            {notice && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-900">{notice}</div>}
            {!files.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات بانتظار الإرسال للمورد</div></div> : (
                <div className="space-y-4">
                    {files.map((file) => {
                        const selections = fileSelection(file);
                        const fileSelectedQuantity = selections.reduce((sum, row) => sum + row.quantity, 0);
                        return (
                            <article key={file.file_number} className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                                <header className="bg-violet-50 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><h4 className="font-black text-slate-950">{file.file_number}</h4><div className="mt-1 text-xs font-black text-violet-700">تاريخ الرفع: {formatRiyadhDate(file.registered_at)}</div>{file.file_title && file.file_title !== file.file_number && <div className="mt-1 text-xs font-bold text-slate-500">{file.file_title}</div>}<div className="mt-2 text-xs font-bold text-slate-600">{file.available_quantity} قطعة بانتظار الرفع · {file.sent_quantity} قطعة مرفوعة للمورد</div></div><div className="rounded-xl bg-white px-3 py-2 text-xs font-black text-violet-900">المحدد: {fileSelectedQuantity} قطعة</div></div></header>
                                <div className="grid grid-cols-2 gap-2 p-2 sm:gap-3 sm:p-4 lg:grid-cols-4" data-testid="dispatch-product-grid">
                                    {(file.products || []).filter((product) => product.available_quantity > 0).map((product) => {
                                        const value = selected[file.file_number]?.[product.group_key] || 0;
                                        const selectionState = dispatchSelectionState(product, value);
                                        const selectionTone = selectionState === "full"
                                            ? "border-emerald-500 bg-emerald-50/50 ring-2 ring-emerald-100"
                                            : selectionState === "partial"
                                                ? "border-amber-400 bg-amber-50/60 ring-2 ring-amber-100"
                                                : "border-slate-200 bg-white";
                                        return (
                                            <article
                                                key={product.group_key}
                                                className={`min-w-0 rounded-2xl border p-2.5 transition ${selectionTone}`}
                                                data-selection-state={selectionState}
                                                data-piece-id={product.piece_id || undefined}
                                            >
                                                <div className="relative">
                                                    <ProductImage product={product} />
                                                    <ProductSelectionButton product={product} value={value} onChange={(quantity) => setQuantity(file.file_number, product.group_key, quantity)} />
                                                    <ProductOptionsMenu product={product} onReturn={() => { setReturnTarget({ file, product }); setReturnReason(""); }} />
                                                </div>
                                                <div className="mt-2 min-w-0"><div className="line-clamp-2 min-h-10 text-xs font-black leading-5 text-slate-900 sm:text-sm">{product.product_name}</div><div className="mt-1 truncate text-[10px] font-bold text-slate-500">{product.sku || "بدون SKU"} · قطعة واحدة{product.order_numbers?.[0] ? ` · طلب ${product.order_numbers[0]}` : ""}</div></div>
                                                <div className="mt-2 flex min-h-6 flex-wrap gap-1">{(product.services || []).filter((service) => service.status !== "completed").slice(0, 2).map((service) => <span key={service.service_id} className="rounded-full bg-amber-50 px-1.5 py-1 text-[9px] font-black text-amber-800">{service.service_name || "خدمة"}</span>)}</div>
                                                <div className="mt-2"><CustomerServiceInstructionBanner instructions={product.customer_service_instructions || []} stage="supplier_dispatch" onUpdated={onChanged} /></div>
                                            </article>
                                        );
                                    })}
                                </div>
                            </article>
                        );
                    })}
                    <section className="sticky bottom-3 z-30 grid gap-3 rounded-2xl border border-violet-200 bg-white/95 p-4 shadow-xl backdrop-blur lg:grid-cols-[minmax(240px,1fr)_auto]" data-testid="multi-file-supplier-dispatch">
                        <div>
                            <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} className="min-h-12 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-violet-500"><option value="">اختر المورد لكل المنتجات المحددة</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name}</option>)}</select>
                            <div className="mt-2 text-xs font-bold text-slate-600">{selectedQuantity} قطعة محددة من {selectedFiles.length} ملف تجهيز — ستُحفظ في ملف مورد واحد مع بقاء كل ملف في بلوك مستقل.</div>
                        </div>
                        <button type="button" onClick={send} disabled={!selectedFiles.length || !supplierId || Boolean(busyFile)} className="inline-flex min-h-12 items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-50">{busyFile === "supplier-file" ? <SpinnerGap className="animate-spin" /> : <Printer size={20} weight="fill" />}حفظ وطباعة ملف المورد</button>
                    </section>
                </div>
            )}
            <ReturnAssignmentDialog target={returnTarget} reason={returnReason} onReasonChange={setReturnReason} busy={Boolean(busyFile)} onCancel={() => { setReturnTarget(null); setReturnReason(""); }} onConfirm={confirmReturn} />
        </div>
    );
}

function InProgressView({ data, loading, error, onRefresh, onBack }) {
    const accounts = (data?.supplier_accounts || []).filter((account) => (account.sent_quantity + account.ready_quantity) > 0);
    return (
        <div className="space-y-5" data-testid="preparation-products-in-progress">
            <SectionHeader title="قيد التنفيذ" description="الموردون والمنتجات الموجودة لديهم حاليًا." onBack={onBack} onRefresh={onRefresh} loading={loading} />
            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {!accounts.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><Storefront size={36} className="mx-auto text-slate-400" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات عند الموردين حاليًا</div></div> : <div className="grid gap-4 xl:grid-cols-2">{accounts.map((account) => {
                const currentProducts = (account.products || []).filter((product) => (product.sent_quantity + product.ready_quantity) > 0);
                return <article key={account.supplier_id} className="overflow-hidden rounded-2xl border border-amber-200 bg-white shadow-sm"><header className="bg-amber-50 p-4"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-amber-700 text-white"><Storefront size={24} /></span><div className="min-w-0 flex-1"><h4 className="truncate font-black text-slate-950">{account.supplier_name}</h4><p className="mt-1 text-xs font-bold text-amber-800">{account.sent_quantity + account.ready_quantity} قطعة قيد التنفيذ</p></div></div></header><div className="grid grid-cols-2 gap-2 p-3">{currentProducts.map((product) => <div key={product.group_key} className="rounded-xl border border-slate-200 p-2"><div className="flex items-center gap-2"><ProductImage product={product} compact /><div className="min-w-0"><div className="line-clamp-2 text-xs font-black text-slate-900">{product.product_name}</div><div className="mt-1 text-[10px] font-bold text-slate-500">{product.sent_quantity + product.ready_quantity} قطعة</div></div></div></div>)}</div><footer className="space-y-2 border-t border-slate-100 bg-slate-50 p-3">{(account.dispatches || []).filter((dispatch) => ["sent", "ready"].includes(dispatch.status)).map((dispatch) => <div key={dispatch.id} className="flex items-center justify-between gap-2 rounded-xl border border-slate-200 bg-white p-3"><div className="min-w-0 text-xs font-bold text-slate-600"><b className="text-slate-900">{dispatch.supplier_file_number || dispatch.file_number}</b> · {dispatch.piece_count} قطعة · {(dispatch.source_file_numbers || []).length || 1} ملف تجهيز</div><button type="button" onClick={() => printSupplierDispatch(supplierDispatchForPrint(dispatch, [{ id: account.supplier_id, company_name: account.supplier_name }], account.supplier_id))} className="inline-flex min-h-9 shrink-0 items-center gap-1 rounded-lg border border-violet-200 px-2 text-[11px] font-black text-violet-700"><Printer size={16} />إعادة الطباعة</button></div>)}</footer></article>;
            })}</div>}
        </div>
    );
}

export function ReceivedView({ data, loading, error, onRefresh, onBack }) {
    const receivedProducts = (data?.files || []).flatMap((file) => (file?.products || [])
        .filter((product) => Number(product?.received_quantity || 0) > 0)
        .map((product) => ({ ...product, file_number: file.file_number })));
    return (
        <div className="space-y-5" data-testid="preparation-products-received">
            <SectionHeader title="تم الاستلام" description="ما استلمه موظف التجهيز من المورد ولم يُسلّمه لموظف الاستلام بالفرع." onBack={onBack} onRefresh={onRefresh} loading={loading} />
            <div className="grid grid-cols-2 gap-3"><SummaryCard value={data?.summary?.received_orders_awaiting_branch_handoff} label="الطلبات" detail="بانتظار التسليم للفرع" tone="emerald" /><SummaryCard value={data?.summary?.received_pieces_awaiting_branch_handoff} label="القطع المستلمة" detail="لم تُسلّم للفرع" tone="violet" /></div>
            {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}
            {!receivedProducts.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد قطع مستلمة بانتظار التسليم للفرع</div></div> : <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{receivedProducts.map((product) => <article key={`${product.file_number}:${product.group_key}`} className="rounded-2xl border border-emerald-200 bg-emerald-50 p-3"><ProductImage product={product} /><h4 className="mt-2 line-clamp-2 text-sm font-black text-slate-950">{product.product_name}</h4><div className="mt-1 text-xs font-bold text-emerald-800">{product.received_quantity} قطعة مستلمة</div><div className="mt-1 truncate text-[10px] font-bold text-slate-500">ملف {product.file_number}</div></article>)}</div>}
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900"><WarningCircle size={19} className="mt-0.5 shrink-0" />إنقاص هذا العدد سيتم فقط في مرحلة تسليم القطع لموظف الاستلام بالفرع بالباركود، وهي بوابة التنفيذ التالية.</div>
        </div>
    );
}

export function MyProductsOverview({ data, onOpen }) {
    const [search, setSearch] = useState("");
    const [cameraOpen, setCameraOpen] = useState(false);
    const handleBarcodeDetected = useCallback((value) => {
        setSearch(orderSearchValueFromBarcode(value));
        setCameraOpen(false);
    }, []);
    const normalizedSearch = search.trim();
    const files = Array.isArray(data?.files) ? data.files : [];
    const supplierAccounts = Array.isArray(data?.supplier_accounts)
        ? data.supplier_accounts
        : [];
    const matches = normalizedSearch
        ? files.flatMap((file) => (file.products || [])
            .filter((product) => (product.order_numbers || [])
                .some((order) => String(order).includes(normalizedSearch)))
            .map((product) => ({ ...product, file_number: file.file_number })))
        : [];
    const latestFiles = files.slice(0, 3);
    const activeSupplierAccounts = supplierAccounts
        .filter((account) => (
            Number(account?.sent_quantity || 0)
            + Number(account?.ready_quantity || 0)
            + Number(account?.received_quantity || 0)
        ) > 0)
        .slice(0, 3);
    const supplierInvoiceCount = supplierAccounts.reduce(
        (total, account) => total + (Array.isArray(account?.dispatches) ? account.dispatches.length : 0),
        0,
    );

    const summaryCards = [
        {
            key: "waiting",
            label: "بانتظار المراجعة",
            value: data?.summary?.waiting_review_pieces,
            detail: "قطعة لم تُرسل للمورد",
            Icon: ClipboardText,
            tone: "amber",
            onClick: () => onOpen("waiting-review"),
        },
        {
            key: "progress",
            label: "قيد التنفيذ",
            value: data?.summary?.in_progress_pieces,
            detail: "قطعة لدى الموردين",
            Icon: ArrowClockwise,
            tone: "blue",
            onClick: () => onOpen("in-progress"),
        },
        {
            key: "received",
            label: "تم الاستلام",
            value: data?.summary?.received_pieces_awaiting_branch_handoff,
            detail: "قطعة لم تُسلّم للفرع",
            Icon: CheckCircle,
            tone: "emerald",
            onClick: () => onOpen("received"),
        },
        {
            key: "total",
            label: "إجمالي القطع المسندة",
            value: data?.summary?.total_assigned_pieces,
            detail: "قطعة في جميع الحالات",
            Icon: ClipboardText,
            tone: "green",
        },
    ];

    const summaryTone = {
        amber: {
            value: "text-amber-600",
            icon: "bg-amber-50 text-amber-600",
            hover: "hover:border-amber-200 hover:bg-amber-50/30",
        },
        blue: {
            value: "text-blue-600",
            icon: "bg-blue-50 text-blue-600",
            hover: "hover:border-blue-200 hover:bg-blue-50/30",
        },
        emerald: {
            value: "text-emerald-700",
            icon: "bg-emerald-50 text-emerald-700",
            hover: "hover:border-emerald-200 hover:bg-emerald-50/30",
        },
        green: {
            value: "text-emerald-800",
            icon: "bg-emerald-50 text-emerald-800",
            hover: "",
        },
    };

    const fileProgress = (file) => {
        const total = Math.max(0, Number(file?.piece_count || 0));
        const received = Math.max(0, Number(file?.received_quantity || 0));
        return total ? Math.min(100, Math.round((received / total) * 100)) : 0;
    };

    const fileStatus = (file) => {
        const executionStatus = String(file?.execution_status || "assigned");
        const total = Math.max(0, Number(file?.piece_count || 0));
        const received = Math.max(0, Number(file?.received_quantity || 0));
        if (total > 0 && received >= total) {
            return { label: "تم الاستلام", className: "text-emerald-700" };
        }
        if (executionStatus === "in_progress") {
            return { label: "قيد التنفيذ", className: "text-blue-600" };
        }
        return { label: "بانتظار المراجعة", className: "text-amber-600" };
    };

    const SupplierIcon = ({ index }) => {
        const icons = [Buildings, Storefront, Factory];
        const Icon = icons[index % icons.length];
        return <Icon size={31} weight="duotone" />;
    };

    const SectionTitle = ({ children }) => (
        <div className="flex items-center gap-2">
            <span className="h-6 w-1 rounded-full bg-amber-500" aria-hidden="true" />
            <h3 className="text-base font-black text-slate-950 sm:text-lg">{children}</h3>
        </div>
    );

    return (
        <div className="mx-auto w-full max-w-[1180px] space-y-6 bg-white pb-4" data-testid="preparation-my-products-overview">
            <header className="text-center">
                <h1 className="text-3xl font-black tracking-tight text-emerald-800 sm:text-4xl">إدارة منتجاتي</h1>
                <p className="mt-1 text-xs font-bold text-slate-500 sm:text-base">إدارة المنتجات المسندة لك ومتابعة الموردين</p>
            </header>

            <section className="space-y-3" aria-labelledby="my-products-summary-title">
                <div id="my-products-summary-title"><SectionTitle>ملخص العمل العام</SectionTitle></div>
                <div className="grid grid-cols-2 gap-2.5 sm:gap-4 lg:grid-cols-4">
                    {summaryCards.map((card) => {
                        const Tag = card.onClick ? "button" : "div";
                        const tone = summaryTone[card.tone];
                        return (
                            <Tag
                                key={card.key}
                                type={card.onClick ? "button" : undefined}
                                onClick={card.onClick}
                                className={`min-h-[112px] rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition sm:min-h-[128px] sm:p-4 ${card.onClick ? tone.hover : ""}`}
                                data-testid={`my-products-summary-${card.key}`}
                            >
                                <div className="flex items-start justify-between gap-2">
                                    <div className="min-w-0">
                                        <div className="text-[11px] font-black leading-5 text-slate-900 sm:text-sm">{card.label}</div>
                                        <div className={`mt-0.5 text-3xl font-black tabular-nums sm:text-4xl ${tone.value}`}>{Number(card.value || 0)}</div>
                                    </div>
                                    <span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-full sm:h-12 sm:w-12 ${tone.icon}`}><card.Icon size={24} weight="duotone" /></span>
                                </div>
                                <div className="mt-1 text-[10px] font-bold leading-5 text-slate-500 sm:text-xs">{card.detail}</div>
                            </Tag>
                        );
                    })}
                </div>
            </section>

            <section className="grid grid-cols-2 gap-2.5 sm:gap-4" aria-label="إجراءات إدارة منتجاتي">
                <button type="button" onClick={() => onOpen("waiting-review")} className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-amber-200 hover:bg-amber-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-open-waiting-review">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">بانتظار المراجعة</div><div className="mt-1 text-[10px] font-bold text-slate-500 sm:text-sm">{Number(data?.summary?.waiting_review_pieces || 0)} قطعة غير مرسلة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-amber-50 text-amber-600 sm:h-14 sm:w-14"><ClipboardText size={28} weight="duotone" /></span>
                </button>
                <button type="button" onClick={() => onOpen("supplier-receiving")} className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-emerald-200 hover:bg-emerald-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-receive-from-supplier">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">استلام من المورد</div><div className="mt-1 text-[10px] font-bold text-slate-500 sm:text-sm">مسح باركود وفتح الفاتورة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-14 sm:w-14"><Camera size={28} weight="fill" /></span>
                </button>
                <div className="min-h-[112px] rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm sm:min-h-[120px] sm:p-5" data-testid="my-products-order-search">
                    <div className="flex items-center justify-between gap-3">
                        <div className="text-sm font-black text-slate-950 sm:text-xl">البحث برقم الطلب</div>
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-12 sm:w-12"><MagnifyingGlass size={26} /></span>
                    </div>
                    <label className="mt-2 flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-2 focus-within:border-emerald-500 sm:min-h-11">
                        <input value={search} onChange={(event) => setSearch(event.target.value)} inputMode="numeric" placeholder="أدخل رقم الطلب" className="min-w-0 flex-1 bg-transparent text-xs font-bold outline-none sm:text-sm" aria-label="البحث برقم الطلب" />
                        <button type="button" onClick={() => setCameraOpen(true)} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-emerald-800 transition hover:bg-emerald-50" aria-label="فتح الكاميرا لمسح باركود الطلب" data-testid="my-products-order-camera-button"><Camera size={19} /></button>
                    </label>
                </div>
                <button type="button" onClick={() => onOpen("supplier-receiving")} className="flex min-h-[112px] items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-3 text-right shadow-sm transition hover:border-emerald-200 hover:bg-emerald-50/30 sm:min-h-[120px] sm:p-5" data-testid="my-products-supplier-invoices">
                    <div><div className="text-sm font-black text-slate-950 sm:text-xl">فواتير الموردين</div><div className="mt-1 text-[10px] font-black text-emerald-700 sm:text-sm">{supplierInvoiceCount} فاتورة</div></div>
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-emerald-50 text-emerald-700 sm:h-14 sm:w-14"><FileText size={28} weight="fill" /></span>
                </button>
            </section>

            {normalizedSearch && (
                <section className="rounded-xl border border-emerald-100 bg-emerald-50/30 p-3" aria-live="polite">
                    <div className="mb-2 text-xs font-black text-emerald-900">نتائج البحث</div>
                    {matches.length ? <div className="grid gap-2 sm:grid-cols-2">{matches.map((product) => <div key={`${product.file_number}:${product.group_key}`} className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3"><ProductImage product={product} compact /><div className="min-w-0"><div className="text-sm font-black text-slate-950">{product.product_name}</div><div className="mt-1 text-[11px] font-bold text-slate-500">ملف {product.file_number} · متاح {product.available_quantity} · عند المورد {Number(product.sent_quantity || 0) + Number(product.ready_quantity || 0)} · مستلم {product.received_quantity}</div></div></div>)}</div> : <div className="text-xs font-bold text-slate-500">لا توجد منتجات مسندة إليك لهذا الطلب.</div>}
                </section>
            )}

            <section id="latest-preparation-files" className="space-y-3">
                <SectionTitle>آخر ملفات التجهيز</SectionTitle>
                {latestFiles.length ? (
                    <div className="grid grid-cols-3 gap-2 sm:gap-4">
                        {latestFiles.map((file) => {
                            const progress = fileProgress(file);
                            const status = fileStatus(file);
                            return (
                                <article key={file.file_number} className="min-w-0 rounded-xl border border-slate-200 bg-white p-2.5 shadow-sm sm:p-4" data-testid="my-products-latest-file">
                                    <div className="truncate text-[10px] font-black text-slate-950 sm:text-sm">{file.file_title || file.file_number}</div>
                                    <div className="mt-1 text-[9px] font-bold text-slate-600 sm:text-xs">{Number(file.piece_count || 0)} قطعة</div>
                                    <div className="mt-1 text-[9px] font-bold text-slate-500 sm:text-xs">{Number(file.received_quantity || 0)} مستلمة</div>
                                    <div className="mt-2 flex items-center gap-2" dir="ltr"><span className="text-[9px] font-black text-slate-700 sm:text-xs">{progress}%</span><span className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-slate-200"><span className="block h-full rounded-full bg-emerald-700" style={{ width: `${progress}%` }} /></span></div>
                                    <div className={`mt-2 text-[9px] font-black sm:text-xs ${status.className}`}>{status.label}</div>
                                </article>
                            );
                        })}
                    </div>
                ) : <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm font-bold text-slate-500">لا توجد ملفات مسندة إليك.</div>}
            </section>

            <section className="space-y-3">
                <SectionTitle>حالة الموردين</SectionTitle>
                {activeSupplierAccounts.length ? (
                    <div className="grid grid-cols-3 gap-2 sm:gap-4">
                        {activeSupplierAccounts.map((account, index) => {
                            const activePieces = Number(account.sent_quantity || 0) + Number(account.ready_quantity || 0);
                            return (
                                <article key={account.supplier_id} className="flex min-w-0 items-center justify-between gap-2 rounded-xl border border-emerald-100 bg-emerald-50/50 p-2.5 sm:p-4" data-testid="my-products-supplier-status">
                                    <div className="min-w-0"><div className="truncate text-[9px] font-black text-slate-950 sm:text-sm">{account.supplier_name}</div><div className="mt-1 text-lg font-black tabular-nums text-emerald-800 sm:text-2xl">{activePieces}</div></div>
                                    <span className="shrink-0 text-emerald-700"><SupplierIcon index={index} /></span>
                                </article>
                            );
                        })}
                    </div>
                ) : <div className="rounded-xl border border-dashed border-slate-300 p-5 text-center text-xs font-bold text-slate-500">لا توجد منتجات نشطة لدى الموردين حاليًا.</div>}
            </section>
            {cameraOpen && <OrderBarcodeCameraScanner onDetected={handleBarcodeDetected} onClose={() => setCameraOpen(false)} />}
        </div>
    );
}

function UnassignedManagerView({ onChanged }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [employeeByGroup, setEmployeeByGroup] = useState({});
    const [busy, setBusy] = useState("");
    const load = useCallback(async () => { setLoading(true); setError(""); try { setData(await getUnassignedPreparationPieces()); } catch (loadError) { setError(loadError.message || "تعذّر تحميل غير المسندة."); } finally { setLoading(false); } }, []);
    useEffect(() => { load(); }, [load]);
    const assign = async (item) => {
        const key = `${item.file_number}:${item.group_key}:${item.rejection_id || "return"}`;
        const employeeId = employeeByGroup[key] || "";
        if (!employeeId || busy) return;
        setBusy(key); setError("");
        try { await reassignPreparationPieces({ client_request_id: newPreparationDispatchRequestId("preparation-reassign"), piece_ids: item.piece_ids, responsible_employee_id: employeeId, note: `إعادة إسناد من الملف ${item.file_number}` }); await Promise.all([load(), onChanged()]); } catch (assignError) { setError(assignError.message || "تعذّر إعادة الإسناد."); } finally { setBusy(""); }
    };
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل المنتجات غير المسندة…</div>;
    const items = data?.items || [];
    return <div className="space-y-5" data-testid="preparation-unassigned-manager-queue"><div className="grid grid-cols-2 gap-3"><SummaryCard value={data?.summary?.unassigned_products} label="منتجات غير مسندة" tone="amber" /><SummaryCard value={data?.summary?.unassigned_pieces} label="إجمالي القطع" tone="violet" /></div><SectionHeader title="منتجات غير مسندة" description="يعرض الموظف الذي أعاد الإسناد وملاحظته، ويحفظ السجل السابق دون حذف." onRefresh={load} loading={loading} />{error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-900">{error}</div>}{!items.length && !error ? <div className="rounded-2xl border border-dashed border-slate-300 p-9 text-center"><CheckCircle size={36} className="mx-auto text-emerald-600" /><div className="mt-3 font-black text-slate-800">لا توجد منتجات غير مسندة</div></div> : <div className="space-y-3">{items.map((item) => { const key = `${item.file_number}:${item.group_key}:${item.rejection_id || "return"}`; return <article key={key} className="grid gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 lg:grid-cols-[72px_minmax(0,1fr)_minmax(220px,320px)_auto] lg:items-center"><ProductImage product={item} compact /><div><div className="font-black text-slate-950">{item.product_name}</div><div className="mt-1 text-xs font-bold text-slate-600">الملف {item.file_number} · {item.quantity} قطعة</div><div className="mt-1 text-xs font-black text-slate-800">أعاد الإسناد: {item.rejected_by_employee_name || "موظف"}</div><div className="mt-1 text-[11px] font-bold text-slate-500">{formatRiyadhDate(item.rejected_at)}</div><div className="mt-2 rounded-lg border border-rose-200 bg-white px-3 py-2 text-xs font-black leading-5 text-rose-700">الملاحظة: {item.rejection_reason}</div></div><select value={employeeByGroup[key] || ""} onChange={(event) => setEmployeeByGroup((current) => ({ ...current, [key]: event.target.value }))} className="min-h-11 rounded-xl border border-amber-200 bg-white px-3 text-sm font-black"><option value="">اختر الموظف الجديد</option>{(data?.employees || []).map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}</select><button type="button" onClick={() => assign(item)} disabled={!employeeByGroup[key] || busy === key} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 text-sm font-black text-white disabled:opacity-50">{busy === key ? <SpinnerGap className="animate-spin" /> : <UserSwitch size={19} />}إعادة الإسناد</button></article>; })}</div>}</div>;
}

function EmployeeProductsWorkspace({ onDataChanged, initialSection = "overview" }) {
    const [data, setData] = useState(null);
    const [section, setSection] = useState(initialSection);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const load = useCallback(async () => { setLoading(true); setError(""); try { setData(await getPreparationSupplierWorkspace({ limit: 200 })); } catch (loadError) { setError(loadError.message || "تعذّر تحميل إدارة منتجاتي."); } finally { setLoading(false); } }, []);
    useEffect(() => { load(); }, [load]);
    const changed = useCallback(async () => { await Promise.all([load(), onDataChanged()]); }, [load, onDataChanged]);
    const dispatchSaved = useCallback((fileDispatches, dispatch) => {
        setData((current) => applySupplierDispatchToWorkspaceData(
            current,
            fileDispatches,
            dispatch,
        ));
    }, []);
    if (loading && !data) return <div className="flex min-h-48 items-center justify-center gap-2 font-black text-violet-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل إدارة منتجاتي…</div>;
    if (section === "waiting-review") {
        const directExecutionView = initialSection === "waiting-review";
        return <WaitingReviewView
            data={data}
            loading={loading}
            error={error}
            onRefresh={load}
            onChanged={changed}
            onDispatchSaved={dispatchSaved}
            onBack={directExecutionView ? undefined : () => setSection("overview")}
            title={directExecutionView ? "رفع المنتجات للمورد" : "بانتظار المراجعة"}
            description={directExecutionView
                ? "حدد المنتجات والكميات؛ ويتحول الملف إلى قيد التنفيذ بعد اكتمال رفع جميع منتجاته."
                : "المنتجات المسندة إليك ولم تُرسل إلى مورد بعد."}
        />;
    }
    if (section === "in-progress") return <InProgressView data={data} loading={loading} error={error} onRefresh={load} onBack={() => setSection("overview")} />;
    if (section === "received") return <ReceivedView data={data} loading={loading} error={error} onRefresh={load} onBack={() => setSection("overview")} />;
    if (section === "supplier-receiving") return <div className="space-y-5"><SectionHeader title="فاتورة المورد" description="استلام المنتجات وسجل فواتير المورد داخل إدارة منتجاتي." onBack={() => setSection("overview")} /><Suspense fallback={<div className="flex min-h-48 items-center justify-center gap-2 font-black text-emerald-700"><SpinnerGap className="animate-spin" /> جارٍ تحميل فاتورة المورد…</div>}><SupplierReceivingWorkspace /></Suspense></div>;
    return <MyProductsOverview data={data} onOpen={setSection} />;
}

export default function PreparationSupplierDispatchWorkspace({ view = "my-products", onDataChanged = async () => {} }) {
    const content = useMemo(() => {
        if (view === "unassigned") return <UnassignedManagerView onChanged={onDataChanged} />;
        const initialSection = ["waiting-review", "in-progress", "received", "supplier-receiving"].includes(view)
            ? view
            : "overview";
        return <EmployeeProductsWorkspace onDataChanged={onDataChanged} initialSection={initialSection} />;
    }, [onDataChanged, view]);
    return content;
}
