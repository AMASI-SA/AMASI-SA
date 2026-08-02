from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


component_path = Path("frontend/src/components/MezanV2NavigationShell.jsx")
test_path = Path("frontend/src/components/MezanV2NavigationShell.test.jsx")
header_test_path = Path("frontend/src/mezanUnifiedHeader.test.js")
component = component_path.read_text(encoding="utf-8")
tests = test_path.read_text(encoding="utf-8")
header_tests = header_test_path.read_text(encoding="utf-8")

component = replace_once(
    component,
    '''export function activeNavigationSection(location) {
    return MEZAN_V2_NAV_SECTIONS.find(
        (section) => section.items.some((item) => isNavigationItemActive(location, item)),
    ) || null;
}
''',
    '''export function activeNavigationSection(location) {
    return MEZAN_V2_NAV_SECTIONS.find(
        (section) => section.items.some((item) => isNavigationItemActive(location, item)),
    ) || null;
}

export function navigationSectionsForDisplay(location, openSectionId = null) {
    const activeSection = activeNavigationSection(location);
    const openSection = MEZAN_V2_NAV_SECTIONS.find(
        (section) => section.id === openSectionId,
    ) || null;
    return {
        activeSection,
        openSection,
        visibleSection: openSection || activeSection,
    };
}
''',
    "navigation display helper",
)

component = replace_once(
    component,
    '''    const activeSection = useMemo(() => activeNavigationSection(location), [location]);
''',
    '''    const { activeSection, openSection, visibleSection } = useMemo(
        () => navigationSectionsForDisplay(location, openSectionId),
        [location, openSectionId],
    );
''',
    "visible section state",
)

old_dropdown = '''
                                    {section.items.length > 1 && open && (
                                        <div
                                            className="absolute right-0 top-[calc(100%+0.6rem)] z-[70] max-h-[65vh] w-72 overflow-y-auto rounded-2xl border border-emerald-950 bg-[#0B4938] p-2 shadow-2xl"
                                            data-testid={`mezan-v2-dropdown-${section.id}`}
                                        >
                                            <div className="mb-1 px-3 py-2 text-xs font-black text-emerald-200">
                                                صفحات {section.label}
                                            </div>
                                            {section.items.map((item) => {
                                                const itemActive = isNavigationItemActive(location, item);
                                                return (
                                                    <Link
                                                        key={item.to}
                                                        to={item.to}
                                                        onClick={() => {
                                                            setOpenSectionId(null);
                                                            setSearchOpen(false);
                                                        }}
                                                        className={[
                                                            "flex items-center justify-between rounded-xl px-4 py-3 text-sm font-bold transition",
                                                            itemActive
                                                                ? "bg-emerald-200 text-slate-950"
                                                                : "text-slate-100 hover:bg-white/10",
                                                        ].join(" ")}
                                                        data-testid={`mezan-v2-dropdown-link-${section.id}`}
                                                    >
                                                        <span className="whitespace-nowrap">{item.label}</span>
                                                        {itemActive && <span className="whitespace-nowrap text-xs">الحالية</span>}
                                                    </Link>
                                                );
                                            })}
                                        </div>
                                    )}
'''
component = replace_once(component, old_dropdown, "\n", "clipped dropdown removal")

component = replace_once(
    component,
    '''            {activeSection && activeSection.items.length > 1 && (
                <nav
                    className="flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-[#0B4938] px-2 scrollbar-thin sm:px-5"
                    aria-label={`صفحات ${activeSection.label}`}
                    data-testid={`mezan-v2-secondary-${activeSection.id}`}
                >
                    {activeSection.items.map((item) => {
''',
    '''            {visibleSection && visibleSection.items.length > 1 && (
                <nav
                    className="relative z-[60] flex flex-nowrap items-center gap-1 overflow-x-auto whitespace-nowrap border-t border-white/10 bg-[#0B4938] px-2 shadow-inner scrollbar-thin sm:px-5"
                    aria-label={`صفحات ${visibleSection.label}`}
                    data-testid={`mezan-v2-secondary-${visibleSection.id}`}
                    data-navigation-source={openSection ? "opened" : "active"}
                >
                    {visibleSection.items.map((item) => {
''',
    "secondary rail visibility",
)
component = replace_once(
    component,
    '''                                data-testid={`mezan-v2-secondary-link-${activeSection.id}`}
''',
    '''                                data-testid={`mezan-v2-secondary-link-${visibleSection.id}`}
''',
    "secondary link section id",
)
component_path.write_text(component, encoding="utf-8")

tests = replace_once(
    tests,
    '''    isMezanV2Route,
    isNavigationItemActive,
''',
    '''    isMezanV2Route,
    isNavigationItemActive,
    navigationSectionsForDisplay,
''',
    "display helper import",
)
tests = replace_once(
    tests,
    '''const PRODUCTS_LOCATION = {
''',
    '''const DASHBOARD_LOCATION = {
    pathname: "/dashboard-v2",
    search: "",
};

const PRODUCTS_LOCATION = {
''',
    "dashboard location",
)
tests = replace_once(
    tests,
    '''test("new navigation does not route to legacy Mezan pages", () => {
''',
    '''test("opening marketing selects a visible secondary rail outside the primary scroller", () => {
    const state = navigationSectionsForDisplay(DASHBOARD_LOCATION, "marketing");
    expect(state.activeSection?.id).toBe("home");
    expect(state.openSection?.id).toBe("marketing");
    expect(state.visibleSection?.id).toBe("marketing");
    expect(state.visibleSection.items.map((item) => item.label)).toEqual([
        "جميع المنصات",
        "سناب شات",
        "تيك توك",
        "ميتا",
        "إعلانات Google",
    ]);
});

test("new navigation does not route to legacy Mezan pages", () => {
''',
    "marketing open rail test",
)
test_path.write_text(tests, encoding="utf-8")

header_tests = replace_once(
    header_tests,
    '''    expect(navigation).toContain('border-emerald-950 bg-[#0B4938]');
    expect(navigation).toContain('border-t border-white/10 bg-[#0B4938]');
''',
    '''    expect(navigation).toContain('border-t border-white/10 bg-[#0B4938]');
    expect(navigation).toContain('data-navigation-source={openSection ? "opened" : "active"}');
    expect(navigation).not.toContain('top-[calc(100%+0.6rem)]');
''',
    "green identity rail contract",
)
header_test_path.write_text(header_tests, encoding="utf-8")

print("MARKETING_SUBNAV_VISIBLE_PATCH_APPLIED")
