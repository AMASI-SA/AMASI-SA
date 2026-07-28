import pytest

from product_media_ai_routes import (
    MAX_PROMPT_LENGTH,
    OPERATION_CATALOG,
    image_provider_status,
    validate_ai_media_request,
)


def test_provider_is_disconnected_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("MEZAN_AI_IMAGE_ENABLED", raising=False)
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)
    status = image_provider_status()
    assert status["connected"] is False
    assert status["ready"] is False
    assert status["state"] == "disconnected"
    assert status["execution_available"] is False
    assert status["mode"] == "proposal_only"


def test_connected_analysis_is_not_reported_as_disconnected_when_images_are_off(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.delenv("MEZAN_AI_IMAGE_ENABLED", raising=False)
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)
    status = image_provider_status()
    assert status["connected"] is True
    assert status["analysis_ready"] is True
    assert status["ready"] is False
    assert status["state"] == "connected_analysis_only"
    assert "متصل للتحليل" in status["label_ar"]


def test_image_readiness_requires_explicit_policy_and_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv("MEZAN_AI_IMAGE_ENABLED", "true")
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)
    assert image_provider_status()["ready"] is False

    monkeypatch.setenv("MEZAN_OPENAI_IMAGE_MODEL", "configured-image-model")
    status = image_provider_status()
    assert status["state"] == "connected_images_ready"
    assert status["ready"] is True
    assert status["execution_available"] is False


def test_edit_operation_requires_owned_source_image():
    with pytest.raises(ValueError, match="source_image_required"):
        validate_ai_media_request({"operation": "remove_background"}, {"https://cdn/x.jpg"})

    with pytest.raises(ValueError, match="source_image_not_owned_by_product"):
        validate_ai_media_request({
            "operation": "remove_background",
            "source_image_url": "https://other/image.jpg",
        }, {"https://cdn/x.jpg"})

    result = validate_ai_media_request({
        "operation": "remove_background",
        "source_image_url": "https://cdn/x.jpg",
        "prompt": "حافظ على لون المنتج",
        "aspect_ratio": "1:1",
    }, {"https://cdn/x.jpg"})
    assert result["permission"] == "products.media.ai_edit"
    assert result["risk"] == "low"


def test_generate_operation_can_work_without_source():
    result = validate_ai_media_request({
        "operation": "generate_from_prompt",
        "prompt": "صورة منتج على خلفية استوديو",
        "aspect_ratio": "4:5",
    }, set())
    assert result["source_image_url"] is None
    assert result["permission"] == "products.media.ai_generate"


def test_request_bounds_and_catalog_are_explicit():
    assert "ad_creative" in OPERATION_CATALOG
    with pytest.raises(ValueError, match="ai_media_prompt_too_long"):
        validate_ai_media_request({
            "operation": "generate_from_prompt",
            "prompt": "x" * (MAX_PROMPT_LENGTH + 1),
        }, set())
    with pytest.raises(ValueError, match="invalid_aspect_ratio"):
        validate_ai_media_request({
            "operation": "generate_from_prompt",
            "aspect_ratio": "3:7",
        }, set())
