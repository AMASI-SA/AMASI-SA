const fs = require("fs");
const path = require("path");

test("ignores stale report success, error and completion state", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "MarketingPlatformWorkspace.jsx"),
        "utf8",
    );
    expect(source).toContain("const loadSequenceRef = useRef(0);");
    expect(source).toContain("const requestId = ++loadSequenceRef.current;");
    expect(source.match(/requestId !== loadSequenceRef\.current/g)?.length).toBeGreaterThanOrEqual(2);
    expect(source).toContain("if (requestId === loadSequenceRef.current)");
    expect(source).toContain("setData((current) => {");
    expect(source).toContain("mergePaginatedRows(");
    expect(source).toContain("setError(\"\");");
});
