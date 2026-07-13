import { useEffect, useMemo, useState } from "react";
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
import api from "../lib/api";

function firstValue(obj, keys, fallback = null) {
    for (const key of keys) {
        const value = obj?.[key];
        if (value !== undefined && value !== null && value !== "") return value;
    }
    return fallback;
}

function formatMoney(value) {
    return `${Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
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
    const [order, setOrder] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let cancelled = false;

        (async () => {
            setLoading(true);
            setError("");

            try {
                const { data } = await api.get("/orders", {
                    params: {
                        search: orderNumber,
                        limit: 15,
                        page: 1,
                    },
                });

                const rows = Array.isArray(data?.items)
                    ? data.items
                    : Array.isArray(data?.orders)
                        ? data.orders
                        : [];

                const exact = rows.find((row) => {
                    const number = String(
                        firstValue(row, [
                            "order_number",
                            "reference_id",
                            "salla_order_number",
                            "order_id",
                            "id",
                        ], "")
                    );
                    return number === String(orderNumber);
                });

                if (!cancelled) {
                    if (!exact) {
                        setError("لم يتم العثور على الطلب في مصدر البيانات الحالي.");
                    } else {
                        setOrder(exact);
                    }
                }
            } catch (err) {
                if (!cancelled) {
                    setError(
                        err?.response?.data?.detail ||
                        err?.message ||
                        "تعذّر تحميل تفاصيل الطلب."
                    );
                }
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();

        return () => {
            cancelled = true;
        };
    }, [orderNumber]);

    const items = useMemo(() => {
        if (Array.isArray(order?.items)) return order.items;
        if (Array.isArray(order?.products)) return order.products;
        return [];
    }, [order]);

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
    const total = firstValue(order, [
        "total_amount",
        "total",
        "amount",
        "grand_total",
    ], 0);

    const createdAt = firstValue(order, [
        "order_created_at",
        "order_date",
        "created_at",
        "date",
    ]);

    const status = firstValue(order, [
        "order_status_native",
        "status_name",
        "order_status",
        "status",
    ], "غير محدد");

    const paymentMethod = firstValue(order, [
        "payment_method_native",
        "payment_method_name",
        "payment_method",
    ], "غير محدد");

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
                            تاريخ الإنشاء: {createdAt || "—"}
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
                        title="منتجات الطلب"
                        testid="order-v2-items"
                    >
                        {items.length === 0 ? (
                            <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">
                                تفاصيل المنتجات والصور والخيارات ستُجلب من
                                Salla Order Engine في المرحلة التالية.
                            </div>
                        ) : (
                            <div className="divide-y divide-slate-100">
                                {items.map((item, index) => (
                                    <div
                                        key={
                                            item.order_item_id ||
                                            item.id ||
                                            item.sku ||
                                            index
                                        }
                                        className="flex gap-4 py-4 first:pt-0 last:pb-0"
                                    >
                                        <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
                                            {item.image_url || item.image ? (
                                                <img
                                                    src={item.image_url || item.image}
                                                    alt={item.name || "صورة المنتج"}
                                                    className="h-full w-full object-cover"
                                                />
                                            ) : (
                                                <Package
                                                    size={28}
                                                    className="text-slate-300"
                                                />
                                            )}
                                        </div>

                                        <div className="min-w-0 flex-1">
                                            <div className="font-extrabold text-slate-950">
                                                {item.name ||
                                                    item.product_name ||
                                                    "منتج بدون اسم"}
                                            </div>
                                            <div className="num mt-1 text-xs text-slate-500">
                                                SKU: {item.sku || "—"}
                                            </div>
                                            <div className="mt-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
                                                <Field
                                                    label="الكمية"
                                                    value={item.quantity || 1}
                                                />
                                                <Field
                                                    label="اللون"
                                                    value={
                                                        item.color ||
                                                        item.options_normalized?.color
                                                    }
                                                />
                                                <Field
                                                    label="المقاس"
                                                    value={
                                                        item.size ||
                                                        item.options_normalized?.size
                                                    }
                                                />
                                                <Field
                                                    label="حالة التجهيز"
                                                    value={
                                                        item.preparation_status ||
                                                        "لم يبدأ"
                                                    }
                                                />
                                            </div>
                                        </div>
                                    </div>
                                ))}
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
                                    order.customer_name ||
                                    "—"
                                }
                            />
                            <Field
                                label="الجوال"
                                value={
                                    customer.phone ||
                                    customer.mobile ||
                                    order.customer_mobile
                                }
                            />
                            <Field
                                label="البريد"
                                value={customer.email || order.customer_email}
                            />
                            <Field
                                label="العنوان"
                                value={
                                    order.shipping_address?.formatted ||
                                    order.shipping_address ||
                                    customer.address
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
                                    order.receiving_bank_name ||
                                    order.receiving_bank ||
                                    order.bank_name
                                }
                            />
                            <Field
                                label="حالة الدفع"
                                value={order.payment_status}
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
                                    order.shipping_company ||
                                    order.shipping_company_name
                                }
                            />
                            <Field
                                label="رقم التتبع"
                                value={
                                    order.tracking_number ||
                                    order.shipment_number
                                }
                            />
                            <Field
                                label="حالة الشحن"
                                value={order.shipping_status}
                            />
                        </div>
                    </InfoCard>

                    <InfoCard
                        icon={UsersThree}
                        title="الموظفون والمسؤوليات"
                        testid="order-v2-employees"
                    >
                        <div className="grid gap-4">
                            <Field
                                label="مسؤول المتابعة والتجهيز"
                                value={order.preparation_employee_name}
                            />
                            <Field
                                label="موظف استلام المنتج"
                                value={order.receiving_employee_name}
                            />
                            <Field
                                label="المورد"
                                value={order.supplier_name}
                            />
                            <Field
                                label="ملف الشراء"
                                value={order.purchase_batch_number}
                            />
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
