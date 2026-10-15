import React, { act } from "react";
import { createRoot } from "react-dom/client";

import AdDecisionChangeCard from "./AdDecisionChangeCard";

describe("AdDecisionChangeCard", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
    });

    test("keeps execution separate from business outcome and never invents a direct-change reason", async () => {
        await act(async () => root.render(
            <AdDecisionChangeCard decision={{
                decision_id: "change-1",
                action: "campaign.update",
                direct_snapchat: true,
                occurred_at: "2026-08-12T10:30:00Z",
                reason: null,
                before: { daily_budget: 100 },
                after: { daily_budget: 80 },
                expected: { cpa_sar: 20 },
                actual: {},
                execution: { status: "completed" },
                outcome: { status: "pending" },
                evidence: { windows: { "14": { roas: 2.1 }, "3": { roas: 4.2 } } },
                trend_override_reason: "نفاد المخزون مثبت رغم التحسن الحديث.",
                supporting_context: ["موعد راتب محتمل وغير مثبت"],
            }} />,
        ));

        expect(container.textContent).toContain("حالة التنفيذ");
        expect(container.textContent).toContain("نتيجة الأعمال");
        expect(container.textContent).toContain("لم يُسجَّل سبب التعديل في Snapchat");
        expect(container.textContent).toContain("سبب تجاهل التحسن الحديث");
        expect(container.textContent).toContain("سياق مساند — ليس أساس القرار وحده");
        ["14 يوم", "7 يوم", "3 يوم", "2 يوم", "1 يوم"].forEach((label) => {
            expect(container.textContent).toContain(label);
        });
    });

    test("separates planned changes from a verified after-state and safely renders measured evidence", async () => {
        await act(async () => root.render(
            <AdDecisionChangeCard decision={{
                decision_id: "change-failed",
                action: "campaign.update",
                direct_snapchat: false,
                reason: "محاولة خفض الصرف مع حماية المبيعات.",
                before: { status: "ACTIVE", daily_budget_micro: 100_000_000 },
                changes: { daily_budget_micro: 80_000_000 },
                after: { status: "PAUSED", daily_budget_micro: 80_000_000 },
                expected: { sales_sar: "increase" },
                actual: { sales_sar: 140 },
                execution: { status: "failed" },
                outcome: {
                    status: "successful",
                    expected_vs_actual: {
                        checks: [{ scope: "campaign", metric: "sales_sar", direction: "increase", met: true }],
                    },
                    deltas: {
                        campaign: { sales_sar: { actual: 140, delta_pct: 40, direction: "increase" } },
                        account: { orders: { actual: 12, delta_pct: 20, direction: "increase" } },
                        store: { gross_profit_before_marketing_sar: { actual: 300, delta_pct: 10, direction: "increase" } },
                    },
                    post_attribution: {
                        campaign_attributed_units: 5,
                        whole_store_product_units: 9,
                        verified_cross_platform_units_excluded: 1,
                        units_unresolved_for_snapchat_decision: 3,
                    },
                },
                evidence: {
                    windows: {},
                    products: [{ product_id: "710474094", product_name: "مشط شنب" }],
                    inventory: [{
                        salla_product_id: "710474094",
                        unlimited_quantity: true,
                        freshness_status: "fresh",
                        observed_after_capture: false,
                        variant_found: true,
                        delivery_blocked: false,
                    }],
                    product_link_state: "confirmed",
                },
                supporting_context: [{
                    code: "payday_note",
                    value: { day: 27, audience: "موظفو الحكومة" },
                    verification_status: "unverified",
                }],
                annotations: [{
                    id: "note-1",
                    text: "ثبت بعد القياس أن المبيعات ارتفعت مع بقاء المكسب موجبًا.",
                    annotated_at: "2026-08-13T10:30:00Z",
                }],
            }} />,
        ));

        expect(container.textContent).toContain("التغيير المخطط");
        expect(container.textContent).toContain("بعد التنفيذ المتحقق");
        expect(container.textContent).toContain("فشل التنفيذ؛ لا توجد حالة بعدية متحققة");
        expect(container.textContent).not.toContain("PAUSED");
        expect(container.textContent).not.toContain("بعد التعديل");
        expect(container.textContent).toContain("فحوص المتوقع مقابل الفعلي");
        expect(container.textContent).toContain("التغير المقاس بعد القرار");
        expect(container.textContent).toContain("الحملة · المبيعات · ارتفاع · تحقق");
        expect(container.textContent).toContain("الحساب");
        expect(container.textContent).toContain("المتجر");
        expect(container.textContent).toContain("إسناد المنتج بعد القرار");
        expect(container.textContent).toContain("day: 27 · audience: موظفو الحكومة");
        expect(container.textContent).not.toContain("[object Object]");
        expect(container.textContent).toContain("ملاحظات التعديل المسجلة لاحقًا");
        expect(container.textContent).toContain("ثبت بعد القياس");
        expect(container.textContent).toContain("المنتج المقصود والمخزون وقت القرار");
        expect(container.textContent).toContain("مشط شنب");
        expect(container.textContent).toContain("غير محدود");
    });

    test("matches stock to each product variant instead of overwriting by product id", async () => {
        await act(async () => root.render(
            <AdDecisionChangeCard decision={{
                decision_id: "variant-stock",
                action: "ad.update",
                reason: "مراجعة مخزون خيارات المنتج قبل القرار.",
                execution: { status: "completed" },
                outcome: { status: "pending" },
                evidence: {
                    windows: {},
                    products: [
                        { product_id: "comb-1", product_variant_id: "gold", product_name: "مشط معدني" },
                        { product_id: "comb-1", product_variant_id: "silver", product_name: "مشط معدني" },
                    ],
                    inventory: [
                        {
                            salla_product_id: "comb-1", product_variant_id: "gold", quantity: 8,
                            freshness_status: "fresh", observed_after_capture: false,
                            variant_found: true, delivery_blocked: false,
                        },
                        {
                            salla_product_id: "comb-1", product_variant_id: "silver", quantity: 0,
                            freshness_status: "fresh", observed_after_capture: false,
                            variant_found: true, delivery_blocked: false,
                        },
                    ],
                    product_link_state: "confirmed",
                },
            }} />,
        ));

        expect(container.textContent).toContain("الخيار: gold");
        expect(container.textContent).toContain("الخيار: silver");
        expect(container.textContent).toContain("المخزون: 8");
        expect(container.textContent).toContain("المخزون: 0");
    });

    test("never presents stale, future, missing-variant, or blocked stock as verified", async () => {
        await act(async () => root.render(
            <AdDecisionChangeCard decision={{
                decision_id: "unverified-stock",
                action: "campaign.update",
                reason: "مراجعة المخزون التاريخي.",
                execution: { status: "completed" },
                outcome: { status: "pending" },
                evidence: {
                    windows: {},
                    products: [
                        { product_id: "stale", product_name: "قديم" },
                        { product_id: "future", product_name: "مستقبلي" },
                        { product_id: "missing", product_variant_id: "v1", product_name: "خيار مفقود" },
                        { product_id: "blocked", product_name: "محظور" },
                    ],
                    inventory: [
                        {
                            salla_product_id: "stale", quantity: 111,
                            freshness_status: "stale_or_unknown", observed_after_capture: false,
                            variant_found: true, delivery_blocked: false,
                        },
                        {
                            salla_product_id: "future", quantity: 222,
                            freshness_status: "observed_after_capture", observed_after_capture: true,
                            variant_found: true, delivery_blocked: true,
                        },
                        {
                            salla_product_id: "missing", product_variant_id: "v1", quantity: 333,
                            freshness_status: "fresh", observed_after_capture: false,
                            variant_found: false, delivery_blocked: true,
                        },
                        {
                            salla_product_id: "blocked", quantity: 444,
                            freshness_status: "fresh", observed_after_capture: false,
                            variant_found: true, delivery_blocked: true,
                        },
                    ],
                },
            }} />,
        ));

        expect((container.textContent.match(/غير متحقق وقت القرار/g) || [])).toHaveLength(4);
        ["المخزون: 111", "المخزون: 222", "المخزون: 333", "المخزون: 444"]
            .forEach((value) => expect(container.textContent).not.toContain(value));
        expect(container.textContent).toContain("لقطة المخزون قديمة أو غير مكتملة");
        expect(container.textContent).toContain("لقطة المخزون أحدث من وقت القرار");
        expect(container.textContent).toContain("خيار المنتج لم يوجد");
        expect(container.textContent).toContain("لا تسمح باعتباره متاحًا");
    });
});
