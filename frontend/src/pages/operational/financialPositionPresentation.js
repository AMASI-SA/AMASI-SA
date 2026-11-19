import XLSX from "../../lib/safeSpreadsheet";
import { formatRiyadhDateTime } from "../../lib/dates";
import {
    UNAVAILABLE, moneyPattern, formatPositionMoney, formatPositionCount, positionText, currencyCodes, isPositionContract,
    PARTY_LABELS, DELIVERY_SCOPE_NOTE, paymentLabel, collectionLabel, destinationLabel, DELIVERY_MONEY_COLUMNS,
    shippingFeeStatusLabel, deliveryRecognitionLabel, shippingRecognitionLabel, deliveryParties, filterDeliveries, readableMessages,
} from "./financialPositionDisplay";

// Retain the original synchronous workbook API for existing consumers.
export * from "./financialPositionDisplay";

function exportValue(value, type) {
    if (type === "money") return typeof value === "string" && moneyPattern.test(value) ? value : UNAVAILABLE;
    if (["count", "exceptions"].includes(type)) return formatPositionCount(value) === UNAVAILABLE ? UNAVAILABLE : String(value);
    return positionText(value);
}

function providerEstimateStatus(row) {
    if (formatPositionMoney(row.estimated_fees) === UNAVAILABLE) return UNAVAILABLE;
    if (row.estimated_fees_complete === true) return "تقدير مكتمل الحساب";
    if (row.estimated_fees_complete === false) return "تقدير جزئي";
    return "اكتمال التقدير غير موثق";
}

function providerEstimateRows(table, totalLabel) {
    if (table.id !== "providers" || !(table.columns || []).some(column => column.key === "estimated_fees")) return [];
    const values = (row, name) => [name, exportValue(row.estimated_fees, "money"), providerEstimateStatus(row),
        exportValue(row.estimated_fee_calculated_orders, "count"), exportValue(row.estimated_fee_incomplete_orders, "count")];
    const rows = [[], ["اكتمال التقدير"],
        ["المنصة", "المبلغ التقديري المعروض", "حالة الحساب", "طلبات محسوبة", "طلبات غير مكتملة"],
        ...(table.rows || []).map(row => values(row, positionText(row.name)))];
    if (table.show_totals !== false && table.totals && Object.keys(table.totals).length) rows.push(values(table.totals, totalLabel));
    return rows;
}

function appendSheet(workbook, name, rows, widths = []) {
    const sheet = XLSX.utils.aoa_to_sheet(rows);
    sheet["!cols"] = widths.map(wch => ({ wch }));
    const worksheet = XLSX.utils.book_append_sheet(workbook, sheet, name);
    worksheet.views = [{ rightToLeft: true }];
    worksheet.pageSetup = { orientation: "landscape", paperSize: 9, fitToPage: true, fitToWidth: 1, fitToHeight: 0 };
    worksheet.eachRow(row => {
        row.height = 25;
        row.eachCell(cell => {
            cell.font = { name: "Cairo", size: 11 };
            cell.alignment = { horizontal: "right", vertical: "middle", wrapText: true };
        });
    });
    worksheet.getRow(1).font = { name: "Cairo", size: 14, bold: true, color: { argb: "FF173458" } };
    return worksheet;
}

const filterDescription = (filters, rows) => [
    `الطلب: ${filters.order?.trim() || "كل الطلبات"}`,
    `الجهة: ${deliveryParties(rows).find(party => party.key === filters.party)?.label || "كل الجهات"}`,
    `الدفع: ${filters.payment ? paymentLabel(filters.payment) : "كل الطرق"}`,
].join(" · ");

export function buildFinancialPositionWorkbook(report, currency, filters = {}, { view = "all", title = "جميع أقسام التقرير" } = {}) {
    if (!isPositionContract(report) || report.status !== "active" || report.permissions?.reports !== true
        || !currencyCodes(report).includes(currency)) throw new Error("report_unavailable");
    const data = report.currencies[currency];
    const deliveries = Array.isArray(data.deliveries?.rows) ? data.deliveries.rows : [];
    const workbook = XLSX.utils.book_new();
    const asOf = formatRiyadhDateTime(report.as_of);
    const metadata = [
        ["تقرير المركز المالي التشغيلي"],
        ["العملة", currency],
        ["آخر احتساب — الرياض", asOf],
        ["بدء التشغيل — الرياض", formatRiyadhDateTime(report.started_at)],
        ["وقت إعداد التقرير — الرياض", formatRiyadhDateTime(report.generated_at)],
        ["نطاق الملف", `${title} · ${currency}`],
        ["الأرصدة الفعلية", data.completeness?.actual === true ? "مكتملة" : "غير مكتملة"],
        ["التوقعات", report.forecast_pending ? "لم تكتمل قراءة التوقعات لهذه اللقطة" : data.completeness?.forecast === true ? "مكتملة" : "غير مكتملة"],
        ["الربح المتوقع", data.completeness?.profit === true ? "مكتمل" : "غير مكتمل"],
        ["نطاق تفاصيل الطلبات", filterDescription(filters, deliveries)],
        ["نطاق الإجماليات", "فلاتر تفاصيل الطلبات لا تغيّر إجماليات التقرير."],
        ["المبالغ", "القيم المالية نصوص دقيقة كما وردت في التقرير؛ غير متاح لا تعني صفرًا."],
        [],
        ...readableMessages([report.refresh_warning, ...(report.issues || []), ...(report.dependencies || []), ...(data.notes || [])]).map(note => [note]),
    ];
    appendSheet(workbook, "معلومات التقرير", metadata, [45, 80]);

    (data.tables || []).forEach((table, index) => {
        const columns = table.columns || [];
        const totalLabel = table.id === "providers" && table.totals_basis === "known_subtotals" ? "المجموع المعلوم" : "الإجمالي";
        const rows = [
            [positionText(table.title), currency],
            ["حالة البيانات", table.complete === true ? "مكتملة" : "غير مكتملة"],
            ...readableMessages(table.notes || []).map(note => [note]),
            [],
            columns.map(column => column.label),
            ...(table.rows || []).map(row => columns.map(column => exportValue(row[column.key], column.type))),
        ];
        if (table.show_totals !== false && table.totals && Object.keys(table.totals).length) rows.push(columns.map((column, columnIndex) =>
            columnIndex === 0 && column.type === "text" ? totalLabel : exportValue(table.totals[column.key], column.type)));
        // Retain the original evidence columns and exact supplied money above.
        // Completeness is explicit: provisional estimates can be fully computed.
        const estimates = providerEstimateRows(table, totalLabel), estimateStart = rows.length + 1;
        rows.push(...estimates);
        const title = `${index + 1} ${table.title || "قسم"}`.replace(/[\\/*?:[\]]/g, " ").slice(0, 31);
        const worksheet = appendSheet(workbook, title, rows, columns.map(column => column.type === "text" ? 38 : 24));
        if (estimates.length) {
            for (let row = estimateStart + 1; row <= rows.length; row += 1) worksheet.getRow(row).height = 36;
        }
    });

    const exceptions = [];
    for (const table of data.tables || []) {
        const seen = new Set();
        for (const row of [...(table.rows || []), {name: "جهة غير محددة", pending_order_details: table.unassigned_order_details || []}]) {
            for (const detail of row.pending_order_details || []) {
                const key = JSON.stringify([row.id || row.name, detail.order_id || detail.order_number]);
                if (seen.has(key)) continue;
                seen.add(key);
                exceptions.push([table.title, row.name, positionText(detail.order_number), positionText(detail.order_id),
                    (detail.reasons || []).map(reason => reason.message).filter(Boolean).join("؛ ")]);
            }
        }
    }
    if (exceptions.length) appendSheet(workbook, "طلبات تحتاج مراجعة", [["القسم", "الجهة", "رقم الطلب", "معرف الطلب", "أسباب عدم التأكد"], ...exceptions], [30, 30, 24, 32, 85]);

    if (["all", "summary", "forecast"].includes(view)) {
        const summaries = [["الإجماليات والمركز والربح", currency], [], ["إجمالي الديون والمستحقات", data.completeness?.actual === true ? "مكتملة" : "الأرصدة المسجّلة فقط"], ["الجهة", "لنا", "علينا"],
            ...(data.debt_rows || []).map(row => [positionText(row.name), exportValue(row.receivable, "money"), exportValue(row.payable, "money")]),
            ["الإجمالي", exportValue(data.debt_totals?.receivable, "money"), exportValue(data.debt_totals?.payable, "money")]];
        [["صافي الأرصدة الحالية", data.current_rows], ["التغير المتوقع من الطلبات المفتوحة", data.forecast_rows], ["حساب الربح المتوقع", data.profit_rows]].forEach(([title, rows]) => {
            summaries.push([], [title], ["البند", "المبلغ", "ملاحظات", "المجموع المعلوم عند نقص البيانات"]);
            (rows || []).forEach(row => summaries.push([positionText(row.label), exportValue(row.amount, "money"), row.reason || "",
                row.known_subtotal == null ? "" : exportValue(row.known_subtotal, "money")]));
        });
        summaries.push([], ["صافي السيولة والمستحقات المتوقع", exportValue(data.metrics?.forecast_monetary_position, "money")],
            ["صافي الربح المتوقع", exportValue(data.metrics?.net_profit, "money")]);
        if (report.forecast_pending) summaries.push(["التوقعات", "لم تكتمل قراءة التوقعات لهذه اللقطة؛ المبلغ غير متاح ولا يُعرض كصفر."]);
        appendSheet(workbook, "الإجماليات والمركز", summaries, [48, 26, 75, 30]);
    }

    if (view === "all" || data.deliveries) {
        const orderRows = filterDeliveries(deliveries, filters);
        appendSheet(workbook, "تفاصيل الطلبات", [
            ["تفاصيل الشحن والتحصيل لكل طلب", currency],
            ["الفلاتر", filterDescription(filters, deliveries)],
            ["حالة البيانات", data.deliveries?.complete === true ? "مكتملة" : "غير مكتملة"],
            ...readableMessages([DELIVERY_SCOPE_NOTE, ...(data.deliveries?.notes || [])]).map(note => [note]),
            [],
            ["رقم الطلب", "معرف الطلب", "جهة التنفيذ", "نوع المنفذ", "تاريخ التسليم — الرياض", "وقت التسجيل — الرياض", "طريقة الدفع", "جهة التحصيل", "حالة التحصيل",
                "أساس احتساب أجرة الشحن", "تاريخ التنفيذ — الرياض", "صندوق خصم الأجرة", "وقت احتساب الأجرة — الرياض", "حالة الطلب عند احتساب الأجرة", "أساس إثبات التسليم", ...DELIVERY_MONEY_COLUMNS.map(column => column.label)],
            ...orderRows.map(row => [positionText(row.order_number), positionText(row.order_id), positionText(row.party_name), PARTY_LABELS[row.party_type] || UNAVAILABLE,
                row.delivered_at ? formatRiyadhDateTime(row.delivered_at) : UNAVAILABLE,
                row.recorded_at ? formatRiyadhDateTime(row.recorded_at) : UNAVAILABLE, paymentLabel(row.payment_method),
                [destinationLabel(row.collection_destination), row.destination_name].filter(Boolean).join(" · "), collectionLabel(row.collection_status),
                shippingRecognitionLabel(row), row.completed_at ? formatRiyadhDateTime(row.completed_at) : UNAVAILABLE, positionText(row.cash_account_name),
                row.fee_recorded_at ? formatRiyadhDateTime(row.fee_recorded_at) : UNAVAILABLE,
                shippingFeeStatusLabel(row), deliveryRecognitionLabel(row),
                ...DELIVERY_MONEY_COLUMNS.map(column => exportValue(row[column.key], "money"))]),
        ], [24, 38, 32, 24, 26, 26, 24, 52, 32, 34, 26, 32, 28, 34, 40, ...DELIVERY_MONEY_COLUMNS.map(() => 26)]);
    }
    return workbook;
}

export function downloadFinancialPositionWorkbook(report, currency, filters, page, filename, shouldContinue) {
    if (!shouldContinue()) return false;
    const workbook = buildFinancialPositionWorkbook(report, currency, filters, page);
    return shouldContinue() && XLSX.writeFile(workbook, filename, { shouldContinue });
}
