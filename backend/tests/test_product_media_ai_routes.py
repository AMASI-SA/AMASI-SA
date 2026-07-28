import base64

import pytest

from product_media_ai_execution_support import install_product_media_ai_execution_support

install_product_media_ai_execution_support()

from product_media_ai_routes import (  # noqa: E402
    MAX_PROMPT_LENGTH,
    AiMediaExecutionError,
    OPERATION_CATALOG,
    _extract_b64_image,
    _extract_response_text,
    _forbidden_ip,
    _image_size,
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
    assert status["image_model"] is None


def test_image_execution_requires_explicit_policy_and_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    monkeypatch.setenv("MEZAN_AI_IMAGE_ENABLED", "true")
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)
    assert image_provider_status()["execution_available"] is False
    monkeypatch.setenv("MEZAN_OPENAI_IMAGE_MODEL", "gpt-image-2")
    status = image_provider_status()
    assert status["state"] == "connected_images_ready"
    assert status["image_model"] == "gpt-image-2"
    assert status["ready"] is True
    assert status["execution_available"] is True
    assert status["mode"] == "governed_execution"


def test_edit_operation_requires_owned_source_and_separate_execution_permission():
    with pytest.raises(ValueError, match="source_image_required"):
        validate_ai_media_request({"operation": "remove_background"}, {"https://cdn/x.jpg"})
    with pytest.raises(ValueError, match="source_image_not_owned_by_product"):
        validate_ai_media_request({"operation": "remove_background", "source_image_url": "https://other/image.jpg"}, {"https://cdn/x.jpg"})
    result = validate_ai_media_request({"operation": "remove_background", "source_image_url": "https://cdn/x.jpg", "prompt": "حافظ على لون المنتج", "aspect_ratio": "1:1"}, {"https://cdn/x.jpg"})
    assert result["permission"] == "products.media.ai_edit"
    assert result["execution_permission"] == "products.ai.execute_low_risk"
    assert result["risk"] == "low"


def test_medium_risk_generation_requires_high_risk_execution_permission():
    result = validate_ai_media_request({"operation": "generate_from_prompt", "prompt": "صورة منتج على خلفية استوديو", "aspect_ratio": "4:5"}, set())
    assert result["source_image_url"] is None
    assert result["permission"] == "products.media.ai_generate"
    assert result["execution_permission"] == "products.ai.execute_high_risk"


def test_request_bounds_and_catalog_are_explicit():
    assert "ad_creative" in OPERATION_CATALOG
    with pytest.raises(ValueError, match="ai_media_prompt_too_long"):
        validate_ai_media_request({"operation": "generate_from_prompt", "prompt": "x" * (MAX_PROMPT_LENGTH + 1)}, set())
    with pytest.raises(ValueError, match="invalid_aspect_ratio"):
        validate_ai_media_request({"operation": "generate_from_prompt", "aspect_ratio": "3:7"}, set())


def test_aspect_ratios_map_to_supported_openai_sizes():
    assert _image_size("1:1") == "1024x1024"
    assert _image_size("4:5") == "1024x1536"
    assert _image_size("9:16") == "1024x1536"
    assert _image_size("16:9") == "1536x1024"
    assert _image_size("original") == "auto"


def test_image_response_is_base64_decoded_and_signature_checked():
    content = b"\x89PNG\r\n\x1a\nresult"
    payload = {"data": [{"b64_json": base64.b64encode(content).decode("ascii")}]}
    assert _extract_b64_image(payload) == content
    with pytest.raises(AiMediaExecutionError, match="نتيجة غير صالحة"):
        _extract_b64_image({"data": [{"b64_json": "not-base64"}]})


def test_response_text_supports_raw_responses_api_shape():
    payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "سلسال فضي بالاسم"}]}]}
    assert _extract_response_text(payload) == "سلسال فضي بالاسم"


def test_private_network_addresses_are_blocked():
    assert _forbidden_ip("127.0.0.1") is True
    assert _forbidden_ip("10.0.0.5") is True
    assert _forbidden_ip("169.254.169.254") is True
    assert _forbidden_ip("8.8.8.8") is False
