import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");
// 属性表单与可见性规则已抽成共享实现（发布页和批量发布弹窗共用）
const propertyForm = readFileSync(new URL("../components/TemuPropertyForm.tsx", import.meta.url), "utf8");
const attrModel = readFileSync(new URL("../data/attributeModel.ts", import.meta.url), "utf8");
// 图片比例裁切抽成共用模块了：单件发布和批量发布必须用同一套尺寸
const imageShape = readFileSync(new URL("../data/imageShape.ts", import.meta.url), "utf8");

test("发布商品：提交前必须人工确认（外部副作用，不允许自动触发）", () => {
  // 真提交会在这个店铺里创建商品，必须先确认再发请求。
  // 2026-11-10：原生 window.confirm → 应用内弹窗（WebView 可能禁用原生框，
  // 点了提交直接返回 false，看着像「没反应」）。
  assert.match(page, /const publishGoods = \(\) =>/, "缺少 publishGoods 处理函数");
  assert.match(page, /const \[publishConfirm, setPublishConfirm\] = useState</);
  // 点提交：先校验、弹确认框，不能直接打接口
  assert.ok(
    page.indexOf('setPublishConfirm({ kind: "submit", draft, skuCount })') <
      page.indexOf('temuPublishApi.publish(draft, job.kind'),
    "确认必须发生在调用提交接口之前",
  );
  // 真正提交只在 confirmPublish（弹窗里点「确认提交」）里发生
  assert.match(page, /const confirmPublish = async \(\) => \{/);
  assert.match(page, /const published = await temuPublishApi\.publish\(draft, job\.kind === "duplicate"\);/);
  // 弹窗要说明后果：真实创建 + 不重试
  assert.match(page, /真的在店铺里创建商品/);
  assert.match(page, /不支持重试/);
  // 发布流程内不再用原生 confirm（店铺删除等其它位置不受影响）
  const publishFlow = page.slice(
    page.indexOf("const publishGoods = () =>"),
    page.indexOf("const copyPayload = async () => {"),
  );
  assert.doesNotMatch(publishFlow, /window\.confirm\(/);
});

test("发布商品：build 与 publish 复用同一份草稿，避免两条路径走偏", () => {
  assert.match(page, /const composeDraft = async \(\): Promise<TemuGoodsDraft> =>/, "缺少 composeDraft");
  // build / publish / 类目必填校验三条入口都必须走同一个 composeDraft
  const composeCalls = page.match(/await composeDraft\(\)/g) ?? [];
  assert.ok(composeCalls.length >= 2, `composeDraft 至少被 build 与 publish 复用，实际 ${composeCalls.length}`);
  assert.match(page, /const checked = await temuPublishApi\.checkMandatory\(draft\);/);
  // 不允许任何一处绕过 composeDraft 直接拼 payload
  assert.match(page, /const built = await temuPublishApi\.buildPayload\(draft\);/);
  assert.match(page, /const published = await temuPublishApi\.publish\(draft, job\.kind === "duplicate"\);/);
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
  assert.match(page, /if \(publishingRef\.current \|\| publishConfirm\) return;/, "发布要能挡住重复点击（弹窗开着也算）");
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
  // 而且按草稿里的每个 SKU 各铺一行，不是只铺第一行
  assert.match(page, /const seedRows = prefill \? buildRowsFromPrefill\(prefill, whId\) : null;/, "带入草稿要整表重建变种行");
  assert.match(page, /if \(seedRows && seedRows\.length\) return seedRows;/);
});

test("提交发布：币种 CNY、图片裁 1:1、仓库避开 type=3", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  // 半托管供货价按人民币结算，传 USD 会被 Temu 拒
  assert.match(page, /currency_type: "CNY"/);
  assert.doesNotMatch(page, /currency_type: "USD"/);
  // 非服饰类目轮播图强制 1:1 ≥800px，上传前必须裁（详情图例外，见详情图那条测试）
  assert.match(imageShape, /export function toSquareDataUrl\(dataUrl: string, size = SQUARE_IMAGE_SIZE\)/);
  assert.match(page, /toSquareDataUrl\(raw\)/);
  // 仓库：managementType=3 的仓发部分商品会被拒，默认要避开；
  // 下拉里不标「可能不支持」（用户嫌吵），真被拒时失败原因会给提示
  assert.match(types, /managementType\?: string;/);
  assert.match(page, /managementType !== "3"/);
  assert.match(page, /function warehouseLabel\(warehouse: TemuWarehouse\): string/);
  assert.doesNotMatch(page, /可能不支持该商品/);
  assert.doesNotMatch(page, /const risk = warehouse\.managementType/);
});

test("服饰类目：轮播裁 3:4（≥1340×1785），素材图单独 1:1", () => {
  // 实测报错：「服饰类目skc轮播图校验失败，应符合 3:4，宽>=1340，高>=1785」
  //          「素材图[...]校验失败，应符合 1:1，宽>=800」
  // 2026-11-05 真机发布女装连衣裙时卡在这两关。
  assert.match(imageShape, /export const APPAREL_CAROUSEL_WIDTH = 1344;/);
  assert.match(imageShape, /export const APPAREL_CAROUSEL_HEIGHT = 1792;/);
  // 通用按比例居中裁切（1:1 只是它的特例），原图不拉伸
  assert.match(imageShape, /export function toRatioDataUrl\(dataUrl: string, width: number, height: number\): Promise<string>/);
  assert.match(imageShape, /export function toSquareDataUrl\(dataUrl: string, size = SQUARE_IMAGE_SIZE\): Promise<string> \{\s*\n\s*return toRatioDataUrl\(dataUrl, size, size\);/);

  // 判据：这个类目必须有尺码表 = 量体类目（连衣裙 39107 required=true，假发/女包/女鞋 false）
  assert.match(page, /const isApparelCategory = sizeChart\.info\?\.required === true;/);
  assert.match(page, /const apparel = chart\.info\?\.required === true;/);
  // 轮播按类目分支裁切
  assert.match(
    page,
    /apparel\s*\n\s*\? toRatioDataUrl\(raw, APPAREL_CAROUSEL_WIDTH, APPAREL_CAROUSEL_HEIGHT\)\s*\n\s*: toSquareDataUrl\(raw\)/,
  );
  // 素材图不能沿用 3:4 轮播首图：上传轮播时顺手另裁一张 1:1 填进 material
  assert.match(page, /if \(slot === "carousel" && apparel && !form\.material\.trim\(\) && files\[0\]\) \{/);
  assert.match(page, /const materialUrl = pickUploadedUrl\(await temuPublishApi\.uploadImage\(square, leaf\?\.catId\)\);/);
  // 草稿带入的竖图没法自动转（alicdn 取不到 canvas 像素），必须在提交前说清楚
  assert.match(page, /服饰类目的产品素材图要单独填一张 1:1（宽 ≥800px）的方图/);
  // 界面文案也要跟着类目变，不能写着 1:1 实际裁 3:4
  // （2026-11-06 改版：轮播的说明收进上传位，写成一行「3:4 竖图（≥1344×1792）」，
  //   完整规则挂在 title 上 —— 断言跟着改，但「服饰必须出现 3:4 而不是 1:1」这条不能丢）
  assert.match(page, /3:4 竖图（≥1344×1792）· 自动裁切压缩/);
  assert.match(page, /比例 3:4；宽 ≥1340、高 ≥1785/);
  // 非服饰分支仍然要写 1:1 / ≥800×800
  assert.match(page, /1:1 方图（≥800×800）· 自动裁切压缩/);
});

test("批量发布也按类目裁图片：服饰 3:4 + 素材图单独 1:1", () => {
  // 批量发布走的是草稿库里的 AI/POD 成品图，不经过发布页的上传流程。
  // 以前这里「原图直接传 + 素材图 = 轮播首图」，发服饰类目必撞两条校验。
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");
  // 和单件发布共用同一套尺寸常量/裁切函数，不能各写一份
  assert.match(batch, /from "\.\/imageShape";/);
  assert.match(batch, /const apparel = await isApparelCategory\(leaf\);/);
  assert.match(batch, /const carouselShape = apparel \? APPAREL_CAROUSEL_SHAPE : SQUARE_SHAPE;/);
  // 限并发上传（每条十几张图，串行太慢）
  assert.match(batch, /await mapWithConcurrency\(carouselSources, IMAGE_UPLOAD_CONCURRENCY, \(path\) =>\s*\n\s*toTemuImage\(path, leaf, carouselShape\),/);
  // 本地成品图才裁得了：取 blob → canvas 裁 → 再传
  assert.match(batch, /const shaped = await toRatioDataUrl\(raw, shape\.width, shape\.height\);/);
  // 服饰素材图不能拿 3:4 的轮播首图，用第一张原图另裁 800×800
  assert.match(batch, /material = pickUrl\(await temuPublishApi\.uploadImage\(await toSquareDataUrl\(firstRaw\), leaf\)\);/);
  assert.match(batch, /material_image_url: material,/);
  assert.doesNotMatch(batch, /material_image_url: carousel\[0\],/);
  // 一批草稿共用一个类目：查询按 catId 缓存，30 件不会打 30 次网关
  assert.match(batch, /const apparelCache = new Map<number, Promise<boolean>>\(\);/);
  assert.match(batch, /const cached = apparelCache\.get\(catId\);/);
  // 必须把来源草稿 id 带上去：后端发布成功后靠它把草稿移出草稿库，
  // 以前没带 → 「发成功了但刷新草稿还在」，同一件商品很容易再发一遍
  assert.match(batch, /draft_id: prefill\.draftId \?\? "",/);
  assert.match(batch, /category_names: templatePath\.map\(\(item\) => item\.catName\)\.filter\(Boolean\),/);
});

test("草稿带入的图也按类目裁比例（不是只有手动上传才裁）", () => {
  // 以前只有 uploadTo（手动上传）走裁切，composeDraft 里的 resolveImage 是原图直传 ——
  // 从草稿库带入的图全靠原图比例碰运气，服饰类目必挂。
  assert.match(page, /const resolveImage = async \(\s*url: string,\s*cateId\?: number,\s*shape\?: \{ width: number; height: number \},\s*\): Promise<string> => \{/);
  // 缓存 key 必须带比例：同一张图先按 1:1 传过、改类目后按 3:4 传，不能直接还回 1:1 的地址
  assert.match(page, /const cacheKey = shape \? `\$\{text\}\|\$\{shape\.width\}x\$\{shape\.height\}` : text;/);
  assert.match(page, /const carouselShape = apparel \? APPAREL_CAROUSEL_SHAPE : SQUARE_SHAPE;/);
  assert.match(page, /carousel = await resolveImages\(carouselSource, leaf\.catId, carouselShape, markUploaded\);/);
  assert.match(page, /outerImages = await resolveImages\(outerSource, leaf\.catId, SQUARE_SHAPE, markUploaded\);/);
  assert.match(page, /await resolveImage\(materialSource, leaf\.catId, SQUARE_SHAPE\)/);
});

test("批量发布：属性 pid 还原原始 pid，并补齐 refPid / 数值录入", () => {
  // 模板里存的 attrs 键是「会话键」：同 pid 多属性的类目会变成 refPid。
  // 以前直接 Number(key) 当 pid 提交 —— 服饰类目会发出一堆根本不存在的 pid。
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");
  assert.match(batch, /pid: prop\?\.realPid \?\? sessionPid,/);
  assert.match(batch, /ref_pid: prop\?\.refPid \?\? undefined,/);
  assert.match(batch, /template_pid: prop\?\.templatePid \?\? undefined,/);
  assert.match(batch, /number_input_value: hasOptions \? pick\.text : "",/);
  // 属性表拿不到就别硬发：pid/refPid 还原不出来
  assert.match(batch, /if \(Object\.keys\(templateAttrs\)\.length && !attrs\) \{/);
});

test("变种行的单独预览图也要转存图床（dataURL / 本地路径不能直发）", () => {
  // 变种图可以是本地文件（FileReader 转的 dataURL）或本机媒体路径：
  // 直发 payload 会超长 / 内网地址，Temu 读不到。
  assert.match(page, /const rowThumbs = new Map<string, string>\(\);/);
  assert.match(page, /rowThumbs\.set\(row\.key, await resolveImage\(raw, leaf\.catId\)\);/);
  assert.match(page, /thumb_url: rowThumbs\.get\(row\.key\) \|\| skcImage,/);
  assert.match(page, /const skcImage = rowThumbs\.get\(row\.key\) \|\| carousel\[0\];/);
  assert.match(page, /const skcImage = colorRows\.map\(\(row\) => rowThumbs\.get\(row\.key\)\)\.find\(Boolean\) \|\| carousel\[0\];/);
});

test("属性数值录入：纯数值属性不带 numberInputValue，下拉+数值才带", () => {
  // 实测两关报错：面料克重「属性数值录入信息必须为空」、成分「属性数值录入不能为空」
  // 区别是属性有没有预设可选值（成分有棉/涤纶，克重 values 是空数组）
  assert.match(page, /const hasOptions = \(attr\?\.values\?\.length \?\? 0\) > 0;/);
  assert.match(page, /number_input_value: hasOptions \? text : "",/);
  // 同一个 pid 挂多条属性时会话里用 refPid 当键，提交必须还原成原始 pid
  assert.match(page, /pid: attr\?\.realPid \?\? Number\(pid\),/);
  // 类目属性表统一在 api 层规范，页面 / 批量弹窗 / 估填不会有人漏调
  const apiSrc = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");
  assert.match(apiSrc, /import \{ normalizeDuplicatePids \} from "\.\.\/data\/attributeModel";/);
  assert.match(apiSrc, /\(attrs\) => normalizeDuplicatePids\(attrs\) \?\? attrs,/);
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
  // map 回调体改成块级了（要在里面算「服饰类目 3:4 / 非服饰 1:1」的文案）
  assert.match(page, /\{IMAGE_SLOTS\.map\(\(slot\) => \{/);
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
  // 规格框优先用「全部取值」（11 个颜色都要列出来），没有才退回单个值
  assert.match(page, /prefill\.specValues2\?\.length \? prefill\.specValues2\.join\(","\) : prefill\.specValue2/);
  assert.match(page, /prefill\.specValues1\?\.length \? prefill\.specValues1\.join\(","\) : prefill\.specValue1/);
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
  // 服务商 client_id / token 由服务器下发：界面上不给输入框，只给状态说明
  assert.ok(!page.includes("setYlClient"), "不要再用输入框收 client_id");
  assert.ok(!page.includes("setYlToken"), "不要再用输入框收服务商 token");
  assert.match(page, /服务商凭据（client_id \/ token）/);
  // 未配置时要说「登录后自动获取」，而不是让用户去找服务商 ——
  // 安装包里不带凭据，正确动作是登录一次，不是联系服务商开权限
  assert.match(page, /登录后打开本页会自动获取/);
  assert.match(page, /已由服务器下发/);
  // 打开面板时重读一次状态，避免显示成旧值
  assert.match(page, /void temuPublishApi\s*\.settings\(\)/);
  assert.match(page, /if \(!fresh\.configured\.ready\) return undefined;/);
  // 面板要说明账号归属，并展示当前店铺/token 有效期
  assert.match(page, /凭证按登录账号各存一份/);
  assert.match(page, /每个账号存自己的店铺，互不影响/);
  assert.match(page, /status\?\.owner\?\.personal/);
});

test("本机留档：表单/预设/最近分类按登录账号分开存，换账号不串数据", () => {
  // 同一台电脑换账号登录时，不能把上一个人的草稿、预设、分类带出来。
  // storageScope 已抽到 data/attrPicksStore.ts（草稿库也要用同一份账号作用域）
  const store = readFileSync(new URL("../data/attrPicksStore.ts", import.meta.url), "utf8");
  assert.match(store, /export function storageScope\(\): string \{/);
  assert.match(store, /return account\?\.user_id \|\| "local";/);
  assert.match(page, /storageScope,\n\} from "\.\.\/data\/attrPicksStore";/);
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
  // （展开逻辑在 data/variantMerge.ts：按颜色外层、尺码内层铺组合）
  const merge = readFileSync(new URL("../data/variantMerge.ts", import.meta.url), "utf8");
  assert.match(panel, /  colorName: string;/);
  assert.match(merge, /for \(const color of colors\) \{/);
  assert.match(merge, /for \(const size of sizes\) \{/);
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
  assert.match(library, /const variant = variantTemplates\.find\(\(item\) => item\.name === variantTpl\) \?\? null;\n    const tasks: BatchTask\[\] = groups\.flatMap/);
  assert.match(library, /接口非幂等：重复提交会在店铺里多出商品/);
  assert.match(library, /for \(const \[index, task\] of tasks\.entries\(\)\)/);
  assert.match(library, /await temuPublishApi\.publish\(draft\);/);
  assert.match(library, /localStore\.addFailure\(\{/);

  // 组装：一个颜色一个 SKC，价格/尺寸取模板批量值优先
  assert.match(batch, /export async function buildDraftForPublish\(/);
  assert.match(batch, /for \(const \[colorIndex, color\] of colorList\.entries\(\)\) \{/);
  assert.match(batch, /for \(const \[sizeIndex, size\] of sizeList\.entries\(\)\) \{/);
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

  // 取值顺序：本机记忆（人工/自动）→ 草稿自带 category_name → 提示去发布页选
  assert.match(library, /function siteCategoryLabel\(/);
  assert.match(library, /const hit = memo\[entry\.id\];/);
  assert.match(library, /if \(hit\?\.label\) return hit\.auto \? `自动 · \$\{hit\.label\}` : hit\.label;/);
  assert.match(library, /return entry\.category_name \|\| "发布页选类目";/);

  // 带入发布页时把 id 和已选类目一起带过去
  assert.match(library, /draftId: entry\.id,/);
  assert.match(library, /categoryPath: memo\?\.\[entry\.id\]\?\.path,/);

  // 发布页选完类目回写给草稿；下次带入直接套用
  assert.match(page, /prefillDraftId\.current = prefill\.draftId \|\| "";/);
  assert.match(page, /localStore\.rememberDraftCategory\(prefillDraftId\.current, path\);/);
  assert.match(page, /void applyCategory\(prefill\.categoryPath\);/);

  // 批量发布用的是「这一条所属模板」的类目，也要记到草稿上
  assert.match(library, /localStore\.rememberDraftCategory\(entry\.id, itemTemplate\.categoryPath\);/);
});

test("草稿库：编辑在上/用于发布在下，用于发布要先确认基础信息", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 两个按钮的位置互换：编辑在前，用于发布在后。
  // 从 <td> 开始切，别从表头 <th> 开始 —— 其它单元格的 tooltip 里也可能出现「用于发布」
  const actions = library.slice(library.lastIndexOf('<td className="col-actions">'));
  assert.ok(
    actions.indexOf("编辑") < actions.indexOf("用于发布"),
    "操作列里「编辑」应排在「用于发布」前面",
  );

  // 「编辑」直接带这条草稿进发布页（草稿库小弹窗改不了类目/属性/图片）
  assert.match(library, /title="带这条草稿进发布页，改完所有信息后再提交"/);
  assert.match(library, /onClick=\{\(\) => onUseDraft\(entryPrefill\(entry, categoryMemo, attrPrefills\)\)\}/);

  // 点「用于发布」不再直接带入：先弹确认
  assert.match(library, /onClick=\{\(\) => setConfirmEntry\(entry\)\}/);
  assert.match(library, /const \[confirmEntry, setConfirmEntry\] = useState<TemuDraftEntry \| null>\(null\);/);

  // 确认弹窗要列出基础信息，并有「确定发布」才真正带入
  assert.match(library, /确认发布信息/);
  assert.match(library, /申报价格/);
  assert.match(library, /库存/);
  assert.match(library, /const confirmUseDraft = \(\) => \{/);
  assert.match(library, /onUseDraft\(entryPrefill\(confirmEntry, categoryMemo, attrPrefills\)\);/);
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

test("规格值优先用类目预设；只有类目没有预设时才自建", () => {
  // 有预设时必须命中预设（自己 createSpec 造颜色会被 Temu 判「不合法的规格属性 Color_Black」），
  // 没有预设的类目（化妆包/桌布这类自定义规格）才允许自建 —— 边界见下一条测试。
  assert.match(page, /const matchedName = matchPresetValue\(name, options\.map\(\(item\) => item\.name\)\)/);
  assert.match(page, /if \(!options\.length\) \{[\s\S]{0,300}temuPublishApi\.createSpec\(/);
  assert.match(page, /const saleValueIndex = useMemo\(/);
  assert.match(page, /if \(!prop\.isSale\) continue;/);
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

test("属性自动加载的恢复链：settings 拉挂要静默重试，属性补拉失败要允许再试", () => {
  // 自动匹配被 status.configured.ready 门控：settings 在进页面那一刻失败
  // （后端刚重启/网络抖动）时 status 停在 null，没有重试的话草稿库点「编辑」
  // 带入的没记忆过类目的草稿永远不触发属性加载，界面像「点了没反应」。
  assert.match(page, /missed\.push\("店铺凭证"\);\n {10,}\/\/ 店铺凭证没取到/);
  assert.match(page, /for \(const delay of \[2000, 5000, 10000\]\)/);
  assert.match(page, /const fresh = await temuPublishApi\.settings\(\);/);
  // 恢复类目后的属性补拉是一次性的：拉失败必须清掉标记，否则属性区
  // 在这次挂载内永远空白（换类目/重套类目也不会再试）。
  assert.match(
    page,
    /restoredAttrsLoadedRef\.current = false;/,
    "属性补拉失败后要清一次性标记，允许后续重试",
  );
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
  assert.match(page, /setPublishConfirm\(\{ kind: "duplicate", draft, skuCount: job\.skuCount, note: text \}\);/);
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
  assert.match(library, /开始发布（\$\{runnableTargets\.length\} 条）/);
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
  // 变种表：空/负数标红；0 不标红（合法），只用橙色提示「售罄」
  assert.match(panel, /String\(row\.stock\)\.trim\(\) === "" \|\| Number\.isNaN\(Number\(row\.stock\)\) \|\| Number\(row\.stock\) < 0/);
  assert.match(panel, /: Number\(row\.stock\) === 0/);
  assert.match(panel, /is-empty-stock/);
  assert.match(panel, /placeholder="0 也可"/);
  // 草稿带入：0 不再被当空丢掉
  assert.match(library, /stock: numericText\(entry\.stock\),/);
});

test("属性联动：父级取值不满足的子属性不显示、也不提交", () => {
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  assert.match(types, /templatePropertyValueParentList\?:/);
  assert.match(types, /parentTemplatePid\?: number \| null;/);

  // 用 attrs.get 返回的联动规则算「当前适用属性」：父级没选/取值不匹配 → 整项隐藏
  assert.match(attrModel, /export function visibleProperties\(/);
  assert.match(attrModel, /const rules = prop\.templatePropertyValueParentList \?\? \[\];/);
  assert.match(attrModel, /const matched = rules\.filter\(\(rule\) => \(rule\.parentVidList \?\? \[\]\)\.includes\(parentVid\)\);/);
  assert.match(attrModel, /values: \(prop\.values \?\? \[\]\)\.filter\(\(value\) => !allowed\.size \|\| allowed\.has\(value\.vid\)\)/);
  // 页面只负责调用共享实现，不再自带一份规则
  assert.match(page, /const visibleProperties = useMemo<TemuProperty\[\]>\(\s*\n\s*\(\) => visibleAttributeProperties\(attributes, attrPicks\),/);

  // 必填/渲染都用可见属性；提交时过滤掉隐藏项（留着旧值会被判属性不合法）
  assert.match(page, /const requiredProps = visibleProperties\.filter\(\(item\) => item\.required\);/);
  assert.match(page, /const visiblePids = new Set\(visibleProperties\.map\(\(item\) => item\.pid\)\);/);
  assert.match(page, /filter\(\(\[pid\]\) => visiblePids\.has\(Number\(pid\)\)\)/);
});

test("属性联动：子属性原地展开在父属性下面，不跳回必填区顶部", () => {
  const styles = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 子属性按 parentTemplatePid → 父属性 templatePid 归到父名下（规则在共享模型里）
  assert.match(attrModel, /export function buildAttributeGroups\(/);
  assert.match(attrModel, /const parentTemplatePid = Number\(item\.parentTemplatePid \?\? 0\);/);
  assert.match(attrModel, /const parent = byTemplatePid\.get\(parentTemplatePid\);/);
  assert.match(attrModel, /const list = children\.get\(parent\.pid\) \?\? \[\];/);

  // 被父属性带出来的项从必填/选填区剔除，否则会同时出现在两处
  assert.match(attrModel, /nested\.add\(item\.pid\);/);
  assert.match(attrModel, /required: visible\.filter\(\(item\) => item\.required && !nested\.has\(item\.pid\)\),/);
  assert.match(attrModel, /optional: visible\.filter\(\(item\) => !item\.required && !nested\.has\(item\.pid\)\),/);

  // 渲染时递归挂到父下面；叶子项留在原网格，嵌套层才加缩进容器
  assert.match(propertyForm, /const attrGroup = \(item: TemuProperty, required: boolean, depth = 0\): ReactNode => \{/);
  assert.match(propertyForm, /\{kids\.map\(\(child\) => attrGroup\(child, child\.required, depth \+ 1\)\)\}/);
  assert.match(propertyForm, /<div className="temu-attr-children">/);
  // 发布页和批量弹窗用同一个组件，别再各写一份
  assert.match(page, /<TemuPropertyForm/);

  // 缩进 + 竖线是「谁带出来的」唯一提示，必须有样式兜住
  assert.match(styles, /\.temu-attr-children \{[\s\S]*?border-left: 2px solid var\(--theme-module-border-strong/);
  assert.match(styles, /\.temu-attr-group \{[\s\S]*?grid-column: 1 \/ -1;/);
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

  // 2) 传图并发 3 且结果保序（进度不变，只是不再排队）；工具已抽到 data/concurrency.ts 并有行为测试
  const concurrency = readFileSync(new URL("../data/concurrency.ts", import.meta.url), "utf8");
  assert.match(concurrency, /export async function mapWithConcurrency<T, R>\(/);
  assert.match(page, /import \{ mapWithConcurrency \} from "\.\.\/data\/concurrency";/);
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
  // 加载期间给一张正经的加载卡（带转圈 + 说明），而不是留空或一行像报错的灰字
  assert.match(page, /\{!statusLoaded && \(\s*<div className="temu-loading-card"/);
  assert.match(page, /className="temu-loading-spinner"/);
  assert.ok(page.includes("正在读取店铺配置"));
});

test("多件装：每件装数量填 4/12 才发 productSkuMultiPackReq", () => {
  const picker = readFileSync(new URL("../components/ShipmentDaysPicker.tsx", import.meta.url), "utf8");
  const batchUi = readFileSync(new URL("../components/BatchManualConfig.tsx", import.meta.url), "utf8");
  // 表单字段 + 落进每个 SKU
  assert.match(page, /piecesPerPack: string;/);
  assert.match(page, /piecesPerPack: "1",/);
  assert.match(page, /pieces_per_pack: Math\.max\(1, Math\.round\(Number\(form\.piecesPerPack \|\| "1"\) \|\| 1\)\)/);
  // 界面上有输入 + 说明（标题里的 4 Pack / 12Pcs）
  assert.ok(page.includes("每件装数量"));
  assert.ok(page.includes("4 Pack / 12Pcs"));
  // 发货时效默认 9 天 = Temu 半托管预设（777600 秒）。
  // 实测依据：店里 230 件商品中 213 件都是 777600，服务商文档的成功案例也是 777600；
  // 之前我们默认 7 天是自己拍的，跟店里其他商品不一致。
  assert.match(picker, /export const DEFAULT_SHIPMENT_DAYS = "9";/);
  assert.match(picker, /export const SHIPMENT_DAY_OPTIONS = \["1", "2", "7", "9"\] as const;/);
  assert.match(page, /shipmentDays: DEFAULT_SHIPMENT_DAYS,/);
  // 2026-11-05 起改成 Temu 后台同款四档单选 + 自定义，发布页与批量弹窗共用
  assert.match(page, /<ShipmentDaysPicker/);
  assert.match(batchUi, /<ShipmentDaysPicker/);
  assert.match(picker, /checked=\{value === day\}/);
  assert.match(picker, /placeholder="如 12"/);
  // 不能再把 1 天 / 7 天当坏默认清洗掉 —— 它们是合法档位，清洗会让用户选的档位
  // 每次刷新都被改回 9
  assert.ok(!page.includes('saved.shipmentDays === "1"'), "1 天是合法档位，不能再被拉回 9");
  assert.ok(page.includes("选太短会被判延迟发货"));
  assert.ok(page.includes("Temu 半托管预设"), "界面要说清 9 天是 Temu 预设，不是我们编的");
  // 自定义档点了没填：要指名道姓，不能只甩一句「发货时效必须大于 0」
  assert.match(page, /请选择「承诺发货时效」；选了自定义就填一个大于 0 的天数/);
  assert.match(page, /shipment_limit_seconds: Number\(shipmentDays\) \* 86400,/);
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

  // 叶子类目变了就查一次；失败按「不知道」处理（fail-open，别把必填项藏掉）
  assert.match(page, /const \[sizeChart, setSizeChart\] = useState<\{/);
  assert.match(page, /has: boolean \| null;/);
  assert.match(page, /info: TemuSizeChartInfo \| null;/);
  assert.match(page, /\.sizeCharts\(leafId\)/);
  assert.match(page, /setSizeChart\(\{ loading: false, has: !!info\.has_size_chart, info \}\)/);
  assert.match(page, /setSizeChart\(\{ loading: false, has: null, info: null \}\)/);

  // 显示条件改成 required（类目要不要），不是 has（店里有没有）：
  // 服装类目新店一张表都没建，用 has 判会把字段整个藏掉。
  assert.match(page, /\{\(sizeChart\.info\?\.required \|\| sizeChart\.has\) && \(/);
  assert.match(page, /className="temu-sizechart-warn"/);
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

  // 货号只留 A-Za-z0-9-_：括号、加号、斜杠、**中文规格名**都会让 Temu 直接拒（1000002）。
  // 和发布页 cleanExtCode 同一口径；清洗后为空的片段用序号兜底，别让多个颜色撞成同一个货号。
  assert.match(batch, /function safeCode\(value: string \| undefined \| null\): string \{/);
  assert.match(batch, /\.replace\(\/\[\^A-Za-z0-9_-\]\+\/g, ""\)/);
  // 只剩标点残渣不算货号片段：「黑茶色-性价比版」洗完只剩 "-"，多个颜色会全撞成同一个货号
  assert.match(batch, /return \/\[A-Za-z0-9\]\/\.test\(cleaned\) \? cleaned : "";/);
  assert.match(batch, /function codePart\(value: string \| undefined \| null, index: number, prefix: string\): string \{/);
  assert.match(batch, /const colorCodes = colorList\.map\(\(value, index\) => codePart\(value, index, "C"\)\);/);
  assert.match(batch, /\[safeCode\(prefill\.sku\) \|\| "SKU", colorCodes\[colorIndex\], sizeCodes\[sizeIndex\]\]/);

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
  // 判据从 has（店里有没有表）改成 required（类目要不要）：
  // 服装类目「需要但还没建表」时，用户手填的模板 id 仍然要带上。
  assert.match(page, /size_template_ids: sizeChart\.info\?\.required === false \? \[\] : sizeTemplates,/);
});

test("单 SKU 与多规格：按类目能力走，不能只看 isSale", () => {
  // 实测 2026-11-04：化妆包 30988 / 桌布 10498 / 餐垫 10493 的 attrs 里
  // 一个 isSale 都没有，但 inputMaxSpecNum=2，说明允许自定义规格；
  // 假发 31035 反过来（inputMaxSpecNum=0、带两个预设销售属性）。
  // 所以「没有 isSale」不能直接判单 SKU —— 化妆包被硬压成单 SKU 是错的。
  assert.match(page, /const specDimLimit = Number\(attributes\?\.inputMaxSpecNum \?\? 0\);/);
  assert.match(page, /const hasPresetSaleProps = saleValueIndex\.size > 0;/);
  assert.match(page, /const canCustomSpecs = specDimLimit > 0;/);
  // 有预设 → 必须多规格；没有预设但能自定义 → 看用户开关；都没有 → 单 SKU
  assert.match(
    page,
    /const singleSkuMode = hasPresetSaleProps \? false : !\(canCustomSpecs && variantsOn\);/,
  );
  // 单 SKU 分支仍然是「规格发空数组」
  assert.match(page, /main_specs: \[\],\n\s*skus: \[buildSku\(row, "变种第 1 行", \[\], skcImage\)\],/);
  // 没有 isSale 但要能开变种：给一个勾选项，别再写「只能单 SKU」
  // 2026-11-05：从一整行大卡收成标题行右侧的小胶囊（说明进 title）
  assert.match(page, /className="temu-spec-switch"/);
  assert.match(page, /按规格发（多色 \/ 多码）/);
  assert.ok(!page.includes("只能按单 SKU 发布；想按颜色卖就换一个带变种的类目"));

  // 价格/库存/物流字段只能有一份映射（抽成 buildSku，两条路径共用）
  assert.match(page, /const buildSku = \(\n\s*row: VariantRow,/);

  // 变种面板按 singleSku 收起变种生成区与颜色/尺码两列
  assert.match(page, /singleSku=\{singleSkuMode\}/);
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /singleSku\?: boolean;/);
  assert.match(panel, /\{!singleSku && \(/);
  assert.match(panel, /temu-variant-row\$\{singleSku \? " is-single" : ""\}/);
});

test("批量发布同样支持单 SKU 类目（不再无脑 createSpec）", () => {
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  // 规格轴：类目给哪一列就用哪一列；两列都没有（或无一对上）→ 单 SKU。
  // 以前不分轴，缺的那一列还会 createSpec 出一个「均码」，Temu 判不合法。
  // 类目支不支持规格看 inputMaxSpecNum，不能只看 isSale：化妆包/桌布/箱包一个 isSale
  // 都没有但 inputMaxSpecNum=2，按 isSale 判会把 11 个颜色的包压成 1 个变种（2026-11-11 实测）。
  assert.match(batch, /const specDimLimit = Number\(attrs\?\.inputMaxSpecNum \?\? 0\);/);
  assert.match(batch, /const canCustomSpecs = specDimLimit > 0;/);
  assert.match(
    batch,
    /const colorAxis = colors\.length > 0 && !!colorParent && \(saleIndex\.has\(colorParent\) \|\| canCustomSpecs\);/,
  );
  assert.match(batch, /if \(!colorAxis && !sizeAxis\) \{/);
  // 变种值发不出去必须报错，不许静默吞成单 SKU
  assert.match(batch, /这些变种会被吞成单个 SKU/);
  assert.match(batch, /这个类目不支持颜色\/尺码规格/);
  assert.match(batch, /main_specs: \[\],\n\s*skus: \[skuFields\(singleCode, \[\]\)\],/);
  assert.ok(
    batch.indexOf("if (!colorAxis && !sizeAxis) {") < batch.indexOf("const colorSpec = colorAxis"),
    "单 SKU 分支必须排在规格解析之前",
  );
  // 只有颜色或只有尺码时，另一轴不能进 specs
  assert.match(batch, /\[colorSpec, sizeSpec\]\.filter\(Boolean\) as TemuSkuDraft\["specs"\]/);
  assert.match(batch, /main_specs: colorSpec \? \[colorSpec\] : \[\],/);
  // 有预设值的类目仍然不许自建：匹不上预设直接报错，别拿错值硬发
  assert.match(batch, /Temu 只认类目预设值/);
  // 自建出来的规格值跨条目复用，别在店铺规格库里堆几十个同名「黑色」
  assert.match(batch, /const createdSpecCache = new Map</);
  assert.match(batch, /const shared = createdSpecCache\.get\(cacheKey\);/);
});

test("属性输入支持「下拉 + 数值」并存（成分比例这类）", () => {
  // 封面材质这类属性既有预设值又有单位 %：只渲染下拉就永远填不进比例
  assert.match(propertyForm, /const attrField = \(item: TemuProperty, required: boolean\) => \{/);
  assert.match(propertyForm, /\{item\.values\?\.length \? \(/);
  assert.match(propertyForm, /\{unit \? \(\n\s*<input/);
  assert.match(propertyForm, /item\.numberInputTitle \|\| "请输入数值"/);
  assert.match(propertyForm, /\{groups\.required\.map\(\(item\) => attrGroup\(item, true\)\)\}/);
  assert.match(propertyForm, /\{groups\.optional\.map\(\(item\) => attrGroup\(item, false\)\)\}/);

  // 带单位的必填属性光选下拉不算填好 —— 但必须按「单位本身非空」判断，
  // 不能用 valueUnit.length：Temu 对无单位属性返回 [""]，长度是 1、内容是空串，
  // 用 length 会把这批纯下拉属性判成「必须填数值」，而界面不渲染数值框 → 永远填不完。
  assert.match(attrModel, /const unit = String\(item\.valueUnit\?\.\[0\] \?\? ""\)\.trim\(\);/);
  assert.match(attrModel, /if \(unit && !pick\.text\.trim\(\)\) return true;/);
  assert.doesNotMatch(attrModel, /item\.valueUnit\?\.length/);
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
  // 颜色是中文时 cleanExtCode 会清成空串，必须有序号兜底（见下一条测试）
  assert.match(page, /const colorCode = cleanExtCode\(color\) \|\| `C\$\{colorIndex\}`;/);
  assert.match(page, /const skuCode = cleanExtCode\(row\.extCode\);|let skuCode = cleaned;/);
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
  // 改关键字要回到第一页，并且清掉「加载更多」的追加（否则两批数据混在一起）
  assert.match(library, /if \(next !== query\) \{\n\s*setQuery\(next\);\n\s*goPage\(1\);/);
  assert.match(library, /const goPage = \(next: number\) => \{\n\s*setItemOffset\(0\);\n\s*setDeepEntries\(\[\]\);/);
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

  // 四态：发布页选过的 / 按标题自动匹配的 / 草稿自带（只是自由文本）/ 都没有
  assert.match(library, /function categoryState\(/);
  assert.match(library, /const hit = memo\[entry\.id\];/);
  assert.match(library, /if \(hit\?\.label\) return hit\.auto \? "auto" : "picked";/);
  assert.match(library, /if \(entry\.category_name\) return "draft";/);
  assert.match(library, /草稿分类：\$\{label\}/);
  // 草稿自带分类用中性色，不当成错误；自动匹配用橙色提醒
  assert.match(library, /state === "auto"/);
  assert.match(library, /\? "is-auto"/);
  assert.match(library, /state === "draft"/);

  // 完整度只认「有 Temu 类目路径」：草稿自带的自由文本不是类目 id，发布时照样得选；
  // 自动匹配出来的算「有」，但界面上标 auto 提醒确认
  assert.match(library, /if \(!categoryMemo\[entry\.id\]\?\.label\) missing\.push\("类目"\);/);
  assert.match(library, /title=\{\s*state === "picked"/);
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
  assert.match(page, /行没填全（缺：\$\{missingRowLabels\.join\("、"\)\}）/);
  assert.match(page, /还差：\$\{step\.missing\.join\("、"\)\}/);
  assert.match(page, /remainingContentSteps\.length \? `还差 \$\{remainingContentSteps\.length\} 项` : "可提交"/);

  // 变种行缺哪列直接列出来；尺码只在有第二销售属性时才算缺
  //（和提交校验一致 —— 单维类目行尺码本来就该是空的，不能永远「没填全」）
  assert.match(page, /const rowMissingLabels = \(row: VariantRow\): string\[\] => \{/);
  assert.match(page, /if \(sizeParentId > 0 && !row\.sizeName\.trim\(\)\) missing\.push\("尺码"\);/);
  assert.match(page, /const missingRowLabels = Array\.from\(new Set\(rows\.flatMap\(rowMissingLabels\)\)\);/);

  // 点发布被拦：标红必填项 + 滚到「出错的那一块」（定位不到才退回第一个缺口）
  assert.match(page, /const draft = await composeDraft\(\)\.catch\(\(cause: unknown\) => \{/);
  assert.match(page, /setShowFieldErrors\(true\);/);
  assert.ok(page.includes("section || flowSteps.find((step) => step.id !== \"sec-publish\" && !step.done)?.id"));

  // 必填项只在「被拦过之后」标红，且属性/标题都覆盖
  assert.match(page, /const \[showFieldErrors, setShowFieldErrors\] = useState\(false\);/);
  // 属性标红在共享组件里（发布页只传 showErrors）
  assert.match(propertyForm, /showErrors && emptyRequired \? " is-required-empty"/);
  assert.match(page, /showErrors=\{showFieldErrors\}/);
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
  assert.match(page, /if \(publishing \|\| busy !== "" \|\| !ready \|\| publishConfirm\) return;/);
  assert.match(page, /event\.preventDefault\(\);/);
  // 用 ref 取最新函数，不是每次 render 重绑监听
  assert.match(page, /const publishRef = useRef\(publishGoods\);/);
  assert.match(page, /void publishRef\.current\(\);/);
});

test("按钮体系：一套尺寸、层级不互相覆盖、交互反馈齐全", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 尺寸只剩两档：34 / 28（原来 34 / 26 / 26 三套）
  // 不带 .is-console 前缀：成品库、批量弹窗（portal 到 body）都在用小号按钮
  assert.match(css, /\.temu-btn-sm,\s*\n\.temu-btn\.is-small \{/);
  assert.match(css, /min-height: 34px;\s*\n\s*padding: 0 15px;/);

  // 顶部操作区那条"浅底灰字"必须排除主按钮：它选择器比 .temu-btn-primary 具体，
  // 不排除就会把主按钮的实心底色和白字冲成浅底（之前就是这样，主按钮不够醒目）
  assert.match(css, /\.is-console \.temu-console-actions \.temu-btn:not\(\.temu-btn-primary\) \{/);
  assert.doesNotMatch(
    css,
    /\.is-console \.temu-console-actions \.temu-btn \{[^}]*background/,
    "不带 :not 的那条不能设 background，否则会盖掉主按钮",
  );

  // 交互反馈：hover 换底色 / active 下沉 / 键盘焦点可见
  assert.match(css, /\.temu-btn:not\(\.temu-btn-primary\):not\(\.is-danger\):not\(\.is-urgent\):hover:not\(:disabled\)/);
  assert.match(css, /\.temu-btn:active:not\(:disabled\) \{\s*\n\s*transform: translateY\(1px\);/);
  assert.match(css, /\.temu-btn:focus-visible,\s*\n\.temu-link:focus-visible \{/);

  // 禁用的主按钮不许留投影，否则看着像还能点
  assert.match(css, /\.temu-btn-primary:disabled \{[^}]*box-shadow: none;/);

  // 圆角跟主题走，不写死
  assert.match(css, /border-radius: var\(--radius-md, 10px\);/);
});

test("批量改价触发器/行删除按钮走主题变量，不写死颜色", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 触发器原来写死 #1f6f92 / #f4fbfe：换 peach / alpine 主题会串成一套不协调的蓝
  const trigger = css.slice(css.indexOf(".temu-bulk-trigger {"), css.indexOf(".temu-bulk-panel {"));
  // 三个关键属性都必须走主题变量（fallback 里留老颜色是对的，不算硬编码）
  assert.match(trigger, /border: 1px solid var\(--theme-module-border/);
  assert.match(trigger, /color: var\(--theme-module-primary-strong/);
  assert.match(trigger, /background: var\(--theme-module-surface-tint/);
  // hover 也不能回落到固定色
  assert.match(trigger, /background: var\(--theme-module-surface-soft/);

  // 行尾 × ：固定 24×24（原来只有字号，hover 时行高会抖），且有反馈
  assert.match(css, /\.is-console \.temu-variant-del \{[^}]*width: 24px;/);
  assert.match(css, /\.is-console \.temu-variant-del:hover:not\(:disabled\)/);
  assert.match(css, /\.is-console \.temu-variant-del:focus-visible/);
});

test("类目：手动选过就不许自动匹配再改掉，套产品模板时属性不能被清空", () => {
  // ① 手动点分类下钻 / 面包屑回退都算「用户选过类目」。
  //    categoryTouchedRef 只拦自动推荐；漏标的话，改标题后自动匹配会把用户选的
  //    类目静默换掉，连已填属性一起 setAttrPicks({}) 清空。
  const chooseCategory = page.slice(
    page.indexOf("const chooseCategory = (item: TemuCategory) =>"),
    page.indexOf("const resetToLevel = (index: number) =>"),
  );
  assert.ok(chooseCategory.length > 0, "expected chooseCategory block");
  assert.match(chooseCategory, /categoryTouchedRef\.current = true;/);

  const resetToLevel = page.slice(
    page.indexOf("const resetToLevel = (index: number) =>"),
    page.indexOf("/** 直接套用一条完整类目路径"),
  );
  assert.ok(resetToLevel.length > 0, "expected resetToLevel block");
  assert.match(resetToLevel, /categoryTouchedRef\.current = true;/);

  // ② 套用产品模板：必须先切类目（会 setAttrPicks({})）再填属性，
  //    顺序反了就是「套了模板属性还是空的」。
  assert.match(page, /const applyProductTemplate = async \(name: string\) => \{/);
  const applyTemplate = page.slice(
    page.indexOf("const applyProductTemplate = async (name: string) =>"),
    page.indexOf("/** 存成变种模板"),
  );
  const switchCategoryAt = applyTemplate.indexOf("await applyCategory(template.categoryPath, { skipConfirm: true })");
  const fillAttrsAt = applyTemplate.indexOf("Object.entries(template.attrs).forEach");
  assert.ok(switchCategoryAt > -1, "expected awaited category switch inside applyProductTemplate");
  assert.ok(fillAttrsAt > -1, "expected attr fill inside applyProductTemplate");
  assert.ok(
    switchCategoryAt < fillAttrsAt,
    "切类目必须在填属性之前，否则 applyCategory 里的 setAttrPicks({}) 会把刚填的清掉",
  );
});

test("类目：换类目后行里残留的非法规格值一次性列出来，不自动清行", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 检查函数：只按当前类目的销售属性预设值判定，且只返回清单（不改行）
  assert.match(page, /const findSpecMismatches = \(/);
  assert.match(page, /if \(!prop\.isSale\) continue;/);
  // field 用 color/size 内部键：不同类目这两列叫「款式/型号/容量」，不能拿中文当键
  assert.match(page, /out\.push\(\{ rowKey: row\.key, rowNo: index \+ 1, field: "color", value: color \}\);/);
  assert.match(page, /out\.push\(\{ rowKey: row\.key, rowNo: index \+ 1, field: "size", value: size \}\);/);
  // 检查函数本身只产出清单，不许顺手改行（清行会丢价格/重量）；
  // 真正改行的是用户主动点的 fixSpecMismatches，单独一个函数。
  assert.doesNotMatch(
    page.slice(page.indexOf("const findSpecMismatches = ("), page.indexOf("/** 按最接近的预设值")),
    /setRows\(/,
  );

  // 三条换类目路径都要触发检查（点选下钻会直接清属性，靠它提示）
  const chooseCategory = page.slice(
    page.indexOf("const chooseCategory = (item: TemuCategory) =>"),
    page.indexOf("const resetToLevel = (index: number) =>"),
  );
  assert.match(chooseCategory, /setSpecMismatch\(findSpecMismatches\(attrs, rows\)\);/);

  const applyCategory = page.slice(
    page.indexOf("const applyCategory = ("),
    page.indexOf("applyCategoryRef.current = applyCategory"),
  );
  assert.match(applyCategory, /setSpecMismatch\(findSpecMismatches\(attrs, rows\)\);/);
  // 非叶子分支也要清属性（P3：原来只 setAttributes(null)，旧属性会挂着）
  assert.match(applyCategory, /setAttributes\(null\);[\s\S]*?setAttrPicks\(\{\}\);/);

  // 提示区 + 样式
  assert.match(page, /className="temu-spec-mismatch"/);
  // 提示文案不写死「颜色/尺码」：手机壳是「型号」、收纳盒是「容量」
  assert.match(page, /有 \{activeSpecMismatch\.length\} 个规格值不在当前类目的预设里/);
  assert.match(css, /\.temu-spec-mismatch \{/);
  assert.match(css, /\.temu-spec-mismatch ul \{[\s\S]*?max-height: 132px;/);
});

test("类目：刷新类目树入口 + 换类目二次确认", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  const api = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");

  // ① 手动刷新：清后端两层缓存 → 重载当前级 + 叶子属性 → 让标题匹配重算
  assert.match(api, /refreshCategories: \(\) =>/);
  assert.match(api, /`\$\{BASE\}\/categories\/refresh`/);
  // 重载逻辑抽成裸函数，发布被 6000002 拒时也要能复用（那条路不能用 run() 包）
  assert.match(page, /const reloadCategoryAttributes = async \(previous\?: TemuAttributes \| null\) => \{/);
  assert.match(page, /const refreshCategoryTree = \(\) =>\s*run\("category", async \(\) => \{\s*await reloadCategoryAttributes\(\);/);
  const refresh = page.slice(
    page.indexOf("const reloadCategoryAttributes = async ("),
    page.indexOf("/**\n   * 换类目会清空已填的类目属性"),
  );
  assert.ok(refresh.length > 0, "expected reloadCategoryAttributes block");
  assert.match(refresh, /await temuPublishApi\.refreshCategories\(\);/);
  assert.match(refresh, /await loadLevel\(/);
  assert.match(refresh, /setMatchSeq\(\(value\) => value \+ 1\);/);
  // 刷新接口会连属性缓存一起清，前端必须真的重拉 —— 只清不拉的话页面还是旧属性表
  assert.match(refresh, /const attrs = await temuPublishApi\.attributes\(currentLeaf\.catId\);/);
  assert.match(refresh, /setAttributes\(attrs\);/);

  // ② 换类目确认：只在「已填属性 + 换成别的叶子」时问
  assert.match(page, /const confirmCategorySwitch = \(nextLeaf\?: TemuCategory\): boolean =>/);
  const confirm = page.slice(
    page.indexOf("const confirmCategorySwitch = (nextLeaf?: TemuCategory): boolean =>"),
    page.indexOf("const chooseCategory = (item: TemuCategory) =>"),
  );
  assert.match(confirm, /if \(Number\(currentLeaf\.catId\) === Number\(nextLeaf\.catId\)\) return true;/);
  assert.match(confirm, /if \(!filled\) return true;/);
  assert.match(confirm, /window\.confirm\(/);
  // 点选下钻也要走确认（会清属性）
  const chooseCategory = page.slice(
    page.indexOf("const chooseCategory = (item: TemuCategory) =>"),
    page.indexOf("const resetToLevel = (index: number) =>"),
  );
  assert.match(chooseCategory, /if \(!confirmCategorySwitch\(item\)\) return;/);
  // 套用整条路径会确认，但自动推荐 / 套产品模板不问
  assert.match(page, /if \(!options\?\.auto && !options\?\.skipConfirm && !confirmCategorySwitch\(/);
  assert.match(page, /await applyCategory\(template\.categoryPath, \{ skipConfirm: true \}\);/);

  // ③ 按钮与样式在位
  assert.match(page, /onClick=\{\(\) => void refreshCategoryTree\(\)\}/);
  assert.match(page, /刷新类目树/);
  assert.match(css, /\.temu-cat-refresh \{/);
});

test("同事反馈的三点：属性落盘、带入即检查并一键修正、校验在上传之前", () => {
  // ① attrPicks 落盘：**按叶子类目分开存**（连发同款时下一个商品能沿用上一个的填法）。
  //    旧版单条存档 v1 仍兼容读取，升级那一下不该把用户刚填的清空。
  const attrStore = readFileSync(new URL("../data/attrPicksStore.ts", import.meta.url), "utf8");
  assert.match(attrStore, /export const ATTR_PICKS_KEY = "temu_publish_attr_picks_v2";/);
  assert.match(attrStore, /export const ATTR_PICKS_KEY_V1 = "temu_publish_attr_picks_v1";/);
  assert.match(page, /const \[attrPicks, setAttrPicks\] = useState<Record<number, AttrPick>>\(\(\) => \{/);
  // 先读「当前这一条」（刷新恢复），没有再退回同类目历史（老版本只有这个）
  assert.match(page, /const current = readCurrentAttrPicks\(scope, currentLeaf\);/);
  // 必须用 ?? 而不是判空对象：用户清空后存的是 {}，那种情况要保持空，
  // 判 length 的话一刷新又从历史里把值填回来了
  assert.match(page, /return current \?\? readAttrPicksHistory\(scope, currentLeaf\);/);
  // 读：先按 catId 取 v2，没有再退 v1（且只认 catId 对得上的）
  assert.match(attrStore, /const hit = all\?\.\[String\(catId\)\]\?\.picks;/);
  assert.match(attrStore, /if \(Number\(legacy\?\.catId \?\? 0\) === catId && legacy\?\.picks && typeof legacy\.picks === "object"\) \{/);
  // 写：按时间裁剪，别让本地存储无限长
  assert.match(attrStore, /export const ATTR_PICKS_HISTORY_MAX = 20;/);
  // 历史只在「用户自己动过属性下拉」时才写：自动填/草稿估填铺的值写进历史，
  // 等于教同类目以后都这么填 —— 一条没改过的草稿就能污染整个类目
  assert.match(page, /const picksTouchedRef = useRef\(false\);/);
  assert.match(page, /if \(picksTouchedRef\.current\) \{\s*\n\s*saveAttrPicksHistory\(scope, leafCatId, attrPicks\);/);
  assert.match(page, /const handleAttributePicksChange = \(next: Record<number, AttrPick>\) => \{\s*\n\s*picksTouchedRef\.current = true;/);
  // 刷新恢复照样写（任何来源都写），两者分工明确
  assert.match(page, /saveCurrentAttrPicks\(scope, leafCatId, attrPicks\);/);
  // 按写入顺序裁（slice(-N)）：同一毫秒连写多个类目时 at 会撞，按 at 排序会丢掉刚写的
  assert.match(attrStore, /\.slice\(-ATTR_PICKS_HISTORY_MAX\)/);

  // ② 刷新后类目恢复出来但属性表没拉过：补拉一次，顺带按该类目预设检查变种行
  const restoreEffect = page.slice(
    page.indexOf("const restoredAttrsLoadedRef = useRef(false);"),
    // 切到「刷新类目树」之前，否则会把后面的 setSpecMismatch 也算进来，断言变空气
    page.indexOf("/** 手动刷新类目树"),
  );
  assert.ok(restoreEffect.length > 0, "expected restored attributes effect");
  assert.match(restoreEffect, /temuPublishApi\s*\n?\s*\.attributes\(currentLeaf\.catId\)/);
  assert.match(restoreEffect, /setSpecMismatch\(findSpecMismatches\(attrs, rows\)\);/);

  // ③ 一键按最接近的预设值修正（对不上的保持原样）
  assert.match(page, /const fixSpecMismatches = \(\) => \{/);
  assert.match(page, /const colorFixed = badColor \? matchPresetValue\(row\.colorName, colorNames\) : null;/);
  assert.match(page, /setSpecMismatch\(findSpecMismatches\(attributes, next\)\);/);
  assert.match(page, /按最接近的预设值修正/);

  // ④ 必填属性校验必须排在图片上传之前（上传要打图床，慢且占配额）
  const compose = page.slice(
    page.indexOf("const composeDraft = async (): Promise<TemuGoodsDraft> =>"),
    page.indexOf("const attrByPid = new Map("),
  );
  const attrCheckAt = compose.indexOf("缺少必填属性");
  const uploadAt = compose.indexOf("carousel = await resolveImages(");
  assert.ok(attrCheckAt > -1, "expected required-attribute check inside composeDraft");
  assert.ok(uploadAt > -1, "expected image upload inside composeDraft");
  assert.ok(attrCheckAt < uploadAt, "必填属性校验必须在图片上传之前，否则白传一遍图才报错");
});

test("批量发布弹窗不能被长标题顶宽（右侧被切掉）", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 弹窗有硬上限：内容里的 nowrap 长标题不能把它撑出视口
  assert.match(css, /\.temu-edit-dialog \{[\s\S]*?max-width: calc\(100vw - 48px\);/);

  // 列表项是 flex，标题是 nowrap：没有 min-width:0 时 flex 子项按内容宽度撑开，
  // ellipsis 根本不生效，整个弹窗跟着变宽 —— 就是「只显示一半」的根因。
  const listItem = css.slice(
    css.indexOf(".temu-batch-list li span {"),
    css.indexOf(".temu-batch-list li em {"),
  );
  assert.ok(listItem.length > 0, "expected batch list item style");
  assert.match(listItem, /min-width: 0;/);

  // 中间任何一层漏掉 min-width:0，长标题就会从弹窗边框里溢出去（文字被切一半）
  assert.match(css, /\.temu-edit-dialog > \* \{\s*min-width: 0;/);
  assert.match(css, /\.temu-batch-body \{[\s\S]*?min-width: 0;/);
  assert.match(css, /\.temu-batch-list \{[\s\S]*?min-width: 0;/);
  assert.match(css, /\.temu-batch-list li \{[\s\S]*?min-width: 0;/);
  // 原生 select 按最长 option 撑宽度，也要允许收缩
  assert.match(css, /\.temu-batch-dialog \.temu-input \{[\s\S]*?min-width: 0;/);
  assert.match(listItem, /text-overflow: ellipsis;/);
  assert.match(listItem, /white-space: nowrap;/);
});

test("批量发布：没有产品模板也能现场配类目+全量属性，直接发", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const manual = readFileSync(
    new URL("../components/BatchManualConfig.tsx", import.meta.url),
    "utf8",
  );

  // 弹窗里挂上现场配置
  // 整批同叶子类目时，连估好的属性一起预填（用户只需核对 + 选运费仓库）
  assert.match(library, /<BatchManualConfig/);
  assert.match(library, /onChange=\{setManualTemplate\}/);
  assert.match(library, /initialPath=\{batchCommonCategory\}/);
  assert.match(library, /initialPicks=\{batchCommonPicks\}/);
  assert.match(library, /const batchCommonPicks = useMemo\(\(\) => \{/);
  // 整批草稿同一个叶子类目时预填，省一次搜索
  assert.match(library, /const batchCommonCategory = useMemo\(\(\) => \{/);
  // 产品模板和现场配置二选一：模板优先，没有就用现场配出来的临时模板
  assert.match(
    library,
    /const template = productTemplates\.find\(\(item\) => item\.name === productTpl\) \?\? manualTemplate;/,
  );
  // 原来「没有模板就没法发」的劝退文案必须改掉
  assert.doesNotMatch(library, /先去看「发布商品」页填好一款再点「存为产品模板」/);

  // 现场配置：搜类目 → 选一条 → 拉该类目属性 → 用共享属性表单
  assert.match(manual, /const search = async \(\) => \{/);
  assert.match(manual, /temuPublishApi\.matchCategory\(text, SITE_ID\)/);
  assert.match(manual, /const loaded = await temuPublishApi\.attributes\(leaf\.catId\);/);
  assert.match(manual, /setAttributes\(loaded\);/);
  assert.match(manual, /<TemuPropertyForm/);
  // 全量属性：必填没填完要提示（不是静默发）
  assert.match(manual, /missingRequiredNames\(visibleProperties\(attributes, picks\), picks\)/);
  // 定价之外的三件事也必须能在这里配齐，否则还是得回发布页
  assert.match(manual, /temuPublishApi\.freightTemplates\(\[SITE_ID\]\)/);
  assert.match(manual, /temuPublishApi\.warehouses\(\[SITE_ID\]\)/);
  assert.match(manual, /temuPublishApi\.parentSpecs\(\)/);
  // 用户在批量配置里改的属性要写「同类目历史」：批量发布是主场景，
  // 他在这儿把 8 项改对，下次同类目估填就该按这版来
  assert.match(manual, /const handlePicksChange = \(next: AttrPickMap\) => \{/);
  assert.match(manual, /saveAttrPicksHistory\(storageScope\(\), catIdRef\.current, next\);/);
  assert.match(manual, /onChange=\{handlePicksChange\}/);
  // 配不齐就回传 null，别让父组件以为能发
  // 配置齐了才回传模板：类目 + 运费 + 仓库 + **必填属性填完**。
  // 少最后一条，父组件的「开始发布」会在必填没填时也亮，整批注定被拒。
  assert.match(manual, /const configReady =\n?\s*path\.length > 0 && !!freightId && !!warehouseId && !!attributes && missing\.length === 0;/);
  assert.match(manual, /if \(!configReady\) \{\s*\n\s*onChange\(null\);/);
});

test("入草稿库时按标题自动匹配 Temu 类目（标 auto、不覆盖人工选的）", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 限流：一次最多 24 条、并发 3，别把网关打爆
  assert.match(library, /const AUTO_LIMIT = 24;/);
  assert.match(library, /const AUTO_CONCURRENCY = 3;/);

  // 只补「还没有类目记忆」的草稿 —— 人工选过的绝不能被自动匹配覆盖
  assert.match(library, /\.filter\(\(entry\) => !categoryMemo\[entry\.id\]\?\.label\)/);
  // 标题太短的没意义，跳过
  assert.match(library, /\.filter\(\(entry\) => \(entry\.title \|\| entry\.subtitle \|\| ""\)\.trim\(\)\.length >= 8\)/);
  // 用标题派生的搜索词查类目，只认叶子路径
  assert.match(library, /const text = categorySearchTexts\(entry\.title \|\| entry\.subtitle\)\[0\];/);
  assert.match(library, /\.find\(\(path\) => path\.length > 0 && path\[path\.length - 1\]\.isLeaf\)/);
  // 写入时打 auto 标记，UI 才知道要标「自动」
  assert.match(library, /localStore\.rememberDraftCategory\(entry\.id, leafPath, \{ auto: true \}\);/);
  // 匹配失败不记空值：否则下次进来永远不会重试
  assert.doesNotMatch(library, /rememberDraftCategory\(entry\.id, \[\]/);
  // categoryMemo 不能进依赖：写入 → effect 重跑 → 死循环
  assert.match(library, /\}, \[entries\]\);/);
});

test("产品属性自动填：同类目沿用上次填法 + 按标题匹配，标注来源且可撤销", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  const autofill = readFileSync(new URL("../data/attrAutofill.ts", import.meta.url), "utf8");

  // ① 属性表一加载就跑一次；同一类目只跑一次，否则用户清空后会被偷偷填回来
  assert.match(page, /const runAttributeAutofill = \(basePicks: Record<number, AttrPick>, leafCatId: number\) => \{/);
  const effect = page.slice(
    page.indexOf("  useEffect(() => {\n    const leafCatId = Number(categoryPath[categoryPath.length - 1]?.catId ?? 0);"),
    page.indexOf("/** 按当前标题重新识别"),
  );
  assert.ok(effect.length > 0, "expected autofill effect");
  assert.match(effect, /if \(autoFilledCatRef\.current === leafCatId\) return;/);
  // 草稿库带进来的估填值先铺上（只铺空位），再把合并后的 base 交给自动填
  assert.match(effect, /const carried = carriedAttrPicksRef\.current;/);
  // 带进来的属性必须跟当前叶子类目对得上才铺：不同类目 pid 完全不同，
  // 铺错等于给 Temu 塞一堆不存在的属性。
  // 对上类目还不够：草稿存的是「估填那一刻」的 vid，Temu 调过属性表之后就查不到了 ——
  // 原样铺进去会变成「下拉框空白却算已填」，本地校验放行、提交被拒
  // 「请求的内容不存在或已被删除」，所以必须再按当前属性表 prune 一遍。
  assert.match(effect, /if \(carried && carried\.catId && carried\.catId === leafCatId\) \{/);
  assert.match(effect, /const pruned = pruneStaleAttrPicks\(carried\.picks, attributes\);/);
  assert.match(effect, /base = \{ \.\.\.pruned\.picks, \.\.\.attrPicksRef\.current \};/);
  assert.match(effect, /setStaleAttrNames\(pruned\.dropped\);/);
  assert.match(effect, /runAttributeAutofill\(base, leafCatId\);/);
  // 清空自动填时，「草稿库估了 N 项」这条提示也要一起撤
  assert.match(page, /setCarriedAttrCount\(0\);/);
  // 依赖只跟 attributes：标题变了不能自动重跑（会把用户清空的值填回来）
  assert.match(effect, /\}, \[attributes\]\);/);

  // ② 命中才写回 state；没命中就别无谓触发重渲染 + 落盘
  assert.match(page, /if \(result\.hits\.length\) setAttrPicks\(result\.picks\);/);
  // ③ 历史（同类目上次的填写）要喂进去
  assert.match(page, /history: readAttrPicksHistory\(storageScope\(\), leafCatId\),/);
  // ③ 文本来源：标题/英文标题/描述/类目名
  assert.match(page, /texts: autoFillSourceTexts\(\{/);
  assert.match(page, /title: form\.title,/);
  assert.match(page, /description: form\.description,/);

  // ④ 撤销与重跑都只动「仍然等于自动填的那个值」，手改过的保留
  assert.match(page, /const reRunAttributeAutofill = \(\) => \{/);
  assert.match(page, /if \(pick && Number\(pick\.vid\) === hit\.vid\) delete base\[hit\.pid\];/);
  assert.match(page, /const clearAttributeAutofill = \(\) => \{/);
  assert.match(page, /if \(pick && Number\(pick\.vid\) === hit\.vid\) delete next\[hit\.pid\];/);

  // ⑤ 提示条写明来源与需要人工确认的项
  assert.match(page, /className="temu-autofill-bar"/);
  // 两个来源分开说：草稿库已经估好的、进页面后补填的
  assert.match(page, /草稿库已按类目估了 <strong>\{carriedAttrCount\}<\/strong> 项属性/);
  // 失效项必须当场说出来并点名，不能等提交被 Temu 拒了才知道
  assert.match(page, /staleAttrNames\.length > 0/);
  assert.match(page, /项草稿属性/);
  assert.match(page, /重新选/);
  assert.match(page, /本次补填 <strong>\{autoFill\.hits\.length\}<\/strong> 项/);
  assert.match(page, /describeAutoFill\(autoFill\.hits\)/);
  assert.match(page, /命中多个值，需要你自己选/);
  assert.match(page, /按标题重新识别/);
  assert.match(page, /清空自动填/);
  assert.match(css, /\.temu-autofill-bar \{/);

  // ⑥ 核心规则在纯函数里（行为测试见 data/attrAutofill.test.ts）
  assert.match(autofill, /if \(item\.isSale\) continue;/);
  assert.match(autofill, /if \(!values\.length\) continue;/);
  assert.match(autofill, /if \(matched\.length === 1\) \{/);
  assert.match(autofill, /else if \(matched\.length > 1\) \{/);
  assert.match(autofill, /const PLACEHOLDER_VALUE = /);
});

test("草稿库不能标「可发」：产品属性草稿库不存，只能在发布页填", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 属性是 Temu 必填项，而草稿库根本没有属性数据（要选完叶子类目才知道要填哪些），
  // 所以任何草稿都不可能「可直接发布」。原来那个「可发」是纯误导。
  assert.doesNotMatch(library, /is-ok">可发</);
  assert.match(library, /<span className="temu-ready is-ok">字段齐<\/span>/);
  assert.match(library, /<span className="temu-ready is-pending">属性待填<\/span>/);
  // 没估上时要说明「为什么」：没类目就去选一次，正在估就等一下
  assert.match(library, /还没有 Temu 类目：点「用于发布」选一次，之后会自动按类目估属性/);
  assert.match(library, /属性正在按类目估（同类目只请求一次），稍等一下或刷新/);
  // 估上了要说清「其中几项是猜的」，否则「已估 8」会被读成「都填对了」
  assert.match(library, /其中 \$\{guessedInPrefill\} 项是凭类目猜的/);

  // 表头也别再说「完整度」——那个词会被读成「填完了就能发」
  assert.doesNotMatch(library, />完整度</);
  assert.match(library, /草稿字段\s*<\/th>/);

  // 有缺项时的 tooltip 同样要提醒属性也要填
  assert.match(library, /另外产品属性也要在发布页填/);

  // 勾了多条但类目不一致 → 必须说清原因，否则用户只看到类目是空的、以为坏了
  assert.match(library, /const batchCategoryConflict = useMemo\(\(\) => \{/);
  assert.match(library, /有 \$\{missing\} 条还没匹配到 Temu 类目，整批没法共用一套属性/);
  assert.match(library, /勾选的草稿不在同一个叶子类目/);
  assert.match(library, /\{batchCategoryConflict && \(/);
  // 只要有一条没类目就不预填：整批套一个类目会把那条塞错
  assert.match(library, /if \(paths\.some\(\(path\) => !path\?\.length\)\) return \[\];/);

  // 判定函数本身不掺属性：草稿库拿不到属性数据，掺进去只会让每条都「缺」
  const fn = library.slice(
    library.indexOf("function draftMissingFields("),
    library.indexOf("/** 尺寸/重量摘要"),
  );
  assert.doesNotMatch(fn, /属性/);
});

test("入草稿库就把产品属性估好，带入发布页直接用", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // ① 只有拿到叶子类目才估：属性是按类目定义的，没有 catId 无从谈起
  assert.match(library, /const ATTR_ESTIMATE_LIMIT = 60;/);
  assert.match(library, /if \(!leaf\) return false;/);
  // 类目变了要重估；同类目「历史」变新了也要重估 —— 否则用户在发布页把属性改对了，
  // 其他草稿还留着上次猜出来的值，用户会以为改一次全都跟着变
  assert.match(library, /if \(!saved \|\| Number\(saved\.catId\) !== leaf\) return true;/);
  assert.match(library, /return Number\(saved\.historyAt \?\? 0\) !== readAttrPicksHistoryAt\(scope, leaf\);/);
  assert.match(library, /historyAt: readAttrPicksHistoryAt\(scope, leaf\),/);

  // ② attrs 按 catId 缓存在内存：同类目 N 条草稿只打一次网关
  assert.match(library, /const attrEstimateCacheRef = useRef\(new Map<number, TemuAttributes>\(\)\);/);
  // 拉不到不写结果（写了空值等于「这条永远不再试」）、也不缓存失败结果，
  // 但要记进 failedCat，否则「一直失败 + 一直重跑」会把网关打爆
  assert.match(library, /拉不到就不写结果/);
  assert.match(library, /attrEstimateFailedRef\.current\.add\(leaf\);/);
  assert.match(library, /if \(attrEstimateFailedRef\.current\.has\(leaf\)\) return false;/);
  // 缓存只写在 try 里（成功才缓存）；catch 里只记 failedCat，不能顺手把失败的也缓存了
  assert.match(library, /try \{\s*attrs = await temuPublishApi\.attributes\(leaf\);\s*attrEstimateCacheRef\.current\.set\(leaf, attrs\);/);
  assert.match(library, /\} catch \{[\s\S]{0,200}?attrEstimateFailedRef\.current\.add\(leaf\);[\s\S]{0,80}?continue;/);
  assert.doesNotMatch(library, /next\[entry\.id\] = \{ catId: leaf, picks: \{\}/);

  // 跑完一轮再触发一轮：自动分类是分批补类目的，错过就永远估不上
  assert.match(library, /setEstimateTick\(\(value\) => value \+ 1\);/);
  assert.match(library, /\}, \[entries, categoryMemo, estimateTick\]\);/);
  // 显示时校验 catId：类目换了但还没重估，不能拿旧类目的数字充数
  assert.match(library, /if \(leafCatId && Number\(prefill\.catId\) !== Number\(leafCatId\)\) return 0;/);

  // ③ 估填本身用的是纯函数，来源可解释
  assert.match(library, /const result = estimateAttributePicks\(\{/);
  assert.match(library, /history: readAttrPicksHistory\(scope, leaf\),/);
  assert.match(library, /saveDraftAttrPrefills\(scope, next\);/);
  // attrPrefills 不能进依赖：写入 → 重跑 → 死循环
  assert.match(library, /\}, \[entries, categoryMemo, estimateTick\]\);/);

  // ④ 带入发布页 + 界面标出「估的」
  assert.match(library, /attrPicks: attrPrefills\?\.\[entry\.id\]\?\.picks,/);
  assert.match(library, /产品属性（自动估）/);
  assert.match(library, /属性已估 \{estimated\}/);
  // 「猜的」要标到具体属性上：8 项都填了但没说哪些是猜的，用户会以为全对
  assert.match(library, /guessedPids: result\.items/);
  assert.match(library, /attrGuessedPids: attrPrefills\?\.\[entry\.id\]\?\.guessedPids,/);
  assert.match(page, /setGuessedPids\(carried\.guessedPids\);/);
  assert.match(page, /guessedPids=\{guessedPids\}/);
});

test("类目属性被 Temu 改过（6000002）：自动重拉属性表，并点名到底变了什么", () => {
  // 实测 2026-11-03：桌布类目 10498 提交时回
  // 「Temu 拒绝了这次提交：货品类目属性更新，请刷新页面后重新创建商品（错误码 6000002）」。
  // 本地属性表是 1 小时缓存，过时后组出来的 pid/vid 全是旧的 —— 用户照旧表反复补，
  // 补几次都还是同一个错。
  //
  // 只提示「请重新选一遍」是句废话：他打开属性区一看跟原来一样，只能盲选再被拒一次。
  // 必须把「新增了哪个必填项 / 哪个选择失效了」点名出来。
  const publish = page.slice(
    page.indexOf("const confirmPublish = async () => {"),
    page.indexOf("const copyPayload = async () => {"),
  );
  assert.ok(publish.length > 0, "expected confirmPublish block");
  assert.match(publish, /if \(\/6000002\|类目属性更新\/\.test\(text\)\) \{/);
  assert.match(publish, /const diff = await reloadCategoryAttributes\(attributes\)\.catch\(/);
  assert.match(publish, /const detail = diff \? attributeDiffText\(diff\) : "";/);
  // 有差异 → 点名；没差异 → 直接说「重选也没用」，别让用户白试第三次
  assert.match(publish, /Temu 后台改过这个类目的属性定义：\$\{detail\}/);
  // 没差异 → 说明属性表没变，真凶在规格（无销售属性类目补的假规格）。
  // 提示必须把这条指出来，不能再让用户「等 1-2 分钟再刷」白等。
  assert.match(publish, /属性定义没有变化/);
  assert.match(publish, /本来就没有销售属性/);
  // 别再对着终端用户说「把程序更新到最新版」——他自己就是最新版，这句等于推卸
  assert.doesNotMatch(publish, /把程序和本地后端都更新到最新版/);
  assert.match(publish, /把类目 id 发给开发排查/);
  // diff 本身的算法在 attributeModel.test.ts 里做行为测试
  assert.match(attrModel, /export function describeAttributeDiff\(/);
  assert.match(attrModel, /export function attributeDiffText\(/);
});

test("变种规格值要把 Temu 的预设值摆出来，而不是让用户猜", () => {
  // 实测反馈：「我怎么知道 Temu 要什么？」—— 用户手填「苹果4」这种值，
  // 提交才被回「[4] 不是尺码表的合法值」。只给 datalist 不够：
  // 不点开输入框根本看不到有哪些可选值。
  // 2026-11-06 二次反馈：「真的只能是预设的吗，那我怎么知道预设有什么？」
  // 只露前 10 个不够（Temu 颜色预设上百个），改成「浏览全部 N 个」+ 搜索面板。
  const panel = readFileSync(
    new URL("../components/PublishSkuPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(panel, /浏览全部\$\{label\}预设/);
  assert.match(panel, /className="temu-spec-panel"/);
  assert.match(panel, /搜索\$\{label\}预设/);
  assert.match(panel, /className=\{`temu-spec-chip/);
  // 手填非法值要当场标红，不能等提交；并且给出「最像的预设值」一键替换
  assert.match(panel, /不是合法的\{colorSpecName\}值（Temu 只认类目预设）/);
  assert.match(panel, /不是合法的\{sizeSpecName\}值（Temu 只认类目预设）/);
  assert.match(panel, /const suggestSpecValues = /);
  assert.match(panel, /replaceSpecValue\(colorsText, value, name\)/);
  // 手填非法值在 chips 编辑器上标红（容器 + 单个 chip）
  assert.match(panel, /temu-spec-chips\$\{bad\.length \? " is-bad" : ""\}/);
  assert.match(panel, /temu-spec-chip-item\$\{badLower\.has\(value\.toLowerCase\(\)\) \? " is-bad" : ""\}/);
  // 两个输入框都要能下拉搜
  assert.match(panel, /list="temu-color-options"/);
  assert.match(panel, /list="temu-size-options"/);
  // 类目预设还没读到时不能断言「没有预设」，否则用户会以为可以自由填
  assert.match(panel, /正在读取类目预设/);
  assert.match(panel, /这个类目没有\$\{label\}预设值/);
  // 样式在位
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  assert.match(css, /\.temu-spec-presets \{/);
  assert.match(css, /\.temu-spec-panel \{/);
  assert.match(css, /\.temu-spec-chip \{/);
  assert.match(css, /\.temu-spec-bad \{/);
});

test("变种两列不能写死「颜色/尺码」：任意类目都要自适应", () => {
  // 手机壳是「颜色 + 型号」、收纳盒是「颜色 + 容量」、毛巾只有「颜色」、
  // 桌布干脆一个销售属性都没有。写死中文名会让标签、校验、payload 全错位。
  // 选择逻辑已抽成纯函数（data/specPair.ts），可被穷举验证
  const specPair = readFileSync(new URL("../data/specPair.ts", import.meta.url), "utf8");
  assert.match(specPair, /export const FIRST_SPEC_WORDS = \[/);
  assert.match(specPair, /export const SECOND_SPEC_WORDS = \[/);
  assert.match(specPair, /export function pickSpecPair\(specs: TemuParentSpec\[\]\)/);
  // 两列绝不能落到同一个属性上（类目返回顺序不保证）
  assert.match(specPair, /item\.parentSpecId !== color/);
  // 页面只负责调用，不再自己实现一份
  assert.match(page, /import \{ pickSpecPair \} from "\.\.\/data\/specPair";/);
  // 只有一列销售属性时，第二列不存在 → 不能硬要尺码，否则抛「父规格列表为空」
  assert.match(page, /if \(sizeParent\) \{/);
  // 界面文案用 Temu 返回的真实属性名
  assert.match(page, /const colorSpecName =/);
  assert.match(page, /const sizeSpecName =/);
  assert.match(page, /colorSpecName=\{colorSpecName\}/);
  assert.match(page, /sizeSpecName=\{sizeSpecName\}/);

  const panel = readFileSync(
    new URL("../components/PublishSkuPanel.tsx", import.meta.url),
    "utf8",
  );
  assert.match(panel, /变种属性一：\{colorSpecName\}/);
  assert.match(panel, /变种属性二：\{sizeParentId > 0 \? sizeSpecName : "不使用"\}/);
  // 单销售属性类目直接不渲染第二列（有第二维时还有「不使用」出口，见对应测试）
  assert.match(panel, /\{parentSpecs\.length > 1 && \(/);
});

test("底部粘性操作条已去掉：只在右侧检查栏留校验/提交", () => {
  // 之前加过一条粘在视口底部的操作条（"必填项都齐了，可以提交" + 两个按钮），
  // 反馈是太吵：右侧已经有一模一样的按钮，底部那条还压住表单最后一行。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  assert.ok(!page.includes("temu-actionbar"), "页面里不要再渲染底部操作条");
  assert.ok(!css.includes(".temu-actionbar"), "样式里也不要留残留");
  // 右栏那两个动作必须还在，别连功能一起删掉
  assert.match(page, /checkMandatory/);
  assert.match(page, /publishGoods/);
  // 说明文案不能再说「页面底部的提交发布」
  assert.ok(!page.includes("页面底部的「提交发布」"));
});

test("顶部店铺名后面要有「绑定/切换店铺」入口", () => {
  // 店铺名 pill 只展示当前店（Little Pony Shop · 剩 83 天）。
  // 换店以前只能点 pill 进凭证面板翻列表，多一步；绑新店也是。
  assert.match(page, /className="temu-shop-switch"/);
  // 只在已绑定时出现：没绑时左边 pill 就是「绑定我的店铺」入口，别搞两个入口
  assert.match(page, /\{ready && \(\s*<div className="temu-shop-switch">/);
  assert.match(page, /切换店铺\n/);
  // 菜单里列出已保存店铺，当前那家点不动，其它家走 switchShop（只切 token，不用重输）
  assert.match(page, /\(status\?\.shops \?\? \[\]\)\.map\(\(shop\) => \{/);
  assert.match(page, /const current = shop\.id === status\?\.active_shop_id;/);
  assert.match(page, /if \(!current\) void switchShop\(shop\.id\);/);
  // 兜底入口：绑新店
  assert.match(page, /＋ 绑定新店铺（粘贴 access_token）/);
  // 点空白处能关掉
  assert.match(page, /className="temu-shop-switch-backdrop"/);

  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 浮层必须用实体卡片底色：peach/alpine 的 card-bg 是透明的，裸用会被风景洗掉
  assert.match(css, /\.temu-shop-switch-panel \{[\s\S]*?background: var\(--theme-card-bg-solid/);
  assert.match(css, /\.temu-shop-switch-item\.is-current \{/);
});

test("首屏只留必填：低频字段都收进「更多选项」折叠", () => {
  // 反馈是「字段太多，不知道填哪些」。原则：首屏只露必填项，
  // 其余（选填/开关/模板/多件装）默认收起 —— 但必须**还渲染着**，
  // 只是折叠（details 内容仍在 DOM 里），否则表单值会丢。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  // 装饰性只读字段删掉（店名顶部 pill 已有，站点只有美国一个选项）
  // 用带标签的完整片段判断：注释里提到这两个词是允许的，字段本身不能还在
  assert.ok(!page.includes("<span>店铺账号</span>"), "只读的「店铺账号」不该占一行");
  assert.ok(!page.includes("<span>经营站点</span>"), "禁用的「经营站点」下拉不该占一行");
  // 「检查必填」只留两处：发布区块一处、右侧检查栏一处。顶栏那份重复的删掉了
  const mandatoryButtons =
    page.match(/onClick=\{(?:\(\) => void )?checkMandatory(?:\(\))?\}/g) ?? [];
  assert.equal(mandatoryButtons.length, 2, "顶栏不该再有第三个「检查必填」");

  // 四处折叠必须在，且内部字段仍被渲染
  for (const [summary, keep] of [
    ["更多选项（英文标题、货号、产地、外链）", "站外产品链接"],
    ["载入 / 保存产品模板", "saveProductTemplate"],
    ["载入 / 保存变种模板", "saveVariantTemplate"],
    ["每件装数量（多件装才改，默认 1）", "piecesPerPack"],
  ] as const) {
    assert.ok(page.includes(summary), `缺少折叠：${summary}`);
    assert.ok(page.includes(keep), `折叠里把字段弄丢了：${keep}`);
  }

  // 敏感货 / 定制产品是合规项：原来藏在「校验发布」的原始返回折叠里（几乎没人点开），
  // 填错直接被 Temu 拒或判违规，所以挪到产品信息段平铺成两张卡片
  assert.match(page, /className=\{`temu-flag\$/);
  assert.ok(!page.includes("敏感货、定制）"), "折叠标题里不该再提敏感货/定制，它们已经平铺");
});

test("草稿库带了真规格就自动按多规格发，不用用户再勾", () => {
  // 开关默认已勾上（2026-11-05 改）；但如果用户手动取消过、而草稿里明明带了「颜色=白」，
  // 还按单 SKU 发，颜色就被吞了 —— 用户看到填了、发出去却没有，这才是卡点。
  assert.match(page, /function hasRealSpecValue\(prefill: TemuPublishPrefill\): boolean \{/);
  // 「默认」是单 SKU 的占位符，不能因为它把开关打开
  assert.match(page, /return !!text && text !== "默认";/);
  // 在 applyPrefillMeta 里判断 —— 草稿入口（挂载态与事件态）都会经过它
  const meta = page.slice(
    page.indexOf("function applyPrefillMeta(prefill: TemuPublishPrefill)"),
    page.indexOf("function hasRealSpecValue"),
  );
  assert.ok(meta.length > 0, "expected applyPrefillMeta block");
  assert.match(meta, /if \(hasRealSpecValue\(prefill\)\) setVariantsOn\(true\);/);
});

test("库存默认 0（先按售罄建品，开卖前补）", () => {
  // 用户要求：新建行库存默认 0，不用每次手填。
  // 0 是 Temu 接受的合法值，商品会落到「售罄/不可售」；后端提交后会带一条提醒，
  // 所以不会出现「悄悄发了 0 库存还不知道」的情况。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const batch = panel.slice(
    panel.indexOf("export const EMPTY_BATCH"),
    panel.indexOf("let rowSeed"),
  );
  assert.match(batch, /stock: "0",/, "新建行的库存默认应为 0");
  // 批量填充输入框的占位提示也要跟着改，否则提示和实际默认值互相打脸
  assert.match(panel, /patchBatch\(\{ stock: e\.target\.value \}\)\} placeholder="0"/);
  // 0 依然是合法值：校验只拦空值/负数
  assert.match(page, /if \(!text \|\| !Number\.isFinite\(number\) \|\| number < 0\)/);
});

test("发布历史：成功与失败分开，成功过的草稿从草稿库消失", () => {
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const apiTs = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");
  const modules = readFileSync(
    new URL("../../../app/navigation/modules.ts", import.meta.url),
    "utf8",
  );
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 两个 tab，各自带数量
  assert.match(history, /const \[tab, setTab\] = useState<Tab>\("success"\)/);
  assert.match(history, /成功 <em>\{succeeded\}<\/em>/);
  assert.match(history, /失败 <em>\{failed\}<\/em>/);
  // 成功显示商品 id，失败展开看原因（Temu 原话 + 错误码）
  assert.match(history, /商品 id <b>\{item\.goods_id \|\| "—"\}<\/b>/);
  assert.match(history, /失败原因|temu-history-reason/);
  assert.match(history, /去成品库重发/);
  // 走的是后端台账，不是本机 localStorage
  assert.match(apiTs, /publish-history\?/);
  assert.match(history, /temuPublishApi\.publishHistory\(/);

  // 导航：草稿库 → 发布商品 → 发布历史
  assert.match(modules, /id: "temu_publish_history"/);
  assert.match(modules, /children: \[temuDraftLibrary, temuPublish, temuPublishHistory\]/);

  // 草稿库里那份「发布失败历史」抽屉已经搬走（避免两份失败列表）
  assert.ok(!library.includes("发布失败历史"), "草稿库不该再有第二条失败列表");
});

test("发布历史列表要「看得懂」：缩略图、类目名、价格、店铺、来源、搜索", () => {
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const apiTs = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");

  // 一屏能认出是哪个货：主图 + 类目路径名（没有名字才退回 cat id）
  assert.match(history, /<HistoryThumb path=\{item\.image\}/);
  assert.match(history, /item\.cat_name/);
  assert.match(history, /类目 \{item\.leaf_cat_id\}/);
  // 价格区间、店铺、来源（AI 处理 / POD 定制 / 手动填写）
  assert.match(history, /formatPriceRange\(item\.price_min, item\.price_max\)/);
  assert.match(history, /item\.shop_name/);
  assert.match(history, /sourceLabelOf\(item\.draft_id\)/);
  // 搜索（搜标题/商品 id/类目名），要防抖并带进请求
  // 2026-11-10 用户要求删掉页头的「成功/失败/成功率」统计卡（页签上已有计数，重复）
  assert.doesNotMatch(history, /成功率/);
  assert.doesNotMatch(history, /temu-history-stats/);
  assert.match(history, /搜标题、商品 id、类目名/);
  assert.match(history, /q: query/);
  assert.match(apiTs, /q: params\.q \?\? ""/);
  // 成功行能一键复制商品 id；失败行给出错误码徽章与复制原因
  assert.match(history, /复制 id/);
  assert.match(history, /错误码 \{code\}/);
  assert.match(history, /复制原因/);
});

test("老记录缺图时自动补一次（按商品 id 去店铺列表认）", () => {
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const apiTs = readFileSync(new URL("../api/temuPublishApi.ts", import.meta.url), "utf8");

  // 加 image/cat_name 列之前发布的记录打开就是一片「无图」：
  // 第一次发现有「有商品 id 但没图」的成功记录时，让后端补一次并刷新。
  assert.match(history, /backfillTried = useRef\(false\)/);
  assert.match(history, /item\.status === "success" && item\.goods_id && !item\.image/);
  assert.match(history, /temuPublishApi\s*\.backfillHistory\(\)/);
  assert.match(history, /if \(result\?\.updated\) void load\(/);
  assert.match(apiTs, /backfillHistory: \(\) =>/);
  assert.match(apiTs, /publish-history\/backfill/);
});

test("草稿库改版：批量入口收敛成一个，选中后直接出快捷动作", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const bar = readFileSync(new URL("../components/TemuDraftBulkBar.tsx", import.meta.url), "utf8");

  // 头部换成轻量标题 + 统计卡（统计卡本身就是来源筛选，点了会切 filter）
  assert.match(library, /<header className="temu-library-head">/);
  assert.match(library, /className=\{`temu-library-stat\$/);
  assert.match(library, /setFilter\(key\);/);
  // 发布历史和创建产品都在头部：发过的商品已经不在草稿库里，得留个入口回看
  assert.match(library, /onOpenHistory/);
  assert.match(library, /创建产品\s*<\/button>/);

  // 三个并列下拉（批量操作 / 全属性修改 / 移动分类）合成一个入口：
  // 它们以前互相嵌套，用户不知道该点哪个
  assert.doesNotMatch(bar, /temu-bulk-menu-wrap/);
  assert.match(bar, /view === "actions"/);
  assert.match(bar, /view === "fields"/);
  assert.match(bar, /view === "category"/);

  // 选中后直接一排动作，常用操作不用再进菜单翻
  assert.match(bar, /has-selection/);
  assert.match(bar, /批量发布/);
  assert.match(bar, /改字段/);
  assert.match(bar, /移分类/);
  assert.match(bar, /取消选择/);
  // 没勾选时面板要解释为什么按钮是灰的
  assert.match(bar, /先勾选左侧复选框，下面的批量项才会亮/);
});

test("生成变种是「补齐组合」，不是重建表格", () => {
  // 用户把「黑/S」的价格改成 199，随后只是加一个尺码再点了一次「生成」，
  // 整表重建会把 20 行的价全冲回默认值 —— 而且没有任何提示。
  // 合并规则抽到 data/variantMerge.ts（有穷举单测），组件只能调它。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /import \{ mergeGeneratedRows, variantCoverage \} from "\.\.\/data\/variantMerge";/);
  assert.match(panel, /mergeGeneratedRows\(rows, colors, sizes, \(color, size, index\) =>/);
  // 已经填过的行原样保留，按钮上要写清楚
  assert.match(panel, /已经填过的行原样保留/);

  // 批量编辑说的「覆盖」只针对填了值的字段，别让人以为生成变种会覆盖
  assert.match(panel, /只覆盖你填了值的项/);
  // 输入框 label 是「元」，文案不能再说「分」，否则用户会填成 1299（= ¥1299）
  assert.doesNotMatch(panel, /价格单位分/);
});

test("草稿库的备注/删除用应用内弹窗，不用 window.prompt / confirm", () => {
  // 原生弹窗样式跟工作台不搭；更麻烦的是 WebView 宿主可以禁用它，
  // 禁掉之后 prompt 返回 null、confirm 返回 false —— 功能静默失效，用户以为点坏了。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(library, /window\.prompt\(/);
  assert.doesNotMatch(library, /window\.confirm\(/);
  assert.match(library, /aria-label="批量备注"/);
  assert.match(library, /aria-label="批量删除"/);
  assert.match(library, /const \[remarkTargets, setRemarkTargets\] = useState<string\[\]>\(\[\]\);/);
  assert.match(library, /const confirmBulkDelete = \(\) => \{/);
});

test("发布历史的复制按钮失败时要有提示，不能静默", () => {
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  // 浏览器/WebView 拒绝写剪贴板时，点「复制 id」不能毫无反应
  assert.match(history, /这个环境不允许写剪贴板，请手动选中文本复制/);
  assert.match(history, /setCopyError\(/);
  assert.match(history, /temu-history-toast/);
});

test("草稿带入要按 SKU 铺满变种表，不用用户再手填颜色/尺码", () => {
  // 真实草稿的 source_variant_records 是「一个颜色一条」，每条有自己的价格/库存/图。
  // 以前只铺第一行，进发布页还得手动补其余颜色 —— 数据本来就有。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  assert.match(page, /function buildRowsFromPrefill\(prefill: TemuPublishPrefill, whId: string\): VariantRow\[\]/);
  assert.match(page, /const variants = \(prefill\.variants \?\? \[\]\)\.filter\(/);
  assert.match(page, /if \(variants\.length <= 1\) \{/);
  // 规格输入框要用全量取值（11 个颜色都要列出来）
  assert.match(page, /prefill\.specValues1\.join\(","\)/);
  assert.match(page, /prefill\.specValues2\.join\(","\)/);
  // 每行的价格/库存/图取自己那条，库存 0 不能被 || 兜掉
  assert.match(page, /typeof variant\.stock === "number" \? String\(variant\.stock\)/);
  assert.match(page, /typeof variant\.price === "number" && variant\.price > 0/);
  assert.match(page, /thumbUrl: variant\.image \|\| merged\.thumbUrl/);
  // 草稿库要把整列表传过去
  assert.match(library, /specValues1: entry\.spec_values_1 \?\? \[\]/);
  assert.match(library, /variants: entry\.variants \?\? \[\]/);
});

test("外链商品图不能带 Referer：alicdn 会 403，缩略图全是空白", () => {
  // 实测：https://cbu01.alicdn.com/... 不带 Referer 返回 200(145KB)，
  // 带上 http://127.0.0.1:5173/ 直接 403 —— 浏览器 <img> 默认会带 Referer，
  // 于是草稿库缩略图、变种预览图、发布历史缩略图全部空白。
  const strip = readFileSync(new URL("../components/TemuImageStrip.tsx", import.meta.url), "utf8");
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");

  for (const [name, source] of [
    ["图片条/变种缩略图", strip],
    ["草稿库缩略图", library],
    ["发布历史缩略图", history],
  ] as Array<[string, string]>) {
    assert.match(source, /referrerPolicy="no-referrer"/, `${name} 必须关掉 Referer`);
  }
  // 加载失败要落到「失效」，不能留一个裂图
  assert.match(strip, /onError=\{\(\) => setFailed\(true\)\}/);
  assert.match(history, /onError=\{\(\) => setFailed\(true\)\}/);
});

test("生成变种按钮：组合齐了就置灰，别让人对着一个点了没反应的按钮发呆", () => {
  // 草稿带入时已经按 SKU 铺满，按钮再亮着就是纯噪音（用户会问「这有什么用」）。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const merge = readFileSync(new URL("../data/variantMerge.ts", import.meta.url), "utf8");

  assert.match(panel, /const coverage = variantCoverage\(/);
  assert.match(panel, /disabled=\{coverage\.inSync\}/);
  assert.match(panel, /✓ 已生成 \$\{coverage\.expected\} 个变种/);
  assert.match(panel, /生成 \$\{coverage\.expected\} 个变种\$\{rows\.length \? `（当前 \$\{rows\.length\} 个）` : ""\}/);
  // 组合判断抽成纯函数（有单测）
  assert.match(merge, /export function variantCoverage\(/);
  assert.match(merge, /inSync: wanted\.size > 0 && missing === 0 && extra === 0/);
});

test("尺码取值只有一个值且各行尺码全空 → 直接写进表格（不用点生成）", () => {
  // 用户在「变种属性二」填「可定制尺寸」，表格里还是「输入尺码」占位符，
  // 检查清单报「N 行没填全」却看不出差在哪 —— 取值框和表格是两份数据，
  // 同步原来只能靠「生成变种」按钮，太隐蔽。单值全空时自动写入。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");

  assert.match(panel, /const handleSizesTextChange = \(value: string\) => \{/);
  assert.match(panel, /onSizesTextChange\(value\);/);
  assert.match(panel, /const single = splitSizes\(value\);/);
  assert.match(panel, /if \(single\.length === 1 && rows\.length && rows\.every\(\(row\) => !row\.sizeName\.trim\(\)\)\)/);
  assert.match(panel, /onRowsChange\(rows\.map\(\(row\) => \(\{ \.\.\.row, sizeName: single\[0\] \}\)\)\);/);
  // chips 加值和预设 chips 都走它（逗号输入框已废）
  assert.match(panel, /if \(kind === "color"\) onColorsTextChange\(next\);\n\s*else handleSizesTextChange\(next\);/);
  assert.match(panel, /renderSpecChoices\(sizeOptions, sizesText, handleSizesTextChange, sizeSpecName, "size"\)/);
});

test("变种属性值用 chips +「+」一个个加，不用逗号拼字符串", () => {
  // 以前是逗号拼一串文本（"咖色,粉色,白色,黑色"）：加值找逗号、拼错整串歪掉、
  // 删也删不干净。现在 chips 单个加/删（回车或 + 提交），底层仍存逗号串。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(panel, /const addSpecValues = \(kind: "color" \| "size", raw: string\) => \{/);
  assert.match(panel, /const removeSpecValue = \(kind: "color" \| "size", value: string\) => \{/);
  assert.match(panel, /const renderSpecEditor = \(kind: "color" \| "size", label: string\) => \{/);
  // 颜色/尺码两个入口都换成 chips 编辑器
  assert.match(panel, /\{renderSpecEditor\("color", colorSpecName\)\}/);
  assert.match(panel, /renderSpecEditor\("size", sizeSpecName\)/);
  // 回车或 + 添加；粘贴一串（"s,m,l"）拆开逐个加
  assert.match(panel, /if \(draft\.trim\(\)\) commit\(\);/);
  assert.match(panel, /className="temu-spec-add"/);
  assert.match(panel, /const incoming = splitSizes\(raw\);/);
  // + 绝不能是 disabled（用户反馈「+都点不了」：输入框空着时按钮灰着，
  // 一进页面就点不动）。空着点 + 要把焦点丢回输入框；
  assert.doesNotMatch(panel, /disabled=\{!draft\.trim\(\)\}/);
  assert.match(panel, /if \(draft\.trim\(\)\) addSpecValues\(kind, draft\);/);
  assert.match(panel, /chipInputRefs\.current\[kind\]\?\.focus\(\);/);
  // chips 的样式
  assert.match(css, /\.temu-spec-chips \{/);
  assert.match(css, /\.temu-spec-chip-item \{/);
  assert.match(css, /\.temu-spec-add \{/);
  // 值多时限高自己滚（11 个长颜色名会把整块撑到 400px 高、把旁边尺码栏压到底部），
  // 输入框和 + 在列表外面，不会被滚走
  assert.match(panel, /className="temu-spec-chip-list"/);
  assert.match(css, /\.temu-spec-chip-list \{[\s\S]{0,220}max-height: 78px;[\s\S]{0,80}overflow-y: auto;/);
  // 变种属性区三列顶部对齐（以前 align-items:end，chips 一高旁边的栏就被压到底）
  assert.match(css, /\.is-console \.temu-variant-spec \{[\s\S]{0,600}align-items: start;/);
});

test("尺寸与重量按尺码绑定，不跟着「应用到全部」一把梭", () => {
  // 不同尺码的长宽高/重量本来就不一样，批量套全部行等于逼人回头逐行改。
  // 现在按尺码分组、改了即生效；「应用到全部」只管价格/库存。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(panel, /const BATCH_APPLY_KEYS: Array<keyof VariantBatch> = \["supplierPrice", "suggestedPrice", "stock"\];/);
  assert.match(panel, /for \(const key of BATCH_APPLY_KEYS\) \{/);
  assert.match(panel, /BATCH_APPLY_KEYS\.filter\(\(key\) => \{/);
  assert.match(panel, /const sizeDimGroups = \(\(\) => \{/);
  assert.match(panel, /const patchSizeDim = \(/);
  // 即改即生效：直接落到同尺码的行上
  assert.match(panel, /onChange=\{\(e\) => patchSizeDim\(group\.sizeName, "lengthCm", e\.target\.value\)\}/);
  assert.match(panel, /onChange=\{\(e\) => patchSizeDim\(group\.sizeName, "weightG", e\.target\.value\)\}/);
  assert.match(css, /\.temu-size-dims \{/);
});

test("从成品库挑图：三个图片槽位都能直接挑，不用先存本地再上传", () => {
  // 成品图本来就在草稿库里（AI 处理 / POD 成品），以前发布页只能本地上传或手填 URL，
  // 想复用同一款的其他图就得先下载再传一遍 —— 店小秘那套「AI 商品套图」就是省这一步。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const picker = readFileSync(new URL("../components/DraftImagePicker.tsx", import.meta.url), "utf8");

  // 槽位里出现入口按钮，点开带上当前槽位
  assert.match(page, /从成品库挑图/);
  assert.match(page, /onClick=\{\(\) => setPickerSlot\(slot\.key\)\}/);
  assert.match(page, /<DraftImagePicker/);

  // 追加逻辑：去重 + 截到槽位上限，超了给一句话提示
  assert.match(page, /const appendSlotImages = \(slot: ImageSlot, urls: string\[\]\) => \{/);
  assert.match(page, /if \(text && !merged\.includes\(text\)\) merged\.push\(text\);/);
  assert.match(page, /merged\.slice\(0, limit\)\.join\("\\n"\)/);
  assert.match(page, /最多 \$\{limit\} 张，多出的 \$\{merged\.length - limit\} 张没有加进来/);

  // 弹窗从成品库读图，且在列表里的置灰、超出剩余张数的不可选
  assert.match(picker, /temuPublishApi\s*\.draftLibrary\(/);
  assert.match(picker, /const remaining = Math\.max\(0, limit - existing\.length\);/);
  assert.match(picker, /disabled=\{added \|\| \(!picked && selected\.length >= remaining\)\}/);
  assert.match(picker, /referrerPolicy="no-referrer"/);
});

test("变种预览图可以改：本地 / 网络 / 引用轮播，且能按颜色批量套", () => {
  // 用户问「能改变种信息的预览图吗」——之前 thumbUrl 只有草稿带入时才写，界面上没有入口。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  // 两个入口分开：点图=看大图（TemuThumb 自己是按钮），点右下角 ✎=换图菜单。
  // 之前整块图都是「开菜单」+ 同时又触发放大，两个浮层一起弹。
  assert.match(panel, /className="temu-thumb-trigger"/);
  assert.match(panel, /className="temu-thumb-edit"/);
  assert.match(panel, /aria-label="更换预览图"/);
  assert.doesNotMatch(panel, /<button[^>]*temu-thumb-trigger/);

  // 三个来源 + 恢复默认
  assert.match(panel, /本地图片/);
  assert.match(panel, /网络图片/);
  assert.match(panel, /引用产品轮播图/);
  assert.match(panel, /恢复默认（轮播首图）/);

  // 应用范围：单行 / 同颜色 / 全部
  assert.match(panel, /const applyThumb = \(key: string, url: string, scope: "row" \| "color" \| "all"\)/);
  assert.match(panel, /scope === "all" \|\| row\.colorName === target\.colorName/);
  assert.match(panel, /仅这一行/);
  assert.match(panel, /同颜色（/);
  assert.match(panel, /全部 \{rows\.length\} 行/);

  // 本地图转 dataURL，提交时 resolveImage 要认；否则会当成「本地文件路径」去读盘
  assert.match(panel, /reader\.readAsDataURL\(file\)/);
  assert.match(page, /text\.startsWith\("data:image\/"\)/);
  // 轮播列表要传进面板，才能「引用产品轮播图」
  assert.match(page, /carouselImages=\{splitLines\(form\.carousel\)\}/);
});

test("缩略图尺寸：变种行/草稿库/历史都要看得清，别被容器压成 34px", () => {
  // 实测反馈「图都太小了」：变种行的容器写死 34px，把 72px 的图压成小方块。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 变种行：第一列 72px + 容器 68px + 图 64px（三层都要够）
  assert.match(css, /grid-template-columns: 72px 96px 62px/);
  assert.match(css, /\.is-console \.temu-variant-thumb \{[\s\S]{0,80}width: 68px;/);
  assert.match(css, /\.is-console \.temu-variant-row \.temu-img-thumb \{[\s\S]{0,80}width: 64px;/);
  // 草稿库 76px 图 + 88px 列，历史 88px
  assert.match(css, /width: 76px;\n  height: 76px;/);
  assert.match(css, /\.temu-draft-table \.col-image \{[\s\S]{0,80}width: 88px;/);
  // 挑图弹窗网格放大
  assert.match(css, /repeat\(auto-fill, minmax\(132px, 1fr\)\)/);
});

test("变种图能点开放大，且容器不能把它压成小方块", () => {
  // 用户反馈：「这个的图片呢，而且还没办法放大」——
  // ① 空图占位在无固定宽度父级里只剩 2px 边框（看起来像一根竖线）；
  // ② `.is-console .temu-variant-thumb .temu-img-thumb` 写死 34px，比图本身小一半；
  // ③ onOpen 传的是空函数，点了没反应。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  assert.doesNotMatch(css, /\.is-console \.temu-variant-thumb \.temu-img-thumb \{[\s\S]{0,60}width: 34px/);
  assert.match(css, /\.is-console \.temu-variant-thumb \.temu-img-thumb \{[\s\S]{0,120}width: 64px/);
  // 空图占位要给固定 64px，否则宽高 100% 算成 0
  assert.match(css, /\.is-console \.temu-variant-row \.temu-img-empty,[\s\S]{0,200}width: 64px/);

  // 点图放大：面板透传回调，页面挂预览层
  assert.match(panel, /onPreviewImage\?: \(src: string, alt\?: string\) => void;/);
  assert.match(panel, /onOpen=\{\(src\) => onPreviewImage\?\.\(src,/);
  assert.match(page, /onPreviewImage=\{\(src, alt\) => setImagePreview\(\{ src, alt \}\)\}/);
  assert.match(page, /aria-label="图片预览"/);
});

test("步骤条要显眼：字号 ≥13px 且用主文字色（深色主题下就是白）", () => {
  // 用户反馈：五个区块名太小、还是灰的。深色主题（diamond）下灰字几乎看不见。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  const themes = readFileSync(new URL("../../../shared/styles/themes.css", import.meta.url), "utf8");

  assert.match(css, /\.temu-stepbar-item \{[\s\S]{0,400}font-size: 13\.5px;/);
  assert.match(css, /\.temu-stepbar-item \{[\s\S]{0,400}color: var\(--temu-text\);/);
  // --temu-text 必须映射到主题主文字色，不能是硬编码灰
  assert.match(css, /--temu-text: var\(--theme-text-primary/);
  // 黑白主题（diamond）的弱化文字要够亮，否则说明小字在深底上看不清
  assert.match(themes, /\[data-theme="diamond"\] \{[\s\S]{0,1200}--theme-text-muted: #d5dde6;/);
});

test("「可自定义规格」开关默认勾上：多数商品都有颜色/尺码", () => {
  // 默认不勾 → 用户填完颜色/尺码才发现没生效（生成按钮、变种表都不出现）。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  assert.match(page, /const \[variantsOn, setVariantsOn\] = useState\(true\);/);
  assert.doesNotMatch(page, /const \[variantsOn, setVariantsOn\] = useState\(false\);/);
  // 有预设销售属性的类目本来就走多规格，不受这个开关影响
  assert.match(page, /const singleSkuMode = hasPresetSaleProps \? false : !\(canCustomSpecs && variantsOn\);/);
});

test("发布页模块之间要有呼吸感：区块间距 24px，别退回 14px", () => {
  // 用户反馈「每个模块挤在一起，信息太多」。原来区块之间只有 14px，
  // 十几行字段连成一片，像一面信息墙。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(css, /\.is-console \.temu-console-main \{\n  gap: 24px;\n\}/);
  assert.match(css, /\.is-console \.temu-sec \{\n  padding: 24px 24px 26px;\n  gap: 18px;\n\}/);
  // 字段行距跟着放宽，标签和输入框之间也留一点
  assert.match(css, /\.is-console \.temu-sec \.temu-two-col,[\s\S]{0,120}gap: 16px 18px;/);
  assert.match(css, /\.is-console \.temu-sec \.temu-field \{\n  gap: 7px;\n\}/);
});

test("基本信息区块内部分段：类目一段、属性一段，属性不再卡里套卡", () => {
  // 用户反馈「里面的结构也该改」：选类目 / 规格开关 / 两个折叠 / 9 个属性
  // 全铺在同一张卡里没有分界，属性还套了第二层边框。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 属性段：独立类名 + 分隔线 + 说明单独一行
  assert.match(page, /className="temu-field temu-field-attrs"/);
  assert.match(page, /className="temu-field-note"/);
  assert.match(css, /\.is-console \.temu-sec > \.temu-field-attrs \{[\s\S]{0,160}border-top: 1px solid/);

  // 必填属性去掉第二层边框（外层卡片已经有边），只留淡底。
  // 不带 .is-console 前缀：批量发布弹窗（portal 到 body）里也要这一整块的底与内边距
  assert.match(css, /\.temu-attr-required \{[\s\S]{0,140}border: 0;/);
  // 3 列：220px 时下拉文字会被压窄
  assert.match(css, /\.is-console \.temu-attr-grid \{[\s\S]{0,100}minmax\(260px, 1fr\)/);
});

test("「按规格发」是标题行的小胶囊，不再占一整行", () => {
  // 用户：「缩小放在别的地方」。原来它是一整行大卡（标题+说明+内边距），
  // 把类目条和属性区隔开；其实只是「单 SKU 还是多规格」的一个开关。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(page, /className="temu-spec-switch"/);
  assert.doesNotMatch(page, /className="temu-variant-toggle"/);

  // 必须挂在「产品分类」标题行上（在 cat-picker 之前），不是类目条下面另起一行
  const titleIdx = page.indexOf("会记住上次用过的分类");
  const switchIdx = page.indexOf("temu-spec-switch");
  const pickerIdx = page.indexOf("temu-cat-picker");
  assert.ok(titleIdx > 0 && switchIdx > titleIdx && switchIdx < pickerIdx, "开关要挂在标题行");

  assert.match(css, /\.is-console \.temu-spec-switch \{[\s\S]{0,260}border-radius: 999px/);
  assert.match(css, /\.is-console \.temu-spec-switch:has\(input:checked\)/);
});

test("来源入口：未带入时挂步骤条右侧，带入后才占一整行", () => {
  // 用户圈出步骤条右边那块空白：来源条原来单占一行，首屏白白少看两个字段。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  // 未带入 → 步骤条右侧的胶囊入口；整行来源条的三元 else 已经是 null
  assert.match(page, /className="temu-stepbar-source"/);
  assert.match(page, /\{!prefillInfo && onOpenDraftLibrary && \(/);
  assert.match(page, /\) : null\}/);
  assert.doesNotMatch(page, /temu-source-bar is-empty/);

  // 带入后仍有整行摘要条（标题/图数/申报价/需核对项放得下）
  assert.match(css, /\.is-console \.temu-source-bar \{[\s\S]{0,200}border: 1px solid/);
  assert.match(css, /\.is-console \.temu-stepbar-source \{[\s\S]{0,260}border-radius: 999px/);
});


test("整页结构：批量编辑分组、图片子卡、发货时效整行、右栏指标卡", () => {
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 变种信息：7 个字段不再挤一行，按语义分成两组
  assert.match(panel, /className="temu-batch-group"/);
  assert.match(panel, /className="temu-batch-group-label">价格与库存/);
  assert.match(panel, /className="temu-batch-group-label">尺寸与重量/);

  // 产品信息：三个图片槽位是独立子卡（有边框有底色）
  assert.match(css, /\.is-console \.temu-img-group \{[\s\S]{0,200}border: 1px solid/);

  // 包装信息：发货时效占整行，说明在标题下方单独一行，档位是四枚胶囊
  assert.match(page, /className="temu-field temu-field-shipment"/);
  // ⚠️ 不能带 .is-console 前缀：批量发布弹窗不在 is-console 作用域里，带前缀那边没样式
  assert.match(css, /^\.temu-ship-pick-opt \{[\s\S]{0,300}border-radius: 999px/m);
  assert.match(css, /\.temu-ship-pick-input \.temu-input \{[\s\S]{0,60}max-width: 90px/);
  // 批量弹窗那一行是三列 grid，发货时效必须占满整行，否则四个胶囊挤在 150px 里
  assert.match(css, /\.temu-batch-manual-row \.temu-batch-shipment \{[\s\S]{0,40}grid-column: 1 \/ -1/);

  // 右栏三个指标做成小卡
  assert.match(css, /\.is-console \.temu-rail-stats div \{[\s\S]{0,160}background: var\(--theme-module-surface-soft/);

  // 校验发布：主按钮靠右，与辅助动作分开
  assert.match(css, /\.is-console \.temu-publish-actions \.temu-btn-primary \{[\s\S]{0,40}margin-left: auto/);
});

test("库存 0 能在提交前就被说清楚（不是提交成功后才提示）", () => {
  // 实测：带入草稿 → 库存改 0 → 提交按钮照样可用（Temu 允许 0 库存建品），
  // 但商品会落在「售罄/不可售」，而界面上原先只有 input 的 title 和提交后的 warning。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(panel, /const zeroStockCount = rows\.filter\(\(row\) => \{/);
  assert.match(panel, /return text === "" \|\| Number\(text\) <= 0;/);
  assert.match(panel, /className="temu-variant-zerohint"/);
  assert.match(panel, /可以提交，Temu 会建品但商品是「售罄 \/ 不可售」状态/);
  // 有 0 库存才出现
  assert.match(panel, /\{zeroStockCount > 0 && \(/);
  assert.match(css, /\.is-console \.temu-variant-zerohint \{[\s\S]{0,200}theme-warning-soft/);
});

test("服装类目「必须有尺码表但店里没建」要显式提示，不能把字段藏掉", () => {
  // 实测 2026-11-05：女装连衣裙 39107 的 meta 给出必填测量项「胸围全围、裙长」，
  // 但 sizecharts.get 返回 0 张表（新店必然如此）。旧逻辑按 has 判 → 字段被隐藏，
  // 用户连填模板 ID 的地方都没有。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");

  assert.match(page, /店里还没有可用的尺码表/);
  assert.match(page, /Temu 要求必填测量项：\$\{sizeChart\.info\.required_elements\.join\("、"\\?\)\}/);
  assert.match(page, /商品管理 → 尺码表模板/);
  assert.match(page, /该尺码组可选尺码：/);
});

test("图片槽位：上传位要看得见、能点能拖能粘（不再靠一行灰字）", () => {
  const strip = readFileSync(new URL("../components/TemuImageStrip.tsx", import.meta.url), "utf8");
  // 空槽位直接是虚线落点，有图时缩成条带末尾的 ＋ 格
  assert.match(strip, /function ImageAddTile\(/);
  assert.match(strip, /onDrop=\{\(event\) => \{/);
  assert.match(strip, /onPaste=\{\(event\) => \{/);
  assert.match(strip, /type="file"/);
  assert.match(strip, /variant === "compact"/);
  // 上传位必须真的接到上传逻辑上；顶部那颗隐蔽的「+ 添加图片」按钮已经删掉
  assert.match(page, /onAddFiles=\{\(files\) => void uploadFiles\(slot\.key, files\)\}/);
  assert.doesNotMatch(page, /\+\s*添加图片\s*<input/, "不该再有那个把 file input 藏进小按钮的写法");
  // 主图槽位铺满整行、可选项只占一个小格：三个一样大的虚线框会把页面撑长且没有主次
  assert.match(page, /variant=\{slot\.key === "carousel" \? "primary" : "compact"\}/);
  // 长规则收进 title，正文只留一行
  assert.match(page, /第一张是主图，可用 ← → 调序；单张 ≤2MB/);
});

test("发布流程条：圆点在上、文字在下的轨迹式布局，且来源入口只出现一次", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 版式：圆点 + 文字竖排，连接段画在两个圆点之间（对齐圆点中心）
  assert.match(page, /className="temu-stepbar-flow"/);
  assert.match(page, /className="temu-stepbar-node"/);
  // 连接段属于「本节点 → 下一个节点」，所以用本节点的 done 决定点亮
  assert.match(page, /temu-stepbar-seg\$\{step\.done \? " is-done" : ""\}/);
  assert.match(page, /className="temu-stepbar-progress"/);
  // 连接段绝对定位在 left:50% + width:100%（等宽单元格 → 正好连到下一个圆点中心）
  assert.match(css, /\.temu-stepbar-seg \{[\s\S]{0,240}left: 50%;[\s\S]{0,80}width: 100%/);
  // 垂直对齐圆点中心（圆点 22px → top 10px），否则会从文字那儿穿过去
  assert.match(css, /\.temu-stepbar-seg \{[\s\S]{0,240}top: 10px;/);
  // 待完成的连接段不能直接用边框色（白底上 1.1:1，等于没有）
  assert.match(css, /\.temu-stepbar-seg \{[\s\S]{0,260}color-mix\(in srgb, var\(--temu-text\) 16%/);
  // 完成度胶囊的文字别用 --temu-text-muted：实测只有 2.9:1
  assert.match(css, /\.temu-stepbar-progress \{[\s\S]{0,320}color-mix\(in srgb, var\(--temu-text\) 72%/);
  // 来源按钮只能有一个（改流程条时手滑复制成了两个，页面上出现两枚一模一样的胶囊）
  const sourceButtons = page.match(/className="temu-stepbar-source"/g) ?? [];
  assert.equal(sourceButtons.length, 1);
});

test("修正规格值要连批量列表一起改，否则再点生成又铺回非法值", () => {
  // 只改表格是治标：上方「颜色 / 尺码 列表」还留着非法值，
  // 用户再点一次「生成变种」就按旧文本重新铺一遍。
  assert.match(page, /const remapList = \(text: string, options: string\[\]\) => \{/);
  assert.match(page, /const nextColorsText = remapList\(form\.colorsText, colorNames\);/);
  assert.match(page, /patch\(\{ colorsText: nextColorsText, sizesText: nextSizesText \}\)/);
  // 对不上的值要直说「手动选」，不能让用户以为修正是坏的
  assert.match(page, /预设里没有相近值，请用上方「浏览全部预设」手动选/);
});

test("预设面板：颜色带色卡 + 按色系分组筛选，且是浮层不撑高布局", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 颜色值前面要有一枚色块，几十个「浅绿/浅绿色/薄荷绿」纯文字分不出来
  assert.match(panel, /const swatch = kind === "color" \? specColorGroup\(name\) : null;/);
  assert.match(panel, /className={`temu-spec-dot/);
  // 按色系收窄，再叠加搜索
  assert.match(panel, /const groups = kind === "color" \? groupSpecColors\(options\) : \[\];/);
  assert.match(panel, /className={`temu-spec-group/);
  // 浮层：内联展开会把整行拉高、把右边的尺码列挤下去
  assert.match(css, /\.temu-spec-panel \{[\s\S]{0,300}position: absolute;/);
  assert.match(css, /\.temu-spec-presets \{[\s\S]{0,120}position: relative;/);
  // 点空白处要能收起
  assert.match(panel, /target\?\.closest\("\.temu-spec-presets"\)/);
});

test("删一行变种：同色最后一行要连颜色列表一起删，否则生成又长回来", () => {
  // 用户问「只需要一个变种，怎么删」—— 只删表格行没用，上面的颜色列表还在，
  // 再点「生成变种」那一行会长回来。
  assert.match(page, /const removeRow = \(key: string\) => \{/);
  assert.match(page, /const target = rows\.find\(\(row\) => row\.key === key\);/);
  assert.match(page, /if \(color && !next\.some\(\(row\) => row\.colorName\.trim\(\) === color\)\) \{[\s\S]{0,220}colorsText: nextColors/);
  assert.match(page, /if \(size && !next\.some\(\(row\) => row\.sizeName\.trim\(\) === size\)\) \{[\s\S]{0,220}sizesText: nextSizes/);
  // 至少留 1 行，并且按钮要说清为什么不能删
  assert.match(page, /if \(!target \|\| rows\.length <= 1\) return;/);
  assert.match(panelSrc(), /至少保留 1 个变种/);
});

/** 便捷读取面板源码（多处断言要用） */
function panelSrc() {
  return readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
}

test("只要一维（只有颜色、不要尺码）：第二维可以关掉", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  // 下拉里必须有「不使用」出口；以前没有，用户只能看着尺码列发呆
  assert.match(panel, /<option value=\{0\}>不使用（单维）<\/option>/);
  // 关掉后：值输入换成一句说明、表格不再渲染尺码列
  assert.match(panel, /className="temu-spec-nodim"/);
  assert.match(panel, /sizeParentId > 0 \? <span>\{sizeSpecName\}<\/span> : null/);
  assert.match(panel, /sizeParentId > 0 \? \(\s*<input[\s\S]{0,300}placeholder=\{sizeOptions\.length/);
  // 生成时不塞「均码」假值：第二维关了就是 [""]；还开着但没填值 → 拦下并说明原因
  assert.match(panel, /else if \(sizeOptions\.includes\("均码"\)\) sizes = \["均码"\];/);
  assert.match(panel, /setGenHint\(/);
  assert.match(panel, /\} else \{\s*sizes = \[""\];/);
  // 提交端本来就只在 sizeParent 存在时才带第二维规格
  assert.match(page, /if \(sizeParent\) \{/);
});

test("变种表的占位符要一眼看出是提示，不是已填值", () => {
  // 截图反馈：一行里 12.99 / 可选 / 0 看着像已经填好了，右栏却显示「申报价 —」。
  // 占位符必须明显淡于真实值。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  assert.match(css, /\.is-console \.temu-variant-row \.temu-input::placeholder \{[\s\S]{0,120}34%, transparent/);
  // 单 SKU 类目不要再提「几个颜色 / 同色下尺码不能重复」
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /\{singleSku \? "。" : ` \/ \$\{new Set/);
  assert.match(panel, /singleSku \|\| sizeParentId <= 0 \? "" : "；同色下尺码不能重复"/);
});

test("变种表尺寸：长/宽/高 各占一列、各有表头", () => {
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 三个独立输入框，各带 aria-label（之前合成一组时被压成 39px 的三条缝）
  assert.match(panel, /aria-label="长（厘米）"/);
  assert.match(panel, /aria-label="宽（厘米）"/);
  assert.match(panel, /aria-label="高（厘米）"/);
  // 表头也是三列，不再是一个「尺寸(cm)」
  assert.match(panel, /<span title="单位：厘米">长<\/span>\s*<span title="单位：厘米">宽<\/span>\s*<span title="单位：厘米">高<\/span>/);
  assert.doesNotMatch(panel, /尺寸\(cm\)/);
  assert.doesNotMatch(panel, /temu-variant-dims/);
  // 两维模式：三列各 60px；单 SKU 模式省下颜色/尺码后摊宽到 96px
  assert.match(css, /grid-template-columns: 72px 96px 62px 104px 66px 60px 60px 60px 58px 70px minmax\(48px, 1fr\) 50px;/);
  assert.match(css, /\.temu-variant-row\.is-single \{[\s\S]{0,140}96px 96px 96px/);
  // 库存列吃剩余宽度；min-width 按真实列总和（732 + 11×6 + 48 = 846）
  assert.match(css, /\.temu-variant-table \{[\s\S]{0,200}min-width: 846px;/);
});

test("不再凭空造「均码」：新会话初始行留空，第二维没填就拦下生成", () => {
  // 以前初始行预填「均码」，不是预设值时提交必被拒；生成时也会塞一个假尺码
  assert.match(page, /return \[createVariantRow\(whId, "", "", EMPTY_BATCH, 0\)\];/);
  assert.match(page, /const fallbackSize = prefill\.specValue2 \|\| "";/);
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /第二维空着不能生成/);
});

test("右栏指标卡：三个数字要能一眼看出哪个没填", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 变种/轮播并排、申报价占整行（它最长，挤在 136px 里连 ¥12.99 都放不下）
  assert.match(page, /<dl className="temu-rail-stats">/);
  assert.match(page, /className=\{`temu-stat\$\{rows\.length \? "" : " is-danger"\}`\}/);
  assert.match(page, /className=\{`temu-stat is-wide\$\{firstPrice && Number\(firstPrice\) > 0 \? "" : " is-muted"\}\`\}/);
  // 轮播不足 3 张是琥珀、0 张才是红（Temu 要求至少 3 张）
  assert.match(page, /carouselCount >= 3 \? "" : carouselCount > 0 \? " is-warn" : " is-danger"/);
  // 有图标，且三个图标不是同一个
  assert.match(page, /function StatIcon\(\{ kind \}/);
  assert.match(page, /kind="variant"/);
  assert.match(page, /kind="image"/);
  assert.match(page, /kind="price"/);
  assert.match(css, /\.is-console \.temu-stat\.is-wide \{\s*grid-column: 1 \/ -1;/);
  // 语义色走主题变量，深色主题自动提亮（写死 #146c43 在 diamond 上几乎看不见）
  assert.match(css, /--temu-danger: var\(--theme-danger/);
  assert.match(css, /--temu-warn: var\(--theme-warning/);
});

test("金属质感：高光/暗边/描边三层都由变量控制，深色主题要反相", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  // 浅色：白高光 + 冷暗边
  assert.match(css, /--temu-metal-hi: rgba\(255, 255, 255, 0\.85\);/);
  assert.match(css, /--temu-metal-lo: rgba\(18, 45, 72, 0\.08\);/);
  // 深色必须覆盖，否则白高光在深底上是一道脏线
  assert.match(css, /\[data-theme="diamond"\] \.temu-publish-page\.is-console \{[\s\S]{0,200}--temu-metal-hi: rgba\(255, 255, 255, 0\.1\)/);
  // 主按钮：渐变 + 内高光 + 下沿暗边；按下时反过来
  assert.match(css, /\.temu-btn-primary \{[\s\S]{0,900}linear-gradient\(/);
  assert.match(css, /\.temu-btn-primary \{[\s\S]{0,900}inset 0 1px 0 rgba\(255, 255, 255, 0\.4\)/);
  assert.match(css, /\.temu-btn-primary:active:not\(:disabled\) \{[\s\S]{0,400}inset 0 2px 5px/);
  // 文字阴影会糊：diamond 主题下主按钮是浅底深字
  assert.doesNotMatch(css, /\.temu-btn-primary \{[\s\S]{0,400}text-shadow/);
});

test("类目没有预设值时必须自建规格，不能报「只认预设值」", () => {
  // 截图复现：化妆包这类自定义规格类目，界面写着「可以自己填写（Temu 按自定义规格收）」，
  // 点提交却回「「黑」不是该 颜色 的合法值：Temu 只认类目预设值」—— 永远发不出去。
  // 批量发布那边一直有自建分支，单个发布漏了。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  assert.match(panel, /这个类目没有\$\{label\}预设值，可以自己填写（Temu 按自定义规格收）/);

  // 提交端要跟这句承诺一致：没有预设 → createSpec
  assert.match(page, /const createdSpecsRef = useRef\(new Map<string, TemuSkuSpec>\(\)\)/);
  assert.match(page, /if \(!options\.length\) \{[\s\S]{0,300}temuPublishApi\.createSpec\(parentSpec\.parentSpecId, name\)/);
  assert.match(page, /createdSpecsRef\.current\.set\(cacheKey, resolved\)/);
  // 同一个值只建一次（颜色×尺码会反复解析）
  assert.match(page, /const cached = createdSpecsRef\.current\.get\(cacheKey\);\s*if \(cached\) return cached;/);
  // 有预设但对不上仍然要拦 —— 那才是真的填错
  assert.match(page, /不在「\$\{parentSpec\.parentSpecName\}」的预设值里/);
  assert.doesNotMatch(page, /不是该 \$\{parentSpec\.parentSpecName\} 的合法值/);
});

test("中文颜色不能让货号撞车：SKU-黑/浅绿 会被洗成同一个 SKU-", () => {
  // 用户截图：「SKC ext_code 重复：SKC」。四个颜色「黑/浅绿/浅黄/象牙」拼进货号后，
  // cleanExtCode 只留 A-Za-z0-9-_，中文全被删 → 全变成空的或只剩「SKU-」→ 撞号。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");

  // 1) 默认货号不再拼中文：带非 ASCII 就直接用序号，表格里看到的就是能提交的值
  assert.match(panel, /const asciiOnly = parts\.every\(\(part\) => !\/\[\^\\x20-\\x7E\]\/\.test\(part\)\);/);
  assert.match(panel, /parts\.length > 1 && asciiOnly/);

  // 2) SKC 货号：颜色洗成空时用 C1/C2… 兜底，并且保证后缀不被 120 字截断
  assert.match(page, /const colorCode = cleanExtCode\(color\) \|\| `C\$\{colorIndex\}`;/);
  assert.match(page, /ext_code: `\$\{skcHead\}-\$\{colorCode\}`/);
  assert.match(page, /120 - colorCode\.length - 1/);

  // 3) SKU 货号：洗成空壳（「SKU-」）或撞号时自动补序号，并留 warning
  assert.match(page, /const usedSkuCodes = new Set<string>\(\);/);
  assert.match(page, /if \(!cleaned \|\| \/\[-_\]\$\/\.test\(cleaned\)\) \{/);
  assert.match(page, /SKU 货号去掉中文后与前面的重复，已改成/);
});

test("长宽高必须按「最长 ≥ 次长 ≥ 最短」提交（Temu 体积校验）", () => {
  // 实测被拒：「体积内容必须遵循最长边 ≥ 次长边 ≥ 最短边原则」。
  // 用户填的 33×13×20 是对物品的描述（长 33、宽 13、高 20），不是排序结果；
  // 体积只看三个边长，替他按降序发即可，物理含义不变。
  assert.match(page, /function sortDimensionsDesc\(a: number, b: number, c: number\): \[number, number, number\]/);
  assert.match(page, /\.sort\(\(x, y\) => y - x\)/);
  assert.match(page, /const \[longest, middle, shortest\] = sortDimensionsDesc\(lengthCm, widthCm, heightCm\);/);
  assert.match(page, /length_mm: Math\.round\(longest \* 10\)/);
  assert.match(page, /width_mm: Math\.round\(middle \* 10\)/);
  assert.match(page, /height_mm: Math\.round\(shortest \* 10\)/);
  // 重排过要告诉用户，别让他以为系统把尺寸填错了
  assert.match(page, /不是「最长 ≥ 次长 ≥ 最短」/);

  // 批量发布走同一套规则，否则那条链路还是会踩同一个坑
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");
  assert.match(batch, /const sortedDims = \[lengthCm, widthCm, heightCm\]\.sort\(\(a, b\) => b - a\);/);
  assert.match(batch, /length_mm: Math\.max\(1, Math\.round\(sortedDims\[0\] \* 10\)\)/);
  assert.match(batch, /height_mm: Math\.max\(1, Math\.round\(sortedDims\[2\] \* 10\)\)/);
});

test("尺码表：店里已有的表要能点选，不用去后台抄 ID", () => {
  // 用户反馈：Vesthara Shop 里有带尺码表的商品，发布时却只能手填一串模板 ID。
  // 后端把 sizecharts.get 的行收敛成 {id, name}，前端直接列成可点的胶囊。
  assert.match(page, /const selectedSizeTemplateIds = form\.sizeTemplates/);
  assert.match(page, /const toggleSizeTemplate = \(id: number\) => \{/);
  assert.match(page, /patch\(\{ sizeTemplates: next\.join\(","\) \}\)/);
  assert.match(page, /className="temu-sizechart-picks"/);
  assert.match(page, /className=\{`temu-sizechart-chip\$\{picked \? " is-picked" : ""\}`\}/);
  // 没有名字的行退化成「尺码表 + ID」，不能让用户看到空白胶囊
  assert.match(page, /\{table\.name \|\| "尺码表"\}/);
  // 有表但网关没给 ID 时要说清楚，别让人对着空白找
  assert.match(page, /但网关没返回能用的 ID/);

  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");
  assert.match(css, /\.is-console \.temu-sizechart-chip \{/);
  assert.match(css, /\.is-console \.temu-sizechart-chip\.is-picked \{/);
});

test("成品库：批次里超过 20 个时能「加载更多」，不再只显示前 20 个", () => {
  // 用户有 500 个品时发现：一个批次只显示前 20 个，界面还写「共 20 条」，
  // 剩下 480 个既看不到、也没有任何提示。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");

  // 请求带每批偏移
  assert.match(library, /const \[itemOffset, setItemOffset\] = useState\(0\);/);
  assert.match(library, /itemOffset,\n\s*refresh,/);
  // offset > 0 时追加而不是替换（否则「加载更多」会把第一页顶掉）
  assert.match(library, /if \(itemOffset > 0\) \{[\s\S]{0,220}setDeepEntries\(\(prev\) => \{/);
  assert.match(library, /\[\.\.\.\(data\?\.entries \?\? \[\]\), \.\.\.deepEntries\]/);
  // 切页/换筛选要清零，否则两批数据混在一起
  assert.match(library, /const goPage = \(next: number\) => \{[\s\S]{0,120}setItemOffset\(0\);[\s\S]{0,80}setDeepEntries\(\[\]\);/);
  // 按钮和提示
  assert.match(library, /每个批次已加载前 \{itemOffset \+ 20\} 个，还有更多/);
  assert.match(library, /onClick=\{\(\) => setItemOffset\(\(current\) => current \+ 20\)\}/);
  assert.match(library, /\{data\?\.has_more \? \(/);
  // 后端契约
  assert.match(types, /has_more\?: boolean;/);
  assert.match(types, /item_offset\?: number;/);
});

test("提交失败要「红到哪里」：出错区块描红 + 清单同步 + 换商品即清", () => {
  // 用户要求：提交失败了，失败的地方有颜色提醒；正常情况没有颜色。
  // 实现：错误按 section 归类（后端自带 section 优先，本地校验按文案归类），
  // 命中时给区块和右栏清单加 is-error 描红并滚过去；下次提交/换商品自动清。
  const page = readFileSync(new URL("./TemuPublishPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 1) 本地校验文案 → 区块
  assert.match(page, /function sectionOfValidation\(message: string\): string \{/);
  assert.ok(page.includes('return "sec-pack";'));
  assert.ok(page.includes('return "sec-variant";'));
  // 后端 section 优先，其次才按文案归类
  assert.ok(page.includes("setErrorSection(typeof section === \"string\" ? section : sectionOfValidation(text));"));

  // 2) 拦下来时：标红必填 + 跳到出错区块（传 cause 进去）
  assert.match(page, /const noteValidationBlocked = \(cause\?: unknown\) => \{/);
  assert.match(page, /noteValidationBlocked\(cause\);/);
  assert.ok(page.includes('if (section) setErrorSection(section);'));

  // 3) 四个区块都能命中 is-error（正常状态没有这个类）
  for (const sec of ["sec-base", "sec-info", "sec-variant", "sec-pack"]) {
    assert.ok(
      page.includes('${errorSection === "' + sec + '" ? " is-error" : ""}'),
      sec + " 要绑定 is-error",
    );
  }

  // 4) 右栏清单同步标红；换商品清掉红标
  assert.ok(page.includes('${errorSection === step.id ? " is-error" : ""}'));
  assert.ok(page.includes('setErrorSection("");'));

  // 5) 样式：只在 .is-error 上才有红色
  assert.match(css, /\.is-console \.temu-sec\.is-error \{[\s\S]{0,200}border-color: #e2384a;/);
  assert.match(css, /\.is-console \.temu-sec\.is-error \.temu-sec-num \{/);
  assert.match(css, /\.is-console \.temu-rail-list li\.is-error \.temu-rail-dot \{/);
  assert.match(css, /\[data-theme="alpine"\] \.is-console \.temu-sec\.is-error \{/);
});

test("发布确认用应用内弹窗：列清标题/类目/变种，重复提交也能二次确认", () => {
  // 原生 window.confirm 在 WebView 里可能被禁（点了直接 false），样式也不搭。
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(page, /className="temu-edit-dialog temu-confirm-dialog"/);
  assert.match(page, /<dt>标题<\/dt>/);
  assert.match(page, /<dt>类目<\/dt>/);
  assert.match(page, /<dt>变种<\/dt>/);
  assert.match(page, /确认提交/);
  assert.match(page, /仍然提交/);
  // 重复提交：换成二次确认，不再走原生 confirm
  assert.match(page, /setPublishConfirm\(\{ kind: "duplicate", draft, skuCount: job\.skuCount, note: text \}\);/);
  // 快捷键在弹窗开着时不能再触发一次
  assert.match(page, /if \(publishing \|\| busy !== "" \|\| !ready \|\| publishConfirm\) return;/);
  assert.match(css, /\.temu-confirm-dialog \{/);
  assert.match(css, /\[data-theme="alpine"\] \.temu-confirm-dialog \{/);
});

test("失败红到字段：showErrors 传给变种面板，缺的格子直接红框", () => {
  // 区块级描红只说「这块有问题」，这块再说清楚「第几行的哪一格空着」。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");

  assert.match(page, /showErrors=\{showFieldErrors\}/);
  assert.match(panel, /showErrors = false/);
  assert.match(panel, /const cellBad = \(missing: boolean\) => \(showErrors && missing \? " is-bad" : ""\);/);
  for (const field of ["colorName", "sizeName", "extCode", "supplierPrice", "lengthCm", "widthCm", "heightCm", "weightG"]) {
    assert.ok(
      panel.includes("cellBad(!row." + field + ".trim())"),
      field + " 缺值要标红",
    );
  }
});

test("提交前的传图走限并发，按钮上能看到上传进度", () => {
  // 原来 10 张轮播 + 6 张外包装一张张串行传，网络一慢提交前干等且没有进度；
  // 限并发 3 折中，轮播/外包装/详情全算进进度，传完或失败立刻清空。
  assert.match(page, /const IMAGE_UPLOAD_CONCURRENCY = 3;/);
  assert.match(page, /mapWithConcurrency\(urls, IMAGE_UPLOAD_CONCURRENCY, async \(url\) =>/);
  assert.match(page, /mapWithConcurrency\(sources, IMAGE_UPLOAD_CONCURRENCY, async \(text\) =>/);
  assert.match(page, /const uploadTotal = carouselSource\.length \+ outerSource\.length \+ detailSource\.length;/);
  assert.match(page, /setUploadProgress\(\{ done: 0, total: uploadTotal \}\);/);
  assert.ok(page.includes("图片上传 ${uploadProgress.done}/${uploadProgress.total}…"));
  // 失败也要清掉，不许一直挂着「上传中」
  assert.match(page, /} finally \{\n      setUploadProgress\(null\);\n    \}/);
  // 并发工具本身有行为测试（data/concurrency.test.ts）
  assert.ok(
    readFileSync(new URL("../data/concurrency.test.ts", import.meta.url), "utf8").includes("峰值并发应等于 3"),
  );
});

test("变种行级「套用到同色/同码」：⋯ 菜单只影响对应分组", () => {
  // 4 色 × 3 码时尺寸一码一套、价格同色一套，逐行手改 12 行太慢。
  const panel = readFileSync(new URL("../components/PublishSkuPanel.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(panel, /const \[rowMenuKey, setRowMenuKey\] = useState<string \| null>\(null\);/);
  assert.match(panel, /const applyRowFields = \(key: string, scope: "color" \| "size"\) => \{/);
  assert.ok(panel.includes('const COLOR_FIELDS = ["supplierPrice", "suggestedPrice", "stock"] as const;'));
  assert.ok(panel.includes('const SIZE_FIELDS = ["lengthCm", "widthCm", "heightCm", "weightG"] as const;'));
  assert.match(panel, /className="temu-variant-more"/);
  assert.match(panel, /className="temu-row-menu"/);
  // 单维类目没有「同尺码」分组，不显示那一项
  assert.match(panel, /\{sizeParentId > 0 && \(\s*\n\s*<button[\s\S]{0,200}applyRowFields\(row\.key, "size"\)/);
  assert.match(css, /\.temu-row-menu \{/);
});

test("发布历史：失败原因归堆，点一下就只看同类失败", () => {
  // 「发 30 条失败 20 条」时，一条条翻才能看出其实都栽在同一个错上。
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(history, /const \[reasonFilter, setReasonFilter\] = useState\(""\);/);
  assert.match(history, /const reasonGroups = \(\(\) => \{/);
  assert.match(history, /const listItems = reasonFilter/);
  assert.match(history, /\{listItems\.map\(\(item\) => \{/);
  assert.match(history, /setTab\("failed"\);\s*\n\s*setExpanded\(\{\}\);\s*\n\s*setReasonFilter\(""\);/);
  assert.match(history, /className=\{`temu-history-reason-chip/);
  assert.match(css, /\.temu-history-reason-chip \{/);
});

test("高山草原：成品库 / 发布历史 / 弹窗统一晨雾质感", () => {
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(css, /\[data-theme="alpine"\] \.temu-edit-dialog \{/);
  assert.match(css, /\[data-theme="alpine"\] \.temu-library-stat \{/);
  assert.match(css, /\[data-theme="alpine"\] \.temu-draft-table tbody tr:hover \{/);
  assert.match(css, /\[data-theme="alpine"\] \.temu-history-row \{/);
  assert.match(css, /\[data-theme="alpine"\] \.temu-history-tab\.is-active \{/);
});

test("必填属性实时说「还缺哪几个」，空项带红点", () => {
  const form = readFileSync(new URL("../components/TemuPropertyForm.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  assert.match(form, /const missingRequired = groups\.required\.filter\(\(item\) => \{/);
  assert.match(form, /className="temu-attr-missing"/);
  assert.match(form, /还缺 \{missingRequired\.length\} 个：/);
  assert.match(form, /className="temu-attr-req-dot"/);
  assert.match(css, /\.temu-attr-missing \{/);
  assert.match(css, /\.temu-attr-req-dot \{/);
});

test("发布确认弹窗带上价格/图片/运费/仓库，最后一眼能核对", () => {
  assert.match(page, /priceLabel\?: string;/);
  assert.match(page, /<dt>价格<\/dt>/);
  assert.match(page, /<dt>图片<\/dt>/);
  assert.match(page, /<dt>运费<\/dt>/);
  assert.match(page, /<dt>仓库<\/dt>/);
  assert.ok(page.includes("轮播 ${splitLines(form.carousel).length}"));
});

test("成品库：失败过的草稿打标 + 只看失败过；历史点「去修这条」自动打开", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 成品库没有「已发布」状态（发成功的会移除），所以做的是「失败过」
  assert.match(library, /const failedTitles = new Set\(/);
  assert.match(library, /const \[onlyFailed, setOnlyFailed\] = useState\(false\);/);
  assert.match(library, /const failedCount = entries\.filter\(\(entry\) => failedTitles\.has\(entry\.title\.trim\(\)\)\)\.length;/);
  assert.match(library, /\{visibleEntries\.map\(\(entry\) => \(/);
  assert.match(library, /className="temu-draft-failed"/);
  assert.match(library, /className={`temu-library-failed-toggle\$\{onlyFailed \? " is-active" : ""\}`}/);
  assert.match(css, /\.temu-library-failed-toggle \{/);
  assert.match(css, /\.temu-draft-failed \{/);

  // 历史 → 草稿库：记下目标草稿 id，草稿库读到后自动打开
  assert.match(types, /export const TEMU_LIBRARY_FOCUS_KEY = "temu_library_focus_draft_v1";/);
  assert.match(history, /window\.sessionStorage\.setItem\(TEMU_LIBRARY_FOCUS_KEY, item\.draft_id\);/);
  assert.match(history, /\{item\.draft_id \? "去修这条" : "去成品库重发"\}/);
  assert.match(library, /window\.sessionStorage\.getItem\(TEMU_LIBRARY_FOCUS_KEY\)/);
  assert.match(library, /onUseDraft\(entryPrefill\(target, categoryMemo, attrPrefills\)\);/);
});

test("批量发布：先预检再提交，发不出去的默认跳过", () => {
  // 以前点「开始发布」直接逐条传图，10 条里 6 条缺图，等它跑完才知道。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const precheck = readFileSync(new URL("../data/batchPrecheck.ts", import.meta.url), "utf8");

  // 纯本地预检（不传图、不打网关）
  assert.match(precheck, /export function precheckBatchItem\(/);
  assert.match(precheck, /轮播图只有 \$\{carousel\.length\} 张（Temu 要求至少 3 张）/);
  assert.match(precheck, /产品模板里没有默认发货仓库/);
  assert.match(precheck, /库存 0（会以售罄状态创建）/);
  // 口径和实际组装一致：变种模板的值优先于草稿自带值
  assert.match(precheck, /variantTemplate\?\.colorsText \|\|/);
  assert.match(precheck, /variantTemplate\?\.batch/);

  // 弹窗里实时算 + 默认跳过阻塞项
  assert.match(library, /const batchPrecheck = useMemo\(/);
  assert.match(library, /const blockedPrecheck = batchPrecheck\.filter/);
  assert.match(library, /const \s*\[skipBroken, setSkipBroken\] = useState\(true\)/);
  assert.match(library, /const runnableTargets = skipBroken/);
  assert.match(library, /className="temu-batch-precheck"/);
  assert.match(library, /跳过发不出去的 \{blockedPrecheck\.length\} 条/);
  assert.match(library, /可发 \{batchTargets\.length - blockedPrecheck\.length\} 条 · 预计失败/);
  // 提交的也是按预检过滤后的 tasks（每条还带着自己那组的模板），不是全部勾选
  assert.match(library, /const tasks: BatchTask\[\] = groups\.flatMap\(\(group, groupIndex\) =>/);
  assert.match(library, /\).map\(\(entry\) => \(\{\s*\n\s*entry,\s*\n\s*template: groupTemplate,/);
});

test("批量发布：跑批途中能停，停下后说清还剩多少条", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // ref 而不是 state：循环里要读到最新值
  assert.match(library, /const batchAbortRef = useRef\(false\);/);
  assert.match(library, /batchAbortRef\.current = false;/);
  assert.match(library, /if \(batchAbortRef\.current \|\| batchStopRequested\(\)\) \{\s*\n\s*aborted = true;\s*\n\s*break;/);
  assert.match(library, /batchAbortRef\.current = true;/);
  assert.match(library, /正在停止（当前这条发完就停）/);
  assert.match(library, /const restText = restCount > 0 \? `，还剩 \$\{restCount\} 条未发` : "";/);
  assert.match(library, /className="temu-batch-stop"/);
  assert.match(css, /\.temu-batch-stop \{/);
});

test("批量发布：每条十几张图走限并发，不再串行排队", () => {
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  assert.match(batch, /const IMAGE_UPLOAD_CONCURRENCY = 3;/);
  assert.match(batch, /await mapWithConcurrency\(carouselSources, IMAGE_UPLOAD_CONCURRENCY/);
  assert.match(batch, /await mapWithConcurrency\(\s*\n\s*splitList\(\(prefill\.detailImages/);
  // 服饰类目取不到原图要「先判再传」，别白传 10 张
  assert.match(batch, /const firstRaw = apparel \? await localDataUrl\(carouselSources\[0\] \?\? ""\) : null;/);
  assert.match(batch, /if \(apparel && !firstRaw\) \{/);
  // 串行写法必须清干净
  assert.doesNotMatch(batch, /for \(const \[index, path\] of carouselSource/);
});

test("批量发布：循环本身炸了也要放开「发布中」，不能锁死界面", () => {
  // 真实隐患：catch 里 localStore.addFailure 写满配额会抛，异常从 catch 冒出去 —
  // async 函数 reject 没人接，batchState.running 永远 true：按钮锁死、弹窗打不开、
  // 只能刷新页面。整批必须包 try/catch/finally 兜底。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  assert.match(library, /let crashed = "";/);
  assert.match(library, /} catch \(cause\) \{\s*\n\s*\/\/ 循环本身炸了/);
  assert.match(library, /crashed = errorMessage\(cause\) \|\| "未知错误";/);
  assert.match(library, /\} finally \{/);
  // finally 里必须无条件把 running 放掉
  assert.match(library, /\} finally \{[\s\S]{0,900}running: false,/);
  assert.match(library, /中断：\$\{crashed\}（已成功 \$\{ok\} 条，失败 \$\{fail\} 条\$\{restText\}）/);
  // 标题可能为空：进度文案不能直接 entry.title.slice
  assert.match(library, /const label = \(entry\.title \|\| entry\.sku \|\| "（无标题）"\)\.slice\(0, 16\);/);
  assert.doesNotMatch(library, /text: `\$\{index \+ 1\}\/\$\{targets\.length\} · \$\{entry\.title\.slice/);
});

test("批量发布：中止后没发的要留着勾选，点一下就能接着发", () => {
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  assert.match(library, /const handledIds: string\[\] = \[\];/);
  // 成功 / 判定重复才算出「处理掉」，失败的要留着（用户可能想改完重发）
  assert.match(library, /handledIds\.push\(entry\.id\);\s*\n\s*ok \+= 1;/);
  assert.match(library, /skipped \+= 1;\s*\n\s*handledIds\.push\(entry\.id\);/);
  // 中止分支只摘掉处理过的
  assert.match(library, /if \(aborted \|\| crashed\) \{[\s\S]{0,400}setSelected\(\(current\) => new Set\(\[\.\.\.current\]\.filter\(\(id\) => !done\.has\(id\)\)\)\)/);
  // 整批跑完才全清
  assert.match(library, /\} else if \(ok > 0\) \{[\s\S]{0,200}setSelected\(new Set\(\)\);/);
});

test("批量发布：切页/刷新前发起的那批还在跑，不许再开一批（防重复上架）", () => {
  // 真隐患：跑批途中切到别的模块，本页被卸载、batchState 归零；回来后用户以为没在跑，
  // 再开一批 → 同一批草稿提交两次，店铺里多出重复商品（接口非幂等）。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");

  // 跨实例标记：localStorage + 心跳，2 分钟没心跳视为已死（刷新会真的杀掉循环）
  assert.match(library, /const BATCH_RUN_KEY = "temu_publish_batch_running_at";/);
  assert.match(library, /const BATCH_STOP_KEY = "temu_publish_batch_stop";/);
  assert.match(library, /const BATCH_HEARTBEAT_TIMEOUT = 120_000;/);
  assert.match(library, /function batchRunningElsewhere\(\): boolean \{/);
  assert.match(library, /return at > 0 && Date\.now\(\) - at < BATCH_HEARTBEAT_TIMEOUT;/);

  // 开批前查跨实例标记（本实例的 state 可能根本不知道那批）：点按钮就拦，别等填完模板
  assert.match(library, /const openBatchDialog = async \(\) => \{[\s\S]{0,400}batchRunningElsewhere\(\)/);
  assert.match(library, /if \(batchRunningElsewhere\(\)\) \{/);
  assert.match(library, /并行提交会把同一批草稿重复上架/);

  // 开批写标记、每轮心跳、finally 一定要清
  assert.match(library, /writeBatchStore\(BATCH_RUN_KEY, String\(Date\.now\(\)\)\);/);
  assert.match(library, /\} finally \{[\s\S]{0,300}writeBatchStore\(BATCH_RUN_KEY, ""\);/);

  // 停止也要跨实例：写 STOP 标记，跑批循环每轮读
  assert.match(library, /function batchStopRequested\(\): boolean \{/);
  assert.match(library, /writeBatchStore\(BATCH_STOP_KEY, "1"\);/);
  assert.match(library, /batchAbortRef\.current \|\| batchStopRequested\(\)/);

  // 切回来要认领：进度条 + 停止按钮放回来，心跳停了就放开并刷新
  assert.match(library, /if \(!batchRunningElsewhere\(\)\) return;/);
  assert.match(library, /另一处发起的批量发布还在提交…/);
  assert.match(library, /上一次批量发布已结束（在别的页签 \/ 切页前跑完的）/);
});

test("失败原因说人话：黑话翻译 + 修法 + 原文留着复制", () => {
  // Temu 回的是「bg.glo.goods.add: 货品类目属性更新，请刷新页面后重新创建货品」——
  // 用户看这句不知道该干嘛（刷新哪？）。31 条失败里 18 条都是这一句。
  const history = readFileSync(new URL("./TemuPublishHistoryPage.tsx", import.meta.url), "utf8");
  const explain = readFileSync(new URL("../data/errorExplain.ts", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 翻译表：具体规则在前（属性[X] 不能被「属性」吃掉），不认识的返回 null 不瞎猜
  assert.match(explain, /export function explainPublishError/);
  assert.match(explain, /const RULES: Rule\[\] = \[/);
  assert.ok(explain.includes("属性\\["), "要有属性[X] 的规则");
  assert.ok(explain.includes("不是有效值|校验错误"));
  assert.match(explain, /if \(!hit\) continue;/);
  assert.match(explain, /return null;/);
  assert.match(explain, /export function shortReasonLabel/);
  assert.ok(explain.includes("shortReasonLabel"));

  // 失败卡片：人话 + 修法在上，原文退成小字
  assert.match(history, /const explained = isSuccess \? null : explainPublishError\(item\.error \|\| reason\);/);
  assert.match(history, /className="temu-history-reason-human"/);
  assert.match(history, /className="temu-history-reason-fix"/);
  assert.match(history, /\{explained \? "原始提示：" : ""\}/);
  assert.match(history, /className=\{`temu-history-reason\$\{explained \? " is-raw" : ""\}/);
  // 归堆 chips 也说人话（筛选仍用原文当 key）
  assert.match(history, /const label = shortReasonLabel\(group\.reason\);/);
  assert.match(css, /\.temu-history-reason-human \{/);
  assert.match(css, /\.temu-history-reason-fix \{/);
});

test("Temu 硬性限制本地先拦：标题 ≤500 字符、重量 2~1000000g", () => {
  // 两个都是「提交才被拒、本地完全能提前算出来」的类型。
  // 标题上限来自 Temu 官方文档（goodsName ≤500 字符）；
  // 重量边界来自实测回执「重量不符合类目2~1000000g限制，无法售卖」。
  assert.match(page, /const TITLE_MAX_CHARS = 500;/);
  assert.match(page, /const WEIGHT_MIN_G = 2;/);
  assert.match(page, /const WEIGHT_MAX_G = 1_000_000;/);
  assert.match(page, /if \(form\.title\.trim\(\)\.length > TITLE_MAX_CHARS\) \{/);
  assert.match(page, /function parseWeightG\(value: string, label: string\): number \{/);
  assert.match(page, /number < WEIGHT_MIN_G \|\| number > WEIGHT_MAX_G/);
  // 变种行的重量必须走范围校验，不能再用只查 >0 的 parsePositive
  assert.match(page, /parseWeightG\(row\.weightG/);
  assert.doesNotMatch(page, /parsePositive\(row\.weightG/);
  // 批量预检同口径（batchPrecheck.test.ts 里有行为测试）
  const precheck = readFileSync(new URL("../data/batchPrecheck.ts", import.meta.url), "utf8");
  assert.match(precheck, /超过 Temu 上限/);
  assert.match(precheck, /超出 Temu 允许范围/);
});

test("批量发布：没选模板时按每条草稿自己的类目分组，匹配上倒计时自动开跑", () => {
  // 用户诉求：「店小秘点一下就全上架，为什么我们不能」。
  // 店小秘的类目藏在「导入任务」里；我们做到更前一步：标题匹配 Temu 类目 → 找对应模板 → 自动跑。
  // 但必须**按条分组**：勾选的一批常横跨好几个类目，拿第一条的类目套整批 = 把化妆包发成托特包。
  const library = readFileSync(new URL("./TemuDraftLibraryPage.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../styles/temuPublish.css", import.meta.url), "utf8");

  // 补类目：只补没有 memo 的，按标题 → matchCategory → 首个叶子路径 → 写 memo
  assert.match(library, /const ensureDraftCategories = async \(entries: TemuDraftEntry\[\]\) => \{/);
  assert.match(library, /const text = categorySearchTexts\(entry\.title \|\| entry\.subtitle \|\| ""\)\[0\];/);
  assert.match(library, /await temuPublishApi\.matchCategory\(text, 100\)/);
  assert.match(library, /localStore\.rememberDraftCategory\(entry\.id, path, \{ auto: true \}\)/);
  // 匹配接口抖动不硬发：这条按「没类目」排除，绝不拿猜的类目发
  assert.match(library, /这条按「没类目」排除，绝不拿猜的类目硬发/);

  // 分组：按每条自己的末级 catId 归组，每组找末级 catId 严格相等的模板（没有就是 null，等自动拼）
  assert.match(library, /const planBatchGroups = \(entries: TemuDraftEntry\[\]\) => \{/);
  assert.match(library, /return templateLeaf && String\(templateLeaf\.catId\) === leafCatId;/);
  assert.match(library, /template,\s*\n\s*categoryPath: bucket\.path,/);
  // 没存模板的类目：用草稿自己的类目 + 估好的属性 + 店铺默认运费/仓库临时拼一份模板
  assert.match(
    library,
    /const buildAutoTemplate = async \(\s*\n\s*group: BatchGroup,\s*\n\s*\): Promise<\{ template: ProductTemplate \| null; reason: string \}> => \{/,
  );
  assert.match(library, /const missing = missingRequiredNames\(visibleProperties\(attributes, picks\), picks\);/);
  // 拼不出来的原因要能说清（缺属性 / 没运费模板 / 没有可用仓库），别只回一个 null
  assert.match(library, /必填属性没估全（缺 \$\{missing\.slice\(0, 3\)\.join\("、"\)\}）/);
  assert.match(library, /这个店没有可用于批量发布的仓库（都被标成类型 3）/);
  assert.match(library, /const auto = await buildAutoTemplate\(group\);/);
  assert.match(library, /已按草稿的类目\/属性自动填（属性是估的）/);
  // 拼不出模板的条目数要说出来（别让用户以为整批都发了）
  assert.match(library, /条没匹配到类目/);
  assert.match(library, /需手动配/);

  // 触发条件：没手选模板 / 有勾选；**没有存过模板也照样走自动**（以前直接弹窗，用户被逼着手配）
  assert.match(library, /if \(productTpl \|\| manualTemplate\) \{\s*\n\s*setBatchOpen\(true\);\s*\n\s*return;/);
  assert.doesNotMatch(library, /if \(!first \|\| !productTemplates\.length\) \{/);
  assert.match(library, /const ready = plan\.groups\.filter\(\(group\) => group\.template\);/);

  // 确认单：**必须手点才发**，不再有倒计时自动开跑（点错就上架过，2026-11-12 改掉）
  assert.match(library, /const \[publishConfirm, setPublishConfirm\] = useState</);
  assert.match(library, /const cancelPublishConfirm = \(openDialog = false\) => \{/);
  assert.match(library, /setPublishConfirm\(\{\s*\n\s*groups: ready,/);
  assert.doesNotMatch(library, /AUTO_START_SECONDS/, "不能再有倒计时自动开跑的常量");
  assert.doesNotMatch(library, /temu-batch-autostart/, "倒计时条的 DOM/样式要清干净");
  assert.match(library, /className="temu-edit-dialog temu-batch-confirm"/);
  // 确认单上要写清发到哪家店、共几条、每组什么模板
  assert.match(library, /将提交到「\{publishConfirm\.shopName \|\| "当前店铺"\}」/);
  assert.match(library, /确认发布（\{publishConfirm\.runnable\} 条）/);
  assert.match(library, /模板「\{group\.template\?\.name \?\? "自动"\}」/);
  assert.match(css, /\.temu-batch-confirm \{/);

  // 自动跑把「分组计划」直接传进去（state 里没有它），每组用自己的模板组装 + 预检
  assert.match(library, /const runBatchPublish = async \(overrideTemplate\?: ProductTemplate, plan\?: BatchGroup\[\]\) => \{/);
  assert.match(library, /const tasks: BatchTask\[\] = groups\.flatMap\(\(group, groupIndex\) =>/);
  assert.match(library, /template: groupTemplate,/);
  assert.match(library, /const \{ entry, template: itemTemplate \} = task;/);
  assert.match(library, /await buildDraftForPublish\(\s*\n\s*entryPrefill\(entry, categoryMemo, attrPrefills\),\s*\n\s*itemTemplate,/);
  assert.match(library, /localStore\.rememberDraftCategory\(entry\.id, itemTemplate\.categoryPath\);/);
  assert.ok(
    library.includes("void runBatchPublish(undefined, groups);"),
    "点「确认发布」要带着分组计划开跑",
  );
  // 能发 0 条不出确认单（这批有硬伤，先看预检）
  assert.match(library, /if \(!runnable\) \{\s*\n\s*setBatchState\(\{ running: false, text: "这批都有阻塞问题，先看下面的预检" \}\);/);
});

test("批量发布：属性按每条草稿自己的估值，不整组共用第一条", () => {
  const batch = readFileSync(new URL("../data/batchPublish.ts", import.meta.url), "utf8");

  // 自动拼的组模板只是「第一条的快照」；同组十几条材料/风格常常不一样，
  // 整组共用一套会发成错属性或被判「属性不合法」
  assert.match(batch, /const carriedPicks =/);
  assert.match(
    batch,
    /prefill\.attrPicks && Number\(prefill\.attrPicksCatId \?\? 0\) === Number\(leaf\)/,
  );
  assert.match(batch, /carriedPicks && Object\.keys\(carriedPicks\)\.length/);
  // 类目对不上就必须退回模板（pid 是类目维度的，套错类目会全废）
  assert.match(batch, /: productTemplate\.attrs \?\? \{\};/);
});
