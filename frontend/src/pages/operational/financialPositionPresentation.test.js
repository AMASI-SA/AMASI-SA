import ExcelJS from "exceljs";
import {
    buildFinancialPositionWorkbook, collectionLabel, currencyCodes, deliveryOrderLabel, deliveryPartyKey,
    destinationLabel, filterDeliveries, formatPositionCount, formatPositionMoney, shippingRecognitionLabel,
} from "./financialPositionPresentation";

function report() {
    return {
        schema_version: 1, authority: "operational_balance_report", status: "active", permissions: { reports: true },
        as_of: "2026-11-01T07:00:00Z", currency_order: ["SAR", "USD"], currencies: {
            SAR: {
                completeness: { actual: true, forecast: false, profit: false },
                tables: [{ id: "suppliers", title: "الموردون", complete: true, columns: [
                    { key: "name", label: "المورد", type: "text" }, { key: "payable", label: "علينا", type: "money" },
                ], rows: [{ id: "one", name: "=1+1", payable: "9007199254740993.25" }], totals: { payable: "9007199254740993.25" } }],
                debt_rows: [], debt_totals: { receivable: "0.00", payable: "9007199254740993.25" },
                current_rows: [{ id: "net", label: "الصافي", amount: "42.07" }],
                forecast_rows: [{ id: "future", label: "المتوقع", amount: null, reason: "المصدر غير مكتمل" }],
                profit_rows: [{ id: "profit", label: "الربح", amount: null, reason: "الإيراد غير متاح" }],
                metrics: { forecast_monetary_position: null, net_profit: null },
                deliveries: { complete: false, rows: [
                    { id: "first", order_number: "1001", party_id: "one", party_name: "مندوب تجريبي", party_type: "store_driver", payment_method: "cash", collection_status: "confirmed", collection_destination: "driver_custody", driver_custody: "55.02" },
                    { id: "second", order_number: "1002", party_id: "two", party_name: "شركة تجريبية", party_type: "courier", payment_method: "cod", collection_status: "pending_review", collection_amount: "71.09" },
                ], notes: ["الحساب البنكي غير متاح"] },
            },
            USD: { tables: [{ title: "EXCLUDED-CURRENCY", rows: [] }] },
        },
    };
}

test("money remains exact beyond Number precision and unavailable never becomes zero", () => {
    expect(formatPositionMoney("9007199254740993.25")).toBe("9,007,199,254,740,993.25");
    expect(formatPositionMoney("-1200.07")).toBe("-1,200.07");
    expect(formatPositionMoney("0.00")).toBe("0.00");
    [null, undefined, 0, 15.12, "", "1.5", "1e9", "NaN", " 1.00"].forEach(value => expect(formatPositionMoney(value)).toBe("غير متاح"));
    expect(formatPositionCount(1200)).toBe("1,200");
    expect(formatPositionCount("1.500")).toBe("1.500");
    expect(formatPositionCount(null)).toBe("غير متاح");
});

test("currency choice excludes absent keys and never mixes reports", () => {
    const payload = report();
    payload.currency_order = ["EUR", "SAR", "SAR"];
    expect(currencyCodes(payload)).toEqual(["SAR", "USD"]);
    const workbook = buildFinancialPositionWorkbook(payload, "SAR");
    const values = JSON.stringify(workbook.worksheets.map(sheet => sheet.getSheetValues()));
    expect(values).not.toContain("EXCLUDED-CURRENCY");
    expect(values).toContain("9007199254740993.25");
    expect(values).toContain("42.07");
    expect(values).toContain("المصدر غير مكتمل");
    expect(values).not.toContain("9007199254740992");
});

test("order, party and payment filters preserve evidence and source rows", () => {
    const rows = report().currencies.SAR.deliveries.rows;
    expect(filterDeliveries(rows, { order: " 1001 " })).toEqual([rows[0]]);
    expect(filterDeliveries(rows, { party: deliveryPartyKey(rows[1]) })).toEqual([rows[1]]);
    expect(filterDeliveries(rows, { payment: "cash" })).toEqual([rows[0]]);
    expect(filterDeliveries(rows, { order: "1001", payment: "cod" })).toEqual([]);
    expect(rows).toHaveLength(2);
    expect(collectionLabel("pending_review")).toBe("بانتظار اعتماد التحصيل");
    expect(collectionLabel(null)).toBe("حالة التحصيل غير متاحة");
    expect(destinationLabel("store_bank_account_unknown")).toContain("الحساب البنكي غير متاح");
    expect(collectionLabel("not_applicable")).toBe("لا يوجد تحصيل عند التسليم");
    expect(destinationLabel("not_applicable")).toBe("لا توجد جهة تحصيل عند التسليم");
});

test("workbook exports the visible currency and delivery filters as exact text without formulas", () => {
    const workbook = buildFinancialPositionWorkbook(report(), "SAR", { payment: "cash" });
    const details = JSON.stringify(workbook.getWorksheet("تفاصيل الطلبات").getSheetValues());
    expect(details).toContain("1001");
    expect(details).toContain("55.02");
    expect(details).not.toContain("1002");
    expect(details).not.toContain("71.09");
    const supplier = workbook.worksheets.find(sheet => sheet.name.includes("الموردون"));
    expect(supplier.getSheetValues().flat()).toContain("=1+1");
    let nameCell;
    supplier.eachRow(row => row.eachCell(cell => { if (cell.value === "=1+1") nameCell = cell; }));
    expect(nameCell.formula).toBeUndefined();
    expect(typeof nameCell.value).toBe("string");
    expect(supplier.views[0].rightToLeft).toBe(true);
});

test("prepaid shipping export preserves completion separately from the unproven delivery date", () => {
    const payload = report();
    payload.currencies.SAR.deliveries.rows = [{ id: "prepaid-fixture", order_id: "prepaid-order", order_number: "P-100",
        party_type: "courier", party_name: "شركة الدفع المقدم", payment_mode: "prepaid", recognition_event: "order_completed",
        completed_at: "2026-11-01T07:00:00Z", delivered_at: null, cash_account_name: "صندوق سلة التجريبي",
        shipping_confirmed: "34.50", shipping_settled: "34.50", shipping_outstanding: "0.00" }];
    const rows = buildFinancialPositionWorkbook(payload, "SAR").getWorksheet("تفاصيل الطلبات").getSheetValues().filter(Boolean);
    const header = rows.find(values => values[1] === "رقم الطلب");
    const order = rows.find(values => values[1] === "P-100");
    expect(header).toContain("تاريخ التنفيذ — الرياض");
    expect(order[header.indexOf("تاريخ التنفيذ — الرياض")]).toContain("10:00");
    expect(order[header.indexOf("تاريخ التسليم — الرياض")]).toBe("غير متاح");
    expect(order[header.indexOf("أساس احتساب أجرة الشحن")]).toBe("مدفوع مقدمًا عند تم التنفيذ");
    expect(order[header.indexOf("صندوق خصم الأجرة")]).toBe("صندوق سلة التجريبي");
    expect(order[header.indexOf("المتبقي من الأجرة")]).toBe("0.00");
});

test("post-start delivered status identifies recognition without supplying a delivery date", () => {
    const recognized = { recognition_basis: "delivered_order_created_after_start", delivered_at: null,
        recorded_at: "2026-11-01T07:00:00Z" };
    expect(shippingRecognitionLabel(recognized)).toBe("طلب تم توصيله وأُنشئ بعد بدء التشغيل");
    expect(shippingRecognitionLabel({ ...recognized, recognition_basis: "owner_confirmed_after_start" }))
        .toBe("تأكيد المالك بالتوصيل بعد بدء التشغيل");
    expect(shippingRecognitionLabel({ delivered_at: "2026-11-01T06:00:00Z" })).toBe("عند تم التوصيل");
    expect(shippingRecognitionLabel({ ...recognized, recognition_basis: "unknown" })).toBe("غير متاح");
    expect(shippingRecognitionLabel({ ...recognized, recognition_event: "order_completed", payment_mode: "deferred" }))
        .toBe("أجرة مستحقة عند تم التنفيذ");
});

test.each([
    ["delivered_order_created_after_start", "طلب تم توصيله وأُنشئ بعد بدء التشغيل"],
    ["owner_confirmed_after_start", "تأكيد المالك بالتوصيل بعد بدء التشغيل"],
])("%s export keeps first recording time, unknown delivery time and pending money distinct", (basis, label) => {
    const payload = report();
    payload.as_of = "2026-11-02T12:00:00Z";
    const data = payload.currencies.SAR;
    data.tables.push({ id: "drivers", title: "مناديب المتجر", complete: false, columns: [
        { key: "name", label: "المندوب", type: "text" },
        { key: "delivered_orders", label: "تواصيل مثبتة", type: "count" },
        { key: "confirmed_orders", label: "تواصيل محتسبة", type: "count" },
        { key: "payable", label: "علينا", type: "money" },
        { key: "pending_orders", label: "طلبات غير مؤكدة", type: "exceptions" },
    ], rows: [{ id: "driver", name: "مندوب تجريبي", delivered_orders: 3, confirmed_orders: 1,
        payable: "10.00", pending_orders: 2 }] });
    data.deliveries.rows = [{ id: "status-delivery", order_id: "status-order", order_number: "D-101",
        party_id: "driver", party_type: "store_driver", party_name: "مندوب تجريبي",
        recognition_basis: basis, recorded_at: "2026-11-01T07:00:00Z", delivered_at: null,
        shipping_confirmed: "10.00", shipping_settled: "0.00", shipping_outstanding: "10.00",
        collection_status: "not_confirmed", driver_custody: null, store_collection: null }];
    const before = JSON.stringify(payload);
    const workbook = buildFinancialPositionWorkbook(payload, "SAR");
    const rows = workbook.getWorksheet("تفاصيل الطلبات").getSheetValues().filter(Boolean);
    const header = rows.find(values => values[1] === "رقم الطلب");
    const order = rows.find(values => values[1] === "D-101");
    expect(order[header.indexOf("تاريخ التسليم — الرياض")]).toBe("غير متاح");
    expect(order[header.indexOf("وقت التسجيل — الرياض")]).toContain("10:00");
    expect(order[header.indexOf("أساس احتساب أجرة الشحن")]).toBe(label);
    expect(order[header.indexOf("أجرة الشحن المؤكدة")]).toBe("10.00");
    expect(order[header.indexOf("حالة التحصيل")]).toBe("التحصيل غير مؤكد");
    expect(order[header.indexOf("عهدة المندوب")]).toBe("غير متاح");
    expect(order[header.indexOf("تحصيل المتجر")]).toBe("غير متاح");
    const driverRows = workbook.worksheets.find(sheet => sheet.name.includes("مناديب المتجر")).getSheetValues().filter(Boolean);
    const driverHeader = driverRows.find(values => values[1] === "المندوب");
    const driver = driverRows.find(values => values[1] === "مندوب تجريبي");
    expect(driver[driverHeader.indexOf("تواصيل مثبتة")]).toBe("3");
    expect(driver[driverHeader.indexOf("تواصيل محتسبة")]).toBe("1");
    expect(driver[driverHeader.indexOf("طلبات غير مؤكدة")]).toBe("2");
    expect(driver[driverHeader.indexOf("علينا")]).toBe("10.00");
    expect(JSON.stringify(payload)).toBe(before);
});

test("export refuses inactive, unauthorized, foreign-currency and invalid contracts", () => {
    const payload = report();
    ["draft", "frozen", "accounting_active"].forEach(status => expect(() => buildFinancialPositionWorkbook({ ...payload, status }, "SAR")).toThrow("report_unavailable"));
    expect(() => buildFinancialPositionWorkbook({ ...payload, permissions: { reports: false } }, "SAR")).toThrow("report_unavailable");
    expect(() => buildFinancialPositionWorkbook(payload, "EUR")).toThrow("report_unavailable");
    expect(() => buildFinancialPositionWorkbook({ ...payload, authority: "another_source" }, "SAR")).toThrow("report_unavailable");
});

test("unproven order number uses a separately labeled identifier and exports separate columns", () => {
    const payload = report();
    const row = payload.currencies.SAR.deliveries.rows[0];
    row.order_number = null; row.order_id = "opaque-order-reference";
    expect(deliveryOrderLabel(row)).toBe("معرف الطلب: opaque-order-reference");
    expect(filterDeliveries(payload.currencies.SAR.deliveries.rows, { order: "opaque-order" })).toEqual([row]);
    const workbook = buildFinancialPositionWorkbook(payload, "SAR", { order: "opaque-order" });
    const rows = workbook.getWorksheet("تفاصيل الطلبات").getSheetValues().filter(Boolean);
    const header = rows.find(values => values[1] === "رقم الطلب");
    const exported = rows.find(values => values[2] === "opaque-order-reference");
    expect(header[2]).toBe("معرف الطلب");
    expect(exported[1]).toBe("غير متاح");
});

test("real XLSX bytes preserve Arabic sheets, exact money text, currency and filtered orders after reload", async () => {
    const workbook = buildFinancialPositionWorkbook(report(), "SAR", { payment: "cash" });
    const bytes = await workbook.xlsx.writeBuffer();
    expect(bytes.byteLength).toBeGreaterThan(0);
    const restored = new ExcelJS.Workbook();
    await restored.xlsx.load(bytes);

    expect(restored.worksheets.map(sheet => sheet.name)).toEqual([
        "معلومات التقرير", "1 الموردون", "الإجماليات والمركز", "تفاصيل الطلبات",
    ]);
    expect(restored.getWorksheet("معلومات التقرير").getCell("B2").value).toBe("SAR");
    const supplier = restored.getWorksheet("1 الموردون");
    const supplierValues = supplier.getSheetValues().flat();
    expect(supplierValues).toContain("المورد");
    expect(supplierValues).toContain("علينا");
    expect(supplierValues).toContain("9007199254740993.25");
    expect(supplierValues).toContain("=1+1");
    supplier.eachRow(row => row.eachCell(cell => {
        if (cell.value === "9007199254740993.25" || cell.value === "=1+1") expect(typeof cell.value).toBe("string");
    }));
    restored.worksheets.forEach(sheet => sheet.eachRow(row => row.eachCell(cell => expect(cell.formula).toBeUndefined())));

    const details = restored.getWorksheet("تفاصيل الطلبات").getSheetValues().flat();
    expect(details).toContain("رقم الطلب");
    expect(details).toContain("معرف الطلب");
    expect(details).toContain("1001");
    expect(details).toContain("55.02");
    expect(details).toContain("تحصيل مؤكد");
    expect(details).not.toContain("1002");
    expect(details).not.toContain("71.09");
    const allValues = restored.worksheets.flatMap(sheet => sheet.getSheetValues().flat());
    expect(allValues).not.toContain("EXCLUDED-CURRENCY");
    expect(allValues).not.toContain("USD");
    expect(allValues).not.toContain("9007199254740992");
});
