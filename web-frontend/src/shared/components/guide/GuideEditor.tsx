/**
 * 新手引导的可视化编辑器（仅管理员入口）。
 *
 * 交互主线：选板块 → 选子任务 → 「点选拾取」后在页面上点要讲解的位置 → 填文案 → 保存。
 * 面板用 pointer-events 穿透，不挡工作台，因此可以一边翻页一边给不同页面加步骤；
 * 面板是可拖动的浮窗（拖标题栏），方便挪开它去拾取被挡住的控件；
 * 拾取时靠 selectorPicker 在 document 捕获阶段拦截点击，不会被面板干扰。
 */

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { GUIDE_BOARD_META, type GuideBoardId } from "../GuideTour";
import {
  GUIDE_ADVANCE_MODE_LABELS,
  GUIDE_ALIGN_LABELS,
  GUIDE_LIMITS,
  GUIDE_PRESET_MODE_LABELS,
  GUIDE_SIDE_LABELS,
  cloneGuideConfig,
  nextSubTaskId,
  type GuideAdvanceMode,
  type GuideAlign,
  type GuideConfig,
  type GuidePresetMode,
  type GuideSide,
  type GuideStepConfig,
  type GuideSubTaskConfig,
} from "./guideConfig";
import { countVisibleMatch, startElementPicker } from "./selectorPicker";

export type GuidePageOption = { id: string; label: string };

type GuideEditorProps = {
  /** 打开编辑器时的配置快照；组件内部持有草稿，之后不再跟随外部变化。 */
  config: GuideConfig;
  /** 可供步骤选择的目标页面，来自工作台实际导航。 */
  pages: GuidePageOption[];
  /** 当前打开的模块 id，新增步骤时用它做默认页面。 */
  activePageId: string;
  /** 保存草稿；失败时 reject（提示由外层负责），编辑器保留「未保存」状态。 */
  onSave: (config: GuideConfig) => Promise<void>;
  onClose: () => void;
  /** 用草稿临时顶替生效配置并播放引导；Promise 在引导结束后 resolve。 */
  onPreview: (config: GuideConfig, boardId: GuideBoardId, subTaskId: string) => Promise<void>;
};

type PickSlot = { stepIndex: number; selectorIndex: number | "new" };

function emptyStep(page: string): GuideStepConfig {
  return {
    selectors: [""],
    page,
    title: "",
    description: "",
    side: "bottom",
    align: "center",
    interactive: false,
  };
}

function subTasksOf(config: GuideConfig, boardId: GuideBoardId): GuideSubTaskConfig[] {
  return config.boards[boardId]?.subTasks ?? [];
}

/** 浮窗与视口边缘的最小间距，同时用于首次定位和拖动越界收敛。 */
const PANEL_GAP = 16;
/** 面板还没挂上 DOM 时（首帧、拖动中重排）用它们估算尺寸做收敛。 */
const PANEL_FALLBACK_WIDTH = 430;
const PANEL_FALLBACK_HEIGHT = 760;

function clampPanelPosition(x: number, y: number, width: number, height: number) {
  const maxX = Math.max(PANEL_GAP, window.innerWidth - width - PANEL_GAP);
  const maxY = Math.max(PANEL_GAP, window.innerHeight - height - PANEL_GAP);
  return {
    x: Math.min(Math.max(x, PANEL_GAP), maxX),
    y: Math.min(Math.max(y, PANEL_GAP), maxY),
  };
}

/** 默认落在右侧靠上，尽量少压住工作台。 */
function initialPanelPosition() {
  const width = Math.min(PANEL_FALLBACK_WIDTH, window.innerWidth - PANEL_GAP * 2);
  return {
    x: Math.max(PANEL_GAP, window.innerWidth - width - PANEL_GAP),
    y: PANEL_GAP * 2,
  };
}

/** 服务端要求「设了预设值方式就必须有内容」，这里先标出来，省得提交后被拒。 */
function presetValueMissing(step: GuideStepConfig): boolean {
  return (step.presetMode ?? "off") !== "off" && (step.presetValue ?? "").trim() === "";
}

export function GuideEditor({
  config,
  pages,
  activePageId,
  onSave,
  onClose,
  onPreview,
}: GuideEditorProps) {
  const [draft, setDraft] = useState<GuideConfig>(() => cloneGuideConfig(config));
  const [boardId, setBoardId] = useState<GuideBoardId>(() => {
    // 默认落在第一个还有教程可改的板块上，省一次手动切换。
    const withSteps = GUIDE_BOARD_META.find((meta) =>
      subTasksOf(config, meta.id).some((task) => task.steps.length > 0),
    );
    return (withSteps ?? GUIDE_BOARD_META[0]).id;
  });
  const [subTaskId, setSubTaskId] = useState<string | null>(
    () => subTasksOf(config, boardId)[0]?.id ?? null,
  );
  const [picking, setPicking] = useState<PickSlot | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [savingLocal, setSavingLocal] = useState(false);
  /** 已落库的那份配置，用来判断草稿有没有改动。 */
  const [savedSnapshot, setSavedSnapshot] = useState(() => JSON.stringify(config));

  const pickerStopRef = useRef<(() => void) | null>(null);

  // 浮窗位置：拖动标题栏更新，窗口尺寸变化时收敛回视口内。
  const panelRef = useRef<HTMLElement | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const [panelPos, setPanelPos] = useState(initialPanelPosition);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const handleResize = () => {
      const rect = panelRef.current?.getBoundingClientRect();
      setPanelPos((prev) =>
        clampPanelPosition(
          prev.x,
          prev.y,
          rect?.width ?? PANEL_FALLBACK_WIDTH,
          rect?.height ?? PANEL_FALLBACK_HEIGHT,
        ),
      );
    };
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  const handleDragStart = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0) return;
    // 关闭按钮就长在标题栏里，别让拖动把它吃掉。
    if ((event.target as Element).closest(".guide-editor-close")) return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: panelPos.x,
      originY: panelPos.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };

  const handleDragMove = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = panelRef.current?.getBoundingClientRect();
    setPanelPos(
      clampPanelPosition(
        drag.originX + event.clientX - drag.startX,
        drag.originY + event.clientY - drag.startY,
        rect?.width ?? PANEL_FALLBACK_WIDTH,
        rect?.height ?? PANEL_FALLBACK_HEIGHT,
      ),
    );
  };

  const handleDragEnd = (event: ReactPointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
  };

  // 面板随时可能被卸载（关窗/切路由），拾取监听必须一并撤掉。
  useEffect(() => () => pickerStopRef.current?.(), []);

  const board = useMemo(() => subTasksOf(draft, boardId), [draft, boardId]);
  const subTask = useMemo(
    () => board.find((task) => task.id === subTaskId) ?? null,
    [board, subTaskId],
  );

  const dirty = useMemo(() => JSON.stringify(draft) !== savedSnapshot, [draft, savedSnapshot]);

  /** 各选择器在当前页面的可见命中数；用于提示「找不到元素 / 会命中多个」。 */
  const matchCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const task of board) {
      for (const step of task.steps) {
        for (const selector of step.selectors) {
          const key = selector.trim();
          if (key && !counts.has(key)) counts.set(key, countVisibleMatch(key));
        }
      }
    }
    return counts;
    // activePageId 变化代表用户翻了页，命中结果需要重算。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board, activePageId]);

  const patchSubTasks = (
    targetBoardId: GuideBoardId,
    updater: (tasks: GuideSubTaskConfig[]) => GuideSubTaskConfig[],
  ) => {
    setDraft((current) => {
      const tasks = current.boards[targetBoardId]?.subTasks ?? [];
      return {
        ...current,
        boards: { ...current.boards, [targetBoardId]: { subTasks: updater(tasks) } },
      };
    });
  };

  const patchSubTask = (
    updater: (task: GuideSubTaskConfig) => GuideSubTaskConfig,
  ) => {
    if (!subTaskId) return;
    patchSubTasks(boardId, (tasks) =>
      tasks.map((task) => (task.id === subTaskId ? updater(task) : task)),
    );
  };

  const patchSteps = (updater: (steps: GuideStepConfig[]) => GuideStepConfig[]) => {
    patchSubTask((task) => ({ ...task, steps: updater(task.steps) }));
  };

  const pickerLive = (slot: PickSlot) => picking?.stepIndex === slot.stepIndex
    && picking?.selectorIndex === slot.selectorIndex;

  const startPick = (slot: PickSlot) => {
    pickerStopRef.current?.();
    setPicking(slot);
    pickerStopRef.current = startElementPicker({
      ignoreSelector: ".guide-editor",
      onPick: (pick) => {
        pickerStopRef.current = null;
        setPicking(null);
        patchSteps((steps) =>
          steps.map((step, index) => {
            if (index !== slot.stepIndex) return step;
            const page = step.page || activePageId;
            if (slot.selectorIndex === "new") {
              if (
                step.selectors.includes(pick.selector)
                || step.selectors.filter((item) => item.trim()).length >= GUIDE_LIMITS.selectorsPerStep
              ) {
                return step;
              }
              return { ...step, page, selectors: [...step.selectors, pick.selector] };
            }
            const selectors = [...step.selectors];
            selectors[slot.selectorIndex] = pick.selector;
            return { ...step, page, selectors };
          }),
        );
      },
      onCancel: () => {
        pickerStopRef.current = null;
        setPicking(null);
      },
    });
  };

  const addSubTask = () => {
    const created: GuideSubTaskConfig = {
      id: nextSubTaskId({ subTasks: board }),
      label: "新子任务",
      steps: [],
    };
    if (board.length >= GUIDE_LIMITS.subTasksPerBoard) return;
    patchSubTasks(boardId, (tasks) => [...tasks, created]);
    setSubTaskId(created.id);
  };

  const removeSubTask = (taskId: string) => {
    const target = board.find((task) => task.id === taskId);
    if (!target) return;
    if (!window.confirm(`删除子任务「${target.label}」及其 ${target.steps.length} 个步骤？此操作保存后生效。`)) {
      return;
    }
    patchSubTasks(boardId, (tasks) => tasks.filter((task) => task.id !== taskId));
    if (subTaskId === taskId) setSubTaskId(null);
  };

  const addStep = () => {
    if (!subTask || subTask.steps.length >= GUIDE_LIMITS.stepsPerSubTask) return;
    patchSteps((steps) => [...steps, emptyStep(activePageId)]);
  };

  const removeStep = (index: number) => {
    patchSteps((steps) => steps.filter((_, itemIndex) => itemIndex !== index));
  };

  const moveStep = (index: number, delta: number) => {
    patchSteps((steps) => {
      const target = index + delta;
      if (target < 0 || target >= steps.length) return steps;
      const next = [...steps];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const patchStep = (index: number, patch: Partial<GuideStepConfig>) => {
    patchSteps((steps) => steps.map((step, itemIndex) => (itemIndex === index ? { ...step, ...patch } : step)));
  };

  const handlePreview = async () => {
    if (!subTask || subTask.steps.length === 0 || previewing) return;
    if (dirty && !window.confirm("预览用的是当前草稿，还没保存。继续预览吗？")) return;
    setPreviewing(true);
    try {
      await onPreview(draft, boardId, subTask.id);
    } finally {
      setPreviewing(false);
    }
  };

  const handleSave = async () => {
    if (!dirty || savingLocal || picking) return;
    setSavingLocal(true);
    const snapshot = JSON.stringify(draft);
    try {
      await onSave(draft);
      setSavedSnapshot(snapshot);
    } catch {
      // 失败原因由外层 toast 说明，这里保留草稿继续可改。
    } finally {
      setSavingLocal(false);
    }
  };

  return (
    <div className="guide-editor-overlay">
      <aside
        ref={panelRef}
        className={`guide-editor${previewing ? " is-previewing" : ""}${dragging ? " is-dragging" : ""}`}
        style={{ left: panelPos.x, top: panelPos.y }}
        role="dialog"
        aria-label="新手引导编辑器"
      >
        <header
          className="guide-editor-head"
          onPointerDown={handleDragStart}
          onPointerMove={handleDragMove}
          onPointerUp={handleDragEnd}
          onPointerCancel={handleDragEnd}
        >
          <div>
            <h2>引导编辑器</h2>
            <p>拖标题栏可移动窗口。选板块 → 选子任务 → 点「点选拾取」，再点页面上要讲解的位置。</p>
          </div>
          <button type="button" className="guide-editor-close" onClick={onClose} aria-label="关闭编辑器">
            ×
          </button>
        </header>

        <div className="guide-editor-body">
          <div className="guide-editor-boards" role="tablist">
            {GUIDE_BOARD_META.map((meta) => (
              <button
                key={meta.id}
                type="button"
                role="tab"
                aria-selected={meta.id === boardId}
                className={`guide-editor-board${meta.id === boardId ? " is-active" : ""}`}
                onClick={() => {
                  setBoardId(meta.id);
                  setSubTaskId(subTasksOf(draft, meta.id)[0]?.id ?? null);
                }}
              >
                <i className={meta.iconClass} aria-hidden="true" />
                {meta.label}
              </button>
            ))}
          </div>

          <ul className="guide-editor-tasks">
            {board.map((task) => (
              <li
                key={task.id}
                className={`guide-editor-task${task.id === subTaskId ? " is-active" : ""}`}
              >
                <button
                  type="button"
                  className="guide-editor-task-pick"
                  onClick={() => setSubTaskId(task.id)}
                >
                  <input
                    className="guide-editor-input guide-editor-task-label"
                    value={task.label}
                    maxLength={GUIDE_LIMITS.subTaskLabel}
                    spellCheck={false}
                    placeholder="子任务名称"
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => {
                      const label = event.target.value;
                      patchSubTasks(boardId, (tasks) =>
                        tasks.map((item) => (item.id === task.id ? { ...item, label } : item)),
                      );
                    }}
                  />
                  <span className="guide-editor-task-count">
                    {task.steps.length > 0 ? `${task.steps.length} 步` : "准备中"}
                  </span>
                </button>
                <button
                  type="button"
                  className="guide-editor-icon-btn"
                  onClick={() => removeSubTask(task.id)}
                  title="删除子任务"
                  aria-label={`删除子任务 ${task.label}`}
                >
                  删
                </button>
              </li>
            ))}
            {board.length === 0 && (
              <li className="guide-editor-empty">这个板块下还没有子任务。</li>
            )}
          </ul>
          <button
            type="button"
            className="guide-editor-add"
            onClick={addSubTask}
            disabled={board.length >= GUIDE_LIMITS.subTasksPerBoard}
          >
            + 新增子任务
          </button>

          {subTask ? (
            <section className="guide-editor-steps">
              <h3>
                子任务「{subTask.label}」的步骤
                <span className="guide-editor-hint">
                  引导按顺序播放；需要用户先填写/操作的步骤记得勾选对应开关，预设值可强制填写或由引导自动填入
                </span>
              </h3>

              {subTask.steps.map((step, index) => (
                <article className="guide-editor-step" key={index}>
                  <div className="guide-editor-step-head">
                    <strong>第 {index + 1} 步</strong>
                    <div className="guide-editor-step-tools">
                      <button
                        type="button"
                        className="guide-editor-icon-btn"
                        onClick={() => moveStep(index, -1)}
                        disabled={index === 0}
                        title="上移"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="guide-editor-icon-btn"
                        onClick={() => moveStep(index, 1)}
                        disabled={index === subTask.steps.length - 1}
                        title="下移"
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="guide-editor-icon-btn"
                        onClick={() => removeStep(index)}
                        title="删除该步骤"
                      >
                        删
                      </button>
                    </div>
                  </div>

                  <div className="guide-editor-selectors">
                    {step.selectors.map((selector, selectorIndex) => {
                      const count = matchCounts.get(selector.trim());
                      return (
                        <div className="guide-editor-selector" key={selectorIndex}>
                          <span className="guide-editor-selector-tag">
                            {selectorIndex === 0 ? "主" : `备${selectorIndex}`}
                          </span>
                          <input
                            className="guide-editor-input guide-editor-selector-input"
                            value={selector}
                            spellCheck={false}
                            placeholder="CSS 选择器，如 .verify-page .verify-actions"
                            onChange={(event) => {
                              const selectors = [...step.selectors];
                              selectors[selectorIndex] = event.target.value;
                              patchStep(index, { selectors });
                            }}
                          />
                          <button
                            type="button"
                            className={`guide-editor-pick${pickerLive({ stepIndex: index, selectorIndex }) ? " is-active" : ""}`}
                            onClick={() => startPick({ stepIndex: index, selectorIndex })}
                          >
                            {pickerLive({ stepIndex: index, selectorIndex }) ? "拾取中…" : "拾取"}
                          </button>
                          {selectorIndex > 0 && (
                            <button
                              type="button"
                              className="guide-editor-icon-btn"
                              title="删除这个备用选择器"
                              onClick={() =>
                                patchStep(index, {
                                  selectors: step.selectors.filter((_, item) => item !== selectorIndex),
                                })
                              }
                            >
                              ×
                            </button>
                          )}
                          {!!selector.trim() && count === 0 && (
                            <span className="guide-editor-warn">当前页面找不到</span>
                          )}
                          {!!selector.trim() && count !== undefined && count > 1 && (
                            <span className="guide-editor-warn">匹配 {count} 个，会高亮第一个</span>
                          )}
                        </div>
                      );
                    })}
                    <button
                      type="button"
                      className="guide-editor-add"
                      disabled={
                        step.selectors.filter((item) => item.trim()).length >= GUIDE_LIMITS.selectorsPerStep
                      }
                      onClick={() => startPick({ stepIndex: index, selectorIndex: "new" })}
                    >
                      + 点选备用选择器（前一个失效时自动回退）
                    </button>
                  </div>

                  <label className="guide-editor-field">
                    <span>标题</span>
                    <input
                      className="guide-editor-input"
                      value={step.title}
                      maxLength={GUIDE_LIMITS.stepTitle}
                      placeholder="如：采集条件"
                      onChange={(event) => patchStep(index, { title: event.target.value })}
                    />
                  </label>

                  <label className="guide-editor-field">
                    <span>说明</span>
                    <textarea
                      className="guide-editor-input guide-editor-textarea"
                      value={step.description}
                      maxLength={GUIDE_LIMITS.stepDescription}
                      rows={2}
                      placeholder="如：填写一些关键信息点击开始采集"
                      onChange={(event) => patchStep(index, { description: event.target.value })}
                    />
                  </label>

                  <label className="guide-editor-check">
                    <input
                      type="checkbox"
                      checked={step.interactive === true}
                      onChange={(event) => patchStep(index, { interactive: event.target.checked })}
                    />
                    <span>
                      本步需要用户先填写/操作
                      <em>勾选后提示卡会多一句提醒，用户操作完再点「下一步」；页面其余区块仍被蒙版挡住</em>
                    </span>
                  </label>

                  <div className="guide-editor-field-row">
                    <label className="guide-editor-field">
                      <span>预设值</span>
                      <select
                        className="guide-editor-input"
                        value={step.presetMode ?? "off"}
                        onChange={(event) => patchStep(index, { presetMode: event.target.value as GuidePresetMode })}
                      >
                        {Object.entries(GUIDE_PRESET_MODE_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                    {(step.presetMode ?? "off") !== "off" && (
                      <label className="guide-editor-field">
                        <span>预设内容</span>
                        <input
                          className="guide-editor-input"
                          value={step.presetValue ?? ""}
                          maxLength={GUIDE_LIMITS.stepPresetValue}
                          spellCheck={false}
                          placeholder="如：某段采集条件、下拉框要选的值"
                          onChange={(event) => patchStep(index, { presetValue: event.target.value })}
                        />
                      </label>
                    )}
                  </div>
                  {presetValueMissing(step) && (
                    <span className="guide-editor-warn">还没填预设内容，保存会被拦下</span>
                  )}

                  <label className="guide-editor-field">
                    <span>放行方式</span>
                    <select
                      className="guide-editor-input"
                      value={step.advanceOn ?? "manual"}
                      onChange={(event) => patchStep(index, { advanceOn: event.target.value as GuideAdvanceMode })}
                    >
                      {Object.entries(GUIDE_ADVANCE_MODE_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </label>
                  {(step.advanceOn ?? "manual") !== "manual" && (
                    <span className="guide-editor-hint">
                      进入本步先禁掉「下一步」，用户在高亮区域内完成对应动作后自动前进，不用再点确认
                    </span>
                  )}

                  <label className="guide-editor-check">
                    <input
                      type="checkbox"
                      checked={step.autoOpen === true}
                      onChange={(event) => patchStep(index, { autoOpen: event.target.checked })}
                    />
                    <span>
                      进入本步时自动点开目标控件
                      <em>下拉框这类不点开就看不到选项的控件，由引导替你点一下</em>
                    </span>
                  </label>

                  <div className="guide-editor-field-row">
                    <label className="guide-editor-field">
                      <span>所在页面</span>
                      <select
                        className="guide-editor-input"
                        value={step.page}
                        onChange={(event) => patchStep(index, { page: event.target.value })}
                      >
                        <option value="">不切页</option>
                        {pages.map((page) => (
                          <option key={page.id} value={page.id}>{page.label}</option>
                        ))}
                        {!!step.page && !pages.some((page) => page.id === step.page) && (
                          <option value={step.page}>{step.page}（已不在导航中）</option>
                        )}
                      </select>
                    </label>
                    <label className="guide-editor-field">
                      <span>提示位置</span>
                      <select
                        className="guide-editor-input"
                        value={step.side}
                        onChange={(event) => patchStep(index, { side: event.target.value as GuideSide })}
                      >
                        {Object.entries(GUIDE_SIDE_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="guide-editor-field">
                      <span>对齐</span>
                      <select
                        className="guide-editor-input"
                        value={step.align}
                        onChange={(event) => patchStep(index, { align: event.target.value as GuideAlign })}
                      >
                        {Object.entries(GUIDE_ALIGN_LABELS).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                </article>
              ))}

              <button
                type="button"
                className="guide-editor-add"
                onClick={addStep}
                disabled={subTask.steps.length >= GUIDE_LIMITS.stepsPerSubTask}
              >
                + 新增步骤
              </button>
            </section>
          ) : (
            <p className="guide-editor-empty">先在上面选一个子任务，再编辑它的步骤。</p>
          )}
        </div>

        <footer className="guide-editor-foot">
          <span className="guide-editor-status">
            {picking ? "拾取中：点击页面上的位置，Esc 取消" : dirty ? "有未保存的修改" : "已与线上一致"}
          </span>
          <button
            type="button"
            className="guide-editor-btn ghost"
            onClick={handlePreview}
            disabled={!subTask || subTask.steps.length === 0 || previewing}
          >
            {previewing ? "预览中…" : "预览"}
          </button>
          <button type="button" className="guide-editor-btn ghost" onClick={onClose}>
            关闭
          </button>
          <button
            type="button"
            className="guide-editor-btn"
            disabled={!dirty || savingLocal || !!picking}
            onClick={handleSave}
          >
            {savingLocal ? "保存中…" : "保存"}
          </button>
        </footer>
      </aside>
    </div>
  );
}
