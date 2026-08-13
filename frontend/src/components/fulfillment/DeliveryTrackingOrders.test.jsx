import fs from "fs";

const source = fs.readFileSync(__filename.replace(/DeliveryTrackingOrders\.test\.jsx$/, "DeliveryTrackingOrders.jsx"), "utf8");

test("delivering board explains the Mezan orders-page sync boundary", () => {
    expect(source).toContain("جاري التوصيل");
    expect(source).toContain("صفحة الطلبات في ميزان");
    expect(source).toContain("طلبات مندوب المتجر لها مسار مستقل");
});

test("delivered board uses English digits in its date formatter contract", () => {
    expect(source).toContain('ar-SA-u-nu-latn');
    expect(source).toContain('data-testid={`delivery-tracking-${normalizedStage}`}');
});
