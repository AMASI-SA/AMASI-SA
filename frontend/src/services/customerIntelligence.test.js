import api from "../lib/api";
import {
    CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS,
    createCustomerIntelligenceReplySuggestion,
    customerIntelligenceWritesLocked,
    getCustomerIntelligenceInbox,
    getCustomerIntelligenceWorkspace,
    normalizeCustomerIntelligenceInbox,
    normalizeCustomerIntelligenceWorkspace,
    reviewCustomerIntelligenceReplySuggestion,
} from "./customerIntelligence";

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
    },
}));

beforeEach(() => {
    jest.clearAllMocks();
});

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

const liveInboxPayload = {
    schema_version: 1,
    generated_at: "2026-08-12T00:30:00Z",
    mode: "live_receive_only",
    data_origin: "whatsapp_webhook",
    connection: {
        provider: "whatsapp",
        status: "connected",
        connected_channels: 1,
        receiving_channels: 1,
        access_token: "must-never-render",
    },
    conversation_count: 1,
    message_count: 2,
    content_unavailable_count: 1,
    has_more: true,
    next_offset: 20,
    conversations: [{
        conversation_id: "conv-safe-1",
        customer_id: "cust-safe-1",
        customer_name: "عميل واتساب",
        customer_mobile: "+966500000000",
        external_conversation_key: "hidden-hmac",
        channel: "whatsapp",
        status: "open",
        last_message: "اختبار ربط ميزان 2",
        last_message_at: "2026-08-12T00:29:00Z",
        message_count: 2,
        content_unavailable_count: 1,
        messages: [{
            message_id: "msg-safe-1",
            direction: "inbound",
            sender: "customer",
            kind: "text",
            body: "اختبار ربط ميزان 2",
            occurred_at: "2026-08-12T00:28:00Z",
            delivery_state: "received",
            content_available: true,
            content_ciphertext: "hidden-ciphertext",
        }, {
            message_id: "msg-safe-2",
            direction: "inbound",
            sender: "customer",
            kind: "image",
            caption: "صورة المنتج",
            mime_type: "image/jpeg",
            occurred_at: "2026-08-12T00:29:00Z",
            delivery_state: "received",
            content_available: true,
            provider_media_id: "hidden-provider-id",
        }],
        reply_suggestion: {
            suggestion_id: "suggestion-safe-1",
            status: "pending_approval",
            text: "أهلًا بك، اللون متوفر.",
            version: 2,
            requires_human_approval: true,
            send_allowed: false,
            created_at: "2026-08-12T00:29:30Z",
            provider_token: "hidden-suggestion-secret",
        },
    }],
    safety_policy: {
        mode: "manage",
        receive_only: false,
        writes_allowed: true,
        whatsapp_send_allowed: true,
        ai_auto_reply_allowed: true,
        commerce_mutation_allowed: true,
    },
};

test("normalizes the live receive-only inbox and drops unapproved sensitive fields", () => {
    const inbox = normalizeCustomerIntelligenceInbox(liveInboxPayload);

    expect(inbox).toMatchObject({
        mode: "live_receive_only",
        data_origin: "whatsapp_webhook",
        conversation_count: 1,
        message_count: 2,
        content_unavailable_count: 1,
        has_more: true,
        next_offset: 20,
        connection: {
            status: "connected",
            connected_channels: 1,
            receiving_channels: 1,
        },
        safety_policy: {
            mode: "observe_only",
            receive_only: true,
            writes_allowed: false,
            whatsapp_send_allowed: false,
            ai_auto_reply_allowed: false,
            commerce_mutation_allowed: false,
        },
    });
    expect(inbox.conversations[0]).toMatchObject({
        id: "conv-safe-1",
        customer_name: "عميل واتساب",
        channel: "whatsapp",
        status: "open",
        content_unavailable_count: 1,
        reply_suggestion: {
            id: "suggestion-safe-1",
            conversation_id: "conv-safe-1",
            status: "pending_approval",
            text: "أهلًا بك، اللون متوفر.",
            version: 2,
            requires_human_approval: true,
            send_allowed: false,
        },
    });
    expect(inbox.conversations[0].messages[1]).toMatchObject({
        id: "msg-safe-2",
        kind: "image",
        caption: "صورة المنتج",
    });

    const serialized = JSON.stringify(inbox);
    expect(serialized).not.toContain("+966500000000");
    expect(serialized).not.toContain("hidden-hmac");
    expect(serialized).not.toContain("hidden-ciphertext");
    expect(serialized).not.toContain("hidden-provider-id");
    expect(serialized).not.toContain("must-never-render");
    expect(serialized).not.toContain("hidden-suggestion-secret");
});

test("live inbox normalization is idempotent so the container cannot drop a suggestion", () => {
    const first = normalizeCustomerIntelligenceInbox(liveInboxPayload);
    const second = normalizeCustomerIntelligenceInbox(first);

    expect(second.conversations[0].reply_suggestion).toEqual(
        first.conversations[0].reply_suggestion,
    );
    expect(second.conversations[0].messages).toEqual(first.conversations[0].messages);
});

test("only exposes an explicit pending human-approval reply suggestion", () => {
    const baseConversation = liveInboxPayload.conversations[0];
    const invalidSuggestions = [
        null,
        { ...baseConversation.reply_suggestion, status: "approved_locked" },
        { ...baseConversation.reply_suggestion, requires_human_approval: false },
        { ...baseConversation.reply_suggestion, send_allowed: true },
        { ...baseConversation.reply_suggestion, text: "" },
        { ...baseConversation.reply_suggestion, text: 123 },
        { ...baseConversation.reply_suggestion, version: "2" },
        [baseConversation.reply_suggestion],
    ];

    invalidSuggestions.forEach((replySuggestion) => {
        const inbox = normalizeCustomerIntelligenceInbox({
            ...liveInboxPayload,
            conversations: [{
                ...baseConversation,
                reply_suggestion: replySuggestion,
                suggested_reply: "must not become a suggestion",
            }],
        });
        expect(inbox.conversations[0].reply_suggestion).toBeNull();
        expect(JSON.stringify(inbox)).not.toContain("must not become a suggestion");
    });
});

test("never converts an outbound employee echo into an AI reply suggestion", () => {
    const sourceConversation = liveInboxPayload.conversations[0];
    const inbox = normalizeCustomerIntelligenceInbox({
        ...liveInboxPayload,
        conversations: [{
            ...sourceConversation,
            reply_suggestion: undefined,
            messages: [
                ...sourceConversation.messages,
                {
                    message_id: "employee-echo-1",
                    direction: "outbound",
                    sender: "employee",
                    kind: "text",
                    body: "هذا رد موظف في واتساب وليس اقتراح ذكاء",
                    occurred_at: "2026-08-12T00:30:00Z",
                    delivery_state: "delivered",
                    content_available: true,
                },
            ],
        }],
    });

    expect(inbox.conversations[0].reply_suggestion).toBeNull();
    expect(inbox.conversations[0].messages).toHaveLength(3);
    expect(inbox.conversations[0].messages[2]).toMatchObject({
        id: "employee-echo-1",
        direction: "outbound",
        sender: "employee",
        delivery_state: "delivered",
        body: "هذا رد موظف في واتساب وليس اقتراح ذكاء",
    });
});

test("rejects conversations unless the response declares the live webhook contract", () => {
    const inbox = normalizeCustomerIntelligenceInbox({
        ...liveInboxPayload,
        mode: "preview_fixture",
    });

    expect(inbox.mode).toBeNull();
    expect(inbox.connection.status).toBe("not_connected");
    expect(inbox.content_unavailable_count).toBe(0);
    expect(inbox.next_offset).toBeNull();
    expect(inbox.conversations).toEqual([]);
});

test("accepts governed employee echoes and drops every other outbound shape", () => {
    const inbox = normalizeCustomerIntelligenceInbox({
        ...liveInboxPayload,
        conversations: [
            {
                ...liveInboxPayload.conversations[0],
                messages: [
                    ...liveInboxPayload.conversations[0].messages,
                    {
                        message_id: "employee-echo-valid",
                        direction: "outbound",
                        sender: "employee",
                        kind: "text",
                        body: "رد موظف يجب عرضه",
                        occurred_at: "2026-08-12T00:30:00Z",
                        delivery_state: "delivered",
                        content_available: true,
                    },
                    {
                        message_id: "unexpected-outbound-sender",
                        direction: "outbound",
                        sender: "assistant",
                        kind: "text",
                        body: "must not render sender",
                        occurred_at: "2026-08-12T00:31:00Z",
                        delivery_state: "delivered",
                        content_available: true,
                    },
                    {
                        message_id: "unexpected-outbound-state",
                        direction: "outbound",
                        sender: "employee",
                        kind: "text",
                        body: "must not render state",
                        occurred_at: "2026-08-12T00:32:00Z",
                        delivery_state: "received",
                        content_available: true,
                    },
                ],
            },
            {
                ...liveInboxPayload.conversations[0],
                conversation_id: "unexpected-channel",
                channel: "instagram",
            },
        ],
    });

    expect(inbox.conversations).toHaveLength(1);
    expect(inbox.conversations[0].messages).toHaveLength(3);
    expect(JSON.stringify(inbox)).toContain("رد موظف يجب عرضه");
    expect(JSON.stringify(inbox)).not.toContain("must not render sender");
    expect(JSON.stringify(inbox)).not.toContain("must not render state");
    expect(JSON.stringify(inbox)).not.toContain("unexpected-channel");
});

test("loads the live inbox from its single read-only endpoint", async () => {
    api.get.mockResolvedValueOnce({ data: liveInboxPayload });

    const inbox = await getCustomerIntelligenceInbox();

    expect(api.get).toHaveBeenCalledWith("/customer-intelligence/v1/inbox");
    expect(inbox.conversations[0].messages).toHaveLength(2);
});

test("uses explicit suggestion lifecycle endpoints and never calls a send route", async () => {
    api.post
        .mockResolvedValueOnce({ data: { status: "pending_approval" } })
        .mockResolvedValueOnce({ data: { status: "approved_locked" } });

    await createCustomerIntelligenceReplySuggestion("conversation/1");
    await reviewCustomerIntelligenceReplySuggestion({
        conversationId: "conversation/1",
        suggestionId: "suggestion/1",
        decision: "approve",
        text: "النص بعد مراجعة الموظف",
        version: 3,
    });

    expect(api.post).toHaveBeenNthCalledWith(
        1,
        "/customer-intelligence/v1/conversations/conversation%2F1/reply-suggestion",
        {},
    );
    expect(api.post).toHaveBeenNthCalledWith(
        2,
        "/customer-intelligence/v1/conversations/conversation%2F1/reply-suggestion/suggestion%2F1/review",
        {
            decision: "approve",
            version: 3,
            text: "النص بعد مراجعة الموظف",
        },
    );
    expect(api.post.mock.calls.flat().join(" ")).not.toContain("/send");
});
