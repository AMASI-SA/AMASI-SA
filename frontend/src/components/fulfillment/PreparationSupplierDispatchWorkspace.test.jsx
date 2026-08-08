import { renderToStaticMarkup } from "react-dom/server";

import PreparationSupplierDispatchWorkspace, {
    dispatchSelections,
    MyProductsOverview,
    WaitingReviewView,
} from "./PreparationSupplierDispatchWorkspace";


test("dispatch selection clamps quantities to the employee available pieces", () => {
    expect(dispatchSelections(
        [
            { group_key: "product:1", available_quantity: 3 },
            { group_key: "product:2", available_quantity: 1 },
        ],
        { "product:1": 9, "product:2": 0 },
    )).toEqual([
        { group_key: "product:1", quantity: 3 },
    ]);
});


test("my products is the default employee in-progress window", () => {
    const markup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace />,
    );

    expect(markup).toContain("جارٍ تحميل إدارة منتجاتي");
});


test("manager unassigned queue remains independent from employee products", () => {
    const employeeMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="my-products" />,
    );
    const managerMarkup = renderToStaticMarkup(
        <PreparationSupplierDispatchWorkspace view="unassigned" />,
    );

    expect(employeeMarkup).toContain("جارٍ تحميل إدارة منتجاتي");
    expect(managerMarkup).toContain("جارٍ تحميل المنتجات غير المسندة");
});


test("my products overview uses the approved four account-wide counters", () => {
    const markup = renderToStaticMarkup(<MyProductsOverview data={{
        summary: {
            waiting_review_products: 21,
            in_progress_products: 74,
            received_orders_awaiting_branch_handoff: 33,
            total_assigned_pieces: 128,
        },
        files: [{
            file_number: "PF-024",
            file_title: "ملف أحمد 024",
            piece_count: 24,
            received_quantity: 9,
            sent_quantity: 4,
            ready_quantity: 0,
            products: [],
        }],
        supplier_accounts: [{
            supplier_id: "supplier-1",
            supplier_name: "مؤسسة النور",
            sent_quantity: 32,
            ready_quantity: 0,
            received_quantity: 0,
            dispatches: [{ id: "invoice-1" }],
        }],
    }} onOpen={() => {}} />);

    expect(markup).toContain("بانتظار المراجعة");
    expect(markup).toContain("قيد التنفيذ");
    expect(markup).toContain("تم الاستلام");
    expect(markup).toContain("إجمالي القطع المسندة");
    ["21", "74", "33", "128"].forEach((value) => expect(markup).toContain(`>${value}<`));
    expect(markup).toContain("ملخص العمل العام");
    expect(markup).not.toContain("ملخص العمل اليوم");
    expect(markup).toContain("إدارة المنتجات المسندة لك ومتابعة الموردين");
    expect(markup).toContain("استلام من المورد");
    expect(markup).toContain("البحث برقم الطلب");
    expect(markup).toContain("فواتير الموردين");
    expect(markup).toContain("آخر ملفات التجهيز");
    expect(markup).toContain("حالة الموردين");
    expect(markup).toContain("ملف أحمد 024");
    expect(markup).toContain("مؤسسة النور");
    expect(markup).toContain("grid-cols-3");
});


test("waiting review renders two product cards per mobile row and mandatory return action", () => {
    const markup = renderToStaticMarkup(<WaitingReviewView
        data={{
            summary: {},
            suppliers: [{ id: "supplier-1", company_name: "مورد النقش" }],
            files: [{
                file_number: "PF-100",
                file_title: "ملف أحمد",
                available_quantity: 2,
                sent_quantity: 0,
                products: [{
                    group_key: "product:1",
                    product_name: "سلسال الاسم",
                    available_quantity: 2,
                    services: [{ service_id: "engrave", service_name: "نحت", status: "pending" }],
                }],
            }],
        }}
        loading={false}
        error=""
        onRefresh={() => {}}
        onChanged={async () => {}}
        onBack={() => {}}
    />);

    expect(markup).toContain("grid-cols-2");
    expect(markup).toContain("سلسال الاسم");
    expect(markup).toContain("إرجاع الإسناد");
    expect(markup).toContain("حفظ وطباعة ملف المورد");
});
