const fs = require("fs");
const path = require("path");

test("decision history aborts requests and ignores stale responses", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "AdAccountDecisionHistory.jsx"),
        "utf8",
    );
    expect(source).toContain("const controller = new AbortController();");
    expect(source).toContain("historyRequestRef.current");
    expect(source).toContain("requestId !== historyRequestRef.current");
    expect(source).toContain("controller.abort();");
    expect(source).toContain("limit: PAGE_SIZE");
});
