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
    const renderRows = (lines = []) => lines.map((line, index) => {
        const services = (line.services || [])
            .filter((service) => service?.status !== "completed")
            .map((service) => service?.service_name || "خدمة")
            .filter(Boolean)
            .join("، ");
        const orders = (line.order_numbers || []).join("، ");
        return `
            <tr>
                <td>${index + 1}</td>
                <td><strong>${escapeHtml(line.product_name || "منتج")}</strong><br><small>${escapeHtml(line.sku || "بدون SKU")}</small></td>
                <td>${escapeHtml(orders || "—")}</td>
                <td>${escapeHtml(services || "المنتج الأساسي")}</td>
                <td class="qty">${Number(line.quantity || 0)}</td>
            </tr>`;
    }).join("");
    const sourceSections = sourceFiles.map((file) => `
        <section class="source-file">
            <div class="source-title">
                <b>ملف التجهيز: ${escapeHtml(file.file_number || "—")}</b>
                <span>تاريخ الرفع: ${escapeHtml(formatRiyadhDate(file.registered_at))}</span>
            </div>
            <table>
                <thead><tr><th>#</th><th>المنتج</th><th>أرقام الطلبات</th><th>الخدمة المطلوبة</th><th>الكمية</th></tr></thead>
                <tbody>${renderRows(file.lines || [])}</tbody>
            </table>
        </section>`).join("");
    return `<!doctype html>
        <html lang="ar" dir="rtl">
        <head>
            <meta charset="utf-8">
            <title>ملف مورد ${escapeHtml(supplierName)}</title>
            <style>
                @page { size: A4; margin: 14mm; }
                * { box-sizing: border-box; }
                body { margin: 0; color: #172033; font-family: Tahoma, Arial, sans-serif; }
                header { border: 2px solid #6d28d9; border-radius: 14px; padding: 16px; }
                h1 { margin: 0 0 10px; color: #5b21b6; font-size: 24px; }
                .meta { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px 18px; font-size: 13px; }
                .source-file { break-inside: avoid; margin-top: 18px; }
                .source-title { display: flex; justify-content: space-between; gap: 12px; border: 1px solid #ddd6fe; border-radius: 10px; background: #faf5ff; padding: 9px 11px; color: #4c1d95; font-size: 12px; }
                table { width: 100%; border-collapse: collapse; margin-top: 16px; }
                th, td { border: 1px solid #d9deea; padding: 10px 8px; text-align: right; vertical-align: top; font-size: 12px; }
                th { background: #f3e8ff; color: #4c1d95; }
                small { color: #667085; }
                .qty { text-align: center; font-size: 17px; font-weight: 800; }
                footer { margin-top: 18px; border-top: 1px solid #d9deea; padding-top: 10px; color: #667085; font-size: 11px; }
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
