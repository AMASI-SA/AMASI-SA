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
    getOptionRuleSummary,
} from "../services/mezanProductCosting";
import {
    deriveInventoryBalances,
    findReadyStockForOrder,
    formatStorageLocation,
    getConfigurationBalance,
} from "../services/mezanProductInventory";

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

function movementTypeCopy(type) {
    if (type === "purchase_receipt") return "فاتورة شراء";
    if (type === "stock_transform") return "تحويل إنتاج";
    if (type === "approved_customer_return") return "مرتجع معتمد";
    return type || "حركة مخزنية";
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

const SOURCE_TONES = {
    components: "border-violet-200 bg-violet-50 text-violet-950",
    purchase_invoices: "border-blue-200 bg-blue-50 text-blue-950",
    production_orders: "border-teal-200 bg-teal-50 text-teal-950",
    returns: "border-amber-200 bg-amber-50 text-amber-950",
};

export default function MezanProducts() {
    const [workspace, setWorkspace] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let active = true;
        async function load() {
            setLoading(true);
            setError("");
            try {
                const result = await getMezanProductWorkspace();
                if (active) setWorkspace(result);
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
    const resources = workspace?.resources || [];
    const recipe = workspace?.recipes?.find((entry) => entry.product_id === product?.id) || null;
    const orderExample = workspace?.order_examples?.find((entry) => entry.product_id === product?.id) || null;
    const inventoryState = useMemo(() => ({
        configurations: workspace?.inventory_configurations || [],
        locations: workspace?.inventory_locations || [],
        movements: workspace?.inventory_movements || [],
        reservations: workspace?.inventory_reservations || [],
    }), [workspace]);
    const inventoryBalances = useMemo(() => deriveInventoryBalances(inventoryState), [inventoryState]);

    if (loading) {
        return (
            <div className="flex min-h-[55vh] items-center justify-center" dir="rtl" data-testid="mezan-products-loading">
                <div className="flex items-center gap-3 font-bold text-violet-700"><SpinnerGap size={28} className="animate-spin" /> جاري تجهيز صفحة المنتج…</div>
            </div>
        );
    }

    if (!product || !recipe || !orderExample) {
        return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 font-bold text-rose-800" dir="rtl">{error || "لم يتم العثور على المنتج المرجعي."}</div>;
    }

    const inventoryResources = resources.filter((entry) => entry.track_inventory);
    const laborResources = resources.filter((entry) => !entry.track_inventory);
    const inventoryLocations = inventoryState.locations;
    const locationById = new Map(inventoryLocations.map((entry) => [entry.id, entry]));
    const configurationById = new Map(inventoryState.configurations.map((entry) => [entry.id, entry]));
    const orderColor = orderExample.selected_options.find((option) => option.key === "color")?.value || "silver";
    const orderName = orderExample.selected_options.find((option) => option.key === "customer_name");
    const orderImage = orderExample.selected_options.find((option) => option.key === "customer_image");
    const exactOrderValuesAvailable = Boolean(orderName?.value && !orderName?.value_masked && orderImage?.fingerprint);
    const costing = calculateConfigurationCost({
        recipe,
        resources,
        selections: { color: orderColor },
    });
    const selectedColorOption = product.options
        ?.find((option) => option.key === "color")
        ?.values?.find((value) => value.key === orderColor);
    const stockMatch = exactOrderValuesAvailable ? findReadyStockForOrder(inventoryState, {
        productId: product.id,
        sku: product.sku,
        color: orderColor,
        customerName: orderName.value,
        attachmentFingerprint: orderImage.fingerprint,
    }) : { matched: false, quantity_available: 0, locations: [], configuration_key: "" };

    const resourceInventory = new Map();
    (recipe.option_rules || []).forEach((rule) => {
        (rule.effects || []).forEach((effect) => {
            if (effect.type !== "add_component") return;
            const configuration = inventoryState.configurations.find((entry) => (
                entry.product_id === product.id
                && entry.stage === "base_ready"
                && entry.option_values?.[rule.when.option_key] === rule.when.value_key
            ));
            if (!configuration) return;
            const rows = inventoryBalances.filter((row) => (
                row.configuration_id === configuration.id
                && row.condition === "sellable"
                && row.quantity_available > 0
            ));
            resourceInventory.set(effect.resource_id, {
                quantity: rows.reduce((sum, row) => sum + row.quantity_available, 0),
                locations: rows.map((row) => formatStorageLocation(locationById.get(row.location_id))),
            });
        });
    });

    const baseInventoryRows = inventoryBalances.filter((row) => (
        configurationById.get(row.configuration_id)?.stage === "base_ready"
        && row.condition === "sellable"
        && row.quantity_available > 0
    ));
    const readyInventoryRows = inventoryBalances.filter((row) => (
        configurationById.get(row.configuration_id)?.stage === "personalized_ready"
        && row.condition === "sellable"
        && row.quantity_available > 0
    ));

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-products-page">
            <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-4 sm:p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Package size={30} weight="fill" /></div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">منتجات Mezan OS</h1>
                                <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-800">صفحة عرض مستقلة</span>
                            </div>
                            <p className="mt-1 text-sm text-slate-500">نفس تصميم المنتج المعتمد، لعرض بيانات سلة والتكلفة والمخزون دون تنفيذ أي عملية تشغيلية.</p>
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
                            <div className="font-black">بيانات مرجعية للقراءة فقط — لا حفظ ولا ترحيل من صفحة المنتج</div>
                            <div className="mt-1 text-sm leading-6 text-amber-800">{workspace?.meta?.label}. القيم الحالية للعرض حتى تصل صلاحية سلة؛ وإدارة المكونات والمشتريات والإنتاج والمرتجعات ستكون في صفحات مستقلة.</div>
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
                <SectionTitle Icon={Gear} title="خيارات المنتج في سلة" subtitle="عرض القيم والربط فقط" />
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
                                    {option.values.map((value) => {
                                        const summary = getOptionRuleSummary(recipe, option.key, value.key);
                                        const baseConfiguration = inventoryState.configurations.find((entry) => (
                                            entry.product_id === product.id
                                            && entry.stage === "base_ready"
                                            && entry.option_values?.[option.key] === value.key
                                        ));
                                        const baseQuantity = baseConfiguration
                                            ? getConfigurationBalance(inventoryState, baseConfiguration.id, { condition: "sellable" })
                                            : 0;
                                        return (
                                            <div key={value.id} className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm">
                                                <div className="flex items-center justify-between gap-3">
                                                    <span className="font-black">{value.name}</span>
                                                    <span className="text-xs font-bold text-slate-500">سعر سلة +0 · تكلفة داخلية +{formatMoney(summary.fixed_cost_delta)}</span>
                                                </div>
                                                <div className="mt-2 flex flex-wrap gap-2 text-[11px] font-black">
                                                    <span className="rounded-full border border-violet-200 bg-white px-2.5 py-1 text-violet-700">التكلفة: من صفحة المكونات</span>
                                                    <span className="rounded-full border border-teal-200 bg-white px-2.5 py-1 text-teal-700">الرصيد العام: <span className="num">{formatQuantity(baseQuantity)}</span></span>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="mt-4 rounded-lg bg-slate-50 p-3 text-xs leading-6 text-slate-500">
                                    <div>القيمة تأتي من العميل كما ظهرت في الطلب ولا تنشئ SKU جديدًا.</div>
                                    <div className="font-bold text-slate-700">تكلفة التجهيز والمخزون المخصص تُداران من صفحتي المكونات وأوامر الإنتاج.</div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="product-domain-sources">
                <SectionTitle Icon={Gear} title="مصادر الإدارة الفعلية" subtitle="هذه الصفحة تجمع النتائج ولا تنشئها" />
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                    {(workspace.page_policy?.operational_sources || []).map((source) => (
                        <div key={source.id} className={`rounded-xl border p-4 ${SOURCE_TONES[source.id] || "border-slate-200 bg-slate-50 text-slate-950"}`}>
                            <div className="font-black">{source.title}</div>
                            <div className="mt-1 text-xs font-black opacity-75">{source.page} · صفحة مستقلة لاحقًا</div>
                            <div className="mt-3 text-xs leading-6 opacity-80">{source.description}</div>
                        </div>
                    ))}
                </div>
            </section>

            <section id="cost-resource-catalog" className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="cost-resource-catalog">
                <SectionTitle Icon={Cube} title="كتالوج مكونات التكلفة المركزي" subtitle="عرض فقط — التحرير سيكون في صفحة المكونات" />
                <div className="overflow-x-auto">
                    <table className="w-full min-w-[820px] text-right text-sm">
                        <thead>
                            <tr className="border-b border-slate-200 text-xs text-slate-500">
                                <th className="px-3 py-3">المكوّن / الخدمة</th>
                                <th className="px-3 py-3">النوع</th>
                                <th className="px-3 py-3">تكلفة الوحدة</th>
                                <th className="px-3 py-3">الرصيد المرتبط</th>
                                <th className="px-3 py-3">الحالة</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-100">
                            {resources.map((resource) => {
                                const linkedInventory = resourceInventory.get(resource.id);
                                return (
                                    <tr key={resource.id}>
                                        <td className="px-3 py-3"><div className="font-black text-slate-950">{resource.name}</div><div className="num mt-0.5 text-[11px] text-slate-400">{resource.code}</div></td>
                                        <td className="px-3 py-3"><span className={`rounded-full px-2.5 py-1 text-xs font-bold ${resource.track_inventory ? "bg-teal-100 text-teal-800" : "bg-violet-100 text-violet-800"}`}>{kindCopy(resource.kind)}</span></td>
                                        <td className="num px-3 py-3 font-black text-slate-700">{formatMoney(resource.unit_cost, "لم تُسجل")}</td>
                                        <td className="px-3 py-3">
                                            {linkedInventory ? (
                                                <div><div className="num font-black text-teal-800">{formatQuantity(linkedInventory.quantity)} قطعة</div><div className="mt-1 text-[10px] font-bold text-slate-500">{linkedInventory.locations.join(" · ")}</div></div>
                                            ) : resource.track_inventory ? (
                                                <span className="text-xs font-bold text-slate-500">سيأتي من صفحة المكونات</span>
                                            ) : (
                                                <span className="text-xs font-bold text-slate-400">لا يتتبع مخزونًا</span>
                                            )}
                                        </td>
                                        <td className="px-3 py-3">
                                            {resource.unit_cost === null ? <span className="rounded-full bg-amber-100 px-2.5 py-1 text-xs font-bold text-amber-800">تحتاج تكلفة</span> : <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2.5 py-1 text-xs font-bold text-emerald-800"><CheckCircle size={13} weight="fill" /> مكتملة</span>}
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
                <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm font-bold text-blue-900">صفحة المنتج تقرأ هذه النتائج فقط. لا يمكن منها تعديل سعر مكوّن أو رصيده، ولا تُوزع تكلفة سلة الخاصة تلقائيًا على المكونات.</div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="inventory-read-model">
                <SectionTitle Icon={Package} title="ملخص المخزون المرتبط بالمنتج" subtitle="عرض الأرصدة والمواقع من محرك المخزون" />

                <div className="grid gap-3 md:grid-cols-2">
                    {baseInventoryRows.map((entry) => {
                        const configuration = configurationById.get(entry.configuration_id);
                        return (
                            <div key={entry.bucket_key} className="rounded-xl border border-teal-200 bg-teal-50 p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div><div className="font-black text-slate-950">{configuration?.label}</div><div className="mt-2 text-xs font-bold text-teal-900">{formatStorageLocation(locationById.get(entry.location_id))}</div></div>
                                    <div className="num text-3xl font-black text-teal-700">{entry.quantity_available}</div>
                                </div>
                                <div className="num mt-3 text-[10px] text-slate-500" dir="ltr">{entry.lot_id}</div>
                            </div>
                        );
                    })}
                </div>

                {stockMatch.matched && (
                    <div className="mt-4 rounded-xl border border-emerald-300 bg-emerald-50 p-4 text-sm text-emerald-950">
                        <div className="font-black">تنبيه: يوجد مخزون جاهز مطابق تمامًا لمواصفات الطلب</div>
                        <div className="mt-2 font-bold">المتاح {stockMatch.quantity_available} · {stockMatch.locations.map((location) => location.location_label).join(" · ")}</div>
                    </div>
                )}
                {!exactOrderValuesAvailable && (
                    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm font-bold text-amber-900">الاسم والصورة مقنّعان في نموذج العرض؛ المطابقة الحقيقية ستظهر هنا تلقائيًا عند وصول بيانات الطلب الكاملة من النظام.</div>
                )}

                <div className="mt-5 overflow-x-auto">
                    <table className="w-full min-w-[760px] text-right text-sm" data-testid="inventory-movement-ledger">
                        <thead><tr className="border-b border-slate-200 text-xs text-slate-500"><th className="px-3 py-3">المستند</th><th className="px-3 py-3">مصدر الحركة</th><th className="px-3 py-3">التكوين والكمية</th><th className="px-3 py-3">الموقع</th><th className="px-3 py-3">الحالة</th></tr></thead>
                        <tbody className="divide-y divide-slate-100">
                            {inventoryState.movements.flatMap((movement) => (movement.lines || []).map((line) => {
                                const configuration = configurationById.get(line.configuration_id);
                                return (
                                    <tr key={`${movement.id}:${line.role}:${line.configuration_id}`}>
                                        <td className="num px-3 py-3 text-xs font-bold">{movement.reference?.id}</td>
                                        <td className="px-3 py-3 font-bold">{movementTypeCopy(movement.type)}</td>
                                        <td className="px-3 py-3"><div className="font-black text-slate-950">{configuration?.label || configuration?.configuration_key}</div><div className={`num mt-1 font-black ${line.delta_units > 0 ? "text-emerald-700" : "text-rose-700"}`}>{line.delta_units > 0 ? "+" : ""}{line.delta_units}</div></td>
                                        <td className="px-3 py-3 text-xs font-bold text-slate-600">{formatStorageLocation(locationById.get(line.location_id))}</td>
                                        <td className="px-3 py-3"><span className="rounded-full bg-slate-100 px-2.5 py-1 text-xs font-black text-slate-700">للعرض</span></td>
                                    </tr>
                                );
                            }))}
                        </tbody>
                    </table>
                </div>
                <div className="mt-3 text-[11px] font-bold text-slate-500">لا توجد حقول كمية أو أزرار ترحيل هنا. الحركات الفعلية ستنشأ من الصفحات التشغيلية ثم تظهر في هذا السجل للقراءة.</div>
            </section>

            <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
                <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="product-recipe">
                    <SectionTitle Icon={Wrench} title="وصفة المنتج BOM" subtitle={`الإصدار ${recipe.version} · عرض`} />
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
                        <div className="font-black">قاعدة اللون في الطلب المرجعي: {selectedColorOption?.name}</div>
                        <div className="mt-1 leading-6">اختيار الفضي يربط «سلسال فضي» ويضيف فرق تكلفة داخلي 5 ر.س مرة واحدة. هذه ليست زيادة على سعر سلة.</div>
                    </div>
                </div>

                <div className="rounded-2xl border border-teal-200 bg-white p-4 shadow-sm sm:p-5" data-testid="configuration-readonly">
                    <SectionTitle Icon={Calculator} title="ملخص التركيبة المرجعية" subtitle="عرض فقط" />
                    <div className="grid gap-2 sm:grid-cols-2">
                        <DetailValue label="الاسم في Snapshot" value={orderName?.value || "غير متوفر"} tone="slate" />
                        <DetailValue label="اللون" value={selectedColorOption?.name || orderColor} tone="teal" />
                        <DetailValue label="صورة العميل" value={orderImage?.attachment_present ? "مرفقة — غير محفوظة" : "غير مرفقة"} tone="amber" />
                        <DetailValue label="حالة المطابقة" value={exactOrderValuesAvailable ? (stockMatch.matched ? "مطابقة موجودة" : "لا توجد مطابقة") : "بانتظار القيم الكاملة"} tone={stockMatch.matched ? "teal" : "amber"} />
                        <DetailValue label="التكلفة المعروفة حاليًا" value={formatMoney(costing.known_total)} mono tone="violet" />
                        <DetailValue label="بنود ناقصة التكلفة" value={costing.missing_lines.length} mono tone={costing.complete ? "teal" : "amber"} />
                    </div>
                    <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs font-bold leading-6 text-slate-600">لا يوجد زر حفظ هنا. عند اكتمال الصفحات المستقلة ستنعكس تكاليف المكونات والأرصدة ومواقع التخزين تلقائيًا داخل هذا الملخص.</div>
                </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="ready-configuration-stock">
                <SectionTitle Icon={Package} title="مخزون المنتجات الجاهزة حسب المواصفات" subtitle="عرض من محرك المخزون والمرتجعات المعتمدة" />
                <div className="grid gap-3 md:grid-cols-2">
                    {readyInventoryRows.map((entry) => {
                        const configuration = configurationById.get(entry.configuration_id);
                        const color = configuration.option_values.color === "silver" ? "فضي" : "ذهبي";
                        const name = configuration.custom_values.customer_name.normalized;
                        return (
                            <div key={entry.bucket_key} className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                                <div className="flex items-center justify-between gap-3"><div><div className="font-black text-slate-950">{product.name}</div><div className="mt-1 text-sm font-bold text-slate-600">الاسم: {name} · اللون: {color}</div></div><div className="num text-3xl font-black text-emerald-700">{entry.quantity_available}</div></div>
                                <div className="mt-2 text-xs font-bold text-emerald-900">{formatStorageLocation(locationById.get(entry.location_id))}</div>
                                <div className="num mt-3 break-all text-[10px] text-slate-500" dir="ltr">{configuration.configuration_key}</div>
                            </div>
                        );
                    })}
                    {readyInventoryRows.length === 0 && <div className="md:col-span-2 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-5 text-center text-sm font-bold text-slate-500">لا يوجد رصيد مخصص ضمن بيانات العرض الحالية. سيظهر هنا بعد ترحيله من أمر إنتاج أو مرتجع معتمد في صفحته المختصة.</div>}
                </div>
                <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-6 text-slate-600">الصورة الأصلية لا تظهر في هذه الصفحة؛ تستخدم بصمتها داخل محرك المطابقة فقط. كل رصيد مخصص سيبقى مرتبطًا بموقع تخزين كامل: المخزن، العمود، الصف، والخانة.</div>
            </section>
        </div>
    );
}
