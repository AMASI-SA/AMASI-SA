import {
  MARKETING_ADS_TABLE_UX_POLICY,
  enhanceMarketingAdsTables,
  getAdsReportLoadState,
  isSnapchatCampaignReportRequest,
  renderAdsReportLoadIndicator,
  setAdsReportLoadState,
} from "./marketingAdsTableUXEnhancer";

describe("Marketing Ads loading indicator", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    setAdsReportLoadState("idle");
  });

  test("recognizes only the Snapchat campaign report endpoint", () => {
    expect(isSnapchatCampaignReportRequest({
      url: "/integrations-v2/snapchat_ads/campaign-report",
    })).toBe(true);
    expect(isSnapchatCampaignReportRequest({
      url: "/integrations-v2/snapchat_ads/ad-squad-report",
    })).toBe(false);
  });

  test("shows a rotating load state then a completed check", () => {
    document.body.innerHTML = `
      <div data-testid="marketing-platform-workspace">
        <form><button type="submit">تطبيق التقرير</button></form>
      </div>
    `;

    setAdsReportLoadState("loading", { detail: "2026-08-01 — 2026-08-04" });
    expect(renderAdsReportLoadIndicator(document)).toBe(true);
    let indicator = document.querySelector('[data-mezan-ads-report-load-indicator]');
    expect(indicator.dataset.state).toBe("loading");
    expect(indicator.querySelector(".mezan-ads-report-spinner")).not.toBeNull();
    expect(indicator.textContent).toContain("جاري تحميل الفترة");

    setAdsReportLoadState("success", { detail: "2026-08-01 — 2026-08-04" });
    renderAdsReportLoadIndicator(document);
    indicator = document.querySelector('[data-mezan-ads-report-load-indicator]');
    expect(indicator.dataset.state).toBe("success");
    expect(indicator.textContent).toContain("✓");
    expect(indicator.textContent).toContain("تم تحميل التقرير");
    expect(getAdsReportLoadState().state).toBe("success");
  });

  test("never mutates table columns after React renders them", () => {
    document.body.innerHTML = `
      <table>
        <thead><tr><th data-column-id="spend">المبلغ المصروف</th><th data-column-id="sales">مبيعات سلة</th></tr></thead>
        <tbody><tr><td>100</td><td>250</td></tr></tbody>
      </table>
    `;
    const before = document.body.innerHTML;
    expect(enhanceMarketingAdsTables(document)).toBe(0);
    expect(document.body.innerHTML).toBe(before);
    expect(document.querySelector('[data-mezan-folded-spend-cell]')).toBeNull();
    expect(document.querySelector('[data-mezan-sales-with-spend]')).toBeNull();
  });

  test("declares the native React table boundary", () => {
    expect(MARKETING_ADS_TABLE_UX_POLICY).toEqual({
      load_indicator_enabled: true,
      table_dom_mutations_enabled: false,
      columns_rendered_by_react: true,
      provider_writes_allowed: false,
    });
  });
});
