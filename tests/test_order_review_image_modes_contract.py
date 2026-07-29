from pathlib import Path


def test_review_image_save_modes_are_wired():
    backend = Path("backend/order_review_image_modes.py").read_text(encoding="utf-8")
    page = Path("frontend/src/pages/OrderReview.jsx").read_text(encoding="utf-8")
    service = Path("frontend/src/services/orderReviewEngine.js").read_text(encoding="utf-8")
    server = Path("backend/server.py").read_text(encoding="utf-8")

    assert 'Literal["order_only", "options", "default"]' in backend
    assert 'mode == "default"' in backend
    assert "score = len(rule_values)" in backend
    assert "image-choice" in backend
    assert "order_review_image_modes import make_order_review_router" in server
    assert "saveOrderReviewImageChoice" in service
    assert "حفظ لهذا الطلب فقط" in page
    assert "حفظ مع الخيارات المحددة" in page
    assert "حفظ كصورة رئيسية في ميزان" in page
    assert "setVisibleSelectedImage(url)" in page
