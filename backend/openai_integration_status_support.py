"""Expose the existing OpenAI runtime connection in Integrations V2.

The integrations catalogue predates the Mezan AI analyzer, so OpenAI was not
represented and could be interpreted as disconnected. This compatibility layer
adds one secret-safe runtime card without reading or returning the API key.
"""
from __future__ import annotations

from typing import Any

from ai_provider_status import openai_runtime_status


def _capability(state: str, reason: str) -> dict[str, Any]:
    return {
        "state": state,
        "available": state == "available",
        "approval_required": state == "approval_required",
        "blocked_by_policy": state in {"approval_required", "planned"},
        "reason": reason,
    }


def openai_integration_card() -> dict[str, Any]:
    status = openai_runtime_status()
    connected = status["connected"]
    image_ready = status["images"]["ready"]
    current = ["runtime_configured"] if connected else []
    return {
        "provider": "openai",
        "name": "OpenAI",
        "name_ar": "OpenAI · ذكاء ميزان",
        "category": "ai",
        "connection_status": status["connection_status"],
        "connection_provenance": status["connection_provenance"],
        "source_mode": "runtime_environment",
        "accounts": ([{
            "mezan_integration_account_id": "openai-runtime",
            "provider": "openai",
            "external_account_id": None,
            "store_id": None,
            "ad_account_id": None,
            "display_name": "Mezan AI Runtime",
            "currency": None,
            "timezone": None,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "permissions": current,
            "capabilities": {},
            "last_sync_at": None,
            "data_delay_minutes": None,
            "health_score": 100,
            "source_mode": "runtime_environment",
        }] if connected else []),
        "permissions": {"current": current, "missing": [] if connected else ["runtime_configuration"], "unknown": False},
        "capabilities": {
            "analysis.generate": _capability("available" if connected else "not_connected", "محلل ميزان يستخدم اتصال OpenAI الحالي بالقراءة فقط." if connected else "إعداد OpenAI غير موجود في بيئة التشغيل."),
            "images.propose": _capability("available" if connected else "not_connected", "يمكن حفظ طلبات تعديل الصور كاقتراحات محكومة." if connected else "يتطلب اتصال OpenAI أولًا."),
            "images.execute": _capability("approval_required" if image_ready else "planned", "مزود الصور جاهز، لكن التنفيذ المباشر يبقى خلف الاعتماد البشري." if image_ready else "الاتصال قائم للتحليل؛ نموذج وسياسة تنفيذ الصور لم يكتمل تفعيلهما."),
        },
        "last_sync_at": None,
        "data_delay_minutes": None,
        "health": {"status": "healthy" if connected else "not_available", "score": 100 if connected else None, "checked_at": None, "data_quality": "good" if connected else "unavailable"},
        "latest_error": None,
        "ai": {
            "can": (["تحليل بيانات ميزان ضمن العقود الآمنة والقراءة فقط", "حفظ اقتراحات تعديل صور المنتجات داخل دورة الاعتماد"] if connected else []),
            "cannot": (["تنفيذ تعديل الصور قبل تفعيل نموذج وسياسة الصور", "النشر المباشر إلى سلة دون مسودة واعتماد وتحقق"] if connected else ["لا يوجد اتصال OpenAI في بيئة التشغيل."]),
        },
        "actions": {
            "test_connection": {"enabled": False, "reason": "حالة الاتصال مثبتة من بيئة التشغيل؛ اختبار الشبكة غير منفذ من مركز التكاملات.", "href": None},
            "reconnect": {"enabled": False, "reason": "يُدار الاتصال من أسرار بيئة النشر.", "href": None},
            "settings": {"enabled": False, "reason": "يُدار الاتصال من أسرار بيئة النشر.", "href": None},
            "disconnect": {"enabled": False, "reason": "إزالة الأسرار إجراء حساس ومحظور من هذه الصفحة.", "href": None},
        },
    }


def _recount(summary: dict[str, Any], providers: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(summary or {})
    result.update({
        "total": len(providers),
        "connected": sum(row.get("connection_status") == "connected" for row in providers),
        "api_connections": sum(row.get("connection_provenance") == "api_connection" for row in providers),
        "legacy_integrations": sum(row.get("connection_provenance") == "legacy_integration" for row in providers),
        "data_feeds": sum(row.get("connection_provenance") == "data_feed" for row in providers),
        "disconnected": sum(row.get("connection_provenance") == "disconnected" for row in providers),
        "planned": sum(row.get("connection_provenance") == "planned" for row in providers),
        "unknown": sum(row.get("connection_provenance") == "unknown" for row in providers),
        "healthy": sum((row.get("health") or {}).get("status") == "healthy" for row in providers),
        "missing_permissions": sum(bool((row.get("permissions") or {}).get("missing")) for row in providers),
        "attention_required": sum((row.get("health") or {}).get("status") in {"degraded", "unhealthy"} or row.get("connection_status") in {"needs_reauth", "expired", "error"} for row in providers),
    })
    return result


def install_openai_integration_status_support() -> None:
    from integrations_control_center import service as service_module

    cls = service_module.IntegrationsControlCenterService
    if getattr(cls, "_mezan_openai_status_installed", False): return
    original_overview = cls.overview
    original_capabilities = cls.capabilities

    async def overview(self, user_id: str) -> dict[str, Any]:
        result = await original_overview(self, user_id)
        providers = [row for row in (result.get("providers") or []) if row.get("provider") != "openai"]
        providers.append(openai_integration_card())
        result["providers"] = providers
        result["summary"] = _recount(result.get("summary") or {}, providers)
        return result

    async def capabilities(self, user_id: str) -> dict[str, Any]:
        result = await original_capabilities(self, user_id)
        providers = [row for row in (result.get("providers") or []) if row.get("provider") != "openai"]
        card = openai_integration_card()
        providers.append({
            "provider": card["provider"], "name": card["name"], "name_ar": card["name_ar"],
            "connection_status": card["connection_status"], "connection_provenance": card["connection_provenance"],
            "source_mode": card["source_mode"], "capabilities": card["capabilities"],
        })
        result["providers"] = providers
        return result

    cls.overview = overview
    cls.capabilities = capabilities
    cls._mezan_openai_status_installed = True
