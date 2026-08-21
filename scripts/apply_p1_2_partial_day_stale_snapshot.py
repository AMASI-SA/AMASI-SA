#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "backend" / "campaign_ai_monitor_legacy.py"
QUALITY = ROOT / "backend" / "campaign_ai_time_window_quality.py"
EXEC = ROOT / "backend" / "campaign_ai_execution_quality_gate.py"
TEST = ROOT / "backend" / "tests" / "test_campaign_ai_partial_day_stale_snapshot_p1_2.py"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        print(f"{label}: already patched")
        return
    if old not in text:
        raise SystemExit(f"{label}: expected anchor not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"{label}: patched")


QUALITY.write_text('''"""Time-window safety semantics for Campaign AI reasoning and execution."""\nfrom __future__ import annotations\n\nfrom datetime import date, datetime, timedelta, timezone\nfrom typing import Any\nfrom zoneinfo import ZoneInfo, ZoneInfoNotFoundError\n\nRIYADH = timezone(timedelta(hours=3))\nSCALE_SNAPSHOT_MAX_AGE_MINUTES = 90\nDEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES = 5 * 60\n\n\ndef _parse_date(value: Any) -> date | None:\n    try:\n        return date.fromisoformat(str(value or ""))\n    except (TypeError, ValueError):\n        return None\n\n\ndef _zone(value: Any):\n    name = str(value or "").strip()\n    if name:\n        try:\n            return ZoneInfo(name)\n        except (ZoneInfoNotFoundError, ValueError):\n            pass\n    return RIYADH\n\n\ndef window_quality(row: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:\n    source = row if isinstance(row, dict) else {}\n    current = now or datetime.now(timezone.utc)\n    if current.tzinfo is None:\n        current = current.replace(tzinfo=timezone.utc)\n    zone = _zone(source.get("account_timezone"))\n    local_now = current.astimezone(zone)\n    start = _parse_date(source.get("source_date_from"))\n    end = _parse_date(source.get("source_date_to"))\n    contains_open_day = bool(end is not None and end == local_now.date())\n    requested_days = ((end - start).days + 1) if start and end and end >= start else None\n    completed_days = None\n    if requested_days is not None:\n        completed_days = max(0, requested_days - (1 if contains_open_day else 0))\n    elapsed_fraction = (\n        round((local_now.hour * 3600 + local_now.minute * 60 + local_now.second) / 86400, 4)\n        if contains_open_day\n        else 1.0\n    )\n    return {\n        "status": "partial_current_day" if contains_open_day else "completed_window",\n        "contains_open_current_day": contains_open_day,\n        "requested_days": requested_days,\n        "completed_days": completed_days,\n        "open_day_elapsed_fraction": elapsed_fraction,\n        "safe_for_scale_comparison": not contains_open_day,\n        "account_timezone": str(getattr(zone, "key", "Asia/Riyadh")),\n    }\n\n\ndef completed_history_window(end: date, days: int) -> tuple[date, date]:\n    days = max(1, int(days))\n    history_end = end - timedelta(days=1)\n    history_start = history_end - timedelta(days=days - 1)\n    return history_start, history_end\n\n\ndef snapshot_max_age_minutes(action: Any) -> int:\n    return (\n        SCALE_SNAPSHOT_MAX_AGE_MINUTES\n        if str(action or "").strip().lower() == "scale"\n        else DEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES\n    )\n\n\n__all__ = [\n    "DEFENSIVE_SNAPSHOT_MAX_AGE_MINUTES",\n    "SCALE_SNAPSHOT_MAX_AGE_MINUTES",\n    "completed_history_window",\n    "snapshot_max_age_minutes",\n    "window_quality",\n]\n''', encoding='utf-8')
print("wrote backend/campaign_ai_time_window_quality.py")

replace_once(
    MONITOR,
    '''        days = max(1, int(_number(row.get("observed_days")) or 1))\n        row["spend_per_day_sar"] = round(spend / days, 2)\n''',
    '''        days = max(1, int(_number(row.get("observed_days")) or 1))\n        from campaign_ai_time_window_quality import window_quality\n        row["time_window_quality"] = window_quality(row)\n        row["contains_open_current_day"] = bool(\n            row["time_window_quality"].get("contains_open_current_day")\n        )\n        row["scale_comparison_safe"] = bool(\n            row["time_window_quality"].get("safe_for_scale_comparison")\n        )\n        row["spend_per_day_sar"] = round(spend / days, 2)\n''',
    "candidate time-window quality",
)

replace_once(
    MONITOR,
    '''        if action == "scale" and (not row.get("data_complete") or int(row.get("purchases") or 0) < 3):\n            action = "monitor"\n''',
    '''        if action == "scale" and (\n            not row.get("data_complete")\n            or int(row.get("purchases") or 0) < 3\n            or row.get("scale_comparison_safe") is not True\n        ):\n            action = "monitor"\n''',
    "govern scale on completed windows",
)

replace_once(
    MONITOR,
    '''    output: dict[str, list[dict[str, Any]]] = {}\n    for days in (7, 30):\n        rows: list[dict[str, Any]] = []\n        for provider in ("snapchat", "meta"):\n            try:\n                rows.extend(await _campaign_entities(\n                    db, user_id, provider, end - timedelta(days=days - 1), end\n                ))\n''',
    '''    output: dict[str, list[dict[str, Any]]] = {}\n    from campaign_ai_time_window_quality import completed_history_window\n    for days in (7, 30):\n        rows: list[dict[str, Any]] = []\n        history_start, history_end = completed_history_window(end, days)\n        for provider in ("snapchat", "meta"):\n            try:\n                rows.extend(await _campaign_entities(\n                    db, user_id, provider, history_start, history_end\n                ))\n''',
    "history excludes open current day",
)

replace_once(
    MONITOR,
    '''        windows[label] = {\n            "from": start.isoformat(),\n            "to": end.isoformat(),\n            **{key: totals.get(key) for key in (\n''',
    '''        from campaign_ai_time_window_quality import window_quality\n        quality = window_quality({\n            "source_date_from": start.isoformat(),\n            "source_date_to": end.isoformat(),\n            "account_timezone": "Asia/Riyadh",\n        })\n        windows[label] = {\n            "from": start.isoformat(),\n            "to": end.isoformat(),\n            "time_window_quality": quality,\n            "contains_open_current_day": quality["contains_open_current_day"],\n            "safe_for_scale_comparison": quality["safe_for_scale_comparison"],\n            **{key: totals.get(key) for key in (\n''',
    "profit windows disclose partial current day",
)

replace_once(
    EXEC,
    '''    generated_at = _parse_datetime(latest.get("generated_at"))\n    current = now().astimezone(timezone.utc)\n    if (\n        generated_at is None\n        or generated_at > current + timedelta(minutes=5)\n        or current - generated_at > timedelta(hours=DEFAULT_MAX_SNAPSHOT_AGE_HOURS)\n    ):\n        raise ExecutionQualityBlocked(["execution_snapshot_stale"])\n    recommendation = next(\n''',
    '''    generated_at = _parse_datetime(latest.get("generated_at"))\n    current = now().astimezone(timezone.utc)\n    if (\n        generated_at is None\n        or generated_at > current + timedelta(minutes=5)\n        or current - generated_at > timedelta(hours=DEFAULT_MAX_SNAPSHOT_AGE_HOURS)\n    ):\n        raise ExecutionQualityBlocked(["execution_snapshot_stale"])\n    recommendation = next(\n''',
    "base stale snapshot guard preserved",
)

replace_once(
    EXEC,
    '''    target = (latest.get("execution_targets") or {}).get(recommendation_id)\n    if (\n        not isinstance(recommendation, dict)\n''',
    '''    target = (latest.get("execution_targets") or {}).get(recommendation_id)\n    if isinstance(recommendation, dict) and generated_at is not None:\n        from campaign_ai_time_window_quality import snapshot_max_age_minutes\n        max_age_minutes = snapshot_max_age_minutes(recommendation.get("action"))\n        snapshot_age_minutes = (current - generated_at).total_seconds() / 60.0\n        if snapshot_age_minutes > max_age_minutes:\n            raise ExecutionQualityBlocked([\n                "execution_scale_snapshot_stale"\n                if str(recommendation.get("action") or "") == "scale"\n                else "execution_snapshot_stale"\n            ], {\n                "snapshot_age_minutes": round(snapshot_age_minutes, 2),\n                "max_age_minutes": max_age_minutes,\n                "action": recommendation.get("action"),\n            })\n    if (\n        not isinstance(recommendation, dict)\n''',
    "action-specific snapshot freshness",
)

TEST.write_text('''from datetime import date, datetime, timezone\n\nimport campaign_ai_monitor_legacy as legacy\nimport campaign_ai_time_window_quality as quality\n\n\ndef test_p1_2_current_local_day_is_marked_partial_and_not_safe_for_scale():\n    row = {\n        "source_date_from": "2026-08-19",\n        "source_date_to": "2026-08-21",\n        "account_timezone": "Asia/Riyadh",\n    }\n    result = quality.window_quality(\n        row, now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)\n    )\n    assert result["contains_open_current_day"] is True\n    assert result["completed_days"] == 2\n    assert result["safe_for_scale_comparison"] is False\n    assert 0 < result["open_day_elapsed_fraction"] < 1\n\n\ndef test_p1_2_completed_historical_window_is_safe_for_scale():\n    row = {\n        "source_date_from": "2026-08-18",\n        "source_date_to": "2026-08-20",\n        "account_timezone": "Asia/Riyadh",\n    }\n    result = quality.window_quality(\n        row, now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)\n    )\n    assert result["contains_open_current_day"] is False\n    assert result["completed_days"] == 3\n    assert result["safe_for_scale_comparison"] is True\n\n\ndef test_p1_2_history_uses_completed_days_only():\n    start, end = quality.completed_history_window(date(2026, 8, 21), 7)\n    assert start == date(2026, 8, 14)\n    assert end == date(2026, 8, 20)\n\n\ndef test_p1_2_scale_snapshot_is_stricter_than_defensive_action():\n    assert quality.snapshot_max_age_minutes("scale") == 90\n    assert quality.snapshot_max_age_minutes("reduce") == 300\n    assert quality.snapshot_max_age_minutes("pause") == 300\n\n\ndef test_p1_2_govern_downgrades_scale_when_window_contains_open_day():\n    candidate = {\n        "provider": "meta",\n        "entity_level": "campaign",\n        "entity_id": "c1",\n        "entity_name": "Campaign",\n        "account_id": "a1",\n        "account_name": "Account",\n        "active": True,\n        "data_complete": True,\n        "purchases": 5,\n        "scale_comparison_safe": False,\n    }\n    item = legacy.RecommendationItem(\n        recommendation_id="r1", provider="meta", entity_level="campaign",\n        entity_id="c1", entity_name="Campaign", account_id="a1",\n        account_name="Account", parent_name=None, action="scale",\n        change_percent=15, priority="high", confidence="high", title="Scale",\n        rationale="r", evidence=[], why_now="n", recommended_wait_hours=5,\n        observation_plan="o", success_criteria=[], risk_if_ignored="risk",\n        guardrail="g", next_check_at="2026-08-21T20:00:00+00:00",\n    )\n    output = legacy.RecommendationOutput(summary="s", recommendations=[item], limitations=[])\n    governed = legacy._govern_output(\n        output, [candidate], next_check_at="2026-08-21T20:00:00+00:00"\n    )\n    assert governed.recommendations[0].action == "monitor"\n    assert governed.recommendations[0].change_percent is None\n''', encoding='utf-8')
print("wrote backend/tests/test_campaign_ai_partial_day_stale_snapshot_p1_2.py")
