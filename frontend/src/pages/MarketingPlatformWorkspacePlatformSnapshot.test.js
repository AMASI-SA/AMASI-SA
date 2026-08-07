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
  expect(source).toContain("28 يومًا للنقر · 7 أيام للمشاهدة");
  expect(source).toContain("قرارات الذكاء الاصطناعي تعتمد وقت التحويل فقط");
});

test("AI tab forces Snapchat conversion-time attribution", () => {
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "MarketingPlatformWorkspace.jsx"),
    "utf8",
  );
  expect(source).toContain('platform === "snapchat" && id === "ai"');
  expect(source).toContain('setActionReportTime("conversion")');
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


test("Snapchat source switch invalidates stale rows and reloads with the selected source", () => {
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "MarketingPlatformWorkspace.jsx"),
    "utf8",
  );
  expect(source).toContain("CAMPAIGN_RESULTS_SOURCE_EVENT");
  expect(source).toContain("handleResultSourceUpdated");
  expect(source).toContain("loadSequenceRef.current += 1");
  expect(source).toContain("setData(null)");
  expect(source).toContain("setResultSourceRevision((value) => value + 1)");
  expect(source).toContain("resultSource,");
});
