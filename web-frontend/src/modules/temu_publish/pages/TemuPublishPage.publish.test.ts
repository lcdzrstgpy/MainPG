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
  // 第一张是 Temu 轮播主图（同时当素材图），要标出来且不能前移
  assert.match(strip, /index === 0 && <em className="temu-img-main">主图<\/em>/);
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
  // 解绑只清店铺授权：服务商 client_id / token 是预置的，换店还要用，不能一起删
  assert.match(page, /window\.confirm\(\s*"解绑只清掉这个账号的店铺授权/);
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
  assert.match(page, /每个账号填自己的 token，互不影响/);
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

test("草稿金额是「元」，带入发布页要换成「分」", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  // 草稿(POD 导出/AI 结果)用元，发布页用分：必须 ×100，否则 ¥150 会变成 1.5 元
  assert.match(library, /function yuanToCents\(value: number \| string \| null \| undefined\): string \{/);
  assert.match(library, /return String\(Math\.round\(number \* 100\)\);/);
  assert.match(library, /supplierPrice: yuanToCents\(entry\.declared_price\)/);
  assert.match(library, /suggestedPrice: yuanToCents\(entry\.suggested_price\)/);
  // 起价/库存等其它数值不参与换算
  assert.match(library, /weightKg: weightG \? \(Number\(weightG\) \/ 1000\)\.toFixed\(3\) : ""/);
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
