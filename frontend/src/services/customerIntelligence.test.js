import api from "../lib/api";
import {
    CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS,
    customerIntelligenceWritesLocked,
    getCustomerIntelligenceWorkspace,
    normalizeCustomerIntelligenceWorkspace,
} from "./customerIntelligence";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
    },
}));

const backendPayload = {
    schema_version: 1,
    generated_at: "2026-07-28T18:00:00Z",
    mode: "preview_fixture",
    data_origin: "synthetic",
    workspace: {
        title_ar: "مركز ذكاء العملاء والمبيعات",
        title_en: "Customer Intelligence & Sales Center",
        description_ar: "معاينة Backend مصطنعة.",
        preview_notice_ar: "لا تنفيذ حقيقي.",
        owner_preview: true,
        operating_level: 1,
        operating_level_label: "اقتراح ومراجعة بشرية",
        tabs: [],
        objections: [{ id: "o1", label: "السعر", count: 1 }],
        campaign_impact: [],
        integrations: [],
    },
    overview: {
        open_conversations: 2,
        needs_human_review: 1,
        follow_ups_due: 1,
        sales_opportunities: 1,
        product_opportunities: 1,
        potential_revenue_sar: 249,
    },
    conversations: [{
        conversation_id: "conversation-1",
        customer_label: "عميلة تجريبية",
        channel: "whatsapp_mock",
        state: "needs_reply",
        intent: "شراء هدية",
        objection: "السعر",
        confidence: 0.91,
        assigned_to: "فريق خدمة العملاء",
        last_message: "هل اللون الكحلي متوفر؟",
        messages: [
            {
                message_id: "message-text",
                sender: "customer",
                kind: "text",
                text: "هل اللون الكحلي متوفر؟",
            },
            {
                message_id: "message-audio",
                sender: "customer",
                kind: "audio",
                media_analysis: {
                    transcript: "بطلبه الخميس.",
                    summary_ar: "موعد شراء لاحق.",
                    confidence: 0.93,
                    is_fixture: true,
                },
            },
        ],
    }],
    customer_profile: {
        customer_id: "customer-1",
        customer_label: "عميلة تجريبية",
        lifecycle_stage: "تنتظر الراتب",
        classifications: ["مستفسرة"],
        preferred_products: ["هدايا شخصية"],
        preferred_colors: ["كحلي"],
        budget_range_sar: "200–300",
        purchase_probability: 0.82,
        contact_consent: "unknown",
        next_best_action: "تحقق الموظف أولًا.",
        facts_are_synthetic: true,
    },
    follow_ups: [{
        follow_up_id: "follow-up-1",
        conversation_id: "conversation-1",
        due_at: "2026-07-30T12:00:00Z",
        reason: "طلبت المتابعة.",
        proposed_message: "رسالة مقترحة فقط.",
        status: "suggested_preview",
        execution_allowed: false,
    }],
    sales_opportunities: [{
        opportunity_id: "sale-1",
        conversation_id: "conversation-1",
        title: "هدية شخصية",
        stage: "suggested",
        score: 82,
        reason: "نية شراء واضحة.",
        next_best_action: "تحقق من اللون.",
    }],
    product_opportunities: [{
        opportunity_id: "product-1",
        title: "لون كحلي",
        stage: "collecting",
        request_count: 6,
        unique_customers: 4,
        confidence: 0.78,
        evidence_examples: ["هل يوجد كحلي؟"],
        closest_store_products: ["منتج قريب"],
        recommendation: "request_sample",
        product_creation_allowed: false,
    }],
    competitor_signals: [{
        signal_id: "competitor-1",
        name: "متجر تجريبي",
        status: "potential_repeated",
        mention_count: 3,
        linked_product: "سلسال كحلي",
        confidence: 0.67,
        external_research_allowed: false,
    }],
    approved_offers: [{
        offer_id: "offer-1",
        label_ar: "عرض تجريبي",
        offer_type: "bundle",
        value: "20 ر.س",
        reason: "معاينة",
        expected_margin_impact_sar: -20,
        approval_state: "demo_approved",
        application_allowed: false,
    }],
    conversation_cart: {
        draft_id: "draft-1",
        conversation_id: "conversation-1",
        status: "preview_only",
        items: [{
            product_id: "product-1",
            title: "منتج تجريبي",
            variant: "كحلي",
            quantity: 1,
            unit_price_sar: 249,
            source_verification: "synthetic_unverified",
        }],
        subtotal_sar: 249,
        shipping_sar: 25,
        discount_sar: 0,
        total_sar: 274,
        price_verified_from_source: false,
        inventory_verified_from_source: false,
        customer_confirmed: false,
        create_order_allowed: false,
        payment_link: {
            url: "https://payment-preview.example.invalid/draft-1",
            label_ar: "رابط وهمي",
            is_real: false,
            creation_allowed: false,
        },
    },
    knowledge: {
        status: "proposal_only",
        suggested_articles: ["سياسة تأكيد التوصيل"],
        publication_allowed: false,
    },
    quality: {
        measurement_mode: "synthetic",
        suggested_reply_acceptance_pct: 84,
        human_escalation_pct: 16,
        paid_order_conversion_pct: 21,
        detected_policy_violations: 0,
    },
    audit_preview: [{
        decision_id: "decision-1",
        evidence_refs: ["message-text"],
        proposed_action: "اقتراح متابعة",
        execution_status: "not_executed",
    }],
    safety_policy: {
        mode: "observe_only",
        preview_only: true,
        fixtures_are_synthetic: true,
        writes_allowed: false,
        external_calls_allowed: false,
        whatsapp_send_allowed: false,
        order_creation_allowed: false,
        discount_creation_allowed: false,
        payment_link_creation_allowed: false,
        product_mutation_allowed: false,
        campaign_mutation_allowed: false,
        ai_execution_allowed: false,
    },
};

test("normalizes the canonical backend aliases without inventing a second fixture", () => {
    const model = normalizeCustomerIntelligenceWorkspace(backendPayload);

    expect(model.schema_version).toBe(1);
    expect(model.conversations[0]).toMatchObject({
        id: "conversation-1",
        customer_name: "عميلة تجريبية",
        status: "needs_reply",
    });
    expect(model.conversations[0].messages[1]).toMatchObject({
        id: "message-audio",
        type: "audio",
        transcript: "بطلبه الخميس.",
        analysis: "موعد شراء لاحق.",
    });
    expect(model.customer_profile).toMatchObject({
        id: "customer-1",
        display_name: "عميلة تجريبية",
        labels: ["مستفسرة"],
        preferred_attributes: ["كحلي"],
    });
    expect(model.follow_ups[0].id).toBe("follow-up-1");
    expect(model.sales_opportunities[0]).toMatchObject({
        id: "sale-1",
        product: "هدية شخصية",
        probability: 0.82,
    });
    expect(model.product_opportunities[0]).toMatchObject({
        id: "product-1",
        mentions: 6,
        distinct_customers: 4,
    });
    expect(model.conversation_cart).toMatchObject({
        id: "draft-1",
        fake_payment_link: "https://payment-preview.example.invalid/draft-1",
    });
    expect(model.knowledge.entries[0].title).toBe("سياسة تأكيد التوصيل");
    expect(model.audit_preview[0].id).toBe("decision-1");
});

test("empty input stays empty and never creates local preview business data", () => {
    const model = normalizeCustomerIntelligenceWorkspace({});

    expect(model.schema_version).toBeNull();
    expect(model.conversations).toEqual([]);
    expect(model.follow_ups).toEqual([]);
    expect(model.sales_opportunities).toEqual([]);
    expect(model.product_opportunities).toEqual([]);
    expect(model.competitor_signals).toEqual([]);
    expect(model.approved_offers).toEqual([]);
    expect(model.conversation_cart.items).toEqual([]);
    expect(model.knowledge.entries).toEqual([]);
    expect(model.quality.metrics).toEqual([]);
    expect(model.audit_preview).toEqual([]);
});

test("fails closed even when a malicious payload advertises manage mode and writes", () => {
    const model = normalizeCustomerIntelligenceWorkspace({
        safety_policy: {
            mode: "manage",
            preview_only: false,
            writes_allowed: true,
            external_calls_allowed: true,
            whatsapp_send_allowed: true,
            order_creation_allowed: true,
            discount_creation_allowed: true,
            payment_link_creation_allowed: true,
            product_mutation_allowed: true,
            campaign_mutation_allowed: true,
            ai_execution_allowed: true,
        },
    });

    expect(model.safety_policy.mode).toBe("observe_only");
    expect(model.safety_policy.preview_only).toBe(true);
    CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS.forEach((key) => {
        expect(model.safety_policy[key]).toBe(false);
    });
    expect(customerIntelligenceWritesLocked(model.safety_policy)).toBe(true);
});

test("loads only the canonical owner preview endpoint", async () => {
    api.get.mockResolvedValueOnce({ data: backendPayload });

    const model = await getCustomerIntelligenceWorkspace();

    expect(api.get).toHaveBeenCalledWith("/customer-intelligence/v1/workspace");
    expect(model.schema_version).toBe(1);
});
