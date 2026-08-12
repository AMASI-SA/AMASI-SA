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

test("mobile receiving starts with one obvious search and camera action", () => {
    const markup = renderToStaticMarkup(<PreparationEmployeeReceivingWorkspace />);

    expect(markup).toContain("الاستلام من التجهيز");
    expect(markup).toContain("ابحث برقم الطلب");
    expect(markup).toContain('placeholder="مثال: 10452"');
    expect(markup).toContain('aria-label="فتح الكاميرا لتصوير باركود المنتج"');
    expect(markup).toContain("قيد التجهيز");
    expect(markup).toContain("التجميع والعنونة");
    expect(markup).toContain("لا توجد مرحلة تغليف مستقلة");
});

test("searched product card shows image customer choices employee and ready button", () => {
    const markup = renderToStaticMarkup(
        <ProductCard
            piece={{
                piece_id: "piece-1",
                unit_index: 1,
                product_name: "ميدالية باسم",
                sku: "MED-1",
                image_url: "https://cdn.example.com/product.jpg",
                responsible_employee_name: "عرفات",
                search_match: true,
                can_receive: true,
                specifications: [
                    { name: "الاسم", value: "سارة" },
                    { name: "اللون", value: "ذهبي" },
                ],
            }}
            busy={false}
            onReceive={() => {}}
        />,
    );

    expect(markup).toContain("هذا هو المنتج الذي تم تصويره أو البحث عنه");
    expect(markup).toContain("ميدالية باسم");
    expect(markup).toContain("عرفات");
    expect(markup).toContain("سارة");
    expect(markup).toContain("ذهبي");
    expect(markup).toContain("استلام المنتج جاهز");
    expect(markup).toContain('src="https://cdn.example.com/product.jpg"');
});
