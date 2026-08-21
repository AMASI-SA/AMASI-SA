#!/usr/bin/env python3
"""Apply the P0-1 Arabic status normalization fix deterministically.

This helper exists only to make the repository edit reproducible in constrained
agent environments. It updates Dashboard V2's shared text matcher and writes a
focused regression test. It does not touch production data or settings.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "backend" / "dashboard_v2_routes.py"
TEST = ROOT / "backend" / "tests" / "test_dashboard_v2_arabic_status_normalization.py"

OLD_IMPORT = "import asyncio\nimport logging\n"
NEW_IMPORT = "import asyncio\nimport logging\nimport re\nimport unicodedata\n"

OLD_MATCHER = '''def _matches_any(value: str, allowed: list[str]) -> bool:\n    if not allowed:\n        return True\n    normalized = str(value or \"\").strip().casefold()\n    return any(\n        candidate and (\n            candidate == normalized\n            or candidate in normalized\n            or normalized in candidate\n        )\n        for candidate in (str(item).strip().casefold() for item in allowed)\n    )\n'''

NEW_MATCHER = '''_ARABIC_DIACRITICS_RE = re.compile(r\"[\\u0610-\\u061a\\u064b-\\u065f\\u0670\\u06d6-\\u06ed]\")\n\n\ndef _normalize_match_text(value: object) -> str:\n    \"\"\"Normalize presentation variants without changing business semantics.\n\n    Arabic hamza/alef presentation differences are common between Salla status\n    labels and saved Mezan settings (for example بانتظار vs بإنتظار). Matching\n    should not drop otherwise identical orders because of those orthographic\n    variants. The function also removes combining marks/tatweel and collapses\n    whitespace, while preserving the actual words and status policy.\n    \"\"\"\n    rendered = unicodedata.normalize(\"NFKC\", str(value or \"\")).casefold()\n    rendered = _ARABIC_DIACRITICS_RE.sub(\"\", rendered).replace(\"ـ\", \"\")\n    rendered = rendered.translate(str.maketrans({\n        \"أ\": \"ا\",\n        \"إ\": \"ا\",\n        \"آ\": \"ا\",\n        \"ٱ\": \"ا\",\n    }))\n    return \" \".join(rendered.split())\n\n\ndef _matches_any(value: str, allowed: list[str]) -> bool:\n    if not allowed:\n        return True\n    normalized = _normalize_match_text(value)\n    return any(\n        candidate and (\n            candidate == normalized\n            or candidate in normalized\n            or normalized in candidate\n        )\n        for candidate in (_normalize_match_text(item) for item in allowed)\n    )\n'''

TEST_CONTENT = '''from dashboard_v2_routes import _matches_any, _normalize_match_text\n\n\ndef test_arabic_status_hamza_variants_match():\n    allowed = [\"بإنتظار المراجعة\"]\n    assert _matches_any(\"بانتظار المراجعة\", allowed) is True\n    assert _matches_any(\"بأنتظار المراجعة\", allowed) is True\n    assert _matches_any(\"بإنتظار المراجعة\", allowed) is True\n\n\ndef test_arabic_status_diacritics_and_whitespace_match():\n    assert _matches_any(\"  بِانتظار   المراجعة  \" , [\"بانتظار المراجعة\"]) is True\n\n\ndef test_status_policy_is_not_broadened_to_different_status():\n    allowed = [\"بإنتظار المراجعة\"]\n    assert _matches_any(\"تم التوصيل\", allowed) is False\n    assert _matches_any(\"ملغي\", allowed) is False\n\n\ndef test_normalizer_preserves_words_while_normalizing_alef():\n    assert _normalize_match_text(\"بإنتظار المراجعة\") == \"بانتظار المراجعة\"\n'''


def main() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    if NEW_MATCHER in text:
        print("dashboard matcher already patched")
    else:
        if OLD_IMPORT not in text:
            raise SystemExit("expected dashboard import anchor not found")
        if OLD_MATCHER not in text:
            raise SystemExit("expected _matches_any implementation not found")
        text = text.replace(OLD_IMPORT, NEW_IMPORT, 1)
        text = text.replace(OLD_MATCHER, NEW_MATCHER, 1)
        DASHBOARD.write_text(text, encoding="utf-8")
        print("patched backend/dashboard_v2_routes.py")

    TEST.parent.mkdir(parents=True, exist_ok=True)
    TEST.write_text(TEST_CONTENT, encoding="utf-8")
    print("wrote backend/tests/test_dashboard_v2_arabic_status_normalization.py")


if __name__ == "__main__":
    main()
