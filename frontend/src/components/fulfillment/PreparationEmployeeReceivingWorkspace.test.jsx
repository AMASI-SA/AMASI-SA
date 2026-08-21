import fs from "fs";
import path from "path";
import { renderToStaticMarkup } from "react-dom/server";

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

jest.mock("../../services/preparationWorkService", () => ({
    newPreparationReceiptRequestId: () => "preparation-receipt:test-1",
    receivePreparationPiece: jest.fn(),
    searchPreparationReceipt: jest.fn(),
}));

import PreparationEmployeeReceivingWorkspace, {
    ProductCard,
} from "./PreparationEmployeeReceivingWorkspace";

test("receiving page scans one piece and never presents the full order", () => {
    const markup = renderToStaticMarkup(<PreparationEmployeeReceivingWorkspace />);

    expect(markup).toContain("استلام قطعة بقطعة");
    expect(markup).toContain("تصوير الباركود يستلم قطعة واحدة فورًا");
    expect(markup).toContain("أدخل باركود القطعة");
    expect(markup).toContain('placeholder="باركود قطعة أماسي"');
    expect(markup).toContain('aria-label="فتح الكاميرا لتصوير باركود المنتج"');
    expect(markup).toContain("مستلمات الجلسة");
    expect(markup).toContain("كل باركود = قطعة واحدة");
    expect(markup).toContain("لن تظهر بقية منتجات الطلب هنا");
    expect(markup).not.toContain("عرض منتجات الطلب");
});

test("session product card is a received piece without a manual receive action", () => {
    const markup = renderToStaticMarkup(
        <ProductCard
            piece={{
                piece_id: "piece-1",
                order_number: "10452",
                unit_index: 1,
                product_name: "ميدالية باسم",
                sku: "MED-1",
                image_url: "https://cdn.example.com/product.jpg",
                responsible_employee_name: "عرفات",
                status: "ready_for_assembly",
                can_receive: false,
                specifications: [
                    { name: "الاسم", value: "سارة" },
                    { name: "اللون", value: "ذهبي" },
                ],
            }}
            busy={false}
            onReceive={() => {}}
        />,
    );

    expect(markup).toContain("الطلب #10452");
    expect(markup).toContain("ميدالية باسم");
    expect(markup).toContain("عرفات");
    expect(markup).toContain("سارة");
    expect(markup).toContain("ذهبي");
    expect(markup).toContain("تم الاستلام");
    expect(markup).not.toContain("استلام المنتج جاهز");
    expect(markup).toContain('src="https://cdn.example.com/product.jpg"');
});

test("barcode flow searches the exact piece then receives it once", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "PreparationEmployeeReceivingWorkspace.jsx"),
        "utf8",
    );

    expect(source).toContain("const matchedPieceId = String(searchResult.matched_piece_id");
    expect(source).toContain("const response = await receivePreparationPiece(");
    expect(source).toContain("receivedThisSession.current.add(matchedPieceId)");
    expect(source).toContain("current.filter((piece) => piece.piece_id !== matchedPieceId)");
    expect(source).toContain("preparation_piece_services_incomplete");
    expect(source).toContain("pending_service_names");
    expect(source).toContain("الخدمات غير المنجزة");
    expect(source).toContain("customer_service_instruction_action_required");
    expect(source).toContain("preparation-receiving-customer-service-gate");
    expect(source).not.toContain("setResult(data)");
    expect(source).not.toContain("searchPreparationReceipt(result.order_number)");
});
