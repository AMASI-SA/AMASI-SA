import { useCallback, useEffect, useRef, useState } from "react";
import {
    ArrowLeft, ArrowSquareOut, CaretLeft, CaretRight, CheckCircle, Clipboard, Eye, EyeSlash,
    FloppyDisk, MagnifyingGlass, Plus, SpinnerGap, WarningCircle, WhatsappLogo, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    completeOrderReview,
    createOrderReviewOperationalItem,
    getOrderReview,
    listPendingOrderReviews,
    unlinkOrderReviewOperationalItem,
    updateOrderReviewItem,
    updateOrderReviewOperationalItemStatus,
    saveOrderReviewImageChoice,
} from "../services/orderReviewEngine";
import CustomerServiceInstructionBanner from "../components/fulfillment/CustomerServiceInstructionBanner";

function money(value, currency = "SAR") {
    const amount = Number(value || 0);
    return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function paymentText(order) {
    return String(order?.payment?.method_native || order?.payment?.method || "غير محدد").trim();
}

function rowTone(order) {
    const method = paymentText(order).toLowerCase();
    if (method === "cod" || method.includes("cash on delivery") || method.includes("الدفع عند الاستلام")) {
        return "bg-rose-50 hover:bg-rose-100/70";
    }
    if (method.includes("bank transfer") || method.includes("تحويل بنكي") || method.includes("حوالة بنكية")) {
        return "bg-amber-50 hover:bg-amber-100/70";
    }
    return "bg-white hover:bg-slate-50";
}

function copy(value, label = "القيمة") {
    navigator.clipboard.writeText(String(value || ""));
    toast.success(`تم نسخ ${label}`);
}

function Field({ label, value, dir }) {
    return (
        <div className="rounded-xl bg-slate-50 p-3">
            <div className="text-xs font-bold text-slate-400">{label}</div>
            <div className="mt-1 break-words font-semibold text-slate-800" dir={dir}>{value || "—"}</div>
        </div>
    );
}

function normalizedSpecName(value) {
    return String(value || "")
        .trim()
        .toLocaleLowerCase("ar")
        .replace(/[ـ:：\s_-]+/g, " ");
}

function canonicalSpecName(value) {
    const normalized = normalizedSpecName(value);
    if (["لون", "اللون", "لون المنتج", "اللون المنتج"].includes(normalized)) return "color";
    if (["مقاس", "المقاس", "مقاس المنتج", "المقاس المنتج"].includes(normalized)) return "size";
    return normalized;
}

function visibleSpecValue(value, depth = 0) {
    if (depth > 6 || value === null || value === undefined || value === "") return "";
    if (typeof value === "boolean") return value ? "نعم" : "لا";
    if (typeof value === "string" || typeof value === "number") return String(value).trim();
    if (Array.isArray(value)) {
        return value.map((entry) => visibleSpecValue(entry, depth + 1)).filter(Boolean).join(" / ");
    }
    if (typeof value === "object") {
        for (const key of ["value", "answer", "selected", "choice", "text", "name", "label", "title", "option_value", "response"]) {
            const visible = visibleSpecValue(value[key], depth + 1);
            if (visible) return visible;
        }
        if (["url", "file_url", "download_url", "src"].some((key) => visibleSpecValue(value[key], depth + 1))) {
            return "مرفق";
        }
    }
    return "";
}

const CUSTOM_FIELD_META_KEYS = new Set([
    "id", "type", "required", "created_at", "updated_at",
    "name", "label", "title", "question", "key", "option",
    "value", "answer", "selected", "choice", "text", "values",
    "option_value", "response", "file", "attachment", "url",
]);

export function reviewProductSpecs(item) {
    const specs = [];
    const seen = new Set();
    const add = (name, value) => {
        const cleanName = String(name || "").trim();
        const cleanValue = visibleSpecValue(value);
        if (!cleanName || !cleanValue) return;
        const key = canonicalSpecName(cleanName);
        if (!key || seen.has(key)) return;
        seen.add(key);
        specs.push({ name: cleanName, value: cleanValue, key });
    };

    (Array.isArray(item.options) ? item.options : []).forEach((option) => add(option?.name, option?.value));
    (Array.isArray(item.custom_fields) ? item.custom_fields : []).forEach((field) => {
        if (!field || typeof field !== "object" || Array.isArray(field)) return;
        const label = field.name || field.label || field.title || field.question || field.key || field.option;
        const value = field.value ?? field.answer ?? field.selected ?? field.choice ?? field.text
            ?? field.values ?? field.option_value ?? field.response ?? field.file ?? field.attachment ?? field.url;
        if (label && visibleSpecValue(value)) {
            add(label, value);
            return;
        }
        Object.entries(field).forEach(([key, rawValue]) => {
            if (!CUSTOM_FIELD_META_KEYS.has(key)) add(key, rawValue);
        });
    });
    add("اللون", item.color);
    add("المقاس", item.size);
    add("الخامة", item.material);
    return specs;
}

function imageIdentity(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const normalizePath = (pathname) => {
        const decoded = decodeURIComponent(pathname || "").toLowerCase();
        const filename = decoded.split("/").filter(Boolean).pop() || decoded;
        return filename
            .replace(/[-_](?:thumb|thumbnail|small|medium|large|original)(?=\.|$)/g, "")
            .replace(/[-_]\d{2,4}x\d{2,4}(?=\.|$)/g, "")
            .replace(/\.(?:webp|avif)$/g, ".jpg");
    };
    try {
        const url = new URL(raw);
        return normalizePath(url.pathname);
    } catch {
        return normalizePath(raw.split(/[?#]/, 1)[0]);
    }
}

function safeReceiptUrl(value) {
    try {
        const url = new URL(String(value || "").trim());
        return ["http:", "https:"].includes(url.protocol) ? url.toString() : "";
    } catch {
        return "";
    }
}

export function PaymentReceiptCard({ receiptUrl }) {
    const url = safeReceiptUrl(receiptUrl);
    if (!url) return null;
    const isPdf = /\.pdf(?:$|[?#])/i.test(url);
    return (
        <div data-testid="order-review-payment-receipt" className="overflow-hidden rounded-xl border border-amber-200 bg-amber-50">
            <div className="border-b border-amber-200 px-3 py-2 text-sm font-extrabold text-amber-950">صورة إيصال التحويل</div>
            {!isPdf && (
                <a href={url} target="_blank" rel="noreferrer" className="block bg-white p-3">
                    <img src={url} alt="إيصال التحويل البنكي" loading="lazy" className="mx-auto max-h-72 w-full rounded-lg object-contain" />
                </a>
            )}
            <a href={url} target="_blank" rel="noreferrer" className="flex items-center justify-center gap-1.5 px-3 py-2.5 text-sm font-extrabold text-amber-900 hover:bg-amber-100">
                <Eye size={17} /> فتح الإيصال بالحجم الكامل
            </a>
        </div>
    );
}

function ProductReviewCard({ item, workflowRevision, orderNumber, onChanged, onCreateOperationalItem }) {
    const [preparationNote, setPreparationNote] = useState(item.preparation_note || "");
    const [internalNote, setInternalNote] = useState(item.internal_note || "");
    const [showPreparationNote, setShowPreparationNote] = useState(false);
    const [showInternalNote, setShowInternalNote] = useState(false);
    const [busy, setBusy] = useState(false);
    const [visibleSelectedImage, setVisibleSelectedImage] = useState(item.selected_image_url || item.image_url || "");
    const [imageDialog, setImageDialog] = useState(null);
    const [selectedImageSpecKeys, setSelectedImageSpecKeys] = useState([]);
    const [imageSavingMode, setImageSavingMode] = useState(null);

    useEffect(() => {
        setPreparationNote(item.preparation_note || "");
        setInternalNote(item.internal_note || "");
    }, [item.internal_note, item.preparation_note]);

    useEffect(() => {
        if (item.selected_image_url) setVisibleSelectedImage(item.selected_image_url);
    }, [item.selected_image_url]);

    const save = async (patch, successMessage) => {
        setBusy(true);
        try {
            const next = await updateOrderReviewItem(orderNumber, item.order_item_id, {
                expected_revision: workflowRevision,
                ...patch,
            });
            onChanged(next);
            toast.success(successMessage);
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };

    const specs = reviewProductSpecs(item);
    const selectedIdentity = imageIdentity(visibleSelectedImage);
    const openImageDialog = (url) => {
        setVisibleSelectedImage(url);
        setImageDialog(url);
        setSelectedImageSpecKeys([]);
    };
    const saveImageChoice = async (mode) => {
        if (!imageDialog) return;
        if (mode === "options" && !selectedImageSpecKeys.length) {
            toast.error("اختر خيارًا واحدًا على الأقل");
            return;
        }
        setBusy(true);
        setImageSavingMode(mode);
        try {
            const next = await saveOrderReviewImageChoice(orderNumber, item.order_item_id, {
                expected_revision: workflowRevision,
                selected_image_url: imageDialog,
                mode,
                selected_spec_keys: mode === "options" ? selectedImageSpecKeys : [],
            });
            setVisibleSelectedImage(imageDialog);
            setImageDialog(null);
            onChanged(next);
            toast.success(mode === "order_only" ? "تم حفظ الصورة لهذا الطلب فقط." : mode === "default" ? "تم حفظ الصورة كرئيسية للمنتج في ميزان." : "تم حفظ الصورة مع الخيارات المحددة.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
            setImageSavingMode(null);
        }
    };
    const sourceGallery = (item.gallery || []).filter(Boolean);
    const gallery = [];
    const seenImageIdentities = new Set();
    for (const url of sourceGallery) {
        const identity = imageIdentity(url);
        if (!identity || identity === selectedIdentity || seenImageIdentities.has(identity)) continue;
        seenImageIdentities.add(identity);
        gallery.push(url);
    }
    return (
        <article data-testid="order-review-product-card" className="min-w-0 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            {imageDialog && (
                <div className="fixed inset-0 z-[140] flex items-center justify-center bg-slate-950/50 p-3" dir="rtl">
                    <div className="max-h-[92vh] w-full max-w-xl overflow-auto rounded-3xl bg-white shadow-2xl">
                        <div className="flex items-center justify-between border-b p-4"><div><h3 className="font-extrabold">حفظ صورة التجهيز</h3><p className="text-xs text-slate-500">اختر طريقة استخدام الصورة داخل ميزان.</p></div><button type="button" onClick={() => setImageDialog(null)} className="rounded-xl border p-2"><X /></button></div>
                        <div className="space-y-4 p-4">
                            <img src={imageDialog} alt="" className="mx-auto h-40 w-40 rounded-2xl border object-cover" />
                            {specs.length > 0 && <div><div className="mb-2 text-sm font-extrabold">الخيارات التي تُحفظ معها الصورة</div><div className="space-y-2">{specs.map((spec) => <label key={spec.key} className="flex items-start gap-2 rounded-xl bg-violet-50 p-3 text-sm"><input type="checkbox" checked={selectedImageSpecKeys.includes(spec.key)} onChange={(event) => setSelectedImageSpecKeys((current) => event.target.checked ? [...new Set([...current, spec.key])] : current.filter((key) => key !== spec.key))} /><span><b>{spec.name}:</b> {spec.value}</span></label>)}</div></div>}
                            <div className="grid gap-2">
                                <button type="button" disabled={busy} onClick={() => saveImageChoice("order_only")} className="inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-3 font-extrabold disabled:opacity-50">{imageSavingMode === "order_only" && <SpinnerGap className="animate-spin" />} {imageSavingMode === "order_only" ? "جارٍ الحفظ…" : "حفظ لهذا الطلب فقط"}</button>
                                <button type="button" disabled={busy || !selectedImageSpecKeys.length} onClick={() => saveImageChoice("options")} className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-4 py-3 font-extrabold text-white disabled:opacity-40">{imageSavingMode === "options" && <SpinnerGap className="animate-spin" />} {imageSavingMode === "options" ? "جارٍ الحفظ…" : "حفظ مع الخيارات المحددة"}</button>
                                <button type="button" disabled={busy || selectedImageSpecKeys.length > 0} onClick={() => saveImageChoice("default")} className="inline-flex items-center justify-center gap-2 rounded-xl bg-emerald-700 px-4 py-3 font-extrabold text-white disabled:cursor-not-allowed disabled:opacity-40">{imageSavingMode === "default" && <SpinnerGap className="animate-spin" />} {imageSavingMode === "default" ? "جارٍ الحفظ…" : "حفظ كصورة رئيسية في ميزان"}</button>
                            </div>
                            {selectedImageSpecKeys.length > 0 && <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs"><b>سيتم ربطها بـ:</b> {specs.filter((spec) => selectedImageSpecKeys.includes(spec.key)).map((spec) => `${spec.name} = ${spec.value}`).join(" · ")}</div>}
                        </div>
                    </div>
                </div>
            )}
            <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-3 p-4 sm:grid-cols-[128px_minmax(0,1fr)]">
                <div>
                    <div className="aspect-square overflow-hidden rounded-xl bg-slate-100">
                        {visibleSelectedImage ? (
                            <img src={visibleSelectedImage} alt={item.name} className="h-full w-full object-cover" />
                        ) : (
                            <div className="flex h-full items-center justify-center text-sm text-slate-400">لا توجد صورة</div>
                        )}
                    </div>
                </div>
                <div className="min-w-0">
                    <h3 className="break-words text-base font-extrabold leading-7 text-slate-900 sm:text-lg">{item.name}</h3>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                        <span>SKU: <b dir="ltr">{item.sku || "—"}</b></span>
                        <span>الكمية: <b>{item.quantity}</b></span>
                    </div>
                    {gallery.length > 0 && (
                    <div className="mt-4">
                        <div className="flex gap-2 overflow-x-auto pb-2">
                            {gallery.map((url, index) => {
                                const selected = imageIdentity(url) === selectedIdentity;
                                return (
                                    <button
                                        type="button"
                                        key={`${url}-${index}`}
                                        disabled={busy || selected}
                                        onClick={() => openImageDialog(url)}
                                        className={`h-16 w-16 shrink-0 overflow-hidden rounded-xl border-2 sm:h-20 sm:w-20 ${selected ? "border-teal-500 ring-2 ring-teal-100" : "border-slate-200 hover:border-violet-400"}`}
                                        aria-label={`اختيار صورة التجهيز رقم ${index + 1}`}
                                    >
                                        <img src={url} alt="" className="h-full w-full object-cover" />
                                    </button>
                                );
                            })}
                        </div>
                    </div>
                    )}
                </div>
            </div>
            <div className="border-t border-slate-100 px-4 py-3">
                <div className="mb-2 text-sm font-extrabold text-slate-700">مواصفات المنتج</div>
                {specs.length > 0 ? (
                    <div data-testid="order-review-product-specs" className="grid gap-2">
                        {specs.map((spec) => (
                            <div key={spec.key} className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-2 rounded-xl bg-violet-50 px-3 py-2 text-sm">
                                <span className="shrink-0 font-bold text-violet-700">{spec.name}:</span>
                                <span className="min-w-0 whitespace-pre-wrap break-words font-extrabold leading-6 text-slate-900">{spec.value}</span>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className="text-sm text-slate-400">لا توجد مواصفات إضافية</div>
                )}
            </div>
            <div className="border-t bg-slate-50/70 p-4">
                <div className="flex flex-wrap gap-2">
                    <button
                        type="button"
                        onClick={() => onCreateOperationalItem(item, specs)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-extrabold text-amber-900 hover:bg-amber-100"
                    >
                        <Plus size={15} /> إضافة منتج تشغيلي
                    </button>
                    <button
                        type="button"
                        aria-expanded={showPreparationNote}
                        onClick={() => setShowPreparationNote((visible) => !visible)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-teal-200 bg-white px-2.5 py-1.5 text-xs font-extrabold text-teal-800 hover:bg-teal-50"
                    >
                        {showPreparationNote ? <EyeSlash size={15} /> : <Eye size={15} />}
                        تعليمات التجهيز
                        {item.preparation_note && <span className="rounded-full bg-teal-100 px-1.5 py-0.5 text-[10px]">محفوظ</span>}
                    </button>
                    <button
                        type="button"
                        aria-expanded={showInternalNote}
                        onClick={() => setShowInternalNote((visible) => !visible)}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-violet-200 bg-white px-2.5 py-1.5 text-xs font-extrabold text-violet-800 hover:bg-violet-50"
                    >
                        {showInternalNote ? <EyeSlash size={15} /> : <Eye size={15} />}
                        ملاحظة داخلية
                        {item.internal_note && <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-[10px]">محفوظ</span>}
                    </button>
                </div>

                {(showPreparationNote || showInternalNote) && (
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        {showPreparationNote && (
                            <label className="block">
                                <span className="mb-1 block text-sm font-extrabold text-slate-700">تعليمات التجهيز — تُطبع مع القطعة</span>
                                <textarea value={preparationNote} onChange={(event) => setPreparationNote(event.target.value)} maxLength={1200} className="min-h-24 w-full rounded-xl border border-slate-200 bg-white p-3 outline-none focus:border-teal-500" placeholder="مثال: اعتماد الصورة الفضية، مع تغليف الورد…" />
                            </label>
                        )}
                        {showInternalNote && (
                            <label className="block">
                                <span className="mb-1 block text-sm font-extrabold text-slate-700">ملاحظة داخلية — لا تُطبع</span>
                                <textarea value={internalNote} onChange={(event) => setInternalNote(event.target.value)} maxLength={2000} className="min-h-24 w-full rounded-xl border border-slate-200 bg-white p-3 outline-none focus:border-violet-500" placeholder="ملاحظة للموظفين فقط" />
                            </label>
                        )}
                        <button
                            type="button"
                            disabled={busy}
                            onClick={() => save({ preparation_note: preparationNote.trim() || null, internal_note: internalNote.trim() || null }, "تم حفظ ملاحظات المنتج.")}
                            className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-bold text-white disabled:opacity-50 sm:col-span-2 sm:justify-self-end"
                        >
                            {busy ? <SpinnerGap className="animate-spin" /> : <FloppyDisk />}
                            حفظ الملاحظات
                        </button>
                    </div>
                )}
            </div>
        </article>
    );
}


function OperationalItemCard({ item, workflowRevision, orderNumber, onChanged }) {
    const [busy, setBusy] = useState(false);
    const statusLabel = "بانتظار التجميع والعنونة";
    const rename = async () => {
        const nextName = window.prompt("اسم المنتج التشغيلي", item.name || "");
        if (!nextName || nextName.trim() === item.name) return;
        setBusy(true);
        try {
            const next = await updateOrderReviewOperationalItemStatus(orderNumber, item.operational_item_id, {
                expected_revision: workflowRevision,
                name: nextName.trim(),
            });
            onChanged(next);
            toast.success("تم تعديل اسم المنتج التشغيلي.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };
    const unlink = async () => {
        if (!window.confirm("إلغاء الربط وإرجاع الحقول إلى المنتج الأصلي؟")) return;
        setBusy(true);
        try {
            const next = await unlinkOrderReviewOperationalItem(orderNumber, item.operational_item_id, workflowRevision);
            onChanged(next);
            toast.success("تم إلغاء الربط وإرجاع الحقول إلى المنتج الأصلي.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setBusy(false);
        }
    };
    return (
        <article className="overflow-hidden rounded-2xl border-2 border-dashed border-amber-300 bg-amber-50/60 shadow-sm" data-testid="order-review-operational-item">
            <div className="p-4">
                <div className="flex items-start justify-between gap-3">
                    <div>
                        <div className="text-xs font-extrabold text-amber-700">منتج تشغيلي داخلي</div>
                        <h3 className="mt-1 text-lg font-extrabold text-slate-900">{item.name}</h3>
                        <div className="mt-1 text-xs text-slate-500">مرتبط بـ: {item.source_product_name || "منتج الطلب"}</div>
                    </div>
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-extrabold text-amber-800">{statusLabel}</span>
                </div>
                <div className="mt-4 grid gap-2">
                    {(item.linked_specs || []).map((spec) => (
                        <div key={`${spec.key}:${spec.value}`} className="rounded-xl bg-white px-3 py-2 text-sm">
                            <span className="font-bold text-violet-700">{spec.name}: </span>
                            <span className="whitespace-pre-wrap break-words font-extrabold text-slate-900">{spec.value}</span>
                        </div>
                    ))}
                </div>
                <div className="mt-4 rounded-xl bg-white/80 p-3 text-xs font-bold leading-6 text-slate-600">
                    يظهر داخل ميزان فقط، ولا يُرسل إلى سلة أو قيود أو ملف المورد. تُسجل جاهزيته من صفحة التجميع والعنونة فقط، ويمنع طباعة الشحنة حتى يصبح جاهزًا.
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                    <button disabled={busy} onClick={rename} className="rounded-lg border border-violet-300 bg-white px-3 py-2 text-xs font-extrabold text-violet-800 disabled:opacity-50">تعديل الاسم</button>
                    <button disabled={busy} onClick={unlink} data-testid="order-review-operational-item-unlink" className="rounded-lg border border-rose-300 bg-white px-3 py-2 text-xs font-extrabold text-rose-700 disabled:opacity-50">إلغاء الربط وإرجاع القيم</button>
                </div>
            </div>
        </article>
    );
}

function ReviewDrawer({ orderNumber, onClose, onCompleted }) {
    const [detail, setDetail] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [completing, setCompleting] = useState(false);
    const [operationalDialog, setOperationalDialog] = useState(null);
    const [operationalName, setOperationalName] = useState("كرت إهداء");
    const [linkedSpecKeys, setLinkedSpecKeys] = useState([]);
    const [creatingOperational, setCreatingOperational] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            setDetail(await getOrderReview(orderNumber));
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => { load(); }, [load]);

    const order = detail?.order;
    const customer = order?.customer || {};
    const payment = order?.payment || {};
    const paymentMethod = paymentText(order).toLowerCase();
    const isBankTransfer = paymentMethod === "bank" || paymentMethod.includes("bank transfer")
        || paymentMethod.includes("تحويل بنكي") || paymentMethod.includes("حوالة بنكية");
    const shipping = order?.shipping || {};
    const address = shipping.address || customer.shipping_address || {};
    const sallaAdminUrl = safeReceiptUrl(order?.salla_admin_url);
    const whatsapp = String(customer.mobile || "").replace(/\D/g, "");

    const openOperationalDialog = (item, specs) => {
        setOperationalDialog({ item, specs });
        setOperationalName("كرت إهداء");
        setLinkedSpecKeys(specs.map((spec) => spec.key));
    };

    const createOperational = async () => {
        if (!operationalDialog || !operationalName.trim()) return;
        setCreatingOperational(true);
        try {
            const next = await createOrderReviewOperationalItem(orderNumber, {
                expected_revision: detail.revision,
                source_order_item_id: operationalDialog.item.order_item_id,
                name: operationalName.trim(),
                linked_spec_keys: linkedSpecKeys,
            });
            setDetail(next);
            setOperationalDialog(null);
            toast.success("تمت إضافة المنتج التشغيلي وربط بياناته.");
        } catch (error) {
            toast.error(error.message);
        } finally {
            setCreatingOperational(false);
        }
    };

    const finish = async () => {
        setCompleting(true);
        try {
            const result = await completeOrderReview(orderNumber, detail.revision);
            if (result.salla_status_sync === "pending") {
                toast.warning("تم اعتماد المراجعة في ميزان، وتغيير حالة سلة بانتظار المزامنة.");
            } else {
                toast.success("تمت مراجعة الطلب وانتقل من هذه المرحلة.");
            }
            onCompleted(orderNumber);
        } catch (finishError) {
            toast.error(finishError.message);
            await load();
        } finally {
            setCompleting(false);
        }
    };

    return (
        <div className="fixed inset-0 z-[80] flex bg-slate-950/45" dir="rtl">
            <button type="button" className="hidden flex-1 md:block" onClick={onClose} aria-label="إغلاق" />
            <section className="h-full w-full overflow-y-auto bg-slate-50 shadow-2xl md:max-w-7xl">
                <header className="sticky top-0 z-10 flex items-center justify-between border-b bg-white/95 px-5 py-4 backdrop-blur">
                    <div>
                        <h2 className="text-xl font-extrabold">مراجعة الطلب #{orderNumber}</h2>
                        <p className="text-sm text-slate-500">المرحلة الأولى — لا يتم إنشاء ملف تجهيز هنا</p>
                    </div>
                    <button type="button" onClick={onClose} className="rounded-xl border p-2"><X size={22} /></button>
                </header>
                {loading ? (
                    <div className="flex min-h-96 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>
                ) : error ? (
                    <div className="m-6 rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>
                ) : (
                    <div className="space-y-5 p-4 sm:p-6">
                        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border bg-white p-4">
                            <div><b className="text-lg">#{order.order_number}</b><div className="text-sm text-slate-500">{new Date(order.created_at).toLocaleString("ar-SA")}</div></div>
                            <div className="flex flex-wrap gap-2">
                                {sallaAdminUrl && <a href={sallaAdminUrl} target="_blank" rel="noreferrer" data-testid="order-review-open-in-salla" className="inline-flex items-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 font-extrabold text-teal-900"><ArrowSquareOut /> فتح الطلب في سلة</a>}
                                <button type="button" onClick={() => copy(order.order_number, "رقم الطلب")} className="inline-flex items-center gap-2 rounded-xl border px-3 py-2 font-bold"><Clipboard /> نسخ رقم الطلب</button>
                            </div>
                        </div>

                        <CustomerServiceInstructionBanner
                            instructions={detail.customer_service_instructions || []}
                            stage="pending_review"
                            onUpdated={load}
                        />

                        <section className="rounded-2xl border bg-white p-4">
                            <h3 className="mb-3 text-lg font-extrabold">معلومات العميل</h3>
                            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                                <Field label="الاسم" value={customer.name} />
                                <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">رقم الجوال</div><div className="mt-1 flex items-center gap-2 font-semibold" dir="ltr"><span>{customer.mobile || "—"}</span>{whatsapp && <a href={`https://wa.me/${whatsapp}`} target="_blank" rel="noreferrer" className="text-emerald-600"><WhatsappLogo size={22} weight="fill" /></a>}</div></div>
                                <Field label="البريد" value={customer.email} dir="ltr" />
                                <Field label="الدولة" value={address.country || customer.shipping_address?.country} />
                                <Field label="المدينة" value={address.city || customer.shipping_address?.city} />
                            </div>
                        </section>

                        <div className="grid gap-5 lg:grid-cols-2">
                            <section className="rounded-2xl border bg-white p-4">
                                <h3 className="mb-3 text-lg font-extrabold">معلومات الدفع</h3>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="طريقة الدفع" value={paymentText(order)} />
                                    <Field label="إجمالي الطلب" value={money(order.totals?.total, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المدفوع" value={money(payment.paid_amount, order.totals?.currency)} dir="ltr" />
                                    <Field label="المبلغ المتبقي" value={money(payment.remaining_amount, order.totals?.currency)} dir="ltr" />
                                    {payment.receiving_bank_name && <Field label="البنك المستلم" value={payment.receiving_bank_name} />}
                                    {payment.receipt_url ? (
                                        <div className="sm:col-span-2"><PaymentReceiptCard receiptUrl={payment.receipt_url} /></div>
                                    ) : isBankTransfer ? (
                                        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900 sm:col-span-2">
                                            لا توجد صورة إيصال محفوظة في بيانات سلة لهذا الطلب.
                                        </div>
                                    ) : null}
                                </div>
                            </section>
                            <section className="rounded-2xl border bg-white p-4">
                                <h3 className="mb-3 text-lg font-extrabold">معلومات الشحن</h3>
                                <div className="grid gap-3 sm:grid-cols-2">
                                    <Field label="شركة الشحن" value={shipping.company || shipping.method} />
                                    <Field label="المدينة" value={address.city} />
                                    <Field label="الحي" value={address.district} />
                                    <Field label="الشارع" value={address.street} />
                                </div>
                            </section>
                        </div>

                        <section>
                            <div className="mb-3 flex items-center justify-between"><h3 className="text-xl font-extrabold">منتجات الطلب</h3><span className="rounded-full bg-violet-100 px-3 py-1 text-sm font-bold text-violet-800">{detail.items.length} منتج</span></div>
                            <div data-testid="order-review-products-grid" className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                                {detail.items.map((item) => (
                                    <ProductReviewCard key={item.order_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} onCreateOperationalItem={openOperationalDialog} />
                                ))}
                            </div>
                        </section>

                        {(detail.operational_items || []).length > 0 && (
                            <section>
                                <div className="mb-3 flex items-center justify-between"><h3 className="text-xl font-extrabold">المنتجات التشغيلية الداخلية</h3><span className="rounded-full bg-amber-100 px-3 py-1 text-sm font-bold text-amber-900">{detail.operational_items.length} منتج</span></div>
                                <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                                    {detail.operational_items.map((item) => (
                                        <OperationalItemCard key={item.operational_item_id} item={item} workflowRevision={detail.revision} orderNumber={orderNumber} onChanged={setDetail} />
                                    ))}
                                </div>
                            </section>
                        )}

                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4">
                            <p className="text-sm font-semibold text-emerald-900">اعتماد المراجعة يجمّد الصورة والتعليمات المختارة، ويخرج الطلب من هذه الصفحة نهائيًا. لا يتم إنشاء ملف تجهيز أو بوليصة شحن في هذه المرحلة.</p>
                            <button type="button" disabled={completing} onClick={finish} className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-5 py-3 text-lg font-extrabold text-white disabled:opacity-50 sm:w-auto">
                                {completing ? <SpinnerGap className="animate-spin" /> : <CheckCircle weight="fill" />}
                                تمت المراجعة
                            </button>
                        </div>
                    </div>
                )}
                {operationalDialog && (
                    <div className="fixed inset-0 z-[95] flex items-center justify-center bg-slate-950/55 p-4" dir="rtl">
                        <div className="w-full max-w-xl rounded-2xl bg-white p-5 shadow-2xl">
                            <div className="flex items-center justify-between gap-3"><h3 className="text-xl font-extrabold">إضافة منتج تشغيلي</h3><button type="button" onClick={() => setOperationalDialog(null)} className="rounded-lg border p-2"><X /></button></div>
                            <p className="mt-2 text-sm text-slate-500">أنشئ منتجًا داخليًا واربط به الخيارات أو النصوص المطلوبة من المنتج الأصلي.</p>
                            <label className="mt-4 block"><span className="mb-1 block text-sm font-extrabold">اسم المنتج الداخلي</span><input value={operationalName} onChange={(event) => setOperationalName(event.target.value)} maxLength={120} className="w-full rounded-xl border border-slate-200 p-3 outline-none focus:border-amber-500" /></label>
                            <div className="mt-4"><div className="mb-2 text-sm font-extrabold">البيانات التي تنتقل إليه</div><div className="grid gap-2">{operationalDialog.specs.map((spec) => (<label key={spec.key} className="flex items-start gap-3 rounded-xl bg-violet-50 p-3"><input type="checkbox" checked={linkedSpecKeys.includes(spec.key)} onChange={(event) => setLinkedSpecKeys((current) => event.target.checked ? [...new Set([...current, spec.key])] : current.filter((key) => key !== spec.key))} className="mt-1" /><span><b className="text-violet-700">{spec.name}:</b> <span className="whitespace-pre-wrap break-words font-bold">{spec.value}</span></span></label>))}</div></div>
                            <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={() => setOperationalDialog(null)} className="rounded-xl border px-4 py-2 font-bold">إلغاء</button><button type="button" disabled={creatingOperational || !operationalName.trim()} onClick={createOperational} className="inline-flex items-center gap-2 rounded-xl bg-amber-600 px-4 py-2 font-extrabold text-white disabled:opacity-50">{creatingOperational ? <SpinnerGap className="animate-spin" /> : <Plus />} إضافة وربط</button></div>
                        </div>
                    </div>
                )}
            </section>
        </div>
    );
}

export const REVIEW_PAGE_SIZE = 10;

export default function OrderReview({ initialSearch = "" }) {
    const normalizedInitialSearch = String(initialSearch || "").replace(/^#/, "").trim();
    const [orders, setOrders] = useState([]);
    const [currentCursor, setCurrentCursor] = useState(null);
    const [previousCursors, setPreviousCursors] = useState([]);
    const [nextCursor, setNextCursor] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [selectedOrder, setSelectedOrder] = useState(null);
    const [search, setSearch] = useState(normalizedInitialSearch);
    const [searchQuery, setSearchQuery] = useState(normalizedInitialSearch);
    const latestRequestId = useRef(0);

    useEffect(() => {
        if (!normalizedInitialSearch) return;
        setSearch(normalizedInitialSearch);
        setSearchQuery(normalizedInitialSearch);
        setCurrentCursor(null);
        setPreviousCursors([]);
    }, [normalizedInitialSearch]);

    const pageNumber = previousCursors.length + 1;
    const hasPreviousPage = previousCursors.length > 0;

    const load = useCallback(async ({ cursor = null, query = "", background = false } = {}) => {
        const requestId = latestRequestId.current + 1;
        latestRequestId.current = requestId;
        if (!background) {
            setLoading(true);
            setError("");
        }
        try {
            const result = await listPendingOrderReviews({
                limit: REVIEW_PAGE_SIZE,
                cursor: query ? null : cursor,
                search: query,
            });
            if (requestId !== latestRequestId.current) return;
            setOrders(result.items);
            setNextCursor(result.nextCursor);
        } catch (loadError) {
            if (!background && requestId === latestRequestId.current) {
                setError(loadError.message);
            }
        } finally {
            if (!background && requestId === latestRequestId.current) {
                setLoading(false);
            }
        }
    }, []);

    useEffect(() => {
        load({ cursor: currentCursor, query: searchQuery });
    }, [currentCursor, load, searchQuery]);

    useEffect(() => {
        const timeoutId = window.setTimeout(() => {
            setSearchQuery(search.trim());
        }, 350);
        return () => window.clearTimeout(timeoutId);
    }, [search]);

    useEffect(() => {
        const refresh = () => {
            if (searchQuery || document.hidden || !navigator.onLine) return;
            load({ cursor: currentCursor, query: searchQuery, background: true });
        };
        const intervalId = window.setInterval(refresh, 10_000);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", refresh);
        return () => {
            window.clearInterval(intervalId);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", refresh);
        };
    }, [currentCursor, load, searchQuery]);

    const goToPreviousPage = () => {
        if (!hasPreviousPage || loading) return;
        const previousCursor = previousCursors[previousCursors.length - 1] ?? null;
        setPreviousCursors((history) => history.slice(0, -1));
        setCurrentCursor(previousCursor);
        setSearch("");
    };

    const goToNextPage = () => {
        if (searchQuery || !nextCursor || loading) return;
        setPreviousCursors((history) => [...history, currentCursor]);
        setCurrentCursor(nextCursor);
        setSearch("");
    };

    return (
        <div className="mx-auto max-w-7xl space-y-5 p-4" dir="rtl">
            <header className="rounded-2xl border bg-white p-5 shadow-sm">
                <h1 className="text-2xl font-extrabold text-slate-900">طلبات بانتظار المراجعة</h1>
                <p className="mt-1 text-sm text-slate-500">المرحلة الأولى من محرك تجهيز الطلب — مراجعة بيانات العميل والدفع والشحن والمنتجات.</p>
                <p className="mt-1 text-xs font-semibold text-violet-700">يعرض الجدول آخر 10 طلبات في كل صفحة، والبحث برقم الطلب يشمل جميع طلبات انتظار المراجعة.</p>
                <div className="relative mt-4 max-w-xl">
                    <MagnifyingGlass className="absolute right-3 top-3 text-slate-400" />
                    <input
                        value={search}
                        onChange={(event) => {
                            setSearch(event.target.value);
                            setCurrentCursor(null);
                            setPreviousCursors([]);
                        }}
                        inputMode="numeric"
                        dir="ltr"
                        className="w-full rounded-xl border py-2.5 pr-10 pl-3 outline-none focus:border-violet-500"
                        placeholder="ابحث برقم الطلب في جميع طلبات انتظار المراجعة"
                    />
                </div>
            </header>

            <section className="overflow-hidden rounded-2xl border bg-white shadow-sm">
                <div className="hidden grid-cols-[70px_1fr_1fr_1fr_1fr] gap-3 border-b bg-slate-100 px-4 py-3 text-sm font-extrabold text-slate-600 md:grid">
                    <div>تفاصيل</div><div>رقم الطلب</div><div>تاريخ الطلب</div><div>طريقة الدفع</div><div>العميل</div>
                </div>
                {loading ? (
                    <div className="flex min-h-64 items-center justify-center"><SpinnerGap size={32} className="animate-spin text-violet-600" /></div>
                ) : error ? (
                    <div className="m-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>
                ) : orders.length === 0 ? (
                    <div className="flex min-h-64 items-center justify-center text-slate-500">{searchQuery ? "لم يتم العثور على طلب بهذا الرقم ضمن انتظار المراجعة" : "لا توجد طلبات بانتظار المراجعة"}</div>
                ) : orders.map((order) => (
                    <button key={order.order_number} type="button" onClick={() => setSelectedOrder(order.order_number)} className={`grid w-full gap-2 border-b px-4 py-4 text-right last:border-b-0 md:grid-cols-[70px_1fr_1fr_1fr_1fr] md:items-center ${rowTone(order)}`}>
                        <span className="inline-flex items-center gap-1 font-bold text-violet-700"><ArrowLeft /> <span className="md:hidden">التفاصيل</span></span>
                        <span className="font-extrabold" dir="ltr">#{order.order_number}</span>
                        <span className="text-sm text-slate-600">{new Date(order.created_at).toLocaleString("ar-SA")}</span>
                        <span className="font-bold">{paymentText(order)}</span>
                        <span>{order.customer?.name || "عميل بدون اسم"}</span>
                    </button>
                ))}

                {!searchQuery && !loading && !error && (orders.length > 0 || hasPreviousPage) && (
                    <div className="flex flex-col gap-3 border-t border-slate-200 bg-slate-50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <div className="text-xs font-bold text-slate-500">10 طلبات كحد أقصى في الصفحة</div>
                        <div className="flex items-center justify-center gap-2" aria-label="التنقل بين صفحات الطلبات">
                            <button
                                type="button"
                                onClick={goToPreviousPage}
                                disabled={!hasPreviousPage || loading}
                                aria-label="الصفحة السابقة"
                                className="inline-flex h-10 items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-extrabold text-slate-700 transition hover:border-violet-300 hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                <CaretRight size={18} weight="bold" />
                                السابق
                            </button>
                            <span className="min-w-24 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2 text-center text-sm font-extrabold text-violet-800">
                                الصفحة {pageNumber}
                            </span>
                            <button
                                type="button"
                                onClick={goToNextPage}
                                disabled={!nextCursor || loading}
                                aria-label="الصفحة التالية"
                                className="inline-flex h-10 items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 text-sm font-extrabold text-slate-700 transition hover:border-violet-300 hover:text-violet-700 disabled:cursor-not-allowed disabled:opacity-40"
                            >
                                التالي
                                <CaretLeft size={18} weight="bold" />
                            </button>
                        </div>
                    </div>
                )}
            </section>
            <div className="rounded-xl border border-slate-200 bg-white p-3 text-sm text-slate-600">
                <b>دليل الألوان:</b> أحمر للدفع عند الاستلام، أصفر للتحويل البنكي، وأبيض لبقية طرق الدفع.
            </div>
            {selectedOrder && (
                <ReviewDrawer
                    orderNumber={selectedOrder}
                    onClose={() => setSelectedOrder(null)}
                    onCompleted={(orderNumber) => {
                        setOrders((current) => current.filter((order) => order.order_number !== orderNumber));
                        setSelectedOrder(null);
                        load({ cursor: currentCursor, background: true });
                    }}
                />
            )}
        </div>
    );
}
