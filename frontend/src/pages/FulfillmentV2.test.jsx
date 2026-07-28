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
    expect(markup).toContain("9 مراحل");
    EXPECTED_STAGE_KEYS.forEach((stageKey) => {
        expect(markup).toContain(`data-testid="fulfillment-stage-tab-${stageKey}"`);
    });
});

test("pending review is embedded under the organized preparation tabs", () => {
    const markup = renderToStaticMarkup(<FulfillmentV2 />);

    expect(markup).toContain("قائمة انتظار المراجعة");
    expect(markup).toContain("المرحلة الحالية");
    expect(markup).toContain("بانتظار المراجعة");
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
