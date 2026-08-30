import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
    ArrowRight,
    ArrowsClockwise,
    ChartLineUp,
    ChatCircleDots,
    CheckCircle,
    ClockCounterClockwise,
    Copy,
    CreditCard,
    DeviceMobile,
    EnvelopeSimple,
    FileText,
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
import {
    issueShippingLabel,
    markOrderRead,
    refreshOrderFromSalla,
    verifyShippingLabel,
} from "../services/orderEngine";
import ReturnDecisionCard from "../components/orders/ReturnDecisionCard";
import OrderActivityPanel from "../components/orders/OrderActivityPanel";
import FulfillmentExperimentPanel from "../components/fulfillment/FulfillmentExperimentPanel";
import { printStoreCourierLabel } from "../lib/storeCourierLabelPrint";

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

function formatWeight(value, unit = "kg") {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "—";
    const normalizedUnit = String(unit || "kg").trim().toLowerCase();
    const label = normalizedUnit === "g" ? "جم" : normalizedUnit === "kg" ? "كجم" : unit;
    return `${new Intl.NumberFormat("ar-SA-u-nu-latn", {
        maximumFractionDigits: 3,
    }).format(amount)} ${label}`;
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
    if (typeof address === "string") return address.trim();
    return [
        address.formatted,
        address.address_line,
        address.address_line1,
        address.description,
        address.city,
        address.district,
        address.neighborhood,
        address.block,
        address.street,
        address.street_name,
        address.building_number,
        address.additional_number,
        address.postal_code,
        address.short_address,
    ]
        .map((value) => typeof value === "object" ? readableScalar(value) : String(value || "").trim())
        .filter((value, index, array) => value && value !== "—" && array.indexOf(value) === index)
        .join("، ");
}

function buildShippingView(order) {
    const nested = order.shipping && typeof order.shipping === "object" ? order.shipping : {};
    const nestedAddress = nested.address && typeof nested.address === "object" ? nested.address : {};
    const rawAddress = order.shipping_address_raw && typeof order.shipping_address_raw === "object"
        ? order.shipping_address_raw
        : {};

    const address = {
        ...rawAddress,
        ...nestedAddress,
        formatted: firstPresent(
            nestedAddress.formatted,
            nestedAddress.address_line,
            order.shipping_address,
        ),
        country: firstPresent(
            nestedAddress.country,
            nestedAddress.country_name,
            rawAddress.country,
            rawAddress.country_name,
            order.shipping_country,
        ),
        city: firstPresent(
            nestedAddress.city,
            rawAddress.city,
            rawAddress.city_name,
            order.shipping_city,
            order.customer_city,
        ),
        district: firstPresent(
            nestedAddress.district,
            nestedAddress.neighborhood,
            nestedAddress.block,
            rawAddress.district,
            rawAddress.neighborhood,
            rawAddress.block,
            order.shipping_district,
        ),
        street: firstPresent(
            nestedAddress.street,
            nestedAddress.street_name,
            nestedAddress.street_number,
            rawAddress.street,
            rawAddress.street_name,
            rawAddress.street_number,
            order.shipping_street,
        ),
        building_number: firstPresent(
            nestedAddress.building_number,
            rawAddress.building_number,
            rawAddress.building_no,
            order.shipping_building_number,
        ),
        additional_number: firstPresent(
            nestedAddress.additional_number,
            rawAddress.additional_number,
            rawAddress.additional_no,
            order.shipping_additional_number,
        ),
        national_address: firstPresent(
            nestedAddress.short_address,
            nestedAddress.national_address,
            rawAddress.short_address,
            rawAddress.national_address,
            rawAddress.national_address_code,
            order.shipping_national_address,
            order.shipping_short_address,
        ),
        postal_code: firstPresent(
            nestedAddress.postal_code,
            rawAddress.postal_code,
            rawAddress.zip_code,
            order.shipping_postal_code,
        ),
        map_url: firstPresent(nestedAddress.map_url, order.shipping_map_url),
        location_url: firstPresent(nestedAddress.location_url, order.shipping_location_url),
    };

    return {
        ...nested,
        company: firstPresent(nested.company, nested.company_name, order.shipping_company),
        method: firstPresent(nested.method, order.shipping_method),
        company_logo: firstPresent(nested.company_logo, nested.logo_url, order.shipping_company_logo),
        tracking_number: firstPresent(nested.tracking_number, order.tracking_number),
        shipment_number: firstPresent(nested.shipment_number, order.shipping_number),
        waybill_number: firstPresent(nested.waybill_number, order.waybill_number),
        status: firstPresent(nested.status, order.shipping_status, order.shipment_status),
        tracking_url: firstPresent(nested.tracking_url, nested.tracking_link, order.tracking_url),
        label_url: firstPresent(nested.label_url, nested.label, order.shipping_label_url),
        address,
    };
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
    const optionLabel = (option) => firstPresent(
        option?.name, option?.label, option?.title, option?.question, option?.key, option?.option,
    );
    const optionValue = (option) => firstPresent(
        option?.value, option?.selected, option?.answer, option?.option_value, option?.text, option?.choice, option?.values,
    );
    push("اللون", item.color);
    push("المقاس", item.size);
    push("الخامة", item.material);
    for (const option of item.options || item.options_raw || []) {
        push(optionLabel(option), optionValue(option));
    }
    for (const field of item.custom_fields || []) {
        push(optionLabel(field), optionValue(field));
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

function ShippingCard({ shipping, customer, orderNumber, onIssued, allowPrinting = false }) {
    const [issuing, setIssuing] = useState(false);
    const [issueError, setIssueError] = useState("");
    const [issueMessage, setIssueMessage] = useState("");
    const [issuedSnapshot, setIssuedSnapshot] = useState(null);
    const address = shipping.address || customer.shipping_address || {};
    const providerTracking = shipping.tracking_number || shipping.shipment_number || shipping.waybill_number;
    const localLabelUrl = shipping.label_url || shipping.label;
    const localStatusKey = String(shipping.status || "")
        .trim()
        .toLowerCase()
        .replaceAll("-", "_")
        .replaceAll(" ", "_");
    const localStatusBlocksPrinting = new Set([
        "pending", "creating", "processing", "cancelled",
        "canceled", "void", "deleted",
    ]).has(localStatusKey);
    const localStatusIsCancelled = new Set([
        "cancelled", "canceled", "void", "deleted",
    ]).has(localStatusKey);
    const localReady = Boolean(
        localLabelUrl && providerTracking && !localStatusBlocksPrinting
    );
    const verifiedStoreLabel = Boolean(
        issuedSnapshot?.ready
        && issuedSnapshot?.label_type === "store_courier"
        && issuedSnapshot?.print_data?.qr_code
    );
    const verifiedSallaLabel = Boolean(
        issuedSnapshot?.ready
        && issuedSnapshot?.label_url
        && (issuedSnapshot?.tracking_number || issuedSnapshot?.shipping_number)
    );
    const labelUrl = verifiedSallaLabel
        ? issuedSnapshot.label_url
        : (localReady ? localLabelUrl : "");
    const hasPrintableLabel = Boolean(labelUrl || verifiedStoreLabel);
    const snapshotTracking = issuedSnapshot?.tracking_number
        || issuedSnapshot?.shipping_number;
    const localTracking = providerTracking && !localStatusIsCancelled
        ? providerTracking
        : "";
    const tracking = snapshotTracking || localTracking;
    const awaitingLabel = Boolean(tracking && !hasPrintableLabel);
    const mapUrl = address.map_url || address.location_url || shipping.map_url;
    const companyLogo = shipping.company_logo || shipping.logo_url;
    const shippingStatus = issuedSnapshot?.status || shipping.status;
    const shippingStatusLabel = {
        store_courier: "بوليصة مندوب المتجر جاهزة",
        created: "تم إصدار رقم الشحنة — رابط البوليصة قيد التجهيز",
        label_pending: "تم إصدار رقم الشحنة — رابط البوليصة قيد التجهيز",
        failed: "فشل إصدار البوليصة — أعد المحاولة",
        verification_failed: "تعذّر التحقق من البوليصة",
    }[shippingStatus] || shippingStatus || "تتبع حالة الشحنة";

    async function issueLabel() {
        if (!allowPrinting || issuing || hasPrintableLabel) return;
        setIssuing(true);
        setIssueError("");
        setIssueMessage("");
        const printWindow = window.open("about:blank", "_blank");
        if (printWindow) {
            printWindow.opener = null;
            printWindow.document.title = "جاري إصدار بوليصة الشحن";
        }
        try {
            const result = await issueShippingLabel(orderNumber);
            setIssuedSnapshot(result);
            setIssueMessage(result?.message || "");
            if (result?.label_type === "store_courier" && result?.ready) {
                if (!printStoreCourierLabel(printWindow, result?.print_data)) {
                    printWindow?.close();
                    setIssuedSnapshot({ ready: false, status: "failed" });
                    setIssueError("تعذّر تجهيز رمز رقم الطلب؛ لم تتم الطباعة.");
                }
            } else if (result?.ready && result?.label_url) {
                if (printWindow) {
                    printWindow.location.replace(result.label_url);
                } else {
                    window.open(result.label_url, "_blank", "noopener,noreferrer");
                }
            } else {
                printWindow?.close();
            }
            onIssued?.();
        } catch (error) {
            printWindow?.close();
            setIssueError(error?.message || "تعذّر إصدار بوليصة الشحن من سلة.");
            onIssued?.();
        } finally {
            setIssuing(false);
        }
    }

    async function printCurrentLabel() {
        if (!allowPrinting || issuing || !hasPrintableLabel) return;
        setIssuing(true);
        setIssueError("");
        setIssueMessage("");
        const printWindow = window.open("about:blank", "_blank");
        if (printWindow) {
            printWindow.opener = null;
            printWindow.document.title = "جاري التحقق من بوليصة الشحن";
        }
        try {
            if (verifiedStoreLabel) {
                if (!printStoreCourierLabel(printWindow, issuedSnapshot?.print_data)) {
                    printWindow?.close();
                    setIssueError("تعذّر تجهيز رمز رقم الطلب؛ لم تتم الطباعة.");
                }
                return;
            }
            const result = await verifyShippingLabel(orderNumber);
            setIssuedSnapshot(result);
            setIssueMessage(result?.message || "");
            if (
                result?.ready
                && result?.label_url
                && (result?.tracking_number || result?.shipping_number)
            ) {
                if (printWindow) {
                    printWindow.location.replace(result.label_url);
                } else {
                    window.open(result.label_url, "_blank", "noopener,noreferrer");
                }
            } else {
                printWindow?.close();
            }
            onIssued?.();
        } catch (error) {
            printWindow?.close();
            // Fail closed: an unverified cached URL must never be printed.
            setIssueError(error?.message || "تعذّر التحقق من البوليصة الحالية في سلة.");
            onIssued?.();
        } finally {
            setIssuing(false);
        }
    }

    const addressRows = [
        ["الدولة", address.country],
        ["المدينة", address.city],
        ["الحي", address.district || address.neighborhood || address.block],
        ["الشارع", address.street || address.street_name || address.street_number],
        ["العنوان", address.formatted || address.address_line || address.address_line1 || address.description || address.location],
        ["العنوان الوطني", address.national_address || address.short_address],
        ["رقم المبنى", address.building_number],
        ["الرقم الإضافي", address.additional_number],
        ["الرمز البريدي", address.postal_code],
    ].filter(([, value]) => isPresent(value) && String(value).trim());

    return (
        <SectionCard title="الشحن" icon={Truck} testid="order-v2-shipping" headerAction={
            !allowPrinting ? (
                <div className="inline-flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-800" data-testid="order-v2-shipping-print-deferred">
                    <Printer size={18} />
                    الطباعة بعد اكتمال التجهيز
                </div>
            ) : hasPrintableLabel ? (
                <button type="button" onClick={printCurrentLabel} disabled={issuing} className="inline-flex items-center gap-2 rounded-lg border border-teal-200 bg-teal-50 px-3 py-2 text-xs font-bold text-teal-800 transition hover:bg-teal-100 disabled:cursor-wait disabled:opacity-60">
                    {issuing ? <SpinnerGap size={18} className="animate-spin" /> : <Printer size={18} />}
                    {issuing
                        ? "تجهيز الطباعة..."
                        : verifiedStoreLabel ? "طباعة بوليصة المتجر" : "طباعة البوليصة"}
                </button>
            ) : (
                <button type="button" onClick={issueLabel} disabled={issuing} className="inline-flex items-center gap-2 rounded-lg border border-teal-200 px-3 py-2 text-xs font-bold text-teal-800 transition hover:bg-teal-50 disabled:cursor-wait disabled:opacity-60">
                    {issuing ? <SpinnerGap size={18} className="animate-spin" /> : <Printer size={18} />}
                    {issuing
                        ? "جاري التحقق من البوليصة..."
                        : awaitingLabel ? "تحديث البوليصة" : "إصدار البوليصة"}
                </button>
            )
        }>
            <div className="flex h-full flex-col justify-center">
                <div className="flex items-center gap-3">
                    {companyLogo && <img src={companyLogo} alt="" className="h-14 w-14 rounded-lg object-contain" />}
                    <div className="text-lg font-extrabold text-slate-800">{shipping.company || shipping.method || "شركة الشحن لم تصل من سلة"}</div>
                </div>

                <div className="mt-4 space-y-2 text-sm">
                    {addressRows.length ? addressRows.map(([label, value]) => (
                        <div key={label} className="flex items-start justify-between gap-4">
                            <span className="font-bold text-slate-400">{label}</span>
                            <span className="max-w-[68%] text-left font-bold text-slate-700">{readableScalar(value)}</span>
                        </div>
                    )) : (
                        <div className="text-slate-400">تفاصيل العنوان لم تصل من سلة</div>
                    )}
                </div>

                {mapUrl && <a href={mapUrl} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1 font-bold text-teal-700"><MapPin size={17} /> موقع العميل على الخريطة</a>}
                {shipping.delivery_estimate && <div className="mt-2 text-sm text-slate-400">{shipping.delivery_estimate}</div>}

                <div className="mt-5 border-t border-slate-100 pt-4">
                    <div className="flex flex-wrap items-center gap-2 text-sm"><span className="text-slate-600">رقم التتبع:</span><span className="num font-bold text-teal-800">{tracking || "—"}</span><CopyValueButton value={tracking} label="نسخ رقم التتبع" /></div>
                    <div className="mt-4 text-sm font-bold text-teal-800">{shippingStatusLabel}</div>
                    {!allowPrinting && <div className="mt-3 rounded-lg bg-amber-50 p-2 text-xs font-bold leading-6 text-amber-800">إصدار وطباعة البوليصة مؤجلان إلى الخطوة الأخيرة بعد تأكيد تجهيز جميع قطع الطلب.</div>}
                    {issueMessage && <div className="mt-3 rounded-lg bg-amber-50 p-2 text-xs font-bold text-amber-800">{issueMessage}</div>}
                    {issueError && <div className="mt-3 rounded-lg bg-rose-50 p-2 text-xs font-bold text-rose-700">{issueError}</div>}
                </div>
            </div>
        </SectionCard>
    );
}

function PaymentCard({ payment, paymentMethod, orderStatus }) {
    const attachment =
        payment.receipt_url ||
        payment.attachment_url ||
        payment.proof_url;

    // الشرط الوحيد: حالة الطلب بانتظار الدفع.
    const waitingForPayment =
        String(orderStatus || "").trim() === "بانتظار الدفع";

    return (
        <SectionCard title="الدفع" icon={CreditCard} testid="order-v2-payment">
            <div className="flex h-full flex-col items-center justify-center text-center">
                <CreditCard
                    size={50}
                    className={
                        waitingForPayment
                            ? "text-rose-500"
                            : "text-slate-400"
                    }
                />

                <div
                    className={`mt-4 text-2xl font-medium ${
                        waitingForPayment
                            ? "text-rose-600"
                            : "text-slate-700"
                    }`}
                >
                    {paymentMethod || "طريقة الدفع غير محددة"}
                </div>

                {waitingForPayment ? (
                    <WarningCircle
                        size={34}
                        className="mt-3 text-rose-500"
                        weight="fill"
                    />
                ) : (
                    <CheckCircle
                        size={34}
                        className="mt-3 text-emerald-400"
                        weight="fill"
                    />
                )}

                {waitingForPayment && (
                    <div className="mt-3 rounded-lg bg-rose-50 px-4 py-2 text-sm font-bold text-rose-600">
                        بانتظار الدفع
                    </div>
                )}

                {attachment && (
                    <a
                        href={attachment}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-4 rounded-lg border border-teal-200 px-3 py-2 text-sm font-bold text-teal-700"
                    >
                        عرض إيصال الدفع
                    </a>
                )}

                {payment.note && (
                    <div className="mt-4 text-sm leading-7 text-rose-500">
                        {payment.note}
                    </div>
                )}
            </div>
        </SectionCard>
    );
}

function ProductCard({ item, index, currency }) {
    const [imageFailed, setImageFailed] = useState(false);
    const selections = collectItemSelections(item);
    const firstImage = Array.isArray(item.image_urls) ? item.image_urls[0] : "";
    const image = String(
        item.image_url || firstImage || item.thumbnail || item.product_thumbnail || ""
    ).trim();
    const weight = isPresent(item.weight) ? `${displayValue(item.weight)} ${displayValue(item.weight_unit || "")}`.trim() : "—";
    const quantity = Number(item.quantity || 1);
    const unitPrice = Number(item.unit_price || 0);
    const discount = Number(item.discount || 0);
    const itemTotal = Number(item.total || 0);
    const explicitTax = Number(item.tax_reported_by_source);
    const beforeTaxAfterDiscount = Math.max((unitPrice * quantity) - discount, 0);
    const derivedTax = Math.max(itemTotal - beforeTaxAfterDiscount, 0);
    const itemTax = Number.isFinite(explicitTax) && explicitTax > 0
        ? explicitTax
        : derivedTax;
    return (
        <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white" data-testid="order-v2-product-card">
            <div className="flex flex-col gap-4 p-5 md:flex-row md:items-start">
                <div className="flex h-28 w-28 shrink-0 items-center justify-center overflow-hidden rounded-2xl bg-slate-100 text-slate-400">{image && !imageFailed ? <img src={image} alt="" className="h-full w-full object-cover" onError={() => setImageFailed(true)} /> : <Package size={42} />}</div>
                <div className="min-w-0 flex-1">
                    <div className="text-xl font-extrabold leading-8 text-slate-950">{item.name || `منتج ${index + 1}`}</div>
                    <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-500"><span>SKU: <b className="num text-slate-700">{item.sku || "—"}</b></span><span>الباركود: <b className="num text-slate-700">{item.barcode || "—"}</b></span></div>
                    {selections.length > 0 && (
                        <div className="mt-5 overflow-hidden rounded-xl border border-slate-200" data-testid="order-v2-customer-options">
                            <div className="border-b border-slate-200 bg-slate-50 px-4 py-3 text-sm font-extrabold text-slate-700">خيارات العميل</div>
                            <div className="divide-y divide-slate-100">
                                {selections.map((selection) => (
                                    <div key={`${selection.label}:${selection.value}`} className="grid min-h-12 grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-center gap-4 px-4 py-3 text-sm">
                                        <div className="font-bold text-slate-400">{selection.label} :</div>
                                        <div className="flex items-center justify-between gap-2 font-bold text-slate-700">
                                            <span className="break-words">{selection.value}</span>
                                            <CopyValueButton value={selection.value} label={`نسخ ${selection.label}`} />
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
            <div className="grid gap-3 border-t border-slate-100 bg-slate-50/50 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                <div><div className="text-xs font-bold text-slate-400">الكمية</div><div className="num mt-1 font-bold">{item.quantity}</div></div>
                <div><div className="text-xs font-bold text-slate-400">الوزن</div><div className="mt-1 font-bold">{weight}</div></div>
                <div><div className="text-xs font-bold text-slate-400">سعر الوحدة قبل الضريبة</div><div className="num mt-1 font-bold">{formatMoney(unitPrice, currency)}</div></div>
                <div><div className="text-xs font-bold text-slate-400">خصم المنتج</div><div className="num mt-1 font-bold text-rose-600">{discount > 0 ? `- ${formatMoney(discount, currency)}` : formatMoney(0, currency)}</div></div>
                <div><div className="text-xs font-bold text-slate-400">ضريبة المنتج</div><div className="num mt-1 font-bold text-slate-700">{itemTax > 0 ? `+ ${formatMoney(itemTax, currency)}` : formatMoney(0, currency)}</div></div>
                <div><div className="text-xs font-bold text-slate-400">إجمالي المنتج شامل الضريبة</div><div className="num mt-1 font-extrabold text-teal-800">{formatMoney(itemTotal, currency)}</div></div>
            </div>
        </article>
    );
}

function OrderSummaryCard({ order, items, currency }) {
    const totals = order.totals || {};
    const subtotal = Number(totals.subtotal || 0);
    const options = Number(totals.options || 0);
    const shipping = Number(totals.shipping || 0);
    const codFee = Number(totals.cod_fee || 0);
    const tax = Number(totals.tax_reported_by_source || 0);
    const total = Number(totals.total || 0);
    const discounts = Array.isArray(totals.discounts) && totals.discounts.length
        ? totals.discounts
        : Number(totals.discount || 0) > 0
            ? [{ title: "الخصم", amount: Number(totals.discount) }]
            : [];
    const fallbackWeight = (items || []).reduce((sum, item) => {
        const weight = Number(item?.weight);
        const quantity = Number(item?.quantity || 1);
        return Number.isFinite(weight) ? sum + (weight * quantity) : sum;
    }, 0);
    const totalWeight = isPresent(order.total_weight)
        ? Number(order.total_weight)
        : fallbackWeight || null;
    const weightUnit = order.total_weight_unit
        || items?.find((item) => item?.weight_unit)?.weight_unit
        || "kg";
    const taxPercent = isPresent(totals.tax_percent)
        ? Number(totals.tax_percent).toFixed(2)
        : null;

    function SummaryRow({ label, children, tone = "normal" }) {
        const toneClass = tone === "discount"
            ? "text-rose-600"
            : tone === "positive" ? "text-slate-800" : "text-slate-700";
        return (
            <div className="flex min-h-14 items-center justify-between gap-5 border-t border-slate-100 px-5 py-3 first:border-t-0">
                <span className="font-bold text-slate-600">{label}</span>
                <span className={`num text-left font-bold ${toneClass}`} dir="ltr">{children}</span>
            </div>
        );
    }

    return (
        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm" data-testid="order-v2-summary">
            <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
                <FileText size={22} className="text-violet-700" />
                <h2 className="text-lg font-extrabold text-slate-950">ملخص الطلب</h2>
            </div>
            <div>
                <SummaryRow label="مجموع السلة">{formatMoney(subtotal, currency)}</SummaryRow>
                {discounts.map((discount, index) => {
                    const label = discount?.title
                        || (discount?.code ? `كوبون خصم ${discount.code}` : "الخصم");
                    return (
                        <SummaryRow key={`${label}-${index}`} label={label} tone="discount">
                            - {formatMoney(Math.abs(Number(discount?.amount || 0)), currency)}
                        </SummaryRow>
                    );
                })}
                <SummaryRow label="خيارات الطلب">{formatMoney(options, currency)}</SummaryRow>
                <SummaryRow label="تكلفة الشحن" tone="positive">
                    {shipping === 0 ? "مجاني" : `+ ${formatMoney(shipping, currency)}`}
                </SummaryRow>
                {codFee > 0 && (
                    <SummaryRow label="عمولة الدفع عند الاستلام" tone="positive">
                        + {formatMoney(codFee, currency)}
                    </SummaryRow>
                )}
                <SummaryRow label={`الضريبة${taxPercent !== null ? ` (${taxPercent}%)` : ""}`} tone="positive">
                    {tax === 0 ? formatMoney(0, currency) : `+ ${formatMoney(tax, currency)}`}
                </SummaryRow>
            </div>
            <div className="grid min-h-20 grid-cols-3 items-center gap-3 border-t border-slate-200 bg-slate-50 px-5 py-4">
                <div className="font-extrabold text-slate-700">إجمالي الطلب</div>
                <div className="text-center font-extrabold text-teal-800">
                    {totalWeight !== null ? formatWeight(totalWeight, weightUnit) : "—"}
                </div>
                <div className="num text-left text-lg font-extrabold text-teal-800" dir="ltr">{formatMoney(total, currency)}</div>
            </div>
        </section>
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
    const rawUtm = sourceObject.utm_raw || {};
    const attributionIncomplete = sourceObject.match_status !== "matched";

    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-advanced-info">
            <div className="mb-5"><h2 className="text-xl font-extrabold text-slate-950">معلومات الطلب المتقدمة</h2><p className="mt-1 text-sm text-slate-500">المصدر، المسؤوليات، ومتابعة دورة تنفيذ الطلب.</p></div>
            <div className="grid gap-5 lg:grid-cols-3">
                <div className={`rounded-2xl border p-4 ${attributionIncomplete ? "border-amber-300 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`} data-testid="order-v2-ad-attribution">
                    <div className="mb-3 flex items-center gap-2"><Megaphone size={21} className="text-violet-700" weight="fill" /><h3 className="font-extrabold">الإسناد الإعلاني</h3></div>
                    {attributionIncomplete && <div className="mb-3 rounded-xl border border-amber-300 bg-white p-3 text-sm font-bold text-amber-900">غير محسوم — إسناد الطلبات غير مكتمل: {sourceObject.unmatched_reason || "لم تصل هوية الحملة كاملة"}</div>}
                    <InfoRow label="المصدر" value={sourceObject.source || sourceObject.platform} />
                    <InfoRow label="Campaign" value={sourceObject.campaign_name} />
                    <InfoRow label="Campaign ID" value={sourceObject.campaign_id} />
                    <InfoRow label="Ad Squad / Ad Set" value={sourceObject.ad_squad_name} />
                    <InfoRow label="Ad Squad ID" value={sourceObject.ad_squad_id} />
                    <InfoRow label="Ad" value={sourceObject.ad_name} />
                    <InfoRow label="Ad ID" value={sourceObject.ad_id} />
                    <InfoRow label="UTM الخام" value={Object.entries(rawUtm).filter(([, value]) => value).map(([key, value]) => `${key}=${value}`).join(" · ")} />
                    <InfoRow label="طريقة المطابقة" value={sourceObject.match_method} />
                    <InfoRow label="الثقة" value={`${Math.round(Number(sourceObject.match_confidence || 0) * 100)}%`} />
                    <InfoRow label="نافذة الإسناد" value={sourceObject.attribution_window} />
                    <InfoRow label="توقيت الرياض" value={sourceObject.order_created_at_riyadh} />
                    <InfoRow label="توقيت حساب المنصة" value={sourceObject.order_created_at_account} />
                    {sourceObject.entity_url && <Link to={sourceObject.entity_url} className="mt-3 inline-flex rounded-lg bg-violet-700 px-3 py-2 text-sm font-bold text-white">فتح الكيان في صفحة الحملات</Link>}
                </div>
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
    const [searchParams] = useSearchParams();
    const requestedReturnTo = searchParams.get("returnTo");
    const allowedReturnPaths = new Set(["/orders-v2", "/dashboard-v2", "/dashboard-advanced"]);
    const returnTo = allowedReturnPaths.has(requestedReturnTo) ? requestedReturnTo : "/orders-v2";
    const returnLabel = returnTo.startsWith("/dashboard") ? "العودة إلى لوحة التحكم" : "العودة إلى الطلبات";
    const { order, loading, error, reload: reloadOrder } = useOrder(orderNumber);
    const { items, loading: itemsLoading, error: itemsError, reload: reloadItems } = useOrderItems(orderNumber);
    const [returnEngineOpen, setReturnEngineOpen] = useState(false);
    const [refreshingFromSalla, setRefreshingFromSalla] = useState(false);
    const [refreshFromSallaError, setRefreshFromSallaError] = useState("");
    const [refreshFromSallaMessage, setRefreshFromSallaMessage] = useState("");
    const itemCount = useMemo(() => items.length, [items]);
    const openedOrderNumber = String(
        order?.order_number || orderNumber || ""
    ).trim();

    useEffect(() => {
        if (!openedOrderNumber) return;
        void markOrderRead(openedOrderNumber).catch(() => {
            // Reading remains non-blocking; a failed marker must not hide the order.
        });
    }, [openedOrderNumber]);

    useEffect(() => {
        setReturnEngineOpen(false);
    }, [openedOrderNumber]);

    async function updateOrderFromSalla() {
        if (!openedOrderNumber || refreshingFromSalla) return;
        setRefreshingFromSalla(true);
        setRefreshFromSallaError("");
        setRefreshFromSallaMessage("");
        try {
            const result = await refreshOrderFromSalla(openedOrderNumber, { force: true });
            await Promise.all([reloadOrder(), reloadItems()]);
            setRefreshFromSallaMessage(
                result?.address_found
                    ? "تم تحديث الطلب والعنوان من سلة."
                    : "تم تحديث الطلب من سلة؛ لم ترجع سلة عنوان توصيل في بيانات الطلب.",
            );
        } catch (refreshError) {
            setRefreshFromSallaError(refreshError?.message || "تعذّر تحديث الطلب من سلة.");
        } finally {
            setRefreshingFromSalla(false);
        }
    }

    if (loading) return <div className="flex min-h-[60vh] items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>;
    if (error || !order) return <div className="space-y-4" dir="rtl"><Link to={returnTo} className="inline-flex items-center gap-2 font-bold text-violet-700"><ArrowRight size={18} /> {returnLabel}</Link><div className="rounded-2xl border border-rose-200 bg-rose-50 p-6 text-rose-800"><div className="flex items-center gap-2 font-extrabold"><WarningCircle size={24} weight="fill" /> تعذّر فتح الطلب</div><p className="mt-2 text-sm">{error}</p></div></div>;

    const customer = order.customer || {};
    const payment = order.payment || {};
    const shipping = buildShippingView(order);
    const currency = normalizeCurrency(order.currency || order.currency_code || order.totals?.currency || order.amounts?.currency);
    const total = order.totals?.total ?? order.total ?? 0;
    const status = order.status_native || order.status || "غير محدد";
    const paymentMethod = payment.method_native || payment.method || "غير محدد";

    return (
        <div className="space-y-5" dir="rtl" data-testid="order-details-v2-page">
            <div className="flex flex-col gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm lg:flex-row lg:items-center lg:justify-between">
                <div><Link to={returnTo} className="mb-3 inline-flex items-center gap-2 text-sm font-bold text-violet-700"><ArrowRight size={17} /> {returnLabel}</Link><h1 className="num text-2xl font-extrabold text-slate-950">الطلب #{orderNumber}</h1><div className="mt-2 flex flex-wrap items-center gap-2 text-sm"><span className="rounded-full border border-sky-200 bg-sky-50 px-3 py-1 font-bold text-sky-800">{status}</span><span className="text-slate-500">تاريخ الإنشاء: {formatOrderDate(order.created_at)}</span></div></div>
                <div className="flex flex-col items-start gap-3 lg:items-end">
                    <div className="text-left"><div className="num text-2xl font-extrabold text-slate-950">{formatMoney(total, currency)}</div><div className="mt-1 text-xs font-bold text-slate-400">عملة الطلب: {currency}</div></div>
                    <button
                        type="button"
                        onClick={updateOrderFromSalla}
                        disabled={refreshingFromSalla}
                        data-testid="order-v2-refresh-from-salla"
                        className="inline-flex items-center justify-center gap-2 rounded-xl border border-violet-200 bg-violet-50 px-4 py-2.5 text-sm font-extrabold text-violet-800 transition hover:bg-violet-100 disabled:cursor-wait disabled:opacity-60"
                    >
                        <ArrowsClockwise size={19} weight="bold" className={refreshingFromSalla ? "animate-spin" : ""} />
                        {refreshingFromSalla ? "جاري التحديث…" : "تحديث من سلة"}
                    </button>
                    {refreshFromSallaMessage && <div className="max-w-sm text-right text-xs font-bold text-emerald-700">{refreshFromSallaMessage}</div>}
                    {refreshFromSallaError && <div className="max-w-sm text-right text-xs font-bold text-rose-700">{refreshFromSallaError}</div>}
                </div>
            </div>

            <div className="grid gap-5 lg:grid-cols-3"><CustomerCard customer={customer} shipping={shipping} /><ShippingCard
    shipping={shipping}
    customer={customer}
    orderNumber={orderNumber}
    onIssued={reloadOrder}
/><PaymentCard
    payment={payment}
    paymentMethod={paymentMethod}
    orderStatus={status}
/></div>

            <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm" data-testid="order-v2-items">
                <div className="mb-4 flex items-center gap-2"><div className="rounded-lg bg-violet-100 p-2 text-violet-700"><Package size={20} weight="fill" /></div><h2 className="font-extrabold text-slate-950">عناصر الطلب ({itemCount.toLocaleString("en-US")})</h2></div>
                {itemsLoading ? <div className="flex min-h-40 items-center justify-center"><SpinnerGap size={28} className="animate-spin text-violet-600" /></div> : itemsError ? <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800"><b>تعذّر تحميل عناصر الطلب</b><p className="mt-2 text-sm">{itemsError}</p><button type="button" onClick={reloadItems} className="mt-3 rounded-lg bg-rose-700 px-3 py-2 text-xs font-bold text-white">إعادة المحاولة</button></div> : items.length === 0 ? <div className="rounded-xl border border-dashed border-slate-300 p-6 text-center text-sm text-slate-500">لا توجد عناصر مرتبطة بهذا الطلب.</div> : <div className="space-y-4">{items.map((item, index) => <ProductCard key={item.order_item_id || index} item={item} index={index} currency={currency} />)}</div>}
            </section>

            <FulfillmentExperimentPanel orderNumber={openedOrderNumber} items={items} />

            <OrderSummaryCard order={order} items={items} currency={currency} />

            {!returnEngineOpen ? (
                <section className="flex flex-col gap-4 rounded-2xl border border-amber-200 bg-gradient-to-l from-amber-50 to-white p-5 shadow-sm sm:flex-row sm:items-center sm:justify-between" data-testid="order-v2-return-entry">
                    <div className="flex items-start gap-3">
                        <div className="rounded-xl bg-amber-100 p-3 text-amber-700"><ClockCounterClockwise size={24} weight="bold" /></div>
                        <div>
                            <h2 className="text-lg font-extrabold text-slate-950">المرتجعات والاستبدال</h2>
                            <p className="mt-1 text-sm text-slate-500">افتح المحرك فقط عند بدء طلب مرتجع أو استبدال لهذا الطلب.</p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => setReturnEngineOpen(true)}
                        disabled={itemsLoading || Boolean(itemsError) || items.length === 0}
                        className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 py-3 text-sm font-extrabold text-white transition hover:bg-violet-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                    >
                        <ClockCounterClockwise size={20} weight="bold" />
                        إنشاء مرتجع أو استبدال
                    </button>
                </section>
            ) : (
                <div className="space-y-3" data-testid="order-v2-return-engine-open">
                    <div className="flex justify-end">
                        <button type="button" onClick={() => setReturnEngineOpen(false)} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-bold text-slate-600 shadow-sm transition hover:bg-slate-50">
                            إغلاق وتأجيل المحرك
                        </button>
                    </div>
                    <ReturnDecisionCard
                        orderNumber={orderNumber}
                        items={items}
                        currency={currency}
                        itemsLoading={itemsLoading}
                    />
                </div>
            )}

            <AdvancedOrderInfo order={order} />
            <AccountingSummary order={order} currency={currency} />
            <OrderActivityPanel
                orderNumber={openedOrderNumber || orderNumber}
            />
        </div>
    );
}
