from supplier_dispatch_share_evidence import _detected_image_type


def test_supplier_dispatch_evidence_detects_supported_images():
    assert _detected_image_type(b"\xff\xd8\xffabc") == "image/jpeg"
    assert _detected_image_type(b"\x89PNG\r\n\x1a\nabc") == "image/png"
    assert _detected_image_type(b"RIFFxxxxWEBPabc") == "image/webp"


def test_supplier_dispatch_evidence_rejects_non_images():
    assert _detected_image_type(b"%PDF-1.7") is None
    assert _detected_image_type(b"hello") is None
