const markSnapchatManualRange = jest.fn();

jest.mock("./marketingCampaignResultSource", () => ({
  markSnapchatManualRange: (...args) => markSnapchatManualRange(...args),
}));

import {
  ADS_DATE_RANGE_APPLIED_EVENT,
  installSnapchatManualRangeGuard,
  isSnapchatAdsManagerLocation,
} from "./marketingSnapchatManualRangeGuard";

describe("Snapchat manual range guard", () => {
  beforeEach(() => {
    markSnapchatManualRange.mockClear();
    delete window.__mezanSnapchatManualRangeGuardInstalled;
    window.history.replaceState({}, "", "/ads-manager?provider=snapchat");
  });

  test("recognizes only the Snapchat Ads Manager route", () => {
    expect(isSnapchatAdsManagerLocation({
      pathname: "/ads-manager",
      search: "?provider=snapchat",
    })).toBe(true);
    expect(isSnapchatAdsManagerLocation({
      pathname: "/ads-manager",
      search: "?provider=meta",
    })).toBe(false);
    expect(isSnapchatAdsManagerLocation({
      pathname: "/dashboard-v2",
      search: "",
    })).toBe(false);
  });

  test("marks a visible picker selection as manual", () => {
    expect(installSnapchatManualRangeGuard()).toBe(true);
    window.dispatchEvent(new CustomEvent(ADS_DATE_RANGE_APPLIED_EVENT, {
      detail: { dateFrom: "2026-08-01", dateTo: "2026-08-04" },
    }));
    expect(markSnapchatManualRange).toHaveBeenCalledTimes(1);
  });

  test("does not mark ranges on other provider pages", () => {
    window.history.replaceState({}, "", "/ads-manager?provider=meta");
    expect(installSnapchatManualRangeGuard()).toBe(true);
    window.dispatchEvent(new CustomEvent(ADS_DATE_RANGE_APPLIED_EVENT, {
      detail: { dateFrom: "2026-08-01", dateTo: "2026-08-04" },
    }));
    expect(markSnapchatManualRange).not.toHaveBeenCalled();
  });
});
