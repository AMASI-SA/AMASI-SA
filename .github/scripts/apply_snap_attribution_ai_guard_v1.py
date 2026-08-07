from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


page = "frontend/src/pages/MarketingPlatformWorkspace.jsx"
replace_once(
    page,
    '''                        onClick={() => setActiveTab(id)}
''',
    '''                        onClick={() => {
                            if (platform === "snapchat" && id === "ai") {
                                setActionReportTime("conversion");
                            }
                            setActiveTab(id);
                        }}
''',
)

# AI guard must be visible in the source contract as well as enforced by Backend.
test = "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"
marker = '''test("flags only an incomplete Snapchat platform TOTAL snapshot", () => {
'''
addition = '''test("AI tab forces Snapchat conversion-time attribution", () => {
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "MarketingPlatformWorkspace.jsx"),
    "utf8",
  );
  expect(source).toContain('platform === "snapchat" && id === "ai"');
  expect(source).toContain('setActionReportTime("conversion")');
});

'''
p = Path(test)
text = p.read_text(encoding="utf-8")
if addition not in text:
    if marker not in text:
        raise SystemExit("frontend AI guard marker missing")
    p.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")

print("SNAP_ATTRIBUTION_AI_GUARD_V1_APPLIED")
