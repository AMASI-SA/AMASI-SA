"""Secret-safe OpenAI runtime status shared across Mezan surfaces.

Connection means the production runtime has an OpenAI API key. Image execution
is a separate governed capability and must never make the core connection look
disconnected merely because its policy flag or image model is not enabled yet.
"""
from __future__ import annotations

import os
from typing import Any


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def openai_runtime_status() -> dict[str, Any]:
    connected = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    analysis_model = os.environ.get("MEZAN_OPENAI_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
    image_policy_enabled = _enabled("MEZAN_AI_IMAGE_ENABLED")
    image_model = os.environ.get("MEZAN_OPENAI_IMAGE_MODEL", "").strip() or None
    image_ready = connected and image_policy_enabled and bool(image_model)

    if not connected:
        state = "disconnected"
        label_ar = "OpenAI غير مربوط"
    elif image_ready:
        state = "connected_images_ready"
        label_ar = "OpenAI متصل والتحليل والصور جاهزان"
    else:
        state = "connected_analysis_only"
        label_ar = "OpenAI متصل للتحليل؛ تنفيذ الصور غير مفعل بعد"

    return {
        "provider": "openai",
        "connected": connected,
        "configured": connected,
        "connection_status": "connected" if connected else "not_configured",
        "connection_provenance": "api_connection" if connected else "disconnected",
        "state": state,
        "label_ar": label_ar,
        "analysis": {
            "ready": connected,
            "model": analysis_model,
            "mode": "read_only_analysis",
            "writes_enabled": False,
        },
        "images": {
            "ready": image_ready,
            "policy_enabled": image_policy_enabled,
            "model": image_model,
            "mode": "proposal_only" if not image_ready else "provider_ready_execution_not_connected",
            "execution_available": False,
            "human_approval_required": True,
            "direct_publish_allowed": False,
        },
    }
