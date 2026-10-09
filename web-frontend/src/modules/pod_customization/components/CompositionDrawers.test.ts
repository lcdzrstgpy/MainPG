import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const generateDrawer = readFileSync(new URL("./CompositionGenerateDrawer.tsx", import.meta.url), "utf8");
const editDrawer = readFileSync(new URL("./CompositionEditDrawer.tsx", import.meta.url), "utf8");
const editor = readFileSync(new URL("./CompositionPanelsEditor.tsx", import.meta.url), "utf8");
const manager = readFileSync(new URL("./CompositionManagerDrawer.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/podCustomizationApi.ts", import.meta.url), "utf8");
const page = readFileSync(new URL("../pages/PodCustomizationPage.tsx", import.meta.url), "utf8");

test("generate drawer owns the AI block and hands the result to the shared panel editor", () => {
  assert.match(generateDrawer, /podCustomizationApi\.generateComposition\(\{ brief: input, locale: "zh-CN" \}\)/);
  assert.match(generateDrawer, /podCustomizationApi\.getLatestComposition\(\)/);
  assert.match(generateDrawer, /import \{ CompositionPanelsEditor \} from "\.\/CompositionPanelsEditor";/);
  assert.match(generateDrawer, /<CompositionPanelsEditor/);
  assert.match(generateDrawer, /const COMPOSITION_INPUT_MAX_LENGTH = 500;/);
  // 保存改原记录的逻辑收敛到共用编辑器里。
  assert.doesNotMatch(generateDrawer, /updateComposition/);
  assert.doesNotMatch(generateDrawer, /clearComposition/);
});

test("edit drawer is manual-editing only: no AI generation at all", () => {
  assert.match(editDrawer, /<CompositionPanelsEditor/);
  // 从「构图模板管理」进来的编辑，不开放 AI 生成模块。
  assert.doesNotMatch(editDrawer, /generateComposition/);
  assert.doesNotMatch(editDrawer, /生成并新增模板/);
  assert.doesNotMatch(editDrawer, /大白话/);
  assert.doesNotMatch(editDrawer, /COMPOSITION_INPUT_MAX_LENGTH/);
  // 内置默认模板不可编辑（管理入口不会传它，双保险）。
  assert.match(editDrawer, /is_builtin/);
});

test("shared panel editor updates the same record with the four role panels", () => {
  assert.match(editor, /podCustomizationApi\.updateComposition\(composition\.composition_id, \{ panels \}\)/);
  assert.match(editor, /export const COMPOSITION_PANEL_MAX_LENGTH = 500;/);
  assert.match(editor, /\{ key: "panel_1", label: "主图" \}/);
  assert.match(editor, /\{ key: "panel_2", label: "细节图 A" \}/);
  assert.match(editor, /\{ key: "panel_3", label: "细节图 B" \}/);
  assert.match(editor, /\{ key: "panel_4", label: "素材图" \}/);
  assert.match(editor, /className="pod-composition-save"/);
  // 面向用户不出现「左上/右上/左下/右下」这类指向，也不出现网格描述。
  assert.doesNotMatch(editor, /label: "[^"]*左[上下]|label: "[^"]*右[上下]/);
  assert.doesNotMatch(editor, /四宫格|四格/);
});

test("the outdated single-template explainer is gone from every drawer", () => {
  for (const source of [generateDrawer, editDrawer, editor, manager]) {
    assert.doesNotMatch(source, /后台会拆成每个角色/);
    assert.doesNotMatch(source, /只保留最新一份/);
    assert.doesNotMatch(source, /用大白话描述四张图想怎么拍/);
  }
});

test("composition manager lists templates with a built-in default plus activate / edit / rename / delete", () => {
  assert.match(manager, /podCustomizationApi\.listCompositions\(\)/);
  assert.match(manager, /podCustomizationApi\.activateComposition\(template\.composition_id\)/);
  assert.match(manager, /podCustomizationApi\.renameComposition\(template\.composition_id, name\)/);
  assert.match(manager, /podCustomizationApi\.deleteComposition\(template\.composition_id\)/);
  // 内置「默认模板」只能设为生效：编辑/重命名/删除都按 is_builtin 隐藏。
  assert.match(manager, /系统内置/);
  assert.match(manager, /\{!template\.is_builtin && \(/);
  // 不再需要用户「新建」默认模板。
  assert.doesNotMatch(manager, /createDefaultComposition|新建默认模板/);
  assert.match(manager, /设为生效/);
  assert.match(manager, /重命名/);
  assert.match(manager, /删除/);
  assert.doesNotMatch(manager, /四宫格|四格/);
});

test("pod api exposes the composition template endpoints", () => {
  assert.match(api, /generateComposition: \(body: \{ brief: string; locale\?: string \}\)/);
  assert.match(api, /listCompositions: \(\) => httpJson<PodCompositionListResponse>/);
  assert.match(api, /updateComposition: \(compositionId: string, body: \{ panels: PodCompositionPanelsZh \}\)/);
  assert.match(api, /renameComposition: \(compositionId: string, name: string\)/);
  assert.match(api, /activateComposition: \(compositionId: string\)/);
  assert.match(api, /deleteComposition: \(compositionId: string\)/);
  assert.match(api, /method: "PUT"/);
  assert.match(api, /method: "PATCH"/);
  assert.match(api, /\/activate`/);
  assert.match(api, /method: "DELETE"/);
});

test("customization page mounts the generate, edit and manager drawers separately", () => {
  assert.match(page, /import \{ CompositionEditDrawer \} from "\.\.\/components\/CompositionEditDrawer";/);
  assert.match(page, /import \{ CompositionGenerateDrawer \} from "\.\.\/components\/CompositionGenerateDrawer";/);
  assert.match(page, /import \{ CompositionManagerDrawer \} from "\.\.\/components\/CompositionManagerDrawer";/);
  assert.doesNotMatch(page, /components\/CompositionDrawer"/);
  // 左侧「创意描述」里的入口只开生成抽屉；管理里的「编辑」开手动编辑抽屉。
  assert.match(page, /className="pod-composition-entry-button" onClick=\{\(\) => setCompositionDrawerOpen\(true\)\}/);
  assert.match(page, /<CompositionGenerateDrawer[\s\S]*?open=\{compositionDrawerOpen\}/);
  assert.match(page, /<CompositionEditDrawer[\s\S]*?open=\{compositionEditOpen\}[\s\S]*?target=\{compositionEditTarget\}/);
  assert.match(page, /onEdit=\{\(template\)[\s\S]{0,120}setCompositionEditOpen\(true\)/);
  // 管理入口放在页头操作区（右上）。
  assert.match(page, /pod-page-header-actions[\s\S]{0,900}setCompositionManagerOpen\(true\)/);
});

test("panel inputs auto-grow and the save button stays concise", () => {
  const autoGrow = readFileSync(new URL("./AutoGrowTextarea.tsx", import.meta.url), "utf8");
  assert.match(autoGrow, /element\.scrollHeight \+ border/);
  assert.match(autoGrow, /addEventListener\("resize"/);
  // 生成与编辑两个入口的多行输入都自适应。
  assert.match(editor, /import \{ AutoGrowTextarea \} from "\.\/AutoGrowTextarea";/);
  assert.match(editor, /<AutoGrowTextarea/);
  assert.match(generateDrawer, /import \{ AutoGrowTextarea \} from "\.\/AutoGrowTextarea";/);
  assert.match(generateDrawer, /<AutoGrowTextarea/);
  // 按钮只写「保存」。
  assert.match(editor, /className="pod-composition-save"[\s\S]{0,200}"保存"/);
  assert.doesNotMatch(editor, /保存修改（重新转写英文）/);
  const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");
  assert.match(styles, /\.pod-composition-panels textarea \{[^}]*overflow: hidden; resize: none;/);
});

test("manager rows constrain their width so a long template name cannot burst the drawer", () => {
  const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");
  // 长名字/长预览必须被约束在卡片内：列表列宽 minmax(0,1fr)、行与 meta 允许收缩。
  assert.match(styles, /\.pod-composition-manager-list \{ display: grid; grid-template-columns: minmax\(0, 1fr\);/);
  assert.match(styles, /\.pod-composition-manager-list > li \{ display: grid; min-width: 0;/);
  assert.match(styles, /\.pod-composition-manager-meta \{ display: flex; min-width: 0;/);
  assert.match(styles, /\.pod-composition-manager-meta b \{[^}]*overflow: hidden; text-overflow: ellipsis; white-space: nowrap;/);
  assert.match(styles, /\.pod-composition-manager-preview \{[^}]*overflow-wrap: anywhere;/);
});

test("portal drawers redeclare the pod theme variables so their background stays opaque", () => {
  const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");
  assert.match(styles, /\.pod-composition-drawer-layer,\s*\n\.pod-composition-manager-layer \{[\s\S]*?--pod-surface-raised:/);
});
