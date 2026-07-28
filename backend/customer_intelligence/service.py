"""Pure synthetic workspace builder for Customer Intelligence Phase 1.

The service receives no database handle, network client, WhatsApp provider,
commerce connector, discount service, or payment service.  It can therefore
only assemble deterministic preview data already present in this module.

This additive preview follows ADR-001 feature isolation, canonical contracts,
versioning, multi-tenant safety (no tenant data is read), and secrets
discipline.  Future real connectors must be introduced behind separate input
adapters and explicit approval/execution lifecycles.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable


MUTATION_LIFECYCLE = [
    "proposal",
    "preview",
    "approval",
    "execution",
    "verification",
    "audit",
    "rollback",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CustomerIntelligencePreviewService:
    """Build one server-owned, synthetic and side-effect-free preview."""

    def __init__(self, *, now: Callable[[], datetime] = _utcnow):
        self._now = now

    def workspace(self) -> dict:
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        conversations = [
            {
                "conversation_id": "demo-conversation-001",
                "customer_label": "عميلة تجريبية 01",
                "channel": "whatsapp_mock",
                "state": "needs_reply",
                "intent": "شراء هدية شخصية",
                "objection": "تريد التأكد من اللون وموعد الطلب",
                "sentiment": "hesitant",
                "confidence": 0.91,
                "assigned_to": "فريق خدمة العملاء",
                "last_message": "إذا توفر بالكحلي بطلبه بعد الراتب.",
                "last_message_at": now - timedelta(minutes=12),
                "unread_count": 2,
                "messages": [
                    {
                        "message_id": "demo-message-001",
                        "sender": "customer",
                        "kind": "text",
                        "text": "أبغى هدية باسم، هل اللون الكحلي متوفر؟",
                        "occurred_at": now - timedelta(minutes=18),
                        "media_analysis": None,
                    },
                    {
                        "message_id": "demo-message-002",
                        "sender": "customer",
                        "kind": "audio",
                        "text": None,
                        "occurred_at": now - timedelta(minutes=15),
                        "media_analysis": {
                            "media_type": "audio",
                            "fixture_asset_key": "demo-audio-gift-request",
                            "transcript": "إذا الراتب نزل الخميس بطلبه، بس ذكّروني.",
                            "summary_ar": "طلب متابعة يوم الخميس مع نية شراء مرتفعة.",
                            "confidence": 0.93,
                            "is_fixture": True,
                        },
                    },
                    {
                        "message_id": "demo-message-003",
                        "sender": "assistant",
                        "kind": "text",
                        "text": "رد مقترح فقط: أتأكد لك من اللون أولًا، ويمكننا تجهيز تذكير للمراجعة.",
                        "occurred_at": now - timedelta(minutes=12),
                        "media_analysis": None,
                    },
                ],
            },
            {
                "conversation_id": "demo-conversation-002",
                "customer_label": "عميلة تجريبية 02",
                "channel": "whatsapp_mock",
                "state": "human_review",
                "intent": "البحث عن منتج مشابه لصورة",
                "objection": "المنتج المطلوب غير مؤكد داخل المتجر",
                "sentiment": "neutral",
                "confidence": 0.76,
                "assigned_to": "مراجعة بشرية مطلوبة",
                "last_message": "عندكم شيء قريب من هذه الصورة؟",
                "last_message_at": now - timedelta(minutes=38),
                "unread_count": 1,
                "messages": [
                    {
                        "message_id": "demo-message-004",
                        "sender": "customer",
                        "kind": "image",
                        "text": "عندكم شيء قريب من هذه الصورة؟",
                        "occurred_at": now - timedelta(minutes=38),
                        "media_analysis": {
                            "media_type": "image",
                            "fixture_asset_key": "demo-image-navy-necklace",
                            "transcript": None,
                            "summary_ar": "سلسال بلون كحلي؛ المطابقة غير مؤكدة ويجب مراجعة المنتجات المشابهة.",
                            "confidence": 0.76,
                            "is_fixture": True,
                        },
                    }
                ],
            },
            {
                "conversation_id": "demo-conversation-003",
                "customer_label": "عميلة تجريبية 03",
                "channel": "whatsapp_mock",
                "state": "follow_up",
                "intent": "شراء قطعتين إذا توفر عرض",
                "objection": "السعر والشحن",
                "sentiment": "positive",
                "confidence": 0.88,
                "assigned_to": "فريق خدمة العملاء",
                "last_message": "لو فيه عرض على قطعتين أكمّل الطلب.",
                "last_message_at": now - timedelta(hours=2),
                "unread_count": 0,
                "messages": [
                    {
                        "message_id": "demo-message-005",
                        "sender": "customer",
                        "kind": "text",
                        "text": "لو فيه عرض على قطعتين أكمّل الطلب.",
                        "occurred_at": now - timedelta(hours=2),
                        "media_analysis": None,
                    }
                ],
            },
        ]

        return {
            "schema_version": 1,
            "generated_at": now,
            "mode": "preview_fixture",
            "data_origin": "synthetic",
            "workspace": {
                "title_ar": "مركز ذكاء العملاء والمبيعات",
                "title_en": "Customer Intelligence & Sales Center",
                "description_ar": "معاينة آمنة لكيف يحول ميزان المحادثات إلى فهم ومتابعات وفرص موثقة.",
                "preview_notice_ar": "جميع البيانات في هذه الصفحة تجريبية، ولا يوجد اتصال واتساب أو تنفيذ حقيقي.",
                "owner_preview": True,
                "operating_level": 1,
                "operating_level_label": "اقتراح ومراجعة بشرية",
                "tabs": [
                    {"key": "conversations", "label_ar": "المحادثات", "count": 3, "state": "preview"},
                    {"key": "customers", "label_ar": "العملاء", "count": 1, "state": "preview"},
                    {"key": "follow_ups", "label_ar": "المتابعات", "count": 2, "state": "preview"},
                    {"key": "sales", "label_ar": "فرص البيع", "count": 2, "state": "preview"},
                    {"key": "orders", "label_ar": "طلبات المحادثة", "count": 1, "state": "preview"},
                    {"key": "products", "label_ar": "فرص المنتجات", "count": 1, "state": "preview"},
                    {"key": "objections", "label_ar": "الاعتراضات والمشكلات", "count": 3, "state": "preview"},
                    {"key": "campaigns", "label_ar": "أثر الحملات", "count": 0, "state": "planned"},
                    {"key": "knowledge", "label_ar": "المعرفة والتعلم", "count": 2, "state": "preview"},
                    {"key": "quality", "label_ar": "الأداء والجودة", "count": 4, "state": "preview"},
                    {"key": "settings", "label_ar": "الإعدادات والتكاملات", "count": 0, "state": "planned"},
                    {"key": "audit", "label_ar": "سجل الإجراءات", "count": 1, "state": "preview"},
                ],
                "objections": [
                    {
                        "id": "demo-objection-price",
                        "label": "السعر",
                        "count": 1,
                        "trend": "متكرر في المعاينة",
                        "evidence": "طلبت عميلة عرضًا عند شراء قطعتين.",
                        "recommendation": "مراجعة القيمة والعرض المعتمد قبل اقتراح الخصم.",
                    },
                    {
                        "id": "demo-objection-shipping",
                        "label": "الشحن",
                        "count": 1,
                        "trend": "يحتاج تحققًا",
                        "evidence": "طلبت عميلة معرفة أثر الشحن على إجمالي القطعتين.",
                        "recommendation": "عدم الوعد بتكلفة أو موعد قبل ربط المصدر الرسمي.",
                    },
                    {
                        "id": "demo-objection-availability",
                        "label": "التوفر",
                        "count": 1,
                        "trend": "يحتاج مراجعة بشرية",
                        "evidence": "اللون الظاهر في الصورة غير مؤكد داخل المتجر.",
                        "recommendation": "مراجعة أقرب منتج وعدم ادعاء المطابقة.",
                    },
                ],
                "campaign_impact": [
                    {
                        "id": "demo-campaign-impact-001",
                        "campaign_name": "حملة تجريبية غير مرتبطة",
                        "source": "synthetic_preview",
                        "conversations": 3,
                        "qualified": 2,
                        "paid_orders": 0,
                        "top_objection": "السعر",
                        "data_quality": "preview_only",
                    }
                ],
                "integrations": [
                    {
                        "id": "demo-whatsapp-integration",
                        "name": "WhatsApp Business",
                        "status": "mock_provider",
                        "detail": "مزود وهمي للعرض؛ لا يستقبل ولا يرسل رسائل.",
                        "reads_allowed": False,
                        "writes_allowed": False,
                    },
                    {
                        "id": "demo-salla-integration",
                        "name": "سلة — قراءة المنتجات والطلبات",
                        "status": "not_connected_here",
                        "detail": "لا تُستخدم أي قراءة حقيقية في هذه المعاينة.",
                        "reads_allowed": False,
                        "writes_allowed": False,
                    },
                    {
                        "id": "demo-ai-integration",
                        "name": "Mezan Intelligence Core",
                        "status": "simulation",
                        "detail": "نتائج التحليل مصطنعة وليست استدعاءات AI حقيقية.",
                        "reads_allowed": False,
                        "writes_allowed": False,
                    },
                ],
            },
            "overview": {
                "open_conversations": 3,
                "needs_human_review": 1,
                "follow_ups_due": 2,
                "sales_opportunities": 2,
                "product_opportunities": 1,
                "potential_revenue_sar": 750.0,
            },
            "conversations": conversations,
            "customer_profile": {
                "customer_id": "demo-customer-001",
                "customer_label": "عميلة تجريبية 01",
                "lifecycle_stage": "جاهزة للشراء بعد موعد محدد",
                "classifications": [
                    "مستفسرة",
                    "نية شراء مرتفعة",
                    "تنتظر الراتب",
                    "تحتاج تأكيد اللون",
                ],
                "preferred_products": ["هدايا شخصية", "سلاسل بالاسم"],
                "preferred_colors": ["كحلي"],
                "budget_range_sar": "200–300",
                "purchase_probability": 0.82,
                "contact_consent": "unknown",
                "next_best_action": "يتحقق موظف من اللون ثم يعتمد أو يرفض متابعة الخميس.",
                "facts_are_synthetic": True,
            },
            "follow_ups": [
                {
                    "follow_up_id": "demo-follow-up-001",
                    "conversation_id": "demo-conversation-001",
                    "due_at": now + timedelta(days=2),
                    "reason": "العميلة قالت إنها ستطلب بعد الراتب.",
                    "proposed_message": "أهلًا، هذا تذكير بالقطعة التي أعجبتك. هل تحبين أن نراجع اللون والمقاس معك؟",
                    "status": "suggested_preview",
                    "execution_allowed": False,
                },
                {
                    "follow_up_id": "demo-follow-up-002",
                    "conversation_id": "demo-conversation-003",
                    "due_at": now + timedelta(hours=4),
                    "reason": "اعتراض سعر مع رغبة بشراء قطعتين.",
                    "proposed_message": "أراجع لك العروض المعتمدة للقطعتين وأرجع لك قبل أي تطبيق.",
                    "status": "suggested_preview",
                    "execution_allowed": False,
                },
            ],
            "sales_opportunities": [
                {
                    "opportunity_id": "demo-sale-001",
                    "conversation_id": "demo-conversation-001",
                    "title": "هدية شخصية بعد الراتب",
                    "stage": "suggested",
                    "score": 82,
                    "reason": "نية صريحة وموعد شراء محدد.",
                    "next_best_action": "تأكيد اللون ثم اعتماد متابعة واحدة.",
                },
                {
                    "opportunity_id": "demo-sale-002",
                    "conversation_id": "demo-conversation-003",
                    "title": "طلب قطعتين",
                    "stage": "detected",
                    "score": 74,
                    "reason": "العميلة مستعدة للشراء إذا توفر عرض معتمد.",
                    "next_best_action": "مراجعة عرض الكمية وتأثير الهامش.",
                },
            ],
            "product_opportunities": [
                {
                    "opportunity_id": "demo-product-opportunity-001",
                    "title": "سلسال كحلي بتخصيص الاسم",
                    "stage": "collecting",
                    "request_count": 6,
                    "unique_customers": 4,
                    "confidence": 0.78,
                    "evidence_examples": [
                        "هل يوجد نفس التصميم بالكحلي؟",
                        "أبغى سلسلة قريبة من الصورة مع اسم.",
                    ],
                    "closest_store_products": [
                        "سلسال الاسم مع وردة الأقحوان",
                        "سلسال هدية مخصص",
                    ],
                    "recommendation": "request_sample",
                    "product_creation_allowed": False,
                }
            ],
            "competitor_signals": [
                {
                    "signal_id": "demo-competitor-001",
                    "name": "متجر تجريبي مذكور",
                    "status": "potential_repeated",
                    "mention_count": 3,
                    "linked_product": "سلسال كحلي مخصص",
                    "confidence": 0.67,
                    "external_research_allowed": False,
                }
            ],
            "approved_offers": [
                {
                    "offer_id": "demo-offer-001",
                    "label_ar": "عرض قطعتين تجريبي",
                    "offer_type": "bundle",
                    "value": "خصم تجريبي 20 ر.س عند شراء قطعتين",
                    "reason": "اعتراض العميلة مرتبط بإجمالي قطعتين والشحن.",
                    "expected_margin_impact_sar": -20.0,
                    "approval_state": "demo_approved",
                    "application_allowed": False,
                }
            ],
            "conversation_cart": {
                "draft_id": "demo-cart-001",
                "conversation_id": "demo-conversation-001",
                "status": "preview_only",
                "items": [
                    {
                        "product_id": "demo-product-001",
                        "title": "سلسال اسم تجريبي",
                        "variant": "كحلي / اسم واحد",
                        "quantity": 1,
                        "unit_price_sar": 249.0,
                        "source_verification": "synthetic_unverified",
                    }
                ],
                "subtotal_sar": 249.0,
                "shipping_sar": 25.0,
                "discount_sar": 0.0,
                "total_sar": 274.0,
                "price_verified_from_source": False,
                "inventory_verified_from_source": False,
                "customer_confirmed": False,
                "create_order_allowed": False,
                "payment_link": {
                    "url": "https://payment-preview.example.invalid/demo-order-001",
                    "label_ar": "معاينة غير حقيقية لرابط الدفع",
                    "is_real": False,
                    "creation_allowed": False,
                },
            },
            "knowledge": {
                "status": "proposal_only",
                "suggested_articles": [
                    "متى نعد العميل بتوفر لون أو مقاس؟",
                    "كيف نستخدم العروض المعتمدة دون تجاوز هامش الربح؟",
                ],
                "publication_allowed": False,
            },
            "quality": {
                "measurement_mode": "synthetic",
                "suggested_reply_acceptance_pct": 84.0,
                "human_escalation_pct": 16.0,
                "paid_order_conversion_pct": 21.0,
                "detected_policy_violations": 0,
            },
            "audit_preview": [
                {
                    "decision_id": "demo-decision-001",
                    "observation": "العميلة حددت موعدًا لاحقًا للشراء وطلبت اللون الكحلي.",
                    "evidence_refs": [
                        "demo-message-001",
                        "demo-message-002",
                    ],
                    "confidence": 0.91,
                    "expected_impact": "زيادة احتمال إكمال الطلب دون تواصل مزعج.",
                    "risk": "medium",
                    "proposed_action": "اقتراح متابعة واحدة بعد تحقق الموظف من اللون.",
                    "required_approval": "employee",
                    "approval_status": "not_requested",
                    "execution_status": "not_executed",
                    "measured_outcome": "not_available",
                    "rollback_status": "not_applicable",
                }
            ],
            "safety_policy": {
                "mode": "observe_only",
                "preview_only": True,
                "fixtures_are_synthetic": True,
                "writes_allowed": False,
                "external_calls_allowed": False,
                "whatsapp_send_allowed": False,
                "order_creation_allowed": False,
                "discount_creation_allowed": False,
                "payment_link_creation_allowed": False,
                "product_mutation_allowed": False,
                "campaign_mutation_allowed": False,
                "ai_execution_allowed": False,
                "lifecycle_required_for_future_writes": MUTATION_LIFECYCLE,
                "blocked_reason_ar": "المرحلة الأولى معاينة تجريبية فقط ولا تحتوي أي موصل أو مسار تنفيذ.",
            },
        }
