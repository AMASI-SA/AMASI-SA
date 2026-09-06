import { act } from "react";
import { createRoot } from "react-dom/client";

import { GoalCard } from "./CampaignRecommendations";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: { get: jest.fn(), put: jest.fn(), post: jest.fn() },
}));

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => <span>{children}</span>,
}));

jest.mock("lucide-react", () => {
    const Icon = () => <span />;
    return {
        AlertTriangle: Icon,
        ArrowRight: Icon,
        BarChart3: Icon,
        Boxes: Icon,
        Clock3: Icon,
        Filter: Icon,
        Image: Icon,
        RefreshCw: Icon,
        ShieldCheck: Icon,
        ShoppingCart: Icon,
        Sparkles: Icon,
        TrendingUp: Icon,
        Wrench: Icon,
    };
});

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function renderGoal(goal) {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    act(() => {
        root.render(
            <GoalCard
                goal={goal}
                goalInput="100000"
                setGoalInput={() => {}}
                saving={false}
                onSave={() => {}}
            />,
        );
    });
    return {
        text: container.textContent,
        cleanup: () => act(() => {
            root.unmount();
            container.remove();
        }),
    };
}

test("renders a real zero as progress and explains incomplete quality", () => {
    const view = renderGoal({
        minimum_net_profit_sar: 100000,
        progress_available: true,
        progress_state: "quality_incomplete",
        status: "profit_accounting_incomplete",
        net_profit_to_date_sar: 0,
        remaining_to_target_sar: 100000,
        days_remaining: 23,
        required_daily_net_profit_sar: 4347.83,
        projected_month_end_net_profit_sar: 0,
    });
    expect(view.text).toContain("0.00 ر.س");
    expect(view.text).toContain("جودة بيانات الربح غير مكتملة");
    view.cleanup();
});

test("does not invent a target or turn a failed read into zero", () => {
    const view = renderGoal({
        minimum_net_profit_sar: null,
        progress_available: false,
        progress_state: "calculation_failed",
        status: "goal_context_unavailable",
    });
    expect(view.text).toContain("تعذر حساب تقدم الهدف");
    expect(view.text).toContain("بانتظار الحساب");
    expect(view.text).not.toContain("100,000.00 ر.س");
    expect(view.text).not.toContain("0.00 ر.س");
    view.cleanup();
});

test("shows the validated evidence period, age, watermark, and accounting quality", () => {
    const view = renderGoal({
        minimum_net_profit_sar: 100000,
        progress_available: true,
        progress_state: "quality_incomplete",
        status: "profit_accounting_incomplete",
        net_profit_to_date_sar: -500,
        remaining_to_target_sar: 100500,
        days_remaining: 23,
        required_daily_net_profit_sar: 4369.57,
        projected_month_end_net_profit_sar: -2214.29,
        profit_accounting_quality_known: true,
        profit_accounting_complete: false,
        evidence: {
            valid: true,
            freshness_status: "fresh",
            age_seconds: 7200,
            max_age_seconds: 21600,
            calculated_at: "2026-09-07T08:00:00+00:00",
            next_run_at: "2026-09-07T13:00:00+00:00",
            data_through: null,
            data_through_status: "source_watermark_unavailable",
            accounting_quality: "incomplete",
            period: {
                kind: "calendar_month_to_date",
                from: "2026-09-01",
                to: "2026-09-07",
                timezone: "Asia/Riyadh",
            },
        },
    });
    expect(view.text).toContain("فترة دليل الربح");
    expect(view.text).toContain("2026-09-01 → 2026-09-07");
    expect(view.text).toContain("عمر الدليل: ساعتان");
    expect(view.text).toContain("تغطية المصدر: غير معروفة");
    expect(view.text).toContain("جودة المحاسبة: غير مكتملة");
    expect(view.text).toContain("-500.00 ر.س");
    view.cleanup();
});
