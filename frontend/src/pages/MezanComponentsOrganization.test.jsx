import {
  generatedComponentGroupName,
  resourcesForComponentCategory,
} from "./MezanComponentsOrganization";
import { activeResourcesForComponentCategory } from "../services/mezanComponentOrganization";

jest.mock("./MezanComponentsProduction", () => () => null);
jest.mock("../services/mezanComponentCatalog", () => ({
  getMezanComponentWorkspace: jest.fn(),
}));
jest.mock("../services/mezanComponentOrganization", () => {
  const actual = jest.requireActual("../services/mezanComponentOrganization");
  return {
    ...actual,
    createComponentCategory: jest.fn(),
    saveComponentGroup: jest.fn(),
    saveResourceCategories: jest.fn(),
    updateComponentCategory: jest.fn(),
  };
});

const resources = [
  { id: "paint", name: "طلاء", track_inventory: false, category_ids: ["metal"] },
  { id: "cut", name: "قص", track_inventory: false, category_ids: ["metal"] },
  { id: "engrave", name: "نحت", track_inventory: false, category_ids: ["metal"] },
  { id: "bag", name: "كيس", track_inventory: true, category_ids: ["metal", "clothes"] },
];

test("group name inherits service names in selected order", () => {
  expect(generatedComponentGroupName(resources, ["paint", "cut", "engrave"]))
    .toBe("طلاء - قص - نحت");
});

test("category filter returns only services or components for that classification", () => {
  expect(resourcesForComponentCategory(resources, "metal", "service").map((row) => row.id))
    .toEqual(["paint", "cut", "engrave"]);
  expect(resourcesForComponentCategory(resources, "clothes", "component").map((row) => row.id))
    .toEqual(["bag"]);
});

test("one component may be shared by multiple classifications", () => {
  expect(resourcesForComponentCategory(resources, "metal", "component")[0].id).toBe("bag");
  expect(resourcesForComponentCategory(resources, "clothes", "component")[0].id).toBe("bag");
});

test("stopped items remain visible for management but are excluded from new groups", () => {
  const withStopped = resources.map((row) => (
    row.id === "cut" ? { ...row, status: "inactive" } : row
  ));

  expect(resourcesForComponentCategory(withStopped, "metal", "service").map((row) => row.id))
    .toEqual(["paint", "cut", "engrave"]);
  expect(activeResourcesForComponentCategory(withStopped, "metal", "service").map((row) => row.id))
    .toEqual(["paint", "engrave"]);
});
