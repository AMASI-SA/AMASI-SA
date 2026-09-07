import { act } from "react";
import { createRoot } from "react-dom/client";

import api from "../lib/api";
import CampaignRecommendations from "./CampaignRecommendations";

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

const goalConfig = {
    minimum_net_profit_sar: 100000,
    configured: true,
    source: "owner_configured",
    updated_at: "2026-09-07T07:00:00+00:00",
    goal_config_id: "goal-1",
};

function displayedGoal(overrides = {}) {
    return {
        ...goalConfig,
        contract_version: "campaign_ai_monthly_profit_goal_v2",
        month: "2026-09",
        progress_available: true,
        progress_state: "quality_incomplete",
        status: "profit_accounting_incomplete",
        net_profit_to_date_sar: 40000,
        remaining_to_target_sar: 60000,
        days_remaining: 23,
        required_daily_net_profit_sar: 2608.7,
        projected_month_end_net_profit_sar: 177142.86,
        profit_accounting_quality_known: true,
        profit_accounting_complete: false,
        evidence: {
            valid: true,
            freshness_status: "fresh",
            age_seconds: 7200,
            max_age_seconds: 21600,
            calculated_at: "2026-09-07T08:00:00+00:00",
            next_run_at: "2026-09-07T13:00:00+00:00",
            data_through: "2026-09-06",
            data_through_status: "source_watermark",
            accounting_quality: "incomplete",
            period: {
                kind: "calendar_month_to_date",
                from: "2026-09-01",
                to: "2026-09-07",
                timezone: "Asia/Riyadh",
            },
        },
        ...overrides,
    };
}

function initialReads(goal = displayedGoal()) {
    api.get
        .mockResolvedValueOnce({ data: { available: true, recommendations: [], monthly_profit_goal: goal } })
        .mockResolvedValueOnce({ data: { active_count: 0, items: [] } })
        .mockResolvedValueOnce({ data: { ...goalConfig, minimum_net_profit_sar: goal.minimum_net_profit_sar, goal_config_id: goal.goal_config_id } });
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
    });
    return { promise, resolve, reject };
}

async function renderPage() {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    await act(async () => {
        root.render(<CampaignRecommendations />);
        await Promise.resolve();
        await Promise.resolve();
    });
    return {
        container,
        cleanup: () => act(() => {
            root.unmount();
            container.remove();
        }),
    };
}

function button(container, label) {
    return [...container.querySelectorAll("button")]
        .find((item) => item.textContent.includes(label));
}

async function changeGoalAndSave(container, value) {
    const input = container.querySelector('input[type="number"]');
    await act(async () => {
        Object.getOwnPropertyDescriptor(
            window.HTMLInputElement.prototype,
            "value",
        ).set.call(input, String(value));
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
        button(container, "حفظ الهدف").dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await Promise.resolve();
        await Promise.resolve();
    });
}

beforeEach(() => {
    jest.clearAllMocks();
    window.alert = jest.fn();
});

test("loads the real page and displays evidence period, age, watermark, and quality", async () => {
    initialReads();
    const view = await renderPage();

    expect(view.container.textContent).toContain("مدير الربح والحملات");
    expect(view.container.textContent).toContain("40,000.00 ر.س");
    expect(view.container.textContent).toContain("2026-09-01 → 2026-09-07");
    expect(view.container.textContent).toContain("عمر الدليل: ساعتان");
    expect(view.container.textContent).toContain("تغطية المصدر: حتى 2026-09-06");
    expect(view.container.textContent).toContain("جودة المحاسبة: غير مكتملة");
    view.cleanup();
});

test("keeps a successful save visible when the server cannot read progress", async () => {
    initialReads();
    api.put.mockResolvedValueOnce({
        data: displayedGoal({
            minimum_net_profit_sar: 125000,
            goal_config_id: "goal-2",
            goal_config_saved: true,
            goal_config_save_status: "saved",
            progress_available: false,
            progress_state: "calculation_failed",
            status: "profit_data_unavailable",
            net_profit_to_date_sar: null,
            remaining_to_target_sar: null,
            required_daily_net_profit_sar: null,
            projected_month_end_net_profit_sar: null,
            progress_unavailable_reason: "snapshot_read_failed_after_goal_save:RuntimeError",
        }),
    });
    const view = await renderPage();

    await changeGoalAndSave(view.container, 125000);

    expect(api.put).toHaveBeenCalledWith(
        "/ads-manager/ai-monitor/monthly-profit-goal",
        { minimum_net_profit_sar: 125000 },
    );
    expect(view.container.textContent).toContain("125,000.00 ر.س");
    expect(view.container.textContent).toContain("تم حفظ الهدف، لكن تعذر عرض التقدم الحالي");
    expect(window.alert).not.toHaveBeenCalled();
    view.cleanup();
});

test("the real page keeps the safe calculation cause separate from invalid evidence", async () => {
    initialReads(displayedGoal({
        progress_available: false,
        progress_state: "calculation_failed",
        status: "profit_data_unavailable",
        net_profit_to_date_sar: null,
        remaining_to_target_sar: null,
        required_daily_net_profit_sar: null,
        projected_month_end_net_profit_sar: null,
        scale_execution_allowed_by_profit_accounting: false,
        calculation_diagnostic: {
            state: "failed",
            reason: "month_to_date_profit_failed:RuntimeError",
            attempted_at: "2026-09-07T10:00:00+00:00",
            age_seconds: 0,
            freshness_status: "fresh",
            current_period: true,
        },
        evidence: {
            valid: false,
            freshness_status: "unknown",
            validation_reason: "monthly_goal_snapshot_calculated_at_missing_or_invalid",
            age_seconds: null,
            calculated_at: null,
            period: {
                kind: "calendar_month_to_date",
                from: "2026-09-01",
                to: "2026-09-07",
                timezone: "Asia/Riyadh",
            },
        },
    }));
    const view = await renderPage();

    expect(view.container.textContent).toContain("تعذر حساب تقدم الهدف");
    expect(view.container.textContent).toContain("نتيجة الحساب: تعذر حساب ربح الشهر من المصدر المحاسبي");
    expect(view.container.textContent).toContain("صلاحية دليل التقدم: غير مكتملة");
    expect(view.container.textContent).toContain("عمر الدليل: غير معروف");
    expect(view.container.textContent).toContain("صافي الربح حتى الآنبانتظار الحساب");
    expect(view.container.textContent).not.toContain("صافي الربح حتى الآن0.00 ر.س");
    view.cleanup();
});

test("ignores a late refresh after saving a newer goal", async () => {
    initialReads();
    api.put.mockResolvedValueOnce({ data: displayedGoal({
        minimum_net_profit_sar: 125000,
        goal_config_id: "goal-2",
        goal_config_saved: true,
        goal_config_save_status: "saved",
    }) });
    const view = await renderPage();
    const lateLatest = deferred();
    const lateWatch = deferred();
    const lateConfig = deferred();
    api.get
        .mockReturnValueOnce(lateLatest.promise)
        .mockReturnValueOnce(lateWatch.promise)
        .mockReturnValueOnce(lateConfig.promise);

    await act(async () => {
        button(view.container, "تحديث").dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await Promise.resolve();
    });
    await changeGoalAndSave(view.container, 125000);
    await act(async () => {
        lateLatest.resolve({ data: { available: true, recommendations: [], monthly_profit_goal: displayedGoal() } });
        lateWatch.resolve({ data: { active_count: 0, items: [] } });
        lateConfig.resolve({ data: goalConfig });
        await Promise.resolve();
        await Promise.resolve();
    });

    expect(view.container.textContent).toContain("125,000.00 ر.س");
    expect(view.container.textContent).not.toContain("100,000.00 ر.س");
    view.cleanup();
});

test("shows latest-read failure and reports a rejected save as a real failure", async () => {
    api.get
        .mockRejectedValueOnce(new Error("synthetic latest failure"))
        .mockResolvedValueOnce({ data: { active_count: 0, items: [] } })
        .mockResolvedValueOnce({ data: goalConfig });
    api.put.mockRejectedValueOnce(new Error("synthetic save failure"));
    const view = await renderPage();

    expect(view.container.textContent).toContain("تعذّر قراءة توصيات الحملات الآن");
    expect(view.container.textContent).toContain("تعذر حساب تقدم الهدف");
    await changeGoalAndSave(view.container, 125000);
    expect(window.alert).toHaveBeenCalledWith("تعذّر حفظ هدف صافي الربح.");
    expect(view.container.textContent).not.toContain("تم حفظ الهدف");
    view.cleanup();
});
