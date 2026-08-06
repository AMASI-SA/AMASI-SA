from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


test_path = "frontend/src/components/marketing/AdsPerformanceExplorer.test.jsx"
replace_once(
    test_path,
    '''    test("builds a 24-hour Snapchat input with Arabic hour labels", () => {\n''',
    '''    test("keeps unavailable commercial metrics as null instead of fake zero", () => {\n        const rows = buildAdsChartRows([\n            {\n                date: "2026-08-06",\n                spend_sar: 2973.19,\n                sales_sar: null,\n                orders: null,\n                roas: null,\n            },\n        ]);\n\n        expect(rows[0].spend_raw).toBe(2973.19);\n        expect(rows[0].sales_raw).toBeNull();\n        expect(rows[0].orders_raw).toBeNull();\n        expect(rows[0].roas_raw).toBeNull();\n    });\n\n    test("builds a 24-hour Snapchat input with Arabic hour labels", () => {\n''',
)

print("SNAP_CAMPAIGN_TOTAL_UI_TESTS_V4_APPLIED")
