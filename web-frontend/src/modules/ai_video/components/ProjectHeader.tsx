import type { Project } from "../api/types";
import { outputStrategyLabel } from "../lib/creation-brief-defaults";
import { resolveStepperSteps, type StepperStepKey } from "../lib/stepper-flow-policy";
import { slidingIndicatorStyle, useSlidingIndicator } from "../../../shared/hooks/useSlidingIndicator";

const STEP_LABELS: Record<StepperStepKey, string> = {
  script: "脚本",
  assets: "素材",
  video: "视频",
  export: "导出",
};

/** stepper-flow-policy 的 hint 是 i18n key；原生前端只保留中文，就近映射。 */
const STEP_HINTS: Record<string, string> = {
  stepDraftAssetsHint: "草稿策略可直接跳过素材",
  stepDraftVideoHint: "草稿策略在合成页直接出片",
  stepFilmScriptHint: "整片策略先确认脚本",
  stepFilmAssetsHint: "整片用分镜网格作参考",
  stepFilmVideoHint: "整片在视频页生成",
};

const STATUS_LABELS: Record<Project["status"], string> = {
  draft: "草稿",
  scripting: "脚本中",
  assets: "素材中",
  video: "视频中",
  composing: "合成中",
  done: "已出片",
};

type ProjectHeaderStep = StepperStepKey | "production" | "transcript" | null;

type ProjectHeaderProps = {
  project: Project;
  /** null = 不点亮任何步骤；附加工具页用 production / transcript 点亮对应胶囊。 */
  step: ProjectHeaderStep;
  onNavigate: (path: string) => void;
};

/** 项目流水线上下文：返回入口 + 标题 + 四步胶囊（制作控制台/逐字稿作为附加工具）。 */
export function ProjectHeader({ project, step, onNavigate }: ProjectHeaderProps) {
  const strategy = project.creationBrief?.outputStrategy ?? null;
  const steps = resolveStepperSteps(strategy);
  // 四段共用一块滑块：选中态不是每个按钮各画一层底色，而是同一块视图在两个 frame 之间插值。
  const { containerRef, rect, ready } = useSlidingIndicator<HTMLDivElement>(":scope > button.is-current", [step, steps.length]);
  const activeHint = STEP_HINTS[steps.find((item) => item.key === step)?.hint ?? ""];
  const stepKey = step === "production" || step === "transcript" ? null : step;

  return (
    <header className="ai-video-project-header">
      <div className="ai-video-project-head-main">
        <button type="button" className="ai-video-project-back" onClick={() => onNavigate("/projects")}>
          ← 我的项目
        </button>
        <div>
          <p className="ai-video-eyebrow">AI 视频项目</p>
          <h2>{project.name || project.productName || "未命名项目"}</h2>
          <p className="ai-video-project-meta">
            {project.productName && <span>{project.productName}</span>}
            {strategy && <span>{outputStrategyLabel(strategy)}</span>}
            <span>{STATUS_LABELS[project.status]}</span>
          </p>
        </div>
        <span className={`ai-video-service-badge${project.status === "done" ? " is-ready" : ""}`}>
          {STATUS_LABELS[project.status]}
        </span>
      </div>

      <nav className="ai-video-stepper" aria-label="项目步骤">
        <div
          className="ai-video-stepper-track"
          ref={containerRef}
          data-ready={ready ? "true" : "false"}
        >
          {rect && (
            <span className="ai-video-stepper-thumb" style={slidingIndicatorStyle(rect)} aria-hidden="true" />
          )}
          {steps.map((item, index) => {
            const hint = STEP_HINTS[item.hint ?? ""];
            return (
              <button
                key={item.key}
                type="button"
                className={`ai-video-step${stepKey === item.key ? " is-current" : ""}${item.status === "optional" ? " is-optional" : ""}`}
                aria-current={stepKey === item.key ? "step" : undefined}
                title={hint}
                onClick={() => onNavigate(`/project/${project.id}/${item.key}`)}
              >
                <span>{index + 1}</span>
                <strong>{STEP_LABELS[item.key]}</strong>
                {item.status === "optional" && <em>可选</em>}
              </button>
            );
          })}
        </div>

        <div className="ai-video-stepper-tools">
          <button
            type="button"
            className={`ai-video-step is-tool${step === "production" ? " is-current" : ""}`}
            aria-current={step === "production" ? "page" : undefined}
            onClick={() => onNavigate(`/project/${project.id}/production`)}
          >
            <span>⚙</span>
            <strong>制作控制台</strong>
          </button>
          <button
            type="button"
            className={`ai-video-step is-tool${step === "transcript" ? " is-current" : ""}`}
            aria-current={step === "transcript" ? "page" : undefined}
            onClick={() => onNavigate(`/project/${project.id}/transcript`)}
          >
            <span>✎</span>
            <strong>逐字稿</strong>
          </button>
        </div>
      </nav>

      {activeHint && (
        <p key={activeHint} className="ai-video-stepper-hint">
          {activeHint}
        </p>
      )}
    </header>
  );
}
