import { renderToStaticMarkup } from "react-dom/server";

let mockSearchParams = new URLSearchParams("stage=pending_review");
const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
}));

jest.mock("./OrderReview", () => function PendingOrderReviewFixture() {
    return <div data-testid="pending-review-queue">قائمة انتظار المراجعة</div>;
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
        "انتظار المراجعة",
        "تم المراجعة",
        "قيد التنفيذ",
        "إدارة التجهيز",
        "الاستلام والتجميع",
        "جاهز للشحن",
        "تم التنفيذ",
        "جاري التوصيل",
        "تم التوصيل",
    ]);
});

test("pending review is embedded inside the new Mezan OS V2 workspace", () => {
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain("إدارة رفع الطلبات");
    expect(markup).toContain("قائمة انتظار المراجعة");
    expect(markup).toContain("المرحلة الحالية");
    expect(markup).toContain("انتظار المراجعة");
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

    expect(markup).not.toContain("قائمة انتظار المراجعة");
});
