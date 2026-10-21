import { useCallback, useEffect, useMemo, useState } from "react";

import { listAiModels } from "../api/endpoints/ai";
import { selectAsset } from "../api/endpoints/assets";
import { getCompose } from "../api/endpoints/compose";
import {
  analyzeMastering,
  decideQuality,
  evaluateQuality,
  executeRepair,
  getProduction,
  getQuality,
  patchProduction,
  previewRepair,
  renderMastering,
} from "../api/endpoints/production";
import type {
  CreativeIntent,
  MasteringAnalysis,
  ProductionOverview,
  QualityCandidate,
  QualityOverview,
  VideoRepairPreview,
  VisualBible,
  WorkflowStagePlan,
} from "../api/types";
import { ProjectHeader } from "../components/ProjectHeader";
import { enabledMainPgMediaProviders, resolveDefaultModelTarget } from "../lib/gen-params";
import { aiVideoMediaUrl } from "../lib/media-url";
import type { GenerationMode } from "../lib/provider-types";
import {
  buildWorkflowPlan,
  diagnoseGenerationFailure,
  estimateProduction,
  routeModel,
  type RouteCandidate,
  type RoutingGoal,
} from "../lib/production-system";
import { useSettingsStore } from "../state/settingsStore";

type Notice = { kind: "ok" | "error"; text: string } | null;

const STAGE_LABELS: Record<string, string> = {
  analyze: "素材分析",
  script: "脚本",
  judge: "台词评审",
  keyframes: "关键帧",
  motion: "动态",
  voice: "配音",
  compose: "合成",
  qc: "质检",
  release: "发布",
};

const GOALS: Array<{ id: RoutingGoal; label: string }> = [
  { id: "balanced", label: "均衡" },
  { id: "cost", label: "省钱" },
  { id: "speed", label: "更快" },
  { id: "quality", label: "质量优先" },
  { id: "consistency", label: "镜头连贯" },
];

function splitList(value: string): string[] {
  return [...new Set(value.split(/[,，、\n]/).map((item) => item.trim()).filter(Boolean))];
}

type ProductionPageProps = {
  projectId: string;
  onNavigate: (path: string) => void;
};

/** 制作控制台：成本卡、工作流开关、创意意图/Visual Bible、质量裁决、模型路由、母版、修复与诊断。 */
export function ProjectProductionPage({ projectId, onNavigate }: ProductionPageProps) {
  const { providers, defaultVideoModel, customModels, llm } = useSettingsStore();

  const [overview, setOverview] = useState<ProductionOverview | null>(null);
  const [quality, setQuality] = useState<QualityOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [saving, setSaving] = useState(false);

  const [intent, setIntent] = useState<CreativeIntent>({ subject: "" });
  const [bible, setBible] = useState<VisualBible>({
    characterAnchors: [],
    productAnchors: [],
    wardrobeAnchors: [],
    environmentAnchors: [],
    lightingAnchors: [],
    forbiddenChanges: [],
  });
  const [bibleDraft, setBibleDraft] = useState<Record<keyof VisualBible, string>>({
    characterAnchors: "",
    productAnchors: "",
    wardrobeAnchors: "",
    environmentAnchors: "",
    lightingAnchors: "",
    forbiddenChanges: "",
  });
  const [listDraft, setListDraft] = useState({ continuity: "", productConstraints: "", negative: "" });

  const [evaluatingId, setEvaluatingId] = useState<string | null>(null);
  const [repair, setRepair] = useState<{ candidateId: string; preview: VideoRepairPreview } | null>(null);
  const [repairBusy, setRepairBusy] = useState(false);

  const [routingGoal, setRoutingGoal] = useState<RoutingGoal>("balanced");
  const [routingBusy, setRoutingBusy] = useState(false);
  const [routing, setRouting] = useState<Array<{ id: string; name: string; score: number; reasons: string[] }>>([]);

  const [masteringBusy, setMasteringBusy] = useState(false);
  const [analysis, setAnalysis] = useState<MasteringAnalysis | null>(null);
  const [normalizeAudio, setNormalizeAudio] = useState(true);
  const [deflicker, setDeflicker] = useState(false);
  const [masteringUrl, setMasteringUrl] = useState<string | null>(null);

  const [snapshotLabel, setSnapshotLabel] = useState("");

  const load = useCallback(async () => {
    try {
      const [nextOverview, nextQuality] = await Promise.all([getProduction(projectId), getQuality(projectId)]);
      setOverview(nextOverview);
      setQuality(nextQuality);
      const nextIntent = nextOverview.creativeIntent ?? { subject: "" };
      const nextBible = nextOverview.visualBible ?? {
        characterAnchors: [],
        productAnchors: [],
        wardrobeAnchors: [],
        environmentAnchors: [],
        lightingAnchors: [],
        forbiddenChanges: [],
      };
      setIntent(nextIntent);
      setBible(nextBible);
      setBibleDraft({
        characterAnchors: nextBible.characterAnchors.join(", "),
        productAnchors: nextBible.productAnchors.join(", "),
        wardrobeAnchors: nextBible.wardrobeAnchors.join(", "),
        environmentAnchors: nextBible.environmentAnchors.join(", "),
        lightingAnchors: nextBible.lightingAnchors.join(", "),
        forbiddenChanges: nextBible.forbiddenChanges.join(", "),
      });
      setListDraft({
        continuity: (nextIntent.continuity ?? []).join(", "),
        productConstraints: (nextIntent.productConstraints ?? []).join(", "),
        negative: (nextIntent.negative ?? []).join(", "),
      });
      setLoadError("");
    } catch (cause) {
      setLoadError(cause instanceof Error ? cause.message : "制作控制台加载失败");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const workflow = useMemo<WorkflowStagePlan[]>(() => {
    if (overview?.workflow?.length) return overview.workflow;
    const strategy = overview?.project.creationBrief?.outputStrategy;
    return buildWorkflowPlan({
      hasSourceMedia: overview?.project.sourceType === "clone",
      aiKeyframes: strategy !== "draft",
      aiMotion: strategy !== "draft" && strategy !== "native-film",
      nativeAudio: overview?.project.creationBrief?.audioStrategy === "native-audio",
    });
  }, [overview?.project.creationBrief?.audioStrategy, overview?.project.creationBrief?.outputStrategy, overview?.project.sourceType, overview?.workflow]);

  const cost = useMemo(
    () =>
      estimateProduction({
        shotCount: overview?.selectedScript?.shotCount ?? 1,
        workflow,
      }),
    [overview?.selectedScript?.shotCount, workflow],
  );

  async function saveWorkflow(next: WorkflowStagePlan[]) {
    setSaving(true);
    try {
      await patchProduction(projectId, { productionWorkflow: next });
      setOverview((current) => (current ? { ...current, workflow: next } : current));
      setNotice({ kind: "ok", text: "工作流已保存" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "工作流保存失败" });
    } finally {
      setSaving(false);
    }
  }

  function toggleStage(id: string, enabled: boolean) {
    void saveWorkflow(workflow.map((stage) => (stage.id === id ? { ...stage, enabled } : stage)));
  }

  async function saveDirection() {
    setSaving(true);
    try {
      const nextIntent: CreativeIntent = {
        ...intent,
        continuity: splitList(listDraft.continuity),
        productConstraints: splitList(listDraft.productConstraints),
        negative: splitList(listDraft.negative),
      };
      const nextBible: VisualBible = {
        characterAnchors: splitList(bibleDraft.characterAnchors),
        productAnchors: splitList(bibleDraft.productAnchors),
        wardrobeAnchors: splitList(bibleDraft.wardrobeAnchors),
        environmentAnchors: splitList(bibleDraft.environmentAnchors),
        lightingAnchors: splitList(bibleDraft.lightingAnchors),
        forbiddenChanges: splitList(bibleDraft.forbiddenChanges),
      };
      await patchProduction(projectId, { creativeIntent: nextIntent, visualBible: nextBible });
      setIntent(nextIntent);
      setBible(nextBible);
      setNotice({ kind: "ok", text: "创作意图与 Visual Bible 已保存" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  }

  async function chooseCandidate(candidate: QualityCandidate) {
    try {
      await selectAsset(projectId, candidate.id);
      await load();
      setNotice({ kind: "ok", text: `第 ${candidate.shotId} 镜已切换到该候选` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "切换候选失败" });
    }
  }

  async function evaluate(candidate: QualityCandidate) {
    if (!llm.baseUrl || !llm.apiKey || !llm.model) {
      setNotice({ kind: "error", text: "请先在设置中配置可看图的 LLM（vision 模型优先）" });
      return;
    }
    setEvaluatingId(candidate.id);
    try {
      await evaluateQuality(projectId, {
        assetId: candidate.id,
        llmConfig: {
          baseUrl: llm.baseUrl,
          apiKey: llm.apiKey,
          model: llm.model,
          ...(llm.visionModel ? { visionModel: llm.visionModel } : {}),
        },
      });
      await load();
      setNotice({ kind: "ok", text: `第 ${candidate.shotId} 镜评估完成` });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "质量评估失败" });
    } finally {
      setEvaluatingId(null);
    }
  }

  async function decide(candidate: QualityCandidate, decision: "accepted" | "rejected") {
    if (!candidate.latestReview) return;
    try {
      await decideQuality(projectId, { reviewId: candidate.latestReview.id, decision });
      await load();
      setNotice({ kind: "ok", text: decision === "accepted" ? "已通过并设为镜头选中项" : "已驳回该候选" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "裁决失败" });
    }
  }

  async function prepareRepair(candidate: QualityCandidate) {
    if (!candidate.latestReview) return;
    setRepairBusy(true);
    setRepair(null);
    try {
      const target = await resolveDefaultModelTarget(providers, defaultVideoModel, customModels, "video");
      if (!target) throw new Error("请先在设置中启用视频平台并选择默认生视频模型");
      const preview = await previewRepair(projectId, {
        assetId: candidate.id,
        reviewId: candidate.latestReview.id,
        provider: target.provider,
        model: target.model,
        supportsAudio: false,
      });
      setRepair({ candidateId: candidate.id, preview });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "修复预演失败" });
    } finally {
      setRepairBusy(false);
    }
  }

  async function confirmRepair() {
    if (!repair) return;
    const candidate = quality?.candidates.find((item) => item.id === repair.candidateId);
    if (!candidate?.latestReview) return;
    const target = await resolveDefaultModelTarget(providers, defaultVideoModel, customModels, "video");
    if (!target) return;
    const costText = repair.preview.summary.estimatedCostUsd != null
      ? `$${repair.preview.summary.estimatedCostUsd.toFixed(2)}`
      : `$${repair.preview.estimatedCost.min ?? "?"}–$${repair.preview.estimatedCost.max ?? "?"}`;
    if (!window.confirm(`将提交一次付费修复（预计 ${costText}），继续？`)) return;
    setRepairBusy(true);
    try {
      const result = await executeRepair(projectId, {
        assetId: candidate.id,
        reviewId: candidate.latestReview.id,
        provider: target.provider,
        model: target.model,
        ...(target.baseUrl ? { baseUrl: target.baseUrl } : {}),
        apiKey: target.apiKey,
        operationId: repair.preview.summary.operationId,
        planHash: repair.preview.summary.planHash,
        confirmed: true,
      });
      setRepair(null);
      setNotice({
        kind: "ok",
        text: result.taskId
          ? `修复任务已提交：${result.taskId}。完成后可在素材页取回。`
          : "修复任务已提交，请稍后在素材页查看。",
      });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "修复提交失败" });
    } finally {
      setRepairBusy(false);
    }
  }

  async function runRouting() {
    setRoutingBusy(true);
    try {
      const enabled = enabledMainPgMediaProviders(providers);
      if (enabled.length === 0) throw new Error("请先在设置中启用生视频平台");
      const { models } = await listAiModels({ providers: enabled, mediaType: "video" });
      const candidates: RouteCandidate[] = models.map((model) => ({
        id: model.id,
        name: model.name,
        modes: model.modes as GenerationMode[],
        supportsAudio: model.supportsAudio,
      }));
      const decision = routeModel(candidates, { mode: "image-to-video", goal: routingGoal });
      setRouting(
        decision.ranked.slice(0, 5).map((row) => ({
          id: row.candidate.id,
          name: row.candidate.name,
          score: row.score,
          reasons: row.reasons,
        })),
      );
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "模型路由失败" });
    } finally {
      setRoutingBusy(false);
    }
  }

  async function runMastering() {
    setMasteringBusy(true);
    setMasteringUrl(null);
    try {
      const { analysis: result } = await analyzeMastering(projectId, {
        ...(overview?.latestComposition?.id ? { compositionId: overview.latestComposition.id } : {}),
      });
      setAnalysis(result);
      setNormalizeAudio(result.hasAudio);
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "母版分析失败" });
    } finally {
      setMasteringBusy(false);
    }
  }

  async function renderMaster() {
    if (!analysis) return;
    if (!normalizeAudio && !deflicker) {
      setNotice({ kind: "error", text: "请至少选择一项本地精修操作" });
      return;
    }
    setMasteringBusy(true);
    try {
      const result = await renderMastering(projectId, {
        ...(overview?.latestComposition?.id ? { compositionId: overview.latestComposition.id } : {}),
        normalizeAudio,
        deflicker,
        label: "Local master",
      });
      setNotice({ kind: "ok", text: "母版渲染已开始，正在等待完成…" });
      for (let attempt = 0; attempt < 80; attempt += 1) {
        const { composition } = await getCompose(projectId, result.compositionId);
        if (composition?.status === "done" && composition.url) {
          setMasteringUrl(composition.url);
          await load();
          break;
        }
        if (composition?.status === "failed") throw new Error("母版渲染失败");
        await new Promise((resolve) => setTimeout(resolve, 2500));
      }
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "母版渲染失败" });
    } finally {
      setMasteringBusy(false);
    }
  }

  async function createSnapshot() {
    setSaving(true);
    try {
      await patchProduction(projectId, {
        action: "snapshot",
        ...(snapshotLabel.trim() ? { label: snapshotLabel.trim() } : {}),
      });
      setSnapshotLabel("");
      await load();
      setNotice({ kind: "ok", text: "母版快照已保存" });
    } catch (cause) {
      setNotice({ kind: "error", text: cause instanceof Error ? cause.message : "快照保存失败" });
    } finally {
      setSaving(false);
    }
  }

  const diagnosis = overview?.latestFailure ? diagnoseGenerationFailure(overview.latestFailure.error) : null;

  if (loading) {
    return (
      <section className="ai-video-page" aria-label="制作控制台">
        <div className="ai-video-empty" role="status">
          <span className="ai-video-empty-icon">◌</span>
          <strong>正在加载制作控制台…</strong>
        </div>
      </section>
    );
  }

  if (loadError || !overview) {
    return (
      <section className="ai-video-page" aria-label="制作控制台">
        <div className="ai-video-empty" role="alert">
          <span className="ai-video-empty-icon">!</span>
          <strong>{loadError || "项目不存在"}</strong>
          <button type="button" onClick={() => onNavigate("/projects")}>返回我的项目</button>
        </div>
      </section>
    );
  }

  return (
    <section className="ai-video-page" aria-label="制作控制台">
      <ProjectHeader project={overview.project} step="production" onNavigate={onNavigate} />

      {notice && (
        <div className={`ai-video-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">
          <span>{notice.text}</span>
          <button type="button" aria-label="关闭提示" onClick={() => setNotice(null)}>×</button>
        </div>
      )}

      <div className="ai-video-production-grid">
        <section className="ai-video-form-card" aria-label="成本卡">
          <div className="ai-video-form-head">
            <h3>成本卡</h3>
            <p>按当前工作流与镜头数估算；未知单价按区间给出，不伪造精度。</p>
          </div>
          <div className="ai-video-cost-card">
            <strong>
              {cost.knownUsd > 0 ? `已知 $${cost.knownUsd.toFixed(2)}` : "暂无已知单价"}
              {cost.unknownCalls > 0 ? ` · ${cost.unknownCalls} 次未知单价调用` : ""}
            </strong>
            <span>
              预计 ${cost.rangeUsd.min.toFixed(2)}–${cost.rangeUsd.max.toFixed(2)} ·{" "}
              {Math.round(cost.estimatedSeconds.min / 60)}–{Math.round(cost.estimatedSeconds.max / 60)} 分钟
            </span>
          </div>
          <div className="ai-video-workflow">
            {workflow.map((stage) => (
              <label key={stage.id} className="ai-video-workflow-stage">
                <input
                  type="checkbox"
                  checked={stage.enabled}
                  disabled={saving}
                  onChange={(event) => toggleStage(stage.id, event.target.checked)}
                />
                <span>{STAGE_LABELS[stage.id] ?? stage.id}</span>
                <em>{stage.billing === "paid" ? "付费" : stage.billing === "conditional" ? "按需" : "免费"}</em>
              </label>
            ))}
          </div>
        </section>

        <section className="ai-video-form-card" aria-label="创意意图与 Visual Bible">
          <div className="ai-video-form-head">
            <h3>创意意图 / Visual Bible</h3>
            <p>写入后脚本与生视频提示词都会带上这些约束；禁止项命中会阻止提交。</p>
          </div>
          <div className="ai-video-settings-grid">
            {([
              ["subject", "主体"],
              ["action", "动作"],
              ["environment", "环境"],
              ["lighting", "光线"],
              ["palette", "色彩"],
              ["composition", "构图"],
              ["camera", "运镜"],
              ["motion", "运动"],
            ] as const).map(([key, label]) => (
              <label key={key} className="ai-video-field">
                {label}
                <input
                  value={intent[key] ?? ""}
                  onChange={(event) => setIntent((current) => ({ ...current, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <label className="ai-video-field">
            连续性（逗号分隔）
            <input value={listDraft.continuity} onChange={(event) => setListDraft((current) => ({ ...current, continuity: event.target.value }))} />
          </label>
          <label className="ai-video-field">
            商品约束（逗号分隔）
            <input
              value={listDraft.productConstraints}
              onChange={(event) => setListDraft((current) => ({ ...current, productConstraints: event.target.value }))}
            />
          </label>
          <label className="ai-video-field">
            负面约束（逗号分隔）
            <input value={listDraft.negative} onChange={(event) => setListDraft((current) => ({ ...current, negative: event.target.value }))} />
          </label>

          <div className="ai-video-settings-grid">
            {([
              ["characterAnchors", "角色锚点"],
              ["productAnchors", "商品锚点"],
              ["wardrobeAnchors", "服装锚点"],
              ["environmentAnchors", "环境锚点"],
              ["lightingAnchors", "光线锚点"],
              ["forbiddenChanges", "禁止改变"],
            ] as const).map(([key, label]) => (
              <label key={key} className="ai-video-field">
                {label}
                <input
                  value={bibleDraft[key]}
                  onChange={(event) => setBibleDraft((current) => ({ ...current, [key]: event.target.value }))}
                />
              </label>
            ))}
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" disabled={saving} onClick={() => void saveDirection()}>
              {saving ? "保存中…" : "保存创意意图与 Bible"}
            </button>
            {bible.forbiddenChanges.length > 0 && (
              <span className="ai-video-settings-message">已设 {bible.forbiddenChanges.length} 条禁止改变</span>
            )}
          </div>
        </section>

        <section className="ai-video-form-card" aria-label="质量裁决">
          <div className="ai-video-form-head">
            <h3>质量门禁裁决</h3>
            <p>视觉模型逐候选打分；人工通过会同时把该 take 设为镜头选中项。</p>
          </div>
          {quality?.modelStats.length ? (
            <ul className="ai-video-diag-list">
              {quality.modelStats.map((stat) => (
                <li key={stat.model}>
                  <span>{stat.model}</span>
                  <em>
                    {stat.reviews} 次 · 均分 {stat.averageOverall} · 驳回率 {Math.round(stat.rejectionRate * 100)}%
                  </em>
                </li>
              ))}
            </ul>
          ) : (
            <p className="ai-video-settings-message">还没有质量评估记录。</p>
          )}

          <div className="ai-video-quality-list">
            {quality?.candidates.slice(0, 12).map((candidate) => {
              const thumb = aiVideoMediaUrl(candidate.thumbnailPath ?? candidate.filePath);
              const review = candidate.latestReview;
              return (
                <article key={candidate.id} className="ai-video-quality-candidate">
                  <div className="ai-video-quality-thumb">
                    {thumb ? <img src={thumb} alt={`第 ${candidate.shotId} 镜候选`} loading="lazy" /> : <span>{candidate.shotId}</span>}
                  </div>
                  <div className="ai-video-quality-body">
                    <strong>第 {candidate.shotId} 镜 · {candidate.model ?? candidate.type}</strong>
                    <span className="ai-video-asset-meta">
                      {candidate.selected ? "当前选中" : "候选"}
                      {review
                        ? ` · ${review.verdict} · ${review.report.overall} 分${
                            review.humanDecision ? ` · 人工${review.humanDecision === "accepted" ? "通过" : "驳回"}` : ""
                          }`
                        : " · 未评估"}
                    </span>
                    {review && <p className="ai-video-asset-desc">{review.report.summary}</p>}
                    <div className="ai-video-asset-actions">
                      <button type="button" className="is-secondary" disabled={candidate.selected} onClick={() => void chooseCandidate(candidate)}>
                        设为选中
                      </button>
                      <button type="button" className="is-secondary" disabled={evaluatingId !== null} onClick={() => void evaluate(candidate)}>
                        {evaluatingId === candidate.id ? "评估中…" : "重新评估"}
                      </button>
                      {review && (
                        <>
                          <button type="button" disabled={Boolean(review.humanDecision === "accepted")} onClick={() => void decide(candidate, "accepted")}>
                            通过
                          </button>
                          <button
                            type="button"
                            className="is-secondary"
                            disabled={Boolean(review.humanDecision === "rejected")}
                            onClick={() => void decide(candidate, "rejected")}
                          >
                            驳回
                          </button>
                          {candidate.filePath.toLowerCase().endsWith(".mp4") && (
                            <button type="button" className="is-secondary" disabled={repairBusy} onClick={() => void prepareRepair(candidate)}>
                              精准修复
                            </button>
                          )}
                        </>
                      )}
                    </div>
                    {repair?.candidateId === candidate.id && (
                      <div className="ai-video-repair">
                        <strong>修复预演（未提交、未计费）</strong>
                        <p>{repair.preview.summary.window.start.toFixed(2)}s – {repair.preview.summary.window.end.toFixed(2)}s · {repair.preview.summary.effectiveScope}</p>
                        <p className="ai-video-asset-desc">{repair.preview.prompt}</p>
                        <p>
                          预计 ${repair.preview.estimatedCost.min ?? "?"}–${repair.preview.estimatedCost.max ?? "?"} ·{" "}
                          {repair.preview.executable ? "可执行" : "当前模型不可执行"}
                        </p>
                        {repair.preview.summary.warnings.length > 0 && (
                          <p className="ai-video-settings-message">{repair.preview.summary.warnings.join(", ")}</p>
                        )}
                        <div className="ai-video-settings-actions">
                          <button type="button" disabled={repairBusy || !repair.preview.executable} onClick={() => void confirmRepair()}>
                            {repairBusy ? "提交中…" : "确认执行修复（付费）"}
                          </button>
                          <button type="button" className="is-secondary" onClick={() => setRepair(null)}>
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="ai-video-form-card" aria-label="模型路由">
          <div className="ai-video-form-head">
            <h3>模型路由</h3>
            <p>按目标在已启用平台里排序可用生视频模型；打分来源透明，不含虚构榜单。</p>
          </div>
          <div className="ai-video-settings-actions">
            <select value={routingGoal} onChange={(event) => setRoutingGoal(event.target.value as RoutingGoal)}>
              {GOALS.map((goal) => (
                <option key={goal.id} value={goal.id}>{goal.label}</option>
              ))}
            </select>
            <button type="button" disabled={routingBusy} onClick={() => void runRouting()}>
              {routingBusy ? "计算中…" : "推荐模型"}
            </button>
          </div>
          {routing.length > 0 && (
            <ul className="ai-video-diag-list">
              {routing.map((row, index) => (
                <li key={row.id}>
                  <span>{index + 1}. {row.name}</span>
                  <em>{row.score} 分 · {row.reasons.join(" / ")}</em>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="母版与精修">
          <div className="ai-video-form-head">
            <h3>母版与本地精修</h3>
            <p>
              对最新成片做响度归一与去闪烁，生成新的非破坏性母版。
              {overview.latestComposition ? ` 当前成片：${overview.latestComposition.id.slice(0, 8)}` : " 还没有成片。"}
            </p>
          </div>
          <div className="ai-video-settings-actions">
            <button type="button" className="is-secondary" disabled={masteringBusy || !overview.latestComposition} onClick={() => void runMastering()}>
              {masteringBusy ? "分析中…" : "分析成片"}
            </button>
            {analysis && (
              <button type="button" disabled={masteringBusy} onClick={() => void renderMaster()}>
                生成母版
              </button>
            )}
          </div>
          {analysis && (
            <div className="ai-video-publish-copy">
              <p>
                时长 {analysis.duration.toFixed(2)}s · {analysis.hasAudio ? "有音轨" : "无音轨"}
                {analysis.loudness?.integratedLufs != null ? ` · ${analysis.loudness.integratedLufs.toFixed(1)} LUFS` : ""}
              </p>
              <label className="ai-video-check">
                <input type="checkbox" checked={normalizeAudio} disabled={!analysis.hasAudio} onChange={(event) => setNormalizeAudio(event.target.checked)} />
                响度归一
              </label>
              <label className="ai-video-check">
                <input type="checkbox" checked={deflicker} onChange={(event) => setDeflicker(event.target.checked)} />
                去闪烁
              </label>
            </div>
          )}
          {masteringUrl && (
            <a href={aiVideoMediaUrl(masteringUrl) ?? "#"} target="_blank" rel="noreferrer">查看母版成片</a>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="母版快照">
          <div className="ai-video-form-head">
            <h3>母版快照</h3>
            <p>记录当前脚本、可用素材与成片的版本组合，方便回看与回滚。</p>
          </div>
          <div className="ai-video-settings-actions">
            <input
              value={snapshotLabel}
              maxLength={60}
              placeholder="快照名称（可选）"
              onChange={(event) => setSnapshotLabel(event.target.value)}
            />
            <button type="button" disabled={saving} onClick={() => void createSnapshot()}>
              {saving ? "保存中…" : "保存当前版本快照"}
            </button>
          </div>
          {overview.snapshots.length > 0 && (
            <ul className="ai-video-diag-list">
              {overview.snapshots.slice(0, 6).map((snapshot) => (
                <li key={snapshot.id}>
                  <span>{snapshot.label}</span>
                  <em>{new Date(snapshot.createdAt).toLocaleString("zh-CN")} · {snapshot.assetIds.length} 个素材</em>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="ai-video-form-card" aria-label="诊断">
          <div className="ai-video-form-head">
            <h3>诊断</h3>
            <p>最近一次失败、版本树与产出计数；失败建议来自可复现的错误分类。</p>
          </div>
          {overview.latestFailure ? (
            <div className="ai-video-verdict is-fail">
              <strong>{overview.latestFailure.source === "task" ? "生成任务" : "流水线"}：{overview.latestFailure.stage}</strong>
              <p>{overview.latestFailure.error}</p>
              {diagnosis && (
                <p>
                  {diagnosis.message.zh}
                  {diagnosis.retryable ? "（可重试）" : "（需人工处理）"}
                  {diagnosis.actions.length > 0 ? ` · 建议：${diagnosis.actions.join(" / ")}` : ""}
                </p>
              )}
            </div>
          ) : (
            <p className="ai-video-settings-message">最近没有失败记录。</p>
          )}
          <ul className="ai-video-diag-list">
            <li><span>脚本 / 素材 / 片段 / 任务 / 成片</span><em>{overview.counts.scripts} / {overview.counts.assets} / {overview.counts.clips} / {overview.counts.tasks} / {overview.counts.compositions}</em></li>
            {overview.versionTree.generations.slice(0, 8).map((row) => (
              <li key={`${row.kind}-${row.id}`}>
                <span>{row.kind === "asset" ? "素材" : row.kind === "task" ? "任务" : "成片"} · {row.label}</span>
                <em>{row.status}{row.shotId != null ? ` · 第 ${row.shotId} 镜` : ""}</em>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </section>
  );
}
