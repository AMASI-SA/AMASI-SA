import {
    GOAL_PROGRESS_MESSAGES,
    monthlyProfitGoalView,
    shouldApplyGoalRead,
} from "./monthlyProfitGoalView";

const config = {
    minimum_net_profit_sar: 100000,
    configured: true,
    updated_at: "2026-09-07T10:00:00Z",
    goal_config_id: "goal-current",
};

test("current owner goal remains authoritative over snapshot fields", () => {
    const view = monthlyProfitGoalView({
        goalConfig: config,
        snapshotGoal: {
            minimum_net_profit_sar: 80000,
            goal_config_id: "goal-current",
            progress_available: true,
            net_profit_to_date_sar: 0,
            progress_state: "available",
        },
    });
    expect(view.minimum_net_profit_sar).toBe(100000);
    expect(view.net_profit_to_date_sar).toBe(0);
    expect(view.progress_available).toBe(true);
});

test("unreconciled config mismatch never mixes old derivatives with a new target", () => {
    const view = monthlyProfitGoalView({
        goalConfig: config,
        snapshotGoal: {
            minimum_net_profit_sar: 80000,
            goal_config_id: "goal-old",
            progress_available: true,
            net_profit_to_date_sar: 70000,
            remaining_to_target_sar: 10000,
        },
    });
    expect(view.minimum_net_profit_sar).toBe(100000);
    expect(view.progress_state).toBe("config_mismatch");
    expect(view.progress_available).toBe(false);
    expect(view.net_profit_to_date_sar).toBeNull();
});

test("config read failure does not invent the default owner target", () => {
    const view = monthlyProfitGoalView({ configReadFailed: true });
    expect(view.minimum_net_profit_sar).toBeNull();
    expect(view.progress_state).toBe("calculation_failed");
    expect(view.progress_unavailable_reason).toBe("goal_config_read_failed");
});

test("latest failure replaces prior progress with an explicit failed read", () => {
    const view = monthlyProfitGoalView({
        goalConfig: config,
        latestReadFailed: true,
        snapshotGoal: {
            goal_config_id: "goal-current",
            progress_available: true,
            net_profit_to_date_sar: 50000,
        },
    });
    expect(view.progress_available).toBe(false);
    expect(view.net_profit_to_date_sar).toBeNull();
    expect(view.progress_unavailable_reason).toBe("latest_recommendation_snapshot_read_failed");
});

test("reads started before or during a goal save cannot overwrite its result", () => {
    expect(shouldApplyGoalRead({
        requestId: 4,
        latestRequestId: 4,
        mutationVersionAtStart: 2,
        currentMutationVersion: 3,
    })).toBe(false);
    expect(shouldApplyGoalRead({
        requestId: 4,
        latestRequestId: 5,
        mutationVersionAtStart: 3,
        currentMutationVersion: 3,
    })).toBe(false);
    expect(shouldApplyGoalRead({
        requestId: 5,
        latestRequestId: 5,
        mutationVersionAtStart: 4,
        currentMutationVersion: 4,
    })).toBe(true);
});

test("all unavailable states have distinct user-facing explanations", () => {
    expect(GOAL_PROGRESS_MESSAGES.missing).not.toBe(GOAL_PROGRESS_MESSAGES.stale);
    expect(GOAL_PROGRESS_MESSAGES.calculation_failed).not.toBe(GOAL_PROGRESS_MESSAGES.quality_incomplete);
    expect(GOAL_PROGRESS_MESSAGES.config_mismatch).toContain("دليل الربح المحفوظ");
});
