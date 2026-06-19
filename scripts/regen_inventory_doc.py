#!/usr/bin/env python3
"""Iter-250a — Regenerate FINANCIAL_PAGES_INVENTORY.md from the
in-process inventory list. Run from /app/backend:

    python3 ../scripts/regen_inventory_doc.py

Idempotent — overwrites the doc each time.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow importing from /app/backend
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from financial_pages_inventory_data import INVENTORY, summary  # noqa


OUT_PATH = Path("/app/docs/FINANCIAL_PAGES_INVENTORY.md")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)


AREA_LABELS = {
    "banks_and_accounts": "البنوك والحسابات",
    "transfers_and_entries": "التحويلات والإدخالات",
    "bnpl": "BNPL (Tamara / Tabby)",
    "ad_accounts": "الحسابات الإعلانية",
    "suppliers": "الموردين",
    "employees_and_salaries": "الموظفين والرواتب",
    "shipping": "شركات الشحن",
    "receivables": "الذمم والعملاء",
    "settlements": "التسويات",
    "expenses": "المصروفات",
    "reports": "التقارير",
    "admin_diagnostics": "الإعدادات والتشخيصات",
}

CLASS_EMOJI = {
    "KEEP": "🟢", "MERGE": "🟡", "DEPRECATE": "🟠", "DELETE": "🔴",
}
RISK_EMOJI = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}
HIDE_EMOJI = {
    "KEEP_VISIBLE": "✅",
    "SAFE_TO_HIDE": "🚫",
    "NEEDS_REDIRECT": "↪️",
    "NEEDS_REVIEW": "🔍",
}


def main() -> None:
    s = summary()
    lines: list[str] = []
    lines.append("# جرد الصفحات المالية — Iter-250a\n")
    lines.append(
        "> **Read-only**. تم توليد هذا الملف من "
        "`/app/backend/financial_pages_inventory_data.py`. "
        "لا تُعدِّل يدوياً.\n"
    )
    lines.append("\n## الملخّص التنفيذي\n")
    lines.append(f"- **total_pages**: `{s['total_pages']}`")
    lines.append(f"- **keep_count**: `{s['keep_count']}` 🟢")
    lines.append(f"- **merge_count**: `{s['merge_count']}` 🟡")
    lines.append(f"- **deprecate_count**: `{s['deprecate_count']}` 🟠")
    lines.append(f"- **delete_count**: `{s['delete_count']}` 🔴")
    lines.append(
        f"- **legacy_pages_affecting_balance**: "
        f"`{s['legacy_pages_affecting_balance']}`"
    )
    lines.append("\n### Hide Safety (Iter-250a)\n")
    for k, v in s["by_hide_safety"].items():
        lines.append(f"- {HIDE_EMOJI.get(k, '')} **{k}**: `{v}`")

    lines.append("\n## 🚫 Routes للإخفاء الآن (SAFE_TO_HIDE)\n")
    lines.append("| Route القديم | البديل | السبب |")
    lines.append("|---|---|---|")
    for r in s["routes_to_hide_now"]:
        lines.append(
            f"| `{r['route']}` | `{r['replacement']}` | {r['reason']} |"
        )

    lines.append("\n## ↪️ Routes تحتاج Redirect (NEEDS_REDIRECT)\n")
    lines.append("| Route | البديل |")
    lines.append("|---|---|")
    for r in s["routes_needing_redirect_stub"]:
        lines.append(f"| `{r['route']}` | `{r['replacement']}` |")

    lines.append("\n## 🔍 Routes تحتاج مراجعة قبل أي إخفاء (NEEDS_REVIEW)\n")
    lines.append(
        "| Route | التصنيف | البديل المقترح | السبب |"
    )
    lines.append("|---|---|---|---|")
    for r in s["routes_needing_review"]:
        lines.append(
            f"| `{r['route']}` | {r['classification']} | "
            f"`{r['replacement'] or '—'}` | {r['reason']} |"
        )

    lines.append(
        "\n## 🔴 أعلى المخاطر (highest_risk_duplicates)\n"
    )
    lines.append(
        "| Route | التصنيف | البديل | السبب |"
    )
    lines.append("|---|---|---|---|")
    for r in s["highest_risk_duplicates"]:
        lines.append(
            f"| `{r['route']}` | {r['classification']} | "
            f"`{r['replacement'] or '—'}` | {r['reason']} |"
        )

    lines.append(
        "\n## 🛠️ أعلى أولوية للتنظيف القادم "
        "(recommended_next_cleanup_batch)\n"
    )
    lines.append(
        "| Route | التصنيف | المخاطر | البديل | السبب |"
    )
    lines.append("|---|---|---|---|---|")
    for r in s["recommended_next_cleanup_batch"]:
        lines.append(
            f"| `{r['route']}` | {r['classification']} | "
            f"{RISK_EMOJI.get(r['risk'], '')} {r['risk']} | "
            f"`{r['replacement'] or '—'}` | {r['reason']} |"
        )

    # ── Detail tables per area ────────────────────────────────────
    by_area: dict = {}
    for it in INVENTORY:
        by_area.setdefault(it["area"], []).append(it)

    lines.append("\n---\n\n## تفاصيل كاملة بحسب القسم\n")
    for area, rows in by_area.items():
        label = AREA_LABELS.get(area, area)
        lines.append(f"\n### {label} (`{area}`)\n")
        lines.append(
            "| Route | المصدر | SSOT | تصنيف | Hide | "
            "Risk | يؤثر على الرصيد؟ | البديل | السبب |"
        )
        lines.append(
            "|---|---|---|---|---|---|---|---|---|"
        )
        for it in rows:
            lines.append(
                f"| `{it['frontend_route']}` "
                f"| `{it['data_source']}` "
                f"| {it['ssot_status']} "
                f"| {CLASS_EMOJI.get(it['classification'], '')} "
                f"{it['classification']} "
                f"| {HIDE_EMOJI.get(it['hide_safety'], '')} "
                f"{it['hide_safety']} "
                f"| {RISK_EMOJI.get(it['risk'], '')} "
                f"{it['risk']} "
                f"| {'نعم' if it['affects_balance'] else 'لا'} "
                f"| `{it['replacement'] or '—'}` "
                f"| {it['reason']} |"
            )

    lines.append(
        "\n---\n\n*Generated by "
        "`/app/scripts/regen_inventory_doc.py` from "
        "`financial_pages_inventory_data.py`.*\n"
    )
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"✓ wrote {OUT_PATH} ({len(lines)} lines)")


if __name__ == "__main__":
    main()
