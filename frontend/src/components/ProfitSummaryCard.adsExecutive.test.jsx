import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

import AdsExecutiveBreakdownTable, {
    buildAdsExecutiveMetricRows,
} from "./AdsExecutiveBreakdownTable";

const data = {
    providers: {
        snapchat: {
            spend_sar: 375.44,
            salla_sales_sar: 550,
            salla_orders: 2,
            platform_cost_per_order_sar: 37.54,
            actual_roas: 1.46,
        },
        tiktok: {
            spend_sar: 0,
            salla_sales_sar: 0,
            salla_orders: 0,
            platform_cost_per_order_sar: null,
            actual_roas: null,
        },
        meta: {
            spend_sar: 200,
            salla_sales_sar: 400,
            salla_orders: 1,
            platform_cost_per_order_sar: 50,
            actual_roas: 2,
        },
        google: {
            spend_sar: 0,
            salla_sales_sar: 0,
            salla_orders: 0,
            platform_cost_per_order_sar: null,
            actual_roas: null,
        },
    },
    total: {
        spend_sar: 575.44,
        salla_sales_sar: 950,
        salla_orders: 3,
        platform_cost_per_order_sar: 41.1,
        actual_roas: 1.65,
    },
    coverage: { salla_unattributed_orders: 1 },
};

test("keeps Salla outcomes separate from platform CPA", () => {
    const rows = buildAdsExecutiveMetricRows(data);

    expect(rows.find((row) => row.key === "orders")).toMatchObject({
        source: "سلة",
        total: 3,
    });
    expect(rows.find((row) => row.key === "sales")).toMatchObject({
        source: "سلة",
        total: 950,
    });
    expect(rows.find((row) => row.key === "cost_per_order")).toMatchObject({
        source: "المنصة الإعلانية",
        total: 41.1,
    });
});

test("renders all approved metric rows", () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => root.render(<AdsExecutiveBreakdownTable data={data} />));

    expect(
        container.querySelector('[data-testid="ads-executive-breakdown-table"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("متوسط تكلفة الطلب");
    expect(container.textContent).toContain("المبيعات / العائد");
    expect(container.textContent).toContain("مبيعات سلة ÷ صرف المنصة");

    act(() => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
});
