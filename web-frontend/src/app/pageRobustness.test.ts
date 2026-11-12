import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

/**
 * 页面健壮性守护：接口回空对象 / 缺字段时不能把整页打崩。
 *
 * 背景（2026-11-04 全模块冒烟）：用「接口返回 {}」模拟网关抽风，41 个模块里有
 * 5 个直接抛错 —— 模板列表、站点列表、构图名。真实环境里接口 5xx、换版、
 * 本地后端重启间隙都会走到这条路径，用户看到的就是「这个页面没能显示出来」。
 * 这类问题只有肉眼点才能发现，所以在这里钉住写法。
 */

const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

test("POD 定制：模板列表回空对象时不能崩在 templates[0]", () => {
  const page = read("../modules/pod_customization/pages/PodCustomizationPage.tsx");
  assert.match(page, /const list = Array\.isArray\(templateResult\.value\?\.templates\)/);
  assert.match(page, /setTemplates\(list\);/);
  assert.doesNotMatch(page, /setTemplates\(templateResult\.value\.templates\)/);
});

test("爆款复刻：productName 缺失时 trim 不能抛", () => {
  const page = read("../modules/pod_replica/pages/PodReplicaPage.tsx");
  assert.match(page, /targets\[styleIndex - 1\]\?\.productName\?\.trim\(\)/);
  assert.doesNotMatch(page, /targets\[styleIndex - 1\]\?\.productName\.trim\(\)/);
});

test("构图名的 trim 要有空值兜底（定制页 + 复刻页）", () => {
  for (const rel of [
    "../modules/pod_customization/pages/PodCustomizationPage.tsx",
    "../modules/pod_replica/pages/PodReplicaPage.tsx",
  ]) {
    const page = read(rel);
    assert.doesNotMatch(page, /composition\.name\.trim\(\)/, `${rel} 还有裸的 composition.name.trim()`);
    assert.match(page, /\(composition\.name \?\? ""\)\.trim\(\)/);
  }
});

test("货源与利润：站点列表要是数组，渲染前再兜一次空", () => {
  const page = read("../modules/sourcing_ledger/pages/SourcingLedgerPage.tsx");
  assert.match(page, /\.then\(\(rows\) => setSites\(Array\.isArray\(rows\) \? rows : \[\]\)\)/);
  assert.match(page, /\{\(sites \?\? \[\]\)\.map/);
  assert.match(page, /\{\(detail\.sites \?\? \[\]\)\.map/);
});

test("利润活动：loadSites 里 data.sites 缺失时不能 map 崩", () => {
  const page = read("../modules/profit_activity/pages/ProfitActivityTestPage.tsx");
  assert.match(page, /\(Array\.isArray\(data\?\.sites\) \? data\.sites : \[\]\)\.map/);
});

test("草稿库：counts 缺失时统计卡不能崩（可选链要落到 counts 上）", () => {
  const page = read("../modules/temu_publish/pages/TemuDraftLibraryPage.tsx");
  // 统计卡三行都必须写成 `data?.counts?.xxx`：只护住 data 是不够的，
  // 接口回 {} 时 counts 是 undefined，`data?.counts.total` 照样抛。
  assert.doesNotMatch(page, /\["all", "全部", data\?\.counts\.total/);
  assert.doesNotMatch(page, /\["ai", "AI 处理", data\?\.counts\.ai/);
  assert.match(page, /\["all", "全部", data\?\.counts\?\.total \?\? 0/);
});

test("列表接口回空对象时按空数组渲染（asArray 统一兜底）", () => {
  const client = read("../transport/http/client.ts");
  assert.match(client, /export function asArray<T>\(value: unknown\): T\[\]/);

  const daily = read("../modules/daily_selection/api/dailySelectionApi.ts");
  assert.match(daily, /asArray<DailySelectionRunSummary>\(value\)/);

  const pod = read("../modules/pod_customization/api/podCustomizationApi.ts");
  assert.match(pod, /batches: asArray<PodBatchListResponse\["batches"\]\[number\]>\(response\?\.batches\)/);
});

test("成品库：缩略图「+N」角标和「加载更多」不能共用同一个类名", () => {
  // 2026-11-07 实测故障：两处都叫 .temu-draft-more，后定义的「加载更多」
  // 把白底虚线框 + padding 套到了每张缩略图的小角标上，界面上就是一堆白框。
  const css = read("../modules/temu_publish/styles/temuPublish.css");
  const definitions = css.match(/^\.temu-draft-more\s*\{/gm) ?? [];
  assert.equal(definitions.length, 1, ".temu-draft-more 只能有一处定义（缩略图角标）");

  const page = read("../modules/temu_publish/pages/TemuDraftLibraryPage.tsx");
  assert.match(page, /className="temu-draft-load-more"/, "「加载更多」要用自己的类名");
  assert.match(page, /className="temu-draft-more"/, "缩略图角标仍用 temu-draft-more");
});

test("发布页：选填属性常驻展开，不能改成折叠块", () => {
  // 选填项里有「下级属性的开关」，折起来就没人知道漏了哪几项能开
  // （2026-11-07 用户要求：选填不要缩起来）。
  const form = read("../modules/temu_publish/components/TemuPropertyForm.tsx");
  assert.doesNotMatch(form, /<details[^>]*temu-optional-attrs/);
  assert.doesNotMatch(form, /<summary>\s*选填属性/);
  assert.match(form, /<div className="temu-optional-attrs">/);
  assert.match(form, /<div className="temu-optional-attrs-head">/);

  const css = read("../modules/temu_publish/styles/temuPublish.css");
  // 样式不能只写在 .is-console 下：批量发布弹窗是 portal 到 body 的，
  // 不在 .is-console 里，选填区在弹窗里会变成没边框没内边距的一团字
  assert.match(css, /^\.temu-optional-attrs \{/m);
  assert.match(css, /^\.temu-optional-attrs-head \{/m);
  assert.doesNotMatch(css, /\.is-console \.temu-optional-attrs/);
  // 高级设置仍可折叠，别把它的 summary 样式一起删了
  assert.match(css, /\.is-console \.temu-advanced > summary \{/);
});

test("发布页：产品素材图的占位文案要区分「必填/可选」", () => {
  // 老文案「覆盖素材图地址（可选）」看着像必填，普通类目其实留空就行。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  assert.doesNotMatch(page, /placeholder="覆盖素材图地址（可选）"/);
  assert.match(page, /"必填 · 粘贴 Temu 图片地址，或点右边「上传方图」"/);
  assert.match(page, /"可选 · 留空自动用轮播第一张"/);
});

test("发布/批量发布：轮播图少于 3 张在本地就拦（Temu 硬性要求）", () => {
  // 网关实测会回「轮播图最少 3 张」（docs/temu-接口实测清单-20261028.md）。
  // 以前只拦空，1~2 张能一路传到网关才被拒，白等一轮图片上传。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  const batch = read("../modules/temu_publish/data/batchPublish.ts");
  assert.match(page, /if \(carouselSource\.length < 3\) \{/);
  assert.match(page, /轮播图至少 3 张/);
  assert.match(batch, /if \(carouselSource\.length < 3\) \{/);
  assert.match(batch, /草稿轮播图不足 3 张/);
  // 界面上的说明必须是同一个数，不能一处写 3 一处写 1
  assert.match(page, /至少 3 张 · 1:1 方图/);
});

test("发布页：完成态对勾用 SVG，不用字符 ✓", () => {
  // 字符 ✓ 的粗细和垂直居中全看字体，换台机器/换主题字体就歪。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  assert.match(page, /import \{ CheckGlyph \} from "\.\.\/components\/CheckGlyph";/);
  assert.equal(
    (page.match(/<CheckGlyph \/>/g) ?? []).length,
    3,
    "步骤条 / 区块徽标 / 右侧清单点三处都要换成 SVG",
  );
  assert.doesNotMatch(page, /\{step\.done \? "✓"/);

  // 类名不能用 temu-check：那个类被「勾选卡片」占着（min-height:34px + 虚线框），
  // SVG 套上去会被撑成一块白框 —— 2026-11-09 就是这么翻车的。
  const glyph = read("../modules/temu_publish/components/CheckGlyph.tsx");
  assert.match(glyph, /className="temu-tick"/);
  assert.match(
    read("../modules/temu_publish/styles/temuPublish.css"),
    /^\.temu-tick \{/m,
  );
});

test("发布页：报错条带「去改」按钮，区块来自后端 detail.section", () => {
  // 光说「哪里错了」不够：长表单里用户还得自己滚到那一块找。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  assert.match(page, /const \[errorSection, setErrorSection\] = useState\(""\);/);
  assert.match(page, /const section = \(cause as \{ section\?: unknown \} \| null\)\?\.section;/);
  assert.match(page, /去改\{SECTION_LABELS\[errorSection\]\}/);
  assert.match(page, /onClick=\{\(\) => jumpTo\(errorSection\)\}/);
  // 区块 id 必须是页面真实存在的 section，写错了按钮会滚到空白
  for (const id of ["sec-base", "sec-info", "sec-variant", "sec-pack"]) {
    assert.match(page, new RegExp(`"${id}": "`), `SECTION_LABELS 缺 ${id}`);
    assert.match(page, new RegExp(`id="${id}"`), `页面里没有 ${id} 这个区块`);
  }

  // 前端不再自己猜一份「怎么改」：后端已经把结论+建议拼进 message，两套会打架
  const store = read("../modules/temu_publish/data/localStore.ts");
  assert.doesNotMatch(store, /轮播图需 1:1、≥800×800；详情图一个模块只能一张/);
});

test("海外上架：店铺授权的「?」能打开图文说明，两张图都在 public/help", () => {
  // 用户 2026-11-09 要求：在店铺授权状态旁边加问号，点开是
  // 「服务市场 → 授权管理」两张截图 + 拿 token 的步骤说明。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  assert.match(page, /className="temu-help-dot"/, "店铺授权旁要有问号按钮");
  assert.match(page, /onClick=\{\(\) => setTokenHelpOpen\(true\)\}/);
  assert.match(page, /temu-help-dialog/, "点开要有说明弹窗");
  for (const img of ["temu-auth-menu.png", "temu-auth-grant.png", "temu-auth-page.png"]) {
    assert.match(page, new RegExp(img), `弹窗里要引用 ${img}`);
    assert.ok(existsSync(new URL(`../../public/help/${img}`, import.meta.url)), `缺少 public/help/${img}`);
  }

  const css = read("../modules/temu_publish/styles/temuPublish.css");
  assert.match(css, /^\.temu-help-dot \{/m);
  assert.match(css, /\.temu-help-shot\.is-narrow \{[\s\S]{0,60}max-width: 300px/);
});

test("发布页：图片预览必须 portal 到 body（否则被说明弹窗盖住）", () => {
  // 2026-11-09 实测：点「?」弹窗里的截图，预览层没显示在最上面。
  // 根因是 .workspace-main { z-index: 1 } 形成层叠上下文，预览层 z-index:9999
  // 也被困在里面；说明弹窗是 body 下的 9998，反而盖住了预览。
  const page = read("../modules/temu_publish/pages/TemuPublishPage.tsx");
  const start = page.indexOf("{imagePreview");
  const block = page.slice(start, page.indexOf("{tokenHelpOpen"));
  assert.ok(start > 0 && block.length > 0, "找不到图片预览块");
  assert.match(block, /createPortal\(/, "预览层要 portal 出去");
  assert.match(block, /document\.body/);
});
