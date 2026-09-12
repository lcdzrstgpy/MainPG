import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodCustomizationPage.tsx", import.meta.url), "utf8");
const drawerSource = readFileSync(new URL("../components/SpecCardDrawer.tsx", import.meta.url), "utf8");
const editorSource = readFileSync(new URL("../components/SpecCardTableEditor.tsx", import.meta.url), "utf8");
const appearanceSource = readFileSync(new URL("../components/SpecCardAppearanceControls.tsx", import.meta.url), "utf8");
const previewSource = readFileSync(new URL("../components/SpecCardPreview.tsx", import.meta.url), "utf8");
const modelSource = readFileSync(new URL("../data/podCustomizationModel.ts", import.meta.url), "utf8");
const draftSource = readFileSync(new URL("../data/podCustomizationDraft.ts", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../api/podCustomizationApi.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");

function between(startMarker: string, endMarker: string): string {
  return source.slice(source.indexOf(startMarker), source.indexOf(endMarker));
}

test("the spec-card entry sits below the SKU preset with its required marker and summary", () => {
  const skuEditor = source.indexOf('className="pod-sku-editor"');
  const entry = source.indexOf('className="pod-spec-card-entry"');
  const volume = source.indexOf('<div className="pod-volume-inline">');

  assert.ok(skuEditor >= 0, "expected the SKU preset block");
  assert.ok(entry > skuEditor, "expected the spec-card entry below the SKU preset block");
  assert.ok(volume > entry, "expected the spec-card entry inside the listing section");
  assert.match(source, /aria-label="规格卡配置"/);
  assert.match(source, /className="pod-spec-card-entry-button"[\s\S]*?>批量添加尺寸<em>\*<\/em>/);
  assert.match(source, /isSpecCardConfigured\(specCard\) \? "pod-spec-card-entry-summary" : "pod-spec-card-entry-summary is-warning"/);
  assert.match(source, /specCardSummaryText\(specCard\)/);
  assert.match(styles, /\.pod-spec-card-entry-summary\.is-warning \{ color: #a84c48;/);
});

test("the spec-card configuration is draft state restored from the local draft", () => {
  assert.match(source, /const \[specCard, setSpecCard\] = useState<SpecCardConfig>\(initialDraft\.state\.spec_card \?\? EMPTY_SPEC_CARD\);/);
  assert.match(source, /spec_card: specCard,/);
  assert.match(source, /, listingFields, selectedTemplateId, specCard, systemTemplates\]\);/);
  assert.match(draftSource, /spec_card: SpecCardConfig;/);
  assert.match(draftSource, /spec_card: createEmptySpecCard\(\)/);
  assert.match(draftSource, /value\.spec_card === undefined \|\| isSpecCardConfig\(value\.spec_card\)/);
});

test("starting a batch is blocked while the spec-card table is not configured", () => {
  const startBatch = between("const startBatch", "const uploadTemplate");

  assert.match(startBatch, /if \(!isSpecCardConfigured\(specCard\)\) \{/);
  assert.match(startBatch, /setError\("请先点击「批量添加尺寸」完成表格配置。"\);/);
  assert.ok(
    startBatch.indexOf("isSpecCardConfigured(specCard)") < startBatch.indexOf("podCustomizationApi.createBatch"),
    "expected the required check before the create-batch request",
  );
  assert.match(startBatch, /listingFieldsForApi\(listingFields, specCard\)/);
});

test("the spec-card configuration is frozen into the create-batch listing snapshot", () => {
  assert.match(modelSource, /export function listingFieldsForApi\(\s*fields: PodListingFieldsDraft,\s*specCard\?: SpecCardConfig \| null,\s*\): PodListingFieldsResult \{/);
  assert.match(modelSource, /\.\.\.\(specCard \? \{ spec_card: specCardForApi\(specCard\) \} : \{\}\)/);
  assert.match(modelSource, /export function isSpecCardConfigured\(config: SpecCardConfig \| null \| undefined\): boolean \{/);
  assert.match(modelSource, /export const SPEC_CARD_DIMENSION_HEADER = \["尺寸图", "长", "宽", "高"\] as const;/);
  // 必填口径：每个 SKU 行的长/宽/高（第 2/3/4 列）都要非空，表头行与第 1 列不参与。
  assert.match(modelSource, /\[1, 2, 3\]\.every\(\(column\) => typeof row\[column\] === "string" && row\[column\]\.trim\(\)\.length > 0\)/);
  assert.match(modelSource, /export function specCardSummaryText\(config: SpecCardConfig \| null \| undefined\): string \{/);
  assert.match(modelSource, /return `\$\{rows\} 行 · \$\{style\} · \$\{corner\}`;/);
  assert.match(modelSource, /if \(!config \|\| !isSpecCardConfigured\(config\)\) return "未配置";/);
  assert.match(modelSource, /export const EMPTY_SPEC_CARD: SpecCardConfig = \{/);
});

test("the drawer mirrors the shared drawer shell with dialog semantics", () => {
  assert.match(source, /import \{ SpecCardDrawer \} from "\.\.\/components\/SpecCardDrawer";/);
  assert.match(source, /<SpecCardDrawer[\s\S]*?open=\{specCardDrawerOpen\}[\s\S]*?config=\{specCard\}[\s\S]*?batch=\{activeBatch\}/);
  assert.match(drawerSource, /<div className="pod-spec-card-drawer-layer">/);
  assert.match(drawerSource, /className="pod-spec-card-drawer-backdrop"/);
  assert.match(drawerSource, /role="dialog" aria-modal="true" aria-label=\{SPEC_CARD_DRAWER_TITLE\}/);
  assert.match(drawerSource, /export const SPEC_CARD_DRAWER_TITLE = "批量添加尺寸";/);
  // 用户规格（2026-09-11）：抽屉不再展示「表格内容会原样印到第 4 张图上」这类说明文案。
  assert.doesNotMatch(drawerSource, /表格内容会原样印到第 4 张图上/);
  assert.match(styles, /\.pod-spec-card-drawer \{ position: absolute;[\s\S]*?width: min\(760px, 94vw\);/);
});

test("the drawer edits the table, the style and the corner through the dedicated sections", () => {
  assert.match(drawerSource, /<SpecCardTableEditor cells=\{cells\} onChange=\{setCells\} disabled=\{readOnly\} \/>/);
  assert.match(drawerSource, /<SpecCardAppearanceControls[\s\S]*?onCornerChange=\{setCorner\}/);
  assert.match(drawerSource, /<SpecCardPreview cells=\{cells\} style=\{style\} corner=\{corner\} baseTemplateId=\{baseTemplateId\} \/>/);
  assert.match(appearanceSource, /const CORNER_OPTIONS: SpecCardCorner\[\] = \["top-left", "top-right", "bottom-left", "bottom-right"\];/);
  assert.match(appearanceSource, /role="radiogroup" aria-label="卡片位置"/);
  assert.match(appearanceSource, /data-corner=\{option\}/);
  assert.match(styles, /\.pod-spec-card-corner-grid \{ display: grid;[\s\S]*?grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
});

test("the table editor enforces the fixed structure and cell limit without rewriting content", () => {
  // 行数跟随 SKU 数量（1 行表头 + 每个 SKU 一行），列数固定为表头列数。
  assert.match(modelSource, /export const SPEC_CARD_MAX_ROWS = MAX_POD_SKU_COUNT \+ 1;/);
  assert.match(modelSource, /export const SPEC_CARD_COLUMNS = SPEC_CARD_DIMENSION_HEADER\.length;/);
  assert.match(modelSource, /export const SPEC_CARD_MAX_COLUMNS = 6;/);
  assert.match(modelSource, /export const SPEC_CARD_CELL_MAX_LENGTH = 120;/);
  assert.match(editorSource, /maxLength=\{forced \? undefined : SPEC_CARD_CELL_MAX_LENGTH\}/);
  assert.match(editorSource, /\{cell\.length\}\/\{SPEC_CARD_CELL_MAX_LENGTH\}/);
  assert.match(editorSource, /if \(value\.length > SPEC_CARD_CELL_MAX_LENGTH\) \{[\s\S]*?return;\s*\}/);
  assert.match(editorSource, /每列居中/);
  // 表头行与第 1 列为强制只读单元格，由 SKU 预设自动映射。
  assert.match(editorSource, /const forced = isForcedCell\(rowIndex, columnIndex\);/);
  assert.match(editorSource, /disabled=\{disabled \|\| forced\}/);
  assert.match(editorSource, /readOnly=\{forced\}/);
  // 结构固定后不再提供「＋ 添加行 / ＋ 添加列」按钮。
  assert.doesNotMatch(editorSource, /添加行/);
  assert.doesNotMatch(editorSource, /添加列/);
});

test("the table editor injects no default table copy and no text processing", () => {
  assert.doesNotMatch(editorSource, /翻译/);
  assert.doesNotMatch(editorSource, /单位/);
  assert.doesNotMatch(editorSource, /占位符/);
  assert.doesNotMatch(editorSource, /变量/);
  assert.doesNotMatch(editorSource, /一键模板/);
  assert.doesNotMatch(editorSource, /translate/i);
  assert.doesNotMatch(editorSource, /Specification/i);
  assert.doesNotMatch(editorSource, /Size Chart/i);
  // 表格编辑器不发起任何请求：预览是独立组件，重印只在抽屉底部操作里。
  assert.doesNotMatch(editorSource, /podCustomizationApi/);
  assert.doesNotMatch(editorSource, /previewSpecCard/);
  assert.doesNotMatch(editorSource, /fetch\(/);
  assert.doesNotMatch(appearanceSource, /translate|翻译|占位符|一键模板|podCustomizationApi/i);
});

test("POD spec-card copy never ships a system-injected default header row", () => {
  for (const text of [source, drawerSource, editorSource, appearanceSource, previewSource]) {
    assert.doesNotMatch(text, /尺寸表/);
    assert.doesNotMatch(text, /Specification/i);
    assert.doesNotMatch(text, /Size Chart/i);
  }
});

test("the preview uses the same-source render endpoint with a debounce and a non-blocking retry", () => {
  assert.match(previewSource, /export const SPEC_CARD_PREVIEW_DEBOUNCE_MS = 300;/);
  assert.match(previewSource, /window\.setTimeout\(\(\) => \{/);
  assert.match(previewSource, /podCustomizationApi\.previewSpecCard\(\{/);
  assert.match(previewSource, /base_template_id: baseTemplateId/);
  assert.match(previewSource, /<img src=\{image\} alt="规格卡示意图" \/>/);
  assert.match(previewSource, /示意效果，最终以生成结果为准。/);
  assert.match(previewSource, /setReloadToken\(\(token\) => token \+ 1\)/);
  assert.match(previewSource, /role="alert"/);
  assert.match(apiSource, /previewSpecCard: \(body: SpecCardPreviewRequest\) => httpJson<SpecCardPreviewResponse>\(/);
  assert.match(apiSource, /`\$\{API_BASE\}\/spec-card\/preview`/);
  assert.match(apiSource, /reprintSpecCard: \(batchId: string, body: SpecCardReprintRequest\) => httpJson<SpecCardReprintResponse>\(/);
  assert.match(apiSource, /`\$\{API_BASE\}\/batches\/\$\{encodeURIComponent\(batchId\)\}\/spec-card\/reprint`/);
});

test("a running batch freezes the drawer while a terminal batch offers the full-batch reprint", () => {
  assert.match(drawerSource, /const SPEC_CARD_REPRINTABLE_STATUSES = new Set<PodBatchStatus>\(\["completed", "partial_failure", "failed"\]\);/);
  assert.match(drawerSource, /export function specCardDrawerMode\(status\?: PodBatchStatus \| null\): SpecCardDrawerMode \{/);
  assert.match(drawerSource, /return SPEC_CARD_REPRINTABLE_STATUSES\.has\(status\) \? "terminal" : "frozen";/);
  assert.match(drawerSource, /const readOnly = mode === "frozen" && !unlocked;/);
  assert.match(drawerSource, /配置已冻结；改动将在下一批次生效/);
  assert.match(drawerSource, /disabled=\{readOnly\} onClick=\{saveConfig\}>保存到本批次<\/button>/);
  assert.match(styles, /\.pod-spec-card-frozen-banner \{/);
});

test("the drawer footer keeps the local actions and reports reprint progress and results", () => {
  assert.match(drawerSource, />保存到本批次<\/button>/);
  assert.match(drawerSource, />恢复默认<\/button>/);
  // 用户规格（2026-09-12）：底栏不再提供「清空」按钮，「恢复默认」只清空已填尺寸。
  assert.doesNotMatch(drawerSource, />清空<\/button>/);
  assert.match(drawerSource, /解锁编辑（仅用于下一批次）/);
  assert.match(drawerSource, /setCells\(buildSpecCardCells\(skuNames\)\)/);
  assert.match(drawerSource, /保存并全批重印/);
  assert.match(drawerSource, /podCustomizationApi\.reprintSpecCard\(batch\.id, \{[\s\S]*?cells: next\.cells,[\s\S]*?style: next\.style,[\s\S]*?corner: next\.corner,[\s\S]*?\}\);/);
  assert.match(drawerSource, /重印中 \{reprintProgress\.done\}\/\{reprintProgress\.total\}/);
  assert.match(drawerSource, /成功 \{reprintResult\.reprinted\} \/ 失败 \{reprintResult\.failed\}/);
  assert.match(drawerSource, /款式 #\{error\.style_index\}：\{error\.message\}/);
  assert.match(drawerSource, /export const SPEC_CARD_REEXPORT_NOTICE = "该批次需重新导出";/);
  assert.match(drawerSource, /reprintResult\.needs_re_export/);
  assert.match(source, /onReprinted=\{\(batchId, result\) => \{/);
  assert.match(source, /全批重印完成/);
});

test("spec card drawer stacks above the workspace back-to-top button", () => {
  // 抽屉必须 portal 到 body，否则会被工作区外壳里的「返回顶部」悬浮按钮（z-index 80）盖住
  assert.match(drawerSource, /createPortal\(/);
  assert.match(drawerSource, /document\.body/);
  const layer = styles.match(/\.pod-spec-card-drawer-layer \{[^}]*z-index:\s*(\d+)/);
  assert.ok(layer, "缺少 .pod-spec-card-drawer-layer 层级规则");
  assert.ok(Number(layer[1]) > 80, `抽屉 z-index ${layer[1]} 必须高于悬浮按钮的 80`);
});

test("spec card drawer keeps the POD theme tokens inside the portal", () => {
  // 抽屉 portal 到 body，必须让 --pod-* 变量在 portal 根节点上也可见，否则面板会变透明
  const scope = styles.match(/\.pod-customization-page,[^{}]*\{/);
  assert.ok(scope, "缺少 POD 主题变量的作用域选择器");
  assert.match(scope![0], /\.pod-spec-card-drawer-layer/);
  assert.match(scope![0], /\.pod-result-lightbox-layer/);
  assert.match(styles, /\.pod-spec-card-drawer-layer \{[\s\S]*?--pod-surface:/);
  assert.match(styles, /\.pod-spec-card-drawer-layer \{[^}]*--pod-text:/);
  assert.match(styles, /\.pod-spec-card-drawer-layer \{[^}]*color: var\(--pod-text\)/);
});

test("both save and reprint close the drawer and return to the POD page", () => {
  // 「保存到本批次」：保存后关闭抽屉
  assert.match(drawerSource, /onSave\(currentConfig\(\)\);\s*\n\s*onClose\(\);/);
  // 「保存并全批重印」：重印回调之后关闭抽屉
  assert.match(drawerSource, /onReprinted\?\.\(batch\.id, result\);[\s\S]{0,200}?onClose\(\);/);
  // 结果摘要改为回到页面后用 toast 呈现（此前 notice 从未渲染，摘要会丢）
  assert.match(source, /\{!error && notice && <div className="pod-page-message"/);
});
