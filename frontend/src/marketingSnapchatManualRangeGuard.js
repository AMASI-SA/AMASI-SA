import { markSnapchatManualRange } from "./marketingCampaignResultSource";

export const ADS_DATE_RANGE_APPLIED_EVENT = "mezan:ads-date-range-applied";

export function isSnapchatAdsManagerLocation(locationLike = window.location) {
  try {
    const pathname = String(locationLike?.pathname || "").replace(/\/+$/, "") || "/";
    if (pathname !== "/ads-manager") return false;
    const provider = new URLSearchParams(locationLike?.search || "").get("provider");
    return String(provider || "").toLowerCase() === "snapchat";
  } catch {
    return false;
  }
}

export function installSnapchatManualRangeGuard() {
  if (typeof window === "undefined") return false;
  if (window.__mezanSnapchatManualRangeGuardInstalled) return false;
  window.__mezanSnapchatManualRangeGuardInstalled = true;
  window.addEventListener(ADS_DATE_RANGE_APPLIED_EVENT, () => {
    if (isSnapchatAdsManagerLocation(window.location)) {
      markSnapchatManualRange();
    }
  });
  return true;
}

if (typeof window !== "undefined" && process.env.NODE_ENV !== "test") {
  installSnapchatManualRangeGuard();
}
