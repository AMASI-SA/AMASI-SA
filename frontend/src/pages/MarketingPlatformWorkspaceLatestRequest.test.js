const fs = require("fs");
const path = require("path");

test("ignores stale report success, error and completion state", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "MarketingPlatformWorkspace.jsx"),
        "utf8",
    );
    expect(source).toContain("const loadSequenceRef = useRef(0);");
    expect(source).toContain("const requestSequence = ++loadSequenceRef.current;");
    expect(source).toContain("const requestId = `snap-report-${Date.now()}-${requestSequence}`;");
    expect(source.match(/requestSequence !== loadSequenceRef\.current/g)?.length).toBeGreaterThanOrEqual(2);
    expect(source).toContain("if (requestSequence === loadSequenceRef.current)");
    expect(source).toContain("setData(null);");
    expect(source).toContain("result.request_id !== requestId");
    expect(source).toContain("setError(\"\");");
});
