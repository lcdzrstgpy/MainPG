import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 页面入口统一为 AiVideoShell；Phase 3 已删除 iframe 与桥接。
const pageShell = readFileSync(new URL("./AiVideoShell.tsx", import.meta.url), "utf8");
const settingsPage = readFileSync(new URL("./SettingsPage.tsx", import.meta.url), "utf8");
const workspaceShell = readFileSync(new URL("../../../app/layout/WorkspaceShell.tsx", import.meta.url), "utf8");
const tabsPersistence = readFileSync(new URL("../../../app/layout/workspaceTabsPersistence.ts", import.meta.url), "utf8");
const navConfig = readFileSync(new URL("../nav.ts", import.meta.url), "utf8");
test("shell has no iframe fallback and dispatches every path natively", () => {
  // Phase 3：iframe、原生路由开关与桥接消息都已删除。
  assert.doesNotMatch(pageShell, /AiVideoFrame/);
  assert.doesNotMatch(pageShell, /NATIVE_ROUTES/);
  assert.doesNotMatch(pageShell, new RegExp("mainpg:" + "ai-video"));
  // 所有一级入口与项目步骤都由原生页面接管。
  for (const marker of [
    /<StartPage /,
    /<ProjectsPage /,
    /<ProductsPage /,
    /<PresentersPage /,
    /<ClonePage /,
    /<BatchPage /,
    /<SettingsPage \/>/,
    /<ProjectScriptPage /,
    /<ProjectAssetsPage /,
    /<ProjectVideoPage /,
    /<ProjectExportPage /,
    /<ProjectProductionPage /,
    /<ProjectTranscriptPage /,
  ]) {
    assert.match(pageShell, marker);
  }
  assert.match(pageShell, /export function isAiVideoPath/);
  // 项目详情深层路径也要能进入页签持久化白名单。
  assert.match(pageShell, /\/project\/\$\{string\}\/\$\{AiVideoProjectStep\}/);
});

test("AI video path lives on the workspace tab so tab switches and refresh keep the page", () => {
  assert.match(workspaceShell, /aiVideoPath/);
  assert.match(workspaceShell, /path=\{aiVideoPath\}/);
  assert.match(workspaceShell, /onPathChange=\{setAiVideoPath\}/);
  assert.match(workspaceShell, /tab\.moduleId === "ai_video" \? \{ \.\.\.tab, aiVideoPath \}/);
  assert.match(tabsPersistence, /"aiVideoPath"/);
});

test("settings covers providers, LLM test and model list", () => {
  assert.match(pageShell, /<SettingsPage \/>/);
  assert.match(settingsPage, /useSettingsStore/);
  assert.match(settingsPage, /setProvider\(/);
  assert.match(settingsPage, /testLlm\(/);
  assert.match(settingsPage, /listLlmModels\(/);
  // 旧内嵌页的 Key 无法自动迁移，必须给出明确提示。
  assert.match(settingsPage, /需要重新填写 Key/);
});

test("projects page lists projects and works, confirms before deleting and resumes at the right step", () => {
  const projectsPage = readFileSync(new URL("./ProjectsPage.tsx", import.meta.url), "utf8");
  assert.match(projectsPage, /listProjects\(\)/);
  assert.match(projectsPage, /listWorks\(\)/);
  assert.match(projectsPage, /deleteProject\(project\.id\)/);
  // 删除是破坏性操作：必须先 confirm，成功后才从列表移除。
  assert.match(projectsPage, /window\.confirm\(/);
  // 已出片 → 导出页；视频/合成 → 视频页；素材 → 素材页；其余 → 脚本页。
  assert.match(projectsPage, /status === "done"\) return "export"/);
  assert.match(projectsPage, /status === "composing" \|\| status === "video"\) return "video"/);
  assert.match(projectsPage, /status === "assets"\) return "assets"/);
  // 成片地址必须走同源代理，下载走 ?download=1。
  assert.match(projectsPage, /aiVideoMediaUrl/);
  assert.match(projectsPage, /download=1/);
});

test("products page keeps a review gate for link imports and hands products to the start page", () => {
  const productsPage = readFileSync(new URL("./ProductsPage.tsx", import.meta.url), "utf8");
  // 链接导入只解析、不建项目；图片按预生成 id 落到商品库目录。
  assert.match(productsPage, /ingestProduct\(\{ url, createProject: false, libraryProductId \}\)/);
  assert.match(productsPage, /uploadProductImages\(/);
  // 商品图上限 5 张，保存时只上传新选文件，已有服务端路径原样保留。
  assert.match(productsPage, /MAX_PRODUCT_IMAGES = 5/);
  assert.match(productsPage, /pendingFiles/);
  // 删除有二次确认；做视频只写一次性预填并跳 /start。
  assert.match(productsPage, /window\.confirm\(/);
  assert.match(productsPage, /removeProduct\(product\.id\)/);
  assert.match(productsPage, /writeStartPrefill\(window\.localStorage, \{ productId: product\.id \}\)/);
  assert.match(productsPage, /onNavigate\("\/start"\)/);
});

test("start page consumes the product handoff and counts a generated video", () => {
  const startPage = readFileSync(new URL("./StartPage.tsx", import.meta.url), "utf8");
  assert.match(startPage, /consumeStartPrefill\(window\.localStorage\)/);
  assert.match(startPage, /inputMode: "product-library"/);
  assert.match(startPage, /productLibraryActions\.incrementVideoCount/);
});

test("presenters page manages characters, generates sheets and previews/deletes them", () => {
  const presentersPage = readFileSync(new URL("./PresentersPage.tsx", import.meta.url), "utf8");
  const manager = readFileSync(new URL("../components/PresenterManager.tsx", import.meta.url), "utf8");
  assert.match(presentersPage, /<PresenterManager \/>/);
  assert.match(manager, /useCharacterStore/);
  assert.match(manager, /generateCharacterSheet\(/);
  assert.match(manager, /resolveDefaultModelTarget\(/);
  // 生成结果替换第一张参考图；预览弹层可直接删除定妆照。
  assert.match(manager, /referenceImages: \[result\.url/);
  assert.match(manager, /deleteSheet\(/);
  assert.match(manager, /默认/);
  // 角色删除也要二次确认。
  assert.match(manager, /window\.confirm\(/);
});

test("start page binds a presenter and sends it with the script request", () => {
  const startPage = readFileSync(new URL("./StartPage.tsx", import.meta.url), "utf8");
  const form = readFileSync(new URL("../components/CreationBriefForm.tsx", import.meta.url), "utf8");
  assert.match(startPage, /useCharacterStore\(\)/);
  assert.match(startPage, /characters=\{characters\.map/);
  assert.match(startPage, /characterId: brief\.characterId/);
  assert.match(startPage, /character: \{/);
  assert.match(form, /values\.videoMode === "live_presenter"/);
  assert.match(form, /ai-video-character-select/);
});

test("clone page analyzes a real reference and falls back to the generic structure for URL-only input", () => {
  const clonePage = readFileSync(new URL("./ClonePage.tsx", import.meta.url), "utf8");
  assert.match(clonePage, /analyzeReplicateVideo\(referenceFile\)/);
  assert.match(clonePage, /setStoryboards\(GENERIC_STRUCTURE\)/);
  assert.match(clonePage, /GENERIC_STRUCTURE: StoryboardCard\[\]/);
  // 商品图先落盘，再写交接；商品名/卖点/节奏骨架都必须带过去。
  assert.match(clonePage, /uploadImages\(temporaryProjectId/);
  assert.match(clonePage, /writeCloneHandoff\(window\.localStorage/);
  assert.match(clonePage, /referenceStructure: analysis\.referenceStructure/);
  assert.match(clonePage, /onNavigate\("\/start"\)/);
});

test("start page consumes the clone handoff with product info and rhythm skeleton", () => {
  const startPage = readFileSync(new URL("./StartPage.tsx", import.meta.url), "utf8");
  assert.match(startPage, /consumeCloneHandoff\(window\.localStorage\)/);
  assert.match(startPage, /productName: handoff\.productName/);
  assert.match(startPage, /cloneRef\.current/);
  assert.match(startPage, /clearCloneHandoffStorage\(window\.localStorage\)/);
});

test("batch page runs a three-way pool, persists every stage and resumes interrupted jobs", () => {
  const batchPage = readFileSync(new URL("./BatchPage.tsx", import.meta.url), "utf8");
  // 并发池固定 3；单项失败只写回自己，不影响其它 worker。
  assert.match(batchPage, /const CONCURRENCY = 3/);
  assert.match(batchPage, /Math\.min\(CONCURRENCY, workItems\.length\)/);
  assert.match(batchPage, /reportItem\(ctx, product\.id, \{ status: "failed", error: message \}\)/);
  // 每个阶段都写回 /api/batch，刷新后可继续。
  assert.match(batchPage, /getBatchJob\(\{ active: true \}\)/);
  // 刷新/关闭导致的请求中止不能写成失败，否则 active 任务消失、无法续跑。
  assert.match(batchPage, /addEventListener\("beforeunload", markUnloading\)/);
  assert.match(batchPage, /if \(unloadingRef\.current\) return;/);
  assert.match(batchPage, /!unloadingRef\.current/);
  assert.match(batchPage, /createBatchJob\(/);
  assert.match(batchPage, /updateBatchItem\(/);
  assert.match(batchPage, /updateBatchJob\(/);
  // 续跑复用原任务配置与已产出的项目/合成。
  assert.match(batchPage, /config\.strategy/);
  assert.match(batchPage, /resume\.projectId/);
  assert.match(batchPage, /resume\.compositionId/);
  // 合成按 compositionId 精确轮询。
  assert.match(batchPage, /COMPOSE_POLL_INTERVAL_MS = 2500/);
  assert.match(batchPage, /getCompose\(projectId, compositionId\)/);
  // 批量结束后的同质化自检。
  assert.match(batchPage, /getHomogeneity\(/);
});

test("settings page completes TTS, characters, brand, model catalog and diagnostics", () => {
  // 试听：付费优先，未配置时自动落到免费 Edge 音色。
  assert.match(settingsPage, /synthesizePaidTts\(/);
  assert.match(settingsPage, /synthesizeFreeTts\(/);
  assert.match(settingsPage, /isPaidTTSReady\(/);
  // 角色与品牌共用本地 store；水印配置在此维护，导出页（T2.4）读取。
  assert.match(settingsPage, /<PresenterManager \/>/);
  assert.match(settingsPage, /useBrandStore\(\)/);
  assert.match(settingsPage, /updateWatermark\(/);
  // 模型目录刷新 + 平台 Key 探针。
  assert.match(settingsPage, /listAiModels\(/);
  assert.match(settingsPage, /testAiProvider\(/);
  // 生成参数。
  assert.match(settingsPage, /setImageParams\(/);
  assert.match(settingsPage, /setVideoParams\(/);
  // 诊断读取与复制。
  assert.match(settingsPage, /getSidecarHealth\(\)/);
  assert.match(settingsPage, /navigator\.clipboard\.writeText/);
});

test("project detail dispatches all six steps natively", () => {
  const shellBlock = readFileSync(new URL("./AiVideoShell.tsx", import.meta.url), "utf8");
  assert.match(shellBlock, /PROJECT_SCRIPT_ROUTE/);
  assert.match(shellBlock, /PROJECT_ASSETS_ROUTE/);
  assert.match(shellBlock, /PROJECT_VIDEO_ROUTE/);
  assert.match(shellBlock, /PROJECT_EXPORT_ROUTE/);
  assert.match(shellBlock, /PROJECT_PRODUCTION_ROUTE/);
  assert.match(shellBlock, /PROJECT_TRANSCRIPT_ROUTE/);
  assert.match(shellBlock, /<ProjectScriptPage projectId=\{projectScript\[1\]\}/);
  assert.match(shellBlock, /<ProjectAssetsPage projectId=\{projectAssets\[1\]\}/);
  assert.match(shellBlock, /<ProjectVideoPage projectId=\{projectVideo\[1\]\}/);
  assert.match(shellBlock, /<ProjectExportPage projectId=\{projectExport\[1\]\}/);
  assert.match(shellBlock, /<ProjectProductionPage projectId=\{projectProduction\[1\]\}/);
  assert.match(shellBlock, /<ProjectTranscriptPage projectId=\{projectTranscript\[1\]\}/);
});

test("assets page locks takes, fills stock and runs image-to-video with recovery", () => {
  const page = readFileSync(new URL("./ProjectAssetsPage.tsx", import.meta.url), "utf8");
  // 锁定 = PATCH selected；批量只处理未锁定镜头。
  assert.match(page, /selectAsset\(projectId, asset\.id\)/);
  assert.match(page, /rows\.filter\(\(row\) => !row\.locked\)/);
  assert.match(page, /fillStock\(projectId, \{ source: "all", mediaType: "auto" \}\)/);
  // 逐镜生图 → 落库；商品图镜头直接复用商品图。
  assert.match(page, /generateAiImage\(\{/);
  assert.match(page, /saveAsset\(projectId, \{ shotId, sourceUrl: productImage, type: "product_image" \}\)/);
  // 以图生视频：带上 projectId/shotId 与关键帧，可中止等待。
  assert.match(page, /mode: "image-to-video"/);
  assert.match(page, /imageUrl: keyframe/);
  assert.match(page, /new AbortController\(\)/);
  assert.match(page, /取消等待/);
  // 超时任务不重提，走 /api/ai/tasks + /api/ai/video/task 恢复。
  assert.match(page, /listAiTasks\(\{ projectId, active: true \}\)/);
  assert.match(page, /queryVideoTask\(\{/);
  // 页头策略提示。
  assert.match(page, /strategy === "draft"/);
  assert.match(page, /native-film/);
});

test("assets page ships the local material library and diagnosis retake", () => {
  const page = readFileSync(new URL("./ProjectAssetsPage.tsx", import.meta.url), "utf8");
  // 本地素材库：GET /materials、上传 1–12 个、套用为 user_upload、按本地素材填充未锁定镜头。
  assert.match(page, /<LocalMaterialLibrary/);
  assert.match(page, /uploadMaterial\(projectId, file\)/);
  assert.match(page, /sourceUrl: material\.url/);
  assert.match(page, /fillStock\(projectId, \{ source: "local", mediaType: "auto" \}\)/);
  // 诊断重拍：单一症状补丁，补丁已存在时不重复提交付费任务。
  assert.match(page, /applyRetakePatch\(prompt, retake\)/);
  assert.match(page, /RETAKE_SYMPTOMS\.map/);
  assert.match(page, /patched\.applied/);
  // 九宫格关键帧 + 整片 dryRun（只估算，不提交）。
  assert.match(page, /generateStoryboardGrid\(projectId, \{/);
  assert.match(page, /previewFilm\(projectId, \{/);
  assert.match(page, /filmPreview\.estimate/);
});

test("video page composes by compositionId and survives leaving the page", () => {
  const page = readFileSync(new URL("./ProjectVideoPage.tsx", import.meta.url), "utf8");
  // 合成：提交后按返回的 compositionId 精确轮询，不用「最新一条」。
  assert.match(page, /composeVideo\(projectId, input\)/);
  assert.match(page, /getCompose\(projectId, compositionId\)/);
  // 合成中离开页面：compositionId 落 localStorage，回来继续查询。
  assert.match(page, /mainpg\.ai-video\.compose\./);
  assert.match(page, /readPending\(projectId\)/);
  assert.match(page, /localStorage\.setItem\(pendingKey\(projectId\)/);
  // 合成设置：画幅/分辨率/渲染档/字幕/BGM/配音。
  assert.match(page, /renderPreset: config\.renderPreset/);
  assert.match(page, /captionPreset: config\.captionPreset/);
  assert.match(page, /freeBgm: true/);
  assert.match(page, /resolveTTSConfig\(tts, providers\)/);
  assert.match(page, /freeTts: \{ enabled: true, voice: config\.freeVoice \}/);
  assert.match(page, /resolveVoiceoverRequest\(/);
  // 成片版本、时间线音频报告与 BGM 上传。
  assert.match(page, /listCompositions\(projectId\)/);
  assert.match(page, /getCompositionTimeline\(projectId, selectedId\)/);
  assert.match(page, /summarizeVoiceReport\(timeline\?\.timeline\)/);
  assert.match(page, /uploadBgm\(projectId, file\)/);
  assert.match(page, /synthesizeFreeTts\(/);
  // 变体矩阵：钩子改写 + 每个版本独立轮询。
  assert.match(page, /buildHookVariants\(/);
  assert.match(page, /voiceoverOverrides: \[\{ shotId: hookShot\.shotId, voiceover: hookShot\.voiceover \}\]/);
});

test("export page groups versions, exports per platform and surfaces QC/gate/credits", () => {
  const page = readFileSync(new URL("./ProjectExportPage.tsx", import.meta.url), "utf8");
  // 成片按策略分组 + 选中版本决定导出/质检/门禁。
  assert.match(page, /groupCompositionsByStrategy\(compositions, strategy\)/);
  assert.match(page, /exportForPlatform\(projectId, \{ compositionId: selected\.id, platform \}\)/);
  // 下载与复制链接。
  assert.match(page, /copyText\(absoluteMediaUrl\)/);
  assert.match(page, /download=\{selected\?\.fileName/);
  // 发布文案、质检、门禁、授权清单 Markdown。
  assert.match(page, /generatePublishCopy\(\{/);
  // LLM 失败/未配置时回退到免 Key 模板文案。
  assert.match(page, /buildPublishPack\(\{/);
  assert.match(page, /模板文案（不调用 LLM）/);
  assert.match(page, /runQc\(projectId, selected\.id\)/);
  assert.match(page, /runGate\(projectId, selected\.id\)/);
  assert.match(page, /getCredits\(projectId\)/);
  assert.match(page, /downloadCreditsMarkdown\(projectId\)/);
  // 更多产出与数据回填。
  assert.match(page, /generateCover\(projectId/);
  assert.match(page, /generateCarousel\(projectId\)/);
  assert.match(page, /generateShopQr\(projectId\)/);
  assert.match(page, /generateEndCard\(projectId\)/);
  assert.match(page, /generateContactSheet\(projectId, wantProxy\)/);
  assert.match(page, /generateNativeFeel\(projectId, \{/);
  assert.match(page, /generateDub\(projectId, \{/);
  assert.match(page, /recordMetrics\(projectId, \{/);
  // 品牌水印读取同一份 store。
  assert.match(page, /useBrandStore\(\)/);
  assert.match(page, /brand\.watermark\.enabled/);
});

test("production console prices the workflow and gates quality decisions", () => {
  const page = readFileSync(new URL("./ProjectProductionPage.tsx", import.meta.url), "utf8");
  // 成本卡 + 工作流开关落库。
  assert.match(page, /estimateProduction\(\{/);
  assert.match(page, /buildWorkflowPlan\(\{/);
  assert.match(page, /patchProduction\(projectId, \{ productionWorkflow: next \}\)/);
  // 创意意图 / Visual Bible。
  assert.match(page, /patchProduction\(projectId, \{ creativeIntent: nextIntent, visualBible: nextBible \}\)/);
  // 质量裁决：评估、人工通过/驳回、切换选中。
  assert.match(page, /getQuality\(projectId\)/);
  assert.match(page, /evaluateQuality\(projectId, \{/);
  assert.match(page, /decideQuality\(projectId, \{ reviewId: candidate\.latestReview\.id, decision \}\)/);
  assert.match(page, /selectAsset\(projectId, candidate\.id\)/);
  // 修复：先预演，再显式确认后付费执行。
  assert.match(page, /previewRepair\(projectId, \{/);
  assert.match(page, /planHash: repair\.preview\.summary\.planHash/);
  assert.match(page, /confirmed: true/);
  assert.match(page, /window\.confirm/);
  // 模型路由、母版与快照、诊断。
  assert.match(page, /listAiModels\(\{ providers: enabled, mediaType: "video" \}\)/);
  assert.match(page, /routeModel\(candidates, \{ mode: "image-to-video", goal: routingGoal \}\)/);
  assert.match(page, /analyzeMastering\(projectId, \{/);
  assert.match(page, /renderMastering\(projectId, \{/);
  assert.match(page, /action: "snapshot"/);
  assert.match(page, /diagnoseGenerationFailure\(overview\.latestFailure\.error\)/);
});

test("transcript page runs resumable local ASR and non-destructive edits", () => {
  const page = readFileSync(new URL("./ProjectTranscriptPage.tsx", import.meta.url), "utf8");
  // 导入 / 列表 / 本地 Worker 分块与断点续转。
  assert.match(page, /uploadMedia\(projectId, file/);
  assert.match(page, /listMedia\(projectId\)/);
  assert.match(page, /new Worker\(new URL\("\.\.\/\.\.\/\.\.\/workers\/asr\.worker\.ts"/);
  assert.match(page, /action: "start", model, language, resume: true/);
  assert.match(page, /action: "checkpoint", chunk, processedSeconds: nextProcessed/);
  assert.match(page, /action: "complete", transcript: finalTranscript/);
  assert.match(page, /action: "cancel"/);
  // 词级编辑、字幕校正与导出。
  assert.match(page, /toggleWord\(word\.id\)/);
  assert.match(page, /findTranscriptPhrase\(transcript, correctionQuery\.trim\(\), 1\)/);
  assert.match(page, /exportDraft\("srt"\)/);
  assert.match(page, /exportDraft\("vtt"\)/);
  assert.match(page, /exportTimelineDraft\(projectId, selected\.id, format, plan\)/);
  // 非破坏剪辑：预演 → 输出 → 取消/重试；片段建议与批量输出。
  assert.match(page, /previewTranscriptEdit\(projectId, selected\.id, \{/);
  assert.match(page, /applyTranscriptEdit\(projectId, selected\.id, \{/);
  assert.match(page, /cancelTranscriptEdit\(projectId, selected\.id, editId\)/);
  assert.match(page, /retryTranscriptEdit\(projectId, selected\.id, editId\)/);
  assert.match(page, /listTranscriptClips\(projectId, selected\.id, \{/);
  assert.match(page, /applyTranscriptBatch\(projectId, selected\.id, \{/);
});

test("script page switches versions, saves shots and applies judge rewrites", () => {
  const page = readFileSync(new URL("./ProjectScriptPage.tsx", import.meta.url), "utf8");
  assert.match(page, /patchScript\(projectId, \{ selectedScriptId: script\.id \}\)/);
  assert.match(page, /patchScript\(projectId, \{ scriptId: selected\.id, shotTexts \}\)/);
  assert.match(page, /judgeScript\(projectId, \{/);
  // 只应用非口味级改写，口味级仅展示。
  assert.match(page, /if \(rewrite\.tier === "taste"\) continue;/);
  assert.match(page, /应用非口味级改写/);
});

test("script page exposes a resumable free pipeline", () => {
  const page = readFileSync(new URL("./ProjectScriptPage.tsx", import.meta.url), "utf8");
  assert.match(page, /getPipelineRun\(projectId\)/);
  assert.match(page, /startPipelineRun\(projectId, \{/);
  assert.match(page, /resume: true/);
  assert.match(page, /PIPELINE_POLL_INTERVAL_MS = 2500/);
  assert.match(page, /从断点续跑/);
});

test("script page gates the native film behind dryRun and explicit confirmation", () => {
  const page = readFileSync(new URL("./ProjectScriptPage.tsx", import.meta.url), "utf8");
  assert.match(page, /previewFilm\(projectId, \{/);
  assert.match(page, /setFilmPreview\(preview\)/);
  assert.match(page, /generateFilm\(projectId, \{/);
  assert.match(page, /acknowledgeOverCap: true/);
  // 确认勾选前生成按钮不可用，且必须展示估算与参考图配额。
  assert.match(page, /disabled=\{!filmConfirmed/);
  assert.match(page, /filmPreview\.estimate/);
  assert.match(page, /referenceQuota\.ok/);
  assert.match(page, /generateStoryboardGrid\(projectId, \{/);
  assert.match(page, /generateCharacterSheet\(\{/);
});

test("start page creates a project, uploads images and hands the project off to the script page", () => {
  const startPage = readFileSync(new URL("./StartPage.tsx", import.meta.url), "utf8");
  const form = readFileSync(new URL("../components/CreationBriefForm.tsx", import.meta.url), "utf8");
  // 唯一创建链路：建项目 → 传图 → 生成脚本 → 跳到项目脚本页。
  assert.match(startPage, /createProject\(/);
  assert.match(startPage, /uploadImages\(/);
  assert.match(startPage, /generateScript\(/);
  assert.match(startPage, /generateTopicScript\(/);
  assert.match(startPage, /onNavigate\(`\/project\/\$\{pending\.projectId\}\/script`\)/);
  // 409 选风格不是失败：保留待重试请求并弹层。
  assert.match(startPage, /parseStyleRequirement\(cause\.status, cause\.payload\)/);
  assert.match(startPage, /<StyleChoicePrompt/);
  // 五种来源都进入同一个共享表单（upload 使用通用商品图选择器）。
  assert.match(form, /INPUT_MODE_OPTIONS\.map/);
  for (const mode of ["link", "topic", "product-library", "clone"]) {
    assert.match(form, new RegExp(`inputMode === "${mode}"`));
  }
});
