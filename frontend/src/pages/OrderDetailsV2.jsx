import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
    ArrowRight,
    ChartLineUp,
    ChatCircleDots,
    CheckCircle,
    ClockCounterClockwise,
    Copy,
    CreditCard,
    DeviceMobile,
    EnvelopeSimple,
    MapPin,
    Megaphone,
    Package,
    Phone,
    Printer,
    SpinnerGap,
    Truck,
    User,
    UsersThree,
    WarningCircle,
    WhatsappLogo,
} from "@phosphor-icons/react";
import { useOrder } from "../hooks/useOrders";
import { useOrderItems } from "../hooks/useOrderItems";

const THREE_DECIMAL_CURRENCIES = new Set(["BHD", "KWD", "OMR"]);

function normalizeCurrency(value) {
    const code = String(value || "SAR").trim().toUpperCase();
    return /^[A-Z]{3}$/.test(code) ? code : "SAR";
}

function formatMoney(value, currency = "SAR") {
    const code = normalizeCurrency(currency);
    const decimals = THREE_DECIMAL_CURRENCIES.has(code) ? 3 : 2;
    try {
        return new Intl.NumberFormat("ar-SA-u-nu-latn", {
            style: "currency",
            currency: code,
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        }).format(Number(value || 0));
    } catch {
        return `${Number(value || 0).toLocaleString("en-US", {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        })} ${code}`;
    }
}

function formatOrderDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
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

function isPresent(value) {
    return value !== null && value !== undefined && value !== "";
}

function firstPresent(...values) {
    return values.find(isPresent);
}

function readableScalar(value) {
    if (!isPresent(value)) return "—";
    if (Array.isArray(value)) {
        const parts = value.map(readableScalar).filter((part) => part !== "—");
        return parts.length ? parts.join("، ") : "—";
    }
    if (typeof value === "object") {
        return readableScalar(firstPresent(
            value.name,
            value.label,
            value.title,
            value.display_name,
            value.value,
            value.code,
            value.slug,
            value.id,
        ));
    }
    if (typeof value === "boolean") return value ? "نعم" : "لا";
    return String(value);
}

function displayValue(value) {
    return readableScalar(value);
}

function normalizePhone(value) {
    const raw = String(value || "").trim();
    if (!raw) return "";
    const digits = raw.replace(/\D/g, "");
    if (!digits) return "";
    if (digits.startsWith("966")) return `+${digits}`;
    if (digits.startsWith("00")) return `+${digits.slice(2)}`;
    if (digits.startsWith("0")) return `+966${digits.slice(1)}`;
    if (digits.length === 9 && digits.startsWith("5")) return `+966${digits}`;
    return raw.startsWith("+") ? raw : `+${digits}`;
}

function addressText(address) {
    if (!address) return "";
    return [
        address.formatted,
        address.address_line,
        address.city,
        address.district,
        address.block,
        address.street,
        address.building_number,
        address.additional_number,
        address.postal_code,
        address.short_address,
    ]
        .map((value) => String(value || "").trim())
        .filter((value, index, array) => value && array.indexOf(value) === index)
        .join("، ");
}

function collectItemSelections(item) {
    const rows = [];
    const seen = new Set();
    const push = (label, value) => {
        const normalizedLabel = String(label || "").trim();
        if (!normalizedLabel || !isPresent(value)) return;
        const shownValue = displayValue(value);
        if (shownValue === "—") return;
        const key = `${normalizedLabel}:${shownValue}`;
        if (seen.has(key)) return;
        seen.add(key);
        rows.push({ label: normalizedLabel, value: shownValue });
    };
    push("اللون", item.color);
    push("المقاس", item.size);
    push("الخامة", item.material);
    for (const option of item.options || item.options_raw || []) {
        push(option?.name || option?.label || option?.key, option?.value || option?.selected || option?.text || option?.choice);
    }
    for (const field of item.custom_fields || []) {
        push(field?.name || field?.label || field?.key, field?.value || field?.text);
    }
    return rows;
}

function SectionCard({ title, icon: Icon, headerAction, children, testid, className = "" }) {
    return (
        <section className={`flex min-h-[330px] flex-col rounded-2xl border border-slate-200 bg-white p-5 shadow-sm ${className}`} data-testid={testid}>
            <div className="mb-5 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                    <div className="rounded-lg bg-violet-100 p-2 text-violet-700"><Icon size={20} weight="fill" /></div>
                    <h2 className="text-xl font-extrabold text-slate-900">{title}</h2>
                </div>
                {headerAction}
            </div>
            <div className="flex-1">{children}</div>
        </section>
    );
}

function CompactAction({ href, label, icon: Icon, onClick, disabled }) {
    const className = `flex h-10 w-10 items-center justify-center rounded-full transition ${disabled ? "cursor-not-allowed bg-slate-100 text-slate-300" : "bg-slate-100 text-slate-600 hover:bg-teal-50 hover:text-teal-700"}`;
    if (href && !disabled) return <a href={href} className={className} aria-label={label} title={label}><Icon size={20} /></a>;
    return <button type="button" className={className} onClick={onClick} disabled={disabled} aria-label={label} title={label}><Icon size={20} /></button>;
}

function CopyValueButton({ value, label = "نسخ" }) {
    const [copied, setCopied] = useState(false);
    const text = String(value || "").trim();
    async function copyValue() {
        if (!text) return;
        await navigator.clipboard?.writeText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1200);
    }
    return (
        <button type="button" onClick={copyValue} disabled={!text} className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-teal-700 transition hover:bg-teal-50 disabled:text-slate-300" aria-label={copied ? "تم النسخ" : label} title={copied ? "تم النسخ" : label}>
            {copied ? <CheckCircle size={18} weight="fill" /> : <Copy size={18} />}
        </button>
    );
}

function CustomerAvatar({ person }) {
    const avatar = String(person?.avatar_url || "").trim();
    return (
        <div className="relative flex h-24 w-24 items-center justify-center overflow-hidden rounded-full bg-slate-100 text-slate-400">
            <User size={48} weight="fill" />
            {avatar && <img src={avatar} alt="" className="absolute inset-0 h-full w-full object-cover" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
        </div>
    );
}

function CustomerCard({ customer, shipping }) {
    const [tab, setTab] = useState("buyer");
    const buyerAddress = customer.shipping_address || shipping.address || null;
    const recipient = shipping.recipient || {};
    const recipientAddress = recipient.address || shipping.address || buyerAddress;
    const buyerPhone = normalizePhone(customer.mobile || customer.phone);
    const recipientPhone = normalizePhone(recipient.mobile || recipient.phone);
    const hasIndependentRecipient = Boolean(recipient.name || recipientPhone || recipient.email || recipient.notes || (recipientAddress && addressText(recipientAddress) !== addressText(buyerAddress))) && !(String(recipient.name || "").trim() === String(customer.name || "").trim() && recipientPhone === buyerPhone && addressText(recipientAddress) === addressText(buyerAddress));
    const active = tab === "recipient" && hasIndependentRecipient ? { ...recipient, mobile: recipientPhone } : { ...customer, mobile: buyerPhone };
    const phone = normalizePhone(active.mobile || active.phone);
    const email = String(active.email || "").trim();
    return (
        <SectionCard title="العميل" icon={User} testid="order-v2-customer" headerAction={
            <div className="inline-flex rounded-xl border border-teal-200 bg-white p-1 text-xs font-bold">
                <button type="button" onClick={() => setTab("buyer")} className={`rounded-lg px-3 py-2 ${tab === "buyer" || !hasIndependentRecipient ? "bg-teal-50 text-teal-800" : "text-slate-500"}`}>المشتري</button>
                <button type="button" disabled={!hasIndependentRecipient} onClick={() => setTab("recipient")} className={`rounded-lg px-3 py-2 ${tab === "recipient" && hasIndependentRecipient ? "bg-teal-50 text-teal-800" : "text-slate-500"} disabled:opacity-35`}>المستلم</button>
            </div>
        }>
            <div className="flex h-full flex-col items-center justify-center text-center">
                <CustomerAvatar person={active} />
                <div className="mt-4 text-xl font-extrabold text-teal-900">{active.name || "عميل بدون اسم"}</div>
                <div className="num mt-2 text-xl font-bold text-slate-600" dir="ltr">{phone || "—"}</div>
                <div className="mt-5 flex flex-wrap justify-center gap-3">
                    <CompactAction href={phone ? `tel:${phone}` : ""} disabled={!phone} label="اتصال" icon={Phone} />
                    <CompactAction href={phone ? `sms:${phone}` : ""} disabled={!phone} label="رسالة نصية" icon={ChatCircleDots} />
                    <CompactAction href={phone ? `https://wa.me/${phone.replace(/\D/g, "")}` : ""} disabled={!phone} label="واتساب" icon={WhatsappLogo} />
                    <CompactAction href={email ? `mailto:${email}` : ""} disabled={!email} label="البريد" icon={EnvelopeSimple} />
                    <CompactAction onClick={() => navigator.clipboard?.writeText(phone)} disabled={!phone} label="نسخ الرقم" icon={Copy} />
                </div>
                {!hasIndependentRecipient && <div className="mt-5 text-xs font-bold text-emerald-700">المستلم هو نفس المشتري</div>}
            </div>
        </SectionCard>
    );
}

function ShippingCard({ shipping, customer }) {
    const address = shipping.address || customer.shipping_address || {};
    const fullAddress = addressText(address);
    const tracking = shipping.tracking_number || shipping.shipment_number || shipping.waybill_number;
    const mapUrl = address.map_url || address.location_url || shipping.map_url;
    const companyLogo = shipping.company_logo || shipping.logo_url;
    return (
        <SectionCard title="الشحن" icon={Truck} testid="order-v2-shipping" headerAction={<button type="button" disabled className="inline-flex items-center gap-2 rounded-lg border border-teal-200 px-3 py-2 text-xs font-bold text-teal-800 disabled:opacity-50"><Printer size={18} /> طباعة البوليصة</button>}>
            <div className="flex h-full flex-col justify-center">
                <div className="flex items-center gap-3">
                    {companyLogo && <img src={companyLogo} alt="" className="h-14 w-14 rounded-lg object-contain" />}
                    <div className="text-lg font-extrabold text-slate-800">{shipping.company || shipping.method || "شركة الشحن غير محددة"}</div>
                </div>
                <div className="mt-4 text-sm leading-7 text-slate-500">{fullAddress || "العنوان غير متوفر"}</div>
                {mapUrl && <a href={mapUrl} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 font-bold text-teal-700"><MapPin size={17} /> موقع العميل على الخريطة</a>}
                {shipping.delivery_estimate && <div className="mt-2 text-sm text-slate-400">{shipping.delivery_estimate}</div>}
                <div className="mt-5 border-t border-slate-100 pt-4">
                    <div className="flex flex-wrap items-center gap-2 text-sm"><span className="text-slate-600">بوليصة الشحن:</span><span className="num font-bold text-teal-800">{tracking || "—"}</span><CopyValueButton value={tracking} label="نسخ رقم البوليصة" /></div>
                    <div className="mt-4 text-sm font-bold text-teal-800">{shipping.status || "تتبع حالة الشحنة"}</div>
                </div>
            </div>
        </SectionCard>
    );
}

function PaymentCard({ payment, paymentMethod }) {
    const attachment = payment.receipt_url || payment.attachment_url || payment.proof_url;
    const paid = String(payment.status || "").toLowerCase().includes("paid") || payment.is_paid === true;
    return (
        <SectionCard title="الدفع" icon={CreditCard} testid="order-v2-payment">
            <div className="flex h-full flex-col justify-center">
                <div className="flex items-center gap-4">
                    {attachment && <img src={attachment} alt="مرفق الدفع" className="h-24 w-20 rounded-lg border border-teal-200 object-cover" />}
                    <div>
                        <div className="flex items-center gap-2 text-lg font-extrabold text-slate-800">{paid && <CheckCircle size={34} className="text-emerald-400" />}{payment.status_native || payment.status || "حالة الدفع غير محددة"}</div>
                        <div className="mt-2 text-sm text-slate-400">{paymentMethod}</div>
                    </div>
                </div>
            </div>
        </SectionCard>
    );
}

function ProductCard({ item, index, currency }) {
    const selections = collectItemSelections(item);
    const quantity = Number(item.quantity || 1);
    const weight = item.weight ? `${Number(item.weight).toLocaleString("en-US")} ${item.weight_unit || "كجم"}` : "—";
    return (
        <article className="overflow-hidden rounded-xl border border-slate-200 bg-white" data-testid={`order-v2-item-${index}`}>
            <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_110px_110px_140px_140px] lg:items-start">
                <div className="min-w-0">
                    <div className="flex items-start gap-3">
                        <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-slate-50">{item.image_url ? <img src={item.image_url} alt={item.name || "صورة المنتج"} className="h-full w-full object-cover" /> : <Package size={27} className="text-slate-300" />}</div>
                        <div className="min-w-0 flex-1"><h3 className="font-extrabold leading-6 text-slate-950">{item.name || "منتج بدون اسم"}</h3><div className="mt-1 flex items-center gap-1 text-xs text-slate-500"><span>SKU:</span><span className="num font-bold">{item.sku || "—"}</span><CopyValueButton value={item.sku} label="نسخ SKU" /></div></div>
                    </div>
                    <div className="mt-4 overflow-hidden rounded-xl border border-slate-200 bg-white">
                        {selections.length > 0 ? selections.map((selection, selectionIndex) => <div key={`${selection.label}-${selectionIndex}`} className="flex min-h-11 items-center justify-between gap-3 border-b border-slate-100 px-3 py-2 last:border-b-0"><div><span className="text-xs font-bold text-slate-400">{selection.label}:</span> <span className="break-words text-sm font-bold text-slate-700">{selection.value}</span></div><CopyValueButton value={selection.value} label={`نسخ ${selection.label}`} /></div>) : <div className="px-3 py-3 text-sm text-slate-400">لا توجد خيارات إضافية</div>}
                    </div>
                </div>
                <div><div className="text-xs font-bold text-slate-400">الكمية</div><div className="mt-1 font-bold">{quantity.toLocaleString("en-US")}</div></div>
                <div><div className="text-xs font-bold text-slate-400">الوزن</div><div className="mt-1 font-bold">{weight}</div></div>
                <div><div className="text-xs font-bold text-slate-400">السعر</div><div className="num mt-1 font-bold">{formatMoney(item.unit_price, currency)}</div></div>
                <div><div className="text-xs font-bold text-slate-400">المجموع</div><div className="num mt-1 font-bold">{formatMoney(item.total, currency)}</div></div>
            </div>
        </article>
    );
}

function InfoRow({ label, value }) {
    return <div className="flex items-start justify-between gap-4 border-b border-slate-100 py-3 last:border-0"><span className="text-sm font-bold text-slate-400">{label}</span><span className="num max-w-[65%] break-words text-left text-sm font-bold text-slate-800">{displayValue(value)}</span></div>;
}

function AdvancedOrderInfo({ order }) {
    const sourceObject = typeof order.source === "object" && order.source ? order.source : {};
    const attribution = firstPresent(order.utm, order.marketing, order.attribution, sourceObject.attribution, sourceObject.utm) || {};
    const source = firstPresent(order.source_native, order.source_name, sourceObject.source_native, sourceObject.source, attribution.source, attribution.utm_source, typeof order.source === "string" ? order.source : null);
    const medium = firstPresent(order.utm_medium, sourceObject.utm_medium, attribution.medium, attribution.utm_medium);
    const campaign = firstPresent(order.utm_campaign, sourceObject.utm_campaign, attribution.campaign, attribution.utm_campaign);
    const channel = firstPresent(order.channel_native, order.channel, order.order_channel, sourceObject.channel, sourceObject.source_event);
    const device = firstPresent(order.device_name, order.device, order.client_device, sourceObject.device, sourceObject.device_type, attribution.device);
    const assignments = firstPresent(order.assignments, order.responsibilities, order.staff) || {};
    const operations = firstPresent(order.fulfillment, order.operations, order.tracking) || {};

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-advanced-info">
            <div className="mb-5"><h2 className="text-xl font-extrabold text-slate-950">معلومات الطلب المتقدمة</h2><p className="mt-1 text-sm text-slate-500">المصدر، المسؤوليات، ومتابعة دورة تنفيذ الطلب.</p></div>
            <div className="grid gap-5 lg:grid-cols-3">
                <div className="rounded-2xl border border-slate-200 p-4">
                    <div className="mb-3 flex items-center gap-2"><Megaphone size={21} className="text-violet-700" weight="fill" /><h3 className="font-extrabold">مصدر الطلب</h3></div>
                    <InfoRow label="المصدر" value={source} />
                    <InfoRow label="الوسيط" value={medium} />
                    <InfoRow label="الحملة" value={campaign} />
                    <InfoRow label="القناة" value={channel} />
                    <InfoRow label="الجهاز" value={device} />
                </div>
                <div className="rounded-2xl border border-slate-200 p-4">
                    <div className="mb-3 flex items-center gap-2"><UsersThree size={21} className="text-violet-700" weight="fill" /><h3 className="font-extrabold">الموظفون والمسؤوليات</h3></div>
                    <InfoRow label="مسؤول الطلب" value={firstPresent(assignments.owner, assignments.order_owner, assignments.assigned_to, order.assigned_to)} />
                    <InfoRow label="التجهيز" value={firstPresent(assignments.preparation, assignments.fulfillment, assignments.preparation_employee)} />
                    <InfoRow label="الشحن" value={firstPresent(assignments.shipping, assignments.shipping_employee)} />
                    <InfoRow label="خدمة العملاء" value={firstPresent(assignments.customer_service, assignments.support, assignments.customer_service_employee)} />
                    <InfoRow label="آخر محدث" value={firstPresent(order.updated_by, assignments.last_updated_by, assignments.updated_by)} />
                </div>
                <div className="rounded-2xl border border-slate-200 p-4">
                    <div className="mb-3 flex items-center gap-2"><DeviceMobile size={21} className="text-violet-700" weight="fill" /><h3 className="font-extrabold">متابعة الطلب</h3></div>
                    <InfoRow label="التجهيز" value={firstPresent(operations.preparation_status, operations.status, order.preparation_status)} />
                    <InfoRow label="الطباعة" value={firstPresent(operations.print_status, order.print_status)} />
                    <InfoRow label="التغليف" value={firstPresent(operations.packing_status, order.packing_status)} />
                    <InfoRow label="التسليم للشركة" value={firstPresent(operations.handover_status, order.handover_status)} />
                    <InfoRow label="التوصيل" value={firstPresent(operations.delivery_status, order.shipping?.status, order.shipment_status)} />
                </div>
            </div>
        </section>
    );
}

function AccountingSummary({ order, currency }) {
    const accounting = order.accounting || order.qoyod || {};
    const totals = order.totals || {};
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-accounting-summary">
            <div className="mb-4 flex items-center gap-2"><ChartLineUp size={22} className="text-violet-700" weight="fill" /><h2 className="text-xl font-extrabold text-slate-950">المحاسبة والربحية</h2></div>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-400">حالة قيود</div><div className="mt-2 font-extrabold">{accounting.status || accounting.qoyod_status || "—"}</div></div>
                <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-400">رقم الفاتورة</div><div className="num mt-2 font-extrabold">{accounting.invoice_number || accounting.invoice_id || "—"}</div></div>
                <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-400">تكلفة الطلب</div><div className="num mt-2 font-extrabold">{totals.cost !== undefined ? formatMoney(totals.cost, currency) : "—"}</div></div>
                <div className="rounded-xl bg-slate-50 p-4"><div className="text-xs font-bold text-slate-400">الربح</div><div className="num mt-2 font-extrabold">{totals.profit !== undefined ? formatMoney(totals.profit, currency) : "—"}</div></div>
            </div>
        </section>
    );
}

export default function OrderDetailsV2() {
    const { orderNumber } = useParams();
    const { order, loading, error } = useOrder(orderNumber);
    const { items, loading: itemsLoading, error: itemsError, reload: reloadItems } = useOrderItems(orderNumber);
    const itemCount = useMemo(() => items.length, [items]);

    if (loading) return <div className="flex min-h-[60vh] items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>;
    if (error || !order) return <div className="space-y-4" dir="rtl"><Link to="/orders-v2" className="inline-flex items-center gap-2 font-bold text-violet-700"><ArrowRight size={18} /> العودة إلى الطلبات</Link><div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800"><div className="flex items-center gap-2 font-extrabold"><WarningCircle size={24} weight="fill" /> تعذّر فتح الطلب</div><p className="mt-2 text-sm">{error}</p></div></div>;

    const customer = order.customer || {};
    const payment = order.payment || {};
    const shipping = order.shipping || {};
    const currency = normalizeCurrency(order.currency || order.currency_code || order.totals?.currency || order.amounts?.currency);
    const total = order.totals?.total ?? order.total ?? 0;
    const status = order.status_native || order.status || "غير محدد";
    const paymentMethod = payment.method_native || payment.method || "غير محدد";

    return (
        <div className="space-y-5" dir="rtl" data-testid="order-details-v2-page">
            <div className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:flex-row lg:items-center lg:justify-between">
                <div><Link to="/orders-v2" className="mb-3 inline-flex items-center gap-2 text-sm font-bold text-violet-700"><ArrowRight size={17} /> العودة إلى الطلبات الجديدة</Link><h1 className="num text-2xl font-extrabold text-slate-950">الطلب #{orderNumber}</h1><div className="mt-2 flex flex-wrap items-center gap-2 text-sm"><span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 font-bold text-sky-800">{status}</span><span className="text-slate-500">تاريخ الإنشاء: {formatOrderDate(order.created_at)}</span></div></div>
                <div className="text-left"><div className="num text-2xl font-extrabold text-slate-950">{formatMoney(total, currency)}</div><div className="mt-1 text-xs font-bold text-slate-400">عملة الطلب: {currency}</div></div>
            </div>

            <div className="grid gap-5 lg:grid-cols-3"><CustomerCard customer={customer} shipping={shipping} /><ShippingCard shipping={shipping} customer={customer} /><PaymentCard payment={payment} paymentMethod={paymentMethod} /></div>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-items">
                <div className="mb-4 flex items-center gap-2"><div className="rounded-lg bg-violet-100 p-2 text-violet-700"><Package size={20} weight="fill" /></div><h2 className="font-extrabold text-slate-950">عناصر الطلب ({itemCount.toLocaleString("en-US")})</h2></div>
                {itemsLoading ? <div className="flex min-h-40 items-center justify-center"><SpinnerGap size={28} className="animate-spin text-violet-600" /></div> : itemsError ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><b>تعذّر تحميل عناصر الطلب</b><p className="mt-2 text-sm">{itemsError}</p><button type="button" onClick={reloadItems} className="mt-3 rounded-lg bg-rose-700 px-3 py-2 text-xs font-bold text-white">إعادة المحاولة</button></div> : items.length === 0 ? <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">لا توجد عناصر مرتبطة بهذا الطلب.</div> : <div className="space-y-4">{items.map((item, index) => <ProductCard key={item.order_item_id || index} item={item} index={index} currency={currency} />)}</div>}
            </section>

            <AdvancedOrderInfo order={order} />
            <AccountingSummary order={order} currency={currency} />

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-timeline">
                <div className="mb-4 flex items-center gap-2"><div className="rounded-lg bg-violet-100 p-2 text-violet-700"><ClockCounterClockwise size={20} weight="fill" /></div><h2 className="font-extrabold text-slate-950">سجل الطلب</h2></div>
                <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">سيظهر هنا تاريخ أحداث الطلب.</div>
            </section>
        </div>
    );
}
