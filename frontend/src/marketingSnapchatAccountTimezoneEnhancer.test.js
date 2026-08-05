import api from "./lib/api";
import { enhanceSnapchatAccountTimezone } from "./marketingSnapchatAccountTimezoneEnhancer";
import {
  clearCampaignReportSnapshot,
  clearSnapchatRestoredReturnRange,
  prepareSnapchatAccountPage,
  rememberSnapchatAdsManagerReturnRange,
  snapchatRestoredReturnRange,
  snapchatSelectedAccountId,
} from "./marketingCampaignResultSource";

describe("Snapchat account-timezone campaign interface", () => {
  const originalAdapter = api.defaults.adapter;

  beforeEach(() => {
    window.history.replaceState({}, "", "/ads-manager?provider=snapchat");
    window.localStorage.clear();
    window.sessionStorage.clear();
    clearSnapchatRestoredReturnRange();
    clearCampaignReportSnapshot("snapchat");
    prepareSnapchatAccountPage();
    document.body.innerHTML = `
      <div data-testid="marketing-platform-workspace">
        <header></header>
        <form>
          <input type="date" value="2026-08-04" />
          <input type="date" value="2026-08-04" />
          <button type="submit">تطبيق التقرير</button>
        </form>
        <section data-testid="campaign-manager-table">
          <div>لا توجد حملات موثقة ضمن الفترة أو البحث.</div>
        </section>
      </div>
    `;
  });

  afterEach(() => {
    api.defaults.adapter = originalAdapter;
    document.body.innerHTML = "";
  });

  async function storeSnapshot({
    dateFrom = "2026-08-04",
    dateTo = "2026-08-04",
    accountId = "riyadh-account",
    timezone = "Asia/Riyadh",
  } = {}) {
    api.defaults.adapter = async (config) => ({
      data: {
        result_source: "salla",
        selected_account_id: accountId,
        account_timezone: timezone,
        date_from: dateFrom,
        date_to: dateTo,
        totals: { spend_sar: 100 },
        source: { account_rows: 1, campaign_rows: 0 },
        available_accounts: [
          {
            account_id: "riyadh-account",
            account_name: "حساب الرياض",
            currency: "SAR",
            timezone: "Asia/Riyadh",
            local_today: "2026-08-04",
          },
          {
            account_id: "us-account",
            account_name: "الحساب الأمريكي",
            currency: "USD",
            timezone: "America/Los_Angeles",
            local_today: "2026-08-03",
          },
        ],
        campaigns: [],
      },
      status: 200,
      statusText: "OK",
      headers: {},
      config,
    });
    await api.get("/integrations-v2/snapchat_ads/campaign-report", {
      params: { from_date: dateFrom, to_date: dateTo },
    });
  }

  test("runs only on the explicit Snapchat Ads Manager route", async () => {
    await storeSnapshot();
    window.history.pushState({}, "", "/products-v2?product=mpv2_1038691572");

    expect(enhanceSnapchatAccountTimezone(document)).toBe(false);
    expect(document.querySelector("[data-mezan-snapchat-account-switcher]")).toBeNull();
  });

  test("shows one separate card per account and the selected timezone policy", async () => {
    await storeSnapshot();

    expect(enhanceSnapchatAccountTimezone(document)).toBe(true);
    const switcher = document.querySelector("[data-mezan-snapchat-account-switcher]");
    const buttons = switcher.querySelectorAll("button[data-account-id]");

    expect(buttons).toHaveLength(2);
    expect(buttons[0].dataset.active).toBe("true");
    expect(switcher.textContent).toContain("Asia/Riyadh");
    expect(switcher.textContent).toContain("America/Los_Angeles");
    expect(switcher.textContent).toContain("لوحة التحكم والمحاسبة تبقيان بتوقيت الرياض");
  });

  test("is idempotent and does not replace account cards on every DOM mutation", async () => {
    await storeSnapshot();
    enhanceSnapchatAccountTimezone(document);
    const firstSwitcher = document.querySelector("[data-mezan-snapchat-account-switcher]");
    const firstAccountButton = firstSwitcher.querySelector('button[data-account-id="riyadh-account"]');

    enhanceSnapchatAccountTimezone(document);

    const secondSwitcher = document.querySelector("[data-mezan-snapchat-account-switcher]");
    const secondAccountButton = secondSwitcher.querySelector('button[data-account-id="riyadh-account"]');
    expect(secondSwitcher).toBe(firstSwitcher);
    expect(secondAccountButton).toBe(firstAccountButton);
  });

  test("switching account sets both dates to that account local today", async () => {
    await storeSnapshot();
    const form = document.querySelector("form");
    form.requestSubmit = jest.fn();
    enhanceSnapchatAccountTimezone(document);

    document.querySelector('button[data-account-id="us-account"]').click();
    const dates = [...document.querySelectorAll('input[type="date"]')].map((input) => input.value);

    expect(snapchatSelectedAccountId()).toBe("us-account");
    expect(dates).toEqual(["2026-08-03", "2026-08-03"]);
    expect(form.requestSubmit).toHaveBeenCalledTimes(1);
  });

  test("restores the saved range after returning from Products V2 without submitting today", async () => {
    window.localStorage.setItem("mezan-snapchat-manager-account-v1", "us-account");
    rememberSnapchatAdsManagerReturnRange({
      dateFrom: "2026-07-28",
      dateTo: "2026-08-04",
      accountId: "us-account",
    });
    prepareSnapchatAccountPage();
    await storeSnapshot({
      dateFrom: "2026-07-28",
      dateTo: "2026-08-04",
      accountId: "us-account",
      timezone: "America/Los_Angeles",
    });
    const form = document.querySelector("form");
    form.requestSubmit = jest.fn();

    expect(enhanceSnapchatAccountTimezone(document)).toBe(true);

    const dates = [...document.querySelectorAll('input[type="date"]')].map((input) => input.value);
    expect(dates).toEqual(["2026-07-28", "2026-08-04"]);
    expect(form.requestSubmit).not.toHaveBeenCalled();
    expect(snapchatRestoredReturnRange()).toBeNull();
  });

  test("does not claim there are no campaigns when account totals exist", async () => {
    await storeSnapshot();
    enhanceSnapchatAccountTimezone(document);

    expect(document.querySelector("[data-mezan-snapchat-campaign-coverage]").textContent)
      .toContain("إجمالي الحساب متوفر");
    expect(document.querySelector('[data-testid="campaign-manager-table"]').textContent)
      .toContain("تفاصيل الحملات قيد الاستكمال");
  });
});
