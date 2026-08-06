import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    Barcode,
    Buildings,
    Camera,
    CaretDown,
    CheckCircle,
    ClockCounterClockwise,
    Keyboard,
    Package,
    PlusCircle,
    SpinnerGap,
    UserCircle,
    WarningCircle,
    XCircle,
} from "@phosphor-icons/react";

import {
    cancelSupplierReceivingSession,
    closeSupplierReceivingSession,
    loadSupplierReceivingCatalog,
    newSupplierReceivingRequestId,
    openSupplierReceivingSession,
    scanSupplierReceivingPiece,
} from "../../services/supplierReceiving";

const PREPARATION_TRACKS = [
    "من المستودع",
    "من المورد",
    "تصنيع داخلي",
    "ينتظر توريد",
    "قيد التجميع",
    "متوقف بسبب نقص منتج",
];

export function formatReceivingDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA", {
        timeZone: "Asia/Riyadh",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(parsed);
}
export function supplierDisplayName(session) {
    return session?.supplier?.company_name || "مورد غير محدد";
}

export function formatSupplierMoney(halalas) {
    return new Intl.NumberFormat("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(Number(halalas || 0) / 100);
}

export function supplierScanReferencePriceHalalas(scan) {
    const direct = Number(scan?.reference_unit_price_halalas);
    if (Number.isFinite(direct) && direct >= 0) return Math.round(direct);
    return (scan?.services || []).reduce((total, service) => {
        const unitCost = Number(service?.reference_unit_cost);
        const quantity = Number(service?.required_quantity || 1);
        if (!Number.isFinite(unitCost) || unitCost < 0) return total;
        return total + Math.round(unitCost * (Number.isFinite(quantity) && quantity > 0 ? quantity : 1) * 100);
    }, 0);
}

export function supplierInvoiceLineKey(scan) {
    const services = (scan?.invoice_services?.length ? scan.invoice_services : scan?.services || [])
        .map((service) => `${service?.service_id || ""}:${service?.required_quantity || 1}`)
        .sort()
        .join("|");
    return [scan?.product_id || "", scan?.sku || "", scan?.product_name || "منتج", services].join("::");
}

function serviceReferenceHalalas(service) {
    const direct = Number(service?.reference_unit_price_halalas);
    if (Number.isFinite(direct) && direct >= 0) return Math.round(direct);
    const amount = Number(service?.reference_unit_cost ?? service?.unit_cost);
    return Number.isFinite(amount) && amount >= 0 ? Math.round(amount * 100) : 0;
}

export function buildSupplierInvoiceLines(scans = [], drafts = {}) {
    const lines = new Map();
    [...scans].reverse().forEach((scan) => {
        const pieceId = String(scan?.piece_id || "").trim();
        if (!pieceId) return;
        const key = supplierInvoiceLineKey(scan);
        const existing = lines.get(key);
        if (existing) {
            if (!existing.piece_ids.includes(pieceId)) existing.piece_ids.push(pieceId);
            return;
        }
        const productReference = Number(scan?.reference_product_unit_price_halalas);
        const productReferenceHalalas = Number.isFinite(productReference) && productReference >= 0
            ? Math.round(productReference)
            : 0;
        const services = (scan?.invoice_services?.length ? scan.invoice_services : scan?.services || [])
            .map((service) => ({
                service_id: String(service?.service_id || "").trim(),
                service_name: service?.service_name || service?.service_code || "خدمة",
                service_code: service?.service_code || null,
                unit: service?.unit || "job",
                quantity_per_piece: Number(service?.required_quantity || service?.quantity_per_piece || 1),
                reference_unit_price_halalas: serviceReferenceHalalas(service),
                unit_price_halalas: serviceReferenceHalalas(service),
                selected: true,
                add_to_product: false,
                reference_price_complete: service?.reference_price_complete !== false
                    && serviceReferenceHalalas(service) > 0,
            }))
            .filter((service) => service.service_id);
        lines.set(key, {
            key,
            product_id: scan?.product_id || null,
            product_name: scan?.product_name || "منتج",
            sku: scan?.sku || null,
            selected_image_url: scan?.selected_image_url || scan?.image_url || null,
            piece_ids: [pieceId],
            reference_product_unit_price_halalas: productReferenceHalalas,
            product_unit_price_halalas: productReferenceHalalas,
            product_reference_price_complete:
                typeof scan?.reference_product_price_complete === "boolean"
                    ? scan.reference_product_price_complete
                    : productReferenceHalalas > 0,
            services,
        });
    });
    return Array.from(lines.values()).map((line) => ({
        ...line,
        ...(drafts[line.key] || {}),
        services: (() => {
            const draftServices = drafts[line.key]?.services || {};
            const merged = line.services.map((service) => ({
                ...service,
                ...(draftServices[service.service_id] || {}),
            }));
            Object.values(draftServices).forEach((service) => {
                if (!merged.some((row) => row.service_id === service.service_id)) {
                    merged.push({ ...service });
                }
            });
            return merged;
        })(),
    })).map((line) => {
        const quantity = line.piece_ids.length;
        const services = line.services.map((service) => {
            const perPiece = Number(service.quantity_per_piece || 1);
            const totalQuantity = (Number.isFinite(perPiece) && perPiece > 0 ? perPiece : 1) * quantity;
            return {
                ...service,
                total_quantity: totalQuantity,
                total_halalas: service.selected
                    ? Math.round(totalQuantity * Number(service.unit_price_halalas || 0))
                    : 0,
            };
        });
        const productTotal = quantity * Number(line.product_unit_price_halalas || 0);
        const servicesTotal = services.reduce((sum, service) => sum + service.total_halalas, 0);
        return {
            ...line,
            services,
            quantity,
            product_total_halalas: productTotal,
            services_total_halalas: servicesTotal,
            total_halalas: productTotal + servicesTotal,
        };
    });
}

function EmptyImage() {
    return (
        <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-400">
            <Package size={24} weight="duotone" />
        </div>
    );
}

function ScanRow({ scan }) {
    return (
        <article className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-3 sm:grid-cols-[64px_minmax(0,1fr)_auto] sm:items-center" data-testid="supplier-receiving-scan-row">
            {scan.selected_image_url ? (
                <img src={scan.selected_image_url} alt="" className="h-14 w-14 rounded-xl border border-slate-200 object-cover" />
            ) : <EmptyImage />}
            <div className="min-w-0">
                <div className="truncate font-black text-slate-950">{scan.product_name || "منتج"}</div>
                <div className="mt-1 text-xs font-bold text-slate-500">
                    طلب {scan.order_number || "—"} · ملف {scan.file_number || "—"} · قطعة {scan.unit_index || "—"}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-extrabold">
                    <span className="rounded-full bg-violet-50 px-2 py-1 text-violet-800">جهّزها: {scan.preparation_employee_name || "—"}</span>
                    <span className="rounded-full bg-emerald-50 px-2 py-1 text-emerald-800">استلمها: {scan.receiving_employee_name || "—"}</span>
                </div>
            </div>
            <div className="text-xs font-bold text-slate-500 sm:text-left">{formatReceivingDate(scan.occurred_at)}</div>
        </article>
    );
}

function SupplierInvoiceLineEditor({
    line,
    permissions,
    serviceCatalog,
    onProductPriceChange,
    onServicePriceChange,
    onServiceToggle,
    onServiceAdd,
}) {
    const [serviceToAdd, setServiceToAdd] = useState("");
    const currentServiceIds = new Set(line.services.map((service) => service.service_id));
    const availableServices = serviceCatalog.filter(
        (service) => !currentServiceIds.has(String(service?.id || "")),
    );
    return (
        <article className="rounded-2xl border border-slate-200 bg-white p-3" data-testid="supplier-receiving-invoice-line">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-3">
                <div className="min-w-0">
                    <div className="truncate font-black text-slate-950">{line.product_name}</div>
                    <div className="mt-1 text-[11px] font-bold text-slate-500">{line.quantity} قطعة{line.sku ? ` · ${line.sku}` : ""}</div>
                </div>
                <div className="min-w-40">
                    <div className="mb-1 text-[10px] font-black text-slate-500">سعر المنتج الأساسي للقطعة</div>
                    <label className="relative block">
                        <input
                            type="number"
                            min="0"
                            step="0.01"
                            value={(Number(line.product_unit_price_halalas || 0) / 100).toFixed(2)}
                            onChange={(event) => onProductPriceChange(line.key, event.target.value)}
                            disabled={!permissions.can_edit_product_price}
                            className="h-10 w-full rounded-lg border border-slate-200 bg-white px-2 pl-8 text-center text-sm font-black tabular-nums outline-none focus:border-emerald-500 disabled:bg-slate-100 disabled:text-slate-500"
                            aria-label={`سعر المنتج ${line.product_name}`}
                        />
                        <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[10px] font-bold text-slate-400">ر.س</span>
                    </label>
                    {!line.product_reference_price_complete && <div className="mt-1 text-[10px] font-black text-amber-700">السعر الأصلي للمنتج غير مسجل</div>}
                </div>
            </div>
            <div className="mt-3 space-y-2">
                <div className="text-[11px] font-black text-violet-800">الخدمات التي نفذها المورد</div>
                {line.services.map((service) => (
                    <div key={service.service_id} className={`grid grid-cols-[auto_minmax(0,1fr)_108px_82px] items-center gap-2 rounded-xl border p-2 ${service.selected ? "border-violet-200 bg-violet-50/50" : "border-slate-200 bg-slate-50 opacity-70"}`}>
                        <input
                            type="checkbox"
                            checked={Boolean(service.selected)}
                            onChange={(event) => onServiceToggle(line.key, service.service_id, event.target.checked)}
                            aria-label={`اختيار خدمة ${service.service_name}`}
                            className="h-5 w-5 accent-violet-700"
                        />
                        <div className="min-w-0">
                            <div className="truncate text-xs font-black text-slate-900">{service.service_name}</div>
                            <div className="mt-0.5 text-[10px] font-bold text-slate-500">{service.quantity_per_piece || 1} لكل قطعة{service.add_to_product ? " · ستُضاف للمنتج" : ""}</div>
                        </div>
                        <label className="relative">
                            <input
                                type="number"
                                min="0.01"
                                step="0.01"
                                value={(Number(service.unit_price_halalas || 0) / 100).toFixed(2)}
                                onChange={(event) => onServicePriceChange(line.key, service.service_id, event.target.value)}
                                disabled={!permissions.can_edit_service_price || !service.selected}
                                className="h-9 w-full rounded-lg border border-slate-200 bg-white px-2 pl-7 text-center text-xs font-black tabular-nums outline-none focus:border-violet-500 disabled:bg-slate-100 disabled:text-slate-500"
                                aria-label={`سعر خدمة ${service.service_name}`}
                            />
                            <span className="absolute left-2 top-1/2 -translate-y-1/2 text-[9px] font-bold text-slate-400">ر.س</span>
                        </label>
                        <div className="text-left text-xs font-black tabular-nums text-violet-800">{formatSupplierMoney(service.total_halalas)}</div>
                    </div>
                ))}
                {!line.services.length && <div className="rounded-xl border border-dashed border-amber-300 bg-amber-50 p-3 text-xs font-black text-amber-900">لا توجد خدمة مرتبطة يستطيع هذا المورد تنفيذها.</div>}
            </div>
            {permissions.can_add_service && availableServices.length > 0 && (
                <div className="mt-3 flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-2">
                    <select value={serviceToAdd} onChange={(event) => setServiceToAdd(event.target.value)} className="min-h-10 min-w-0 flex-1 rounded-lg border border-emerald-200 bg-white px-2 text-xs font-black" aria-label={`إضافة خدمة إلى ${line.product_name}`}>
                        <option value="">اختر خدمة موجودة لإضافتها للمنتج</option>
                        {availableServices.map((service) => <option key={service.id} value={service.id}>{service.name} · {Number(service.unit_cost || 0).toFixed(2)} ر.س</option>)}
                    </select>
                    <button type="button" disabled={!serviceToAdd} onClick={() => { onServiceAdd(line.key, serviceToAdd); setServiceToAdd(""); }} className="rounded-lg bg-emerald-700 px-3 text-xs font-black text-white disabled:opacity-40"><PlusCircle className="ml-1 inline" /> إضافة</button>
                </div>
            )}
            <div className="mt-3 flex items-center justify-between border-t border-slate-100 pt-3 text-sm font-black"><span>إجمالي المنتج والخدمات</span><span className="tabular-nums text-emerald-800">{formatSupplierMoney(line.total_halalas)} ر.س</span></div>
        </article>
    );
}

function SupplierInvoiceCompactTable({
    invoiceLines,
    permissions,
    serviceCatalog,
    onProductPriceChange,
    onServicePriceChange,
    onServiceToggle,
    onServiceAdd,
    showEditors = true,
}) {
    const pieceCount = invoiceLines.reduce((sum, line) => sum + Number(line.quantity || 0), 0);
    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="supplier-receiving-mobile-invoice">
            <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
                <h4 className="font-black text-slate-950">فاتورة المورد</h4>
                <span className="text-xs font-black text-slate-500">{pieceCount} قطع · {invoiceLines.length} منتجات</span>
            </header>
            {!invoiceLines.length ? (
                <div className="p-7 text-center text-sm font-bold text-slate-500">امسح أول قطعة لتظهر هنا مباشرة.</div>
            ) : (
                <>
                    <div className="grid grid-cols-[minmax(0,1fr)_42px_68px_70px] items-center gap-1 bg-slate-50 px-3 py-2 text-[10px] font-black text-slate-500">
                        <span>المنتج</span>
                        <span className="text-center">الكمية</span>
                        <span className="text-center">سعر الوحدة</span>
                        <span className="text-left">الإجمالي</span>
                    </div>
                    <div data-testid="supplier-receiving-mobile-invoice-rows">
                        {invoiceLines.map((line) => {
                            const unitTotal = line.quantity
                                ? Math.round(Number(line.total_halalas || 0) / Number(line.quantity))
                                : 0;
                            return (
                                <div key={line.key} className="grid grid-cols-[minmax(0,1fr)_42px_68px_70px] items-center gap-1 border-b border-slate-100 px-3 py-3 last:border-b-0" data-testid="supplier-receiving-mobile-invoice-row">
                                    <div className="flex min-w-0 items-center gap-2">
                                        {line.selected_image_url ? (
                                            <img src={line.selected_image_url} alt="" className="h-11 w-11 shrink-0 rounded-xl border border-slate-200 object-cover" />
                                        ) : (
                                            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-slate-400"><Package size={20} weight="duotone" /></span>
                                        )}
                                        <span className="min-w-0">
                                            <span className="block line-clamp-2 text-xs font-black leading-5 text-slate-950">{line.product_name}</span>
                                            {line.sku && <span className="mt-0.5 block truncate text-[9px] font-bold text-slate-400">{line.sku}</span>}
                                        </span>
                                    </div>
                                    <span className="text-center text-sm font-black tabular-nums text-slate-800">{line.quantity}</span>
                                    <span className="text-center text-xs font-black tabular-nums text-slate-800">{formatSupplierMoney(unitTotal)}</span>
                                    <span className="text-left text-xs font-black tabular-nums text-emerald-800">{formatSupplierMoney(line.total_halalas)}</span>
                                </div>
                            );
                        })}
                    </div>
                </>
            )}
            {showEditors && invoiceLines.length > 0 && (
                <details className="group border-t border-slate-200" data-testid="supplier-receiving-mobile-invoice-review">
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-black text-slate-800">
                        <span>مراجعة الأسعار والخدمات</span>
                        <CaretDown size={18} className="transition-transform group-open:rotate-180" weight="bold" />
                    </summary>
                    <div className="space-y-3 border-t border-slate-100 bg-slate-50 p-3">
                        {invoiceLines.map((line) => (
                            <SupplierInvoiceLineEditor
                                key={line.key}
                                line={line}
                                permissions={permissions}
                                serviceCatalog={serviceCatalog}
                                onProductPriceChange={onProductPriceChange}
                                onServicePriceChange={onServicePriceChange}
                                onServiceToggle={onServiceToggle}
                                onServiceAdd={onServiceAdd}
                            />
                        ))}
                    </div>
                </details>
            )}
        </section>
    );
}

export function SupplierPieceCameraScanner({
    onDetected,
    onClose,
    onCancel,
    onSave,
    cancelling = false,
    saving = false,
    scanning = false,
    error = "",
    lastScan = null,
    invoiceLines = [],
    permissions = {},
    serviceCatalog = [],
    onProductPriceChange = () => {},
    onServicePriceChange = () => {},
    onServiceToggle = () => {},
    onServiceAdd = () => {},
    supplierName = "",
    employeeName = "",
    manualBarcode = "",
    onManualBarcodeChange = () => {},
    onManualSubmit = () => {},
}) {
    const videoRef = useRef(null);
    const [cameraError, setCameraError] = useState("");
    const [cameraReady, setCameraReady] = useState(false);
    const [cameraEngine, setCameraEngine] = useState("");
    const [manualOpen, setManualOpen] = useState(false);

    useEffect(() => {
        let stopped = false;
        let detecting = false;
        let lastDetectedValue = "";
        let stream;
        let animationFrame;
        let zxingControls;

        const acceptDetectedValue = async (rawValue) => {
            const value = String(rawValue || "").trim();
            if (!value) {
                lastDetectedValue = "";
                return;
            }
            if (value === lastDetectedValue || detecting || stopped) return;

            detecting = true;
            lastDetectedValue = value;
            try {
                await onDetected(value);
            } finally {
                detecting = false;
            }
        };

        const createNativeDetector = async () => {
            if (!globalThis.BarcodeDetector) return null;
            try {
                const getSupportedFormats = globalThis.BarcodeDetector.getSupportedFormats;
                if (typeof getSupportedFormats !== "function") {
                    return new globalThis.BarcodeDetector();
                }
                const supported = await getSupportedFormats.call(globalThis.BarcodeDetector);
                const formats = ["qr_code", "code_128"].filter((value) => supported.includes(value));
                return formats.length ? new globalThis.BarcodeDetector({ formats }) : null;
            } catch {
                return null;
            }
        };

        async function startCamera() {
            if (!navigator.mediaDevices?.getUserMedia) {
                setCameraError("هذا الجهاز لا يتيح الكاميرا للمتصفح. تأكد من فتح ميزان عبر HTTPS ومنح المتصفح صلاحية الكاميرا.");
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
                            await acceptDetectedValue(rows?.[0]?.rawValue);
                        } catch {
                            // A frame without a readable code is expected while the camera is moving.
                            lastDetectedValue = "";
                        }
                        animationFrame = requestAnimationFrame(detectFrame);
                    };

                    animationFrame = requestAnimationFrame(detectFrame);
                    return;
                }

                setCameraEngine("zxing");
                const { BarcodeFormat, BrowserMultiFormatReader } = await import("@zxing/browser");
                if (stopped || !videoRef.current) return;
                const reader = new BrowserMultiFormatReader(undefined, {
                    delayBetweenScanAttempts: 180,
                    delayBetweenScanSuccess: 500,
                });
                reader.possibleFormats = [BarcodeFormat.QR_CODE, BarcodeFormat.CODE_128];
                zxingControls = await reader.decodeFromVideoElement(
                    videoRef.current,
                    (result) => {
                        if (result) {
                            void acceptDetectedValue(result.getText());
                        } else {
                            lastDetectedValue = "";
                        }
                    },
                );
                if (stopped) zxingControls?.stop?.();
            } catch (cameraStartError) {
                const messages = {
                    NotAllowedError: "اسمح لميزان باستخدام الكاميرا من إعدادات المتصفح ثم حاول مرة أخرى.",
                    NotFoundError: "لم يتم العثور على كاميرا في هذا الجهاز.",
                    NotReadableError: "الكاميرا مستخدمة في تطبيق آخر. أغلقه ثم حاول مرة أخرى.",
                    SecurityError: "تشغيل الكاميرا يحتاج فتح ميزان عبر اتصال آمن HTTPS.",
                };
                setCameraError(messages[cameraStartError?.name] || "تعذّر تشغيل الكاميرا أو قارئ QR. حدّث الصفحة وحاول مرة أخرى، أو استخدم الإدخال اليدوي مؤقتًا.");
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

    const total = invoiceLines.reduce((sum, line) => sum + Number(line.total_halalas || 0), 0);
    const cannotSave = saving
        || scanning
        || !invoiceLines.length
        || invoiceLines.some((line) => !line.services.some(
            (service) => service.selected && Number(service.unit_price_halalas) > 0,
        ));

    return (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-white p-0 lg:bg-slate-950/90 lg:p-3" dir="rtl" role="dialog" aria-modal="true" aria-label="تصوير QR القطعة" data-testid="supplier-receiving-camera-dialog">
            <div className="flex h-[100dvh] max-h-[100dvh] w-full max-w-7xl flex-col overflow-hidden bg-white shadow-2xl lg:h-[96vh] lg:max-h-[96vh] lg:rounded-3xl">
                <header className="flex items-center justify-between gap-3 bg-emerald-800 px-3 py-3 text-white lg:border-b lg:border-slate-200 lg:bg-white lg:p-4 lg:text-slate-950">
                    <div className="min-w-0">
                        <h3 className="flex items-center gap-2 text-lg font-black"><Camera size={24} className="text-emerald-200 lg:text-emerald-700" weight="duotone" /> <span className="lg:hidden">استلام المورد</span><span className="hidden lg:inline">تصوير QR القطعة</span></h3>
                        <p className="mt-0.5 truncate text-[11px] font-bold text-emerald-100 lg:mt-1 lg:text-xs lg:leading-5 lg:text-slate-500">
                            <span className="lg:hidden">{supplierName || "جلسة المورد"}{employeeName ? ` · ${employeeName}` : ""}</span>
                            <span className="hidden lg:inline">الكاميرا تبقى مفتوحة. وجّهها إلى كل QR، ثم احفظ فاتورة المورد بعد اكتمال التصوير.</span>
                        </p>
                    </div>
                    <button type="button" onClick={onClose} disabled={saving || cancelling} className="shrink-0 rounded-xl border border-white/20 bg-white/10 px-3 py-2 text-sm font-black text-white disabled:opacity-50 lg:border-slate-300 lg:bg-white lg:text-slate-800"><XCircle className="ml-1 inline lg:hidden" /> <span className="hidden lg:inline">إغلاق الكاميرا</span><span className="lg:hidden">إغلاق</span></button>
                </header>

                <div className="flex min-h-0 flex-1 flex-col lg:grid lg:grid-cols-2" data-testid="supplier-receiving-camera-split-layout">
                    <section className="shrink-0 border-b border-slate-200 bg-white p-3 lg:min-h-0 lg:border-b-0 lg:border-l lg:bg-slate-950">
                        {cameraError ? (
                            <div className="flex min-h-[190px] items-start gap-2 rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-black leading-6 text-amber-950 lg:min-h-0">
                                <WarningCircle size={22} className="mt-0.5 shrink-0" weight="fill" />
                                <span>{cameraError}</span>
                            </div>
                        ) : (
                            <div className="relative h-[29dvh] min-h-[190px] max-h-[300px] overflow-hidden rounded-2xl bg-black lg:h-full lg:max-h-none" data-camera-engine={cameraEngine || undefined}>
                                <video ref={videoRef} muted playsInline className="h-full w-full object-cover" />
                                {!cameraReady && (
                                    <div className="absolute inset-0 flex items-center justify-center bg-slate-950 text-sm font-black text-white">
                                        <SpinnerGap size={24} className="ml-2 animate-spin" /> جارٍ تشغيل الكاميرا…
                                    </div>
                                )}
                                {cameraReady && (
                                    <>
                                        <div className="absolute left-3 top-3 rounded-full bg-white/90 px-3 py-1.5 text-[11px] font-black text-emerald-800 shadow"><span className="ml-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />الكاميرا جاهزة</div>
                                        <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-12">
                                            <div className="aspect-square w-full max-w-72 rounded-3xl border-[3px] border-emerald-400 shadow-[0_0_0_999px_rgba(2,6,23,0.30)]" />
                                        </div>
                                    </>
                                )}
                                {scanning && (
                                    <div className="absolute bottom-3 right-3 flex items-center gap-2 rounded-full bg-white px-3 py-2 text-xs font-black text-emerald-800 shadow-lg">
                                        <SpinnerGap size={18} className="animate-spin" /> جارٍ إضافة المنتج…
                                    </div>
                                )}
                            </div>
                        )}

                        <div className="mt-2 grid grid-cols-2 gap-2 lg:hidden">
                            <button type="button" onClick={() => setManualOpen((value) => !value)} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-sm font-black text-slate-800"><Keyboard size={20} /> إدخال يدوي</button>
                            <button type="button" onClick={onClose} disabled={saving || cancelling} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-slate-200 bg-white text-sm font-black text-slate-800 disabled:opacity-50"><XCircle size={20} /> إغلاق الكاميرا</button>
                        </div>
                        {manualOpen && (
                            <form onSubmit={(event) => { event.preventDefault(); onManualSubmit(); }} className="mt-2 flex gap-2 lg:hidden">
                                <input value={manualBarcode} onChange={(event) => onManualBarcodeChange(event.target.value)} autoComplete="off" placeholder="أدخل رمز القطعة" className="min-h-11 min-w-0 flex-1 rounded-xl border border-emerald-300 px-3 font-mono text-sm font-black outline-none focus:border-emerald-600" />
                                <button type="submit" disabled={!manualBarcode.trim() || scanning} className="rounded-xl bg-emerald-700 px-4 text-sm font-black text-white disabled:opacity-50">إضافة</button>
                            </form>
                        )}
                        <p className="mt-2 hidden text-center text-xs font-bold text-slate-200 lg:block">أبعد QR السابق عن الإطار ثم وجّه الكاميرا إلى القطعة التالية.</p>
                    </section>

                    <section className="flex min-h-0 flex-1 flex-col bg-slate-50" data-testid="supplier-receiving-invoice-draft">
                        {(lastScan || error) && (
                            <div className="space-y-2 px-3 pt-3 lg:border-b lg:border-slate-200 lg:bg-white lg:p-4">
                                {lastScan && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-black text-emerald-800"><CheckCircle className="ml-1 inline" weight="fill" /> تمت إضافة {lastScan.product_name || "المنتج"} إلى الفاتورة</div>}
                                {error && <div className="rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs font-black text-rose-900">{error}</div>}
                            </div>
                        )}

                        <div className="min-h-0 flex-1 overflow-auto p-3">
                            <div className="lg:hidden">
                                <SupplierInvoiceCompactTable
                                    invoiceLines={invoiceLines}
                                    permissions={permissions}
                                    serviceCatalog={serviceCatalog}
                                    onProductPriceChange={onProductPriceChange}
                                    onServicePriceChange={onServicePriceChange}
                                    onServiceToggle={onServiceToggle}
                                    onServiceAdd={onServiceAdd}
                                />
                                <details className="group mt-3 rounded-xl border border-slate-200 bg-white">
                                    <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-black text-slate-800"><span>التعليمات والمسؤوليات</span><CaretDown size={18} className="transition-transform group-open:rotate-180" /></summary>
                                    <div className="space-y-2 border-t border-slate-100 p-3 text-xs font-bold leading-5 text-slate-600">
                                        <p>موظف التجهيز محفوظ مع كل قطعة، وموظف الاستلام هو صاحب الجلسة.</p>
                                        <p>اعتماد الفاتورة ينشئ مديونية المورد داخل ميزان 2 فقط.</p>
                                    </div>
                                </details>
                            </div>
                            <div className="hidden space-y-3 lg:block">
                                {!invoiceLines.length ? (
                                    <div className="flex min-h-52 items-center justify-center rounded-2xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm font-bold text-slate-500">ابدأ بتصوير باركود المنتجات؛ ستظهر هنا دون إغلاق الكاميرا.</div>
                                ) : invoiceLines.map((line) => (
                                    <SupplierInvoiceLineEditor
                                        key={line.key}
                                        line={line}
                                        permissions={permissions}
                                        serviceCatalog={serviceCatalog}
                                        onProductPriceChange={onProductPriceChange}
                                        onServicePriceChange={onServicePriceChange}
                                        onServiceToggle={onServiceToggle}
                                        onServiceAdd={onServiceAdd}
                                    />
                                ))}
                            </div>
                        </div>

                        <footer className="shrink-0 border-t border-slate-200 bg-white p-3 lg:p-4">
                            <div className="mb-2 flex items-center justify-between"><span className="font-black text-slate-700">الإجمالي</span><span className="text-xl font-black tabular-nums text-emerald-800 lg:text-2xl">{formatSupplierMoney(total)} ر.س</span></div>
                            <button type="button" onClick={onSave} disabled={cannotSave} className="min-h-12 w-full rounded-xl bg-emerald-700 px-5 text-base font-black text-white disabled:opacity-50" data-testid="supplier-receiving-save-invoice">
                                {saving ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />} اعتماد فاتورة المورد وإنهاء الجلسة
                            </button>
                            <p className="mt-1.5 text-center text-[10px] font-bold text-emerald-800">الاعتماد ينشئ مديونية المورد داخل ميزان 2 فقط.</p>
                            <button type="button" onClick={onCancel} disabled={saving || cancelling || scanning} className="mt-1.5 min-h-9 w-full rounded-xl text-xs font-black text-rose-700 disabled:opacity-50" data-testid="supplier-receiving-cancel-session-camera">
                                {cancelling ? <SpinnerGap className="ml-1 inline animate-spin" /> : <XCircle className="ml-1 inline" weight="fill" />} إلغاء الجلسة والخروج
                            </button>
                        </footer>
                    </section>
                </div>
            </div>
        </div>
    );
}

export default function SupplierReceivingWorkspace() {
    const [data, setData] = useState({ suppliers: [], sessions: [], active_session_scans: [] });
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState("");
    const [supplierId, setSupplierId] = useState("");
    const [openNote, setOpenNote] = useState("");
    const [closeNote, setCloseNote] = useState("");
    const [barcode, setBarcode] = useState("");
    const [cameraOpen, setCameraOpen] = useState(false);
    const [manualEntryOpen, setManualEntryOpen] = useState(false);
    const [error, setError] = useState("");
    const [lastScan, setLastScan] = useState(null);
    const [invoiceDrafts, setInvoiceDrafts] = useState({});
    const barcodeRef = useRef(null);
    const scanBusyRef = useRef(false);

    const load = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        setError("");
        try {
            const result = await loadSupplierReceivingCatalog({ limit: 50 });
            setData(result || {});
        } catch (loadError) {
            setError(loadError.message || "تعذّر تحميل جلسات الاستلام.");
        } finally {
            if (!quiet) setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);
    const active = data?.active_session || null;
    const sessionCancelling = active?.status === "cancelling";
    const scans = useMemo(
        () => (Array.isArray(data?.active_session_scans) ? data.active_session_scans : []),
        [data?.active_session_scans],
    );
    const invoiceLines = useMemo(
        () => buildSupplierInvoiceLines(scans, invoiceDrafts),
        [scans, invoiceDrafts],
    );
    const activeServiceCatalog = useMemo(() => {
        const allowed = new Set(
            (active?.supplier?.service_links || []).map((service) => String(service?.service_id || "")),
        );
        return (data?.service_catalog || []).filter((service) => allowed.has(String(service?.id || "")));
    }, [active?.supplier?.service_links, data?.service_catalog]);
    const closedSessions = useMemo(
        () => (data?.sessions || []).filter((row) => row?.status === "closed"),
        [data?.sessions],
    );
    const invoiceTotal = invoiceLines.reduce(
        (sum, line) => sum + Number(line.total_halalas || 0),
        0,
    );
    const invoiceCannotSave = Boolean(
        busy
        || !invoiceLines.length
        || invoiceLines.some((line) => !line.services.some(
            (service) => service.selected && Number(service.unit_price_halalas) > 0,
        )),
    );

    useEffect(() => {
        if (active && !busy) barcodeRef.current?.focus();
    }, [active, busy]);

    async function openSession(event) {
        event.preventDefault();
        if (!supplierId || busy) return;
        setBusy("open");
        setError("");
        try {
            const result = await openSupplierReceivingSession({
                client_request_id: newSupplierReceivingRequestId(),
                supplier_id: supplierId,
                note: openNote.trim() || null,
            });
            setData((current) => ({
                ...current,
                active_session: result.session,
                active_session_scans: [],
                sessions: [result.session, ...(current.sessions || [])],
            }));
            setOpenNote("");
            setLastScan(null);
            setInvoiceDrafts({});
        } catch (openError) {
            setError(openError.message);
        } finally {
            setBusy("");
        }
    }

    const receivePiece = useCallback(async (rawValue, { refocus = true } = {}) => {
        const value = String(rawValue || "").trim();
        if (!active?.id || !value || scanBusyRef.current) return false;
        scanBusyRef.current = true;
        setBusy("scan");
        setError("");
        try {
            const result = await scanSupplierReceivingPiece(active.id, value);
            setLastScan(result.scan);
            setBarcode("");
            setData((current) => ({
                ...current,
                active_session: result.session,
                active_session_scans: [
                    result.scan,
                    ...(current.active_session_scans || []).filter(
                        (row) => row.piece_id !== result.scan?.piece_id,
                    ),
                ],
                sessions: (current.sessions || []).map((row) => (
                    row.id === result.session?.id ? result.session : row
                )),
            }));
            return true;
        } catch (scanError) {
            setError(scanError.message);
            setBarcode("");
            return false;
        } finally {
            scanBusyRef.current = false;
            setBusy("");
            if (refocus) window.setTimeout(() => barcodeRef.current?.focus(), 0);
        }
    }, [active?.id]);

    function scanPiece(event) {
        event.preventDefault();
        receivePiece(barcode);
    }

    const handleCameraDetected = useCallback(
        (value) => receivePiece(value, { refocus: false }),
        [receivePiece],
    );

    function changeProductPrice(lineKey, value) {
        const amount = Number(value);
        setInvoiceDrafts((current) => ({
            ...current,
            [lineKey]: {
                ...(current[lineKey] || {}),
                product_unit_price_halalas: Number.isFinite(amount) && amount >= 0
                    ? Math.round(amount * 100)
                    : 0,
            },
        }));
    }

    function patchDraftService(lineKey, serviceId, patch) {
        setInvoiceDrafts((current) => ({
            ...current,
            [lineKey]: {
                ...(current[lineKey] || {}),
                services: {
                    ...(current[lineKey]?.services || {}),
                    [serviceId]: {
                        ...(current[lineKey]?.services?.[serviceId] || {}),
                        service_id: serviceId,
                        ...patch,
                    },
                },
            },
        }));
    }

    function changeServicePrice(lineKey, serviceId, value) {
        const amount = Number(value);
        patchDraftService(lineKey, serviceId, {
            unit_price_halalas: Number.isFinite(amount) && amount >= 0
                ? Math.round(amount * 100)
                : 0,
        });
    }

    function toggleService(lineKey, serviceId, selected) {
        patchDraftService(lineKey, serviceId, { selected: Boolean(selected) });
    }

    function addService(lineKey, serviceId) {
        const service = activeServiceCatalog.find((row) => String(row?.id || "") === String(serviceId));
        if (!service) return;
        const unitPrice = Number(service.unit_cost);
        patchDraftService(lineKey, String(service.id), {
            service_name: service.name || service.code || "خدمة",
            service_code: service.code || null,
            unit: service.unit || "job",
            quantity_per_piece: 1,
            reference_unit_price_halalas: Number.isFinite(unitPrice) && unitPrice >= 0 ? Math.round(unitPrice * 100) : 0,
            unit_price_halalas: Number.isFinite(unitPrice) && unitPrice >= 0 ? Math.round(unitPrice * 100) : 0,
            reference_price_complete: Number.isFinite(unitPrice) && unitPrice > 0,
            selected: true,
            add_to_product: true,
        });
    }

    async function closeSession() {
        if (!active?.id || busy) return;
        setBusy("close");
        setError("");
        try {
            await closeSupplierReceivingSession(active.id, {
                note: closeNote.trim(),
                invoice_lines: invoiceLines.map((line) => ({
                    piece_ids: line.piece_ids,
                    product_unit_price_halalas: line.product_unit_price_halalas,
                    services: line.services.filter((service) => service.selected).map((service) => ({
                        service_id: service.service_id,
                        unit_price_halalas: service.unit_price_halalas,
                        add_to_product: Boolean(service.add_to_product),
                    })),
                })),
            });
            setCameraOpen(false);
            setCloseNote("");
            setLastScan(null);
            setInvoiceDrafts({});
            await load({ quiet: true });
        } catch (closeError) {
            setError(closeError.message);
        } finally {
            setBusy("");
        }
    }

    async function cancelSession() {
        if (!active?.id || busy) return;
        const pieceCount = invoiceLines.reduce((sum, line) => sum + line.quantity, 0);
        const message = pieceCount
            ? `هل تريد إلغاء الجلسة والخروج؟ ستُهمل الفاتورة وتُعاد ${pieceCount} قطعة إلى حالتها قبل المسح، ولن يُحفظ استلام.`
            : "هل تريد إلغاء الجلسة والخروج؟ لن تُحفظ فاتورة أو جلسة استلام.";
        if (!window.confirm(message)) return;
        setBusy("cancel");
        setError("");
        try {
            await cancelSupplierReceivingSession(active.id, {
                note: closeNote.trim(),
            });
            setCameraOpen(false);
            setCloseNote("");
            setLastScan(null);
            setInvoiceDrafts({});
            setBarcode("");
            setData((current) => ({
                ...current,
                active_session: null,
                active_session_scans: [],
                sessions: (current.sessions || []).filter((row) => row.id !== active.id),
            }));
            await load({ quiet: true });
        } catch (cancelError) {
            setError(cancelError.message);
        } finally {
            setBusy("");
        }
    }

    return (
        <section className="space-y-5" dir="rtl" data-testid="supplier-receiving-workspace">
            <div className="hidden overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm lg:block">
                <header className="bg-gradient-to-l from-slate-950 to-violet-900 p-5 text-white sm:p-6">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="text-xs font-black text-violet-200">Supplier Receiving V1</div>
                            <h2 className="mt-1 text-2xl font-black">استلام منتجات المورد بالباركود</h2>
                            <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-violet-100">
                                افتح جلسة للمورد، ثم امسح QR المنتجات والكاميرا تبقى مفتوحة. تجمع القطع في فاتورة واحدة، ثم يحفظها الموظف عند الانتهاء.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-2">
                            <div className="rounded-2xl border border-white/15 bg-white/10 px-4 py-3 text-center">
                                <div className="text-2xl font-black tabular-nums">{Number(active?.scan_count || 0)}</div>
                                <div className="text-[11px] font-bold text-violet-100">داخل الجلسة</div>
                            </div>
                            <div className="rounded-2xl border border-white/15 bg-white/10 px-4 py-3 text-center">
                                <div className="text-2xl font-black tabular-nums">{Number(data?.eligible_piece_count || 0)}</div>
                                <div className="text-[11px] font-bold text-violet-100">قابلة للاستلام</div>
                            </div>
                        </div>
                    </div>
                </header>
                <div className="flex flex-wrap gap-2 border-t border-slate-100 bg-slate-50 p-3">
                    {PREPARATION_TRACKS.map((track) => (
                        <span key={track} className={`rounded-full border px-3 py-1.5 text-xs font-black ${track === "من المورد" ? "border-violet-500 bg-violet-100 text-violet-950" : "border-slate-200 bg-white text-slate-500"}`}>{track}</span>
                    ))}
                </div>
            </div>

            <div className="hidden items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-950 lg:flex">
                <WarningCircle size={22} className="mt-0.5 shrink-0" weight="fill" />
                <div>عند الاعتماد تتحول مسودة الاستلام إلى فاتورة مورد محاسبية واحدة داخل ميزان 2، وتُنشأ مديونية المورد بالقيمة النهائية. لا يُرسل شيء إلى قيود أو سلة.</div>
            </div>

            {error && (
                <div className={`${active ? "hidden lg:flex" : "flex"} items-start gap-2 rounded-2xl border border-rose-300 bg-rose-50 p-4 text-sm font-black text-rose-950`} data-testid="supplier-receiving-error">
                    <WarningCircle size={21} className="mt-0.5 shrink-0" />{error}
                </div>
            )}

            {!active ? (
                <form onSubmit={openSession} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="supplier-receiving-open-form">
                    <div className="flex items-center gap-3">
                        <span className="flex h-11 w-11 items-center justify-center rounded-2xl bg-violet-100 text-violet-700"><Buildings size={24} weight="duotone" /></span>
                        <div><h3 className="font-black text-slate-950">فتح جلسة استلام جديدة</h3><p className="mt-1 text-xs font-bold text-slate-500">يمكن لكل موظف يملك صلاحية الاستلام فتح جلسته الخاصة.</p></div>
                    </div>
                    <div className="mt-5 grid gap-3 lg:grid-cols-[minmax(220px,1fr)_minmax(260px,2fr)_auto]">
                        <select value={supplierId} onChange={(event) => setSupplierId(event.target.value)} disabled={loading || busy === "open"} className="min-h-12 rounded-xl border border-slate-200 bg-white px-3 text-sm font-black outline-none focus:border-violet-500" aria-label="المورد">
                            <option value="">اختر المورد</option>
                            {(data?.suppliers || []).map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name} · {supplier.service_links?.map((service) => service.service_name).filter(Boolean).join("، ") || "بلا خدمات"}</option>)}
                        </select>
                        <input value={openNote} onChange={(event) => setOpenNote(event.target.value)} placeholder="ملاحظة للجلسة — اختياري" className="min-h-12 rounded-xl border border-slate-200 px-3 text-sm font-bold outline-none focus:border-violet-500" />
                        <button type="submit" disabled={!supplierId || loading || busy === "open"} className="min-h-12 rounded-xl bg-violet-700 px-5 text-sm font-black text-white disabled:opacity-50">
                            {busy === "open" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <Barcode className="ml-1 inline" />} فتح الجلسة
                        </button>
                    </div>
                    {loading && <div className="mt-4 text-xs font-bold text-violet-700"><SpinnerGap className="ml-1 inline animate-spin" /> جارٍ تحميل الموردين والجلسات…</div>}
                    {!loading && !(data?.suppliers || []).length && <div className="mt-4 rounded-xl border border-dashed border-amber-300 bg-amber-50 p-3 text-xs font-bold text-amber-900">لا يوجد مورد نشط مرتبط بخدمات في ميزان 2. <Link to="/suppliers-v2" className="underline">افتح صفحة الموردين لإضافة المورد وخدماته.</Link></div>}
                </form>
            ) : (
                <>
                    <section className="space-y-4 lg:hidden" data-testid="supplier-receiving-mobile-active-session">
                        <header className="rounded-2xl bg-emerald-800 p-4 text-white shadow-sm">
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="text-[10px] font-black text-emerald-100">استلام المورد</div>
                                    <h3 className="mt-1 truncate text-xl font-black">{supplierDisplayName(active)}</h3>
                                    <p className="mt-1 text-xs font-bold text-emerald-100">{active.opened_by_name || "—"} · {Number(active.scan_count || 0)} قطعة</p>
                                </div>
                                <span className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-black ${sessionCancelling ? "bg-rose-600" : "bg-white/15"}`}>{sessionCancelling ? "الإلغاء غير مكتمل" : "جلسة مفتوحة"}</span>
                            </div>
                        </header>

                        {error && (
                            <div className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black leading-5 text-rose-900" data-testid="supplier-receiving-mobile-error">
                                <WarningCircle size={19} className="mt-0.5 shrink-0" weight="fill" /> {error}
                            </div>
                        )}

                        <section className="rounded-2xl border border-slate-200 bg-white p-4 text-center shadow-sm" data-testid="supplier-receiving-mobile-scan-launcher">
                            <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700"><Barcode size={36} weight="duotone" /></span>
                            <h3 className="mt-3 text-xl font-black text-slate-950">مسح قطعة جديدة</h3>
                            <p className="mt-1 text-sm font-bold text-slate-500">امسح رمز القطعة لإضافتها إلى فاتورة المورد</p>
                            <button type="button" onClick={() => { setError(""); setCameraOpen(true); }} disabled={busy === "scan" || sessionCancelling} className="mt-4 inline-flex min-h-[52px] w-full items-center justify-center gap-2 rounded-xl bg-emerald-700 px-5 text-base font-black text-white disabled:opacity-50" data-testid="supplier-receiving-camera-button-mobile">
                                <Camera size={23} weight="duotone" /> فتح الكاميرا ومسح القطعة
                            </button>
                            <button type="button" onClick={() => setManualEntryOpen((value) => !value)} disabled={busy === "scan" || sessionCancelling} className="mt-2 inline-flex min-h-12 w-full items-center justify-center gap-2 rounded-xl border-2 border-emerald-600 bg-white px-5 text-sm font-black text-emerald-800 disabled:opacity-50">
                                <Keyboard size={21} /> إدخال الرمز يدويًا
                            </button>
                            {manualEntryOpen && (
                                <form onSubmit={scanPiece} className="mt-3 flex gap-2" data-testid="supplier-receiving-mobile-manual-form">
                                    <input value={barcode} onChange={(event) => setBarcode(event.target.value)} autoComplete="off" placeholder="رمز القطعة" className="min-h-11 min-w-0 flex-1 rounded-xl border border-emerald-300 px-3 font-mono text-sm font-black outline-none focus:border-emerald-600" />
                                    <button type="submit" disabled={!barcode.trim() || busy === "scan"} className="rounded-xl bg-emerald-700 px-4 text-sm font-black text-white disabled:opacity-50">إضافة</button>
                                </form>
                            )}
                        </section>

                        {lastScan && (
                            <div className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-black text-emerald-800" data-testid="supplier-receiving-mobile-last-success">
                                <CheckCircle size={22} weight="fill" /> تمت إضافة {lastScan.product_name || "القطعة"} إلى الفاتورة
                            </div>
                        )}

                        <SupplierInvoiceCompactTable
                            invoiceLines={invoiceLines}
                            permissions={data?.permissions || {}}
                            serviceCatalog={activeServiceCatalog}
                            onProductPriceChange={changeProductPrice}
                            onServicePriceChange={changeServicePrice}
                            onServiceToggle={toggleService}
                            onServiceAdd={addService}
                        />

                        <details className="group rounded-2xl border border-slate-200 bg-white shadow-sm">
                            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-black text-slate-800"><span>التعليمات والمسؤوليات</span><CaretDown size={18} className="transition-transform group-open:rotate-180" /></summary>
                            <div className="space-y-3 border-t border-slate-100 p-4 text-xs font-bold leading-5 text-slate-600">
                                <p>موظف التجهيز محفوظ أصلًا مع كل قطعة، وموظف الاستلام هو صاحب هذه الجلسة.</p>
                                <p>الاعتماد ينشئ فاتورة ومديونية داخل ميزان 2 فقط، ولا يرسل شيئًا إلى قيود أو سلة.</p>
                                <textarea value={closeNote} onChange={(event) => setCloseNote(event.target.value)} rows={2} placeholder="ملاحظة الإغلاق — اختياري" className="w-full rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-emerald-500" />
                            </div>
                        </details>

                        <footer className="sticky bottom-0 z-20 -mx-4 border-t border-slate-200 bg-white/95 px-4 py-3 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] backdrop-blur">
                            <div className="mb-2 flex items-center justify-between gap-3"><span className="font-black text-slate-950">الإجمالي</span><span className="text-xl font-black tabular-nums text-emerald-800">{formatSupplierMoney(invoiceTotal)} ر.س</span></div>
                            <button type="button" onClick={closeSession} disabled={invoiceCannotSave} className="min-h-12 w-full rounded-xl bg-emerald-700 px-5 text-base font-black text-white disabled:opacity-50" data-testid="supplier-receiving-save-invoice-mobile">
                                {busy === "close" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />} مراجعة واعتماد الفاتورة
                            </button>
                            <button type="button" onClick={cancelSession} disabled={!!busy} className="mt-1 min-h-9 w-full text-xs font-black text-rose-700 disabled:opacity-50" data-testid="supplier-receiving-cancel-session-mobile">
                                {busy === "cancel" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <XCircle className="ml-1 inline" />} إلغاء الجلسة والخروج
                            </button>
                        </footer>
                    </section>

                    <div className="hidden gap-5 lg:grid xl:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.75fr)]">
                    <div className="space-y-4">
                        <section className="rounded-2xl border border-emerald-300 bg-emerald-50 p-4 shadow-sm" data-testid="supplier-receiving-active-session">
                            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div>
                                    <div className="flex flex-wrap items-center gap-2"><span className={`rounded-full px-2.5 py-1 text-xs font-black text-white ${sessionCancelling ? "bg-rose-700" : "bg-emerald-700"}`}>{sessionCancelling ? "الإلغاء غير مكتمل" : "جلسة مفتوحة"}</span><span className="font-mono text-xs font-bold text-emerald-900">{active.reference}</span></div>
                                    <h3 className="mt-2 text-xl font-black text-emerald-950">{supplierDisplayName(active)}</h3>
                                    <p className="mt-1 text-xs font-bold text-emerald-800">فتحها: {active.opened_by_name || "—"} · {formatReceivingDate(active.opened_at)}</p>
                                </div>
                                <button type="button" onClick={() => load()} disabled={loading || !!busy} className="inline-flex min-h-10 items-center justify-center gap-2 rounded-xl border border-emerald-300 bg-white px-3 text-xs font-black text-emerald-900 disabled:opacity-50"><ArrowClockwise size={17} className={loading ? "animate-spin" : ""} /> تحديث</button>
                            </div>
                            <form onSubmit={scanPiece} className="mt-5 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]" data-testid="supplier-receiving-scan-form">
                                <label className="relative block">
                                    <Barcode size={22} className="absolute right-4 top-1/2 -translate-y-1/2 text-emerald-700" />
                                    <input ref={barcodeRef} value={barcode} onChange={(event) => setBarcode(event.target.value)} autoComplete="off" inputMode="text" placeholder="امسح QR القطعة هنا" disabled={busy === "scan" || sessionCancelling} className="min-h-14 w-full rounded-2xl border-2 border-emerald-300 bg-white pr-12 pl-4 font-mono text-base font-black outline-none focus:border-emerald-600 focus:ring-4 focus:ring-emerald-100 disabled:opacity-50" data-testid="supplier-receiving-barcode-input" />
                                </label>
                                <button type="button" onClick={() => { setError(""); setCameraOpen(true); }} disabled={busy === "scan" || sessionCancelling} className="inline-flex min-h-14 items-center justify-center gap-2 rounded-2xl border-2 border-emerald-600 bg-white px-5 text-base font-black text-emerald-800 disabled:opacity-50" data-testid="supplier-receiving-camera-button">
                                    <Camera size={22} weight="duotone" /> فتح الكاميرا
                                </button>
                                <button type="submit" disabled={!barcode.trim() || busy === "scan" || sessionCancelling} className="min-h-14 rounded-2xl bg-emerald-700 px-7 text-base font-black text-white disabled:opacity-50">
                                    {busy === "scan" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <CheckCircle className="ml-1 inline" weight="fill" />} استلام القطعة
                                </button>
                            </form>
                            {sessionCancelling && <div className="mt-3 rounded-xl border border-rose-300 bg-rose-50 p-3 text-xs font-black text-rose-900">تعذر إكمال إلغاء سابق. اضغط «إلغاء الجلسة والخروج» مرة أخرى لإكمال إعادة القطع.</div>}
                            <p className="mt-3 text-xs font-bold leading-5 text-emerald-800">من الجوال اضغط «فتح الكاميرا»، أو استخدم قارئ الباركود مثل لوحة المفاتيح واضغط Enter. ملفات التجهيز المعاد تنزيلها تحمل QR فريدًا لكل قطعة.</p>
                        </section>

                        {lastScan && (
                            <div className="rounded-2xl border-2 border-emerald-400 bg-white p-4 shadow-sm" data-testid="supplier-receiving-last-success">
                                <div className="mb-3 flex items-center gap-2 font-black text-emerald-800"><CheckCircle size={24} weight="fill" /> تمت إضافة القطعة للمسودة ومنع تكرارها</div>
                                <ScanRow scan={lastScan} />
                            </div>
                        )}

                        <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                            <div className="mb-3 flex items-center justify-between gap-3"><div><h3 className="font-black text-slate-950">قطع الجلسة</h3><p className="mt-1 text-xs font-bold text-slate-500">آخر القطع أولًا — إجمالي {Number(active.scan_count || 0)}</p></div><Barcode size={25} className="text-violet-700" /></div>
                            {!scans.length ? <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center text-sm font-bold text-slate-500">لم تُمسح أي قطعة بعد.</div> : <div className="space-y-2">{scans.map((scan) => <ScanRow key={scan.piece_id} scan={scan} />)}</div>}
                        </section>
                    </div>

                    <aside className="space-y-4">
                        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
                            <div className="flex items-center gap-2 font-black text-slate-950"><UserCircle size={23} className="text-violet-700" /> سجل المسؤوليات</div>
                            <div className="mt-4 space-y-3 text-sm font-bold">
                                <div className="rounded-xl bg-violet-50 p-3 text-violet-900">موظف التجهيز محفوظ أصلًا مع كل قطعة ولا يتغير بالمسح.</div>
                                <div className="rounded-xl bg-emerald-50 p-3 text-emerald-900">موظف الاستلام هو صاحب هذه الجلسة ويسجل مستقلًا.</div>
                            </div>
                        </section>
                        <section className="rounded-2xl border border-rose-200 bg-white p-4 shadow-sm">
                            <h3 className="font-black text-slate-950">إنهاء الجلسة</h3>
                            <p className="mt-1 text-xs font-bold leading-5 text-slate-500">احفظ الفاتورة لاعتماد الاستلام، أو ألغِ الجلسة للخروج دون حفظ.</p>
                            <textarea value={closeNote} onChange={(event) => setCloseNote(event.target.value)} rows={3} placeholder="ملاحظة الإغلاق — اختياري" className="mt-3 w-full rounded-xl border border-slate-200 p-3 text-sm font-bold outline-none focus:border-rose-400" />
                            <button type="button" onClick={() => setCameraOpen(true)} disabled={!!busy || !invoiceLines.length || sessionCancelling} className="mt-3 min-h-11 w-full rounded-xl bg-slate-950 px-4 text-sm font-black text-white disabled:opacity-50"><CheckCircle className="ml-1 inline" /> مراجعة الخدمات والأسعار قبل الاعتماد</button>
                            <button type="button" onClick={cancelSession} disabled={!!busy} className="mt-2 min-h-11 w-full rounded-xl border-2 border-rose-300 bg-rose-50 px-4 text-sm font-black text-rose-800 disabled:opacity-50" data-testid="supplier-receiving-cancel-session">
                                {busy === "cancel" ? <SpinnerGap className="ml-1 inline animate-spin" /> : <XCircle className="ml-1 inline" weight="fill" />} إلغاء الجلسة والخروج
                            </button>
                            <p className="mt-2 text-center text-[11px] font-bold text-rose-700">الإلغاء لا ينشئ فاتورة، ويعيد أي قطع صُوّرت إلى حالتها السابقة.</p>
                        </section>
                    </aside>
                    </div>
                </>
            )}

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="supplier-receiving-history">
                <div className="flex items-center gap-2"><ClockCounterClockwise size={23} className="text-violet-700" /><div><h3 className="font-black text-slate-950">سجل فواتير المورد المعتمدة</h3><p className="mt-1 text-xs font-bold text-slate-500">الجلسات الجديدة ترتبط بفاتورة محاسبية ومديونية داخل ميزان 2؛ وتبقى المسودات التشغيلية السابقة مميزة بوضوح.</p></div></div>
                {!closedSessions.length ? <div className="mt-4 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm font-bold text-slate-500">لا توجد جلسات مغلقة بعد.</div> : <div className="mt-4 grid gap-3 lg:grid-cols-2">{closedSessions.map((session) => (
                    <article key={session.id} className="rounded-2xl border border-slate-200 p-4">
                        <div className="flex items-start justify-between gap-3"><div><div className="font-black text-slate-950">{supplierDisplayName(session)}</div><div className="mt-1 font-mono text-xs font-bold text-violet-700">{session.reference}</div></div><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-black text-slate-700">{session.scan_count} قطعة</span></div>
                        <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs font-bold text-slate-500"><span>اعتمدها {session.closed_by_name || session.opened_by_name || "—"} · {formatReceivingDate(session.closed_at)}</span>{session.supplier_invoice && <span className="rounded-full bg-emerald-50 px-2.5 py-1 font-black text-emerald-800">{session.supplier_invoice.invoice_number ? `${session.supplier_invoice.invoice_number} · ` : ""}{formatSupplierMoney(session.supplier_invoice.total_halalas)} ر.س · مديونية</span>}{!session.supplier_invoice && session.operational_invoice && <span className="rounded-full bg-amber-50 px-2.5 py-1 font-black text-amber-800">مسودة تشغيلية سابقة · {formatSupplierMoney(session.operational_invoice.total_halalas)} ر.س</span>}</div>
                    </article>
                ))}</div>}
            </section>

            {cameraOpen && active && (
                <SupplierPieceCameraScanner
                    onDetected={handleCameraDetected}
                    onClose={() => setCameraOpen(false)}
                    onCancel={cancelSession}
                    onSave={closeSession}
                    cancelling={busy === "cancel"}
                    saving={busy === "close"}
                    scanning={busy === "scan"}
                    error={error}
                    lastScan={lastScan}
                    invoiceLines={invoiceLines}
                    permissions={data?.permissions || {}}
                    serviceCatalog={activeServiceCatalog}
                    onProductPriceChange={changeProductPrice}
                    onServicePriceChange={changeServicePrice}
                    onServiceToggle={toggleService}
                    onServiceAdd={addService}
                    supplierName={supplierDisplayName(active)}
                    employeeName={active.opened_by_name || ""}
                    manualBarcode={barcode}
                    onManualBarcodeChange={setBarcode}
                    onManualSubmit={() => receivePiece(barcode, { refocus: false })}
                />
            )}
        </section>
    );
}
