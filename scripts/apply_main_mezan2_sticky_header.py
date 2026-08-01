from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "frontend/src/components/Layout.jsx"
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
    'className="sticky top-0 z-40 border-b border-slate-200/70 bg-background/95 backdrop-blur sm:px-4 sm:py-2 lg:px-6"\n                    data-testid="mezan-v2-unified-header"',
    "Mezan 2 unified header positioning",
)

if 'className="sticky top-0 z-40' not in layout:
    raise SystemExit("sticky header marker missing")
LAYOUT.write_text(layout, encoding="utf-8")

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
TEST.write_text(test, encoding="utf-8")

print("MAIN_MEZAN2_STICKY_HEADER_PATCHED")
