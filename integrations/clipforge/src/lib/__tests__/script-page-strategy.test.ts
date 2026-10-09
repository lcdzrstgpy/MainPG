import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveScriptFlowPolicy } from "@/lib/script-flow-policy";
import {
  NEEDS_EXPLICIT_STYLE_CODE,
  parseStyleRequirement,
} from "@/components/project-creation/script-style-requirement";

/**
 * 仓库没有组件渲染器（无 @testing-library/react），脚本页的行为用「纯函数直测 + 源码契约」断言，
 * 与 src/components/project-creation/__tests__/start-entry.test.ts 的既有风格一致。
 *
 * 覆盖计划 Task 2 / Task 3（脚本页策略门控收敛到 resolveScriptFlowPolicy，genPref 删除）。
 * 纯函数判据本身由 script-flow-policy.test.ts 直测，这里只钉页面与组件的接线。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const scriptPage = read("src/app/project/[id]/script/page.tsx");
const simpleModeActions = read("src/components/script/simple-mode-actions.tsx");
const requirementModule = read("src/components/project-creation/script-style-requirement.ts");
const promptModule = read("src/components/project-creation/style-choice-prompt.tsx");

/** ?auto=1 自动启动那一段 effect 的主体（到依赖数组为止）。 */
const autoStartEffectBody = () => {
  const fromComment = scriptPage.slice(scriptPage.indexOf("?auto=1 fresh start"));
  return fromComment.slice(0, fromComment.indexOf("}, [autoMode"));
};

/** autoFinish 触发器函数体（到服务端续跑注释为止）。 */
const autoFinishBody = () => {
  const from = scriptPage.slice(scriptPage.indexOf("const autoFinish"));
  return from.slice(0, from.indexOf("// On entry"));
};

/** 转手动按钮完整片段（含 onClick 处理器，到 </Button> 为止）。 */
const manualButtonRegion = () => {
  const labelIdx = scriptPage.indexOf("autoModeManual");
  const btnIdx = scriptPage.lastIndexOf("<Button", labelIdx);
  return scriptPage.slice(btnIdx, scriptPage.indexOf("</Button>", labelIdx) + "</Button>".length);
};

const strategyBlock = (marker: string, endMarker: string) =>
  scriptPage.slice(scriptPage.indexOf(marker), scriptPage.indexOf(endMarker));

describe("resolveScriptFlowPolicy 接入（design §7.4）", () => {
  it("页面放弃 shouldAutoStartPipeline，改从 resolveScriptFlowPolicy 取判据", () => {
    expect(scriptPage).not.toMatch(/shouldAutoStartPipeline/);
    expect(scriptPage).toMatch(/resolveScriptFlowPolicy/);
    // 语义保持不变：draft 才允许自动启动
    expect(resolveScriptFlowPolicy({ outputStrategy: "draft", autoMode: true }).allowAutoStart).toBe(true);
    expect(resolveScriptFlowPolicy({ outputStrategy: "controlled-motion", autoMode: true }).allowAutoStart).toBe(false);
  });

  it("自动启动判据是 outputStrategy 与 autoMode 的组合（并带 briefState）", () => {
    const body = autoStartEffectBody();
    expect(body).toMatch(/resolveScriptFlowPolicy\(\{ outputStrategy, autoMode, briefState \}\)/);
    expect(body).toMatch(/if \(!flow\.allowAutoStart\) return/);
    // 门控在前、启动在后：非 draft 策略在到达 autoFinish 之前就返回了
    expect(body.indexOf("resolveScriptFlowPolicy")).toBeLessThan(body.indexOf("autoFinish()"));
  });

  it("简报还没读到时门控不生效，避免把受控动态误当成旧项目自动跑", () => {
    const body = autoStartEffectBody();
    expect(body).toMatch(/!briefLoaded/);
    expect(scriptPage).toMatch(/\[autoMode, autoModeTriggered, loading, briefLoaded, outputStrategy, briefState, currentScript, pipelineChecked, resumableRun\]/);
    // genPref 已彻底移除（无产生者，不发明替代参数）
    expect(body).not.toMatch(/genPref/);
  });

  it("autoFinish 自带 allowFreeChain 门控：付费策略绝不被这一个按钮降级成静态草稿", () => {
    const body = autoFinishBody();
    expect(body).toMatch(/resolveScriptFlowPolicy/);
    expect(body).toMatch(/if \(!flow\.allowFreeChain\) return/);
    expect(body).toMatch(/startPipeline\(false\)/);
  });

  it("接管进度卡片只在免费链确实适用时出现", () => {
    expect(scriptPage).toMatch(/flow\.allowAutoStart && !autoFinishError && \(autoFinishing \|\| !autoModeTriggered\)/);
  });

  it("genPref 从页面彻底删除，不再读取 ?gen=ai", () => {
    expect(scriptPage).not.toMatch(/genPref/);
    expect(scriptPage).not.toMatch(/qs\.get\("gen"\)/);
  });
});

describe("simple 模式：动作区委托给 SimpleModeActions 组件", () => {
  it("页面导入并渲染 SimpleModeActions，自身不再内联双按钮", () => {
    expect(scriptPage).toMatch(/import \{ SimpleModeActions \} from "@\/components\/script\/simple-mode-actions"/);
    expect(scriptPage).toMatch(/<SimpleModeActions/);
  });

  it("组件按策略渲染四种形态，且不直接发起任何 fetch/API", () => {
    expect(simpleModeActions).not.toMatch(/fetch\(|api\//);
    expect(simpleModeActions).toMatch(/policy\.strategy/);
    // controlled-motion 主操作 = 进素材页逐镜动态链接
    expect(simpleModeActions).toMatch(/\$\{id\}\/assets/);
    expect(simpleModeActions).toMatch(/进入素材页生成逐镜动态/);
    // draft 走 autoFinish、native-film 走 runAiFilm、legacy 保留双入口并标注策略未记录
    expect(simpleModeActions).toMatch(/autoFinish/);
    expect(simpleModeActions).toMatch(/runAiFilm/);
    expect(simpleModeActions).toMatch(/策略未记录/);
  });

  it("每个入口都受 policy.show* 旗标约束：简报未读定时不给任何可执行动作", () => {
    expect(simpleModeActions).toMatch(/policy\.strategy === "draft" && policy\.showDraftAction/);
    expect(simpleModeActions).toMatch(/policy\.strategy === "controlled-motion" && policy\.showControlledMotionAction/);
    expect(simpleModeActions).toMatch(/policy\.strategy === "native-film" && policy\.showNativeFilmAction/);
    expect(simpleModeActions).toMatch(/policy\.strategy === "legacy" && \(policy\.showDraftAction \|\| policy\.showNativeFilmAction\)/);
    // legacy 且双入口都被关（简报失败）时只显示「策略未知」提示，无任何按钮动作
    expect(simpleModeActions).toMatch(/!policy\.showDraftAction && !policy\.showNativeFilmAction/);
    expect(simpleModeActions).toMatch(/t\("strategyUnknown"\)/);
    expect(simpleModeActions).toMatch(/t\("strategyUnknownHint"\)/);
  });
});

describe("pro 模式：策略一致的非主操作", () => {
  it("免费快剪 / 素材链接 / 整片预览分别按 flow.show* 门控", () => {
    expect(scriptPage).toMatch(/flow\.showDraftAction/);
    expect(scriptPage).toMatch(/flow\.showControlledMotionAction/);
    expect(scriptPage).toMatch(/flow\.showNativeFilmAction/);
  });

  it("入口唯一化后，整片预览在导演模式仍留一个（工具栏 ✨）", () => {
    expect(scriptPage).toMatch(/flow\.showNativeFilmAction && \([\s\S]{0,600}onClick=\{runAiFilm\}/);
  });
});

describe("转手动（Task 3）：进入导演模式且任务继续运行", () => {
  it("onClick 同时切导演模式且绝不取消/停止/重提交流水线", () => {
    const region = manualButtonRegion();
    expect(region).toMatch(/setAutoMode\(false\);\s*setUiMode\("pro"\)/);
    expect(region).not.toMatch(/cancel|stopPipeline|abort|revoke/i);
    expect(region).not.toMatch(/startPipeline|autoFinish|api\//);
  });
});

describe("脚本页：出片策略停在脚本确认页（沿用？auto=1 不降级语义）", () => {
  it("controlled-motion：说明卡只解释策略，不再自带入口", () => {
    const block = strategyBlock('flow.strategy === "controlled-motion"', 'flow.strategy === "native-film"');
    expect(block).toMatch(/该策略不会自动启动免费静态流水线/);
    // 入口唯一化：说明卡里不再渲染可点击入口（素材页入口只留在小白动作区与导演模式工具栏）
    expect(block).not.toMatch(/\/assets/);
    expect(block).not.toMatch(/<Button|<Link/);
    expect(block).not.toMatch(/autoFinish\(\)|startPipeline\(/);
  });

  it("native-film：说明卡只解释策略与计费，不再重复渲染整片预览按钮", () => {
    const block = strategyBlock('flow.strategy === "native-film"', "// 重新生成 = 新版本");
    expect(block).toMatch(/该策略不会自动启动免费静态流水线/);
    expect(block).toMatch(/模型原生音频/);
    // 同一个动作不能在同一屏出现两次：说明卡不再渲染整片预览按钮
    expect(block).not.toMatch(/onClick=\{runAiFilm\}/);
    expect(block).not.toMatch(/<Button|<Link/);
    expect(block).not.toMatch(/autoFinish\(\)|startPipeline\(/);
  });
});

describe("脚本页：旧项目（creationBrief 为 null）兼容", () => {
  it("仍读取 ?auto=1 并明确说明这是旧项目", () => {
    expect(scriptPage).toMatch(/qs\.get\("auto"\) === "1"/);
    expect(scriptPage).toMatch(/旧项目：没有创作简报/);
    expect(scriptPage).toMatch(/断点恢复/);
  });

  it("简报读取完成（含读取失败）后标记 briefLoaded，门控不会永久等待", () => {
    expect(scriptPage).toMatch(/setBriefLoaded\(true\)/);
    expect(scriptPage).toMatch(/proj\.creationBrief \? sanitizeCreationBrief\(proj\.creationBrief\) : null/);
  });
});

describe("脚本页：ProjectHeader 与 409 显式风格", () => {
  it("把 outputStrategy 传给 ProjectHeader（步进器消费）", () => {
    expect(scriptPage).toMatch(
      /<ProjectHeader projectName=\{projectName \|\| t\("defaultProjectName"\)\} outputStrategy=\{outputStrategy\} \/>/
    );
  });

  it("页面持久展示 CreationBriefSummary", () => {
    expect(scriptPage).toMatch(
      /import \{ CreationBriefSummary \} from "@\/components\/project-creation\/creation-brief-summary"/
    );
    expect(scriptPage).toMatch(/<CreationBriefSummary/);
    expect(scriptPage).toMatch(/brief=\{creationBrief \?\? DEFAULT_CREATION_BRIEF\}/);
  });

  it("409 needs_explicit_style 交给 StyleChoicePrompt 呈现 candidates，不是失败", () => {
    expect(scriptPage).toMatch(
      /import \{ StyleChoicePrompt \} from "@\/components\/project-creation\/style-choice-prompt"/
    );
    expect(scriptPage).toMatch(/<StyleChoicePrompt/);
    expect(scriptPage).toMatch(/needs_explicit_style/);
    expect(scriptPage).toMatch(/candidates/);
    expect(scriptPage).toMatch(/parseStyleRequirement\(res\.status, e\)/);
    const generateBlock = scriptPage.slice(scriptPage.indexOf("const handleGenerate"), scriptPage.indexOf("/** 用户在 409"));
    expect(generateBlock.indexOf("setStylePrompt(requirement)")).toBeLessThan(generateBlock.indexOf("throw new Error("));
  });

  it("用户选完风格后写回简报（styleSource: explicit）再重试同一个请求", () => {
    expect(scriptPage).toMatch(/styleSource: "explicit"/);
    expect(scriptPage).toMatch(/await handleGenerate\(styleType\)/);
    expect(scriptPage).toMatch(/body: JSON\.stringify\(\{ creationBrief: nextBrief \}\)/);
  });

  it("共享解析器与提示组件确实是 409 的那条通路", () => {
    expect(requirementModule).toMatch(/needs_explicit_style/);
    expect(requirementModule).toMatch(/candidates/);
    expect(promptModule).toMatch(/candidates\.map/);
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: ["drama", "story"] })?.candidates)
      .toEqual(["drama", "story"]);
    expect(parseStyleRequirement(500, { code: NEEDS_EXPLICIT_STYLE_CODE })).toBeNull();
  });

  it("重新生成保留旧脚本记录，不静默覆盖", () => {
    expect(scriptPage).toMatch(/snapshotCurrentScripts/);
    expect(scriptPage).toMatch(/previousScriptsPanel/);
    expect(scriptPage).toMatch(/setPreviousScripts/);
  });
});

describe("纵深门禁：按钮显隐不是唯一防线", () => {
  const sliceFrom = (marker: string, endMarker: string) => {
    const from = scriptPage.slice(scriptPage.indexOf(marker));
    return from.slice(0, from.indexOf(endMarker));
  };

  it("startPipeline（autoFinish / 断点续跑 / 重新开始的共同入口）自带 allowFreeChain 门禁", () => {
    const body = sliceFrom("const startPipeline", "const autoFinish");
    expect(body).toMatch(/if \(!resolveScriptFlowPolicy\(\{ outputStrategy, uiMode, autoMode, briefState \}\)\.allowFreeChain\) return;/);
  });

  it("attachPipeline 拒绝为免费链禁用项目挂接，轮询中再查最新策略", () => {
    const body = sliceFrom("const attachPipeline", "const startPipeline");
    expect(body).toMatch(/if \(freeChainForbiddenRef\.current\) return;/);
    expect(body).toMatch(/if \(freeChainForbiddenRef\.current\) \{\s*setAutoFinishing\(false\);\s*return;/);
  });

  it("freeChainForbiddenRef = 简报失败或策略禁止，随两者更新（fail-closed 且不破坏 loading 期挂接）", () => {
    expect(scriptPage).toMatch(
      /freeChainForbiddenRef\.current =\s*briefLoadFailed \|\| !resolveScriptFlowPolicy\(\{ outputStrategy \}\)\.allowFreeChain;/
    );
    expect(scriptPage).toMatch(/\[outputStrategy, briefLoadFailed\]/);
  });

  it("runAiFilm 自带 showNativeFilmAction 门禁（含 briefState，不修改其九宫格/整片生成逻辑）", () => {
    const body = sliceFrom("const runAiFilm", "// Phase 2");
    expect(body).toMatch(/if \(!resolveScriptFlowPolicy\(\{ outputStrategy, briefState \}\)\.showNativeFilmAction\) return;/);
  });

  it("confirmAiFilm 在付费提交前同样校验 showNativeFilmAction（含 briefState）", () => {
    const body = sliceFrom("const confirmAiFilm", "// switching scripts");
    expect(body).toMatch(/if \(!resolveScriptFlowPolicy\(\{ outputStrategy, briefState \}\)\.showNativeFilmAction\) return;/);
  });

  it("autoFinish 函数体同样带 briefState 门控", () => {
    const body = autoFinishBody();
    expect(body).toMatch(/resolveScriptFlowPolicy\(\{ outputStrategy, uiMode, autoMode, briefState \}\)/);
  });
});

describe("简报读取失败 ≠ 旧项目（不得误启免费链）", () => {
  it("失败记 briefLoadFailed，策略解析带 briefState，自动启动被拦", () => {
    expect(scriptPage).toMatch(/const \[briefLoadFailed, setBriefLoadFailed\] = useState\(false\)/);
    expect(scriptPage).toMatch(/const briefState = briefLoadFailed \? "failed" : briefLoaded \? "loaded" : "loading"/);
    expect(scriptPage).toMatch(/resolveScriptFlowPolicy\(\{ outputStrategy, uiMode, autoMode, briefState \}\)/);
    expect(autoStartEffectBody()).toMatch(/resolveScriptFlowPolicy\(\{ outputStrategy, autoMode, briefState \}\)/);
  });

  it("失败态横幅明确策略未知且已阻止自动启动", () => {
    expect(scriptPage).toMatch(/创作简报读取失败：出片策略未知/);
    expect(scriptPage).toMatch(/已阻止免费草稿自动任务自动启动/);
  });
});
