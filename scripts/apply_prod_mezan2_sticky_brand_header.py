from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "frontend/src/components/Layout.jsx"
NAV = ROOT / "frontend/src/components/MezanV2NavigationShell.jsx"
TEST = ROOT / "frontend/src/mezanUnifiedHeader.test.js"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return source.replace(old, new, 1)


layout = LAYOUT.read_text(encoding="utf-8")
layout = replace_once(
    layout,
    'className="relative z-30 border-b border-slate-200/70 bg-background/95 sm:px-4 sm:py-2 lg:px-6"\n                    data-testid="mezan-v2-unified-header"',
    'className="sticky top-0 z-40 border-b border-emerald-950/20 bg-background/95 backdrop-blur sm:px-4 sm:py-2 lg:px-6"\n                    data-testid="mezan-v2-unified-header"',
    "production unified header positioning",
)
LAYOUT.write_text(layout, encoding="utf-8")

nav = NAV.read_text(encoding="utf-8")
nav = replace_once(
    nav,
    'className="relative overflow-visible border-y border-slate-800 bg-slate-950 shadow-xl sm:rounded-2xl sm:border"',
    'className="relative overflow-visible border-y border-emerald-950 bg-brand shadow-xl sm:rounded-2xl sm:border"',
    "Mezan primary brand surface",
)
nav = replace_once(
    nav,
    'className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-2 shadow-2xl"',
    'className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-emerald-950 bg-[#0B4938] p-2 shadow-2xl"',
    "Mezan dropdown brand surface",
)
nav = replace_once(
    nav,
    'className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-slate-900/90 px-2 scrollbar-thin sm:px-5"',
    'className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-[#0B4938] px-2 scrollbar-thin sm:px-5"',
    "Mezan secondary brand surface",
)
NAV.write_text(nav, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
old_test = '''test("the unified Mezan 2 header scrolls with the page rather than staying fixed", () => {
    const layout = read("src/components/Layout.jsx");
    const headerStart = layout.indexOf('data-testid="mezan-v2-unified-header"');
    expect(headerStart).toBeGreaterThan(-1);

    const headerContext = layout.slice(Math.max(0, headerStart - 220), headerStart + 100);
    expect(headerContext).toContain('className="relative z-30');
    expect(headerContext).not.toContain('sticky top-0');
    expect(headerContext).not.toContain('fixed top-0');
});
'''
new_test = '''test("the unified Mezan 2 header stays visible while the page scrolls", () => {
    const layout = read("src/components/Layout.jsx");
    const headerStart = layout.indexOf('data-testid="mezan-v2-unified-header"');
    expect(headerStart).toBeGreaterThan(-1);

    const headerContext = layout.slice(Math.max(0, headerStart - 280), headerStart + 100);
    expect(headerContext).toContain('className="sticky top-0 z-40');
    expect(headerContext).toContain('backdrop-blur');
    expect(headerContext).not.toContain('fixed top-0');
});

test("the unified header uses the established Mezan green identity", () => {
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(navigation).toContain('border-emerald-950 bg-brand shadow-xl');
    expect(navigation).toContain('border-emerald-950 bg-[#0B4938]');
    expect(navigation).toContain('border-t border-white/10 bg-[#0B4938]');
    expect(navigation).not.toContain('border-y border-slate-800 bg-slate-950 shadow-xl');
});
'''
test = replace_once(test, old_test, new_test, "production sticky brand header tests")
TEST.write_text(test, encoding="utf-8")

for marker, path in (
    ('sticky top-0 z-40', LAYOUT),
    ('bg-brand shadow-xl', NAV),
    ('bg-[#0B4938]', NAV),
    ('DashboardAnalyticsPlacement active={showsDashboardAnalytics}', LAYOUT),
    ('DashboardSnapchatAccountsPlacement active={showsDashboardAnalytics}', LAYOUT),
):
    if marker not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"required marker missing: {marker}")

print("PROD_MEZAN2_STICKY_BRAND_HEADER_PATCHED")
