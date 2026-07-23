import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle,
    Cube,
    LinkSimple,
    MagnifyingGlass,
    Package,
    Plus,
    Receipt,
    SpinnerGap,
    WarningCircle,
    Wrench,
    X,
} from "@phosphor-icons/react";

import {
    addMezanComponentPreview,
    filterMezanComponents,
    getMezanComponentWorkspace,
    linkMezanComponentToProductPreview,
    saveMezanComponentPreviewCost,
    summarizeMezanComponents,
    unlinkMezanComponentFromProductPreview,
} from "../services/mezanComponentCatalog";

const FILTERS = [
    { key: "all", label: "الكل" },
    { key: "stock", label: "مخزنية" },
    { key: "service", label: "خدمات عمل" },
    { key: "missing_cost", label: "ناقصة التكلفة" },
];

function kindCopy(component) {
    return component?.track_inventory ? "مكوّن مخزني" : "خدمة عمل";
}

function unitCopy(unit) {
    if (unit === "piece") return "قطعة";
    if (unit === "job") return "عملية";
    return unit || "غير محددة";
}

function categoryCopy(category) {
    const labels = {
        packaging: "التغليف",
        chains: "السلاسل",
        labor: "العمل والتخصيص",
        finishing: "التشطيب",
        other: "أخرى",
    };
    return labels[category] || category || "غير مصنف";
}

function conditionCopy(usage) {
    if (!usage?.condition) return "دائم مع المنتج";
    const option = usage.condition.option_name || (usage.condition.option_key === "color"
        ? "اللون"
        : usage.condition.option_key);
    const value = usage.condition.value_name || (usage.condition.value_key === "silver"
        ? "فضي"
        : usage.condition.value_key === "gold"
            ? "ذهبي"
            : usage.condition.value_key);
    return `عند ${option}: ${value}`;
}

function attributeCopy(key) {
    const labels = {
        material: "الخامة",
        color: "اللون",
        size: "المقاس",
        finish: "التشطيب",
        service_type: "نوع الخدمة",
    };
    return labels[key] || key;
}

function optionDefaults(product) {
    const option = (product?.options || []).find((entry) => (entry.values || []).length > 0);
    return {
        option_key: option?.key || "",
        value_key: option?.values?.[0]?.key || "",
    };
}

function MetricCard({ label, value, Icon, tone = "violet" }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        teal: "border-teal-200 bg-teal-50 text-teal-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        slate: "border-slate-200 bg-white text-slate-950",
    };
    return (
        <div className={`rounded-2xl border p-4 shadow-sm ${tones[tone] || tones.slate}`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-bold opacity-65">{label}</div>
                    <div className="num mt-1 text-2xl font-black">{value}</div>
                </div>
                <div className="rounded-xl bg-white/80 p-2"><Icon size={22} weight="fill" /></div>
            </div>
        </div>
    );
}

function DetailValue({ label, value, mono = false, tone = "slate" }) {
    const tones = {
        slate: "border-slate-200 bg-slate-50",
        teal: "border-teal-200 bg-teal-50",
        amber: "border-amber-200 bg-amber-50",
        violet: "border-violet-200 bg-violet-50",
    };
    return (
        <div className={`rounded-xl border p-3 ${tones[tone] || tones.slate}`}>
            <div className="text-[11px] font-bold text-slate-500">{label}</div>
            <div className={`mt-1 break-words text-sm font-black text-slate-950 ${mono ? "num" : ""}`}>{value ?? "—"}</div>
        </div>
    );
}

function SectionTitle({ Icon, title, subtitle, action }) {
    return (
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
                <Icon size={23} weight="fill" className="text-violet-700" />
                <h2 className="text-lg font-black text-slate-950">{title}</h2>
            </div>
            <div className="flex items-center gap-2">
                {subtitle && <div className="text-xs font-bold text-slate-500">{subtitle}</div>}
                {action}
            </div>
        </div>
    );
}

function componentStatus(component) {
    return component.reference_cost?.amount == null
        ? { label: "تحتاج تكلفة", className: "bg-rose-100 text-rose-800" }
        : { label: "تكلفة مكتملة", className: "bg-emerald-100 text-emerald-800" };
}

export default function MezanComponents() {
    const [workspace, setWorkspace] = useState(null);
    const [components, setComponents] = useState([]);
    const [selectedId, setSelectedId] = useState("");
    const [query, setQuery] = useState("");
    const [filter, setFilter] = useState("all");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [costDraft, setCostDraft] = useState("");
    const [addOpen, setAddOpen] = useState(false);
    const [linkOpen, setLinkOpen] = useState(false);
    const [newComponent, setNewComponent] = useState({
        name: "",
        code: "",
        kind: "stock_component",
        unit: "piece",
    });
    const [linkDraft, setLinkDraft] = useState({
        product_id: "",
        quantity: 1,
        mode: "base",
        option_key: "",
        value_key: "",
    });

    useEffect(() => {
        let active = true;
        async function load() {
            setLoading(true);
            setError("");
            try {
                const result = await getMezanComponentWorkspace();
                if (!active) return;
                setWorkspace(result);
                setComponents(result.components || []);
                setSelectedId(result.components?.[0]?.id || "");
                setCostDraft(result.components?.[0]?.reference_cost?.amount ?? "");
                const firstProduct = result.products?.[0];
                setLinkDraft((current) => ({
                    ...current,
                    product_id: firstProduct?.id || "",
                    ...optionDefaults(firstProduct),
                }));
            } catch (loadError) {
                if (active) setError(loadError?.message || "تعذّر تحميل كتالوج المكونات.");
            } finally {
                if (active) setLoading(false);
            }
        }
        load();
        return () => { active = false; };
    }, []);

    const selected = components.find((component) => component.id === selectedId)
        || components[0]
        || null;
    const summary = useMemo(() => summarizeMezanComponents(components), [components]);
    const visibleComponents = useMemo(() => filterMezanComponents(components, {
        query,
        filter,
    }), [components, filter, query]);

    function selectComponent(componentId) {
        const component = components.find((entry) => entry.id === componentId);
        setSelectedId(componentId);
        setCostDraft(component?.reference_cost?.amount ?? "");
        setNotice("");
        setLinkOpen(false);
    }

    function applyComponentWorkspace(nextWorkspace, preferredId = selectedId) {
        const nextComponents = nextWorkspace?.components || [];
        const nextSelected = nextComponents.find((entry) => entry.id === preferredId)
            || nextComponents[0]
            || null;
        setWorkspace(nextWorkspace);
        setComponents(nextComponents);
        setSelectedId(nextSelected?.id || "");
        setCostDraft(nextSelected?.reference_cost?.amount ?? "");
    }

    function serviceErrorCopy(code) {
        const messages = {
            invalid_cost: "أدخل تكلفة صحيحة تساوي صفرًا أو أكثر.",
            invalid_component: "أدخل اسم المكوّن ورمزه.",
            component_code_exists: "رمز المكوّن مستخدم مسبقًا.",
            invalid_quantity: "أدخل كمية ربط صحيحة أكبر من صفر.",
            component_not_found: "المكوّن غير موجود في الكتالوج.",
            product_not_found: "المنتج غير موجود في الكتالوج.",
            option_value_not_found: "الخيار أو قيمته غير موجودين في المنتج المحدد.",
            link_exists: "هذا المكوّن مرتبط بالمنتج والشرط نفسه مسبقًا.",
            link_not_found: "تعذّر العثور على الربط المطلوب.",
        };
        return messages[code] || "تعذّر تنفيذ العملية داخل المعاينة.";
    }

    async function savePreviewCost() {
        if (!selected) return;
        const parsed = costDraft === "" ? null : Number(costDraft);
        if (parsed !== null && (!Number.isFinite(parsed) || parsed < 0)) {
            setNotice("أدخل تكلفة صحيحة تساوي صفرًا أو أكثر.");
            return;
        }
        const result = await saveMezanComponentPreviewCost(selected.id, parsed);
        if (!result.ok) {
            setNotice(serviceErrorCopy(result.code));
            return;
        }
        applyComponentWorkspace(result.component_workspace, selected.id);
        setNotice("تم حفظ التكلفة داخل المعاينة فقط؛ ستُلغى عند إعادة تحميل الصفحة.");
    }

    async function addPreviewComponent(event) {
        event.preventDefault();
        const name = newComponent.name.trim();
        const code = newComponent.code.trim().toUpperCase();
        if (!name || !code) return;
        const result = await addMezanComponentPreview({
            code,
            name,
            kind: newComponent.kind,
        });
        if (!result.ok) {
            setNotice(serviceErrorCopy(result.code));
            return;
        }
        applyComponentWorkspace(result.component_workspace, result.resource.id);
        setLinkOpen(false);
        setNewComponent({ name: "", code: "", kind: "stock_component", unit: "piece" });
        setAddOpen(false);
        setNotice("أُضيف المكوّن إلى المعاينة فقط دون إنشاء مخزون أو قيد محاسبي.");
    }

    async function addPreviewProductLink(event) {
        event.preventDefault();
        if (!selected) return;
        const product = workspace?.products?.find((entry) => entry.id === linkDraft.product_id);
        const quantity = Number(linkDraft.quantity);
        if (!product || !Number.isFinite(quantity) || quantity <= 0) {
            setNotice("اختر منتجًا وأدخل كمية ربط صحيحة.");
            return;
        }
        const option = (product.options || []).find((entry) => entry.key === linkDraft.option_key);
        const value = (option?.values || []).find((entry) => entry.key === linkDraft.value_key);
        if (linkDraft.mode === "option" && (!option || !value)) {
            setNotice("اختر حقلًا وقيمة موجودين في خيارات المنتج.");
            return;
        }
        const condition = linkDraft.mode === "option"
            ? { option_key: linkDraft.option_key, value_key: linkDraft.value_key }
            : null;
        const result = await linkMezanComponentToProductPreview({
            productId: product.id,
            resourceId: selected.id,
            quantity,
            condition,
        });
        if (!result.ok) {
            setNotice(serviceErrorCopy(result.code));
            return;
        }
        applyComponentWorkspace(result.component_workspace, selected.id);
        setLinkOpen(false);
        setNotice("تم تحديث وصفة المنتج داخل المعاينة؛ المنتج يعرف المكوّن الآن ولا يوجد تعديل في سلة.");
    }

    async function removePreviewProductLink(usage) {
        if (!selected) return;
        const result = await unlinkMezanComponentFromProductPreview({
            productId: usage.product_id,
            resourceId: selected.id,
            condition: usage.condition || null,
        });
        if (!result.ok) {
            setNotice(serviceErrorCopy(result.code));
            return;
        }
        applyComponentWorkspace(result.component_workspace, selected.id);
        setNotice("تم فك الربط من وصفة المنتج داخل المعاينة فقط.");
    }

    if (loading) {
        return (
            <div className="flex min-h-[55vh] items-center justify-center" dir="rtl" data-testid="mezan-components-loading">
                <div className="flex items-center gap-3 font-bold text-violet-700">
                    <SpinnerGap size={28} className="animate-spin" />
                    جاري تجهيز كتالوج المكونات…
                </div>
            </div>
        );
    }

    if (!selected) {
        return (
            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 font-bold text-rose-800" dir="rtl">
                {error || "لا توجد مكونات في الكتالوج التجريبي."}
            </div>
        );
    }

    const status = componentStatus(selected);
    const selectedProduct = workspace?.products?.find((entry) => entry.id === linkDraft.product_id);
    const productOptions = (selectedProduct?.options || []).filter((option) => (
        (option.values || []).length > 0
    ));
    const selectedOption = productOptions.find((option) => option.key === linkDraft.option_key)
        || productOptions[0]
        || null;

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-components-page">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Cube size={30} weight="fill" /></div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">مكونات Mezan OS</h1>
                                <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-800">نظام مستقل جديد</span>
                            </div>
                            <p className="mt-1 text-sm text-slate-500">كتالوج مركزي يربط المكوّن بأكثر من منتج ويحسب تكلفة كل وصفة دون تكرار.</p>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                        <div className="font-black text-slate-950">المصدر الحالي: بيانات تجريبية</div>
                        <div className="mt-1 text-xs text-slate-500">المنتجات الحية تنتظر Salla products.read</div>
                    </div>
                </div>
                <div className="border-t border-amber-200 bg-amber-50 px-4 py-4 sm:px-5" data-testid="components-preview-banner">
                    <div className="flex items-start gap-3 text-amber-950">
                        <WarningCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-amber-600" />
                        <div>
                            <div className="font-black">{workspace?.meta?.label}</div>
                            <div className="mt-1 text-sm leading-6 text-amber-800">هذه الصفحة تبني التعريف والربط والتكلفة فقط. لا توجد كميات قابلة للتعديل، ولا تُنشأ حركة مخزون أو فاتورة أو قيد محاسبي.</div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="إجمالي المكونات" value={summary.total} Icon={Cube} />
                <MetricCard label="مكونات مخزنية" value={summary.stock_components} Icon={Package} tone="teal" />
                <MetricCard label="خدمات العمل" value={summary.labor_services} Icon={Wrench} tone="slate" />
                <MetricCard label="تحتاج تكلفة" value={summary.missing_cost} Icon={WarningCircle} tone="amber" />
            </section>

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

            <section className="grid items-start gap-5 xl:grid-cols-[20rem_minmax(0,1fr)]">
                <aside className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="component-catalog">
                    <div className="border-b border-slate-200 p-4">
                        <div className="flex items-center justify-between gap-3">
                            <h2 className="font-black text-slate-950">كتالوج المكونات</h2>
                            <button type="button" onClick={() => setAddOpen(true)} className="rounded-lg bg-violet-600 p-2 text-white" title="إضافة مكوّن تجريبي">
                                <Plus size={18} weight="bold" />
                            </button>
                        </div>
                        <label className="mt-3 flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5">
                            <MagnifyingGlass size={18} className="text-slate-400" />
                            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو الرمز…" className="min-w-0 flex-1 bg-transparent text-sm outline-none" />
                        </label>
                        <div className="mt-3 grid grid-cols-2 gap-2">
                            {FILTERS.map((entry) => (
                                <button key={entry.key} type="button" onClick={() => setFilter(entry.key)} className={`rounded-lg border px-2 py-2 text-xs font-black ${filter === entry.key ? "border-violet-600 bg-violet-600 text-white" : "border-slate-200 bg-white text-slate-600"}`}>
                                    {entry.label}
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="max-h-[68vh] divide-y divide-slate-100 overflow-y-auto">
                        {visibleComponents.map((component) => {
                            const rowStatus = componentStatus(component);
                            return (
                                <button key={component.id} type="button" onClick={() => selectComponent(component.id)} className={`w-full p-4 text-right transition ${selected.id === component.id ? "bg-violet-50" : "hover:bg-slate-50"}`}>
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <div className="truncate font-black text-slate-950">{component.name}</div>
                                            <div className="num mt-1 truncate text-[11px] font-bold text-slate-500" dir="ltr">{component.code}</div>
                                        </div>
                                        <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-black ${rowStatus.className}`}>{rowStatus.label}</span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                        <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600">{kindCopy(component)}</span>
                                        <span className="rounded-full bg-teal-100 px-2 py-1 text-[10px] font-bold text-teal-700">{component.product_usages?.length || 0} رابط</span>
                                    </div>
                                </button>
                            );
                        })}
                        {visibleComponents.length === 0 && <div className="p-8 text-center text-sm font-bold text-slate-500">لا توجد مكونات مطابقة.</div>}
                    </div>
                </aside>

                <div className="space-y-5">
                    <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="component-detail">
                        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                            <div className="flex items-center gap-3">
                                <div className="flex h-16 w-16 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-100 via-white to-teal-100 text-lg font-black text-violet-700">{selected.code.slice(0, 3)}</div>
                                <div>
                                    <div className="flex flex-wrap items-center gap-2">
                                        <h2 className="text-xl font-black text-slate-950">{selected.name}</h2>
                                        <span className={`rounded-full px-2.5 py-1 text-[11px] font-black ${status.className}`}>{status.label}</span>
                                    </div>
                                    <div className="num mt-1 text-xs font-bold text-violet-700" dir="ltr">{selected.code}</div>
                                </div>
                            </div>
                            <span className={`w-fit rounded-full px-3 py-1.5 text-xs font-black ${selected.track_inventory ? "bg-teal-100 text-teal-800" : "bg-slate-100 text-slate-700"}`}>{kindCopy(selected)}</span>
                        </div>
                        <p className="mt-4 text-sm leading-6 text-slate-600">{selected.description}</p>
                        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                            <DetailValue label="العائلة" value={selected.family} />
                            <DetailValue label="التصنيف الداخلي" value={categoryCopy(selected.category)} />
                            <DetailValue label="الوحدة" value={unitCopy(selected.unit)} />
                            <DetailValue label="تتبّع المخزون" value={selected.track_inventory ? "نعم — عبر الحركات" : "لا — خدمة عمل"} tone={selected.track_inventory ? "teal" : "slate"} />
                        </div>
                        {Object.keys(selected.attributes || {}).length > 0 && (
                            <div className="mt-4 flex flex-wrap gap-2">
                                {Object.entries(selected.attributes).map(([key, value]) => (
                                    <span key={key} className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-bold text-violet-800">{attributeCopy(key)}: {value}</span>
                                ))}
                            </div>
                        )}
                    </section>

                    <div className="grid items-start gap-5 lg:grid-cols-2">
                        <section className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5">
                            <SectionTitle Icon={Receipt} title="التكلفة المرجعية" subtitle="معاينة داخل المتصفح" />
                            <label className="block">
                                <span className="text-xs font-black text-slate-600">تكلفة {selected.track_inventory ? "الوحدة" : "العملية"}</span>
                                <div className="mt-2 flex overflow-hidden rounded-xl border border-slate-200 bg-white focus-within:border-teal-500">
                                    <input type="number" min="0" step="0.01" value={costDraft} onChange={(event) => { setCostDraft(event.target.value); setNotice(""); }} placeholder="أدخل التكلفة" className="num min-w-0 flex-1 px-3 py-3 text-left outline-none" dir="ltr" />
                                    <span className="border-r border-slate-200 bg-slate-50 px-3 py-3 text-sm font-bold text-slate-500">ر.س</span>
                                </div>
                            </label>
                            <div className="mt-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs leading-6 text-slate-600">
                                {selected.track_inventory
                                    ? "التكلفة الحقيقية ستُستمد لاحقًا من فاتورة الشراء وطبقات التكلفة المعتمدة."
                                    : "تكلفة خدمة العمل تُعرّف هنا لكل عملية، دون إنشاء كمية مخزون."}
                            </div>
                            <button type="button" onClick={savePreviewCost} className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-teal-600 px-4 py-3 text-sm font-black text-white">
                                <CheckCircle size={19} weight="bold" />
                                حفظ تجريبي
                            </button>
                        </section>

                        <section className="rounded-2xl border border-amber-200 bg-white p-4 shadow-sm sm:p-5">
                            <SectionTitle Icon={Package} title="سياسة المخزون" />
                            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7 text-amber-950">
                                {selected.track_inventory
                                    ? "لا تُكتب الكمية داخل المكوّن. الرصيد سيُشتق من فاتورة الشراء، والمرتجع المعتمد، وحركات التصنيع، وكل رصيد يرتبط بموقع تخزين."
                                    : "هذا مكوّن خدمي؛ يدخل في تكلفة المنتج لكنه لا يملك كمية أو موقع تخزين."}
                            </div>
                            <div className="mt-3 grid grid-cols-2 gap-3">
                                <DetailValue label="الكمية الحالية" value="غير مفعّلة هنا" tone="amber" />
                                <DetailValue label="التعديل اليدوي" value="ممنوع" tone="amber" />
                            </div>
                        </section>
                    </div>

                    <section className="rounded-2xl border border-violet-200 bg-white p-4 shadow-sm sm:p-5" data-testid="component-product-links">
                        <SectionTitle
                            Icon={LinkSimple}
                            title="المنتجات المرتبطة بالمكوّن"
                            subtitle="ربط داخلي مثل تصنيفات المنتج — دون تعديل سلة"
                            action={(
                                <button type="button" onClick={() => setLinkOpen((current) => !current)} className="flex items-center gap-1 rounded-lg bg-violet-600 px-3 py-2 text-xs font-black text-white">
                                    {linkOpen ? <X size={16} /> : <Plus size={16} />}
                                    {linkOpen ? "إغلاق" : "ربط منتج"}
                                </button>
                            )}
                        />

                        {linkOpen && (
                            <form onSubmit={addPreviewProductLink} className="mb-4 grid gap-3 rounded-xl border border-violet-200 bg-violet-50 p-4 md:grid-cols-2">
                                <label className="block">
                                    <span className="text-xs font-black text-slate-600">المنتج</span>
                                    <select
                                        value={linkDraft.product_id}
                                        onChange={(event) => {
                                            const productId = event.target.value;
                                            const product = (workspace?.products || []).find((entry) => entry.id === productId);
                                            setLinkDraft((current) => ({
                                                ...current,
                                                product_id: productId,
                                                ...optionDefaults(product),
                                            }));
                                        }}
                                        className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm font-bold"
                                    >
                                        {(workspace?.products || []).map((product) => <option key={product.id} value={product.id}>{product.name} · {product.sku}</option>)}
                                    </select>
                                </label>
                                <label className="block">
                                    <span className="text-xs font-black text-slate-600">نوع الربط</span>
                                    <select value={linkDraft.mode} onChange={(event) => setLinkDraft((current) => ({ ...current, mode: event.target.value }))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm font-bold">
                                        <option value="base">مكوّن أساسي دائم</option>
                                        <option value="option">حسب خيار العميل</option>
                                    </select>
                                </label>
                                <label className="block">
                                    <span className="text-xs font-black text-slate-600">الكمية في المنتج</span>
                                    <input type="number" min="0.001" step="0.001" value={linkDraft.quantity} onChange={(event) => setLinkDraft((current) => ({ ...current, quantity: event.target.value }))} className="num mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-left" dir="ltr" />
                                </label>
                                {linkDraft.mode === "option" && productOptions.length > 0 && (
                                    <>
                                        <label className="block">
                                            <span className="text-xs font-black text-slate-600">حقل الخيار</span>
                                            <select
                                                value={selectedOption?.key || ""}
                                                onChange={(event) => {
                                                    const option = productOptions.find((entry) => entry.key === event.target.value);
                                                    setLinkDraft((current) => ({
                                                        ...current,
                                                        option_key: option?.key || "",
                                                        value_key: option?.values?.[0]?.key || "",
                                                    }));
                                                }}
                                                className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm font-bold"
                                            >
                                                {productOptions.map((option) => <option key={option.key} value={option.key}>{option.name}</option>)}
                                            </select>
                                        </label>
                                        <label className="block">
                                            <span className="text-xs font-black text-slate-600">قيمة الخيار</span>
                                            <select value={linkDraft.value_key} onChange={(event) => setLinkDraft((current) => ({ ...current, value_key: event.target.value }))} className="mt-1 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm font-bold">
                                                {(selectedOption?.values || []).map((value) => <option key={value.key} value={value.key}>{value.name}</option>)}
                                            </select>
                                        </label>
                                    </>
                                )}
                                {linkDraft.mode === "option" && productOptions.length === 0 && (
                                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs font-bold text-amber-800 md:col-span-2">
                                        هذا المنتج لا يحتوي خيارًا ذا قيم محددة يمكن ربط المكوّن به.
                                    </div>
                                )}
                                <button type="submit" disabled={linkDraft.mode === "option" && !selectedOption} className="rounded-lg bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:cursor-not-allowed disabled:opacity-40 md:col-span-2">إضافة الربط إلى المعاينة</button>
                            </form>
                        )}

                        <div className="space-y-3">
                            {(selected.product_usages || []).map((usage) => (
                                <div key={usage.id} className="grid gap-3 rounded-xl border border-slate-200 p-4 sm:grid-cols-[minmax(0,1fr)_100px_150px_80px] sm:items-center">
                                    <div>
                                        <div className="font-black text-slate-950">{usage.product_name}</div>
                                        <div className="num mt-1 text-xs font-bold text-violet-700" dir="ltr">{usage.product_sku}</div>
                                    </div>
                                    <div className="rounded-lg bg-slate-100 px-3 py-2 text-center text-xs font-black text-slate-700">× {Number(usage.quantity).toLocaleString("en-US")}</div>
                                    <div className={`rounded-lg px-3 py-2 text-center text-xs font-black ${usage.condition ? "bg-amber-100 text-amber-800" : "bg-teal-100 text-teal-800"}`}>{conditionCopy(usage)}</div>
                                    <button type="button" onClick={() => removePreviewProductLink(usage)} className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-xs font-black text-rose-700">
                                        فك الربط
                                    </button>
                                </div>
                            ))}
                            {(selected.product_usages || []).length === 0 && (
                                <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 p-6 text-center text-sm font-bold text-slate-500">
                                    هذا المكوّن غير مرتبط بمنتج حاليًا. استخدم «ربط منتج» لإضافته إلى الوصفة التجريبية.
                                </div>
                            )}
                        </div>
                        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-600">
                            عند وصول صلاحية سلة سنستبدل المنتج التجريبي بمنتجات سلة الحقيقية ونحفظ العلاقة في ميزان بواسطة معرّف المنتج وSKU. مجموع تكلفة المنتج = تكلفة المكونات الدائمة + المكونات المشروطة بالخيارات.
                        </div>
                    </section>

                    {notice && <div className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm font-bold text-blue-900">{notice}</div>}
                </div>
            </section>

            {addOpen && (
                <div className="fixed inset-0 z-[70] flex items-center justify-center bg-slate-950/40 p-4">
                    <form onSubmit={addPreviewComponent} className="w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-2xl">
                        <div className="flex items-center justify-between border-b bg-violet-50 px-5 py-4">
                            <div>
                                <h3 className="text-lg font-black text-slate-950">إضافة مكوّن تجريبي</h3>
                                <p className="mt-1 text-xs text-slate-500">لن يُحفظ خارج هذه الصفحة.</p>
                            </div>
                            <button type="button" onClick={() => setAddOpen(false)} className="rounded-lg p-2 hover:bg-white"><X size={20} /></button>
                        </div>
                        <div className="grid gap-4 p-5 sm:grid-cols-2">
                            <label className="block sm:col-span-2"><span className="text-xs font-black text-slate-600">اسم المكوّن</span><input value={newComponent.name} onChange={(event) => setNewComponent((current) => ({ ...current, name: event.target.value }))} required className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-violet-500" /></label>
                            <label className="block sm:col-span-2"><span className="text-xs font-black text-slate-600">الرمز الداخلي</span><input value={newComponent.code} onChange={(event) => setNewComponent((current) => ({ ...current, code: event.target.value.toUpperCase() }))} required className="num mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-left outline-none focus:border-violet-500" dir="ltr" /></label>
                            <label className="block"><span className="text-xs font-black text-slate-600">النوع</span><select value={newComponent.kind} onChange={(event) => setNewComponent((current) => ({ ...current, kind: event.target.value, unit: event.target.value === "stock_component" ? "piece" : "job" }))} className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5"><option value="stock_component">مكوّن مخزني</option><option value="labor_service">خدمة عمل</option></select></label>
                            <label className="block"><span className="text-xs font-black text-slate-600">الوحدة</span><input value={newComponent.kind === "stock_component" ? "قطعة" : "عملية"} readOnly className="mt-1 w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 font-bold text-slate-600" /></label>
                        </div>
                        <div className="flex justify-end gap-3 border-t bg-slate-50 p-5">
                            <button type="button" onClick={() => setAddOpen(false)} className="rounded-xl border border-slate-200 bg-white px-5 py-2.5 font-bold text-slate-700">إلغاء</button>
                            <button type="submit" className="flex items-center gap-2 rounded-xl bg-violet-700 px-5 py-2.5 font-black text-white"><Plus size={18} /> إضافة للمعاينة</button>
                        </div>
                    </form>
                </div>
            )}
        </div>
    );
}
