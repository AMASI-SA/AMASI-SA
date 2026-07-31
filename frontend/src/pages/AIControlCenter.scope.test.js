import fs from "fs";
import path from "path";

describe("AI Control Center temporary Qoyod scope", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "AIControlCenter.jsx"),
        "utf8",
    );

    test("removes Qoyod statistics and blockers from the AI page", () => {
        expect(source).not.toContain(
            "/integrations/qoyod/first-sync-monitor/stats/summary",
        );
        expect(source).not.toContain('title="فشل قيود"');
        expect(source).not.toContain('title: "قيود / ZATCA"');
        expect(source).not.toContain("qoyod_failed:");
        expect(source).not.toContain("qoyodStats");
    });

    test("shows an explicit paused-scope notice", () => {
        expect(source).toContain("qoyod-analysis-paused-notice");
        expect(source).toContain(
            "تحليل قيود والطلبات غير المرحلة موقوف مؤقتًا",
        );
        expect(source).toContain("QOYOD_ANALYSIS_PAUSED");
    });
});
