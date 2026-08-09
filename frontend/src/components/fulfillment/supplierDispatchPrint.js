import { create as createQrCode } from "qrcode";

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatRiyadhDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return new Intl.DateTimeFormat("ar-SA-u-nu-latn", {
        timeZone: "Asia/Riyadh",
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date);
}

function formatRiyadhDateOnly(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    const parts = Object.fromEntries(
        new Intl.DateTimeFormat("en-CA", {
            timeZone: "Asia/Riyadh",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
        }).formatToParts(date).map((part) => [part.type, part.value]),
    );
    return `${parts.year}/${parts.month}/${parts.day}`;
}

function positiveInteger(value, fallback = 1) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0
        ? Math.max(1, Math.trunc(parsed))
        : fallback;
}

function qrCodeSvg(rawValue) {
    const barcodeValue = String(rawValue || "").trim();
    if (!barcodeValue) return '<div class="barcode-missing">—</div>';
    try {
        const qrValue = barcodeValue.toUpperCase();
        const qr = createQrCode(
            [{ data: qrValue, mode: "alphanumeric" }],
            { errorCorrectionLevel: "H" },
        );
        const quietZone = 2;
        const viewBoxSize = qr.modules.size + (quietZone * 2);
        const modules = [];
        for (let row = 0; row < qr.modules.size; row += 1) {
            for (let column = 0; column < qr.modules.size; column += 1) {
                if (qr.modules.get(row, column)) {
                    modules.push(`M${column + quietZone} ${row + quietZone}h1v1h-1z`);
                }
            }
        }
        return `<svg class="qr-code" viewBox="0 0 ${viewBoxSize} ${viewBoxSize}" role="img" aria-label="باركود ${escapeHtml(qrValue)}" shape-rendering="crispEdges" xmlns="http://www.w3.org/2000/svg"><rect width="100%" height="100%" fill="#fff"/><path d="${modules.join("")}" fill="#050505"/></svg>`;
    } catch {
        return '<div class="barcode-missing">تعذّر إنشاء الباركود</div>';
    }
}

function specificationRows(card = {}) {
    const rows = [];
    const seen = new Set();
    const add = (name, value) => {
        const cleanName = String(name || "").trim();
        const cleanValue = String(value ?? "").trim();
        if (!cleanName || !cleanValue) return;
        const key = `${cleanName.toLocaleLowerCase("ar")}:${cleanValue.toLocaleLowerCase("ar")}`;
        if (seen.has(key)) return;
        seen.add(key);
        rows.push({ name: cleanName, value: cleanValue });
    };
    for (const row of [
        ...(card.specifications || card.specifications_snapshot || []),
        ...(card.service_specifications || card.service_specifications_snapshot || []),
    ]) {
        if (!row || typeof row !== "object") continue;
        add(row.name || row.label || row.spec_name, row.value ?? row.spec_value);
    }
    for (const [name, value] of Object.entries(
        card.product_options || card.product_options_snapshot || {},
    )) add(name, value);
    add("ملاحظة", card.preparation_note);
    return rows;
}

function sourceCards(file = {}) {
    if (Array.isArray(file.cards) && file.cards.length) return file.cards;
    return (file.lines || []).flatMap((line) => {
        const quantity = positiveInteger(line?.quantity);
        const orderNumbers = Array.isArray(line?.order_numbers)
            ? line.order_numbers
            : [];
        return Array.from({ length: quantity }, (_, index) => ({
            ...line,
            quantity: 1,
            order_number: orderNumbers[index] || orderNumbers[0] || line?.order_number,
            barcode_value: (line?.barcode_values || [])[index]
                || line?.barcode_value
                || line?.piece_id
                || orderNumbers[index]
                || orderNumbers[0],
        }));
    });
}

function renderProductCard(card = {}, registeredAt) {
    const imageUrl = String(
        card.selected_image_url || card.resolved_image_url || card.image_url || "",
    ).trim();
    const specs = specificationRows(card);
    const orderNumber = String(
        card.order_number || (card.order_numbers || [])[0] || "—",
    ).trim() || "—";
    const shippingCompany = String(card.shipping_company || "—").trim() || "—";
    const quantity = positiveInteger(card.quantity);
    const orderPieceCount = positiveInteger(
        card.order_piece_count || card.total_products_in_order,
        quantity,
    );
    return `
        <article class="product-card">
            <div class="order-side">
                <div class="media-box product-image-box">
                    ${imageUrl
        ? `<img src="${escapeHtml(imageUrl)}" alt="" />`
        : '<div class="image-missing">لا توجد صورة</div>'}
                </div>
                <div class="dispatch-facts">
                    <div class="order-number"><span class="detail-label">ط:</span><span dir="ltr">${escapeHtml(orderNumber)}</span></div>
                    <div class="registered-date" dir="ltr">${escapeHtml(formatRiyadhDateOnly(registeredAt))}</div>
                    <div class="compact-facts"><span><span class="detail-label">الكمية:</span> ${quantity}</span><span>${escapeHtml(shippingCompany)} - ${orderPieceCount}</span></div>
                </div>
            </div>
            <div class="specification-side">
                <div class="media-box qr-box">${qrCodeSvg(card.barcode_value || card.piece_id || orderNumber)}</div>
                <div class="specifications">
                    ${specs.length
        ? specs.map((row) => `<div><span class="detail-label">${escapeHtml(row.name)}:</span> <span>${escapeHtml(row.value)}</span></div>`).join("")
        : '<div class="muted">بدون مواصفات إضافية</div>'}
                </div>
            </div>
        </article>`;
}

export function buildSupplierDispatchPrintHtml(dispatch = {}) {
    const supplierName = String(dispatch.supplier_name || "").trim() || "مورد غير محدد";
    const sourceFiles = Array.isArray(dispatch.source_files) && dispatch.source_files.length
        ? dispatch.source_files
        : [{
            file_number: (dispatch.source_file_numbers || [])[0] || dispatch.file_number,
            registered_at: dispatch.created_at,
            lines: Array.isArray(dispatch.lines) ? dispatch.lines : [],
        }];
    const sourceNumbers = sourceFiles
        .map((file) => file?.file_number)
        .filter(Boolean)
        .join("، ");
    const productCards = sourceFiles.flatMap((file) => (
        sourceCards(file).map((card) => renderProductCard(card, file.registered_at))
    ));
    return `<!doctype html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="utf-8">
            <title>ملف مورد ${escapeHtml(supplierName)}</title>
            <style>
                @page { size: A4 portrait; margin: 10mm; }
                * { box-sizing: border-box; }
                body { margin: 0; color: #172033; font-family: Tahoma, Arial, sans-serif; background: #fff; }
                header { border: 2px solid #6d28d9; border-radius: 14px; padding: 16px; }
                h1 { margin: 0 0 10px; color: #5b21b6; font-size: 24px; }
                .meta { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 18px; font-size: 13px; }
                .products-section { margin-top: 5mm; }
                .product-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: start; gap: 5mm 3mm; direction: rtl; }
                .product-card { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); min-width: 0; background: #fff; direction: ltr; break-inside: avoid; page-break-inside: avoid; }
                .order-side, .specification-side { min-width: 0; padding: 1.2mm; }
                .media-box { display: flex; width: 100%; height: 34mm; align-items: center; justify-content: center; overflow: hidden; background: #fff; }
                .product-image-box img { width: 100%; height: 100%; object-fit: contain; }
                .image-missing, .barcode-missing { color: #94a3b8; font-size: 10px; font-weight: 700; text-align: center; }
                .qr-box { padding: .5mm; }
                .qr-code { width: 100%; height: 100%; }
                .specifications, .dispatch-facts { min-height: 23mm; padding-top: 2mm; direction: rtl; color: #151515; font-size: 10.5px; font-weight: 800; line-height: 1.45; text-align: right; }
                .specifications > div { overflow-wrap: anywhere; }
                .detail-label { color: #d12b2b; font-weight: 900; }
                .muted { color: #94a3b8; }
                .order-number { display: flex; justify-content: flex-start; gap: 2px; font-weight: 900; }
                .registered-date { margin-top: .5mm; font-weight: 900; text-align: right; }
                .compact-facts { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-start; gap: 1mm 2mm; margin-top: .5mm; direction: rtl; font-weight: 900; }
                footer { margin-top: 18px; border-top: 1px solid #d9deea; padding-top: 10px; color: #667085; font-size: 11px; }
                @media screen { body { max-width: 210mm; margin: 10px auto; padding: 10mm; box-shadow: 0 4px 22px rgba(15, 23, 42, .16); } }
            </style>
        </head>
        <body>
            <header>
                <h1>ملف تجهيز المورد — ${escapeHtml(supplierName)}</h1>
                <div class="meta">
                    <div><b>المورد:</b> ${escapeHtml(supplierName)}</div>
                    <div><b>رقم ملف المورد:</b> ${escapeHtml(dispatch.supplier_file_number || dispatch.file_number || "—")}</div>
                    <div><b>موظف التجهيز:</b> ${escapeHtml(dispatch.sent_by_name || "—")}</div>
                    <div><b>تاريخ الرفع:</b> ${escapeHtml(formatRiyadhDate(dispatch.sent_at || dispatch.created_at))}</div>
                    <div><b>إجمالي القطع:</b> ${Number(dispatch.piece_count || 0)}</div>
                    <div><b>ملفات التجهيز:</b> ${escapeHtml(sourceNumbers || "—")}</div>
                </div>
            </header>
            <section class="products-section">
                <div class="product-grid">
                    ${productCards.length ? productCards.join("") : '<div class="empty-products">لا توجد منتجات للطباعة.</div>'}
                </div>
            </section>
            <footer>هذا الملف تشغيلي داخل ميزان، ويجب مطابقة كل قطعة بباركودها عند الاستلام من المورد.</footer>
        </body>
        </html>`;
}

export function printSupplierDispatch(dispatch, targetWindow = null) {
    const printWindow = targetWindow || globalThis.window?.open?.("", "_blank");
    if (!printWindow) return false;
    printWindow.opener = null;
    printWindow.document.open();
    printWindow.document.write(buildSupplierDispatchPrintHtml(dispatch));
    printWindow.document.close();
    printWindow.document.title = `ملف مورد ${String(dispatch?.supplier_name || "مورد").trim() || "مورد"}`;
    printWindow.focus?.();
    globalThis.setTimeout(() => printWindow.print?.(), 150);
    return true;
}
