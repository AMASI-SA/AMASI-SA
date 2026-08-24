const fs = require("fs");
const path = require("path");

function read(relativePath) {
    return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

test("Salla manual order sync is backgrounded, leased, and polled", () => {
    const page = read("src/pages/SallaIntegration.jsx");
    const routes = read("../backend/salla_integration/routes.py");
    const sync = read("../backend/salla_integration/sync.py");

    expect(page).toContain('timeZone: "Asia/Riyadh"');
    expect(page).toContain("hasRunningOrderSync");
    expect(page).toContain('api.get("/salla/sync/logs?limit=20")');
    expect(page).toContain("5_000");
    expect(page).toContain("بدأت مزامنة الفترة في الخلفية");
    expect(page).toContain('log.status === "interrupted"');

    expect(routes).toContain('@router.post("/sync/orders", status_code=202)');
    expect(routes).toContain("_acquire_order_sync_lease");
    expect(routes).toContain("asyncio.create_task(");
    expect(routes).toContain("run_orders_sync_background(");

    expect(sync).toContain("MAX_RANGE_PAGES_PER_RUN = 120");
    expect(sync).toContain("if from_date and to_date");
    expect(sync).toContain("except asyncio.CancelledError:");
    expect(sync).toContain('finish_sync_log(db, log_id, "interrupted"');
});
