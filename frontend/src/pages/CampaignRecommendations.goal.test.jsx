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
