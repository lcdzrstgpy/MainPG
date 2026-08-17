import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../styles/priceVerificationSource.css", import.meta.url), "utf8");

test("source search statistics have space between each metric and its value", () => {
  assert.match(styles, /\.pv-source-inline-stats\s*\{[^}]*gap:\s*18px/);
  assert.match(styles, /\.pv-source-inline-stats\s+span\s*\{[^}]*gap:\s*6px/);
  assert.match(styles, /\.pv-source-inline-stats\s*\{[^}]*flex-wrap:\s*wrap/);
});
