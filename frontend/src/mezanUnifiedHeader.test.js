const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("Mezan 2 navigation and order search share one responsive header", () => {
    const layout = read("src/components/Layout.jsx");
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(layout).toContain('data-testid="mezan-v2-unified-header"');
    expect(layout).toContain('searchForm={<GlobalOrderSearch compact />}');
    expect(layout).toContain('notificationControl={<NotificationBell />}');
    expect(layout).toContain('{!isMezanV2 && (\n                <header');

    expect(navigation).toContain('data-testid="mezan-v2-unified-primary-row"');
    expect(navigation).toContain('data-testid="mezan-v2-primary-scroll"');
    expect(navigation).toContain('data-testid="mezan-v2-search-trigger"');
    expect(navigation).toContain('data-testid="mezan-v2-search-dropdown"');
    expect(navigation).toContain('flex-nowrap');
    expect(navigation).toContain('whitespace-nowrap');
    expect(navigation).toContain('label: "الذكاء الاصطناعي"');
    expect(navigation).not.toContain('flex min-h-16 flex-wrap items-center');
});

test("the unified Mezan 2 header stays visible while the page scrolls", () => {
    const layout = read("src/components/Layout.jsx");
    const headerStart = layout.indexOf('data-testid="mezan-v2-unified-header"');
    expect(headerStart).toBeGreaterThan(-1);

    const headerContext = layout.slice(Math.max(0, headerStart - 260), headerStart + 100);
    expect(headerContext).toContain('className="sticky top-0 z-40');
    expect(headerContext).toContain('backdrop-blur');
    expect(headerContext).not.toContain('fixed top-0');
});

test("the search field stays collapsed until its header icon is activated", () => {
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(navigation).toContain('const [searchOpen, setSearchOpen] = useState(false);');
    expect(navigation).toContain('aria-expanded={searchOpen}');
    expect(navigation).toContain('{searchOpen && searchForm && (');
    expect(navigation).toContain('setSearchOpen((value) => !value);');
});

test("mobile and narrow desktop widths keep primary labels on one line", () => {
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(navigation).toContain('overflow-x-auto overscroll-x-contain');
    expect(navigation).toContain('text-[11px]');
    expect(navigation).toContain('2xl:text-sm');
    expect(navigation).toContain('<span className="whitespace-nowrap">{section.label}</span>');
});


test("the unified header uses the established Mezan green identity", () => {
    const navigation = read("src/components/MezanV2NavigationShell.jsx");

    expect(navigation).toContain('bg-brand shadow-xl');
    expect(navigation).toContain('bg-[#0B4A38]');
    expect(navigation).toContain('bg-[#0B4A38]/95');
    expect(navigation).not.toContain('bg-slate-950');
});
