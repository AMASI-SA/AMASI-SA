import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";

let mockAuthUser = { is_owner: true, permissions: [] };
let mockSearchParams = new URLSearchParams();
const mockSetSearchParams = jest.fn();

jest.mock("react-router-dom", () => ({
    useSearchParams: () => [mockSearchParams, mockSetSearchParams],
}), { virtual: true });

jest.mock("../context/AuthContext", () => ({
    useAuth: () => ({ user: mockAuthUser, loading: false }),
}));

jest.mock("../lib/api", () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
    },
}));

import {
    CUSTOMER_INTELLIGENCE_TABS,
    CustomerIntelligenceCenterView,
    default as CustomerIntelligenceCenter,
} from "./CustomerIntelligenceCenter";
import api from "../lib/api";

const previewPayload = {
    schema_version: 1,
    generated_at: "2026-07-28T18:00:00Z",
    mode: "preview_fixture",
    data_origin: "synthetic",
    workspace: {
        title_ar: "مركز ذكاء العملاء والمبيعات",
        title_en: "Customer Intelligence & Sales Center",
        description_ar: "معاينة آمنة من Backend.",
        preview_notice_ar: "لا تنفيذ حقيقي.",
        owner_preview: true,
        operating_level: 1,
        operating_level_label: "اقتراح ومراجعة بشرية",
        tabs: [],
        objections: [{
            id: "objection-1",
            label: "السعر",
            count: 1,
            trend: "متكرر في المعاينة",
            evidence: "اعتراض مصطنع.",
            recommendation: "مراجعة القيمة.",
        }],
        campaign_impact: [{
            id: "campaign-1",
            campaign_name: "حملة تجريبية",
            source: "synthetic_preview",
            conversations: 2,
            qualified: 1,
            paid_orders: 0,
            top_objection: "السعر",
            data_quality: "preview_only",
        }],
        integrations: [{
            id: "whatsapp-mock",
            name: "WhatsApp Business",
            status: "mock_provider",
            detail: "لا يرسل ولا يستقبل.",
        }],
    },
    overview: {
        open_conversations: 2,
        needs_human_review: 1,
        follow_ups_due: 1,
        sales_opportunities: 1,
        product_opportunities: 1,
        potential_revenue_sar: 249,
    },
    conversations: [
        {
            conversation_id: "conversation-1",
            customer_label: "عميلة تجريبية",
            channel: "whatsapp_mock",
            state: "needs_reply",
            intent: "شراء هدية",
            objection: "السعر",
            confidence: 0.91,
            assigned_to: "فريق خدمة العملاء",
            last_message: "أبغى اللون الكحلي.",
            messages: [
                {
                    message_id: "text-1",
                    sender: "customer",
                    kind: "text",
                    text: "أبغى اللون الكحلي.",
                },
                {
                    message_id: "audio-1",
                    sender: "customer",
                    kind: "audio",
                    media_analysis: {
                        transcript: "بطلبه الخميس.",
                        summary_ar: "موعد شراء لاحق.",
                        confidence: 0.93,
                        is_fixture: true,
                    },
                },
                {
                    message_id: "reply-1",
                    sender: "assistant",
                    kind: "text",
                    text: "رد مقترح فقط.",
                },
            ],
        },
        {
            conversation_id: "conversation-2",
            customer_label: "عميلة تجريبية 2",
            channel: "whatsapp_mock",
            state: "human_review",
            intent: "مطابقة صورة",
            confidence: 0.76,
            assigned_to: "مراجعة بشرية",
            last_message: "هل يوجد منتج قريب؟",
            messages: [{
                message_id: "image-1",
                sender: "customer",
                kind: "image",
                text: "هل يوجد منتج قريب؟",
                media_analysis: {
                    summary_ar: "مطابقة غير مؤكدة.",
                    confidence: 0.76,
                    is_fixture: true,
                },
            }],
        },
    ],
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
        next_best_action: "مراجعة بشرية.",
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
        evidence_refs: ["text-1"],
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
    },
    conversation_count: 2,
    message_count: 4,
    content_unavailable_count: 1,
    has_more: false,
    next_offset: null,
    conversations: [{
        conversation_id: "conversation-live-1",
        customer_id: "customer-live-1",
        customer_name: "عميل واتساب",
        channel: "whatsapp",
        status: "open",
        last_message: "اختبار ربط ميزان 2",
        last_message_at: "2026-08-12T00:29:00Z",
        message_count: 3,
        content_unavailable_count: 0,
        messages: [{
            message_id: "message-live-text",
            direction: "inbound",
            sender: "customer",
            kind: "text",
            body: "اختبار ربط ميزان 2",
            occurred_at: "2026-08-12T00:28:00Z",
            delivery_state: "received",
            content_available: true,
        }, {
            message_id: "message-live-employee-echo",
            direction: "outbound",
            sender: "employee",
            kind: "text",
            body: "رد الموظف السابق من تطبيق واتساب",
            occurred_at: "2026-08-12T00:28:30Z",
            delivery_state: "read",
            content_available: true,
        }, {
            message_id: "message-live-image",
            direction: "inbound",
            sender: "customer",
            kind: "image",
            caption: "صورة المنتج",
            mime_type: "image/jpeg",
            occurred_at: "2026-08-12T00:29:00Z",
            delivery_state: "received",
            content_available: true,
        }],
        reply_suggestion: {
            suggestion_id: "reply-suggestion-live-1",
            status: "pending_approval",
            text: "أهلًا بك، سأتأكد من توفر المنتج.",
            version: 1,
            requires_human_approval: true,
            send_allowed: false,
            created_at: "2026-08-12T00:29:30Z",
        },
    }, {
        conversation_id: "conversation-live-2",
        customer_id: "customer-live-2",
        customer_name: "عميل واتساب 2",
        channel: "whatsapp",
        status: "needs_human",
        last_message: "أحتاج مساعدة",
        last_message_at: "2026-08-11T22:00:00Z",
        message_count: 1,
        content_unavailable_count: 1,
        messages: [{
            message_id: "message-live-audio",
            direction: "inbound",
            sender: "customer",
            kind: "audio",
            mime_type: "audio/ogg",
            occurred_at: "2026-08-11T22:00:00Z",
            delivery_state: "received",
            content_available: true,
        }],
    }],
    safety_policy: {
        mode: "observe_only",
        receive_only: true,
        writes_allowed: false,
        whatsapp_send_allowed: false,
        ai_auto_reply_allowed: false,
        commerce_mutation_allowed: false,
    },
};

function renderTab(activeTab, props = {}) {
    return renderToStaticMarkup(
        <CustomerIntelligenceCenterView
            model={previewPayload}
            inbox={liveInboxPayload}
            activeTab={activeTab}
            {...props}
        />,
    );
}

beforeEach(() => {
    mockAuthUser = { is_owner: true, permissions: [] };
    mockSearchParams = new URLSearchParams();
    mockSetSearchParams.mockClear();
    api.get.mockReset();
});

test("renders the owner preview shell with exactly thirteen governed tabs", () => {
    const markup = renderTab("overview");

    expect(CUSTOMER_INTELLIGENCE_TABS).toHaveLength(13);
    expect(new Set(CUSTOMER_INTELLIGENCE_TABS.map((tab) => tab.id)).size).toBe(13);
    expect(markup.match(/data-testid="customer-intelligence-tab-/g) || []).toHaveLength(13);
    expect(markup).toContain("معاينة المالك");
    expect(markup).toContain('data-preview-only="true"');
    expect(markup).toContain('data-write-mode="observe_only"');
    CUSTOMER_INTELLIGENCE_TABS.forEach(({ id, label }) => {
        expect(markup).toContain(`data-testid="customer-intelligence-tab-${id}"`);
        expect(markup).toContain(label);
    });
});

test("forces a permitted employee into conversations without loading the owner workspace", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    mockAuthUser = {
        is_owner: false,
        permissions: ["customer_intelligence.inbox.read"],
    };
    mockSearchParams = new URLSearchParams("tab=overview");
    api.get.mockImplementation((path) => {
        if (path === "/customer-intelligence/v1/inbox") {
            return Promise.resolve({ data: liveInboxPayload });
        }
        return Promise.reject(new Error(`Unexpected owner request: ${path}`));
    });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(<CustomerIntelligenceCenter />);
            await Promise.resolve();
            await Promise.resolve();
        });

        expect(api.get).toHaveBeenCalledTimes(1);
        expect(api.get).toHaveBeenCalledWith("/customer-intelligence/v1/inbox");
        expect(container.querySelectorAll(
            '[data-testid^="customer-intelligence-tab-"]',
        )).toHaveLength(1);
        expect(container.querySelector(
            '[data-testid="customer-intelligence-tab-conversations"]',
        )).not.toBeNull();
        expect(container.querySelector(
            '[data-testid="customer-intelligence-tab-overview"]',
        )).toBeNull();
        expect(container.querySelector(
            '[data-testid="customer-intelligence-panel-conversations"]',
        )).not.toBeNull();
        expect(mockSetSearchParams).toHaveBeenCalledTimes(1);
        const [redirectedParams, options] = mockSetSearchParams.mock.calls[0];
        expect(redirectedParams.get("tab")).toBe("conversations");
        expect(options).toEqual({ replace: true });
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("shows a pending employee-approved suggestion while keeping WhatsApp send locked", () => {
    const markup = renderTab("conversations");

    expect(markup).toContain('data-preview-only="false"');
    expect(markup).toContain('data-live-inbox="true"');
    expect(markup).toContain('data-testid="customer-intelligence-live-conversation-list"');
    expect(markup).toContain('data-testid="customer-intelligence-live-message-stream"');
    expect(markup.match(/data-testid="customer-intelligence-live-message"/g) || []).toHaveLength(3);
    expect(markup).toContain("اختبار ربط ميزان 2");
    expect(markup).toContain("صورة المنتج");
    expect(markup).toContain("رد الموظف من واتساب");
    expect(markup).toContain("رد الموظف السابق من تطبيق واتساب");
    expect(markup).toContain('data-message-direction="outbound"');
    expect(markup).toContain("تمت القراءة");
    expect(markup).toContain("واتساب متصل ويستقبل الرسائل");
    expect(markup).toContain('data-testid="customer-intelligence-content-unavailable-warning"');
    expect(markup).toContain("تعذر عرض محتوى");
    expect(markup).not.toContain("WhatsApp وهمي");
    expect(markup).not.toContain("بيانات مصطنعة");
    expect(markup).toContain('data-testid="customer-intelligence-pending-reply-suggestion"');
    expect(markup).toContain("اقتراح رد من ذكاء ميزان");
    expect(markup).toContain("يحتاج اعتماد موظف");
    expect(markup).toContain("أهلًا بك، سأتأكد من توفر المنتج.");
    expect(markup).toContain('data-testid="customer-intelligence-approve-and-send"');
    expect(markup.match(/<button[^>]*data-testid="customer-intelligence-approve-and-send"[^>]*>/)?.[0])
        .toContain('disabled=""');
    expect(markup).toContain("اعتماد وإرسال — الإرسال مقفل حاليًا");
    expect(markup).toContain("<textarea");
    expect(markup).not.toContain("<form");
});

test("mobile inbox starts with the list, then offers a clear back control", () => {
    const markup = renderTab("conversations");

    expect(markup).toContain('data-testid="customer-intelligence-responsive-inbox"');
    expect(markup).toMatch(/<div class="block"[^>]*>.*?<section[^>]*data-testid="customer-intelligence-live-conversation-list"/);
    expect(markup).toMatch(/<div class="hidden lg:block"[^>]*>.*?<button[^>]*data-testid="customer-intelligence-back-to-conversations"/);
    expect(markup).toContain("رجوع إلى المحادثات");
    expect(markup).toContain("lg:grid-cols-[minmax(300px,.75fr)_minmax(0,1.25fr)]");
});

test("makes a truncated live message window explicit", () => {
    const truncatedInbox = {
        ...liveInboxPayload,
        conversations: [{
            ...liveInboxPayload.conversations[0],
            message_count: 45,
        }],
    };

    const markup = renderTab("conversations", { inbox: truncatedInbox });

    expect(markup).toContain('data-testid="customer-intelligence-message-window-notice"');
    expect(markup).toContain("يعرض أحدث");
    expect(markup).toContain("45");
});

test("renders a conversation cart and a visibly fake non-clickable payment URL", () => {
    const markup = renderTab("drafts");

    expect(markup).toContain('data-testid="customer-intelligence-conversation-cart"');
    expect(markup).toContain('data-testid="customer-intelligence-fake-payment-link"');
    expect(markup).toContain("https://payment-preview.example.invalid/draft-1");
    expect(markup).toContain("رابط دفع وهمي وغير قابل للفتح");
    expect(markup).not.toContain('href="https://payment-preview.example.invalid/draft-1"');
});

test("never renders mutation controls in any of the thirteen tabs", () => {
    CUSTOMER_INTELLIGENCE_TABS.forEach(({ id }) => {
        const markup = renderTab(id);
        const buttons = markup.match(/<button[\s\S]*?<\/button>/g) || [];

        buttons.forEach((button) => {
            expect(button).not.toMatch(/إرسال الآن|إنشاء طلب|إنشاء خصم|تطبيق الخصم|دفع الآن|تعديل حملة|تعديل منتج/);
        });
    });
});

test("shows an error without substituting local business fixtures", () => {
    const markup = renderToStaticMarkup(
        <CustomerIntelligenceCenterView
            model={{}}
            activeTab="overview"
            error="Backend unavailable"
        />,
    );

    expect(markup).toContain('data-testid="customer-intelligence-error"');
    expect(markup).toContain("لم تُعرض بيانات بديلة");
    expect(markup).not.toContain("عميلة تجريبية");
});

test("escapes live customer text and exposes no send or provider-secret fields", () => {
    const unsafeInbox = {
        ...liveInboxPayload,
        conversations: [{
            ...liveInboxPayload.conversations[0],
            customer_name: "<script>name</script>",
            messages: [{
                ...liveInboxPayload.conversations[0].messages[0],
                body: "<script>alert('x')</script>",
                provider_media_id: "provider-secret-1",
                content_ciphertext: "ciphertext-secret-1",
            }],
        }],
    };
    const markup = renderTab("conversations", { inbox: unsafeInbox });

    expect(markup).toContain("&lt;script&gt;alert(&#x27;x&#x27;)&lt;/script&gt;");
    expect(markup).not.toContain("<script>");
    expect(markup).not.toContain("provider-secret-1");
    expect(markup).not.toContain("ciphertext-secret-1");
    expect(markup.match(/<button[^>]*data-testid="customer-intelligence-approve-and-send"[^>]*>/)?.[0])
        .toContain('disabled=""');
    expect(markup).not.toContain("<form");
});

test("shows a connected empty state without synthetic conversation fallback", () => {
    const markup = renderTab("conversations", {
        inbox: {
            ...liveInboxPayload,
            conversation_count: 0,
            message_count: 0,
            content_unavailable_count: 0,
            conversations: [],
        },
    });

    expect(markup).toContain("لا توجد رسائل واردة حتى الآن");
    expect(markup).not.toContain("عميلة تجريبية");
});

test("shows a live inbox error without falling back to preview conversations", () => {
    const markup = renderTab("conversations", {
        inbox: {},
        inboxError: "Inbox unavailable",
    });

    expect(markup).toContain('data-testid="customer-intelligence-error"');
    expect(markup.match(/data-testid="customer-intelligence-error"/g) || []).toHaveLength(1);
    expect(markup).toContain("تعذر تحميل رسائل واتساب");
    expect(markup).toContain("لم تُعرض بيانات بديلة");
    expect(markup).not.toContain("عميلة تجريبية");
});

test("editing or pressing Enter never reviews or sends until the employee clicks review", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onReviewSuggestion = jest.fn().mockResolvedValue({});
    const onRejectSuggestion = jest.fn().mockResolvedValue({});
    const onEscalateSuggestion = jest.fn().mockResolvedValue({});

    try {
        await act(async () => {
            root.render(
                <CustomerIntelligenceCenterView
                    model={previewPayload}
                    inbox={liveInboxPayload}
                    activeTab="conversations"
                    onReviewSuggestion={onReviewSuggestion}
                    onRejectSuggestion={onRejectSuggestion}
                    onEscalateSuggestion={onEscalateSuggestion}
                />,
            );
        });

        const editor = container.querySelector(
            '[data-testid="customer-intelligence-reply-suggestion-editor"]',
        );
        await act(async () => {
            Object.getOwnPropertyDescriptor(
                HTMLTextAreaElement.prototype,
                "value",
            ).set.call(editor, "النص بعد تعديل الموظف\nسطر ثانٍ");
            editor.dispatchEvent(new Event("input", { bubbles: true }));
            editor.dispatchEvent(new KeyboardEvent("keydown", {
                key: "Enter",
                bubbles: true,
            }));
        });

        expect(onReviewSuggestion).not.toHaveBeenCalled();
        expect(onRejectSuggestion).not.toHaveBeenCalled();
        expect(onEscalateSuggestion).not.toHaveBeenCalled();
        expect(container.querySelector("form")).toBeNull();
        expect(container.querySelector(
            '[data-testid="customer-intelligence-approve-and-send"]',
        ).disabled).toBe(true);

        await act(async () => {
            container.querySelector('[data-testid="customer-intelligence-review-suggestion"]')
                .dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(onReviewSuggestion).toHaveBeenCalledTimes(1);
        expect(onReviewSuggestion).toHaveBeenCalledWith(
            "conversation-live-1",
            "reply-suggestion-live-1",
            { text: "النص بعد تعديل الموظف\nسطر ثانٍ", version: 1 },
        );
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("creates one AI suggestion only on an explicit employee click", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    let finishCreate;
    const onCreateSuggestion = jest.fn(() => new Promise((resolve) => {
        finishCreate = resolve;
    }));
    const withoutSuggestion = {
        ...liveInboxPayload,
        conversations: [{
            ...liveInboxPayload.conversations[0],
            reply_suggestion: null,
        }],
    };

    try {
        await act(async () => {
            root.render(
                <CustomerIntelligenceCenterView
                    model={previewPayload}
                    inbox={withoutSuggestion}
                    activeTab="conversations"
                    onCreateSuggestion={onCreateSuggestion}
                />,
            );
        });

        expect(onCreateSuggestion).not.toHaveBeenCalled();
        const createButton = container.querySelector(
            '[data-testid="customer-intelligence-create-suggestion"]',
        );
        await act(async () => {
            createButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        expect(onCreateSuggestion).toHaveBeenCalledTimes(1);
        expect(onCreateSuggestion).toHaveBeenCalledWith("conversation-live-1");
        expect(createButton.disabled).toBe(true);

        createButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        expect(onCreateSuggestion).toHaveBeenCalledTimes(1);

        await act(async () => finishCreate({ status: "pending_approval" }));
        expect(createButton.disabled).toBe(false);
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("conversation selection swaps list and thread on mobile, with a working back action", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    try {
        await act(async () => {
            root.render(
                <CustomerIntelligenceCenterView
                    model={previewPayload}
                    inbox={liveInboxPayload}
                    activeTab="conversations"
                />,
            );
        });

        const list = container.querySelector(
            '[data-testid="customer-intelligence-live-conversation-list"]',
        );
        const stream = container.querySelector(
            '[data-testid="customer-intelligence-live-message-stream"]',
        );
        expect(list.parentElement.className).toBe("block");
        expect(stream.parentElement.className).toBe("hidden lg:block");

        await act(async () => {
            container.querySelectorAll(
                '[data-testid="customer-intelligence-live-conversation"]',
            )[1].dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        expect(list.parentElement.className).toBe("hidden lg:block");
        expect(stream.parentElement.className).toBe("block");
        expect(stream.textContent).toContain("عميل واتساب 2");

        await act(async () => {
            container.querySelector(
                '[data-testid="customer-intelligence-back-to-conversations"]',
            ).dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        expect(list.parentElement.className).toBe("block");
        expect(stream.parentElement.className).toBe("hidden lg:block");
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("failed AI suggestion creation shows one local error and never sends", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    const onCreateSuggestion = jest.fn().mockRejectedValue(new Error("AI unavailable"));
    const withoutSuggestion = {
        ...liveInboxPayload,
        conversations: [{
            ...liveInboxPayload.conversations[0],
            reply_suggestion: null,
        }],
    };

    try {
        await act(async () => {
            root.render(
                <CustomerIntelligenceCenterView
                    model={previewPayload}
                    inbox={withoutSuggestion}
                    activeTab="conversations"
                    onCreateSuggestion={onCreateSuggestion}
                />,
            );
        });
        await act(async () => {
            container.querySelector('[data-testid="customer-intelligence-create-suggestion"]')
                .dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });

        expect(container.querySelectorAll(
            '[data-testid="customer-intelligence-create-suggestion-error"]',
        )).toHaveLength(1);
        expect(container.textContent).toContain("لم تُرسل أي رسالة إلى واتساب");
        expect(container.querySelector("form")).toBeNull();
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
