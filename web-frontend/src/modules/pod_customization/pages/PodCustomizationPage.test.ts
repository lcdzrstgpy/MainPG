import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodCustomizationPage.tsx", import.meta.url), "utf8");
const gallerySource = readFileSync(new URL("../components/PodBatchGallery.tsx", import.meta.url), "utf8");
const listingDrawerSource = readFileSync(new URL("../components/PodListingDetailDrawer.tsx", import.meta.url), "utf8");
const modelSource = readFileSync(new URL("../data/podCustomizationModel.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");

test("POD page does not render the pending billing authorization banner", () => {
  assert.doesNotMatch(source, /待恢复的 POD 任务/);
  assert.doesNotMatch(source, /个任务需要重新授权/);
  assert.doesNotMatch(source, />继续任务</);
});

test("current template card follows the selected template and current draft", () => {
  assert.match(source, /const summaryTemplate = selectedTemplateSnapshot \?\? selectedTemplate;/);
  assert.match(source, /const summaryFields = businessFieldsForApi\(businessFields\);/);
  assert.doesNotMatch(source, /const summaryTemplate = activeBatch/);
  assert.doesNotMatch(source, /const summaryFields = activeBatch/);
});

test("saving a system template uses the image snapshot currently shown to the user", () => {
  const saveTemplate = source.slice(source.indexOf("const saveCurrentAsSystemTemplate"), source.indexOf("const applySystemTemplate"));
  assert.match(saveTemplate, /const templateSnapshot = selectedTemplateSnapshot \?\? selectedTemplate;/);
  assert.match(saveTemplate, /template: templateSnapshot/);
});

test("POD customer-facing copy does not mention four-grid generation", () => {
  assert.doesNotMatch(source, /四宫格/);
  assert.doesNotMatch(gallerySource, /四宫格/);
  assert.doesNotMatch(modelSource, /四宫格/);
  assert.doesNotMatch(source, /每款一次.*请求/);
  assert.doesNotMatch(source, /自动拆分为四张商品图/);
  assert.doesNotMatch(gallerySource, /每款一次.*请求/);
});

test("POD listing result labels identify the original scene as primary and the original hero as material", () => {
  assert.match(gallerySource, /const ROLE_LABELS = \["主图", "细节图 A", "细节图 B", "素材图"\]/);
  assert.match(listingDrawerSource, /const ROLE_LABELS = \["主图", "细节图 A", "细节图 B", "素材图"\]/);
  assert.match(modelSource, /四格顺序固定为主图、细节图 A、细节图 B、素材图。/);
});

test("system-template save control is independent and immediately precedes generation", () => {
  const advancedStart = source.indexOf('<div className="pod-advanced-prompt">');
  const advancedEnd = source.indexOf('<div className="pod-volume-inline">', advancedStart);
  const saveControl = source.indexOf('className="pod-save-system-template-button"');
  const startControl = source.indexOf('className="pod-start-button"');

  assert.ok(saveControl >= 0, "expected a dedicated system-template save control");
  assert.ok(startControl > saveControl, "expected the save control above start generation");
  assert.ok(saveControl > advancedEnd, "expected the save control outside the advanced prompt editor");
  assert.doesNotMatch(source.slice(advancedStart, advancedEnd), /保存为系统模板/);
});

test("system-template save control has its own secondary action treatment", () => {
  assert.match(source, /className="pod-save-system-template-button"[\s\S]*?<span className="iconfont icon-save" aria-hidden="true" \/>[\s\S]*?className="pod-save-system-template-copy"[\s\S]*?<b>保存为系统模板<\/b>[\s\S]*?<small>保存当前提示词与模板图<\/small>/);
  assert.match(styles, /\.pod-save-system-template-button \{[\s\S]*?display: flex;[\s\S]*?border: 1px solid var\(--pod-border-strong\);/);
  assert.match(styles, /\.pod-save-system-template-copy small \{[\s\S]*?font-size:/);
});

test("listing editor keeps dimensions and weight on each SKU and appends a blank SKU", () => {
  const listingFieldDeclaration = source.slice(source.indexOf("const LISTING_FIELDS"), source.indexOf("function toSummary"));

  assert.doesNotMatch(listingFieldDeclaration, /length_cm/);
  assert.doesNotMatch(listingFieldDeclaration, /width_cm/);
  assert.doesNotMatch(listingFieldDeclaration, /height_cm/);
  assert.doesNotMatch(listingFieldDeclaration, /weight_g/);
  assert.match(source, /新增 SKU/);
  assert.match(source, /aria-label="SKU 名称"/);
  assert.match(source, /aria-label="SKU 长（cm）"/);
  assert.match(source, /aria-label="SKU 宽（cm）"/);
  assert.match(source, /aria-label="SKU 高（cm）"/);
  assert.match(source, /aria-label="SKU 重量（g）"/);
  assert.match(source, /aria-label="删除 SKU"/);
  assert.match(source, /updateSku/);
  assert.match(source, /addSku/);
  assert.match(source, /removeSku/);
  assert.match(source, /skus: \[\.\.\.current\.skus, \{ name: "", length_cm: "", width_cm: "", height_cm: "", weight_g: "" \}\]/);
});

test("SKU validation marks each invalid field beside its own input", () => {
  assert.match(source, /const \[skuFieldErrors, setSkuFieldErrors\] = useState<SkuFieldErrors>\(\{\}\);/);
  assert.match(source, /validateSkuFields\(listingFields\.skus\)/);
  assert.match(source, /aria-invalid=\{Boolean\(skuFieldErrors\[skuErrorKey\(index, "name"\)\]\)\}/);
  assert.match(source, /className="pod-sku-field-error"/);
  assert.match(source, /SKU「\$\{skuLabel\}」的\$\{SKU_FIELD_LABELS\[key\]\}/);
});

test("SKU rows fit all five fields in the existing desktop setup panel and only wrap on narrow viewports", () => {
  assert.match(styles, /\.pod-sku-input-row \{ display: grid; grid-template-columns: minmax\(62px, 1\.14fr\) repeat\(4, minmax\(32px, \.6fr\)\) 20px; align-items: end; gap: 3px; \}/);
  assert.match(styles, /@media \(max-width: 420px\) \{[\s\S]*?\.pod-sku-input-row \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\) 22px; \}/);
  assert.match(styles, /@media \(max-width: 420px\) \{[\s\S]*?\.pod-sku-input-row > button \{ grid-column: 3; grid-row: 2; width: 22px; \}/);
});

test("adding SKU stops at the supported limit with an accessible explanation", () => {
  assert.match(source, /const skuLimitReached = listingFields\.skus\.length >= 100;/);
  assert.match(source, /disabled=\{skuLimitReached\}/);
  assert.match(source, /aria-describedby=\{skuLimitReached \? "pod-sku-limit-notice" : undefined\}/);
  assert.match(source, /已达到 100 个 SKU 上限。/);
});

test("listing detail shows SKU dimensions and weight while retaining legacy dimensions", () => {
  assert.match(listingDrawerSource, /SKU 规格、尺寸与重量/);
  assert.match(listingDrawerSource, /listingFields\?\.skus/);
  assert.match(listingDrawerSource, /sku\.weight_g/);
  assert.match(listingDrawerSource, /legacyListingFields\.weight_g/);
  assert.match(listingDrawerSource, /legacyListingFields\.length_cm/);
  assert.match(listingDrawerSource, /legacyListingFields\.width_cm/);
  assert.match(listingDrawerSource, /legacyListingFields\.height_cm/);
});

test("creating a batch keeps the current prompt draft for later refreshes", () => {
  const startBatch = source.slice(source.indexOf("const startBatch"), source.indexOf("const uploadTemplate"));
  assert.doesNotMatch(startBatch, /setCurrentBatchEdit\(null\)/);
});

test("a local draft recovery warning survives initial POD data loading", () => {
  const bootstrap = source.slice(source.indexOf("const bootstrap"), source.indexOf("useEffect(() => {\n    const updateVisibility"));
  assert.doesNotMatch(bootstrap, /setError\(""\)/);
});

test("POD local state fails closed when the signed-in account scope is incomplete", () => {
  assert.match(source, /const accountId = \(account\?\.account_id \|\| account\?\.customer_id\)\?\.trim\(\) \?\? "";/);
  assert.match(source, /const workspaceId = \(account\?\.workspace_id \|\| account\?\.workspace_code\)\?\.trim\(\) \?\? "";/);
  assert.match(source, /return accountId && workspaceId \? \{ accountId, workspaceId \} : null;/);
  assert.doesNotMatch(source, /account\?\.account_id \|\| "default"/);
});

test("POD page opens a batch failed-retry dialog and refreshes the batch after submitting selections", () => {
  assert.match(source, /import \{ PodFailedRetryDialog \} from "\.\.\/components\/PodFailedRetryDialog"/);
  assert.match(source, /const \[failedRetryOpen, setFailedRetryOpen\] = useState\(false\);/);
  assert.match(source, /podCustomizationApi\.retryFailed\(activeBatch\.id, request\)/);
  assert.match(source, /setNotice\(`已提交图片重试 \$\{request\.image_style_indices\.length\} 款、标题重试 \$\{request\.title_style_indices\.length\} 款。`\)/);
  assert.match(source, /onOpenFailedRetry=\{\(\) => setFailedRetryOpen\(true\)\}/);
  assert.match(source, /<PodFailedRetryDialog[\s\S]*?open=\{failedRetryOpen\}/);
});

test("result header exposes a batch failed-retry entry beside the export action", () => {
  assert.match(gallerySource, /onOpenFailedRetry: \(\) => void;/);
  assert.match(gallerySource, /onClick=\{onOpenFailedRetry\}>批量重试失败项<\/button>/);
  assert.match(gallerySource, /disabled=\{Boolean\(busyAction\)\}/);
});
