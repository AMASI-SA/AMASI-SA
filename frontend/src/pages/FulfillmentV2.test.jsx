import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

let mockSearchParams = new URLSearchParams("stage=pending_review");
let mockUser = {
    id: "owner-1",
    role: "owner",
    is_owner: true,
    permissions: [],
};
const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
}));

jest.mock("../context/AuthContext", () => ({
    useAuth: () => ({ user: mockUser }),
}));

jest.mock("../components/PermissionRoute", () => ({
    userHasPermission: (user, permission) => Boolean(
        user?.is_owner === true
        || (Array.isArray(user?.permissions) && user.permissions.includes(permission))
    ),
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

jest.mock("../components/fulfillment/PreparationEmployeeReceivingWorkspace", () => function PreparationEmployeeReceivingFixture() {
    return <div data-testid="preparation-employee-receiving-workspace">بحث برقم الطلب · فتح الكاميرا · استلام المنتج جاهز</div>;
});

jest.mock("../components/fulfillment/PreparationWorkDashboard", () => function PreparationWorkDashboardFixture({ initialView, standalone }) {
    return <div data-testid="preparation-work-dashboard">{standalone && initialView === "my-products" ? "إدارة منتجاتي المستقلة" : "تفاصيل قيد التنفيذ وإدارة الموظفين"}</div>;
});

jest.mock("../components/fulfillment/ReadyToShipOrders", () => function ReadyToShipFixture() {
    return <div data-testid="ready-to-ship-window">جاهز للشحن</div>;
});

jest.mock("../components/fulfillment/CompletedFulfillmentOrders", () => function CompletedFulfillmentFixture() {
    return <div data-testid="completed-fulfillment-orders">تم التنفيذ · بوليصة الشحن</div>;
});

jest.mock("../components/fulfillment/DeliveryTrackingOrders", () => function DeliveryTrackingFixture({ stage }) {
    return <div data-testid={`delivery-tracking-${stage}`}>{stage === "delivered" ? "تم التوصيل" : "جاري التوصيل"} · مزامنة صفحة الطلبات في ميزان</div>;
});

jest.mock("../components/fulfillment/StoreCourierDispatchWorkspace", () => function StoreCourierDispatchFixture() {
    return <div data-testid="store-courier-dispatch-workspace">اختيار الموصل ثم تصوير الشحنة لإسنادها</div>;
});

jest.mock("../components/fulfillment/StoreCourierMyShipments", () => function StoreCourierMyShipmentsFixture({ stage }) {
    return <div data-testid={`store-courier-my-shipments-${stage}`}>شحنات مندوب المتجر · {stage}</div>;
});

jest.mock("../components/fulfillment/SupplierReceivingWorkspace", () => function SupplierReceivingFixture() {
    return <div data-testid="supplier-receiving-workspace">استلام منتجات المورد بالباركود · من المستودع · من المورد · تصنيع داخلي · ينتظر توريد · قيد التجميع · متوقف بسبب نقص منتج</div>;
});

import FulfillmentV2, { FULFILLMENT_NAVIGATION_ITEMS, FULFILLMENT_STAGES } from "./FulfillmentV2";

const EXPECTED_STAGE_KEYS = [
    "pending_review",
    "reviewed",
    "in_progress",
    "preparation",
    "assembly",
    "ready_to_ship",
    "completed",
    "courier_dispatch",
    "delivering",
    "delivered",
];

beforeAll(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});

afterAll(() => {
    delete globalThis.IS_REACT_ACT_ENVIRONMENT;
});

beforeEach(() => {
    mockSearchParams = new URLSearchParams("stage=pending_review");
    mockUser = {
        id: "owner-1",
        role: "owner",
        is_owner: true,
        permissions: [],
    };
    mockSetSearchParams.mockClear();
});

test("fulfillment workspace keeps the governed stage order", () => {
    expect(FULFILLMENT_STAGES.map((stage) => stage.key)).toEqual(EXPECTED_STAGE_KEYS);
    expect(new Set(FULFILLMENT_STAGES.map((stage) => stage.key)).size).toBe(FULFILLMENT_STAGES.length);
    expect(FULFILLMENT_STAGES.map((stage) => stage.label)).toEqual([
        "بانتظار المراجعة",
        "تم المراجعة",
        "قيد التجهيز",
        "استلام المورد",
        "الاستلام من التجهيز",
        "التجميع والعنونة",
        "تم التنفيذ",
        "إدارة الموصلين",
        "جاري التوصيل",
        "تم التوصيل",
    ]);
});

test("my products is a navigation stop immediately before supplier receiving", () => {
    expect(FULFILLMENT_NAVIGATION_ITEMS.map((item) => item.key)).toEqual([
        "pending_review",
        "reviewed",
        "in_progress",
        "my_products",
        "preparation",
        "assembly",
        "ready_to_ship",
        "completed",
        "courier_dispatch",
        "delivering",
        "delivered",
    ]);
    expect(FULFILLMENT_NAVIGATION_ITEMS[3]).toMatchObject({
        label: "إدارة منتجاتي",
        workspace: "my-products",
    });
});

test("preparation is the Mezan OS parent with eleven nested navigation tabs", () => {
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain("إدارة التجهيز");
    expect(markup).not.toContain("إدارة رفع الطلبات");
    expect(markup).toContain("تبويبات إدارة التجهيز");
    expect(markup.match(/data-testid="fulfillment-stage-tab-/g) || []).toHaveLength(11);
    EXPECTED_STAGE_KEYS.forEach((stageKey) => {
        expect(markup).toContain(`data-testid="fulfillment-stage-tab-${stageKey}"`);
    });
    expect(markup).toContain('data-testid="fulfillment-stage-tab-my_products"');
    expect(markup.indexOf('data-testid="fulfillment-stage-tab-my_products"')).toBeLessThan(
        markup.indexOf('data-testid="fulfillment-stage-tab-preparation"'),
    );
    expect(markup.indexOf('data-testid="fulfillment-stage-tab-completed"')).toBeLessThan(
        markup.indexOf('data-testid="fulfillment-stage-tab-courier_dispatch"'),
    );
    expect(markup.indexOf('data-testid="fulfillment-stage-tab-courier_dispatch"')).toBeLessThan(
        markup.indexOf('data-testid="fulfillment-stage-tab-delivering"'),
    );
});

test("opening fulfillment without a stage starts at pending review", () => {
    mockSearchParams = new URLSearchParams("");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="pending-review-queue"');
    expect(markup).toContain("قائمة انتظار المراجعة");
    expect(markup).toContain("تبويبات إدارة التجهيز");
    expect(markup).toContain("Mezan OS V2");
    expect(markup).not.toContain("إدارة منتجاتي المستقلة");
});

test("my products workspace is standalone and separate from fulfillment stages", () => {
    mockSearchParams = new URLSearchParams("workspace=my-products");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="preparation-work-dashboard"');
    expect(markup).toContain("إدارة منتجاتي المستقلة");
    expect(markup).not.toContain("تبويبات إدارة التجهيز");
    expect(markup).not.toContain("Mezan OS V2");
    expect(markup).not.toContain('data-testid="pending-review-queue"');
});

test("clicking my products opens its standalone workspace route", () => {
    const container = document.createElement("div");
    const root = createRoot(container);

    act(() => root.render(<FulfillmentV2 />));
    const myProductsLink = container.querySelector('[data-testid="fulfillment-stage-tab-my_products"]');
    expect(myProductsLink).not.toBeNull();

    act(() => myProductsLink.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    const [nextSearchParams, options] = mockSetSearchParams.mock.calls.at(-1);
    expect(nextSearchParams.toString()).toBe("workspace=my-products");
    expect(options).toEqual({ replace: true });
    act(() => root.unmount());
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
    expect(markup).toContain("تفاصيل قيد التنفيذ وإدارة الموظفين");
    expect(markup).toContain("تبويبات إدارة التجهيز");
    expect(markup).toContain("Mezan OS V2");
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

test("assembly stage opens the simple employee receiving workspace", () => {
    mockSearchParams = new URLSearchParams("stage=assembly");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="preparation-employee-receiving-workspace"');
    expect(markup).toContain("بحث برقم الطلب");
    expect(markup).toContain("فتح الكاميرا");
    expect(markup).toContain("استلام المنتج جاهز");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
});

test("completed stage keeps the carrier label action available", () => {
    mockSearchParams = new URLSearchParams("stage=completed");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="completed-fulfillment-orders"');
    expect(markup).toContain("تم التنفيذ");
    expect(markup).toContain("بوليصة الشحن");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
});

test("courier dispatch is a governed stage after completed for managers", () => {
    mockSearchParams = new URLSearchParams("stage=courier_dispatch");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="store-courier-dispatch-workspace"');
    expect(markup).toContain("اختيار الموصل ثم تصوير الشحنة لإسنادها");
    expect(markup).toContain("إدارة الموصلين");
    expect(markup).not.toContain('data-testid="store-courier-my-shipments-waiting"');
});

test("delivering stage renders the external carrier tracking board for managers", () => {
    mockSearchParams = new URLSearchParams("stage=delivering");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="delivery-tracking-delivering"');
    expect(markup).toContain("جاري التوصيل");
    expect(markup).toContain("مزامنة صفحة الطلبات في ميزان");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
});

test("delivered stage renders the completed external carrier board for managers", () => {
    mockSearchParams = new URLSearchParams("stage=delivered");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="delivery-tracking-delivered"');
    expect(markup).toContain("تم التوصيل");
    expect(markup).not.toContain("هذه المرحلة مثبتة ضمن المسار، ولم نفعّل عملياتها بعد");
});

test("store courier sees only assignment pickup delivering and delivered tabs", () => {
    mockUser = {
        id: "courier-1",
        role: "viewer",
        is_owner: false,
        permissions: ["fulfillment.store_courier.deliver"],
    };
    mockSearchParams = new URLSearchParams("stage=courier_dispatch");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="store-courier-my-shipments-waiting"');
    expect(markup).toContain("توصيل مندوب المتجر");
    expect(markup.match(/data-testid="fulfillment-stage-tab-/g) || []).toHaveLength(3);
    expect(markup).toContain('data-testid="fulfillment-stage-tab-courier_dispatch"');
    expect(markup).toContain('data-testid="fulfillment-stage-tab-delivering"');
    expect(markup).toContain('data-testid="fulfillment-stage-tab-delivered"');
    expect(markup).not.toContain('data-testid="fulfillment-stage-tab-pending_review"');
    expect(markup).not.toContain('data-testid="fulfillment-stage-tab-my_products"');
    expect(markup).not.toContain('data-testid="store-courier-dispatch-workspace"');
});

test("store courier delivering tab shows only the courier own shipment queue", () => {
    mockUser = {
        id: "courier-1",
        role: "viewer",
        is_owner: false,
        permissions: ["fulfillment.store_courier.deliver"],
    };
    mockSearchParams = new URLSearchParams("stage=delivering");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="store-courier-my-shipments-delivering"');
    expect(markup).not.toContain('data-testid="delivery-tracking-delivering"');
});

test("store courier delivered tab shows only the courier completed history", () => {
    mockUser = {
        id: "courier-1",
        role: "viewer",
        is_owner: false,
        permissions: ["fulfillment.store_courier.deliver"],
    };
    mockSearchParams = new URLSearchParams("stage=delivered");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="store-courier-my-shipments-delivered"');
    expect(markup).not.toContain('data-testid="delivery-tracking-delivered"');
});

test("store courier is forced back to its assignment queue from unrelated stages", () => {
    mockUser = {
        id: "courier-1",
        role: "viewer",
        is_owner: false,
        permissions: ["fulfillment.store_courier.deliver"],
    };
    mockSearchParams = new URLSearchParams("stage=pending_review");
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain('data-testid="store-courier-my-shipments-waiting"');
    expect(markup).not.toContain('data-testid="pending-review-queue"');
});
