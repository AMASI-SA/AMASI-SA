from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_review_mezan_images_are_review_only_and_selectable():
    backend = (ROOT / "backend" / "order_review_image_modes.py").read_text(encoding="utf-8")
    frontend = (ROOT / "frontend" / "src" / "reviewMezanImageEnhancer.js").read_text(encoding="utf-8")
    index = (ROOT / "frontend" / "src" / "index.js").read_text(encoding="utf-8")

    assert 'MEZAN_IMAGES = "order_review_mezan_images"' in backend
    assert 'MAX_IMAGE_BYTES = 5 * 1024 * 1024' in backend
    assert '/mezan-images"' in backend
    assert 'image_not_in_product_gallery' in backend
    assert 'mezan_image_in_use' in backend
    assert 'reviewMezanImageEnhancer' in index
    assert 'إضافة صورة ميزان' in frontend
    assert 'تظهر في انتظار المراجعة فقط' in frontend
    assert 'حفظ كصورة رئيسية في ميزان' in frontend
    assert 'حفظ مع الخيارات المحددة' in frontend
    assert 'image/jpeg,image/png,image/webp' in frontend
    assert 'selected_image_url: imageUrl' in frontend
    assert 'window.location.reload()' in frontend


def test_review_customer_history_and_ai_loyalty_are_read_only():
    frontend = (ROOT / "frontend" / "src" / "reviewCustomerHistoryFast.js").read_text(encoding="utf-8")
    index = (ROOT / "frontend" / "src" / "index.js").read_text(encoding="utf-8")

    assert 'reviewCustomerHistoryFast' in index
    assert 'سجل العميل السابق' in frontend
    assert 'الدفع عند الاستلام' in frontend
    assert 'decision_mode: "ai_policy_v1"' in frontend
    assert 'requires_approval: true' in frontend
    assert 'لا تُضاف هدية أو خصم دون اعتماد بشري' in frontend
    assert 'api.get("/orders-v2"' in frontend
    assert 'api.post(' not in frontend
    assert 'api.patch(' not in frontend
    assert 'api.delete(' not in frontend
