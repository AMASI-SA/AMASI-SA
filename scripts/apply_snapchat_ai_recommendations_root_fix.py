#!/usr/bin/env python3
"""Fail-closed Snapchat recommendation status fix for Mezan."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MONITOR = Path("backend/campaign_ai_monitor.py")
TEST = Path("backend/tests/test_campaign_ai_monitor.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count == 0 and new in text:
        print(f"already applied: {label}")
        return text
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return text.replace(old, new, 1)


def patch(path: Path, edits: list[tuple[str, str, str]], check: bool) -> bool:
    original = path.read_text(encoding="utf-8")
    updated = original
    for label, old, new in edits:
        updated = replace_once(updated, old, new, f"{path}:{label}")
    changed = updated != original
    print(("would update" if check else "updated") if changed else "unchanged", path)
    if changed and not check:
        path.write_text(updated, encoding="utf-8")
    return changed


STATUS_OLD = '''def _active(value: Any) -> bool:\n    return _text(value).upper() in {\n        "ACTIVE", "ENABLED", "RUNNING", "DELIVERING",\n    }\n'''
STATUS_NEW = '''def _status_text(value: Any) -> str:\n    if isinstance(value, dict):\n        priority = (\n            "effective_status", "delivery_state", "delivery_status",\n            "configured_status", "status", "state", "code", "label",\n        )\n        ordered = [value.get(key) for key in priority if value.get(key) is not None]\n        ordered.extend(item for key, item in value.items() if key not in priority)\n        value = ordered\n    if isinstance(value, (list, tuple, set)):\n        value = " ".join(_status_text(item) for item in value)\n    rendered = _text(value, limit=600).casefold()\n    for marker in ("_", "-", "/", "—", "–", ":", "|", "،", ",", "."):\n        rendered = rendered.replace(marker, " ")\n    return " ".join(rendered.split())\n\n\ndef _normalized_status(value: Any) -> str:\n    rendered = _status_text(value)\n    inactive = (\n        "not delivering", "not active", "inactive", "paused", "disabled",\n        "stopped", "ended", "deleted", "archived", "rejected",\n        "disapproved", "out of budget", "pending review", "draft",\n        "لا يتم التسليم", "لا تسليم", "غير نشط", "غير نشطة",\n        "غير فعال", "غير فعالة", "متوقف", "متوقفة", "موقوف",\n        "موقوفة", "محذوف", "مرفوض", "منتهي", "قيد المراجعة",\n        "بانتظار المراجعة", "نفدت الميزانية",\n    )\n    if any(marker in rendered for marker in inactive):\n        return "inactive"\n    active = (\n        "active", "enabled", "running", "delivering", "live", "serving",\n        "يتم التسليم", "قيد التسليم", "جاري التسليم", "جار التسليم",\n        "نشط", "نشطة", "مفعل", "مفعلة", "فعال", "فعالة",\n        "مرحلة التعلم",\n    )\n    return "active" if any(marker in rendered for marker in active) else "unknown"\n\n\ndef _active(value: Any) -> bool:\n    return _normalized_status(value) == "active"\n'''

ENTITY_OLD = '''        "status": _text(status, limit=60) or "unknown",\n        "active": _active(status),\n'''
ENTITY_NEW = '''        "status": _text(status, limit=60) or "unknown",\n        "normalized_status": _normalized_status(status),\n        "active": _active(status),\n'''

CAMPAIGN_OLD = '''            parent_name=None,\n            status=item.get("delivery_status") or item.get("status"),\n            spend_sar=item.get("spend_sar_equivalent"),\n'''
CAMPAIGN_NEW = '''            parent_name=None,\n            status=(\n                item.get("effective_status")\n                or item.get("delivery_state")\n                or item.get("delivery_status")\n                or item.get("status")\n            ),\n            spend_sar=item.get("spend_sar_equivalent"),\n'''

SQUAD_OLD = '''                entity_id=item.get("ad_squad_id"), entity_name=item.get("ad_squad_name"),\n                parent_name=item.get("campaign_name"), status=item.get("delivery_status") or item.get("status"),\n                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),\n'''
SQUAD_NEW = '''                entity_id=item.get("ad_squad_id"), entity_name=item.get("ad_squad_name"),\n                parent_name=item.get("campaign_name"),\n                status=(\n                    item.get("effective_status")\n                    or item.get("delivery_state")\n                    or item.get("delivery_status")\n                    or item.get("status")\n                ),\n                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),\n'''

AD_OLD = '''                parent_name=item.get("ad_squad_name") or item.get("campaign_name"),\n                status=item.get("delivery_status") or item.get("status"),\n                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),\n'''
AD_NEW = '''                parent_name=item.get("ad_squad_name") or item.get("campaign_name"),\n                status=(\n                    item.get("effective_status")\n                    or item.get("delivery_state")\n                    or item.get("delivery_status")\n                    or item.get("status")\n                ),\n                spend_sar=item.get("spend_sar"), revenue_sar=item.get("sales_sar"),\n'''

IMPORT_OLD = '''    _deterministic_recommendations,\n    _govern_output,\n'''
IMPORT_NEW = '''    _active,\n    _deterministic_recommendations,\n    _govern_output,\n'''

TEST_OLD = '''def test_paused_entity_is_not_recommended_for_another_change():\n    assert deterministic_candidates([entity(status="PAUSED", active=False)]) == []\n\n\ndef test_governance_discards_a_stale_pause_for_an_entity_now_stopped():\n'''
TEST_NEW = '''def test_paused_entity_is_not_recommended_for_another_change():\n    assert deterministic_candidates([entity(status="PAUSED", active=False)]) == []\n\n\ndef test_real_snapchat_arabic_delivery_status_reaches_ai_candidates():\n    assert _active("يتم التسليم — مرحلة التعلم") is True\n    row = entity(\n        entity_id="snap-live",\n        status="يتم التسليم — مرحلة التعلم",\n        active=_active("يتم التسليم — مرحلة التعلم"),\n    )\n    assert [item["entity_id"] for item in deterministic_candidates([row])] == ["snap-live"]\n\n\ndef test_snapchat_negative_delivery_status_wins_before_active_words():\n    assert _active("NOT_DELIVERING") is False\n    assert _active("غير نشط — لا يتم التسليم") is False\n    assert _active({\n        "configured_status": "ACTIVE",\n        "delivery_state": "NOT_DELIVERING",\n        "delivery_status": "لا يتم التسليم",\n    }) is False\n\n\ndef test_governance_discards_a_stale_pause_for_an_entity_now_stopped():\n'''


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()

    patch(MONITOR, [
        ("status normalizer", STATUS_OLD, STATUS_NEW),
        ("persist normalized status", ENTITY_OLD, ENTITY_NEW),
        ("campaign status precedence", CAMPAIGN_OLD, CAMPAIGN_NEW),
        ("ad squad status precedence", SQUAD_OLD, SQUAD_NEW),
        ("ad status precedence", AD_OLD, AD_NEW),
    ], args.check)
    patch(TEST, [
        ("import active helper", IMPORT_OLD, IMPORT_NEW),
        ("real Snapchat statuses", TEST_OLD, TEST_NEW),
    ], args.check)

    if args.check:
        print("check passed; no files written")
        return 0
    subprocess.run([sys.executable, "-m", "py_compile", str(MONITOR), str(TEST)], check=True)
    if args.test:
        subprocess.run([sys.executable, "-m", "pytest", str(TEST), "-q"], check=True)
    print("Snapchat AI recommendation root fix applied")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
