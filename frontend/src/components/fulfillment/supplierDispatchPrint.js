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

const CODE39 = {
    "0": "nnnwwnwnn", "1": "wnnwnnnnw", "2": "nnwwnnnnw", "3": "wnwwnnnnn",
    "4": "nnnwwnnnw", "5": "wnnwwnnnn", "6": "nnwwwnnnn", "7": "nnnwnnwnw",
    "8": "wnnwnnwnn", "9": "nnwwnnwnn", A: "wnnnnwnnw", B: "nnwnnwnnw",
    C: "wnwnnwnnn", D: "nnnnwwnnw", E: "wnnnwwnnn", F: "nnwnwwnnn",
    G: "nnnnnwwnw", H: "wnnnnwwnn", I: "nnwnnwwnn", J: "nnnnwwwnn",
    K: "wnnnnnnww", L: "nnwnnnnww", M: "wnwnnnnwn", N: "nnnnwnnww",
    O: "wnnnwnnwn", P: "nnwnwnnwn", Q: "nnnnnnwww", R: "wnnnnnwwn",
    S: "nnwnnnwwn", T: "nnnnwnwwn", U: "wwnnnnnnw", V: "nwwnnnnnw",
    W: "wwwnnnnnn", X: "nwnnwnnnw", Y: "wwnnwnnnn", Z: "nwwnwnnnn",
    "-": "nwnnnnwnw", ".": "wwnnnnwnn", " ": "nwwnnnwnn", "*": "nwnnwnwnn",
};

function normalizedBarcodeValue(value) {
    const raw = String(value || "").trim();
    const withoutPrefix = raw.toUpperCase().startsWith("MEZAN-PIECE:")
        ? raw.slice("MEZAN-PIECE:".length)
        : raw;
    return withoutPrefix.toUpperCase().replace(/[^0-9A-Z.\- ]/g, "");
}

function code39Svg(rawValue) {
    const barcodeValue = normalizedBarcodeValue(rawValue);
    if (!barcodeValue) return '<div class="barcode-missing">—</div>';
    const value = `*${barcodeValue}*`;
    const narrow = 2;
    const wide = 5;
    const gap = 2;
    const height = 82;
    let x = 8;
    const bars = [];
    for (const char of value) {
        const pattern = CODE39[char];
        if (!pattern) continue;
        pattern.split("").forEach((unit, index) => {
            const width = unit === "w" ? wide : narrow;
            if (index % 2 === 0) {
                bars.push(`<rect x="${x}" y="4" width="${width}" height="${height}" />`);
            }
            x += width;
        });
        x += gap;
    }
    return `<svg class="barcode" viewBox="0 0 ${x + 8} 90" role="img" aria-label="باركود ${escapeHtml(barcodeValue)}" xmlns="http://www.w3.org/2000/svg"><g fill="#050505">${bars.join("")}</g></svg>`;
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
            <div class="product-visual">
                <div class="media-box product-image-box">
                    ${imageUrl
        ? `<img src="${escapeHtml(imageUrl)}" alt="" />`
        : '<div class="image-missing">لا توجد صورة</div>'}
                </div>
                <div class="specifications">
                    ${specs.length
        ? specs.map((row) => `<div><b>${escapeHtml(row.name)}:</b> ${escapeHtml(row.value)}</div>`).join("")
        : '<div class="muted">بدون مواصفات إضافية</div>'}
                </div>
            </div>
            <div class="product-identity">
                <div class="media-box barcode-box">${code39Svg(card.barcode_value || card.piece_id || orderNumber)}</div>
                <div class="dispatch-facts">
                    <div class="order-number" dir="ltr">ط:${escapeHtml(orderNumber)}</div>
                    <div class="registered-date" dir="ltr">${escapeHtml(formatRiyadhDateOnly(registeredAt))}</div>
                    <div class="compact-facts"><span>الكمية: ${quantity}</span><span>${escapeHtml(shippingCompany)} - ${orderPieceCount}</span></div>
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
    const sourceSections = sourceFiles.map((file) => `
        <section class="source-file">
            <div class="source-title">
                <b>ملف التجهيز: ${escapeHtml(file.file_number || "—")}</b>
                <span>تاريخ الرفع: ${escapeHtml(formatRiyadhDate(file.registered_at))}</span>
            </div>
            <div class="product-grid">
                ${sourceCards(file).map((card) => renderProductCard(card, file.registered_at)).join("")}
            </div>
        </section>`).join("");
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
                .source-file { margin-top: 16px; }
                .source-title { display: flex; justify-content: space-between; gap: 12px; border: 1px solid #ddd6fe; border-radius: 10px; background: #faf5ff; padding: 9px 11px; color: #4c1d95; font-size: 12px; }
                .product-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 3mm; margin-top: 4mm; }
                .product-card { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); overflow: hidden; min-width: 0; border: 1.6px solid #9b5de5; border-radius: 10px; background: linear-gradient(180deg, #fff 0%, #faf5ff 100%); direction: ltr; break-inside: avoid; page-break-inside: avoid; }
                .product-visual, .product-identity { min-width: 0; padding: 2.2mm; }
                .product-identity { border-left: 1px solid #d8b4fe; }
                .media-box { display: flex; width: 100%; height: 31mm; align-items: center; justify-content: center; overflow: hidden; background: #fff; }
                .product-image-box img { width: 100%; height: 100%; object-fit: contain; }
                .image-missing, .barcode-missing { color: #94a3b8; font-size: 9px; font-weight: 700; }
                .barcode-box { padding: 1.5mm 0.8mm; }
                .barcode { width: 100%; height: 100%; }
                .specifications, .dispatch-facts { min-height: 22mm; padding-top: 2mm; direction: rtl; color: #111827; font-size: 8.5px; font-weight: 700; line-height: 1.55; }
                .specifications > div { overflow-wrap: anywhere; }
                .specifications b { font-weight: 900; }
                .muted { color: #94a3b8; }
                .dispatch-facts { text-align: center; }
                .order-number { font-weight: 900; font-size: 9px; }
                .registered-date { margin-top: 1mm; font-size: 8.5px; }
                .compact-facts { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 1mm 2mm; margin-top: 2mm; direction: rtl; font-size: 8.5px; font-weight: 900; }
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
            ${sourceSections}
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
