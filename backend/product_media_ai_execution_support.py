"""Activate governed AI-image execution without mutating shared integration status.

The shared OpenAI status remains conservative for Apps & Integrations. Product
media owns the execution endpoint, so only this isolated surface promotes a
ready provider to governed execution after the explicit policy/model gates pass.
"""
from __future__ import annotations


def install_product_media_ai_execution_support() -> None:
    import product_media_ai_routes as routes

    if getattr(routes, "_mezan_ai_execution_support_installed", False):
        return

    original_status = routes.image_provider_status

    def image_provider_status():
        status = dict(original_status())
        ready = bool(status.get("ready"))
        status["execution_available"] = ready
        status["mode"] = "governed_execution" if ready else "proposal_only"
        status["human_approval_required"] = True
        status["direct_publish_allowed"] = False
        return status

    routes.image_provider_status = image_provider_status
    routes._mezan_ai_execution_support_installed = True
