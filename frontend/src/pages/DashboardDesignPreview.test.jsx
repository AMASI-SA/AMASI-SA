import { renderToStaticMarkup } from "react-dom/server";
import DashboardDesignPreview, {
    ORDER_TIMES,
    PROFIT_SUMMARY_LABELS,
} from "./DashboardDesignPreview";

describe("DashboardDesignPreview", () => {
    test("keeps every approved executive profit row", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);

        PROFIT_SUMMARY_LABELS.forEach((label) => {
            expect(html).toContain(label);
        });
        expect(html).toContain("إجمالي تكاليف الشحن (مقدم + أجل)");
        expect(html).toContain("إجمالي رسوم جميع طرق الدفع");
        expect(html).toContain("المصروفات التشغيلية (رواتب وإيجارات وغيرها)");
    });

    test("advertising spend stays a chart with daily and monthly grouping", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);

        expect(html).toContain("مصروفات منصات الإعلانات");
        expect(html).toContain(">يومي</button>");
        expect(html).toContain(">شهري</button>");
        expect(html).toContain("سناب شات");
        expect(html).toContain("تيك توك");
        expect(html).toContain("Meta");
        expect(html).toContain("Google Ads");
    });

    test("places advertising between abandoned carts and top products", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);
        const abandonedIndex = html.indexOf('data-testid="preview-abandoned-carts"');
        const adsIndex = html.indexOf('data-testid="preview-ads-chart"');
        const productsIndex = html.indexOf('data-testid="preview-top-products"');

        expect(abandonedIndex).toBeGreaterThan(-1);
        expect(adsIndex).toBeGreaterThan(abandonedIndex);
        expect(productsIndex).toBeGreaterThan(adsIndex);
    });

    test("keeps all four KPI values on one line in the wider KPI strip", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);

        expect(html).toContain('data-testid="preview-kpi-cards"');
        ["25.18 ر.س", "903", "7.44×", "187.33 ر.س"].forEach((value) => expect(html).toContain(value));
        expect((html.match(/whitespace-nowrap/g) || []).length).toBeGreaterThanOrEqual(5);
    });

    test("renders the approved order row structure and elapsed-time sequence", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);
        let previousIndex = html.indexOf("أحدث الطلبات");

        ORDER_TIMES.forEach((time) => {
            const nextIndex = html.indexOf(time, previousIndex + 1);
            expect(nextIndex).toBeGreaterThan(previousIndex);
            previousIndex = nextIndex;
        });
        expect((html.match(/>جديد</g) || [])).toHaveLength(3);
        expect(html).toContain("277947819");
        expect(html).toContain("تم المراجعة");
        expect(html).toContain("بانتظار المراجعة");
        expect(html).toContain("مصدر الطلب: سناب شات");
        expect(html).toContain("مصدر الطلب: جوجل");
        expect(html).toContain("مصدر الطلب: إنستقرام");
        expect(html).toContain("returnTo=%2Fdashboard-design-preview");
        expect((html.match(/فتح الطلب /g) || []).length).toBe(ORDER_TIMES.length);
    });

    test("is explicitly fake-data preview and includes every approved side card", () => {
        const html = renderToStaticMarkup(<DashboardDesignPreview />);

        expect(html).toContain("Preview ببيانات وهمية");
        expect(html).toContain("لا توجد أي كتابة على Production");
        expect(html).toContain("آخر 5 سلات متروكة");
        expect(html).toContain("المنتجات الأكثر مبيعًا");
        expect(html).toContain("Google Analytics 4 — مباشر");
        expect(html).toContain("المستخدمون النشطون الآن");
    });
});
