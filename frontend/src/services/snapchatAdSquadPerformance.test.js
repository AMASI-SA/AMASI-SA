import {
    normalizeAdSquad,
    normalizeSnapchatAdSquadReport,
    snapchatEntityPageLimit,
    SNAPCHAT_ENTITY_PAGE_SIZE,
} from "./snapchatAdSquadPerformance";

describe("Snapchat Ad Squad performance service", () => {
    test("normalizes identity, parent campaign and metrics", () => {
        expect(normalizeAdSquad({
            account_id: "account-1",
            account_name: "سناب الرياض",
            ad_squad_id: "squad-1",
            ad_squad_name: "مجموعة التحويلات",
            campaign_id: "campaign-1",
            campaign_name: "حملة المبيعات",
            status: "ACTIVE",
            delivery_status: "يتم التسليم",
            spend_sar: "125.5",
            orders: "4",
            sales_sar: "500",
            roas: "3.984",
            budget: { currency: "SAR", daily_native: "200" },
        })).toMatchObject({
            ad_squad_id: "squad-1",
            ad_squad_name: "مجموعة التحويلات",
            campaign_id: "campaign-1",
            campaign_name: "حملة المبيعات",
            status: "ACTIVE",
            configured_status: "ACTIVE",
            effective_status: "ACTIVE",
            spend_sar: 125.5,
            orders: 4,
            sales_sar: 500,
            roas: 3.984,
            budget: { currency: "SAR", daily_native: 200 },
            result_source: "platform",
        });
    });

    test("keeps configured status active while debt blocks delivery", () => {
        expect(normalizeAdSquad({
            ad_squad_id: "squad-2",
            status: "PAUSED",
            configured_status: "ACTIVE",
            effective_status: "ACTIVE",
            previous_operational_status: "PAUSED",
            delivery_inherited_from_account: true,
            delivery_state: "NOT_DELIVERING",
            delivery_reason_code: "ACCOUNT_PAYMENT_BLOCKED",
            delivery_status: "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد",
            delivery_detail: "رصيد الحساب أو وسيلة الدفع لا تسمح بالتسليم.",
        })).toMatchObject({
            status: "ACTIVE",
            configured_status: "ACTIVE",
            effective_status: "ACTIVE",
            previous_operational_status: "PAUSED",
            status_inherited_from_campaign: false,
            delivery_inherited_from_account: true,
            delivery_state: "NOT_DELIVERING",
            delivery_reason_code: "ACCOUNT_PAYMENT_BLOCKED",
            delivery_status: "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد",
        });
    });

    test("keeps the configured switch active while parent delivery is blocked", () => {
        expect(normalizeAdSquad({
            ad_squad_id: "squad-3",
            status: "PAUSED",
            configured_status: "ACTIVE",
            effective_status: "ACTIVE",
            delivery_inherited_from_campaign: true,
            delivery_state: "NOT_DELIVERING",
            delivery_reason_code: "PARENT_CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
            delivery_status: "لا تسليم — الحملة خارج الميزانية اليومية",
            parent_campaign_configured_status: "ACTIVE",
            parent_campaign_delivery_state: "NOT_DELIVERING",
        })).toMatchObject({
            status: "ACTIVE",
            configured_status: "ACTIVE",
            effective_status: "ACTIVE",
            status_inherited_from_campaign: false,
            delivery_inherited_from_campaign: true,
            delivery_state: "NOT_DELIVERING",
            delivery_reason_code: "PARENT_CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
            delivery_status: "لا تسليم — الحملة خارج الميزانية اليومية",
        });
    });

    test("keeps pagination and platform-only policy", () => {
        const report = normalizeSnapchatAdSquadReport({
            date_from: "2026-08-04",
            date_to: "2026-08-04",
            account_timezone: "Asia/Riyadh",
            selected_account_id: "account-1",
            totals: { spend_sar: 100, orders: 2 },
            daily: [{ date: "2026-08-04", spend_sar: 100, orders: 2 }],
            ad_squads: [{ ad_squad_id: "squad-1" }],
            pagination: { page: 1, limit: 25, total: 1, pages: 1 },
            policy: { mode: "observe_only", mutations_allowed: false },
        });

        expect(report.entity_level).toBe("ad_squad");
        expect(report.account_timezone).toBe("Asia/Riyadh");
        expect(report.totals.spend_sar).toBe(100);
        expect(report.ad_squads).toHaveLength(1);
        expect(report.pagination.total).toBe(1);
        expect(report.result_source).toBe("platform");
        expect(report.policy.mutations_allowed).toBe(false);
    });

    test("defaults entity pagination to nine rows", () => {
        const report = normalizeSnapchatAdSquadReport({});
        expect(SNAPCHAT_ENTITY_PAGE_SIZE).toBe(9);
        expect(report.pagination.limit).toBe(9);
    });

    test("caps ad squad API requests at nine rows", () => {
        expect(snapchatEntityPageLimit(100)).toBe(9);
        expect(snapchatEntityPageLimit("9")).toBe(9);
        expect(snapchatEntityPageLimit(5)).toBe(5);
        expect(snapchatEntityPageLimit(0)).toBe(1);
        expect(snapchatEntityPageLimit("invalid")).toBe(9);
    });

});
