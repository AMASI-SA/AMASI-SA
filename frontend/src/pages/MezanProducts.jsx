import { useEffect, useMemo, useState } from "react";
import {
    Calculator,
    CheckCircle,
    Cube,
    Gear,
    ImageSquare,
    Package,
    Receipt,
    SpinnerGap,
    Storefront,
    WarningCircle,
    Wrench,
} from "@phosphor-icons/react";

import { getMezanProductWorkspace } from "../services/mezanProductCatalog";
import {
    calculateConfigurationCost,
    matchReadyConfigurationStock,
} from "../services/mezanProductCosting";

function amount(money) {
    const value = money?.amount;
    return value === null || value === undefined || value === "" ? null : Number(value);
}

function formatMoney(value, empty = "غير محدد") {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return empty;
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function formatQuantity(value) {
    if (value === null || value === undefined || value === "") return "غير محدد";
    return Number(value).toLocaleString("en-US");
}

function kindCopy(kind) {
    if (kind === "stock_component") return "مكوّن مخزني";
    if (kind === "labor_service") return "تكلفة عمل";
    if (kind === "option_cost") return "فرق خيار";
    return kind || "غير محدد";
}

function optionTypeCopy(type) {
    if (type === "text") return "نص قصير";
    if (type === "image") return "رفع صورة";
    if (type === "radio") return "اختيار واحد";
    return type;
}

function optionInternalCostDelta(recipe, optionKey, valueKey) {
    const rule = recipe?.option_rules?.find((entry) => (
        entry.when?.option_key === optionKey && entry.when?.value_key === valueKey
    ));
    return (rule?.effects || []).reduce((sum, effect) => (
        effect.type === "fixed_cost_delta" ? sum + Number(effect.amount || 0) : sum
    ), 0);
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

function SectionTitle({ Icon, title, subtitle }) {
    return (
        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
                <Icon size={23} weight="fill" className="text-violet-700" />
                <h2 className="text-lg font-black text-slate-950">{title}</h2>
            </div>
            {subtitle && <div className="text-xs font-bold text-slate-500">{subtitle}</div>}
        </div>
    );
}

export default function MezanProducts() {
    const [workspace, setWorkspace] = useState(null);
    const [resources, setResources] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [selectedColor, setSelectedColor] = useState("silver");
    const [customerName, setCustomerName] = useState("اسم تجريبي");
    const [attachmentPresent, setAttachmentPresent] = useState(true);
    const [notice, setNotice] = useState("");

    useEffect(() => {
        let active = true;
        async function load() {
            setLoading(true);
            setError("");
            try {
                const result = await getMezanProductWorkspace();
                if (!active) return;
                setWorkspace(result);
                setResources(result.resources || []);
            } catch (loadError) {
                if (active) setError(loadError?.message || "تعذّر تحميل مساحة عمل المنتجات.");
            } finally {
                if (active) setLoading(false);
            }
        }
        load();
        return () => { active = false; };
    }, []);

    const product = workspace?.products?.[0] || null;
    const recipe = workspace?.recipes?.find((entry) => entry.product_id === product?.id) || null;
    const orderExample = workspace?.order_examples?.find((entry) => entry.product_id === product?.id) || null;

    const costing = useMemo(() => calculateConfigurationCost({
        recipe,
        resources,
        selections: { color: selectedColor },
    }), [recipe, resources, selectedColor]);

    const stockMatch = useMemo(() => matchReadyConfigurationStock({
        readyStock: workspace?.ready_stock || [],
        productId: product?.id,
        sku: product?.sku,
        color: selectedColor,
        customerName,
    }), [customerName, product?.id, product?.sku, selectedColor, workspace?.ready_stock]);

    const selectedColorOption = product?.options
        ?.find((option) => option.key === "color")
        ?.values?.find((value) => value.key === selectedColor);

    function updateResource(resourceId, field, rawValue) {
        setResources((current) => current.map((resource) => {
            if (resource.id !== resourceId) return resource;
            const parsed = rawValue === "" ? null : Math.max(0, Number(rawValue || 0));
            if (field === "unit_cost") return { ...resource, unit_cost: parsed };
            return {
                ...resource,
                inventory: { ...(resource.inventory || {}), on_hand: parsed },
            };
        }));
        setNotice("");
    }

    function applyPreview() {
        setNotice("تم تطبيق القيم داخل المعاينة فقط. لا توجد كتابة إلى سلة أو المخزون أو المحاسبة.");
    }

    if (loading) {
        return (
            <div className="flex min-h-[55vh] items-center justify-center" dir="rtl" data-testid="mezan-products-loading">
                <div className="flex items-center gap-3 font-bold text-violet-700"><SpinnerGap size={28} className="animate-spin" /> جاري تجهيز مساحة عمل المنتج…</div>
            </div>
        );
    }

    if (!product) {
        return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 font-bold text-rose-800" dir="rtl">{error || "لم يتم العثور على المنتج المرجعي."}</div>;
    }

    const readyStock = workspace?.ready_stock || [];
    const inventoryResources = resources.filter((entry) => entry.track_inventory);
    const laborResources = resources.filter((entry) => !entry.track_inventory);

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-products-page">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Package size={30} weight="fill" /></div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">منتجات Mezan OS</h1>
                                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-black text-emerald-800">مرجع سلة موثّق</span>
                            </div>
                            <p className="mt-1 text-sm text-slate-500">منتج سلة، مكوناته المشتركة، قواعد تكلفته، ومخزون التركيبات الجاهزة.</p>
                        </div>
                    </div>
                    <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm">
                        <div className="num font-black text-slate-950">SKU: {product.sku}</div>
                        <div className="mt-1 text-xs text-slate-500">المزامنة الحية تنتظر products.read</div>
                    </div>
                </div>
                <div className="border-t border-amber-200 bg-amber-50 px-4 py-4 sm:px-5" data-testid="manual-reference-banner">
                    <div className="flex items-start gap-3 text-amber-950">
                        <WarningCircle size={24} weight="fill" className="mt-0.5 shrink-0 text-amber-600" />
                        <div>
                            <div className="font-black">{workspace?.meta?.label}</div>
                            <div className="mt-1 text-sm leading-6 text-amber-800">هذا المنتج حقيقي ومطابق للصور المرسلة، لكن القيم ليست استجابة API حية بعد. صورة العميل واسمه الكامل غير محفوظين في الكود العام، ولا توجد أي كتابة خارج هذه الصفحة.</div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="سعر البيع في سلة" value={formatMoney(amount(product.price))} Icon={Storefront} />
                <MetricCard label="سعر التكلفة المرجعي" value="محجوب" Icon={Receipt} tone="amber" />
                <MetricCard label="المكونات المركزية" value={inventoryResources.length} Icon={Cube} tone="teal" />
                <MetricCard label="خدمات العمل" value={laborResources.length} Icon={Wrench} tone="slate" />
            </section>

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">{error}</div>}

            <section className="grid gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                    <SectionTitle Icon={Storefront} title="بيانات المنتج في سلة" subtitle="موثّقة من شاشة الإدارة" />
                    <div className="flex flex-col gap-4 sm:flex-row">
                        <div className="flex h-28 w-full shrink-0 items-center justify-center rounded-2xl border border-violet-200 bg-gradient-to-br from-violet-100 via-white to-teal-100 text-xl font-black text-violet-800 sm:w-28">AMS</div>
                        <div className="min-w-0 flex-1">
                            <h2 className="text-xl font-black text-slate-950">{product.name}</h2>
                            <p className="mt-2 text-sm leading-6 text-slate-600">{product.description}</p>
                            <div className="mt-3 flex flex-wrap gap-2">
                                {product.categories.map((category) => <span key={category.id} className="rounded-full bg-violet-100 px-3 py-1 text-xs font-bold text-violet-800">{category.name}</span>)}
                                <span className="rounded-full bg-teal-100 px-3 py-1 text-xs font-bold text-teal-800">يتطلب شحن</span>
                                <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold text-slate-700">كمية غير محدودة في سلة</span>
                            </div>
                        </div>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <DetailValue label="SKU" value={product.sku} mono />
                        <DetailValue label="سعر البيع" value={formatMoney(amount(product.price))} mono />
                        <DetailValue label="سعر التكلفة في سلة" value="موثّق ومحجوب في الكود العام" tone="amber" />
                        <DetailValue label="الوزن" value={`${product.weight} كجم`} mono />
                    </div>
                </div>

                <div className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="verified-order-example">
                    <SectionTitle Icon={Receipt} title="شكل المنتج داخل الطلب" subtitle="Snapshot موثّق ومقنّع" />
                    <div className="rounded-xl border border-teal-200 bg-teal-50 p-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <div className="font-black text-slate-950">{orderExample.product_name}</div>
                                <div className="num mt-1 text-xs font-bold text-teal-700">{orderExample.sku} · الكمية {orderExample.quantity}</div>
                            </div>
                            <div className="rounded-xl bg-white p-2 text-teal-700"><ImageSquare size={25} weight="fill" /></div>
                        </div>
                        <div className="mt-4 space-y-2">
                            {orderExample.selected_options.map((option) => (
                                <div key={option.key} className="flex items-center justify-between gap-3 rounded-lg bg-white px-3 py-2 text-sm">
                                    <span className="font-bold text-slate-500">{option.label}</span>
                                    <span className="font-black text-slate-950">
                                        {option.attachment_present ? "صورة مرفقة — غير محفوظة في Fixture" : (option.display_value || option.value)}
                                    </span>
                                </div>
                            ))}
                        </div>
                        <div className="mt-3 grid grid-cols-2 gap-2">
                            <DetailValue label="سعر الوحدة" value={formatMoney(amount(orderExample.unit_price))} mono tone="teal" />
                            <DetailValue label="الإجمالي" value={formatMoney(amount(orderExample.total))} mono tone="teal" />
                        </div>
                    </div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <SectionTitle Icon={Gear} title="خيارات المنتج في سلة" subtitle="كل الحقول إلزامية" />
                <div className="grid gap-3 lg:grid-cols-3">
                    {product.options.map((option) => (
                        <div key={option.id} className="rounded-xl border border-slate-200 p-4">
                            <div className="flex items-start justify-between gap-3">
                                <div>
                                    <div className="font-black text-slate-950">{option.name}</div>
                                    <div className="mt-1 text-xs text-slate-500">{optionTypeCopy(option.type)} · {option.placeholder}</div>
                                </div>
                                <span className="rounded bg-rose-100 px-2 py-1 text-[10px] font-black text-rose-700">مطلوب</span>
                            </div>
                            {option.values?.length > 0 ? (
                                <div className="mt-4 space-y-2">
                                    {option.values.map((value) => (
                                        <div key={value.id} className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-sm">
                                            <span className="font-black">{value.name}</span>
                                            <span className="text-xs font-bold text-slate-500">
                                                سعر سلة +0 · تكلفة داخلية +{optionInternalCostDelta(recipe, option.key, value.key)}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            ) : <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs text-slate-500">القيمة تأتي من العميل ولا تنشئ SKU جديدًا.</div>}
                        </div>
                    ))}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="cost-resource-catalog">
                <SectionTitle Icon={Cube} title="كتالوج مكونات التكلفة المركزي" subtitle="المكوّن يُنشأ مرة واحدة ثم تربطه الوصفات" />
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[760px] text-right text-sm">
                        <thead>
                            <tr className="border-b border-slate-200 text-xs text-slate-500">
                                <th className="px-3 py-3">المكوّن / الخدمة</th>
                                <th className="px-3 py-3">النوع</th>
                                <th className="px-3 py-3">تكلفة الوحدة</th>
                                <th className="px-3 py-3">المخزون المتاح</th>
                                <th className="px-3 py-3">الحالة</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {resources.map((resource) => (
                                <tr key={resource.id}>
                                    <td className="px-3 py-3"><div className="font-black text-slate-950">{resource.name}</div><div className="num mt-0.5 text-[11px] text-slate-400">{resource.code}</div></td>
                                    <td className="px-3 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${resource.track_inventory ? "bg-teal-100 text-teal-800" : "bg-violet-100 text-violet-800"}`}>{kindCopy(resource.kind)}</span></td>
                                    <td className="px-3 py-3">
                                        <input type="number" min="0" step="0.01" value={resource.unit_cost ?? ""} onChange={(event) => updateResource(resource.id, "unit_cost", event.target.value)} placeholder="أدخل التكلفة" className="num w-36 rounded-lg border border-slate-200 px-3 py-2 text-left outline-none focus:border-violet-400" />
                                    </td>
                                    <td className="px-3 py-3">
                                        {resource.track_inventory ? <input type="number" min="0" step="1" value={resource.inventory?.on_hand ?? ""} onChange={(event) => updateResource(resource.id, "on_hand", event.target.value)} placeholder="أدخل الكمية" className="num w-36 rounded-lg border border-slate-200 px-3 py-2 text-left outline-none focus:border-teal-400" /> : <span className="text-xs font-bold text-slate-400">لا يتتبع مخزونًا</span>}
                                    </td>
                                    <td className="px-3 py-3">
                                        {resource.unit_cost === null ? <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-800">تحتاج تكلفة</span> : <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800"><CheckCircle size={13} weight="fill" /> جاهزة</span>}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm font-bold text-blue-900">تكلفة سلة الإجمالية تبقى مرجعًا خاصًا ومحجوبًا في الكود العام؛ ولا نوزعها افتراضيًا على الكيس والعلبة والسلسال والعمل.</div>
            </section>

            <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="product-recipe">
                    <SectionTitle Icon={Wrench} title="وصفة المنتج BOM" subtitle={`الإصدار ${recipe.version} · مسودة`} />
                    <div className="space-y-2">
                        {costing.lines.map((line) => (
                            <div key={line.id} className="grid gap-2 rounded-xl border border-slate-200 p-3 sm:grid-cols-[minmax(0,1.4fr)_90px_130px] sm:items-center">
                                <div><div className="font-black text-slate-950">{line.name}</div><div className="mt-0.5 text-[11px] text-slate-500">{line.reason || kindCopy(line.kind)} · {line.source.startsWith("option:") ? "حسب اختيار العميل" : "أساسي"}</div></div>
                                <div className="num text-sm font-bold">× {line.quantity}</div>
                                <div className={`num text-sm font-black ${line.cost_known ? "text-emerald-700" : "text-amber-700"}`}>{line.cost_known ? formatMoney(line.total_cost) : "تكلفة ناقصة"}</div>
                            </div>
                        ))}
                    </div>
                    <div className="mt-4 rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950">
                        <div className="font-black">قاعدة اللون المحددة الآن: {selectedColorOption?.name}</div>
                        <div className="mt-1 leading-6">اختيار الفضي يربط «سلسال فضي» ويضيف فرق تكلفة داخلي `5 ر.س` مرة واحدة. هذه ليست زيادة على سعر سلة.</div>
                    </div>
                </div>

                <div className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="configuration-calculator">
                    <SectionTitle Icon={Calculator} title="حاسبة التركيبة" subtitle="تكلفة + مخزون جاهز" />
                    <div className="space-y-3">
                        <label className="block"><span className="text-xs font-black text-slate-600">الاسم المطلوب</span><input value={customerName} onChange={(event) => { setCustomerName(event.target.value); setNotice(""); }} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 outline-none focus:border-teal-400" data-testid="configuration-name" /></label>
                        <div><div className="text-xs font-black text-slate-600">اللون</div><div className="mt-1 grid grid-cols-2 gap-2">{product.options.find((option) => option.key === "color").values.map((value) => <button key={value.key} type="button" onClick={() => { setSelectedColor(value.key); setNotice(""); }} className={`rounded-xl border px-3 py-2.5 text-sm font-black ${selectedColor === value.key ? "border-teal-600 bg-teal-600 text-white" : "border-slate-200 bg-white text-slate-700"}`}>{value.name}</button>)}</div></div>
                        <label className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm font-bold"><input type="checkbox" checked={attachmentPresent} onChange={(event) => setAttachmentPresent(event.target.checked)} /> صورة العميل مرفقة</label>
                    </div>
                    <div className="mt-4 grid gap-2 sm:grid-cols-2">
                        <DetailValue label="التكلفة المعروفة حاليًا" value={formatMoney(costing.known_total)} mono tone="violet" />
                        <DetailValue label="بنود ناقصة التكلفة" value={costing.missing_lines.length} mono tone={costing.complete ? "teal" : "amber"} />
                        <DetailValue label="مطابقة مخزون جاهز" value={stockMatch.matched ? "مطابقة موجودة" : "لا توجد مطابقة"} tone={stockMatch.matched ? "teal" : "amber"} />
                        <DetailValue label="الكمية الجاهزة المتاحة" value={formatQuantity(stockMatch.quantity_available)} mono tone={stockMatch.quantity_available > 0 ? "teal" : "amber"} />
                    </div>
                    <div className="num mt-3 break-all rounded-lg bg-slate-950 p-3 text-left text-[11px] text-slate-200" dir="ltr">{stockMatch.configuration_key}</div>
                    {!attachmentPresent && <div className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm font-bold text-rose-800">الطلب يحتاج صورة؛ لا يمكن اعتباره مكتمل المواصفات.</div>}
                    {notice && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-sm font-bold text-emerald-800">{notice}</div>}
                    <button type="button" onClick={applyPreview} className="mt-4 w-full rounded-xl bg-teal-600 px-4 py-3 text-sm font-black text-white hover:bg-teal-700">تطبيق في المعاينة</button>
                    <div className="mt-2 text-center text-[11px] text-slate-400">الحفظ غير دائم ولا يخصم أي مخزون.</div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="ready-configuration-stock">
                <SectionTitle Icon={Package} title="مخزون المنتجات الجاهزة حسب التركيبة" subtitle="مستقل عن مخزون سلة ومخزون المكونات" />
                <div className="grid gap-3 md:grid-cols-2">
                    {readyStock.map((entry) => {
                        const color = entry.configuration.option_values.color === "silver" ? "فضي" : "ذهبي";
                        const name = entry.configuration.custom_values.customer_name.normalized;
                        const available = Math.max(0, Number(entry.quantity_on_hand || 0) - Number(entry.quantity_reserved || 0));
                        return (
                            <div key={entry.id} className={`rounded-xl border p-4 ${available > 0 ? "border-emerald-200 bg-emerald-50" : "border-rose-200 bg-rose-50"}`}>
                                <div className="flex items-center justify-between gap-3"><div><div className="font-black text-slate-950">{product.name}</div><div className="mt-1 text-sm font-bold text-slate-600">الاسم: {name} · اللون: {color}</div></div><div className={`num text-3xl font-black ${available > 0 ? "text-emerald-700" : "text-rose-700"}`}>{available}</div></div>
                                <div className="num mt-3 break-all text-[10px] text-slate-500" dir="ltr">{entry.configuration_key}</div>
                            </div>
                        );
                    })}
                </div>
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-600">الصورة مرفقة بالطلب، لكنها لا تدخل حاليًا في مفتاح المخزون الجاهز. هذا قرار معاينة قابل للتعديل قبل بناء التخزين الدائم.</div>
            </section>
        </div>
    );
}
