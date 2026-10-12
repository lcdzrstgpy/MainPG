import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("./SystemVersionPanel.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../../app_update/api/appUpdateApi.ts", import.meta.url), "utf8");
const styles = readFileSync(
  new URL("../styles/personalCenter.css", import.meta.url),
  "utf8",
);

test("更新日志：接口走 /api/app-update/changelog，面板挂区块", () => {
  assert.match(api, /const CHANGELOG_PATH = "\/api\/app-update\/changelog";/);
  assert.match(api, /changelog: \(\) => httpJson<ChangelogSnapshot>\(CHANGELOG_PATH\)/);
  assert.match(panel, /appUpdateApi\s*\n?\s*\.changelog\(\)/);
  assert.match(panel, /<h3>更新日志<\/h3>/);
});

test("更新日志：默认只展示最新 3 个，可展开全部；当前版本带标记", () => {
  assert.match(panel, /const CHANGELOG_PREVIEW_COUNT = 3;/);
  assert.match(panel, /changelog\.releases\.slice\(0, CHANGELOG_PREVIEW_COUNT\)/);
  assert.match(panel, /展开全部（共 \$\{changelog\.releases\.length\} 个版本）/);
  assert.match(panel, /version-changelog-current/);
  assert.match(panel, /item\.version === view\.currentVersion/);
});

test("更新日志：加载中 / 失败降级 / 空列表都有文案，失败不阻塞面板", () => {
  assert.match(panel, /正在加载更新日志…/);
  assert.match(panel, /更新日志暂时无法获取，请稍后重试。/);
  assert.match(
    panel,
    /setChangelog\(\{ releases: \[\], error: "更新日志暂时无法获取。" \}\)/,
  );
  assert.match(panel, /暂无更新日志。/);
});

test("更新日志：日期按北京时间（Asia/Shanghai）格式化，行级拆分保留逐条展示", () => {
  assert.match(panel, /timeZone: "Asia\/Shanghai"/);
  assert.match(panel, /function releaseNoteLines\(notes: string\)/);
  assert.match(panel, /本次更新未填写说明/);
});

test("更新日志：样式使用主题变量且类名齐备", () => {
  assert.match(styles, /\.version-changelog \{/);
  assert.match(styles, /\.version-changelog-version \{/);
  assert.match(styles, /\.version-changelog-current \{/);
  assert.match(styles, /\.version-changelog-notes \{/);
  assert.match(styles, /\.version-changelog-toggle \{/);
  assert.match(styles, /var\(--theme-primary/);
});
