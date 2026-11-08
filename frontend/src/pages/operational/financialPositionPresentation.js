import XLSX from "../../lib/safeSpreadsheet";
import { formatRiyadhDateTime } from "../../lib/dates";

export const UNAVAILABLE = "غير متاح";
const moneyPattern = /^(-?)(\d+)\.(\d{2})$/;
const grouped = value => value.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

// Presentation only: retain the exact server string, including cents. Neither
// display nor export calculates balances, fees, exchange rates or profit.
export function formatPositionMoney(value) {
    const match = typeof value === "string" ? moneyPattern.exec(value) : null;
    return match ? `${match[1]}${grouped(match[2])}.${match[3]}` : UNAVAILABLE;
}

export function formatPositionCount(value) {
    if (typeof value === "number") return Number.isSafeInteger(value) && value >= 0 ? grouped(String(value)) : UNAVAILABLE;
    const match = typeof value === "string" ? /^(\d+)(\.\d+)?$/.exec(value) : null;
    return match ? `${grouped(match[1])}${match[2] || ""}` : UNAVAILABLE;
}

export function positionText(value) {
    return typeof value === "string" && value.trim() ? value : UNAVAILABLE;
}

export function currencyCodes(report) {
    const available = Object.keys(report?.currencies || {});
    return [...new Set([...(Array.isArray(report?.currency_order) ? report.currency_order : []), ...available])]
        .filter(code => typeof code === "string" && available.includes(code));
}

export function isPositionContract(report) {
    return report?.schema_version === 1
        && report?.authority === "operational_balance_report"
        && typeof report?.status === "string"
        && report.currencies !== null && typeof report.currencies === "object" && !Array.isArray(report.currencies);
}

export const PAYMENT_LABELS = {
    cash: "نقدًا",
    bank_transfer: "تحويل بنكي",
    card_terminal: "شبكة / بطاقة",
    cod: "الدفع عند الاستلام",
};

export const COLLECTION_LABELS = {
    confirmed: "تحصيل مؤكد",
    courier_collection_unverified: "لم يُوثّق تحصيل الشركة من العميل",
    pending_review: "بانتظار اعتماد التحصيل",
    not_confirmed: "التحصيل غير مؤكد",
    not_applicable: "لا يوجد تحصيل عند التسليم",
    unavailable: "حالة التحصيل غير متاحة",
};

export const DESTINATION_LABELS = {
    store_bank_account: "بنك المتجر · تحصيل مثبت",
    store_pos_receivable: "ذمة شبكة المتجر · بانتظار التسوية",
    driver_custody: "عهدة نقدية لدى المندوب",
    courier_receivable: "ذمة الدفع عند الاستلام لدى شركة الشحن",
    store_bank_account_unknown: "تحويل للمتجر · الحساب البنكي غير متاح",
    store_pos_account_unknown: "شبكة المتجر · حساب التسوية غير متاح",
    not_applicable: "لا توجد جهة تحصيل عند التسليم",
    unavailable: "جهة التحصيل غير متاحة",
};

export const PARTY_LABELS = { courier: "شركة شحن", store_driver: "مندوب المتجر" };
export const DELIVERY_SCOPE_NOTE = "المتبقي وفق مصدر الطلب معلومة من الطلب، ولا يُضاف مرة أخرى إلى تحصيل شركة الشحن أو عهدة المندوب.";
export const paymentLabel = value => PAYMENT_LABELS[value] || UNAVAILABLE;
export const collectionLabel = value => COLLECTION_LABELS[value] || COLLECTION_LABELS.unavailable;
export const destinationLabel = value => DESTINATION_LABELS[value] || DESTINATION_LABELS.unavailable;

export const DELIVERY_MONEY_COLUMNS = [
    { key: "shipping_confirmed", label: "أجرة الشحن المؤكدة", role: "payable" },
    { key: "shipping_settled", label: "المسدد من الأجرة", role: "neutral" },
    { key: "shipping_outstanding", label: "المتبقي من الأجرة", role: "payable" },
    { key: "collection_amount", label: "مبلغ التحصيل", role: "neutral" },
    { key: "driver_custody", label: "عهدة المندوب", role: "neutral" },
    { key: "collection_settled", label: "السداد المخصص للتحصيل", role: "neutral" },
    { key: "collection_outstanding", label: "المتبقي لدى المنفذ", role: "receivable" },
    { key: "store_collection", label: "تحصيل المتجر", role: "neutral" },
    { key: "customer_outstanding", label: "المتبقي وفق مصدر الطلب", role: "neutral" },
    { key: "courier_responsibility", label: "ذمة الشركة الأصلية", role: "receivable" },
];

export function deliveryPartyKey(row) {
    return JSON.stringify([row?.party_type || null, row?.party_id || null, row?.party_name || null]);
}

export function deliveryOrderLabel(row) {
    if (positionText(row?.order_number) !== UNAVAILABLE) return row.order_number;
    return positionText(row?.order_id) !== UNAVAILABLE ? `معرف الطلب: ${row.order_id}` : UNAVAILABLE;
}

export function shippingRecognitionLabel(row) {
    if (row?.recognition_event === "order_completed") return "مدفوع مقدمًا عند تم التنفيذ";
    return row?.delivered_at ? "عند تم التوصيل" : UNAVAILABLE;
}

export function deliveryParties(rows = []) {
    return [...new Map(rows.map(row => [deliveryPartyKey(row), {
        key: deliveryPartyKey(row),
        label: `${positionText(row.party_name)}${PARTY_LABELS[row.party_type] ? ` · ${PARTY_LABELS[row.party_type]}` : ""}`,
    }])).values()];
}

export function filterDeliveries(rows = [], filters = {}) {
    const order = String(filters.order || "").trim().toLocaleLowerCase();
    return rows.filter(row => (!order || [row.order_number, row.order_id].some(value => typeof value === "string" && value.toLocaleLowerCase().includes(order)))
        && (!filters.party || deliveryPartyKey(row) === filters.party)
        && (!filters.payment || (row.payment_method || "unavailable") === filters.payment))
        // Older reports used "confirmed" for the courier's booked responsibility.
        // This field did not attest customer collection.
        .map(row => row.party_type === "courier" && row.collection_status === "confirmed"
            ? { ...row, collection_status: "courier_collection_unverified", collection_amount: null } : row);
}

export function readableMessages(items = []) {
    return [...new Set(items.map(item => typeof item === "string" ? item : item?.message)
        .filter(value => typeof value === "string" && value.trim()))];
}

function exportValue(value, type) {
    if (type === "money") return typeof value === "string" && moneyPattern.test(value) ? value : UNAVAILABLE;
    if (type === "count") return formatPositionCount(value) === UNAVAILABLE ? UNAVAILABLE : String(value);
    return positionText(value);
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

export function buildFinancialPositionWorkbook(report, currency, filters = {}) {
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
        ["الأرصدة الفعلية", data.completeness?.actual === true ? "مكتملة" : "غير مكتملة"],
        ["التوقعات", data.completeness?.forecast === true ? "مكتملة" : "غير مكتملة"],
        ["الربح المتوقع", data.completeness?.profit === true ? "مكتمل" : "غير مكتمل"],
        ["نطاق تفاصيل الطلبات", filterDescription(filters, deliveries)],
        ["نطاق الإجماليات", "فلاتر تفاصيل الطلبات لا تغيّر إجماليات التقرير."],
        ["المبالغ", "القيم المالية نصوص دقيقة كما وردت في التقرير؛ غير متاح لا تعني صفرًا."],
        [],
        ...readableMessages([...(report.issues || []), ...(report.dependencies || []), ...(data.notes || [])]).map(note => [note]),
    ];
    appendSheet(workbook, "معلومات التقرير", metadata, [45, 80]);

    (data.tables || []).forEach((table, index) => {
        const columns = table.columns || [];
        const rows = [
            [positionText(table.title), currency],
            ["حالة البيانات", table.complete === true ? "مكتملة" : "غير مكتملة"],
            ...readableMessages(table.notes || []).map(note => [note]),
            [],
            columns.map(column => column.label),
            ...(table.rows || []).map(row => columns.map(column => exportValue(row[column.key], column.type))),
        ];
        if (table.show_totals !== false && table.totals && Object.keys(table.totals).length) rows.push(columns.map((column, columnIndex) =>
            columnIndex === 0 && column.type === "text" ? "الإجمالي" : exportValue(table.totals[column.key], column.type)));
        const title = `${index + 1} ${table.title || "قسم"}`.replace(/[\\/*?:[\]]/g, " ").slice(0, 31);
        appendSheet(workbook, title, rows, columns.map(column => column.type === "text" ? 38 : 24));
    });

    const summaries = [["الإجماليات والمركز والربح", currency], [], ["إجمالي الديون والمستحقات", data.completeness?.actual === true ? "مكتملة" : "الأرصدة المسجّلة فقط"], ["الجهة", "لنا", "علينا"],
        ...(data.debt_rows || []).map(row => [positionText(row.name), exportValue(row.receivable, "money"), exportValue(row.payable, "money")]),
        ["الإجمالي", exportValue(data.debt_totals?.receivable, "money"), exportValue(data.debt_totals?.payable, "money")]];
    [["صافي الأرصدة الحالية", data.current_rows], ["التغير المتوقع من الطلبات المفتوحة", data.forecast_rows], ["حساب الربح المتوقع", data.profit_rows]].forEach(([title, rows]) => {
        summaries.push([], [title], ["البند", "المبلغ", "ملاحظات"]);
        (rows || []).forEach(row => summaries.push([positionText(row.label), exportValue(row.amount, "money"), row.reason || ""]));
    });
    summaries.push([], ["صافي السيولة والمستحقات المتوقع", exportValue(data.metrics?.forecast_monetary_position, "money")],
        ["صافي الربح المتوقع", exportValue(data.metrics?.net_profit, "money")]);
    appendSheet(workbook, "الإجماليات والمركز", summaries, [48, 26, 75]);

    const orderRows = filterDeliveries(deliveries, filters);
    appendSheet(workbook, "تفاصيل الطلبات", [
        ["تفاصيل الشحن والتحصيل لكل طلب", currency],
        ["الفلاتر", filterDescription(filters, deliveries)],
        ["حالة البيانات", data.deliveries?.complete === true ? "مكتملة" : "غير مكتملة"],
        ...readableMessages([DELIVERY_SCOPE_NOTE, ...(data.deliveries?.notes || [])]).map(note => [note]),
        [],
        ["رقم الطلب", "معرف الطلب", "جهة التنفيذ", "نوع المنفذ", "تاريخ التسليم — الرياض", "طريقة الدفع", "جهة التحصيل", "حالة التحصيل",
            "أساس احتساب أجرة الشحن", "تاريخ التنفيذ — الرياض", "صندوق خصم الأجرة", ...DELIVERY_MONEY_COLUMNS.map(column => column.label)],
        ...orderRows.map(row => [positionText(row.order_number), positionText(row.order_id), positionText(row.party_name), PARTY_LABELS[row.party_type] || UNAVAILABLE,
            row.delivered_at ? formatRiyadhDateTime(row.delivered_at) : UNAVAILABLE, paymentLabel(row.payment_method),
            [destinationLabel(row.collection_destination), row.destination_name].filter(Boolean).join(" · "), collectionLabel(row.collection_status),
            shippingRecognitionLabel(row), row.completed_at ? formatRiyadhDateTime(row.completed_at) : UNAVAILABLE, positionText(row.cash_account_name),
            ...DELIVERY_MONEY_COLUMNS.map(column => exportValue(row[column.key], "money"))]),
    ], [24, 38, 32, 24, 26, 24, 52, 32, 34, 26, 32, ...DELIVERY_MONEY_COLUMNS.map(() => 26)]);
    return workbook;
}
