import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");

test("发布商品：提交前必须人工确认（外部副作用，不允许自动触发）", () => {
  // 真提交会在这个店铺里创建商品，必须先 confirm 再发请求
  assert.match(page, /const publishGoods = \(\) =>/, "缺少 publishGoods 处理函数");
  assert.match(page, /window\.confirm\(/, "提交前必须有确认弹窗");
  assert.ok(
    page.indexOf("window.confirm(") < page.indexOf("temuPublishApi.publish(draft)"),
    "确认必须发生在调用提交接口之前",
  );
  // 用户取消时直接返回，不发请求
  assert.match(page, /if \(!confirmed\) return;/);
  // confirm 文案要说明后果：真实创建 + 不重试
  assert.match(page, /真的在店铺里创建商品/);
  assert.match(page, /不支持重试/);
});

test("发布商品：build 与 publish 复用同一份草稿，避免两条路径走偏", () => {
  assert.match(page, /const composeDraft = async \(\): Promise<TemuGoodsDraft> =>/, "缺少 composeDraft");
  // build / publish / 类目必填校验三条入口都必须走同一个 composeDraft
  const composeCalls = page.match(/await composeDraft\(\)/g) ?? [];
  assert.ok(composeCalls.length >= 2, `composeDraft 至少被 build 与 publish 复用，实际 ${composeCalls.length}`);
  assert.match(page, /const checked = await temuPublishApi\.checkMandatory\(draft\);/);
  // 不允许任何一处绕过 composeDraft 直接拼 payload
  assert.match(page, /const built = await temuPublishApi\.buildPayload\(draft\);/);
  assert.match(page, /const published = await temuPublishApi\.publish\(draft\);/);
});

test("发布商品：接口指向 /publish，按钮提交中要禁用", () => {
  assert.match(api, /publish: \(draft: TemuGoodsDraft, force = false\) =>/, "api 缺少 publish 方法");
  assert.match(api, /`\$\{BASE\}\/publish\$\{force/, "publish 必须打到 /api/temu-publish/publish");
  // 提交中/凭证缺失/正在发布时都要禁用，避免连点重复上架
  assert.match(
    page,
    /const submitDisabled = busy !== "" \|\| !ready \|\| publishing;/,
    "缺少提交禁用条件",
  );
  // 发布进行中别的任务跑完不能把它解禁：要么看 publishing，要么有 ref 兜底
  assert.match(page, /if \(publishingRef\.current\) return;/, "发布要能挡住重复点击");
  assert.match(page, /setBusy\(\(current\) => \(current === label \? "" : current\)\);/);
  assert.ok(page.includes("disabled={submitDisabled}"), "提交中必须禁用按钮");
  assert.match(page, /提交发布/);
});

test("草稿库「用于发布」：页签已打开时也要生效（事件通道，不能只靠挂载读 storage）", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  const shell = readFileSync(new URL("../../../app/layout/WorkspaceShell.tsx", import.meta.url), "utf8");

  // 事件名要集中定义，两端共用
  assert.match(types, /export const TEMU_PUBLISH_PREFILL_EVENT = "mainpg:temu-publish-prefill";/);
  // openModule 会复用已存在的页签（只切不重建），所以必须在 openModule 之前广播事件
  assert.match(shell, /window\.dispatchEvent\(new CustomEvent\(TEMU_PUBLISH_PREFILL_EVENT, \{ detail: prefill \}\)\);/);
  const dispatchAt = shell.indexOf("new CustomEvent(TEMU_PUBLISH_PREFILL_EVENT");
  // 注意：WorkspaceShell 里「创建产品」也会调 openModule("temu_publish")，
  // 所以要从草稿库 onUseDraft 那段往后找，不能用 indexOf 取第一个。
  const useDraftAt = shell.indexOf("onUseDraft={(prefill) =>");
  const openAt = shell.indexOf('openModule("temu_publish")', useDraftAt);
  assert.ok(dispatchAt > 0 && openAt > dispatchAt, "事件必须在 openModule 之前广播，否则首次挂载会漏");
  // 首次挂载仍走 sessionStorage 兜底
  assert.match(shell, /window\.sessionStorage\.setItem\(TEMU_PUBLISH_PREFILL_KEY/);
  // 页面要订阅并真正铺进表单
  assert.match(page, /window\.addEventListener\(TEMU_PUBLISH_PREFILL_EVENT, onPrefill\)/);
  assert.match(page, /const applyPrefillNow = \(prefill: TemuPublishPrefill\) =>/);
  assert.match(page, /title: prefill\.title \|\| current\.title,/);
  assert.match(page, /carousel: \(prefill\.images \|\| \[\]\)\.join\("\\n"\) \|\| current\.carousel,/);
  // 预填的读取+清空必须在 setState updater 之外：updater 在 StrictMode 下跑两次，
  // 写进 updater 里第二次就拿到 null，草稿的尺码/价格全落不到 SKU 行（2026-10-26 实测踩到）。
  assert.match(
    page,
    /const prefill = prefillRef\.current;\n\s*prefillRef\.current = null;/,
    "prefillRef 不能在 setRows 的 updater 里读写",
  );
  // 变种行也要在 updater 外面按草稿算好（换草稿整表重建，不留上一条的行）
  assert.match(page, /const seedRow = prefill\n\s*\? prefillToRow\(createVariantRow/, "带入草稿要整表重建变种行");
});

test("提交发布：币种 CNY、图片裁 1:1、仓库避开 type=3", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  // 半托管供货价按人民币结算，传 USD 会被 Temu 拒
  assert.match(page, /currency_type: "CNY"/);
  assert.doesNotMatch(page, /currency_type: "USD"/);
  // 非服饰类目轮播图强制 1:1 ≥800px，上传前必须裁（详情图例外，见详情图那条测试）
  assert.match(page, /function toSquareDataUrl\(dataUrl: string, size = 800\)/);
  assert.match(page, /toSquareDataUrl\(raw\)/);
  // 仓库：managementType=3 的仓发部分商品会被拒，默认要避开并在下拉里标注
  assert.match(types, /managementType\?: string;/);
  assert.match(page, /managementType !== "3"/);
  assert.match(page, /function warehouseLabel\(warehouse: TemuWarehouse\): string/);
});

test("发布页布局：分区顺序对齐ERP + 右侧检查清单 + 发布预设", () => {
  // 分区名称与顺序完全按ERP Temu 刊登页
  for (const label of ["基本信息", "产品信息", "变种信息", "包装信息", "校验发布"]) {
    assert.ok(page.includes(`label: "${label}"`), `缺少分区 ${label}`);
  }
  assert.match(page, /const jumpTo = \(id: string\) =>/);
  assert.match(page, /scrollIntoView/);
  for (const id of ["sec-base", "sec-info", "sec-variant", "sec-pack", "sec-publish"]) {
    assert.ok(page.includes(`id="${id}"`), `缺少锚点 ${id}`);
  }
  // 清单渲染在右侧栏，且每项可点击跳转
  assert.match(page, /className="temu-console-rail"/);
  assert.match(page, /onClick=\{\(\) => jumpTo\(step\.id\)\}/);

  // 发布预设：类目 + 运费模板 + 仓库，存本机
  assert.match(page, /const PRESETS_KEY = "temu_publish_presets_v1";/);
  assert.match(page, /const applyPreset = \(preset: PublishPreset\) =>/);
  assert.match(page, /const savePreset = \(\) =>/);

  // 一级类目墙默认收起（原来 20 多个分类卡片占半屏）
  assert.match(page, /const \[showCategoryBrowser, setShowCategoryBrowser\] = useState\(false\);/);
  assert.match(page, /temu-browser-toggle/);

  // 最近使用分类：进页面就恢复（ERP「会记住上次分类」）
  assert.match(page, /RECENT_CATEGORY_KEY/);
  // 最近分类按账号分开存（换账号登录不能带入上一个人的分类）
  assert.match(page, /loadJsonLocal<TemuCategory\[\]>\(`\$\{RECENT_CATEGORY_KEY\}:\$\{storageScope\(\)\}`, \[\]\)/);
});

test("发布页：轮播/外包装/详情都能看到缩略图（草稿库带来的本地图也要能显示）", () => {
  const strip = readFileSync(
    new URL("../components/TemuImageStrip.tsx", import.meta.url),
    "utf8",
  );

  // 本地媒体（/api/product-processing/media/...）必须带鉴权取 blob，不能当公网 URL 直连
  assert.match(strip, /import \{ httpBlob \} from "\.\.\/\.\.\/\.\.\/transport\/http\/client";/);
  assert.match(strip, /if \(isPublicHttp\(path\)\) \{/);
  assert.match(strip, /void httpBlob\(path\)/);
  assert.match(strip, /URL\.createObjectURL\(blob\)/);
  // 第一张是 Temu 轮播主图（同时当素材图），要标出来且不能前移。
  // 角标由调用方给：详情图/外包装图的第一张不是「主图」，不能一起标。
  assert.match(strip, /index === 0 && firstBadge \? <em className="temu-img-main">\{firstBadge\}<\/em> : null/);
  assert.match(strip, /firstBadge\?: string;/);
  assert.match(page, /firstBadge=\{slot\.key === "carousel" \? "主图" : undefined\}/);
  assert.match(strip, /disabled=\{index === 0\}/);
  // 支持调序 / 删除
  assert.match(strip, /const swap = \(from: number, to: number\) =>/);
  assert.match(strip, /onChange\(list\.filter\(\(_, i\) => i !== index\)\)/);
  // 点缩略图能放大
  assert.match(strip, /className="temu-image-preview"/);

  // 三组图片（轮播/外包装/详情）都由 IMAGE_SLOTS 驱动，循环渲染同一个预览条
  assert.match(
    page,
    /const IMAGE_SLOTS: Array<\{ key: ImageSlot; label: string; required: boolean; hint: string \}> = \[/,
  );
  for (const slot of ['key: "carousel"', 'key: "outer"', 'key: "detail"']) {
    assert.ok(page.includes(slot), `缺少图片组 ${slot}`);
  }
  assert.match(page, /\{IMAGE_SLOTS\.map\(\(slot\) => \(/);
  const uses = page.match(/<TemuImageStrip/g) ?? [];
  assert.equal(uses.length, 1, `三组图片共用一处预览条，实际 ${uses.length}`);
});

test("SKU 面板：公共设置跟随第一行，生成不会用默认值盖掉草稿带入的价格", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  // 公共值默认镜像第一行；用户手改后就停止同步
  assert.match(panel, /const \[batchTouched, setBatchTouched\] = useState\(false\);/);
  assert.match(panel, /if \(batchTouched\) return;/);
  // 生成时「公共值 or 第一行已有值」兜底，否则 10 / 0.2 / 20×15×3 会覆盖带入的库存与尺寸
  assert.match(panel, /batch\.supplierPrice\.trim\(\) \|\| base\?\.supplierPrice \|\| ""/);
  assert.match(panel, /batch\.weightG\.trim\(\) \|\| base\?\.weightG \|\| ""/);
  // 尺码列表提升到页面持有（草稿带入 specValue2 后可一键生成），面板不能自己再存一份
  assert.doesNotMatch(panel, /const \[sizesText, setSizesText\] = useState/);
  assert.match(page, /sizesText=\{form\.sizesText\}/);
  assert.match(page, /sizesText: prefill\.specValue2 \|\| current\.sizesText,/);
});

test("我的店铺：凭证按登录账号隔离，可解绑且未绑定时给出入口", () => {
  // 接口层必须有解绑（DELETE /settings），只删当前账号那份
  assert.match(api, /clearSettings: \(\) => httpJson<TemuSettingsStatus>\(`\$\{BASE\}\/settings`, \{ method: "DELETE" \}\)/);
  // 页面：解绑前要确认，成功后清掉店铺信息
  assert.match(page, /const clearShop = \(\) =>/);
  // 停用当前店铺：条目留在列表里，切回来不用重新贴 token
  assert.match(page, /window\.confirm\(\s*"停用当前店铺/);
  assert.match(page, /const next = await temuPublishApi\.clearSettings\(\);/);
  // 未绑定时顶部横幅 + 顶部 chip 都是「绑定我的店铺」，不能只是报“凭证未配置”
  assert.match(page, /还没绑定你的 Temu 店铺/);
  assert.match(page, /去绑定我的店铺/);
  assert.match(page, /"绑定我的店铺"/);
  // 服务商凭据只显示「已配置/未配置」，不把 client/token 掩码摆出来
  assert.match(page, /const gatewayReady = !!\(status\?\.configured\.yl_client/);
  assert.ok(!/status\?\.hints\.yl_client/.test(page), "不要再把 client/token 掩码展示出来");
  assert.match(page, /服务商凭据<\/span>/);
  // 服务商 client_id / token 是预置的：界面上不给输入框，只给状态说明
  assert.ok(!page.includes("setYlClient"), "不要再用输入框收 client_id");
  assert.ok(!page.includes("setYlToken"), "不要再用输入框收服务商 token");
  assert.match(page, /服务商凭据（client_id \/ token）/);
  assert.match(page, /已预置，不用管/);
  // 打开面板时重读一次状态，避免显示成旧值
  assert.match(page, /void temuPublishApi\s*\.settings\(\)/);
  assert.match(page, /if \(!fresh\.configured\.ready\) return undefined;/);
  // 面板要说明账号归属，并展示当前店铺/token 有效期
  assert.match(page, /凭证按登录账号各存一份/);
  assert.match(page, /每个账号存自己的店铺，互不影响/);
  assert.match(page, /status\?\.owner\?\.personal/);
});

test("本机留档：表单/预设/最近分类按登录账号分开存，换账号不串数据", () => {
  // 同一台电脑换账号登录时，不能把上一个人的草稿、预设、分类带出来
  assert.match(page, /function storageScope\(\): string \{/);
  assert.match(page, /return account\?\.user_id \|\| "local";/);
  assert.match(page, /const formKey = `\$\{FORM_KEY\}:\$\{storageSuffix\}`;/);
  assert.match(page, /const presetsKey = `\$\{PRESETS_KEY\}:\$\{storageSuffix\}`;/);
  assert.match(page, /setTimeout\(\(\) => saveJsonLocal\(formKey, form\), 300\)/);
  assert.match(page, /saveJsonLocal\(presetsKey, next\);/);
  // 表单里只保留会提交的字段；唯一留档的备注也是收起的、写清楚不提交
  assert.match(page, /备注（不提交，只留档）/);
  assert.doesNotMatch(page, /function LocalTag/);
});

test("变种：多颜色 = 多个 SKC，按颜色分组提交", () => {
  const panel = readFileSync(
    new URL("../components/PublishSkuPanel.tsx", import.meta.url),
    "utf8",
  );
  // 面板：颜色 × 尺码 展开成变种行，行里带颜色
  assert.match(panel, /  colorName: string;/);
  assert.match(panel, /for \(const color of colors\) \{/);
  assert.match(panel, /for \(const size of sizes\) \{/);
  assert.match(panel, /value=\{row\.colorName\}/);

  // 页面：按颜色分组，一个颜色一个 SKC（SKC 图/主规格都按颜色走）
  assert.match(page, /const grouped = new Map<string, VariantRow\[\]>\(\);/);
  assert.match(page, /for \(const \[color, colorRows\] of grouped\) \{/);
  assert.match(page, /const colorSpec = await ensureSpec\(colorParent, color\);/);
  assert.match(page, /skcs\.push\(\{/);
  assert.match(page, /color_image_url: skcImage,/);
  // 同色内尺码去重（跨颜色允许同名尺码）
  assert.match(page, /const seenSizes = new Set<string>\(\);/);
  // 提交体直接用分组结果，不再写死单个 skc
  assert.match(page, /^      skcs,$/m);
});

test("草稿库：批量发布要套模板、逐条提交、失败进记录", () => {
  const bar = readFileSync(new URL("../components/TemuDraftBulkBar.tsx", import.meta.url), "utf8");
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  // 三个按钮不再写死 enabled:false，改成由父组件按“有没有模板/有没有勾选”决定
  assert.doesNotMatch(bar, /label: "批量发布", action: "publish", enabled: false/);
  assert.doesNotMatch(bar, /引用产品模板", action: "template", enabled: false/);
  assert.match(bar, /onBatchPublish: \(\) => void;/);
  assert.match(bar, /onPickTemplate: \(kind: "product" \| "variant"\) => void;/);

  // 批量发布：弹窗里确认（非幂等），逐条 try/catch，失败只跳过不中断
  assert.match(library, /setBatchOpen\(false\);\n\n    const variant/);
  assert.match(library, /接口非幂等：重复提交会在店铺里多出商品/);
  assert.match(library, /for \(const \[index, entry\] of targets\.entries\(\)\)/);
  assert.match(library, /await temuPublishApi\.publish\(draft\);/);
  assert.match(library, /localStore\.addFailure\(\{/);

  // 组装：一个颜色一个 SKC，价格/尺寸取模板批量值优先
  assert.match(batch, /export async function buildDraftForPublish\(/);
  assert.match(batch, /for \(const color of colors\) \{/);
  assert.match(batch, /for \(const size of sizes\) \{/);
  assert.match(batch, /currency_type: "CNY"/);
  assert.match(batch, /personalization: productTemplate\.isCustom/);
});

test("价格全链路用「元」，只在提交前换成分", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  // 草稿库带入发布页：原样带「元」，不再 ×100
  // 草稿价先圆整到「分」再带入：12.345 会在提交时被「最多两位小数」拦住
  assert.match(library, /function moneyText\(value: number \| string \| null \| undefined\): string \{/);
  assert.match(library, /return String\(Math\.round\(amount \* 100\) \/ 100\);/);
  assert.match(library, /supplierPrice: moneyText\(entry\.declared_price\)/);
  assert.match(library, /suggestedPrice: moneyText\(entry\.suggested_price\)/);
  assert.doesNotMatch(library, /yuanToCents/, "草稿库不该再做元→分换算");

  // 发布页：元 → 分只在组装 payload 时发生，换算函数只有一份（utils/price.ts）
  const price = readFileSync(new URL("../utils/price.ts", import.meta.url), "utf8");
  assert.match(price, /export function yuanToCents\(value: string, label: string\): number \{/);
  assert.match(price, /Math\.round\(Number\(text\) \* 100\)/);
  assert.match(price, /最多两位小数（例如 12\.99）/);
  assert.match(page, /supplier_price: yuanToCents\(row\.supplierPrice, `\$\{at\}申报价格`\)/);
  assert.match(page, /import \{ centsToYuanText, formatYuan, yuanToCents \} from "\.\.\/utils\/price";/);

  // 批量发布复用同一份，不许各写一套
  assert.match(batch, /import \{ yuanToCents \} from "\.\.\/utils\/price";/);
  assert.match(batch, /supplier_price: priceCents,/);
  assert.doesNotMatch(batch, /function yuanToCents\(/, "批量发布不该再自己实现一份换算");

  // 输入框本身是元：label 与 placeholder 都不能再让人按分填
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /申报价格（元）/);
  assert.match(panel, /申报价格\(元\)/);
  assert.doesNotMatch(panel, /申报价格（分）|申报价格\(分\)/);
  assert.doesNotMatch(panel, /placeholder="1299"/);
});

test("本机老数据（价格存的是分）升级后自动换成元，且只换一次", () => {
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  // 一次性迁移：不迁的话老数据 1299 会显示成 ¥1299（100 倍价差）
  assert.match(page, /function migrateLegacyPriceUnit\(suffix: string\): void \{/);
  assert.match(page, /const PRICE_UNIT_FLAG = "temu_publish_price_unit_yuan_v1";/);
  // 标记按账号分：数据是 <key>:<account>，全局标记会让第二个账号漏迁
  assert.match(page, /const flagKey = `\$\{PRICE_UNIT_FLAG\}:\$\{suffix\}`;/);
  assert.match(page, /if \(store\.getItem\(flagKey\) === "yuan"\) return;/);
  assert.match(page, /store\.setItem\(flagKey, "yuan"\);/);
  // 变种行与变种模板都要迁（批量发布套模板会带出旧价格）
  assert.match(page, /const rowsKey = `\$\{ROWS_KEY\}:\$\{suffix\}`;/);
  assert.match(page, /const templateKey = `\$\{VARIANT_TEMPLATE_KEY\}:\$\{suffix\}`;/);
  // 必须在读 state 之前跑，否则读到的还是分
  assert.ok(
    page.indexOf("migrateLegacyPriceUnit(storageScope());") <
      page.indexOf("const saved = loadJsonLocal<VariantRow[]>"),
    "迁移要排在读取本机变种行之前",
  );
});

test("草稿库站点列显示类目路径：选过的优先、其次草稿自带、最后提示", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 取值顺序：本机记忆 → 草稿自带 category_name → 提示去发布页选
  assert.match(library, /function siteCategoryLabel\(/);
  assert.match(
    library,
    /return memo\[entry\.id\]\?\.label \|\| entry\.category_name \|\| "发布页选类目";/,
  );

  // 带入发布页时把 id 和已选类目一起带过去
  assert.match(library, /draftId: entry\.id,/);
  assert.match(library, /categoryPath: memo\?\.\[entry\.id\]\?\.path,/);

  // 发布页选完类目回写给草稿；下次带入直接套用
  assert.match(page, /prefillDraftId\.current = prefill\.draftId \|\| "";/);
  assert.match(page, /localStore\.rememberDraftCategory\(prefillDraftId\.current, path\);/);
  assert.match(page, /void applyCategory\(prefill\.categoryPath\);/);

  // 批量发布用的是产品模板类目，也要记到草稿上
  assert.match(library, /localStore\.rememberDraftCategory\(entry\.id, template\.categoryPath\);/);
});

test("草稿库：编辑在上/用于发布在下，用于发布要先确认基础信息", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 两个按钮的位置互换：编辑在前，用于发布在后
  const actions = library.slice(library.indexOf('className="col-actions"'));
  assert.ok(
    actions.indexOf("编辑") < actions.indexOf("用于发布"),
    "操作列里「编辑」应排在「用于发布」前面",
  );

  // 「编辑」直接带这条草稿进发布页（草稿库小弹窗改不了类目/属性/图片）
  assert.match(library, /title="带这条草稿进发布页，改完所有信息后再提交"/);
  assert.match(library, /onClick=\{\(\) => onUseDraft\(entryPrefill\(entry, categoryMemo\)\)\}/);

  // 点「用于发布」不再直接带入：先弹确认
  assert.match(library, /onClick=\{\(\) => setConfirmEntry\(entry\)\}/);
  assert.match(library, /const \[confirmEntry, setConfirmEntry\] = useState<TemuDraftEntry \| null>\(null\);/);

  // 确认弹窗要列出基础信息，并有「确定发布」才真正带入
  assert.match(library, /确认发布信息/);
  assert.match(library, /申报价格/);
  assert.match(library, /库存/);
  assert.match(library, /const confirmUseDraft = \(\) => \{/);
  assert.match(library, /onUseDraft\(entryPrefill\(confirmEntry, categoryMemo\)\);/);
  assert.match(library, /确定发布/);
  // 库存不可用时要明确提醒（半托管 ≥1）
  assert.match(library, /库存没有可用值：Temu 半托管要求 ≥1/);
});

test("详情图：手动上传不裁方、提交时统一宽度（Temu 2000306）", () => {
  // 商详楼层是长图：手传详情图不能像主图那样裁成 1:1，否则内容被切掉
  assert.match(page, /slot === "detail"/);
  assert.match(page, /temuPublishApi\.uploadDetailImage\(raw, 0, leaf\?\.catId\)/);
  // 1688 详情图常见 1392 与 800 混排，直接提交会被判「floor image width is incorrect」
  assert.match(page, /function mostCommonWidth\(widths: number\[\]\): number \{/);
  assert.match(page, /const target = mostCommonWidth\(widths\);/);
  assert.match(page, /temuPublishApi\.uploadDetailImage\(image, target, cateId\)/);
  assert.match(api, /uploadDetailImage: \(image: string, targetWidth = 800, cateId\?: number\)/);
  // 提交时带真实宽高，不再写死 800×800
  assert.match(page, /detail_images: detailImages,/);
});

test("规格值必须来自类目预设，多颜色合并成一个 SKC", () => {
  // 自己 createSpec 造颜色会被 Temu 判「不合法的规格属性 Color_Black」
  assert.doesNotMatch(page, /temuPublishApi\.createSpec\(/);
  assert.match(page, /const saleValueIndex = useMemo\(/);
  assert.match(page, /if \(!prop\.isSale\) continue;/);
  assert.match(page, /Temu 只认类目预设值/);
  // 变种表的颜色/尺码给预设值下拉
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /list="temu-color-options"/);
  assert.match(panel, /list="temu-size-options"/);
  assert.match(page, /colorOptions=\{specOptions\(colorParentId\)\}/);
});

test("标题自动匹配类目：防抖匹配、没手动选过才自动套用", () => {
  assert.match(page, /const \[titleMatches, setTitleMatches\]/);
  assert.match(page, /const categoryTouchedRef = useRef\(false\);/);
  // 标题停下 1.2 秒后调 Temu 类目匹配
  assert.match(page, /\}, 1200\);/);
  // 查询词不是整条标题（会把结果带偏），走 categorySearchTexts 取商品主体
  assert.match(page, /const queries = categorySearchTexts\(title\);/);
  assert.match(page, /temuPublishApi\.matchCategory\(text, SITE_ID\)/);
  assert.match(page, /dedupeCategoryPaths\(/);
  // 手动选过就不再覆盖
  assert.match(page, /if \(categoryTouchedRef\.current\) return;/);
  assert.match(page, /applyCategoryRef\.current\(firstLeaf, \{ auto: true \}\)/);
  // 界面上给出推荐并可切换
  assert.match(page, /按标题推荐（点一下切换）/);
  assert.match(page, /className="temu-title-match"/);
});

test("换草稿：上一条商品的类目和推荐列表不能留在新商品上", () => {
  // 从草稿带入但没有存过类目时，清空并重新按标题匹配
  assert.match(page, /} else \{\n {4,}\/\/ 上一条商品的类目\/属性也必须清掉/);
  assert.match(page, /categoryTouchedRef\.current = false;\n {4,}setCategoryPath\(\[\]\);/);
  assert.match(page, /setTitleMatches\(\[\]\);/);
  // 进页面时不能直接顶「最近用过的类目」：带草稿进来要按草稿走
  assert.match(page, /const \[categoryPath, setCategoryPath\] = useState<TemuCategory\[\]>\(initialCategoryPath\);/);
  assert.match(page, /function initialCategoryPath\(\): TemuCategory\[\]/);
  assert.match(page, /return prefill\?\.categoryPath\?\.length \? prefill\.categoryPath : \[\];/);
});

test("推荐列表按当前商品重算：记住过类目的草稿也要换掉上一条的推荐", () => {
  // 清推荐 + 重新触发匹配排在「有没有存过类目」判断之前，两条分支都会走
  const clearAt = page.indexOf("setTitleMatches([]);\n    setMatchSeq");
  assert.ok(clearAt > 0, "换草稿时先清推荐列表并触发重算");
  const branchAt = page.indexOf("if (prefill.categoryPath?.length) {");
  assert.ok(branchAt > clearAt, "清推荐要早于类目分支，否则记住过类目的草稿会留着上一条的推荐");
  // 存过类目时只给推荐，不自动覆盖
  assert.match(page, /setTitleMatches\(merged\);\n {10}if \(categoryTouchedRef\.current\) return;/);
  // 也不能在匹配前就 return，否则刚说的「重算」永远不执行
  assert.ok(!/if \(categoryTouchedRef\.current\) return;\n {4}const timer/.test(page));
});


test("变种行也落盘：刷新后价格/库存/尺寸不丢", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(page, /const ROWS_KEY = "temu_publish_rows_v1";/);
  // 恢复出来的行 key 不能和之后新建的行撞（撞了 React 会复用错行）
  assert.match(panel, /export function withFreshRowKeys\(rows: VariantRow\[\]\): VariantRow\[\]/);
  assert.match(page, /withFreshRowKeys\(saved\.filter\(\(row\) => row && row\.key\)\)/);
  // 防抖 300ms 再写，避免逐字符输入价格时每个键都写一次 localStorage
  assert.match(page, /setTimeout\(\(\) => saveJsonLocal\(rowsKey, rows\), 300\)/);
});

test("发布：同一条草稿 24 小时内重复提交要先二次确认", () => {
  assert.match(api, /publish: \(draft: TemuGoodsDraft, force = false\) =>/);
  assert.match(api, /\$\{BASE\}\/publish\$\{force \? "\?force=true" : ""\}/);
  // 后端 409 会带「重复提交：…」前缀，前端识别后弹确认，确认才带 force 再发
  assert.match(page, /if \(\/重复提交\/\.test\(text\)\)/);
  assert.match(page, /await temuPublishApi\.publish\(draft, true\)/);
  assert.ok(page.includes("店铺里会多出一件相同商品"));
});


test("批量发布：勾完直接发，模板在弹窗里选（不用先去菜单引用模板）", () => {
  const library = readFileSync(
    new URL("./TemuDraftLibraryPage.tsx", import.meta.url),
    "utf8",
  );
  const bar = readFileSync(new URL("../components/TemuDraftBulkBar.tsx", import.meta.url), "utf8");

  // 菜单项不再因为「没选模板」被禁用，只是勾选/运行中会禁用
  assert.ok(
    !/item\.action === "publish" && \(batchRunning \|\| !selectedCount \|\| !canBatchPublish\)/.test(bar),
  );
  assert.match(bar, /onBatchPublish/);

  // 点「批量发布」开弹窗，弹窗里选产品模板/变种模板再开始
  assert.match(library, /onBatchPublish={openBatchDialog}/);
  assert.match(library, /const \[batchOpen, setBatchOpen\] = useState\(false\);/);
  assert.match(library, /aria-label="批量发布"/);
  assert.match(library, /<span>\s*产品模板<em className="temu-required">\*<\/em>/);
  assert.match(library, /不套用变种模板/);
  assert.match(library, /开始发布（\$\{batchTargets\.length\} 条）/);
  // 勾选的条数按「当前可见 + 已勾选」算，和表格看到的行一致
  assert.match(library, /const batchTargets = useMemo\(/);
  // 弹窗确认后不再二次 window.confirm
  assert.ok(!/const confirmed = window\.confirm\(\s*\[\s*`即将把这/.test(library));

  // 台账挡下的重复提交：不算失败，单独计数并回显，避免用户以为「发失败了」去重试
  assert.match(library, /if \(\/重复提交\/\.test\(reasonText\)\) \{/);
  assert.match(library, /跳过 \$\{skipped\} 条（24 小时内提交过）/);
  assert.match(library, /setBatchSkipped\(\(current\) => \[\.\.\.current,/);
});

test("库存可以是 0：不拦不标红，但草稿带入要保留 0", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 价格/重量还是 >0；库存单独一个允许 0 的解析器
  assert.match(page, /function parseStock\(value: string, label: string\): number \{/);
  assert.match(page, /if \(!text \|\| !Number\.isFinite\(number\) \|\| number < 0\)/);
  assert.match(page, /stock: parseStock\(row\.stock, `\$\{at\}库存`\)/);
  // 变种表：只有空/负数标红，0 正常
  assert.match(panel, /String\(row\.stock\)\.trim\(\) === "" \|\| Number\(row\.stock\) < 0/);
  assert.match(panel, /placeholder="0 也可"/);
  // 草稿带入：0 不再被当空丢掉
  assert.match(library, /stock: numericText\(entry\.stock\),/);
});

test("属性联动：父级取值不满足的子属性不显示、也不提交", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  assert.match(types, /templatePropertyValueParentList\?:/);
  assert.match(types, /parentTemplatePid\?: number \| null;/);

  // 用 attrs.get 返回的联动规则算「当前适用属性」：父级没选/取值不匹配 → 整项隐藏
  assert.match(page, /const visibleProperties = useMemo<TemuProperty\[\]>\(\(\) => \{/);
  assert.match(page, /const rules = prop\.templatePropertyValueParentList \?\? \[\];/);
  assert.match(page, /const matched = rules\.filter\(\(rule\) => \(rule\.parentVidList \?\? \[\]\)\.includes\(parentVid\)\);/);
  assert.match(page, /values: \(prop\.values \?\? \[\]\)\.filter\(\(value\) => !allowed\.size \|\| allowed\.has\(value\.vid\)\)/);

  // 必填/渲染都用可见属性；提交时过滤掉隐藏项（留着旧值会被判属性不合法）
  assert.match(page, /const requiredProps = visibleProperties\.filter\(\(item\) => item\.required\);/);
  assert.match(page, /const visiblePids = new Set\(visibleProperties\.map\(\(item\) => item\.pid\)\);/);
  assert.match(page, /filter\(\(\[pid\]\) => visiblePids\.has\(Number\(pid\)\)\)/);
});

test("发布超时后反查店铺：确认建成了就显示「已在店铺里查到」", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  assert.match(types, /confirmed_by_lookup\?: boolean;/);
  assert.match(types, /matched_goods\?: Record<string, unknown>;/);
  assert.match(page, /publishResult\.confirmed_by_lookup\s*\n?\s*\? "已在店铺里查到（提交时网络中断）"/);
  assert.match(page, /publishResult\.matched_goods\?\.product_id/);
});

test("发布后核对：按 SKC 货号查店铺里的商品", () => {
  assert.match(api, /searchGoods: \(params: \{ extCode\?: string; title\?: string; page\?: number; pageSize\?: number \}\) =>/);
  assert.match(api, /\$\{BASE\}\/goods\$\{query\(\{/);
  assert.match(api, /goodsDetail: \(productId: string \| number\) =>/);

  assert.match(page, /const checkShopGoods = async \(extCode: string\) => \{/);
  assert.match(page, /temuPublishApi\.searchGoods\(\{ extCode: extCode \|\| undefined, pageSize: 5 \}\)/);
  assert.match(page, /核对店铺里的商品/);
  assert.match(page, /productSkcReqs\?\.\[0\]\?\.extCode/);
  assert.match(page, /className="temu-goods-list"/);
});

test("健壮性：类目匹配重试、传图并发、初始化各自降级、落盘防抖、失败记录带类目名", () => {
  // 1) 类目匹配：3 次重试 + 退避，自动匹配和手动搜索都走它
  assert.match(page, /async function matchCategoryWithRetry\(\s*text: string,\s*attempts = 3,\s*\)/);
  assert.match(page, /await new Promise\(\(resolve\) => setTimeout\(resolve, 800 \* \(attempt \+ 1\)\)\)/);
  assert.match(page, /queries\.map\(\(text\) => matchCategoryWithRetry\(text\)\)/);
  assert.match(page, /const response = await matchCategoryWithRetry\(categoryQuery\.trim\(\)\);/);
  assert.match(page, /类目匹配服务暂时不可用（已重试 3 次）/);

  // 2) 传图并发 3 且结果保序（进度不变，只是不再排队）
  assert.match(page, /async function mapWithConcurrency<T, R>\(/);
  assert.match(page, /await mapWithConcurrency\(files, 3, async \(file\) => \{/);

  // 3) 初始化：五个接口各自降级，只有 settings 决定店铺状态
  assert.match(page, /const settle = <T,>\(promise: Promise<T>\) =>/);
  assert.match(page, /这些没取到（不影响已填内容，可点「刷新」重试）/);
  assert.doesNotMatch(page, /const \[settings, cats, specs, freights, whGroups\] = await Promise\.all/);

  // 4) 表单落盘防抖 300ms，且卸载时补写最后一次（防抖窗口内切页签不丢输入）
  assert.match(page, /setTimeout\(\(\) => saveJsonLocal\(formKey, form\), 300\)/);
  assert.match(page, /const latestSaveRef = useRef\(\{ formKey, rowsKey, form, rows \}\);/);

  // 5) 单条发布失败记类目名路径，不再是裸 catId
  assert.match(page, /categoryPath\.map\(\(step\) => step\.catName\)\.filter\(Boolean\)\.join\(" > "\)/);
});

test("多店铺：一家一家存下来，切换不用重新贴 token", () => {
  const apiSrc = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");

  // 类型：settings 里带店铺列表与当前启用 id
  assert.match(types, /shops\?: TemuShop\[\];/);
  assert.match(types, /active_shop_id\?: string;/);
  assert.match(types, /export type TemuShop = \{/);

  // 接口：新增（后端先校验）/ 切换 / 删除
  assert.ok(apiSrc.includes("${BASE}/settings/shops`"));
  assert.ok(apiSrc.includes("/settings/shops/${shopId}/activate"));
  assert.ok(apiSrc.includes("`${BASE}/settings/shops/${shopId}`, { method: \"DELETE\" }"));

  // 保存 = 走 addShop（后端验证通过才落库），不再用 saveSettings 直接写
  assert.match(page, /const saved = await temuPublishApi\.addShop\(token\);/);
  assert.ok(!page.includes("access_token: accessToken.trim() || undefined"));

  // 切换 / 删除店铺
  assert.match(page, /const switchShop = \(shopId: string\) =>/);
  assert.match(page, /await temuPublishApi\.activateShop\(shopId\)/);
  assert.match(page, /const deleteShop = \(shop: TemuShop\) =>/);

  // 列表 UI：当前使用标记 + 切换到这家
  assert.ok(page.includes("已保存的店铺（"));
  assert.ok(page.includes("切换到这家"));
  assert.ok(page.includes("temu-shop-badge"));
});

test("首屏不闪「还没绑定店铺」：状态没回来之前不渲染横幅", () => {
  // status 初值是 null，接口回来前 ready 也是 false —— 直接按 !ready 渲染就会闪一下红条
  assert.match(page, /const \[statusLoaded, setStatusLoaded\] = useState\(false\);/);
  assert.match(page, /setStatusLoaded\(true\);/);
  assert.match(page, /const ready = !!status\?\.configured\.ready;/);
  // 横幅必须同时满足「已加载」和「未绑定」
  assert.match(page, /\{statusLoaded && !ready && \(/);
  assert.ok(!/\{!ready && \(\s*<div className="temu-bind-banner">/.test(page));
  // 加载期间给一行轻提示，而不是留空
  assert.match(page, /\{!statusLoaded && <p className="temu-muted temu-console-loading">正在读取店铺配置…<\/p>\}/);
});

test("多件装：每件装数量填 4/12 才发 productSkuMultiPackReq", () => {
  // 表单字段 + 落进每个 SKU
  assert.match(page, /piecesPerPack: string;/);
  assert.match(page, /piecesPerPack: "1",/);
  assert.match(page, /pieces_per_pack: Math\.max\(1, Math\.round\(Number\(form\.piecesPerPack \|\| "1"\) \|\| 1\)\)/);
  // 界面上有输入 + 说明（标题里的 4 Pack / 12Pcs）
  assert.ok(page.includes("每件装数量"));
  assert.ok(page.includes("4 Pack / 12Pcs"));
  // 发货时效默认 7 天（1 天等于承诺 24 小时发货，做不到）
  assert.match(page, /const DEFAULT_SHIPMENT_DAYS = "7";/);
  assert.match(page, /shipmentDays: DEFAULT_SHIPMENT_DAYS,/);
  assert.match(page, /placeholder=\{DEFAULT_SHIPMENT_DAYS\}/);
  // 本机存档里旧的 1 天要纠正，模板里的 1 天也不能沿用
  assert.match(page, /if \(saved\.shipmentDays === "1"\) saved\.shipmentDays = DEFAULT_SHIPMENT_DAYS;/);
  assert.ok(page.includes("填太短会被判延迟发货"));
});

test("变种面板：货号清洗特殊字符 + 批量值没应用要提示", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");

  // 颜色/尺码里的 + ( ) 空格 等不进 SKU 货号（Temu 只保证字母数字 -_ 安全）
  assert.match(panel, /function safeCodePart\(value: string\): string \{/);
  assert.match(panel, /const parts = \[extCodePrefix \|\| "SKU", safeCodePart\(colorName\), safeCodePart\(sizeName\)\]/);

  // 批量框填了但没点「应用到全部」时，值不会进提交，必须显式提示
  assert.match(panel, /const pendingBatchFields = !batchTouched/);
  assert.match(panel, /有 \{pendingBatchFields\.length\} 项还没应用/);
  assert.match(panel, /is-urgent/);
});

test("尺码表模板字段按类目隐藏（宠物用品类目不需要）", () => {
  const apiSrc = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");

  assert.match(types, /export type TemuSizeChartInfo = \{/);
  assert.ok(apiSrc.includes("/categories/${catId}/size-charts"));

  // 叶子类目变了就查一次；失败按「需要」处理（fail-open，别把必填项藏掉）
  assert.match(page, /const \[sizeChart, setSizeChart\] = useState<\{ loading: boolean; has: boolean \| null \}>/);
  assert.match(page, /\.sizeCharts\(leafId\)/);
  assert.match(page, /setSizeChart\(\{ loading: false, has: !!info\.has_size_chart \}\)/);
  assert.match(page, /\.catch\(\(\) => \{\s*if \(alive\) setSizeChart\(\{ loading: false, has: null \}\);/);

  // 只有 has === false（明确不需要）才不渲染
  assert.match(page, /\{sizeChart\.has !== false && \(/);
  assert.ok(page.includes("无关类目会自动隐藏"));
});

test("切店后必须重取店铺级数据（运费模板/仓库/尺码表）", () => {
  // 这两样每家店各一套：带着上一家店的 id 去发新店，Temu 只会回「仓库不存在」
  assert.match(page, /const reloadShopScopedData = async \(\) => \{/);
  assert.match(page, /temuPublishApi\.freightTemplates\(\[SITE_ID\]\)/);
  assert.match(page, /temuPublishApi\.warehouses\(\[SITE_ID\]\)/);
  assert.match(page, /setRows\(\(current\) => current\.map\(\(row\) => \(\{ \.\.\.row, warehouseId: nextWarehouse \}\)\)\)/);

  // 三个入口都必须调：新增店铺 = 直接启用，换店，删除店铺
  const callSites = page.split("await reloadShopScopedData();").length - 1;
  assert.equal(callSites, 3, "新增/切换/删除店铺都要重取店铺级数据");

  // 尺码表按类目 + 店铺版本号重取
  assert.match(page, /\}, \[categoryPath, shopEpoch\]\)/);

  // 停用店铺要把旧 id 清掉，别留给下一家店
  assert.match(page, /setFreightTemplates\(\[\]\)/);
  assert.match(page, /freightId: "", warehouseId: ""/);
});

test("发布成功要直接显示商品 id（不用去翻原始 JSON）", () => {
  assert.match(page, /function goodsIdOf\(result: unknown\): string \{/);
  assert.match(page, /const publishedGoodsId = publishResult/);
  assert.match(page, /\{publishedGoodsId \? ` · 商品 id \$\{publishedGoodsId\}` : ""\}/);
});

test("批量发布与单条发布同一套规矩（货号清洗/详情图尺寸/预设规格值）", () => {
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  // 货号里的括号、加号、斜杠会让 Temu 直接拒，批量路径也要清洗
  assert.match(batch, /function safeCode\(value: string \| undefined \| null\): string \{/);
  assert.match(batch, /\[safeCode\(prefill\.sku\) \|\| "SKU", safeCode\(color\), safeCode\(size\)\]/);

  // 详情图必须统一宽度并带真实尺寸，否则 2000306「详情图宽度不正确」
  assert.match(batch, /const DETAIL_WIDTH = 800;/);
  assert.match(batch, /temuPublishApi\.uploadDetailImage\(image, DETAIL_WIDTH, leaf\)/);
  assert.match(batch, /detail_images: detailImages/);

  // 颜色/尺码优先用类目预设值；只有类目压根没有预设值时才 createSpec
  assert.match(batch, /const saleIndex = new Map<number, Array<\{ name: string; specId: number \}>>\(\)/);
  assert.match(batch, /const matched = matchPresetValue\(name, options\.map\(\(item\) => item\.name\)\)/);
  assert.ok(
    batch.indexOf("if (preset) {") < batch.indexOf("temuPublishApi.createSpec(parentId, name)"),
    "必须先试预设值，匹不到才自建规格",
  );
});

test("批量发布的提示与商品 id 不能静默丢掉", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  assert.match(library, /const result = await temuPublishApi\.publish\(draft\);/);
  assert.match(library, /if \(result\.warnings\?\.length\) \{/);
  assert.match(library, /const warnText = warned/);
  assert.match(library, /const idText = publishedIds\.length/);
  assert.match(library, /\$\{idText\}\$\{skippedText\}/);
});

test("类目不需要尺码表时不带旧的尺码模板 id", () => {
  assert.match(page, /size_template_ids: sizeChart\.has === false \? \[\] : sizeTemplates,/);
});

test("没有销售属性的类目按单 SKU 发（桌布这类永远发不出去的老问题）", () => {
  // saleValueIndex 为空 = 该类目没有 isSale 属性 → 不调 ensureSpec，规格发空数组
  assert.match(page, /const singleSku = saleValueIndex\.size === 0;/);
  assert.match(page, /main_specs: \[\],\n\s*skus: \[buildSku\(row, "变种第 1 行", \[\], skcImage\)\],/);
  assert.match(page, /该类目没有销售属性（不能建颜色\/尺码变种）：只提交第 1 行/);

  // 价格/库存/物流字段只能有一份映射（抽成 buildSku，两条路径共用）
  assert.match(page, /const buildSku = \(\n\s*row: VariantRow,/);

  // 变种面板要按这个开关收起变种生成区与颜色/尺码两列
  assert.match(page, /singleSku=\{saleValueIndex\.size === 0\}/);
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /singleSku\?: boolean;/);
  assert.match(panel, /\{!singleSku && \(/);
  assert.match(panel, /temu-variant-row\$\{singleSku \? " is-single" : ""\}/);
});

test("批量发布同样支持单 SKU 类目（不再无脑 createSpec）", () => {
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  assert.match(batch, /if \(saleIndex\.size === 0\) \{/);
  assert.match(batch, /main_specs: \[\],\n\s*skus: \[skuFields\(singleCode, \[\]\)\],/);
  assert.ok(
    batch.indexOf("if (saleIndex.size === 0) {") < batch.indexOf("const colorSpec = await ensure("),
    "单 SKU 分支必须排在规格解析之前",
  );
});

test("属性输入支持「下拉 + 数值」并存（成分比例这类）", () => {
  // 封面材质这类属性既有预设值又有单位 %：只渲染下拉就永远填不进比例
  assert.match(page, /const attrField = \(item: TemuProperty, required: boolean\) => \{/);
  assert.match(page, /\{item\.values\?\.length \? \(/);
  assert.match(page, /\{unit \? \(\n\s*<input/);
  assert.match(page, /item\.numberInputTitle \|\| "请输入数值"/);
  assert.match(page, /\{requiredProps\.map\(\(item\) => attrField\(item, true\)\)\}/);
  assert.match(page, /\{optionalProps\.map\(\(item\) => attrField\(item, false\)\)\}/);

  // 带单位的必填属性光选下拉不算填好
  assert.match(page, /if \(item\.valueUnit\?\.length && !pick\.text\.trim\(\)\) return true;/);
});

test("图片张数上限在上传时就挡（外包装 6 张、轮播 10 张）", () => {
  assert.match(page, /const IMAGE_LIMITS: Record<ImageSlot, number> = \{ carousel: 10, outer: 6, detail: 30 \};/);
  assert.match(page, /const exist = splitLines\(slotText\(slot\)\)\.length;/);
  assert.match(page, /if \(exist \+ files\.length > limit\) \{/);
  // 提交前再兜一次（草稿带入 / 手填 URL 也要守上限）
  assert.match(page, /if \(outerSource\.length > 6\) throw new Error\("外包装图片最多 6 张"\);/);
  assert.match(page, /if \(detailSource\.length > IMAGE_LIMITS\.detail\) \{/);
});

test("货号只发字母数字（中文/符号会让 Temu 直接拒）", () => {
  assert.match(page, /function cleanExtCode\(value: string\): string \{/);
  assert.match(page, /\.replace\(\/\[\^A-Za-z0-9_-\]\+\/g, ""\)/);

  // 产品货号含中文：清洗后为空就报错，否则提示被改成了什么
  assert.match(page, /产品货号只能包含字母、数字、- 和 _（不能含中文或其它符号）/);
  assert.match(page, /产品货号里的中文\/符号已自动去掉/);
  assert.match(page, /含中文\/符号，提交时会去掉/);

  // 每个出口都用清洗后的值：SKC 货号、变种前缀、SKU 货号
  assert.match(page, /ext_code: \(productCode \|\| "SKC"\)\.slice\(0, 120\),/);
  assert.match(page, /ext_code: \[productCode \|\| "SKC", cleanExtCode\(color\)\]/);
  assert.match(page, /const skuCode = cleanExtCode\(row\.extCode\);/);
  assert.match(page, /extCodePrefix=\{productCode\}/);
});

test("标题用多行文本域（150+ 字符单行看不见）", () => {
  assert.match(
    page,
    /<textarea\n\s*id="temu-title-input"\n\s*className=\{`temu-input\$\{[^}]*\}`\}\n\s*value=\{form\.title\}/,
  );
  assert.match(page, /rows=\{3\}/);
  // 标题本身已是英文时，英文标题字段提示可留空
  assert.match(page, /标题已是英文，这里可留空/);
});

test("产地/站外链接按 Temu 的格式约束", () => {
  // productOrigin.region1ShortName 要两位国家代码，填「中国」会被拒
  assert.match(page, /maxLength=\{2\}/);
  assert.match(page, /patch\(\{ origin: event\.target\.value\.toUpperCase\(\) \}\)/);
  // outerGoodsUrl 只收 https
  assert.ok(page.includes("站外产品链接必须是 https:// 开头的地址"), "站外链接要校验 https");
});

test("草稿库改成服务端分页，30 条/页（不再一次拉 80 条）", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const apiSrc = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");

  assert.match(library, /const PAGE_SIZE = 30;/);
  // 请求带 page/page_size/source/q，而不是只给一个 limit
  assert.match(apiSrc, /draftLibrary: \(params: \{/);
  assert.match(apiSrc, /page_size: params\.pageSize \?\? 30,/);
  assert.match(library, /pageSize: PAGE_SIZE,/);
  assert.match(library, /source: nextFilter === "all" \? "" : nextFilter,/);
  assert.match(library, /q: override\?\.query \?\? query,/);
  // 页码/页签/关键字变化才请求；不再前端 slice
  assert.match(library, /\}, \[page, filter, query\]\);/);
  assert.doesNotMatch(library, /visibleEntries\.slice\(/);
  assert.doesNotMatch(library, /const pageSize = 50;/);
});

test("草稿库搜索防抖 + 页签切换回到第一页", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  assert.match(library, /const SEARCH_DEBOUNCE_MS = 300;/);
  assert.match(library, /const timer = window\.setTimeout\(\(\) => \{/);
  assert.match(library, /if \(next !== query\) \{\n\s*setQuery\(next\);\n\s*setPage\(1\);/);
  // 切页签清空勾选（勾选数与可见条数必须一致）
  assert.match(library, /setFilter\(key\);/);
});

test("草稿库按发布 payload 把信息摆出来（类目/价格分/库存/尺寸/完整度）", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 完整度：缺类目/申报价/货号/重量/尺寸 会被点名
  assert.match(library, /function draftMissingFields\(/);
  assert.match(library, /missing\.push\("类目"\)/);
  assert.match(library, /missing\.push\("申报价"\)/);
  assert.match(library, /missing\.push\("货号"\)/);
  assert.match(library, /missing\.push\("重量"\)/);
  assert.match(library, /missing\.push\("尺寸"\)/);
  assert.match(library, /temu-ready is-ok/);
  assert.match(library, /缺 \{missing\.join\(" \/ "\)\}/);

  // 价格只显示「元」：以前还附一行「15000 分」，数字大又没人看
  assert.doesNotMatch(library, /\{Math\.round\(price \* 100\)\} 分/);
  assert.match(library, /return <strong>¥\{price\.toFixed\(2\)\}<\/strong>;/);
  // 未填与 0 的语义分开
  assert.match(library, /temu-draft-missing/);
  assert.match(library, /title="0 库存仍可建品，但在店里是不可售状态"/);
  // 尺寸重量摘要
  assert.match(library, /function specSummary\(/);
  assert.match(library, /return `\$\{dim\} cm · \$\{weightText\}`;/);
});

test("价格照常带入，但界面文案里不出现「推算」二字", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");

  // 功能：成本算出来的价照旧当申报价带进发布页（不拦、不置空）
  assert.match(library, /supplierPrice: moneyText\(entry\.declared_price\),/);
  assert.doesNotMatch(library, /declared_price_estimated \? ""/);
  // 有价就算齐，不会因为「是算出来的」被判缺项
  assert.doesNotMatch(library, /declared_price_estimated\) missing\.push/);

  // 措辞：四个文件（草稿库、发布页、批量发布、类型注释）都不许出现这两个字
  for (const source of [library, page, batch, types]) {
    assert.doesNotMatch(source, /推算/, "界面文案里不应出现「推算」");
  }
});

test("草稿库区分「草稿自带分类」和「已选 Temu 类目」", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 三态：发布页选过的 / 草稿自带（只是自由文本）/ 都没有
  assert.match(library, /function categoryState\(/);
  assert.match(library, /if \(memo\[entry\.id\]\?\.label\) return "picked";/);
  assert.match(library, /if \(entry\.category_name\) return "draft";/);
  assert.match(library, /草稿分类：\$\{label\}/);
  // 草稿自带分类用中性色，不当成错误
  assert.match(library, /state === "draft" \? "is-draft"/);

  // 完整度只认发布页真选过的类目：自由文本不是类目 id，发布时照样得选
  assert.match(library, /if \(!categoryMemo\[entry\.id\]\?\.label\) missing\.push\("类目"\);/);
});

test("右侧「发布检查」下滑时固定在视口", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 吸顶：top 用变量（顶栏 pin 状态不同高度不同），内容超高时自己滚
  assert.match(css, /\.is-console \.temu-console-rail \{\n\s*position: sticky;/);
  assert.match(css, /top: var\(--temu-rail-sticky-top, 132px\);/);
  assert.match(css, /max-height: calc\(100vh - var\(--temu-rail-sticky-top, 132px\) - 16px\);/);
  assert.match(css, /overscroll-behavior: contain;/);

  // 窄屏变单列，侧栏跑到表单下面，不能再吸顶
  assert.match(css, /@media \(max-width: 1180px\) \{\n\s*\.is-console \.temu-console-rail \{\n\s*position: static;/);

  // 顶栏高度实测写入变量（顶栏 pinned 时才计算高度）
  assert.match(page, /const syncRailTop = \(\) => \{/);
  assert.match(page, /classList\.contains\("is-pinned"\)/);
  assert.match(page, /setProperty\(\s*"--temu-rail-sticky-top",/);
  assert.match(page, /new MutationObserver\(syncRailTop\)/);
});

test("发布检查：每一项都说清「还差什么」，被拦时自动定位", () => {
  // 检查清单不再是静态说明：没填完就列缺项
  assert.match(page, /const flowBase: Array<\{ id: string; label: string; hint: string; missing: string\[\] \}> = \[/);
  assert.match(page, /missingAttrs > 0 \? \[`\$\{missingAttrs\} 个必填属性`\] : \[\]/);
  assert.match(page, /incompleteRows \? \[`\$\{incompleteRows\} 行没填全`\] : \[\]/);
  assert.match(page, /还差：\$\{step\.missing\.join\("、"\)\}/);
  assert.match(page, /remainingContentSteps\.length \? `还差 \$\{remainingContentSteps\.length\} 项` : "可提交"/);

  // 点发布被拦：标红必填项 + 滚到第一个没填完的区块
  assert.match(page, /const draft = await composeDraft\(\)\.catch\(\(cause: unknown\) => \{/);
  assert.match(page, /setShowFieldErrors\(true\);/);
  assert.match(page, /const firstGap = flowSteps\.find\(\(step\) => step\.id !== "sec-publish" && !step\.done\);/);

  // 必填项只在「被拦过之后」标红，且属性/标题都覆盖
  assert.match(page, /const \[showFieldErrors, setShowFieldErrors\] = useState\(false\);/);
  assert.match(page, /showFieldErrors && emptyRequired \? " is-required-empty"/);
  assert.match(page, /showFieldErrors && !form\.title\.trim\(\) \? " is-bad"/);

  // 错误摘要放固定侧栏（滚下去也看得到），并带操作按钮
  assert.match(page, /className="temu-rail-error"/);
  assert.match(page, /className="temu-rail-actions"/);
  assert.match(page, /onClick=\{\(\) => void publishGoods\(\)\}/);
});

test("发下一条：只清本商品特有的内容，类目/属性/运费/仓库全部留着", () => {
  assert.match(page, /const startNextItem = \(\) => \{/);

  // 清掉的是「这一件」的东西
  assert.match(
    page,
    /title: "",\s*titleEn: "",\s*productCode: "",\s*externalUrl: "",\s*carousel: "",\s*outerImages: "",\s*detailImages: "",\s*description: "",/,
  );
  assert.match(page, /setRows\(\[\]\);/);
  // 不清提交结果的话，侧栏还挂着上一条的「已提交 · 商品 id」
  assert.match(page, /setPublishResult\(null\);/);

  // 一批商品共用的设置一个都不许清（类目/属性/运费模板/仓库/时效/开关/产地）
  const start = page.indexOf("const startNextItem = () => {");
  const body = page.slice(start, page.indexOf("  };", start));
  for (const keep of [
    "freightId",
    "warehouseId",
    "categoryPath",
    "attrPicks",
    "colorsText",
    "sizesText",
    "origin",
    "piecesPerPack",
    "isSensitive",
  ]) {
    assert.ok(!body.includes(keep), `「发下一条」不该清掉共用设置：${keep}`);
  }

  // 上一条草稿的预填数据要一起清：留着会被「rows 为空就消费 prefillRef」重新填回来
  assert.match(page, /prefillRef\.current = null;\s*prefillDraftId\.current = "";\s*setPrefillInfo\(null\);/);

  // 标题清空后不能被自动匹配把已选类目冲掉
  assert.match(page, /categoryTouchedRef\.current = true;\s*jumpTo\("sec-info"\);/);
  // 光标落到标题，接着就能打下一件
  assert.match(page, /id="temu-title-input"/);
  assert.match(page, /document\.getElementById\("temu-title-input"\)\?\.focus\(\{ preventScroll: true \}\)/);

  // 两个入口都要有：侧栏（成功后主按钮换成它）+ 底部结果块
  assert.equal(page.split("onClick={startNextItem}").length - 1, 2, "侧栏和结果块各一个入口");
});

test("Ctrl/⌘ + Enter 直接提交，且不会重复上架", () => {
  assert.match(page, /if \(event\.key !== "Enter" \|\| !\(event\.ctrlKey \|\| event\.metaKey\)\) return;/);
  // 提交中/别的任务在跑/未就绪时直接忽略，避免连按重复提交
  assert.match(page, /if \(publishing \|\| busy !== "" \|\| !ready\) return;/);
  assert.match(page, /event\.preventDefault\(\);/);
  // 用 ref 取最新函数，不是每次 render 重绑监听
  assert.match(page, /const publishRef = useRef\(publishGoods\);/);
  assert.match(page, /void publishRef\.current\(\);/);
});
