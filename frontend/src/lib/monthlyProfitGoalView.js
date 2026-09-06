export const GOAL_PROGRESS_MESSAGES = {
    missing: "لا توجد نتيجة محفوظة لهذا الشهر بعد.",
    stale: "نتيجة الربح المحفوظة قديمة ولا تُعرض كتقدم حالي.",
    calculation_failed: "تعذر حساب تقدم الهدف من بيانات الربح الحالية.",
    quality_incomplete: "التقدم ظاهر، لكن جودة بيانات الربح غير مكتملة.",
    config_mismatch: "أُعيد اشتقاق التقدم للهدف الحالي من دليل الربح المحفوظ.",
    available: null,
};

function unavailable(config, progressState, reason) {
    return {
        ...(config || {}),
        progress_available: false,
        progress_state: progressState,
        progress_unavailable_reason: reason,
        net_profit_to_date_sar: null,
        remaining_to_target_sar: null,
        required_daily_net_profit_sar: null,
        projected_month_end_net_profit_sar: null,
    };
}

export function monthlyProfitGoalView({
    goalConfig,
    snapshotGoal,
    latestReadFailed = false,
    configReadFailed = false,
}) {
    if (configReadFailed || !goalConfig) {
        return unavailable(
            {
                minimum_net_profit_sar: null,
                configured: null,
                goal_config_id: null,
                source: "goal_config_unavailable",
            },
            "calculation_failed",
            "goal_config_read_failed",
        );
    }
    if (latestReadFailed) {
        return unavailable(
            goalConfig,
            "calculation_failed",
            "latest_recommendation_snapshot_read_failed",
        );
    }
    if (!snapshotGoal) {
        return unavailable(
            goalConfig,
            "missing",
            "monthly_goal_snapshot_missing",
        );
    }
    if (
        !goalConfig.goal_config_id
        || snapshotGoal.goal_config_id !== goalConfig.goal_config_id
    ) {
        return unavailable(
            goalConfig,
            "config_mismatch",
            "goal_config_mismatch_not_reconciled_by_server",
        );
    }
    return {
        ...snapshotGoal,
        ...goalConfig,
    };
}

export function shouldApplyGoalRead({
    requestId,
    latestRequestId,
    mutationVersionAtStart,
    currentMutationVersion,
}) {
    return (
        requestId === latestRequestId
        && mutationVersionAtStart === currentMutationVersion
    );
}
