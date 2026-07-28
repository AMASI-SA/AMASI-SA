import httpx

from product_media_ai_gpt2_compat_routes import (
    RETRYABLE_EXECUTION_STATUSES,
    _provider_error_payload,
    build_gpt_image_request_fields,
)


def test_gpt_image_2_background_removal_is_opaque_and_omits_legacy_fidelity(monkeypatch):
    monkeypatch.setenv("MEZAN_OPENAI_IMAGE_QUALITY", "medium")
    fields, allow_input_fidelity = build_gpt_image_request_fields(
        job={
            "operation": "remove_background",
            "aspect_ratio": "original",
            "prompt": "حافظ على لون المنتج",
        },
        product={"name": "مريول مدرسي"},
        model="gpt-image-2",
    )
    assert fields["background"] == "opaque"
    assert fields["size"] == "auto"
    assert "pure white opaque ecommerce background" in fields["prompt"]
    assert allow_input_fidelity is False
    assert "input_fidelity" not in fields


def test_snapshot_model_is_also_treated_as_gpt_image_2():
    _, allow_input_fidelity = build_gpt_image_request_fields(
        job={"operation": "improve_quality", "aspect_ratio": "1:1"},
        product={},
        model="gpt-image-2-2026-04-21",
    )
    assert allow_input_fidelity is False


def test_failed_job_requires_explicit_but_supported_retry():
    assert "failed" in RETRYABLE_EXECUTION_STATUSES
    assert "completed" not in RETRYABLE_EXECUTION_STATUSES
    assert "executing" not in RETRYABLE_EXECUTION_STATUSES


def test_provider_error_identifies_legacy_fidelity_parameter_safely():
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://api.openai.com/v1/images/edits"),
        json={
            "error": {
                "code": "invalid_value",
                "param": "input_fidelity",
                "message": "internal provider details must not be copied",
            }
        },
    )
    code, message = _provider_error_payload(response)
    assert code == "invalid_value"
    assert "GPT Image 2" in message
    assert "internal provider details" not in message


def test_provider_error_identifies_transparent_background_safely():
    response = httpx.Response(
        400,
        request=httpx.Request("POST", "https://api.openai.com/v1/images/edits"),
        json={"error": {"code": "invalid_value", "param": "background"}},
    )
    _, message = _provider_error_payload(response)
    assert "الخلفية الشفافة" in message
    assert "خلفية بيضاء" in message
