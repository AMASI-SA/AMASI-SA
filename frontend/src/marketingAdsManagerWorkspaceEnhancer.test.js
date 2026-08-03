import {
  enhanceMarketingAdsManager,
  marketingPlatformFromLocation,
} from "./marketingAdsManagerWorkspaceEnhancer";

describe("marketing ads manager workspace enhancer", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
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
    expect(table.dataset.adsManagerDesign).toBe("v1");

    const actions = table.querySelectorAll("[data-mezan-ads-manager-actions]");
    expect(actions).toHaveLength(1);
    expect(actions[0].textContent).toContain("المسودات");
    expect(actions[0].textContent).toContain("إنشاء حملة");
    expect([...actions[0].querySelectorAll("button")].every((button) => button.disabled)).toBe(true);

    expect(table.querySelector('[data-mezan-entity-tab="campaigns"]')).toBeTruthy();
    expect(table.querySelector('[data-mezan-entity-tab="adsets"]').title).toContain("المرحلة التالية");
  });
});
