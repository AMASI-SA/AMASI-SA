from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mezan_image_choice_refreshes_react_review_card():
    frontend = (ROOT / "frontend" / "src" / "reviewMezanImageEnhancer.js").read_text(encoding="utf-8")

    assert "window.location.reload()" in frontend
    assert "selected_image_url: imageUrl" in frontend
