jest.mock("react-router-dom", () => ({
    useNavigate: () => jest.fn(),
}));

import { isSnapchatPlatformSnapshotPending } from "./MarketingPlatformWorkspace";

test("Snapchat attribution UI defaults to conversion-time semantics", () => {
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "MarketingPlatformWorkspace.jsx"),
    "utf8",
  );
  expect(source).toContain('useState("conversion")');
  expect(source).toContain("وقت التحويل · موصى به");
  expect(source).toContain("وقت الظهور · مقارنة");
  expect(source).toContain("قرارات الذكاء الاصطناعي تعتمد وقت التحويل فقط");
});

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
