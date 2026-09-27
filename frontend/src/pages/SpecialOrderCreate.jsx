import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
    ArrowRight,
    Bank,
    Camera,
    CheckCircle,
    CurrencyCircleDollar,
    Gift,
    Info,
    MapPin,
    Package,
    Plus,
    Truck,
    UploadSimple,
    User,
} from "@phosphor-icons/react";
import { toast } from "sonner";

const ORDER_TYPES = [
    {
        key: "replacement",
        label: "بدل / تعويض",
        description: "يرتبط بطلب أصلي ويرث بيانات العميل والمنتجات مع السماح بتعديل خيارات البدل.",
        Icon: ArrowRight,
    },
    {
        key: "gift",
        label: "هدية",
        description: "طلب غير بيعي تُحتسب تكاليف المنتج والشحن حسب سبب الهدية.",
        Icon: Gift,
    },
    {
        key: "creator",
        label: "تصوير / صانع محتوى",
        description: "منتجات مجانية للتصوير أو المحتوى وتُحمّل على التسويق والإعلان.",
        Icon: Camera,
    },
    {
        key: "marketing",
        label: "استخدام تسويقي",
        description: "طلب تشغيلي غير بيعي لحملة أو نشاط تسويقي مع مركز تكلفة واضح.",
        Icon: Package,
    },
];

const inputClass =
    "w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-900 outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/20";
const labelClass = "mb-1.5 block text-xs font-bold text-slate-600";

function Section({ title, subtitle, children, testid }) {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid={testid}>
            <div className="mb-4">
                <h2 className="text-base font-extrabold text-slate-900" style={{ fontFamily: "Tajawal" }}>
                    {title}
                </h2>
                {subtitle && <p className="mt-1 text-xs text-slate-500">{subtitle}</p>}
            </div>
            {children}
        </section>
    );
}

export default function SpecialOrderCreate() {
    const navigate = useNavigate();
    const [searchParams] = useSearchParams();
    const initialType = ORDER_TYPES.some((item) => item.key === searchParams.get("type"))
        ? searchParams.get("type")
        : "replacement";

    const [orderType, setOrderType] = useState(initialType);
    const [originalOrder, setOriginalOrder] = useState(searchParams.get("order") || "");
    const [deliveryType, setDeliveryType] = useState("courier");
    const [customerShareMode, setCustomerShareMode] = useState("free");
    const [paymentMethod, setPaymentMethod] = useState("bank_transfer");

    const selectedType = useMemo(
        () => ORDER_TYPES.find((item) => item.key === orderType) || ORDER_TYPES[0],
        [orderType]
    );
    const isReplacement = orderType === "replacement";
    const isPartial = customerShareMode === "partial";

    const previewOnly = () => {
        toast.info("هذه النسخة للواجهة فقط. ربط إنشاء الطلب الفعلي والمحاسبة سيكون في المرحلة التالية بعد اعتماد التصميم.");
    };

    return (
        <div className="space-y-5 pb-28" dir="rtl" data-testid="special-order-create-page">
            <header className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <button
                        type="button"
                        onClick={() => navigate("/orders")}
                        className="mb-2 inline-flex items-center gap-1 text-xs font-bold text-slate-500 hover:text-slate-800"
                        data-testid="special-order-back"
                    >
                        <ArrowRight size={14} weight="bold" />
                        العودة إلى الطلبات
                    </button>
                    <div className="flex items-center gap-3">
                        <div className="rounded-2xl bg-brand/10 p-2.5 text-brand">
                            <Package size={28} weight="duotone" />
                        </div>
                        <div>
                            <h1 className="text-2xl font-extrabold text-slate-950 sm:text-3xl" style={{ fontFamily: "Tajawal" }}>
                                إنشاء طلب ميزان
                            </h1>
                            <p className="mt-1 text-sm text-slate-500">
                                طلب تشغيلي كامل يبدأ من «انتظار المراجعة» ويمر بنفس مراحل تجهيز الطلبات.
                            </p>
                        </div>
                    </div>
                </div>
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800">
                    واجهة أولية — بدون حفظ فعلي
                </div>
            </header>

            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5" data-testid="special-order-steps">
                {["نوع الطلب", "بيانات المستلم", "المنتجات", "التحصيل", "المراجعة"].map((step, index) => (
                    <div
                        key={step}
                        className={`rounded-xl border px-3 py-2 text-center text-xs font-bold ${
                            index === 0
                                ? "border-brand bg-brand/10 text-brand"
                                : "border-slate-200 bg-white text-slate-500"
                        }`}
                    >
                        <span className="num me-1">{index + 1}</span>
                        {step}
                    </div>
                ))}
            </div>

            <Section
                title="1) نوع الطلب"
                subtitle="النوع يحدد الأثر المالي والإحصائي، لكن جميع الأنواع تستخدم دورة التشغيل نفسها."
                testid="special-order-type-section"
            >
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {ORDER_TYPES.map(({ key, label, description, Icon }) => {
                        const active = orderType === key;
                        return (
                            <button
                                key={key}
                                type="button"
                                onClick={() => setOrderType(key)}
                                className={`rounded-2xl border-2 p-4 text-right transition ${
                                    active
                                        ? "border-brand bg-brand/5 shadow-sm"
                                        : "border-slate-200 bg-white hover:border-slate-300"
                                }`}
                                data-testid={`special-order-type-${key}`}
                                aria-pressed={active}
                            >
                                <div className="mb-3 flex items-center justify-between">
                                    <Icon size={24} weight="duotone" className={active ? "text-brand" : "text-slate-500"} />
                                    {active && <CheckCircle size={18} weight="fill" className="text-brand" />}
                                </div>
                                <div className="font-extrabold text-slate-900">{label}</div>
                                <div className="mt-1 text-xs leading-5 text-slate-500">{description}</div>
                            </button>
                        );
                    })}
                </div>
            </Section>

            {isReplacement ? (
                <Section
                    title="2) ربط الطلب الأصلي"
                    subtitle="أدخل رقم طلب سلة الأصلي ليجلب ميزان العميل والعنوان والمنتجات وخياراتها."
                    testid="special-order-original-section"
                >
                    <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                        <div>
                            <label className={labelClass}>رقم الطلب الأصلي</label>
                            <input
                                value={originalOrder}
                                onChange={(e) => setOriginalOrder(e.target.value)}
                                className={inputClass}
                                placeholder="مثال: 283943210"
                                inputMode="numeric"
                                data-testid="special-order-original-input"
                            />
                        </div>
                        <button
                            type="button"
                            onClick={previewOnly}
                            className="self-end rounded-xl bg-slate-900 px-5 py-2.5 text-sm font-extrabold text-white hover:bg-slate-800"
                            data-testid="special-order-load-original"
                        >
                            جلب الطلب
                        </button>
                    </div>
                    <div className="mt-3 flex items-start gap-2 rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs leading-5 text-sky-900">
                        <Info size={17} className="mt-0.5 shrink-0" />
                        <span>
                            بعد الجلب سيظهر الطلب الأصلي للقراءة فقط، ثم تختار القطع المعوضة وتعدل خيارات البدل بدون تعديل الطلب الأصلي.
                        </span>
                    </div>
                </Section>
            ) : (
                <Section
                    title="2) بيانات المستلم"
                    subtitle={`بيانات مستلم طلب «${selectedType.label}» تُحفظ داخل ميزان ولا تحتاج طلبًا أصليًا في سلة.`}
                    testid="special-order-recipient-section"
                >
                    <div className="grid gap-3 md:grid-cols-2">
                        <div>
                            <label className={labelClass}>اسم المستلم</label>
                            <div className="relative">
                                <User size={17} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                <input className={`${inputClass} pe-9`} placeholder="الاسم الكامل" />
                            </div>
                        </div>
                        <div>
                            <label className={labelClass}>رقم الجوال</label>
                            <input className={inputClass} placeholder="05xxxxxxxx" inputMode="tel" />
                        </div>
                    </div>
                </Section>
            )}

            <Section
                title={isReplacement ? "3) التوصيل وبيانات العميل" : "3) التوصيل والعنوان"}
                subtitle={
                    isReplacement
                        ? "مندوب المتجر يرث عنوان الطلب الأصلي. شركة الشحن تحتاج بوليصة جديدة خاصة بطلب البدل."
                        : "اختر طريقة التوصيل، ثم أدخل العنوان الكامل أو ارفع بوليصة شركة الشحن."
                }
                testid="special-order-delivery-section"
            >
                <div className="mb-4 grid gap-3 sm:grid-cols-2">
                    <button
                        type="button"
                        onClick={() => setDeliveryType("courier")}
                        className={`rounded-xl border-2 p-3 text-right ${
                            deliveryType === "courier" ? "border-brand bg-brand/5" : "border-slate-200"
                        }`}
                    >
                        <div className="flex items-center gap-2 font-extrabold text-slate-900">
                            <MapPin size={20} weight="duotone" className="text-brand" />
                            مندوب المتجر
                        </div>
                        <p className="mt-1 text-xs text-slate-500">
                            {isReplacement ? "يرث عنوان العميل من الطلب الأصلي ويمكن مراجعته." : "أدخل عنوان المستلم كاملًا."}
                        </p>
                    </button>
                    <button
                        type="button"
                        onClick={() => setDeliveryType("shipping_company")}
                        className={`rounded-xl border-2 p-3 text-right ${
                            deliveryType === "shipping_company" ? "border-brand bg-brand/5" : "border-slate-200"
                        }`}
                    >
                        <div className="flex items-center gap-2 font-extrabold text-slate-900">
                            <Truck size={20} weight="duotone" className="text-brand" />
                            شركة شحن
                        </div>
                        <p className="mt-1 text-xs text-slate-500">ارفع بوليصة الشحنة الجديدة ليحتفظ بها ميزان حتى التجميع والعنونة.</p>
                    </button>
                </div>

                {deliveryType === "courier" ? (
                    <div className="grid gap-3 md:grid-cols-2">
                        <div>
                            <label className={labelClass}>المدينة</label>
                            <input className={inputClass} placeholder={isReplacement ? "تُجلب من الطلب الأصلي" : "الرياض"} />
                        </div>
                        <div>
                            <label className={labelClass}>الحي</label>
                            <input className={inputClass} placeholder={isReplacement ? "تُجلب من الطلب الأصلي" : "اسم الحي"} />
                        </div>
                        <div className="md:col-span-2">
                            <label className={labelClass}>العنوان الكامل</label>
                            <textarea
                                rows={3}
                                className={inputClass}
                                placeholder={isReplacement ? "عنوان العميل من الطلب الأصلي مع إمكانية المراجعة" : "الشارع، رقم المبنى، معلم قريب، ملاحظات الوصول"}
                            />
                        </div>
                    </div>
                ) : (
                    <label className="flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-5 text-center hover:border-brand/50 hover:bg-brand/5">
                        <UploadSimple size={28} weight="duotone" className="mb-2 text-brand" />
                        <span className="text-sm font-extrabold text-slate-800">رفع بوليصة الشحن الجديدة</span>
                        <span className="mt-1 text-xs text-slate-500">PDF أو صورة — تحفظ داخل الطلب وتطبع في مرحلة التجميع والعنونة</span>
                        <input type="file" className="hidden" accept="application/pdf,image/*" />
                    </label>
                )}
            </Section>

            <Section
                title="4) المنتجات وخياراتها"
                subtitle={
                    isReplacement
                        ? "ستظهر منتجات الطلب الأصلي، اختر القطع المعوضة فقط ثم عدل خيارات البدل عند الحاجة."
                        : "اختر المنتجات من كتالوج سلة ثم حدد خيارات كل منتج كما لو كان طلبًا عاديًا."
                }
                testid="special-order-products-section"
            >
                <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 p-5">
                    <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                        <div>
                            <label className={labelClass}>{isReplacement ? "منتجات الطلب الأصلي" : "بحث في منتجات سلة"}</label>
                            <input
                                className={inputClass}
                                placeholder={isReplacement ? "تظهر بعد جلب الطلب الأصلي" : "اسم المنتج أو الرقم المخزني"}
                                disabled={isReplacement}
                            />
                        </div>
                        <button
                            type="button"
                            onClick={previewOnly}
                            className="self-end rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-extrabold text-slate-700 hover:bg-slate-100"
                        >
                            <Plus size={16} weight="bold" className="ms-1 inline" />
                            إضافة منتج
                        </button>
                    </div>
                    <div className="mt-4 rounded-xl bg-white p-3 text-xs leading-5 text-slate-500">
                        كل قطعة ستحفظ Snapshot مستقلًا للاسم، SKU، الصورة وخيارات العميل. في طلب البدل تبقى خيارات الطلب الأصلي محفوظة للتدقيق وتطبع خيارات البدل الجديدة فقط.
                    </div>
                </div>
            </Section>

            <Section
                title="5) تحمل التكلفة والتحصيل"
                subtitle="حدد هل المتجر يتحمل الطلب كاملًا أم للعميل مساهمة جزئية. هذا لا يحول الطلب إلى مبيعة تسويقية جديدة."
                testid="special-order-payment-section"
            >
                <div className="grid gap-3 sm:grid-cols-2">
                    <button
                        type="button"
                        onClick={() => setCustomerShareMode("free")}
                        className={`rounded-xl border-2 p-4 text-right ${
                            customerShareMode === "free" ? "border-brand bg-brand/5" : "border-slate-200"
                        }`}
                    >
                        <div className="flex items-center gap-2 font-extrabold text-slate-900">
                            <Gift size={20} weight="duotone" className="text-brand" />
                            مجاني 100% — على المتجر
                        </div>
                        <p className="mt-1 text-xs text-slate-500">المنتجات والشحن والخدمات تُحتسب تكلفة على المتجر حسب نوع الطلب.</p>
                    </button>
                    <button
                        type="button"
                        onClick={() => setCustomerShareMode("partial")}
                        className={`rounded-xl border-2 p-4 text-right ${
                            customerShareMode === "partial" ? "border-brand bg-brand/5" : "border-slate-200"
                        }`}
                    >
                        <div className="flex items-center gap-2 font-extrabold text-slate-900">
                            <CurrencyCircleDollar size={20} weight="duotone" className="text-brand" />
                            مساهمة جزئية من العميل
                        </div>
                        <p className="mt-1 text-xs text-slate-500">يوزع مبلغ العميل على المنتجات والشحن والخدمات مع بقاء الفرق على المتجر.</p>
                    </button>
                </div>

                {isPartial && (
                    <div className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4">
                        <div className="grid gap-3 md:grid-cols-3">
                            <div>
                                <label className={labelClass}>المبلغ على العميل</label>
                                <input className={inputClass} placeholder="0.00" inputMode="decimal" />
                            </div>
                            <div>
                                <label className={labelClass}>طريقة الدفع</label>
                                <select value={paymentMethod} onChange={(e) => setPaymentMethod(e.target.value)} className={inputClass}>
                                    <option value="bank_transfer">تحويل بنكي</option>
                                    <option value="cod">عند الاستلام</option>
                                </select>
                            </div>
                            {paymentMethod === "bank_transfer" && (
                                <div>
                                    <label className={labelClass}>البنك المستلم</label>
                                    <div className="relative">
                                        <Bank size={17} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                                        <select className={`${inputClass} pe-9`} defaultValue="">
                                            <option value="" disabled>اختر البنك</option>
                                        </select>
                                    </div>
                                </div>
                            )}
                        </div>
                        {paymentMethod === "bank_transfer" && (
                            <label className="mt-3 flex cursor-pointer items-center justify-between rounded-xl border border-dashed border-slate-300 bg-white p-3 hover:border-brand/50">
                                <div>
                                    <div className="text-sm font-extrabold text-slate-800">إيصال التحويل البنكي</div>
                                    <div className="mt-0.5 text-xs text-slate-500">يُعامل لاحقًا بنفس دورة إيصالات طلبات التحويل البنكي العادية.</div>
                                </div>
                                <UploadSimple size={22} className="text-brand" />
                                <input type="file" className="hidden" accept="application/pdf,image/*" />
                            </label>
                        )}
                    </div>
                )}

                <div className="mt-4 overflow-hidden rounded-2xl border border-slate-200">
                    <div className="bg-slate-50 px-4 py-3 text-sm font-extrabold text-slate-800">معاينة توزيع التكلفة</div>
                    <div className="grid grid-cols-4 gap-2 border-t border-slate-200 px-4 py-2 text-[11px] font-bold text-slate-500">
                        <div>البند</div>
                        <div className="text-center">التكلفة</div>
                        <div className="text-center">على العميل</div>
                        <div className="text-center">على المتجر</div>
                    </div>
                    {["المنتجات", "الشحن", "الخدمات والمكونات"].map((row) => (
                        <div key={row} className="grid grid-cols-4 gap-2 border-t border-slate-100 px-4 py-3 text-xs text-slate-700">
                            <div className="font-bold">{row}</div>
                            <div className="num text-center">0.00</div>
                            <div className="num text-center">0.00</div>
                            <div className="num text-center">0.00</div>
                        </div>
                    ))}
                </div>
            </Section>

            <div className="fixed bottom-0 left-0 right-0 z-20 border-t border-slate-200 bg-white/95 p-3 shadow-[0_-8px_25px_rgba(15,23,42,0.08)] backdrop-blur">
                <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-2">
                    <div>
                        <div className="text-sm font-extrabold text-slate-900">
                            عند الحفظ النهائي يبدأ الطلب من: <span className="text-brand">انتظار المراجعة</span>
                        </div>
                        <div className="mt-0.5 text-[11px] text-slate-500">
                            ثم يستخدم نفس مراحل التجهيز والاستلام والخدمات والتجميع والعنونة والتوصيل.
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => navigate("/orders")}
                            className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-bold text-slate-700 hover:bg-slate-50"
                        >
                            إلغاء
                        </button>
                        <button
                            type="button"
                            onClick={previewOnly}
                            className="rounded-xl bg-brand px-5 py-2.5 text-sm font-extrabold text-white shadow-sm hover:bg-brand-hover"
                            data-testid="special-order-create-preview"
                        >
                            إنشاء في انتظار المراجعة
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}
