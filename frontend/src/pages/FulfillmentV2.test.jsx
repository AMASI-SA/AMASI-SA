import { renderToStaticMarkup } from "react-dom/server";

let mockSearchParams = new URLSearchParams("stage=pending_review");
const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
}));

jest.mock("./OrderReview", () => function PendingOrderReviewFixture() {
    return <div data-testid="pending-review-queue">قائمة انتظار المراجعة</div>;
});

jest.mock("./ReviewedOrders", () => function ReviewedOrdersFixture() {
    return <div data-testid="reviewed-products-window">منتجات تمت مراجعتها</div>;
});

jest.mock("../components/fulfillment/PreparationFilesRegistry", () => function PreparationFilesRegistryFixture() {
    return <div data-testid="preparation-files-registry-window">سجل ملفات التجهيز المستقل</div>;
});

jest.mock("../components/fulfillment/PreparationWorkDashboard", () => function PreparationWorkDashboardFixture() {
    return <div data-testid="preparation-work-dashboard">منتجاتي وإدارة منتجات الموظفين</div>;
});

jest.mock("../components/fulfillment/ReadyToShipOrders", () => function ReadyToShipFixture() {
    return <div data-testid="ready-to-ship-window">جاهز للشحن</div>;
});

jest.mock("../components/fulfillment/SupplierReceivingWorkspace", () => function SupplierReceivingFixture() {
    return <div data-testid="supplier-receiving-workspace">استلام منتجات المورد بالباركود · من المستودع · من المورد · تصنيع داخلي · ينتظر توريد · قيد التجميع · متوقف بسبب نقص منتج</div>;
});

import FulfillmentV2, { FULFILLMENT_STAGES } from "./FulfillmentV2";

const EXPECTED_STAGE_KEYS = [
    "pending_review",
    "reviewed",
    "in_progress",
    "preparation",
    "assembly",
    "ready_to_ship",
    "completed",
    "delivering",
    "delivered",
];

beforeEach(() => {
    mockSearchParams = new URLSearchParams("stage=pending_review");
    mockSetSearchParams.mockClear();
});

test("fulfillment workspace keeps the governed stage order", () => {
    expect(FULFILLMENT_STAGES.map((stage) => stage.key)).toEqual(EXPECTED_STAGE_KEYS);
    expect(new Set(FULFILLMENT_STAGES.map((stage) => stage.key)).size).toBe(FULFILLMENT_STAGES.length);
    expect(FULFILLMENT_STAGES.map((stage) => stage.label)).toEqual([
        "بانتظار المراجعة",
        "تم المراجعة",
        "قيد التنفيذ",
        "التجهيز",
        "الاستلام والتجميع",
        "جاهز للشحن",
        "تم التنفيذ",
        "جاري التوصيل",
        "تم التوصيل",
    ]);
});

test("preparation is the Mezan OS parent with nine nested stage tabs", () => {
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain("إدارة التجهيز");
    expect(markup).not.toContain("إدارة رفع الطلبات");
    expect(markup).toContain("تبويبات إدارة التجهيز");
    expect(markup.match(/data-testid="fulfillment-stage-tab-/g) || []).toHaveLength(9);
    EXPECTED_STAGE_KEYS.forEach((stageKey) => {
        expect(markup).toContain(`data-testid="fulfillment-stage-tab-${stageKey}"`);
    });
});

test("opening fulfillment without a stage renders the compact mobile work overview", () => {
    mockSearchParams = new URLSearchParams("");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="fulfillment-mobile-overview"');
    expect(markup).toContain("ملخص العمل اليوم");
    expect(markup).toContain("المهام السريعة");
    expect(markup).toContain("مراجعة الطلبات");
    expect(markup).toContain("ملفات التجهيز");
    expect(markup).toContain("استلام المورد");
});

test("pending review is embedded under the organized preparation tabs", () => {
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain("قائمة انتظار المراجعة");
    expect(markup).toContain("النافذة الحالية");
    expect(markup).toContain("بانتظار المراجعة");
});

test("reviewed products window does not render the preparation files registry", () => {
    mockSearchParams = new URLSearchParams("stage=reviewed&view=products");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="reviewed-products-window"');
    expect(markup).toContain("منتجات تمت مراجعتها");
    expect(markup).not.toContain('data-testid="preparation-files-registry-window"');
    expect(markup).not.toContain("سجل ملفات التجهيز المستقل");
});

test("preparation files window does not render reviewed products", () => {
    mockSearchParams = new URLSearchParams("stage=reviewed&view=files");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="preparation-files-registry-window"');
    expect(markup).toContain("سجل ملفات التجهيز المستقل");
    expect(markup).toContain("النافذة الحالية");
    expect(markup).not.toContain('data-testid="reviewed-products-window"');
    expect(markup).not.toContain("منتجات تمت مراجعتها");
});

test("in progress stage renders the employee work dashboard instead of a placeholder", () => {
    mockSearchParams = new URLSearchParams("stage=in_progress");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="preparation-work-dashboard"');
    expect(markup).toContain("منتجاتي وإدارة منتجات الموظفين");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
});

test("preparation stage exposes warehouse supplier manufacturing and shortage tracks", () => {
    mockSearchParams = new URLSearchParams("stage=preparation");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    [
        "من المستودع",
        "من المورد",
        "تصنيع داخلي",
        "ينتظر توريد",
        "قيد التجميع",
        "متوقف بسبب نقص منتج",
    ].forEach((track) => expect(markup).toContain(track));

    expect(markup).toContain('data-testid="supplier-receiving-workspace"');
    expect(markup).toContain("استلام منتجات المورد بالباركود");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
    expect(markup).not.toContain("قائمة انتظار المراجعة");
});
