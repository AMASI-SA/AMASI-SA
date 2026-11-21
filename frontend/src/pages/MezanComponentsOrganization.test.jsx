import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import MezanComponentsOrganization from "./MezanComponentsOrganization";
import { getMezanComponentWorkspace } from "../services/mezanComponentCatalog";
import {
  generatedComponentGroupName,
  resourcesForComponentCategory,
} from "./MezanComponentsOrganization";
import { activeResourcesForComponentCategory } from "../services/mezanComponentOrganization";

jest.mock("react-router-dom", () => { global.TextEncoder = require("util").TextEncoder; return jest.requireActual("react-router"); });
jest.mock("../context/AuthContext", () => ({ useOptionalAuth: () => ({ user: { is_owner: true } }) }));

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


async function withOrganization(accessMode, assertion) {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  getMezanComponentWorkspace.mockResolvedValue({ components: resources, categories: [{ id: "metal", name: "مطليات" }, { id: "clothes", name: "ملابس" }], groups: [] });
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host);
  try {
    await act(async () => root.render(<MemoryRouter><MezanComponentsOrganization accessMode={accessMode}/></MemoryRouter>));
    await assertion(host);
  } finally {
    await act(async () => root.unmount()); host.remove();
  }
}

test("read-only component staff retain classification browsing without cost or organization writers", async () => {
  await withOrganization("read", async (host) => {
    expect(host.textContent).toContain("مطليات");
    expect(host.textContent).toContain("طلاء");
    expect(host.textContent).not.toContain("إدارة العناصر والتكاليف");
    expect(host.querySelector("form")).toBeNull();
    expect(host.querySelector('[data-testid="component-group-editor"]')).toBeNull();
    expect(host.textContent).not.toContain("تعديل التصنيفات");
  });
});

test("organization-write staff retain category and group controls without owner catalog costs", async () => {
  await withOrganization("write", async (host) => {
    expect(host.textContent).toContain("إضافة التصنيف");
    expect(host.querySelector('[data-testid="component-group-editor"]')).not.toBeNull();
    expect(host.textContent).toContain("تعديل التصنيفات");
    expect(host.textContent).not.toContain("إدارة العناصر والتكاليف");
  });
});
