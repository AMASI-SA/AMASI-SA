function escapePrintHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

export function storeCourierLabelHtml(data = {}) {
    const address = data.address || {};
    const addressLine = address.address_line_two || address.address_line || [
        address.street_number,
        address.block,
        address.city,
        address.country,
    ].filter(Boolean).join("، ");
    const shortAddress = address.short_address || "—";
    const remaining = data.remaining_amount || {};
    const remainingText = `${remaining.amount ?? 0} ${remaining.currency || "SAR"}`;
    const items = (data.items || []).map((item) => (
        `<li>${escapePrintHtml(item.name || "منتج")} × ${escapePrintHtml(item.quantity || 1)}</li>`
    )).join("");
    const logo = data.store_logo
        ? `<img class="logo" src="${escapePrintHtml(data.store_logo)}" alt="شعار المتجر" />`
        : "";

    return `<!doctype html>
<html lang="ar" dir="rtl">
<head>
    <meta charset="utf-8" />
    <title>بوليصة مندوب المتجر - ${escapePrintHtml(data.order_number)}</title>
    <style>
        @page { size: A6 portrait; margin: 0; }
        * { box-sizing: border-box; }
        html, body { margin: 0; background: #fff; color: #111; font-family: Tahoma, Arial, sans-serif; }
        .sheet { width: 100mm; min-height: 148mm; margin: 0 auto; padding: 5mm; }
        .header { display: flex; justify-content: space-between; align-items: flex-start; gap: 5mm; }
        .brand { min-width: 36mm; text-align: center; font-weight: 700; font-size: 13pt; }
        .logo { display: block; max-width: 31mm; max-height: 19mm; margin: 0 auto 2mm; object-fit: contain; }
        .meta { direction: rtl; text-align: right; font-size: 10pt; line-height: 1.75; }
        .meta b, .customer b { font-weight: 700; }
        .divider { border-top: 0.55mm solid #111; margin: 3mm 0; }
        h1 { margin: 0 0 2mm; text-align: center; font-size: 14pt; }
        .customer { border: 0.4mm solid #555; border-radius: 3mm; padding: 3mm; font-size: 10pt; line-height: 1.65; }
        .row { margin-bottom: 1mm; overflow-wrap: anywhere; }
        .items { margin: 2mm 0 0; padding-right: 5mm; font-size: 8.5pt; line-height: 1.5; }
        .remaining { margin-top: 2mm; font-size: 10pt; }
        .qr { display: block; width: 30mm; height: 30mm; margin: 2mm auto 1mm; image-rendering: pixelated; }
        .barcode-value { direction: ltr; text-align: center; font: 700 12pt Arial, sans-serif; letter-spacing: 0.7mm; }
        .caption { text-align: center; color: #444; font-size: 7.5pt; margin-top: 1mm; }
        @media screen { body { background: #e5e7eb; } .sheet { background: #fff; box-shadow: 0 2mm 8mm #999; } }
    </style>
</head>
<body>
    <main class="sheet">
        <section class="header">
            <div class="brand">${logo}<div>${escapePrintHtml(data.store_name || "المتجر")}</div><div dir="ltr">${escapePrintHtml(data.store_phone || "")}</div></div>
            <div class="meta">
                <div><b>التاريخ:</b> <span dir="ltr">${escapePrintHtml(data.order_date || "—")}</span></div>
                <div><b>رقم الطلب:</b> <span dir="ltr">${escapePrintHtml(data.order_number || "—")}</span></div>
                <div><b>التوصيل:</b> ${escapePrintHtml(data.courier_name || "مندوب المتجر")}</div>
            </div>
        </section>
        <div class="divider"></div>
        <h1>معلومات العميل</h1>
        <section class="customer">
            <div class="row"><b>الاسم:</b> ${escapePrintHtml(data.customer_name || "—")}</div>
            <div class="row"><b>الهاتف:</b> <span dir="ltr">${escapePrintHtml(data.customer_phone || "—")}</span></div>
            <div class="row"><b>العنوان:</b> ${escapePrintHtml(addressLine || "—")}</div>
            <div class="row"><b>العنوان الوطني:</b> <span dir="ltr">${escapePrintHtml(shortAddress)}</span></div>
            ${items ? `<ul class="items">${items}</ul>` : ""}
        </section>
        <div class="remaining"><b>المبلغ المتبقي:</b> <span dir="ltr">${escapePrintHtml(remainingText)}</span></div>
        <img class="qr" src="${escapePrintHtml(data.qr_code || "")}" alt="رمز رقم الطلب" />
        <div class="barcode-value">${escapePrintHtml(data.barcode_value || data.order_number || "")}</div>
        <div class="caption">عند مسح الرمز يظهر رقم الطلب فقط</div>
    </main>
</body>
</html>`;
}

export function printStoreCourierLabel(printWindow, data) {
    if (!printWindow || !data?.qr_code) return false;
    printWindow.document.open();
    printWindow.document.write(storeCourierLabelHtml(data));
    printWindow.document.close();
    window.setTimeout(() => {
        printWindow.focus();
        printWindow.print();
    }, 700);
    return true;
}
