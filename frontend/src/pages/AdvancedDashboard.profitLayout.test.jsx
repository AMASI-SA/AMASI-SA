import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import fs from "fs";
import path from "path";
import { ProfitCard } from "./AdvancedDashboard";

jest.mock("react-router-dom", () => ({
  Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

const css = fs.readFileSync(path.resolve(__dirname, "../index.css"), "utf8");
const tune = fs.readFileSync(path.resolve(__dirname, "../profitSummarySizeTune.css"), "utf8");
const sections = ["advanced-profit-kpis", "advanced-profit-expenses", "advanced-profit-net-hero"];

function render(pending, noActivity = false) {
  const data = {
    totals: { total_sales: 700, total_orders: 2, total_ads_cost: pending ? null : 70,
      net_profit: pending ? null : 470, product_cost_complete: true,
      ads_spend_data_complete: false, ads_spend_amount_available: !pending,
      ads_spend_provisional: !pending },
    ads_v2: { spend_quality: { status: pending ? "incomplete" : "provisional",
      amount_available: !pending, amount_complete: false, provisional: !pending,
      snapchat: { reason_codes: noActivity ? ["snapchat_operational_no_activity_observed"] : [] } } },
  };
  const root = document.createElement("div");
  root.setAttribute("data-testid", "advanced-dashboard-page");
  root.innerHTML = renderToStaticMarkup(<ProfitCard data={data} />);
  return root;
}

test.each([[false, false], [true, false], [false, true]])(
  "profit styling keeps stable hooks with spend pending=%s and observation hint=%s",
  (pending, noActivity) => {
    const root = render(pending, noActivity);
    for (const section of sections) {
      const selector = '[data-testid="' + section + '"]';
      expect(root.querySelector(selector)).not.toBeNull();
      const target = '[data-testid="advanced-dashboard-page"] [data-testid="advanced-profit-summary"] > .dashboard-panel-content > ' + selector;
      expect(css).toContain(target);
      expect(root.querySelector(target)).toBe(root.querySelector(selector));
    }
    expect(Boolean(root.querySelector('[data-testid="advanced-profit-ads-incomplete-hint"]'))).toBe(pending);
    expect(Boolean(root.querySelector('[data-testid="advanced-profit-no-activity-hint"]'))).toBe(noActivity);
  }
);
test("optional warnings never shift executive CSS selectors", () => {
  expect(css).not.toMatch(/> \.dashboard-panel-content > div:nth-child\([123]\)/);
  expect(tune).not.toMatch(/\[data-testid="advanced-profit-summary"\] > div:nth-child\([234]\)/);
});
