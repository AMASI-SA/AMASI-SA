jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
}));

import { isSnapchatPlatformSnapshotPending } from "./MarketingPlatformWorkspace";

test("flags only an incomplete Snapchat platform TOTAL snapshot", () => {
  expect(isSnapchatPlatformSnapshotPending("snapchat", {
    result_source: "platform",
    source: { platform_total_snapshot_ready: false },
  })).toBe(true);
  expect(isSnapchatPlatformSnapshotPending("snapchat", {
    result_source: "salla",
    source: { platform_total_snapshot_ready: false },
  })).toBe(false);
  expect(isSnapchatPlatformSnapshotPending("snapchat", {
    result_source: "platform",
    source: { platform_total_snapshot_ready: true },
  })).toBe(false);
});
