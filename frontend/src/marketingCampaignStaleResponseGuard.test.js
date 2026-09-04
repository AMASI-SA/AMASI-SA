import {
  CAMPAIGN_STALE_RESPONSE_POLICY,
  chooseCampaignResponseData,
} from "./marketingCampaignStaleResponseGuard";

describe("Campaign stale response guard", () => {
  test("marks an older response without replacing its body", () => {
    const newest = chooseCampaignResponseData({
      sequence: 8,
      data: { date_from: "2026-08-01", date_to: "2026-08-04" },
      state: { latestSequence: 0, latestData: null },
    });
    const stale = chooseCampaignResponseData({
      sequence: 7,
      data: { date_from: "2026-08-04", date_to: "2026-08-04" },
      state: newest.state,
    });

    expect(newest.stale).toBe(false);
    expect(stale.stale).toBe(true);
    expect(stale.data).toEqual({
      date_from: "2026-08-04",
      date_to: "2026-08-04",
    });
    expect(stale.state.latestSequence).toBe(8);
  });

  test("accepts a newer response and updates the stored payload", () => {
    const selected = chooseCampaignResponseData({
      sequence: 9,
      data: { date_from: "2026-07-29", date_to: "2026-08-04" },
      state: {
        latestSequence: 8,
        latestData: { date_from: "2026-08-01", date_to: "2026-08-04" },
      },
    });

    expect(selected.stale).toBe(false);
    expect(selected.state.latestSequence).toBe(9);
    expect(selected.data.date_from).toBe("2026-07-29");
  });

  test("declares a read-only replacement policy", () => {
    expect(CAMPAIGN_STALE_RESPONSE_POLICY).toEqual({
      older_responses_cannot_replace_newer_range: true,
      replacement_uses_latest_successful_payload: false,
      provider_writes_allowed: false,
    });
  });
});
