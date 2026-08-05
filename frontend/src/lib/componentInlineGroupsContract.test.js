import fs from "fs";
import path from "path";

test("product operations shows groups inline before individual resources", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "../components/products/ProductOperationsEditor.jsx"),
        "utf8",
    );
    const groups = source.indexOf('data-testid="product-groups-inline"');
    const services = source.indexOf('title="الخدمات المفردة"');
    const components = source.indexOf('title="المكوّنات المفردة"');
    expect(groups).toBeGreaterThan(-1);
    expect(services).toBeGreaterThan(groups);
    expect(components).toBeGreaterThan(groups);
    expect(source).not.toContain("product-group-picker-modal");
    expect(source).not.toContain("open-product-group-picker");
});

test("organization tab refreshes workspace when selected", () => {
    const source = fs.readFileSync(
        path.join(__dirname, "../pages/MezanComponentsOrganization.jsx"),
        "utf8",
    );
    expect(source).toContain('setTab("organization"); load({ quiet: true });');
});
