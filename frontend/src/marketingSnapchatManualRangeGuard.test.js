const mockMarkSnapchatManualRange = jest.fn();

jest.mock("./marketingCampaignResultSource", () => ({
  markSnapchatManualRange: (...args) => mockMarkSnapchatManualRange(...args),
}));

import {
  ADS_DATE_RANGE_APPLIED_EVENT,
  installSnapchatManualRangeGuard,
  isSnapchatAdsManagerLocation,
} from "./marketingSnapchatManualRangeGuard";

describe("Snapchat manual range guard", () => {
  beforeEach(() => {
    mockMarkSnapchatManualRange.mockClear();
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

  test("marks a visible picker selection as manual with its exact dates", () => {
    expect(installSnapchatManualRangeGuard()).toBe(true);
    const detail = { dateFrom: "2026-08-01", dateTo: "2026-08-04" };
    window.dispatchEvent(new CustomEvent(ADS_DATE_RANGE_APPLIED_EVENT, { detail }));
    expect(mockMarkSnapchatManualRange).toHaveBeenCalledTimes(1);
    expect(mockMarkSnapchatManualRange).toHaveBeenCalledWith(detail);
  });

  test("does not mark ranges on other provider pages", () => {
    window.history.replaceState({}, "", "/ads-manager?provider=meta");
    expect(installSnapchatManualRangeGuard()).toBe(true);
    window.dispatchEvent(new CustomEvent(ADS_DATE_RANGE_APPLIED_EVENT, {
      detail: { dateFrom: "2026-08-01", dateTo: "2026-08-04" },
    }));
    expect(mockMarkSnapchatManualRange).not.toHaveBeenCalled();
  });
});
