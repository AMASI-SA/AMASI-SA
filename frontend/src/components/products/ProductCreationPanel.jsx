import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    CheckCircle, Eye, Package, Plus, RocketLaunch, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveProductCreationDraft,
    createProductCreationDraft,
    listProductCreationDrafts,
    getProductCreationDraft,
    previewProductCreationDraft,
    publishProductCreationDraft,
    updateProductCreationDraft,
} from "../../services/mezanProductsV2";
import {
    FROZEN_FULFILLMENT_TYPE,
    FROZEN_INVENTORY_POLICY,
    PRODUCT_OPERATION_CHOICES_ENABLED,
    applyFrozenProductOperationDefaults,
} from "../../lib/productOperationFreeze";
import ProductCreationOptionsEditor from "./ProductCreationOptionsEditor";
import BundleEditor from "./BundleEditor";
import ProductCreationOptionsPreview from "./ProductCreationOptionsPreview";
import {
    PRODUCT_TYPE_LABELS, creationOptionsError, creationOptionsFromDraft,
    serializeCreationOptions,
} from "./productCreationOptions";

const EMPTY_FORM = {
    name: "",
    sku: "",
    price: "",
    description: "",
    product_type: "product",
    weight: "",
    options: [],
    image_url: "",
    fulfillment_type: FROZEN_FULFILLMENT_TYPE,
    inventory_policy: FROZEN_INVENTORY_POLICY,
    stockout_policy: "close_when_out_of_stock",
    low_stock_threshold: 3,
};

const STATUS_LABELS = {
    draft: "مسودة",
    approved: "معتمد",
    publishing: "جارٍ الإنشاء",
    publish_unknown: "بانتظار المصالحة",
    published: "تم الإنشاء والتحقق",
    published_unverified: "تم الإنشاء ويحتاج تحققًا",
};

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    const code = detail?.code;
    const messages = {
        product_sku_already_used: "رمز SKU مستخدم مسبقًا في ميزان أو في مسودة أخرى.",
        salla_product_write_scope_required: "اتصال سلة يحتاج صلاحية products.read_write. أعد ربط سلة بعد تفعيل الصلاحية.",
        sku_exists_in_salla: "رمز SKU موجود أصلًا في سلة؛ لم يتم إنشاء نسخة مكررة.",
        salla_product_creation_uncertain: "لم يصل رد نهائي من سلة. احتفظنا بالطلب للمصالحة الآمنة قبل أي إعادة.",
        salla_product_inventory_policy_uncertain: "أُنشئ المنتج في سلة، لكن لم نتأكد من تطبيق سياسة المخزون. احتفظنا بالطلب للمصالحة الآمنة.",
        invalid_stockout_policy: "اختر ماذا يحدث عند نفاد المخزون.",
        invalid_low_stock_threshold: "حد قرب النفاد يجب أن يكون صفرًا أو عددًا صحيحًا موجبًا.",
        unsupported_product_type: "اختر منتجًا جاهزًا أو خدمة حسب الطلب.",
        invalid_product_option_name: "أسماء الخيارات مطلوبة ويجب ألا تتكرر.",
        invalid_product_option_values: "كل قائمة تحتاج قيمتين مختلفتين على الأقل.",
        invalid_product_option_prices: "راجع الأسعار الإضافية لقيم الخيارات؛ يجب أن تكون مبالغ موجبة أو صفرًا.",
        text_option_cannot_have_values: "حقل النص لا يحتوي قيمًا ثابتة.",
        shipping_weight_required: "أدخل وزن الشحن بالكيلوجرام للخدمة المشحونة.",
        invalid_product_option_condition: "اختر حقل قائمة وقيمة صحيحة لشرط الظهور.",
        cyclic_product_option_condition: "شروط الظهور لا يمكن أن تعتمد على بعضها في حلقة.",
        salla_product_options_uncertain: "أُنشئ المنتج، لكن لم يكتمل التحقق من الخيارات وأسعارها وشروطها. سنكمل إعدادات المنتج الموجود قبل أي إعادة، دون إنشاء نسخة أخرى.",
    };
    if (Array.isArray(detail)) return "راجع بيانات المنتج والخيارات؛ بعض الحقول غير مكتملة أو تتجاوز الحد المسموح.";
    return messages[code] || detail?.message || "تعذر تنفيذ العملية";
}

export default function ProductCreationPanel({ onCreated }) {
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState(EMPTY_FORM);
    const [drafts, setDrafts] = useState([]);
    const [activeDraft, setActiveDraft] = useState(null);
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(false);
    const [editingDraftId, setEditingDraftId] = useState(null);
    const optionsError = creationOptionsError(form.options);
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [publishError, setPublishError] = useState("");
    const publishInFlight = useRef(false);

    async function refreshDraft(draftId) {
        const result = await getProductCreationDraft(draftId);
        setActiveDraft((current) => current?.id === draftId ? result.draft : current);
    }

    const loadDrafts = useCallback(async () => {
        try {
            const result = await listProductCreationDrafts({ limit: 30 });
            setDrafts(result.items || []);
        } catch (error) {
            toast.error(errorMessage(error));
        }
    }, []);

    useEffect(() => { loadDrafts(); }, [loadDrafts]);

    const canSave = useMemo(() => (
        form.name.trim().length >= 2
        && form.sku.trim()
        && form.price !== ""
        && PRODUCT_TYPE_LABELS[form.product_type]
        && (form.product_type !== "service" || Number(form.weight) > 0)
        && !optionsError
        && (
            !PRODUCT_OPERATION_CHOICES_ENABLED
            || (
                form.fulfillment_type
                && form.inventory_policy
                && (
                    form.inventory_policy !== "branch_stock_required"
                    || (
                        form.stockout_policy
                        && Number.isInteger(Number(form.low_stock_threshold))
                        && Number(form.low_stock_threshold) >= 0
                    )
                )
            )
        )
    ), [form, optionsError]);

    function update(key, value) {
        setForm((current) => ({ ...current, [key]: value }));
    }

    async function saveDraft(event) {
        event.preventDefault();
        if (!canSave) return;
        setBusy(true);
        try {
            const payload = applyFrozenProductOperationDefaults({
                name: form.name,
                sku: form.sku,
                price: Number(form.price),
                description: form.description || null,
                product_type: form.product_type,
                weight: form.weight === "" ? null : Number(form.weight),
                options: serializeCreationOptions(form.options),
                category_ids: form.category_ids || [],
                image_urls: [form.image_url.trim(), ...(form.image_urls || []).slice(1)].filter(Boolean),
                fulfillment_type: form.fulfillment_type,
                inventory_policy: form.inventory_policy,
                stockout_policy: form.stockout_policy,
                low_stock_threshold: Number(form.low_stock_threshold),
            });
            const result = editingDraftId
                ? await updateProductCreationDraft(editingDraftId, payload)
                : await createProductCreationDraft(payload);
            setActiveDraft(result.draft);
            setEditingDraftId(null);
            setPreview(null);
            toast.success("حُفظت مسودة المنتج داخل ميزان");
            await loadDrafts();
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy(false);
        }
    }

    async function reviewDraft() {
        if (!activeDraft) return;
        setBusy(true);
        try {
            const result = await previewProductCreationDraft(activeDraft.id);
            setPreview(result);
            toast.success("تم فحص بيانات الإنشاء بدون الكتابة في سلة");
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy(false);
        }
    }

    async function approveDraft() {
        if (!activeDraft || !preview) return;
        setBusy(true);
        try {
            const result = await approveProductCreationDraft(activeDraft.id);
            setActiveDraft(result.draft);
            toast.success("تم اعتماد المسودة، ولم يُنشأ المنتج في سلة بعد");
            await loadDrafts();
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setBusy(false);
        }
    }

    async function publishDraft() {
        if (!activeDraft || busy || publishInFlight.current) return;
        publishInFlight.current = true;
        setConfirmOpen(false);
        setPublishError("");
        setBusy(true);
        try {
            const result = await publishProductCreationDraft(activeDraft.id);
            setActiveDraft(result.draft);
            toast.success(
                result.verified
                    ? "تم إنشاء المنتج في سلة والتحقق منه"
                    : "تم إنشاء المنتج، وسيظهر تنبيه حتى يكتمل التحقق",
            );
            await loadDrafts();
            onCreated?.(result.product);
        } catch (error) {
            const message = errorMessage(error);
            setPublishError(message);
            toast.error(message);
            try {
                await refreshDraft(activeDraft.id);
            } catch {
                setPublishError(`${message} تعذر تحديث حالة الطلب؛ تحقق من الحالة قبل إعادة المحاولة.`);
            }
            await loadDrafts();
        } finally {
            publishInFlight.current = false;
            setBusy(false);
        }
    }


    function startNew() {
        setPublishError("");
        setForm(EMPTY_FORM);
        setEditingDraftId(null);
        setActiveDraft(null);
        setPreview(null);
        setOpen(true);
    }

    function editDraft() {
        setForm({
            ...EMPTY_FORM, ...activeDraft,
            description: activeDraft.description || "",
            weight: activeDraft.weight ?? "",
            image_url: activeDraft.image_urls?.[0] || "",
            options: creationOptionsFromDraft(activeDraft.options),
        });
        setEditingDraftId(activeDraft.id);
        setActiveDraft(null);
        setPreview(null);
    }
    const inventoryNotice = activeDraft?.inventory_policy === "branch_stock_required"
            ? activeDraft?.stockout_policy === "allow_preorder"
                ? "سيُنشأ بحالة نفد المخزون، ويحفظ ميزان سياسة الحجز المسبق حتى تُدخل مخزون فرع. "
                : "سيُنشأ بحالة نفد المخزون حتى تُدخل كمية في أحد الفروع."
            : "سيُنشأ دون تتبع مخزون للمنتج النهائي.";
    const preparationNotice = activeDraft?.fulfillment_type === "requires_preparation"
            ? "مساره الافتراضي يحتاج تجهيزًا."
            : "مساره الافتراضي مباشر، وأي خدمة مرتبطة به يمكن أن تدخله التجهيز.";

    return (
        <section className="rounded-3xl border border-indigo-200 bg-white shadow-sm" data-testid="product-creation-panel">
            <AlertDialog.Root open={confirmOpen} onOpenChange={setConfirmOpen}>
                <AlertDialog.Portal>
                    <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
                    <AlertDialog.Content dir="rtl" className="fixed left-1/2 top-1/2 z-50 w-[90vw] max-w-lg -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-white p-6 shadow-xl">
                        <AlertDialog.Title className="text-lg font-black">تأكيد إنشاء المنتج في سلة</AlertDialog.Title>
                        <AlertDialog.Description className="my-4 text-sm leading-7">
                            سيتم إرسال طلب المنتج «{activeDraft?.name}» ({activeDraft?.sku}) بسعر {activeDraft?.price} ر.س.
                            {" "}{PRODUCT_OPERATION_CHOICES_ENABLED ? `${inventoryNotice} ${preparationNotice}` : "سيكون مساره يحتاج تجهيزًا."}
                            {!activeDraft?.image_urls?.length && " سيبقى مخفيًا حتى تُضاف صورة."}
                        </AlertDialog.Description>
                        <div className="flex gap-3">
                            <AlertDialog.Cancel className="rounded-xl border px-4 py-2">إلغاء</AlertDialog.Cancel>
                            <AlertDialog.Action disabled={busy} onClick={publishDraft} className="rounded-xl bg-indigo-700 px-4 py-2 text-white">تأكيد ومتابعة</AlertDialog.Action>
                        </div>
                    </AlertDialog.Content>
                </AlertDialog.Portal>
            </AlertDialog.Root>
            <div className="flex flex-col gap-3 p-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <div className="flex items-center gap-2 font-black text-indigo-950">
                        <Package size={24} weight="duotone" />
                        إنشاء منتج جديد من ميزان إلى سلة
                    </div>
                    <p className="mt-1 text-sm leading-6 text-slate-600">
                        إنشاء فقط: مسودة ومعاينة واعتماد، ثم تأكيد صريح. لا نستورد منتجًا من سلة ولا نستخدم الاستيراد الجماعي.
                    </p>
                </div>
                <button type="button" disabled={busy} onClick={startNew} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-indigo-700 px-4 py-3 font-black text-white hover:bg-indigo-800">
                    <Plus weight="bold" /> منتج جديد
                </button>
            </div>

            {!open && drafts.length > 0 && (
                <div className="border-t bg-slate-50 p-4">
                    <div className="mb-2 text-xs font-black text-slate-500">آخر طلبات الإنشاء</div>
                    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                        {drafts.slice(0, 6).map((draft) => (
                            <button
                                key={draft.id}
                                type="button"
                                onClick={() => { setPublishError(""); setActiveDraft(draft); setPreview(null); setOpen(true); }}
                                className="rounded-xl border bg-white p-3 text-right hover:border-indigo-300"
                            >
                                <div className="truncate font-black">{draft.name}</div>
                                <div className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                                    <span>{draft.sku}</span>
                                    <span>{STATUS_LABELS[draft.status] || draft.status}</span>
                                </div>
                            </button>
                        ))}
                    </div>
                </div>
            )}

            {open && (
                <div className="border-t p-5">
                    <div className="mb-4 flex items-center justify-between">
                        <h3 className="font-black text-slate-900">
                            {activeDraft ? `طلب إنشاء: ${activeDraft.name}` : "بيانات المنتج الجديد"}
                        </h3>
                        <button type="button" disabled={busy} onClick={() => setOpen(false)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="إغلاق"><X /></button>
                    </div>

                    {!activeDraft && (
                        <form onSubmit={saveDraft} className="grid gap-4 lg:grid-cols-2">
                            <label className="space-y-1 text-sm font-bold">
                                <span>اسم المنتج</span>
                                <input value={form.name} onChange={(event) => update("name", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400" required />
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>SKU فريد</span>
                                <input value={form.sku} onChange={(event) => update("sku", event.target.value)} dir="ltr" className="h-11 w-full rounded-xl border px-3 text-left outline-none focus:border-indigo-400" placeholder="RING-100" required />
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>السعر</span>
                                <input type="number" min="0" step="0.01" value={form.price} onChange={(event) => update("price", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400" required />
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>نوع المنتج في سلة</span>
                                <select value={form.product_type} onChange={(event) => update("product_type", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                    {Object.entries(PRODUCT_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                                </select>
                                <span className="block text-xs font-normal text-slate-500">اختر النوع قبل الإنشاء؛ لا يمكن تغييره في سلة لاحقًا.</span>
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>وزن الشحن (كجم)</span>
                                <input type="number" min="0.001" max="1000000" step="0.001" value={form.weight} onChange={(event) => update("weight", event.target.value)} required={form.product_type === "service"} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400" />
                                <span className="block text-xs font-normal text-slate-500">أدخل الوزن الفعلي؛ مطلوب للخدمة حسب الطلب التي تُشحن للعميل.</span>
                            </label>
                            <div className="rounded-xl border border-violet-200 bg-violet-50 p-3 text-xs font-bold leading-6 text-violet-950">
                                خيارات التشغيل والمخزون مجمّدة مؤقتًا؛ كل منتج جديد يدخل مسار التجهيز تلقائيًا.
                            </div>
                            {PRODUCT_OPERATION_CHOICES_ENABLED && <>
                                <label className="space-y-1 text-sm font-bold">
                                <span>المسار الافتراضي للتنفيذ</span>
                                <select value={form.fulfillment_type} onChange={(event) => update("fulfillment_type", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                    <option value="" disabled>اختر نوع التنفيذ</option>
                                    <option value="requires_preparation">يحتاج تجهيز</option>
                                    <option value="instant">مباشر للشحن ما لم توجد خدمة</option>
                                </select>
                                <span className="block text-xs font-normal leading-5 text-slate-500">الخدمة المرتبطة بالمنتج كله تُدخل جميع طلباته التجهيز، والخدمة المرتبطة بخيار تُطبق عند اختياره.</span>
                                </label>
                                <label className="space-y-1 text-sm font-bold">
                                <span>سياسة مخزون المنتج النهائي</span>
                                <select value={form.inventory_policy} onChange={(event) => update("inventory_policy", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                    <option value="" disabled>اختر سياسة المخزون</option>
                                    <option value="branch_stock_required">يتتبع مخزون الفروع</option>
                                    <option value="finished_goods_inventory_not_tracked">لا يتتبع مخزون المنتج النهائي</option>
                                </select>
                                <span className="block text-xs font-normal leading-5 text-slate-500">تتبع المخزون مستقل عن التجهيز؛ يمكن حجز السلسال من المخزون ثم إرساله لخدمة كتابة الاسم.</span>
                                </label>
                                <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs leading-6 text-sky-950">
                                لا تُدخل الكمية هنا. بعد إنشاء المنتج، تُسجل كل كمية من صفحة المخزون داخل الفرع الذي توجد فيه فعليًا.
                                </div>
                                {form.inventory_policy === "branch_stock_required" && (
                                <>
                                    <label className="space-y-1 text-sm font-bold">
                                        <span>عند نفاد المخزون</span>
                                        <select value={form.stockout_policy} onChange={(event) => update("stockout_policy", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                            <option value="close_when_out_of_stock">إيقاف البيع والتنبيه</option>
                                            <option value="allow_preorder">السماح بالحجز المسبق</option>
                                        </select>
                                        <span className="block text-xs font-normal leading-5 text-slate-500">الحجز المسبق ينتظر المخزون ولا ينتقل إلى الشحن دون كمية.</span>
                                    </label>
                                    <label className="space-y-1 text-sm font-bold">
                                        <span>حد التنبيه بقرب النفاد</span>
                                        <input type="number" min="0" max="100000" step="1" value={form.low_stock_threshold} onChange={(event) => update("low_stock_threshold", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400" />
                                        <span className="block text-xs font-normal leading-5 text-slate-500">مثال: 3 يعني يبدأ التحذير عندما يصبح المتاح 3 قطع أو أقل.</span>
                                    </label>
                                </>
                                )}
                            </>}
                            <label className="space-y-1 text-sm font-bold lg:col-span-2">
                                <span>رابط صورة HTTPS (اختياري)</span>
                                <input value={form.image_url} onChange={(event) => update("image_url", event.target.value)} dir="ltr" className="h-11 w-full rounded-xl border px-3 text-left outline-none focus:border-indigo-400" placeholder="https://…" />
                            </label>
                            <label className="space-y-1 text-sm font-bold lg:col-span-2">
                                <span>الوصف</span>
                                <textarea value={form.description} onChange={(event) => update("description", event.target.value)} rows={4} className="w-full rounded-xl border p-3 outline-none focus:border-indigo-400" />
                            </label>
                            <div className="lg:col-span-2">
                                <ProductCreationOptionsEditor options={form.options} onChange={(options) => update("options", options)} disabled={busy} />
                                {optionsError && <p role="alert" className="mt-2 text-sm text-rose-700">{optionsError}</p>}
                            </div>
                            <div className="lg:col-span-2">
                                <button type="submit" disabled={!canSave || busy} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-5 py-3 font-black text-white disabled:opacity-50">
                                    <Plus /> {editingDraftId ? "حفظ تعديلات المسودة" : "حفظ المسودة في ميزان"}
                                </button>
                            </div>
                        </form>
                    )}

                    {activeDraft && (
                        <div className="space-y-4">
                            <div className="grid gap-3 rounded-2xl border bg-slate-50 p-4 sm:grid-cols-2 lg:grid-cols-6">
                                <div><div className="text-xs text-slate-500">نوع المنتج</div><div className="font-black">{PRODUCT_TYPE_LABELS[activeDraft.product_type || "product"]}</div></div>
                                <div><div className="text-xs text-slate-500">SKU</div><div className="font-black">{activeDraft.sku}</div></div>
                                <div><div className="text-xs text-slate-500">السعر</div><div className="font-black">{activeDraft.price} ر.س</div></div>
                                {!PRODUCT_OPERATION_CHOICES_ENABLED && <div><div className="text-xs text-slate-500">التشغيل</div><div className="font-black">يحتاج تجهيز (ثابت مؤقتًا)</div></div>}
                                {PRODUCT_OPERATION_CHOICES_ENABLED && <>
                                    <div><div className="text-xs text-slate-500">التشغيل</div><div className="font-black">{activeDraft.fulfillment_type === "instant" ? "مباشر افتراضيًا" : "يحتاج تجهيز"}</div></div>
                                    <div><div className="text-xs text-slate-500">المخزون</div><div className="font-black">{activeDraft.inventory_policy === "branch_stock_required" ? "يتتبع الفروع" : "لا يتتبع النهائي"}</div></div>
                                    <div><div className="text-xs text-slate-500">عند النفاد</div><div className="font-black">{activeDraft.inventory_policy !== "branch_stock_required" ? "غير مطبق" : activeDraft.stockout_policy === "allow_preorder" ? "حجز مسبق" : "إيقاف البيع"}</div></div>
                                </>}
                                <div><div className="text-xs text-slate-500">الحالة</div><div className="font-black">{STATUS_LABELS[activeDraft.status] || activeDraft.status}</div></div>
                            </div>

                            {!!activeDraft.options?.length && <ProductCreationOptionsPreview key={activeDraft.id} options={activeDraft.options} />}
                            {activeDraft.status === "published" && activeDraft.salla_product_id && <BundleEditor key={activeDraft.salla_product_id} productId={activeDraft.salla_product_id} salePrice={activeDraft.price} />}
                            {activeDraft.status === "published_unverified" && <p role="alert" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-900">أُنشئ المنتج، لكن لم يكتمل التحقق من نوعه وخياراته في سلة. لا تُنشئ نسخة أخرى.</p>}

                            {preview && (
                                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
                                    <div className="flex items-center gap-2 font-black"><CheckCircle /> المعاينة سليمة ولم تُجرِ أي كتابة خارجية</div>
                                    <div className="mt-2">
                                        {!PRODUCT_OPERATION_CHOICES_ENABLED && <>سينشأ المنتج قابلًا للبيع، ومساره <b>يحتاج تجهيزًا</b>.</>}
                                        {PRODUCT_OPERATION_CHOICES_ENABLED && <>{activeDraft.inventory_policy === "branch_stock_required"
                                            ? activeDraft.stockout_policy === "allow_preorder"
                                                ? <>سينشأ بحالة <b>نفد المخزون</b>، ويحفظ ميزان سياسة <b>الحجز المسبق</b> لمسار الطلب. عرض الحجز للعميل في سلة يحتاج خطوة نشر مستقلة ولم تنفذها هذه المعاينة.</>
                                                : <>سينشأ المنتج بحالة <b>نفد المخزون</b>، ولن يُباع حتى تُدخل مخزونًا في أحد الفروع.</>
                                            : <>سينشأ المنتج <b>قابلًا للبيع دون تتبع مخزون المنتج النهائي</b>.</>}
                                        {" "}
                                        {activeDraft.fulfillment_type === "requires_preparation"
                                            ? <>مساره الافتراضي <b>يحتاج تجهيزًا</b>.</>
                                            : <>مساره الافتراضي <b>مباشر للشحن</b>، لكن الخدمات المرتبطة به تظل قادرة على إدخاله التجهيز.</>}
                                        </>}
                                        {!activeDraft.image_urls?.length && <> سيبقى مخفيًا في سلة حتى تُضاف له صورة.</>}
                                    </div>
                                </div>
                            )}

                            {publishError && <p role="alert" className="rounded-xl bg-red-50 p-3 text-red-800">{publishError}</p>}
                            {activeDraft.status === "publishing" && <p role="status">جارٍ معالجة الطلب. حدّث الحالة لمعرفة النتيجة.</p>}
                            <button type="button" disabled={busy} onClick={() => refreshDraft(activeDraft.id).catch((error) => toast.error(errorMessage(error)))}>تحديث حالة الطلب</button>
                            <div className="flex flex-wrap gap-2">
                                {["draft", "approved"].includes(activeDraft.status) && <button type="button" disabled={busy} onClick={editDraft} className="rounded-xl border px-4 py-3 font-black disabled:opacity-50">تعديل المسودة</button>}
                                {activeDraft.status === "draft" && !preview && (
                                    <button type="button" disabled={busy} onClick={reviewDraft} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-3 font-black text-white disabled:opacity-50"><Eye /> معاينة وفحص</button>
                                )}
                                {activeDraft.status === "draft" && preview && (
                                    <button type="button" disabled={busy} onClick={approveDraft} className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-50"><CheckCircle /> اعتماد المسودة</button>
                                )}
                                {["approved", "publish_unknown"].includes(activeDraft.status) && (
                                    <button type="button" disabled={busy} onClick={() => { setPublishError(""); setConfirmOpen(true); }} className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-4 py-3 font-black text-white disabled:opacity-50"><RocketLaunch /> {busy ? "جارٍ تنفيذ الطلب…" : activeDraft.status === "publish_unknown" ? "التحقق من نتيجة الإنشاء" : "إنشاء المنتج في سلة"}</button>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </section>
    );
}
