from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "frontend/src/components/Layout.jsx"
NAVIGATION = ROOT / "frontend/src/components/MezanV2NavigationShell.jsx"
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
    'className="sticky top-0 z-40 border-b border-emerald-900/20 bg-background/95 backdrop-blur sm:px-4 sm:py-2 lg:px-6"\n                    data-testid="mezan-v2-unified-header"',
    "Mezan 2 unified header positioning",
)

if 'className="sticky top-0 z-40' not in layout:
    raise SystemExit("sticky header marker missing")
LAYOUT.write_text(layout, encoding="utf-8")

navigation = NAVIGATION.read_text(encoding="utf-8")
navigation = replace_once(
    navigation,
    'className="relative overflow-visible border-y border-slate-800 bg-slate-950 shadow-xl sm:rounded-2xl sm:border"',
    'className="relative overflow-visible border-y border-emerald-900/70 bg-brand shadow-xl sm:rounded-2xl sm:border"',
    "Mezan brand primary header surface",
)
navigation = replace_once(
    navigation,
    'className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-2 shadow-2xl"',
    'className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-emerald-800 bg-[#0B4A38] p-2 shadow-2xl"',
    "Mezan brand dropdown surface",
)
navigation = replace_once(
    navigation,
    'className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-slate-900/90 px-2 scrollbar-thin sm:px-5"',
    'className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-[#0B4A38]/95 px-2 scrollbar-thin sm:px-5"',
    "Mezan brand secondary navigation surface",
)

required_navigation = (
    'bg-brand shadow-xl',
    'bg-[#0B4A38]',
    'bg-[#0B4A38]/95',
    'data-testid="mezan-v2-search-trigger"',
)
for marker in required_navigation:
    if marker not in navigation:
        raise SystemExit(f"navigation marker missing after patch: {marker}")
if 'bg-slate-950' in navigation:
    raise SystemExit("legacy black header surface remains")
NAVIGATION.write_text(navigation, encoding="utf-8")

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

    const headerContext = layout.slice(Math.max(0, headerStart - 260), headerStart + 100);
    expect(headerContext).toContain('className="sticky top-0 z-40');
    expect(headerContext).toContain('backdrop-blur');
    expect(headerContext).not.toContain('fixed top-0');
});
'''
test = replace_once(test, old_test, new_test, "sticky header contract test")
test += '''\n
test("the unified header uses the established Mezan green identity", () => {
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(navigation).toContain('bg-brand shadow-xl');
    expect(navigation).toContain('bg-[#0B4A38]');
    expect(navigation).toContain('bg-[#0B4A38]/95');
    expect(navigation).not.toContain('bg-slate-950');
});
'''
TEST.write_text(test, encoding="utf-8")

print("MAIN_MEZAN2_STICKY_BRAND_HEADER_PATCHED")
