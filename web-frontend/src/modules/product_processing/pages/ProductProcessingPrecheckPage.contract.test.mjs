import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./ProductProcessingPrecheckPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/productProcessingApi.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../types/index.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/ProductProcessingVerifyPage.css", import.meta.url), "utf8");

test("precheck renders editable matched SKU package measurements and keeps unmatched rows display-only", () => {
  assert.match(page, /SKU 包装件重尺/);
  assert.match(page, /effectiveShippingPackageRecords/);
  assert.match(page, /setShippingPackageField/);
  assert.match(page, /const editable = isEditableShippingPackageRecord\(record\);/);
  assert.match(page, /const recordDisabled = mutationsLocked \|\| !editable;/);
  assert.match(page, /规格/);
  assert.match(page, /体积\(cm³\)/);
  assert.match(page, /匹配状态\/来源/);
});

test("preview save payload sends SKU package overrides without replacing source records", () => {
  assert.match(api, /shipping_package_records\?: Record<string, ShippingPackageRecordOverride>/);
  assert.match(types, /shipping_package_records\?: ShippingPackageRecord\[\]/);
  assert.match(types, /shipping_package_records\?: Record<string, ShippingPackageRecordOverride>/);
  assert.match(page, /shipping_package_records: effectiveShippingPackageOverrides\(item\)/);
});

test("precheck only keeps overrides for matched source SKU package rows with valid keys", () => {
  assert.match(page, /const isEditableShippingPackageRecord = \(record: ShippingPackageRecord\): boolean =>/);
  assert.match(page, /record\.match_status === 'matched' && record\.variant_key\.trim\(\)\.length > 0/);
  assert.match(page, /Object\.fromEntries\(\s*Object\.entries\(combined\)\.filter/);
  assert.match(page, /sourceRecords\.some\(\s*\(record\) => isEditableShippingPackageRecord\(record\) && record\.variant_key === variantKey/);
  assert.match(page, /shipping_package_records: effectiveShippingPackageOverrides\(item\),/);
});

test("precheck edits package volume for matched SKU rows", () => {
  assert.match(page, /\['length_cm', 'width_cm', 'height_cm', 'volume_cm3'\] as const/);
  assert.match(page, /setShippingPackageField\(draftId, record\.variant_key, field, event\.target\.value\)/);
});

test("precheck highlights variant translations that retained their original value", () => {
  assert.match(types, /variant_translation_review_values\?: string\[\]/);
  assert.match(page, /item\.variant_translation_review_values\?\.length/);
  assert.match(page, /规格翻译待确认/);
  assert.match(page, /系统已保留原值，请在导出前人工修改/);
  assert.match(styles, /\.precheck-variant-review\s*\{/);
  assert.match(styles, /border-left:\s*4px solid #d94855/);
});
