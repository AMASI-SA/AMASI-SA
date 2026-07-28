from product_media_upload_routes import ALLOWED_TYPES, MAX_IMAGE_BYTES, UPLOAD_TTL_DAYS, _detected_type


def test_detects_supported_image_signatures():
    assert _detected_type(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert _detected_type(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert _detected_type(b"RIFF1234WEBPrest") == "image/webp"


def test_rejects_unknown_signature():
    assert _detected_type(b"GIF89a") is None
    assert _detected_type(b"<svg></svg>") is None


def test_upload_policy_is_bounded():
    assert set(ALLOWED_TYPES) == {"image/jpeg", "image/png", "image/webp"}
    assert MAX_IMAGE_BYTES == 5 * 1024 * 1024
    assert UPLOAD_TTL_DAYS == 7
