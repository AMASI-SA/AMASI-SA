import {
    normalizeSnapchatAd,
    normalizeSnapchatAdReport,
    SNAPCHAT_ENTITY_PAGE_SIZE,
} from "./snapchatAdPerformance";

describe("Snapchat Ad performance service", () => {
    test("normalizes ad identity, parent hierarchy and metrics", () => {
        expect(normalizeSnapchatAd({
            account_id: "account-1",
            account_name: "سناب الرياض",
            ad_id: "ad-1",
            ad_name: "فيديو المنتج",
            ad_squad_id: "squad-1",
            ad_squad_name: "مجموعة الرياض",
            campaign_id: "campaign-1",
            campaign_name: "حملة المبيعات",
            status: "ACTIVE",
            review_status: "APPROVED",
            delivery_state: "DELIVERING",
            delivery_status: "يتم التسليم",
            creative_id: "creative-1",
            creative_name: "إبداع المنتج",
            creative_type: "SNAP_AD",
            spend_sar: "125.5",
            orders: "4",
            sales_sar: "500",
            roas: "3.984",
            view_content: "40",
            add_to_cart: "12",
            start_checkout: "6",
            add_billing: "4",
            paid_reach: "1000",
            paid_frequency: "1.5",
            reach_frequency_scope: "exact_one_day_total",
        })).toMatchObject({
            ad_id: "ad-1",
            ad_name: "فيديو المنتج",
            ad_squad_id: "squad-1",
            campaign_id: "campaign-1",
            status: "ACTIVE",
            review_status: "APPROVED",
            delivery_state: "DELIVERING",
            creative_id: "creative-1",
            creative_type: "SNAP_AD",
            spend_sar: 125.5,
            orders: 4,
            sales_sar: 500,
            roas: 3.984,
            view_content: 40,
            add_to_cart: 12,
            start_checkout: 6,
            add_billing: 4,
            paid_reach: 1000,
            paid_frequency: 1.5,
            reach_frequency_scope: "exact_one_day_total",
            result_source: "platform",
        });
    });

    test("keeps inherited delivery blocker separate from configured status", () => {
        expect(normalizeSnapchatAd({
            ad_id: "ad-2",
            status: "ACTIVE",
            delivery_state: "NOT_DELIVERING",
            delivery_status: "لا تسليم — الحساب موقوف بسبب الدفع",
            delivery_reason_code: "ACCOUNT_PAYMENT_BLOCKED",
            delivery_inherited_from_ad_squad: true,
        })).toMatchObject({
            status: "ACTIVE",
            configured_status: "ACTIVE",
            delivery_state: "NOT_DELIVERING",
            delivery_reason_code: "ACCOUNT_PAYMENT_BLOCKED",
            delivery_inherited_from_ad_squad: true,
        });
    });

    test("normalizes report pagination and read-only policy", () => {
        const report = normalizeSnapchatAdReport({
            date_from: "2026-08-04",
            date_to: "2026-08-04",
            account_timezone: "Asia/Riyadh",
            selected_account_id: "account-1",
            totals: { spend_sar: 100, orders: 2 },
            ads: [{ ad_id: "ad-1" }],
            pagination: { page: 1, limit: 100, total: 1, pages: 1 },
            policy: { mode: "observe_only", mutations_allowed: false },
        });

        expect(report.entity_level).toBe("ad");
        expect(report.account_timezone).toBe("Asia/Riyadh");
        expect(report.totals.spend_sar).toBe(100);
        expect(report.ads).toHaveLength(1);
        expect(report.pagination.total).toBe(1);
        expect(report.policy.mutations_allowed).toBe(false);
    });

    test("defaults entity pagination to nine rows", () => {
        const report = normalizeSnapchatAdReport({});
        expect(SNAPCHAT_ENTITY_PAGE_SIZE).toBe(9);
        expect(report.pagination.limit).toBe(9);
    });

});
