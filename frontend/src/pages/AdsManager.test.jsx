import { renderToStaticMarkup } from "react-dom/server";
import { AdsManagerView } from "./AdsManager";
import { normalizeAdsManagerOverview } from "../services/adsManager";

test("read-only view separates spend sources and never renders mutation controls", () => {
    const sentinel = "SENTINEL-WRITE-ACTION";
    const overview = {
        ...normalizeAdsManagerOverview({
            generated_at: "2026-07-28T12:00:00+03:00",
            range: {
                date_from: "2026-07-01",
                date_to: "2026-07-28",
                timezone: "Asia/Riyadh",
                provider: "all",
            },
            metrics: {
                provider_reported_spend_sar: 1250,
                booked_ad_expense_sar: 980,
                platform_attributed_revenue_sar: null,
                platform_roas: null,
            },
            coverage: {
                providers_total: 3,
                provider_spend_providers: 2,
                booked_expense_providers: 1,
                campaign_detail_providers: 2,
                providers_with_performance_data: 2,
                revenue_is_partial: true,
                provider_spend_is_partial: true,
                booked_expense_is_partial: false,
                source_row_limit_reached: ["snapchat_daily_facts"],
            },
            providers: [{
                provider: "meta",
                provider_label: "ميتا",
                connection_provenance: "api_connection",
                metrics: { provider_reported_spend_sar: 1250 },
                freshness: {
                    status: "fresh",
                    observed_days: 28,
                    requested_days: 28,
                    last_observed_at: "2026-07-28T10:00:00+03:00",
                },
                campaign_coverage: {
                    status: "available",
                    campaign_count: 1,
                    source_rows: 1,
                    detail: "available",
                },
                reconciliation: { status: "not_comparable" },
            }],
            daily_spend: [],
            campaigns: [{
                provider: "meta",
                provider_label: "ميتا",
                account_id: "act_123",
                campaign_id: "campaign-1",
                campaign_name: "حملة القراءة",
                spend_reported: 1250,
                spend_currency: "USD",
                spend_sar_equivalent: 1250,
                revenue_reported: 100,
                revenue_sar_equivalent: 375,
                purchases: null,
                clicks: 42,
                roas: null,
                last_observed_date: "2026-07-28",
                data_source: "provider_daily_facts",
                currency_evidence: "sar",
            }],
            campaign_pagination: { page: 1, limit: 25, total: 1, pages: 1 },
            insights: [],
            sources: [],
            policy: {
                mode: "manage",
                mutations_allowed: true,
                actions: [{ label: sentinel }],
            },
        }),
        actions: {
            create: { label: `إنشاء حملة ${sentinel}` },
            edit: { label: `تعديل الميزانية ${sentinel}` },
            pause: { label: `إيقاف الحملة ${sentinel}` },
        },
    };
    const markup = renderToStaticMarkup(
        <AdsManagerView
            overview={overview}
            filters={{
                dateFrom: "2026-07-01",
                dateTo: "2026-07-28",
                provider: "all",
                campaignQuery: "",
            }}
        />,
    );

    expect(markup).toContain("قراءة وتحليل فقط");
    expect(markup).toContain("الصرف المبلّغ من المنصات");
    expect(markup).toContain("مصروف الإعلانات المثبت محاسبيًا");
    expect(markup).toContain("1,250.00 ر.س");
    expect(markup).toContain("980.00 ر.س");
    expect(markup).toContain("حملة القراءة");
    expect(markup).toContain("375.00 ر.س");
    expect(markup).toContain(">100</span>");
    expect(markup).toContain(">USD</span>");
    expect(markup).toContain("أصلًا");
    expect(markup).toContain("الصرف المنصّي جزئي");
    expect(markup).toContain("snapchat_daily_facts");
    expect(markup).toContain("ربط API مباشر");
    expect(markup).toContain("غير متاح");
    expect(markup).toContain('href="/integrations-v2"');
    expect(markup).not.toContain(sentinel);

    const renderedButtons = markup.match(/<button[\s\S]*?<\/button>/g) || [];
    expect(renderedButtons.length).toBeGreaterThan(0);
    renderedButtons.forEach((button) => {
        expect(button).not.toMatch(/إنشاء|تعديل|إيقاف|ميزانية/);
    });
});
