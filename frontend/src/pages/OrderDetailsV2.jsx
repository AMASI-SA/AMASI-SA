import { Link, useParams } from "react-router-dom";
import {
    ArrowRight,
    Package,
    User,
    CreditCard,
    Truck,
    ClockCounterClockwise,
    Calculator,
    Printer,
    Factory,
    UsersThree,
    SpinnerGap,
    WarningCircle,
} from "@phosphor-icons/react";
import { useOrder } from "../hooks/useOrders";
import { useOrderItems } from "../hooks/useOrderItems";

function formatMoney(value) {
    return `${Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function formatOrderDate(value) {
    if (!value) return "—";

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return String(value);
    }

    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        hour12: true,
    }).format(date);
}

function displayValue(value) {
    if (value === null || value === undefined || value === "") {
        return "—";
    }

    if (typeof value === "object") {
        try {
            return JSON.stringify(value);
        } catch {
            return String(value);
        }
    }

    return String(value);
}

function collectItemSelections(item) {
    const rows = [];
    const seen = new Set();

    const push = (label, value) => {
        const normalizedLabel = String(label || "").trim();

        if (
            !normalizedLabel ||
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return;
        }

        const key = `${normalizedLabel}:${displayValue(value)}`;

        if (seen.has(key)) return;

        seen.add(key);
        rows.push({
            label: normalizedLabel,
            value: displayValue(value),
        });
    };

    push("اللون", item.color);
    push("المقاس", item.size);
    push("الخامة", item.material);

    for (const option of item.options || []) {
        push(
            option?.name || option?.label,
            option?.value
        );
    }

    for (const field of item.custom_fields || []) {
        push(
            field?.name || field?.label,
            field?.value
        );
    }

    return rows;
}

function InfoCard({ icon: Icon, title, children, testid }) {
    return (
        <section
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
            data-testid={testid}
        >
            <div className="mb-4 flex items-center gap-2">
                <div className="rounded-lg bg-violet-100 p-2 text-violet-700">
                    <Icon size={20} weight="fill" />
                </div>
                <h2 className="font-extrabold text-slate-950">{title}</h2>
            </div>
            {children}
        </section>
    );
}

function Field({ label, value }) {
    return (
        <div>
            <div className="text-xs font-bold text-slate-400">{label}</div>
            <div className="mt-1 break-words text-sm font-bold text-slate-800">
                {value || "—"}
            </div>
        </div>
    );
}

export default function OrderDetailsV2() {
    const { orderNumber } = useParams();
    const { order, loading, error } = useOrder(orderNumber);
    const {
        items,
        loading: itemsLoading,
        error: itemsError,
        reload: reloadItems,
    } = useOrderItems(orderNumber);

    if (loading) {
        return (
            <div className="flex min-h-[60vh] items-center justify-center">
                <SpinnerGap size={34} className="animate-spin text-violet-600" />
            </div>
        );
    }

    if (error || !order) {
        return (
            <div className="space-y-4" dir="rtl">
                <Link
                    to="/orders-v2"
                    className="inline-flex items-center gap-2 font-bold text-violet-700"
                >
                    <ArrowRight size={18} />
                    العودة إلى الطلبات
                </Link>

                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800">
                    <div className="flex items-center gap-2 font-extrabold">
                        <WarningCircle size={24} weight="fill" />
                        تعذّر فتح الطلب
                    </div>
                    <p className="mt-2 text-sm">{error}</p>
                </div>
            </div>
        );
    }

    const customer = order.customer || {};
    const payment = order.payment || {};
    const shipping = order.shipping || {};
    const total = order.totals?.total || 0;
    const createdAt = order.created_at;
    const status =
        order.status_native ||
        order.status ||
        "غير محدد";
    const paymentMethod =
        payment.method_native ||
        payment.method ||
        "غير محدد";

    return (
        <div
            className="space-y-5"
            dir="rtl"
            data-testid="order-details-v2-page"
        >
            <div className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:flex-row lg:items-center lg:justify-between">
                <div>
                    <Link
                        to="/orders-v2"
                        className="mb-3 inline-flex items-center gap-2 text-sm font-bold text-violet-700"
                    >
                        <ArrowRight size={17} />
                        العودة إلى الطلبات الجديدة
                    </Link>

                    <h1 className="num text-2xl font-extrabold text-slate-950">
                        الطلب #{orderNumber}
                    </h1>

                    <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                        <span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 font-bold text-sky-800">
                            {status}
                        </span>
                        <span className="text-slate-500">
                            تاريخ الإنشاء: {formatOrderDate(createdAt)}
                        </span>
                    </div>
                </div>

                <div className="num text-2xl font-extrabold text-slate-950">
                    {formatMoney(total)}
                </div>
            </div>

            <div className="grid gap-5 xl:grid-cols-3">
                <div className="space-y-5 xl:col-span-2">
                    <InfoCard
                        icon={Package}
                        title={`عناصر الطلب (${items.length.toLocaleString("en-US")})`}
                        testid="order-v2-items"
                    >
                        {itemsLoading ? (
                            <div
                                className="flex min-h-40 items-center justify-center"
                                data-testid="order-v2-items-loading"
                            >
                                <SpinnerGap
                                    size={28}
                                    className="animate-spin text-violet-600"
                                />
                            </div>
                        ) : itemsError ? (
                            <div
                                className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"
                                data-testid="order-v2-items-error"
                            >
                                <div className="flex items-center gap-2 font-extrabold">
                                    <WarningCircle
                                        size={21}
                                        weight="fill"
                                    />
                                    تعذّر تحميل عناصر الطلب
                                </div>

                                <p className="mt-2 text-sm">
                                    {itemsError}
                                </p>

                                <button
                                    type="button"
                                    onClick={reloadItems}
                                    className="mt-3 rounded-lg bg-rose-700 px-3 py-2 text-xs font-bold text-white"
                                >
                                    إعادة المحاولة
                                </button>
                            </div>
                        ) : items.length === 0 ? (
                            <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
                                لا توجد عناصر مرتبطة بهذا الطلب.
                            </div>
                        ) : (
                            <div className="space-y-3">
                                {items.map((item, index) => {
                                    const selections =
                                        collectItemSelections(item);

                                    return (
                                        <article
                                            key={item.order_item_id}
                                            className="rounded-xl border border-slate-200 bg-slate-50/60 p-3 sm:p-4"
                                            data-testid={`order-v2-item-${index}`}
                                        >
                                            <div className="flex items-start gap-3">
                                                <div className="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white sm:h-20 sm:w-20">
                                                    {item.image_url ? (
                                                        <img
                                                            src={item.image_url}
                                                            alt={
                                                                item.name ||
                                                                "صورة المنتج"
                                                            }
                                                            className="h-full w-full object-cover"
                                                        />
                                                    ) : (
                                                        <Package
                                                            size={27}
                                                            className="text-slate-300"
                                                        />
                                                    )}
                                                </div>

                                                <div className="min-w-0 flex-1">
                                                    <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                                                        <div>
                                                            <h3 className="font-extrabold text-slate-950">
                                                                {item.name ||
                                                                    "منتج بدون اسم"}
                                                            </h3>

                                                            <div className="num mt-1 text-[11px] text-slate-500">
                                                                SKU:{" "}
                                                                {item.sku ||
                                                                    "—"}
                                                            </div>
                                                        </div>

                                                        <div className="flex items-center gap-2">
                                                            <span className="rounded-full border border-violet-200 bg-violet-50 px-2.5 py-1 text-xs font-bold text-violet-800">
                                                                الكمية:{" "}
                                                                {Number(
                                                                    item.quantity ||
                                                                        1
                                                                ).toLocaleString(
                                                                    "en-US"
                                                                )}
                                                            </span>

                                                            {item.total !==
                                                                null &&
                                                                item.total !==
                                                                    undefined && (
                                                                    <span className="num text-sm font-extrabold text-slate-950">
                                                                        {formatMoney(
                                                                            item.total
                                                                        )}
                                                                    </span>
                                                                )}
                                                        </div>
                                                    </div>

                                                    {selections.length > 0 && (
                                                        <div className="mt-3 flex flex-wrap gap-1.5">
                                                            {selections.map(
                                                                (
                                                                    selection,
                                                                    selectionIndex
                                                                ) => (
                                                                    <span
                                                                        key={`${selection.label}-${selectionIndex}`}
                                                                        className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-700"
                                                                    >
                                                                        <strong>
                                                                            {
                                                                                selection.label
                                                                            }
                                                                            :
                                                                        </strong>{" "}
                                                                        {
                                                                            selection.value
                                                                        }
                                                                    </span>
                                                                )
                                                            )}
                                                        </div>
                                                    )}

                                                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                                                        <Field
                                                            label="رقم العنصر"
                                                            value={
                                                                item
                                                                    .source
                                                                    ?.source_order_item_id
                                                            }
                                                        />
                                                        <Field
                                                            label="المتغير"
                                                            value={
                                                                item.variant_id
                                                            }
                                                        />
                                                        <Field
                                                            label="الباركود"
                                                            value={
                                                                item.barcode
                                                            }
                                                        />
                                                        <Field
                                                            label="حالة التشغيل"
                                                            value="لم يبدأ"
                                                        />
                                                    </div>
                                                </div>
                                            </div>
                                        </article>
                                    );
                                })}
                            </div>
                        )}
                    </InfoCard>

                    <InfoCard
                        icon={Factory}
                        title="التجهيز وملفات الشراء"
                        testid="order-v2-preparation"
                    >
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="rounded-xl border border-dashed border-violet-200 bg-violet-50/50 p-4">
                                <div className="font-extrabold text-violet-950">
                                    طباعة تجهيز هذا الطلب
                                </div>
                                <p className="mt-2 text-xs leading-6 text-violet-700">
                                    سيستخدم نفس محرك PDF الخاص بصفحة تجهيز المنتجات،
                                    لكن لهذا الطلب فقط.
                                </p>
                                <button
                                    type="button"
                                    disabled
                                    className="mt-4 inline-flex cursor-not-allowed items-center gap-2 rounded-lg bg-slate-200 px-3 py-2 text-xs font-bold text-slate-500"
                                >
                                    <Printer size={17} />
                                    قريبًا
                                </button>
                            </div>

                            <div className="rounded-xl border border-dashed border-amber-200 bg-amber-50/50 p-4">
                                <div className="font-extrabold text-amber-950">
                                    دورة حياة عنصر الطلب
                                </div>
                                <p className="mt-2 text-xs leading-6 text-amber-800">
                                    الموظف المسؤول، المورد، رقم ملف الشراء، تاريخ
                                    الإضافة، من أكد الجاهزية، ومن استلم بعد التجهيز.
                                </p>
                            </div>
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={ClockCounterClockwise}
                        title="سجل الطلب"
                        testid="order-v2-timeline"
                    >
                        <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
                            سيظهر هنا تاريخ أحداث سلة، التجهيز، المورد، الاستلام،
                            الشحن، قيود والمحاسبة.
                        </div>
                    </InfoCard>
                </div>

                <div className="space-y-5">
                    <InfoCard
                        icon={User}
                        title="العميل"
                        testid="order-v2-customer"
                    >
                        <div className="grid gap-4">
                            <Field
                                label="الاسم"
                                value={
                                    customer.name ||
                                    "—"
                                }
                            />
                            <Field
                                label="الجوال"
                                value={
                                    customer.mobile
                                }
                            />
                            <Field
                                label="البريد"
                                value={customer.email}
                            />
                            <Field
                                label="العنوان"
                                value={
                                    customer.shipping_address?.formatted ||
                                    shipping.address?.formatted
                                }
                            />
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={CreditCard}
                        title="الدفع"
                        testid="order-v2-payment"
                    >
                        <div className="grid gap-4">
                            <Field label="طريقة الدفع" value={paymentMethod} />
                            <Field
                                label="البنك المستلم"
                                value={
                                    payment.receiving_bank_name
                                }
                            />
                            <Field
                                label="حالة الدفع"
                                value={payment.status}
                            />
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={Truck}
                        title="الشحن"
                        testid="order-v2-shipping"
                    >
                        <div className="grid gap-4">
                            <Field
                                label="شركة الشحن"
                                value={
                                    shipping.company
                                }
                            />
                            <Field
                                label="رقم التتبع"
                                value={
                                    shipping.tracking_number
                                }
                            />
                            <Field
                                label="حالة الشحن"
                                value={shipping.status}
                            />
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={UsersThree}
                        title="المسؤوليات التشغيلية"
                        testid="order-v2-employees"
                    >
                        <div className="rounded-xl border border-violet-200 bg-violet-50/60 p-4">
                            <div className="text-sm font-extrabold text-violet-950">
                                تُدار لكل عنصر بشكل مستقل
                            </div>
                            <p className="mt-2 text-xs leading-6 text-violet-800">
                                المورد، مسؤول التجهيز، مراحل التصنيع وموظف
                                الاستلام ستظهر داخل عنصر الطلب المناسب، وليس
                                كقيمة واحدة على مستوى الطلب كاملًا.
                            </p>
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={Calculator}
                        title="المحاسبة والربحية"
                        testid="order-v2-accounting"
                    >
                        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                            <div className="text-sm font-extrabold text-amber-950">
                                قسم إداري محمي
                            </div>
                            <p className="mt-2 text-xs leading-6 text-amber-800">
                                سيحتوي فاتورة قيود، السداد، تكلفة المنتج، تكلفة
                                الإعلان، الشحن، العمولات وصافي الربح.
                            </p>
                        </div>
                    </InfoCard>
                </div>
            </div>
        </div>
    );
}
