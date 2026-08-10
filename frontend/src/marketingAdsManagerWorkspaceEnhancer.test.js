import {
  enhanceMarketingAdsManager,
  marketingPlatformFromLocation,
} from "./marketingAdsManagerWorkspaceEnhancer";
import { campaignResultsSource } from "./marketingCampaignResultSource";

describe("marketing ads manager workspace enhancer", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
    window.localStorage.clear();
    window.history.pushState({}, "", "/ads-manager?provider=snapchat");
  });

  test("normalizes the platform from the query string", () => {
    expect(marketingPlatformFromLocation({ search: "?provider=meta" })).toBe("meta");
    expect(marketingPlatformFromLocation({ search: "?provider=tiktok" })).toBe("tiktok");
    expect(marketingPlatformFromLocation({ search: "?provider=unknown" })).toBe("snapchat");
  });

  test("marks the campaigns workspace and adds locked primary actions once", () => {
    document.body.innerHTML = `
      <main data-testid="marketing-platform-workspace">
        <header><div></div><div>status</div></header>
        <section data-testid="campaign-manager-table">
          <div>
            <button>الحملات<span></span></button>
            <button disabled>المجموعات الإعلانية</button>
            <button disabled>الإعلانات</button>
          </div>
          <div></div>
        </section>
      </main>
    `;

    expect(enhanceMarketingAdsManager(document)).toBe(true);
    expect(enhanceMarketingAdsManager(document)).toBe(true);

    const workspace = document.querySelector('[data-testid="marketing-platform-workspace"]');
    const table = document.querySelector('[data-testid="campaign-manager-table"]');
    expect(workspace.dataset.marketingPlatform).toBe("snapchat");
    expect(workspace.dataset.adsManagerMode).toBe("campaigns");
    expect(workspace.dataset.campaignResultSource).toBe("salla");
    expect(table.dataset.adsManagerDesign).toBe("v1");

    const actions = table.querySelectorAll("[data-mezan-ads-manager-actions]");
    expect(actions).toHaveLength(1);
    expect(actions[0].textContent).toContain("المسودات");
    expect(actions[0].textContent).toContain("إنشاء حملة");
    expect([...actions[0].querySelectorAll("button")].every((button) => button.disabled)).toBe(true);

    const sourceToggles = document.querySelectorAll("[data-mezan-campaign-result-source]");
    expect(sourceToggles).toHaveLength(1);
    expect(sourceToggles[0].textContent).toContain("سلة — النتائج الفعلية");
    expect(sourceToggles[0].querySelector('[data-result-source="salla"]').dataset.active).toBe("true");

    expect(table.querySelector('[data-mezan-entity-tab="campaigns"]')).toBeTruthy();
    expect(table.querySelector('[data-mezan-entity-tab="adsets"]').title).toContain("المرحلة التالية");
  });

  test("switches the selected commercial result source and refreshes the report", () => {
    document.body.innerHTML = `
      <main data-testid="marketing-platform-workspace">
        <header>
          <div><button data-testid="marketing-platform-refresh">تحديث التقرير</button></div>
          <div>status</div>
        </header>
        <section data-testid="campaign-manager-table"><div><button>الحملات</button></div></section>
      </main>
    `;
    const refresh = document.querySelector('[data-testid="marketing-platform-refresh"]');
    refresh.click = jest.fn();

    enhanceMarketingAdsManager(document);
    document.querySelector('[data-result-source="platform"]').click();

    expect(campaignResultsSource("snapchat")).toBe("platform");
    expect(refresh.click).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-result-source="platform"]').dataset.active).toBe("true");
  });

  test("does not inject obsolete locked actions beside the native management panel", () => {
    document.body.innerHTML = `
      <main data-testid="marketing-platform-workspace">
        <header><div></div><div>status</div></header>
        <section data-testid="snapchat-campaign-management-panel"></section>
        <section data-testid="campaign-manager-table"><div><button>الحملات</button></div></section>
      </main>
    `;

    enhanceMarketingAdsManager(document);

    expect(document.querySelector("[data-mezan-ads-manager-actions]")).toBeNull();
  });
});
