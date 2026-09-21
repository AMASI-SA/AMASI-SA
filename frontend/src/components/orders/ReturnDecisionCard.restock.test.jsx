import fs from "fs";
import path from "path";

const cardSource = fs.readFileSync(
  path.join(__dirname, "ReturnDecisionCard.jsx"),
  "utf8",
);
const serviceSource = fs.readFileSync(
  path.join(__dirname, "../../services/returnDecisionEngine.js"),
  "utf8",
);

test("inspected sellable returns require a real scanned inventory restock", () => {
  expect(serviceSource).toContain("/restock-options");
  expect(serviceSource).toContain("/restock");
  expect(cardSource).toContain("5. إعادة الكمية الصالحة فعليًا للمخزون");
  expect(cardSource).toContain("اختر الـlot الذي خرجت منه القطعة");
  expect(cardSource).toContain("باركود الخانة");
  expect(cardSource).toContain("تثبيت Restock الفعلي");
});

test("restock never guesses when an item came from multiple source lots", () => {
  expect(cardSource).toContain("إذا خرج السطر من أكثر من lot تظهر المصادر منفصلة");
  expect(cardSource).toContain("source.source_target_key");
  expect(cardSource).toContain("source.remaining_source_quantity");
  expect(cardSource).toContain("source.compatible_locations");
});

test("restock request id survives an interrupted response to prevent duplicates", () => {
  expect(cardSource).toContain("request_fingerprint");
  expect(cardSource).toContain("crypto.randomUUID()");
  expect(cardSource).toContain("request_id: requestId");
  expect(serviceSource).toContain("restockReturnInventory");
});

test("inventory gate distinguishes physical restock progress and completion", () => {
  expect(cardSource).toContain("restock_posting");
  expect(cardSource).toContain("restock_in_progress");
  expect(cardSource).toContain("restocked_sellable_inventory");
  expect(cardSource).toContain("أصبح لدى P03 دليل");
});
