import assert from "node:assert/strict";
import test from "node:test";

import { workspaceModules } from "./modules.ts";

test("sidebar navigation groups the product workflow around its three entry pages", () => {
  const productWorkflow = workspaceModules.find((module) => module.id === "product_workflow");

  assert.equal(productWorkflow?.defaultChildId, "daily_selection");
  assert.deepEqual(productWorkflow?.children?.map((child) => child.id), [
    "daily_selection",
    "product_processing",
    "dimension_canvas",
  ]);
  assert.deepEqual(productWorkflow?.children?.map((child) => child.label), ["采集", "AI处理", "尺寸画布"]);
});

test("sidebar navigation prioritizes price and source matching in its default sourcing workflow", () => {
  const sourcingWorkflow = workspaceModules.find((module) => module.id === "sourcing_workflow");

  assert.equal(sourcingWorkflow?.defaultChildId, "price_verification");
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.id), [
    "price_verification",
    "profit_activity",
    "profit_activity_products",
  ]);
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.label), [
    "核价/货源匹配",
    "利润活动",
    "货源关联产品库",
  ]);
});
