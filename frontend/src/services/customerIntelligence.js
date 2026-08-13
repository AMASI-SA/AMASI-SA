import api from "../lib/api";

const READ_ONLY_KEYS = [
    "writes_allowed",
    "external_calls_allowed",
    "whatsapp_send_allowed",
    "instagram_send_allowed",
    "instagram_comment_reply_allowed",
    "order_creation_allowed",
    "discount_creation_allowed",
    "payment_link_creation_allowed",
    "product_mutation_allowed",
    "campaign_mutation_allowed",
    "ai_execution_allowed",
];

const LIVE_INBOX_MESSAGE_KINDS = new Set([
    "text",
    "image",
    "audio",
    "document",
    "interactive",
]);

const LIVE_INBOX_CONVERSATION_STATUSES = new Set([
    "open",
    "needs_human",
    "follow_up_due",
    "resolved",
    "closed",
]);

const LIVE_INBOX_EMPLOYEE_DELIVERY_STATES = new Set([
    "sent",
    "delivered",
    "read",
    "failed",
]);

const EMPTY_LIVE_INBOX = {
    schema_version: null,
    generated_at: null,
    mode: null,
    data_origin: null,
    connection: {
        provider: "whatsapp",
        status: "not_connected",
        connected_channels: 0,
        receiving_channels: 0,
    },
    connections: [],
    conversation_count: 0,
    message_count: 0,
    content_unavailable_count: 0,
    has_more: false,
    next_offset: null,
    conversations: [],
    safety_policy: {
        mode: "observe_only",
        receive_only: true,
        writes_allowed: false,
        whatsapp_send_allowed: false,
        instagram_send_allowed: false,
        instagram_comment_reply_allowed: false,
        ai_auto_reply_allowed: false,
        commerce_mutation_allowed: false,
    },
};

const EMPTY_SAFE_WORKSPACE = {
    schema_version: null,
    generated_at: null,
    mode: null,
    data_origin: null,
    workspace: {
        title_ar: "",
        title_en: "",
        description_ar: "",
        preview_notice_ar: "",
        owner_preview: false,
        operating_level: 0,
        operating_level_label: "",
        tabs: [],
        objections: [],
        campaign_impact: [],
        integrations: [],
    },
    overview: {
        metrics: [],
        alerts: [],
        potential_revenue_sar: null,
    },
    conversations: [],
    customer_profile: {
        id: "",
        display_name: "",
        identity_status: "",
        lifecycle: "",
        consent_status: "",
        labels: [],
        preferred_products: [],
        preferred_attributes: [],
        inferred_budget: "",
        purchase_probability: null,
        lifetime_value_sar: null,
        last_order_status: "",
        next_best_action: "",
        evidence: [],
    },
    follow_ups: [],
    sales_opportunities: [],
    product_opportunities: [],
    competitor_signals: [],
    approved_offers: [],
    conversation_cart: {
        id: "",
        status: "",
        customer_name: "",
        currency: "SAR",
        items: [],
        subtotal_sar: null,
        shipping_sar: null,
        total_sar: null,
        validation: {
            price_verified: false,
            stock_verified: false,
            variant_verified: false,
            customer_approved: false,
            employee_approved: false,
        },
        fake_payment_link: "",
        note: "",
    },
    knowledge: {
        entries: [],
        learning_policy: "",
    },
    quality: {
        metrics: [],
        recent_reviews: [],
    },
    audit_preview: [],
    safety_policy: {
        mode: "",
        preview_only: false,
        fixtures_are_synthetic: false,
        lifecycle_required_for_future_writes: [],
        blocked_reason_ar: "",
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

function isObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function object(value) {
    return isObject(value) ? value : {};
}

function array(value) {
    return Array.isArray(value) ? value : [];
}

function text(value) {
    if (typeof value === "string") return value.trim();
    if (typeof value === "number") return String(value);
    return "";
}

function finite(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function nonNegativeInteger(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric >= 0 ? Math.floor(numeric) : 0;
}

function optionalNonNegativeInteger(value) {
    if (value == null || value === "") return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) && numeric >= 0 ? Math.floor(numeric) : null;
}

function timestamp(value) {
    return typeof value === "string" && value.trim() ? value : null;
}

function normalizeLiveInboxMessage(message, index) {
    const source = object(message);
    const kind = text(source.kind);
    const employeeEcho = source.direction === "outbound" && source.sender === "employee";
    return {
        id: text(source.message_id || source.id) || `live-message-${index + 1}`,
        // An outbound employee echo is history received from a channel, not a
        // UI send capability and never a substitute for reply_suggestion.
        direction: employeeEcho ? "outbound" : "inbound",
        sender: employeeEcho ? "employee" : "customer",
        kind: LIVE_INBOX_MESSAGE_KINDS.has(kind) ? kind : "interactive",
        body: text(source.body),
        caption: text(source.caption),
        filename: text(source.filename),
        mime_type: text(source.mime_type),
        occurred_at: timestamp(source.occurred_at),
        delivery_state: employeeEcho ? text(source.delivery_state) : "received",
        surface: ["direct_message", "comment", "unknown"].includes(source.surface)
            ? source.surface
            : "direct_message",
        content_available: source.content_available === true,
    };
}

function normalizePendingReplySuggestion(value, conversationId) {
    if (!isObject(value)) return null;
    const source = object(value);
    const rawSuggestionId = source.suggestion_id ?? source.id;
    const suggestionId = typeof rawSuggestionId === "string"
        ? rawSuggestionId.trim()
        : "";
    const suggestionText = typeof source.text === "string" ? source.text.trim() : "";
    const version = Number.isInteger(source.version) && source.version >= 1
        ? source.version
        : 0;

    // A reply suggestion is a separate, explicit approval object. Never infer
    // one from an outbound/employee message echo or from arbitrary text fields.
    if (
        source.status !== "pending_approval"
        || source.requires_human_approval !== true
        || source.send_allowed !== false
        || !suggestionId
        || !suggestionText
        || version < 1
    ) {
        return null;
    }

    return {
        id: suggestionId,
        conversation_id: conversationId,
        status: "pending_approval",
        text: suggestionText,
        version,
        requires_human_approval: true,
        send_allowed: false,
        surface: ["direct_message", "comment", "unknown"].includes(source.surface)
            ? source.surface
            : "direct_message",
        created_at: timestamp(source.created_at),
    };
}

function normalizeLiveInboxConversation(conversation, index) {
    const source = object(conversation);
    const status = text(source.status);
    const messages = array(source.messages)
        .filter((message) => {
            const row = object(message);
            const inboundCustomer = row.direction === "inbound"
                && row.sender === "customer"
                && row.delivery_state === "received";
            const outboundEmployee = row.direction === "outbound"
                && row.sender === "employee"
                && LIVE_INBOX_EMPLOYEE_DELIVERY_STATES.has(row.delivery_state);
            return inboundCustomer || outboundEmployee;
        })
        .map(normalizeLiveInboxMessage);
    const id = text(source.conversation_id || source.id) || `live-conversation-${index + 1}`;
    const channel = ["whatsapp", "instagram"].includes(source.channel)
        ? source.channel
        : "whatsapp";
    const surface = ["direct_message", "comment", "unknown"].includes(source.surface)
        ? source.surface
        : (messages[messages.length - 1]?.surface || "direct_message");
    return {
        id,
        customer_name: text(source.customer_name)
            || (channel === "instagram" ? "عميل إنستغرام" : "عميل واتساب"),
        channel,
        surface,
        status: LIVE_INBOX_CONVERSATION_STATUSES.has(status) ? status : "open",
        last_message: text(source.last_message),
        last_message_at: timestamp(source.last_message_at),
        message_count: nonNegativeInteger(source.message_count),
        content_unavailable_count: nonNegativeInteger(
            source.content_unavailable_count,
        ),
        messages,
        reply_suggestion: normalizePendingReplySuggestion(source.reply_suggestion, id),
    };
}

function normalizeMessage(message, index) {
    const source = object(message);
    const media = object(source.media_analysis);
    const kind = text(source.kind || source.type) || "text";
    return {
        id: text(source.message_id || source.id) || `message-${index + 1}`,
        type: kind,
        direction: text(source.direction)
            || (source.sender === "customer" ? "inbound" : "outbound"),
        body: text(source.text || source.body),
        duration_seconds: finite(source.duration_seconds),
        transcript: text(source.transcript || media.transcript),
        caption: text(source.caption)
            || (kind === "image" ? text(source.text) : ""),
        analysis: text(source.analysis || media.summary_ar),
        confidence: finite(source.confidence ?? media.confidence),
        occurred_at: source.occurred_at || null,
        is_fixture: media.is_fixture === true,
        fixture_asset_key: text(media.fixture_asset_key),
    };
}

function normalizeConversation(conversation, index) {
    const source = object(conversation);
    const messages = array(source.messages).map(normalizeMessage);
    const conversationId = text(source.conversation_id || source.id)
        || `conversation-${index + 1}`;
    const replySuggestion = normalizePendingReplySuggestion(
        source.reply_suggestion,
        conversationId,
    );
    return {
        id: conversationId,
        customer_id: text(source.customer_id),
        customer_name: text(source.customer_label || source.customer_name),
        channel: text(source.channel),
        status: text(source.state || source.status),
        intent: text(source.intent),
        objection: text(source.objection),
        sentiment: text(source.sentiment),
        confidence: finite(source.confidence),
        assigned_to: text(source.assigned_to),
        unread_count: finite(source.unread_count) ?? 0,
        ai_summary: text(source.ai_summary || source.last_message),
        next_best_action: text(source.next_best_action)
            || (source.assigned_to ? `مراجعة بواسطة: ${text(source.assigned_to)}` : ""),
        suggested_reply: replySuggestion?.text || "",
        reply_suggestion: replySuggestion,
        last_message_at: source.last_message_at || null,
        messages,
    };
}

function conversationCustomer(conversations, conversationId) {
    return conversations.find((row) => row.id === conversationId)?.customer_name || "";
}

function normalizeOverview(value) {
    const source = object(value);
    if (!Object.keys(source).length) {
        return { ...EMPTY_SAFE_WORKSPACE.overview };
    }
    const metrics = array(source.metrics);
    return {
        metrics: metrics.length ? metrics : [
            {
                key: "open_conversations",
                label: "محادثات مفتوحة",
                value: finite(source.open_conversations) ?? 0,
                hint: "بيانات مصطنعة من مساحة المعاينة",
                tone: "emerald",
            },
            {
                key: "needs_human_review",
                label: "تحتاج مراجعة بشرية",
                value: finite(source.needs_human_review) ?? 0,
                hint: "لا يوجد تنفيذ آلي",
                tone: "amber",
            },
            {
                key: "follow_ups_due",
                label: "متابعات مقترحة",
                value: finite(source.follow_ups_due) ?? 0,
                hint: "غير مجدولة فعليًا",
                tone: "violet",
            },
            {
                key: "sales_opportunities",
                label: "فرص بيع",
                value: finite(source.sales_opportunities) ?? 0,
                hint: "اقتراحات للمراجعة",
                tone: "blue",
            },
            {
                key: "product_opportunities",
                label: "فرص منتجات",
                value: finite(source.product_opportunities) ?? 0,
                hint: "لا تنشئ منتجات",
                tone: "slate",
            },
        ],
        alerts: array(source.alerts),
        potential_revenue_sar: finite(source.potential_revenue_sar),
    };
}

function normalizeCustomerProfile(value) {
    const source = object(value);
    return {
        id: text(source.customer_id || source.id),
        display_name: text(source.customer_label || source.display_name),
        identity_status: source.facts_are_synthetic === true ? "synthetic" : "",
        lifecycle: text(source.lifecycle_stage || source.lifecycle),
        consent_status: text(source.contact_consent || source.consent_status),
        labels: array(source.classifications || source.labels),
        preferred_products: array(source.preferred_products),
        preferred_attributes: array(source.preferred_colors || source.preferred_attributes),
        inferred_budget: text(source.budget_range_sar || source.inferred_budget),
        purchase_probability: finite(source.purchase_probability),
        lifetime_value_sar: finite(source.lifetime_value_sar),
        last_order_status: text(source.last_order_status),
        next_best_action: text(source.next_best_action),
        evidence: array(source.evidence),
    };
}

function normalizeFollowUps(value, conversations) {
    return array(value).map((item, index) => {
        const source = object(item);
        const conversationId = text(source.conversation_id);
        return {
            id: text(source.follow_up_id || source.id) || `follow-up-${index + 1}`,
            conversation_id: conversationId,
            customer_name: text(source.customer_name)
                || conversationCustomer(conversations, conversationId),
            due_label: text(source.due_label || source.due_at),
            reason: text(source.reason),
            channel: text(source.channel) || "whatsapp_mock",
            status: text(source.status),
            attempts_allowed: source.execution_allowed === true
                ? finite(source.attempts_allowed) ?? 1
                : 0,
            proposed_message: text(source.proposed_message),
        };
    });
}

function normalizeSalesOpportunities(value, conversations) {
    return array(value).map((item, index) => {
        const source = object(item);
        const conversationId = text(source.conversation_id);
        const score = finite(source.score);
        return {
            id: text(source.opportunity_id || source.id) || `sale-${index + 1}`,
            customer_name: text(source.customer_name)
                || conversationCustomer(conversations, conversationId),
            product: text(source.product || source.title),
            stage: text(source.stage),
            probability: score == null ? finite(source.probability) : score / 100,
            estimated_value_sar: finite(source.estimated_value_sar),
            blocker: text(source.blocker || source.reason),
            next_step: text(source.next_step || source.next_best_action),
        };
    });
}

function recommendationLabel(value) {
    const labels = {
        ignore: "تجاهل",
        monitor: "مراقبة",
        request_sample: "طلب عينة بعد المراجعة",
        review_for_store: "مراجعة للإضافة إلى المتجر",
    };
    return labels[value] || text(value);
}

function normalizeProductOpportunities(value) {
    return array(value).map((item, index) => {
        const source = object(item);
        const confidence = finite(source.confidence);
        const evidence = array(source.evidence_examples);
        return {
            id: text(source.opportunity_id || source.id) || `product-opportunity-${index + 1}`,
            title: text(source.title),
            status: text(source.stage || source.status),
            demand_score: finite(source.demand_score)
                ?? (confidence == null ? null : Math.round(confidence * 100)),
            confidence,
            distinct_customers: finite(
                source.unique_customers ?? source.distinct_customers,
            ) ?? 0,
            mentions: finite(source.request_count ?? source.mentions) ?? 0,
            reason: text(source.reason) || evidence.join(" · "),
            recommendation: recommendationLabel(source.recommendation),
            similar_products: array(
                source.closest_store_products || source.similar_products,
            ),
        };
    });
}

function normalizeCompetitorSignals(value) {
    return array(value).map((item, index) => {
        const source = object(item);
        const mentions = finite(source.mention_count ?? source.mentions) ?? 0;
        return {
            id: text(source.signal_id || source.id) || `competitor-${index + 1}`,
            display_name: text(source.name || source.display_name),
            status: text(source.status),
            mentions,
            linked_product: text(source.linked_product),
            confidence: finite(source.confidence),
            evidence: text(source.evidence)
                || `ظهر ${mentions} مرات في بيانات مصطنعة؛ لا يكفي لاعتماده منافسًا.`,
            next_step: source.external_research_allowed === true
                ? "يتطلب موافقة منفصلة قبل البحث الخارجي."
                : "البحث الخارجي مقفل؛ اجمع إشارات إضافية ثم راجع.",
        };
    });
}

function normalizeApprovedOffers(value) {
    return array(value).map((item, index) => {
        const source = object(item);
        const margin = finite(source.expected_margin_impact_sar);
        return {
            id: text(source.offer_id || source.id) || `offer-${index + 1}`,
            name: text(source.label_ar || source.name),
            type: text(source.offer_type || source.type),
            status: text(source.approval_state || source.status),
            eligibility: text(source.reason || source.eligibility),
            value: text(source.value),
            margin_effect: margin == null ? "" : `أثر هامش تجريبي: ${margin.toFixed(2)} ر.س`,
        };
    });
}

function normalizeConversationCart(value, conversations) {
    const source = object(value);
    const paymentLink = object(source.payment_link);
    const conversationId = text(source.conversation_id);
    return {
        id: text(source.draft_id || source.id),
        status: text(source.status),
        customer_name: text(source.customer_name)
            || conversationCustomer(conversations, conversationId),
        currency: text(source.currency) || "SAR",
        items: array(source.items).map((item, index) => {
            const row = object(item);
            return {
                id: text(row.product_id || row.id) || `cart-item-${index + 1}`,
                product_name: text(row.title || row.product_name),
                variant: text(row.variant),
                quantity: finite(row.quantity) ?? 0,
                unit_price_sar: finite(row.unit_price_sar),
                source_status: text(row.source_verification || row.source_status),
            };
        }),
        subtotal_sar: finite(source.subtotal_sar),
        shipping_sar: finite(source.shipping_sar),
        total_sar: finite(source.total_sar),
        validation: {
            price_verified: source.price_verified_from_source === true,
            stock_verified: source.inventory_verified_from_source === true,
            variant_verified: source.variant_verified_from_source === true,
            customer_approved: source.customer_confirmed === true,
            employee_approved: source.employee_approved === true,
        },
        fake_payment_link: text(source.fake_payment_link || paymentLink.url),
        note: text(source.note || paymentLink.label_ar),
    };
}

function normalizeKnowledge(value) {
    const source = object(value);
    const entries = array(source.entries);
    const suggested = array(source.suggested_articles);
    return {
        entries: entries.length ? entries : suggested.map((title, index) => ({
            id: `knowledge-${index + 1}`,
            title: text(title),
            status: source.status === "proposal_only" ? "proposed" : text(source.status),
            source: "اقتراح Backend مصطنع",
            body: "عنوان معرفة مقترح؛ لا يُنشر قبل المراجعة والاعتماد.",
        })),
        learning_policy: text(source.learning_policy)
            || (source.publication_allowed === false
                ? "لا تتحول المحادثات إلى معرفة منشورة تلقائيًا؛ تمر باقتراح ومراجعة واعتماد."
                : ""),
    };
}

function qualityMetric(key, label, value, hint) {
    const numeric = finite(value);
    return {
        key,
        label,
        value: numeric,
        display: numeric == null ? "غير مقاس" : `${numeric.toFixed(0)}%`,
        hint,
    };
}

function normalizeQuality(value) {
    const source = object(value);
    if (!Object.keys(source).length) {
        return { ...EMPTY_SAFE_WORKSPACE.quality };
    }
    const metrics = array(source.metrics);
    return {
        metrics: metrics.length ? metrics : [
            qualityMetric(
                "reply_acceptance",
                "قبول الردود",
                source.suggested_reply_acceptance_pct,
                "مؤشر مصطنع للمعاينة.",
            ),
            qualityMetric(
                "human_escalation",
                "تصعيد بشري",
                source.human_escalation_pct,
                "مؤشر مصطنع للمعاينة.",
            ),
            qualityMetric(
                "paid_conversion",
                "تحويل مدفوع",
                source.paid_order_conversion_pct,
                "ليس ناتج مبيعات حقيقيًا.",
            ),
            {
                key: "policy_violations",
                label: "مخالفات السياسة",
                value: finite(source.detected_policy_violations),
                display: finite(source.detected_policy_violations) == null
                    ? "غير مقاس"
                    : String(source.detected_policy_violations),
                hint: "محاكاة جودة فقط.",
            },
        ],
        recent_reviews: array(source.recent_reviews),
    };
}

function normalizeAudit(value) {
    return array(value).map((item, index) => {
        const source = object(item);
        return {
            id: text(source.decision_id || source.id) || `audit-${index + 1}`,
            action: text(source.proposed_action || source.action),
            actor: text(source.actor) || "Mezan Intelligence Preview",
            result: text(source.execution_status || source.result),
            source: text(source.source) || array(source.evidence_refs).join(", "),
            occurred_at: source.occurred_at || null,
        };
    });
}

function normalizeSafetyPolicy(value) {
    const source = object(value);
    const normalized = {
        mode: "observe_only",
        preview_only: true,
        fixtures_are_synthetic: source.fixtures_are_synthetic === true,
        lifecycle_required_for_future_writes: array(
            source.lifecycle_required_for_future_writes,
        ),
        blocked_reason_ar: text(source.blocked_reason_ar),
    };
    READ_ONLY_KEYS.forEach((key) => {
        // Phase 1 is a rendering-only surface. Never trust an input payload to
        // enable a write capability, even if a malformed server claims true.
        normalized[key] = false;
    });
    return normalized;
}

export function normalizeCustomerIntelligenceWorkspace(payload = {}) {
    const source = object(payload);
    const workspace = object(source.workspace);
    const conversations = array(source.conversations).map(normalizeConversation);

    return {
        ...EMPTY_SAFE_WORKSPACE,
        schema_version: Number.isFinite(Number(source.schema_version))
            ? Number(source.schema_version)
            : null,
        generated_at: source.generated_at || null,
        mode: text(source.mode),
        data_origin: text(source.data_origin),
        workspace: {
            ...EMPTY_SAFE_WORKSPACE.workspace,
            title_ar: text(workspace.title_ar),
            title_en: text(workspace.title_en),
            description_ar: text(workspace.description_ar),
            preview_notice_ar: text(workspace.preview_notice_ar),
            owner_preview: workspace.owner_preview === true,
            operating_level: finite(workspace.operating_level) ?? 0,
            operating_level_label: text(workspace.operating_level_label),
            tabs: array(workspace.tabs),
            objections: array(workspace.objections),
            campaign_impact: array(workspace.campaign_impact),
            integrations: array(workspace.integrations),
        },
        overview: normalizeOverview(source.overview),
        conversations,
        customer_profile: normalizeCustomerProfile(source.customer_profile),
        follow_ups: normalizeFollowUps(source.follow_ups, conversations),
        sales_opportunities: normalizeSalesOpportunities(
            source.sales_opportunities,
            conversations,
        ),
        product_opportunities: normalizeProductOpportunities(source.product_opportunities),
        competitor_signals: normalizeCompetitorSignals(source.competitor_signals),
        approved_offers: normalizeApprovedOffers(source.approved_offers),
        conversation_cart: normalizeConversationCart(
            source.conversation_cart,
            conversations,
        ),
        knowledge: normalizeKnowledge(source.knowledge),
        quality: normalizeQuality(source.quality),
        audit_preview: normalizeAudit(source.audit_preview),
        safety_policy: normalizeSafetyPolicy(source.safety_policy),
    };
}

export function customerIntelligenceWritesLocked(policy = {}) {
    const normalized = normalizeSafetyPolicy(policy);
    return normalized.preview_only
        && normalized.mode === "observe_only"
        && READ_ONLY_KEYS.every((key) => normalized[key] === false);
}

export async function getCustomerIntelligenceWorkspace() {
    const response = await api.get("/customer-intelligence/v1/workspace");
    return normalizeCustomerIntelligenceWorkspace(response.data);
}

export function normalizeCustomerIntelligenceInbox(payload = {}) {
    const source = object(payload);
    const connection = object(source.connection);
    const conversations = array(source.conversations)
        .filter((conversation) => ["whatsapp", "instagram"].includes(
            object(conversation).channel,
        ))
        .map(normalizeLiveInboxConversation);
    const suppliedConnections = array(source.connections).length
        ? array(source.connections)
        : (connection.provider ? [connection] : []);
    const connections = suppliedConnections
        .filter((item) => ["whatsapp", "instagram"].includes(object(item).provider))
        .map((item) => {
            const row = object(item);
            const receivingChannels = nonNegativeInteger(row.receiving_channels);
            return {
                provider: row.provider,
                status: row.status === "connected" && receivingChannels > 0
                    ? "connected"
                    : "not_connected",
                connected_channels: nonNegativeInteger(row.connected_channels),
                receiving_channels: receivingChannels,
            };
        });
    const connectedChannels = connections.reduce(
        (total, item) => total + item.connected_channels,
        0,
    );
    const receivingChannels = connections.reduce(
        (total, item) => total + item.receiving_channels,
        0,
    );
    const supportedOrigin = ["whatsapp_webhook", "channel_webhooks"].includes(
        source.data_origin,
    );
    const liveContract = Number(source.schema_version) === 1
        && source.mode === "live_receive_only"
        && supportedOrigin
        && connections.length > 0;

    return {
        ...EMPTY_LIVE_INBOX,
        connection: {
            ...EMPTY_LIVE_INBOX.connection,
            status: liveContract && receivingChannels > 0
                ? "connected"
                : "not_connected",
            connected_channels: connectedChannels,
            receiving_channels: receivingChannels,
        },
        connections: liveContract ? connections : [],
        schema_version: Number(source.schema_version) === 1 ? 1 : null,
        generated_at: timestamp(source.generated_at),
        mode: liveContract ? "live_receive_only" : null,
        data_origin: liveContract ? source.data_origin : null,
        conversation_count: liveContract ? nonNegativeInteger(source.conversation_count) : 0,
        message_count: liveContract ? nonNegativeInteger(source.message_count) : 0,
        content_unavailable_count: liveContract
            ? nonNegativeInteger(source.content_unavailable_count)
            : 0,
        has_more: liveContract && source.has_more === true,
        next_offset: liveContract && source.has_more === true
            ? optionalNonNegativeInteger(source.next_offset)
            : null,
        conversations: liveContract ? conversations : [],
        // These values are intentionally client-enforced, like the preview
        // policy above. A malformed response cannot enable a write control.
        safety_policy: { ...EMPTY_LIVE_INBOX.safety_policy },
    };
}

export async function getCustomerIntelligenceInbox() {
    const response = await api.get("/customer-intelligence/v1/inbox");
    return normalizeCustomerIntelligenceInbox(response.data);
}

const CUSTOMER_LEARNING_STATES = new Set([
    "not_configured",
    "no_data",
    "healthy",
    "processing",
    "attention_required",
]);

export function normalizeCustomerLearningStatus(payload = {}) {
    const source = object(payload);
    return {
        schema_version: Number(source.schema_version) === 1 ? 1 : null,
        generated_at: timestamp(source.generated_at),
        state: CUSTOMER_LEARNING_STATES.has(source.state)
            ? source.state
            : "not_configured",
        runtime_configured: source.runtime_configured === true,
        worker_enabled: source.worker_enabled === true,
        inbound_customer_messages: nonNegativeInteger(source.inbound_customer_messages),
        employee_responses: nonNegativeInteger(source.employee_responses),
        total_evidence_events: nonNegativeInteger(source.total_evidence_events),
        queued_for_analysis: nonNegativeInteger(source.queued_for_analysis),
        analyzed_messages: nonNegativeInteger(source.analyzed_messages),
        pending_messages: nonNegativeInteger(source.pending_messages),
        failed_messages: nonNegativeInteger(source.failed_messages),
        queue_coverage_percent: Math.min(100, finite(source.queue_coverage_percent) ?? 0),
        analysis_completion_percent: Math.min(
            100,
            finite(source.analysis_completion_percent) ?? 0,
        ),
        signals_detected: nonNegativeInteger(source.signals_detected),
        open_problems: nonNegativeInteger(source.open_problems),
        proposed_decisions: nonNegativeInteger(source.proposed_decisions),
        metadata_only_media_events: nonNegativeInteger(source.metadata_only_media_events),
        // Status is content-free and cannot grant execution authority.
        customer_content_exposed: false,
        automatic_execution_allowed: false,
    };
}

export async function getCustomerLearningStatus() {
    const response = await api.get("/customer-intelligence/v1/learning/status");
    return normalizeCustomerLearningStatus(response.data);
}

const INSTAGRAM_SETUP_STATES = new Set([
    "ready",
    "connected",
    "meta_reauthorization_required",
    "no_instagram_account",
    "store_not_ready",
]);

export function normalizeInstagramCustomerIntelligenceSetup(payload = {}) {
    const source = object(payload);
    const state = INSTAGRAM_SETUP_STATES.has(source.state) ? source.state : null;
    return {
        schema_version: Number(source.schema_version) === 1 ? 1 : null,
        state,
        candidates: state === "ready"
            ? array(source.candidates).map((candidate) => {
                const row = object(candidate);
                return {
                    candidate_ref: text(row.candidate_ref),
                    display_name: text(row.display_name) || "حساب إنستغرام",
                };
            }).filter((candidate) => candidate.candidate_ref)
            : [],
        required_permissions_ready: source.required_permissions_ready === true,
        receive_only: true,
        send_allowed: false,
        comment_reply_allowed: false,
        ai_auto_reply_allowed: false,
    };
}

export async function getInstagramCustomerIntelligenceSetup() {
    const response = await api.get(
        "/customer-intelligence/v1/channels/instagram/setup",
    );
    return normalizeInstagramCustomerIntelligenceSetup(response.data);
}

export async function connectInstagramCustomerIntelligence(candidateRef) {
    const candidate = text(candidateRef);
    if (!candidate) throw new Error("instagram candidate is required");
    const response = await api.post(
        "/customer-intelligence/v1/channels/instagram/setup",
        {
            candidate_ref: candidate,
            confirmation: "CONNECT_RECEIVE_ONLY_INSTAGRAM",
        },
    );
    return {
        status: ["connected", "no_change"].includes(response.data?.status)
            ? response.data.status
            : null,
        provider: "instagram",
        receive_only: true,
        send_allowed: false,
        comment_reply_allowed: false,
        ai_auto_reply_allowed: false,
    };
}

function replySuggestionPath(conversationId) {
    const id = text(conversationId);
    if (!id) throw new Error("conversation_id is required");
    return `/customer-intelligence/v1/conversations/${encodeURIComponent(id)}/reply-suggestion`;
}

export async function createCustomerIntelligenceReplySuggestion(conversationId) {
    const response = await api.post(replySuggestionPath(conversationId), {});
    return response.data;
}

export async function reviewCustomerIntelligenceReplySuggestion({
    conversationId,
    suggestionId,
    decision,
    text: reviewedText = "",
    version,
    note = "",
}) {
    const id = text(suggestionId);
    const allowedDecision = ["approve", "reject", "escalate"].includes(decision);
    if (!id) throw new Error("suggestion_id is required");
    if (!allowedDecision) throw new Error("unsupported reply suggestion decision");
    const normalizedVersion = nonNegativeInteger(version);
    if (normalizedVersion < 1) throw new Error("suggestion version is required");

    const response = await api.post(
        `${replySuggestionPath(conversationId)}/${encodeURIComponent(id)}/review`,
        {
            decision,
            version: normalizedVersion,
            ...(decision === "approve" && text(reviewedText)
                ? { text: text(reviewedText) }
                : {}),
            ...(text(note) ? { note: text(note) } : {}),
        },
    );
    return response.data;
}

export const CUSTOMER_INTELLIGENCE_WRITE_POLICY_KEYS = [...READ_ONLY_KEYS];
