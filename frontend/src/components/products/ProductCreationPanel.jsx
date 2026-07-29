import { useCallback, useEffect, useMemo, useState } from "react";
import {
    CheckCircle, Eye, Package, Plus, RocketLaunch, X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    approveProductCreationDraft,
    createProductCreationDraft,
    listProductCreationDrafts,
    previewProductCreationDraft,
    publishProductCreationDraft,
} from "../../services/mezanProductsV2";

const EMPTY_FORM = {
    name: "",
    sku: "",
    price: "",
    quantity: "0",
    description: "",
    product_type: "product",
    image_url: "",
    fulfillment_type: "requires_preparation",
    warehouse_id: "",
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
        warehouse_required_for_instant_shipping: "اختر المخزن المسؤول عن المنتج الجاهز للشحن.",
        salla_product_write_scope_required: "اتصال سلة يحتاج صلاحية products.read_write. أعد ربط سلة بعد تفعيل الصلاحية.",
        sku_exists_in_salla: "رمز SKU موجود أصلًا في سلة؛ لم يتم إنشاء نسخة مكررة.",
        salla_product_creation_uncertain: "لم يصل رد نهائي من سلة. احتفظنا بالطلب للمصالحة الآمنة قبل أي إعادة.",
    };
    return messages[code] || detail?.message || "تعذر تنفيذ العملية";
}

export default function ProductCreationPanel({ onCreated }) {
    const [open, setOpen] = useState(false);
    const [form, setForm] = useState(EMPTY_FORM);
    const [drafts, setDrafts] = useState([]);
    const [warehouses, setWarehouses] = useState([]);
    const [activeDraft, setActiveDraft] = useState(null);
    const [preview, setPreview] = useState(null);
    const [busy, setBusy] = useState(false);

    const loadDrafts = useCallback(async () => {
        try {
            const result = await listProductCreationDrafts({ limit: 30 });
            setDrafts(result.items || []);
            setWarehouses(result.warehouses || []);
        } catch (error) {
            toast.error(errorMessage(error));
        }
    }, []);

    useEffect(() => { loadDrafts(); }, [loadDrafts]);

    const canSave = useMemo(() => (
        form.name.trim().length >= 2
        && form.sku.trim()
        && form.price !== ""
        && (
            form.fulfillment_type !== "instant"
            || Boolean(form.warehouse_id)
        )
    ), [form]);

    function update(key, value) {
        setForm((current) => ({ ...current, [key]: value }));
    }

    async function saveDraft(event) {
        event.preventDefault();
        if (!canSave) return;
        setBusy(true);
        try {
            const result = await createProductCreationDraft({
                name: form.name,
                sku: form.sku,
                price: Number(form.price),
                quantity: Number(form.quantity || 0),
                description: form.description || null,
                product_type: "product",
                category_ids: [],
                image_urls: form.image_url.trim() ? [form.image_url.trim()] : [],
                fulfillment_type: form.fulfillment_type,
                warehouse_id: form.warehouse_id || null,
            });
            setActiveDraft(result.draft);
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
        if (!activeDraft) return;
        const confirmed = window.confirm(
            `سيتم الآن إنشاء المنتج "${activeDraft.name}" في سلة كمنتج مخفي. هل تريد المتابعة؟`,
        );
        if (!confirmed) return;
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
            toast.error(errorMessage(error));
            await loadDrafts();
        } finally {
            setBusy(false);
        }
    }

    function startNew() {
        setForm(EMPTY_FORM);
        setActiveDraft(null);
        setPreview(null);
        setOpen(true);
    }

    return (
        <section className="rounded-3xl border border-indigo-200 bg-white shadow-sm" data-testid="product-creation-panel">
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
                <button type="button" onClick={startNew} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-indigo-700 px-4 py-3 font-black text-white hover:bg-indigo-800">
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
                                onClick={() => { setActiveDraft(draft); setPreview(null); setOpen(true); }}
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
                        <button type="button" onClick={() => setOpen(false)} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" aria-label="إغلاق"><X /></button>
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
                                <span>الكمية الأولية</span>
                                <input type="number" min="0" step="1" value={form.quantity} onChange={(event) => update("quantity", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400" />
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>نوع التجهيز في ميزان</span>
                                <select value={form.fulfillment_type} onChange={(event) => update("fulfillment_type", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                    <option value="requires_preparation">يحتاج تجهيز</option>
                                    <option value="instant">جاهز للشحن الفوري</option>
                                </select>
                            </label>
                            <label className="space-y-1 text-sm font-bold">
                                <span>المخزن {form.fulfillment_type === "instant" ? "(مطلوب)" : "(اختياري)"}</span>
                                <select value={form.warehouse_id} onChange={(event) => update("warehouse_id", event.target.value)} className="h-11 w-full rounded-xl border px-3 outline-none focus:border-indigo-400">
                                    <option value="">اختر المخزن</option>
                                    {warehouses.map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name} {warehouse.code ? `— ${warehouse.code}` : ""}</option>)}
                                </select>
                            </label>
                            <label className="space-y-1 text-sm font-bold lg:col-span-2">
                                <span>رابط صورة HTTPS (اختياري)</span>
                                <input value={form.image_url} onChange={(event) => update("image_url", event.target.value)} dir="ltr" className="h-11 w-full rounded-xl border px-3 text-left outline-none focus:border-indigo-400" placeholder="https://…" />
                            </label>
                            <label className="space-y-1 text-sm font-bold lg:col-span-2">
                                <span>الوصف</span>
                                <textarea value={form.description} onChange={(event) => update("description", event.target.value)} rows={4} className="w-full rounded-xl border p-3 outline-none focus:border-indigo-400" />
                            </label>
                            <div className="lg:col-span-2">
                                <button type="submit" disabled={!canSave || busy} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-5 py-3 font-black text-white disabled:opacity-50">
                                    <Plus /> حفظ المسودة في ميزان
                                </button>
                            </div>
                        </form>
                    )}

                    {activeDraft && (
                        <div className="space-y-4">
                            <div className="grid gap-3 rounded-2xl border bg-slate-50 p-4 sm:grid-cols-2 lg:grid-cols-4">
                                <div><div className="text-xs text-slate-500">SKU</div><div className="font-black">{activeDraft.sku}</div></div>
                                <div><div className="text-xs text-slate-500">السعر</div><div className="font-black">{activeDraft.price} ر.س</div></div>
                                <div><div className="text-xs text-slate-500">التشغيل</div><div className="font-black">{activeDraft.fulfillment_type === "instant" ? "شحن فوري" : "يحتاج تجهيز"}</div></div>
                                <div><div className="text-xs text-slate-500">الحالة</div><div className="font-black">{STATUS_LABELS[activeDraft.status] || activeDraft.status}</div></div>
                            </div>

                            {preview && (
                                <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
                                    <div className="flex items-center gap-2 font-black"><CheckCircle /> المعاينة سليمة ولم تُجرِ أي كتابة خارجية</div>
                                    <div className="mt-2">سينشأ المنتج في سلة بالحالة <b>مخفي</b>، ونوعه <b>منتج فعلي</b> لا يمكن تغييره بعد الإنشاء.</div>
                                </div>
                            )}

                            <div className="flex flex-wrap gap-2">
                                {activeDraft.status === "draft" && !preview && (
                                    <button type="button" disabled={busy} onClick={reviewDraft} className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-3 font-black text-white disabled:opacity-50"><Eye /> معاينة وفحص</button>
                                )}
                                {activeDraft.status === "draft" && preview && (
                                    <button type="button" disabled={busy} onClick={approveDraft} className="inline-flex items-center gap-2 rounded-xl bg-emerald-700 px-4 py-3 font-black text-white disabled:opacity-50"><CheckCircle /> اعتماد المسودة</button>
                                )}
                                {["approved", "publish_unknown"].includes(activeDraft.status) && (
                                    <button type="button" disabled={busy} onClick={publishDraft} className="inline-flex items-center gap-2 rounded-xl bg-indigo-700 px-4 py-3 font-black text-white disabled:opacity-50"><RocketLaunch /> إنشاء المنتج في سلة</button>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </section>
    );
}
