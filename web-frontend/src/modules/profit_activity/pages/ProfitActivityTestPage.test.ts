import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./ProfitActivityTestPage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/profitActivityTest.css", import.meta.url), "utf8");

test("profit settings open in a dialog from the save-directory button", () => {
  assert.match(source, /const \[settingsDialogOpen, setSettingsDialogOpen\] = useState\(false\)/);
  assert.match(source, />\s*设置保存目录/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /className="profit-settings-dialog-backdrop"/);
  assert.doesNotMatch(source, /profit-settings-collapse/);
  assert.match(source, /const \[settingsSite, setSettingsSite\] = useState<Site>\("US"\)/);
  assert.match(source, /const \[settingsDraft, setSettingsDraft\] = useState<Record<string, string>>\(\{\}\)/);
  assert.match(source, /const builtinSiteSettingProfiles: SiteSettingProfile\[\] = \[/);
  assert.match(source, /siteProfiles\.map\(\(profile\) =>/);
  assert.match(source, /fieldsForSite\(settingsSite\)\.map/);
  assert.match(source, /value=\{settingsDraft\[field\.key\] \?\? ""\}/);
  assert.match(source, /us_first_mile_rate: 0/);
  assert.match(source, /shipping_subsidy: 0/);
  assert.match(styles, /\.profit-settings-dialog-backdrop\s*\{/);
  assert.match(styles, /\.profit-settings-dialog\s*\{/);
});

test("profit settings can create and switch to a persisted custom site", () => {
  assert.match(source, /const \[siteProfiles, setSiteProfiles\] = useState/);
  assert.match(source, /request<\{ sites: Array<Record<string, unknown>> \}>\("\/api\/profit-activity\/sites"\)/);
  assert.match(source, />\+ 新增站点</);
  assert.match(source, /request<\{ site: Record<string, unknown> \}>\("\/api\/profit-activity\/sites", \{/);
  assert.match(source, /method: "POST"/);
  assert.match(source, /setSite\(created\.id\)/);
  assert.match(source, /<span aria-hidden="true">×<\/span>/);
  assert.match(styles, /\.profit-settings-dialog-close span\s*\{/);
  assert.match(styles, /\.profit-test-page \.profit-settings-dialog-close\s*\{/);
});

test("profit page does not expose development-only notices", () => {
  assert.doesNotMatch(source, /管理员默认不加载员工资料库/);
  assert.doesNotMatch(source, /站点代码为 2-12 位大写字母、数字或下划线。/);
});

test("product import opens from the hero actions in a dialog", () => {
  assert.match(source, /const \[importDialogOpen, setImportDialogOpen\] = useState\(false\)/);
  assert.match(source, />\s*产品资料导入/);
  assert.match(source, /className="profit-settings-dialog profit-import-dialog"/);
  assert.match(styles, /\.profit-hero-actions\s*\{/);
});

test("activity upload uses a compact file status instead of a second action block", () => {
  assert.match(source, /className="profit-activity-upload"/);
  assert.match(source, /profit-file-status/);
  assert.match(styles, /\.profit-activity-upload\s*\{/);
});

test("profit hero keeps only its title and actions", () => {
  assert.doesNotMatch(source, /className="profit-hero-steps"/);
  assert.doesNotMatch(source, /title="填单品"/);
});

test("single-product guidance is available from an inline tooltip", () => {
  assert.match(source, /className="profit-help-tooltip"/);
  assert.match(source, /输入商品ID（支持 SKU、SKC、SPU）、售价、成本、重量后会自动预览利润。/);
  assert.match(styles, /\.profit-help-tooltip\s*\{/);
});
