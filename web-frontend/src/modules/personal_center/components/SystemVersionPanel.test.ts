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

test("更新日志默认收起：点「更新日志」标题才展开内容，进页面不直接摊开", () => {
  assert.match(panel, /const \[changelogOpen, setChangelogOpen\] = useState\(false\)/);
  assert.match(panel, /aria-expanded=\{changelogOpen\}/);
  assert.match(panel, /onClick=\{\(\) => setChangelogOpen\(\(value\) => !value\)\}/);
  // 内容整体只在展开时渲染
  assert.match(panel, /\{changelogOpen && \(/);
  // 标题行可点 + 版本数提示 + 箭头
  assert.match(panel, /className="version-changelog-head"/);
  assert.match(panel, /version-changelog-count/);
  assert.match(styles, /\.version-changelog-head \{/);
  assert.match(styles, /\.version-changelog-chevron/);
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

test("更新日志：面板重新可见时必须补拉一次（页签 hidden 不卸载，切回来要刷新）", () => {
  // 2026-10-23 实测：页签是「全部渲染 + hidden」，切模块不重新挂载本组件，
  // 拉取 effect 只依赖 pollGeneration → 后台改完说明，用户不点检查更新就永远看旧数据。
  assert.match(panel, /const rootRef = useRef<HTMLElement \| null>\(null\)/);
  assert.match(panel, /ref=\{rootRef\}/);
  assert.match(panel, /new IntersectionObserver\(/);
  assert.match(panel, /observer\.observe\(element\)/);
  assert.match(panel, /observer\.disconnect\(\)/);
  // 从不可见变可见 -> 递增 pollGeneration 触发重拉
  assert.match(panel, /nextVisible && !visible/);
  assert.match(panel, /setPollGeneration\(\(generation\) => generation \+ 1\)/);
  // 别把首次挂载算成「重新可见」，否则会重复拉一次
  assert.match(panel, /let visible = true;/);
});
