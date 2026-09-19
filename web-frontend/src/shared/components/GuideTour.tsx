import { useEffect, useState } from "react";
import { driver, type DriveStep, type Driver, type PopoverDOM } from "driver.js";

import {
  getActiveGuideConfig,
  getGuideBoardConfig,
  type GuideAdvanceMode,
  type GuidePresetMode,
  type GuideStepConfig,
  type GuideSubTaskConfig,
} from "./guide/guideConfig";

/** 引导分两级：一级是业务板块，二级是板块内的子任务（如「采集」），各自单独播放。 */
export type GuideBoardId = "product_workflow" | "pod_customization" | "sourcing_workflow";

/** 二级子任务 id：同一板块内唯一，完成标记按「板块 + 子任务」记录。 */
export type GuideSubTaskId = string;

/** 引导步骤所在页面：取值是工作台模块 id，切页前由工作台校验目标是否存在。 */
export type GuidePageId = string;

/** 每个二级子任务各自的完成标记：走完该任务引导后写入，面板据此显示进度。 */
const GUIDE_SEEN_PREFIX = "jye_workspace_guide_seen:";
/** 板块面板自动弹出过的标记：首次进入工作台提示一次，之后只保留顶部入口。 */
const PANEL_SEEN_KEY = "jye_workspace_guide_panel_seen";

const WAIT_FOR_ELEMENT_MS = 4000;

/** 门控满足后停留多久再自动翻页：给用户一点时间看清「已完成」。 */
const GATE_ADVANCE_DELAY_MS = 700;

function readFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function writeFlag(key: string): void {
  try {
    window.localStorage.setItem(key, "1");
  } catch {
    // 隐私模式等场景下 localStorage 不可用，忽略即可。
  }
}

function seenKey(boardId: GuideBoardId, subTaskId: GuideSubTaskId): string {
  return `${GUIDE_SEEN_PREFIX}${boardId}:${subTaskId}`;
}

export function isGuideSubTaskDone(boardId: GuideBoardId, subTaskId: GuideSubTaskId): boolean {
  return readFlag(seenKey(boardId, subTaskId));
}

export function markGuideSubTaskDone(boardId: GuideBoardId, subTaskId: GuideSubTaskId): void {
  writeFlag(seenKey(boardId, subTaskId));
}

export function hasSeenGuidePanel(): boolean {
  return readFlag(PANEL_SEEN_KEY);
}

export function markGuidePanelSeen(): void {
  writeFlag(PANEL_SEEN_KEY);
}

export type GuideBoardMeta = {
  id: GuideBoardId;
  label: string;
  iconClass: string;
  description: string;
};

/**
 * 三个一级板块：与工作台导航结构绑定，因此在代码里固定。
 * 板块下的子任务与步骤全部来自服务端配置（见 guide/guideConfig.ts），
 * 管理员可以在引导编辑器里增删改，不用重新打包。
 */
export const GUIDE_BOARD_META: GuideBoardMeta[] = [
  {
    id: "product_workflow",
    label: "产品处理",
    iconClass: "iconfont icon-build",
    description: "从采集链接到 AI 处理出图的完整流程",
  },
  {
    id: "pod_customization",
    label: "POD定制",
    iconClass: "iconfont icon-skin",
    description: "批量生成 POD 图片与标题并导出店小秘文件",
  },
  {
    id: "sourcing_workflow",
    label: "核价及货源",
    iconClass: "iconfont icon-audit",
    description: "拉取 Temu 核价信息并对接货源",
  },
];

/** 板块下的子任务取自当前生效配置；服务端配置还没加载完时是内置默认引导。 */
function guideSubTasks(boardId: GuideBoardId): GuideSubTaskConfig[] {
  return getGuideBoardConfig(getActiveGuideConfig(), boardId).subTasks;
}

function guideSubTask(boardId: GuideBoardId, subTaskId: GuideSubTaskId): GuideSubTaskConfig | undefined {
  return guideSubTasks(boardId).find((task) => task.id === subTaskId);
}

/** 板块下至少有一个子任务已备好步骤，才允许从面板进入。 */
function isGuideBoardReady(boardId: GuideBoardId): boolean {
  return guideSubTasks(boardId).some((task) => task.steps.length > 0);
}

/**
 * 板块下「已有教程」的子任务都看完才算完成。
 * 还没写教程的子任务（置灰的「准备中」）不计入，否则板块永远无法完成；
 * 一个教程都没有的板块也永远不算完成。
 */
export function isGuideBoardDone(boardId: GuideBoardId): boolean {
  const readyTasks = guideSubTasks(boardId).filter((task) => task.steps.length > 0);
  return readyTasks.length > 0 && readyTasks.every((task) => isGuideSubTaskDone(boardId, task.id));
}

/**
 * 首次进入工作台要自动播放的那一段引导：按板块顺序找第一个「有教程且没看过」的子任务。
 * 全都看过了（或都还没写教程）时返回 null，调用方退回板块面板。
 */
export function firstPendingGuideSubTask(): { boardId: GuideBoardId; subTaskId: GuideSubTaskId } | null {
  for (const board of GUIDE_BOARD_META) {
    const task = guideSubTasks(board.id).find(
      (item) => item.steps.length > 0 && !isGuideSubTaskDone(board.id, item.id),
    );
    if (task) return { boardId: board.id, subTaskId: task.id };
  }
  return null;
}

/** 已完成的板块数量 / 板块总数，用于面板与入口展示进度。 */
export function getGuideProgress(): { done: number; total: number } {
  return {
    done: GUIDE_BOARD_META.filter((board) => isGuideBoardDone(board.id)).length,
    total: GUIDE_BOARD_META.length,
  };
}

/**
 * 按选择器顺序取第一个「真实可见」的元素。
 * 各模块面板常驻挂载（未激活时靠 hidden 隐藏），同一类名还会出现在多个未激活面板里，
 * 直接 querySelector 会命中尺寸为 0 的隐藏节点，导致高亮落空；因此逐个匹配项按尺寸过滤。
 * 都取不到时返回 null，driver.js 会按 waitForElement 继续等待；
 * 其类型声明未覆盖空返回值这一运行时分支，故此处断言。
 */
function visible(...selectors: string[]): () => Element {
  return () => {
    for (const selector of selectors) {
      for (const element of document.querySelectorAll(selector)) {
        const rect = element.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) return element;
      }
    }
    return null as unknown as Element;
  };
}

/** driver.js 的「没找到高亮元素」占位节点 id：这种步骤不能做预设动作。 */
const DUMMY_ELEMENT_ID = "driver-dummy-element";

/** 未填预设值时挂在提示卡上的说明块（样式见 guide-tour.css）。 */
const PRESET_HINT_CLASS = "guide-preset-hint";

/** 能在配置里被当「控件」处理的标签。 */
const CONTROL_SELECTOR = "input:not([type='hidden']), textarea, select";

/** 自动点开时的优先点击目标：控件本身的触发区。 */
const CLICKABLE_SELECTOR = "button, [role='combobox'], [role='button'], [aria-haspopup], a, input, select, summary";

function presetModeOf(step: GuideStepConfig): GuidePresetMode {
  return step.presetMode ?? "off";
}

/** 本步的放行方式，缺省 manual（用户自己点「下一步」）。 */
function advanceModeOf(step: GuideStepConfig): GuideAdvanceMode {
  return step.advanceOn ?? "manual";
}

/**
 * 这一步是否需要用户先动手（自己填写 / 引导替他点开控件）。
 * 这类步骤不会放开蒙版：driver.js 默认只放行本步高亮的控件，
 * 用户操作完再点提示卡上的「下一步」，页面其他区块照旧点不动。
 * 这里只用来决定提示卡要不要补一行说明（样式见 guide-tour.css）。
 *
 * 带放行门控（advanceOn 非 manual）的步骤不补这行通用说明：那句话写的是
 * 「完成后再点下一步」，而门控步骤是自动前进，且门控会挂自己的精确提示。
 */
function needsUserAction(step: GuideStepConfig): boolean {
  if (advanceModeOf(step) !== "manual") return false;
  return step.interactive === true || presetModeOf(step) === "require" || step.autoOpen === true;
}

/** 按配置的选择器取本步的高亮元素；取不到返回 null。 */
function resolveStepElement(step: GuideStepConfig): Element | null {
  const selectors = step.selectors.filter((selector) => selector.trim() !== "");
  if (selectors.length === 0) return null;
  return (visible(...selectors)() as Element | null) ?? null;
}

/** 高亮区域里真正承载值的控件：本身是输入框/下拉框就用它，否则取内部第一个。 */
function findControl(element: Element | null): Element | null {
  if (!element) return null;
  if (element.matches(CONTROL_SELECTOR)) return element;
  return element.querySelector(CONTROL_SELECTOR);
}

/**
 * 高亮区域里的文件选择框：本身是 file input 就用它，否则取内部第一个。
 * 「必须选中文件才能下一步」的门控靠监听它的 change 放行。
 */
function findFileInput(element: Element | null): HTMLInputElement | null {
  if (!element) return null;
  if (element instanceof HTMLInputElement && element.type === "file") return element;
  const nested = element.querySelector("input[type='file']");
  return nested instanceof HTMLInputElement ? nested : null;
}

/** 读控件当前的值；不是输入类控件时退化成读文本内容。 */
function readControlValue(element: Element | null): string {
  if (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement) {
    return element.value.trim();
  }
  return (element?.textContent ?? "").trim();
}

/**
 * 把预设值写进控件并派发 input/change。
 * React 会在实例上覆写 value 的 setter，直接赋值不会触发 onChange，
 * 因此这里取原型上的原生 setter 写入，再补发事件让受控组件同步。
 */
function writeControlValue(element: Element | null, value: string): void {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
    return;
  }
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value")?.set;
  if (setter) setter.call(element, value);
  else element.value = value;
  element.dispatchEvent(new Event("input", { bubbles: true }));
  element.dispatchEvent(new Event("change", { bubbles: true }));
}

/** 替用户点开下拉框这类「不点就展不开」的控件：优先点高亮元素本身，否则点它内部的触发区。 */
function autoOpenControl(element: Element | null): void {
  if (!element) return;
  const target = element.matches(CLICKABLE_SELECTOR)
    ? element
    : element.querySelector(CLICKABLE_SELECTOR) ?? element;
  target.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, view: window }));
}

/** 配置里的一条步骤 -> driver.js 的步骤；选择器为空时退化成居中的无高亮提示。 */
function toDriveStep(step: GuideStepConfig): DriveStep {
  return {
    element: visible(...step.selectors.filter((selector) => selector.trim() !== "")),
    waitForElement: WAIT_FOR_ELEMENT_MS,
    popover: {
      title: step.title,
      description: step.description,
      side: step.side,
      align: step.align,
      // 需要用户自己操作的步骤额外补一行说明（样式见 guide-tour.css）。
      popoverClass: needsUserAction(step) ? "guide-step-interactive" : "",
      // 带「不做完不放行」门控的步骤：先把「下一步」禁掉，满足条件后由门控逻辑放行。
      disableButtons:
        presetModeOf(step) === "require" || advanceModeOf(step) !== "manual" ? ["next"] : undefined,
    },
  };
}

type GuideTourOptions = {
  /** 引导需要跨页时回调，由工作台校验目标页面后执行真正的切页。 */
  onRequestPage: (page: GuidePageId) => void;
  /** 引导结束（完成 / 跳过 / Esc）后的回调，completed 表示是否有走完整个子任务。 */
  onFinish?: (completed: boolean) => void;
};

/**
 * 启动某个二级子任务的蒙版引导，返回 driver 实例（可 isActive() / destroy()）。
 * 子任务没有步骤时返回 null（面板里这类任务已置灰，正常点不到）。
 */
export function startGuideTour(
  boardId: GuideBoardId,
  subTaskId: GuideSubTaskId,
  { onRequestPage, onFinish }: GuideTourOptions,
): Driver | null {
  const task = guideSubTask(boardId, subTaskId);
  if (!task || task.steps.length === 0) return null;

  const steps = task.steps.map(toDriveStep);
  /** 与 steps 下标一一对应的页面；跨页时据此切页。 */
  const stepPages = task.steps.map((step) => step.page);
  let tour: Driver | null = null;
  /** 是否已经走到本子任务的最后一步：只有走到最后才算完成，中途跳过不计。 */
  let reachedLastStep = false;

  /** 前后翻页时若跨页，先请求切页；目标元素由 waitForElement 兜底等待。 */
  const switchPageFor = (from: number, to: number) => {
    const page = stepPages[to];
    if (page && page !== stepPages[from]) onRequestPage(page);
  };

  /** driver.js 当前那张提示卡的 DOM，用于禁用「下一步」和挂预设值说明。 */
  let currentPopover: PopoverDOM | null = null;
  /** 本步「不做完不放行」的拦截状态（填入预设值 / 选中文件两种门控共用）。 */
  let gateBlocked = false;
  /** 撤销上一步门控挂上去的监听与说明块。 */
  let releasePreset: (() => void) | null = null;
  /** 门控说明块，抖动提示时要用。 */
  let presetHint: HTMLElement | null = null;
  /** 门控满足后「延迟自动翻页」的定时器；离开本步必须撤销，否则会多翻一步。 */
  let advanceTimer: number | null = null;

  const clearPreset = () => {
    if (advanceTimer !== null) {
      window.clearTimeout(advanceTimer);
      advanceTimer = null;
    }
    releasePreset?.();
    releasePreset = null;
    gateBlocked = false;
    presetHint?.remove();
    presetHint = null;
  };

  /** 目标元素找不到时不能继续拦「下一步」，否则用户会被卡死在引导里。 */
  const releaseNextButton = () => {
    const button = currentPopover?.nextButton;
    if (!button) return;
    button.disabled = false;
    button.classList.remove("driver-popover-btn-disabled");
  };

  /** 未填预设值时按「下一步」：抖一下说明块提示用户，不前进。 */
  const pulsePresetHint = () => {
    if (!presetHint) return;
    presetHint.classList.remove("is-shake");
    // 读一次布局宽度强制回流，否则连续点击时动画不会重播。
    void presetHint.offsetWidth;
    presetHint.classList.add("is-shake");
  };

  /** 在提示卡里挂一块门控说明（两种门控共用同一套样式与抖动反馈）。 */
  const mountGateHint = (text: string): HTMLElement => {
    const hint = document.createElement("div");
    hint.className = PRESET_HINT_CLASS;
    hint.textContent = text;
    currentPopover?.wrapper.appendChild(hint);
    return hint;
  };

  /** 前进到下一步；跨页时先请求切页。门控自动放行与「下一步」按钮共用这一条路径。 */
  const goNextStep = () => {
    const index = tour?.getActiveIndex();
    if (index === undefined) return;
    switchPageFor(index, index + 1);
    tour?.moveNext();
  };

  /**
   * 放行门控（advanceOn = click / file）：先禁掉「下一步」，等用户在页面上真的做出
   * 对应动作后自动前进，不再要求他到提示卡上二次确认。
   *
   * 判定方式按模式分开：
   * - click：监听高亮区域内的点击（子元素冒泡上来也算），点到即完成；
   * - file：监听区域内 file input 的 change，真的选到文件才算完成。文件框的值由浏览器
   *   接管，脚本既读不到真实路径也写不进去，只能认「选过文件」这件事。
   */
  const applyAdvanceGate = (step: GuideStepConfig, element: Element) => {
    const mode = advanceModeOf(step);
    const input = mode === "file" ? findFileInput(element) : null;
    if (mode === "file" && !input) {
      // 高亮区域里根本没有文件选择框：不能拦人，否则用户会被卡死在引导里。
      releaseNextButton();
      return;
    }

    const waitingText = mode === "file" ? "请先选择文件再继续" : "请先点击高亮区域完成本步";
    const hint = mountGateHint(waitingText);
    presetHint = hint;
    const button = currentPopover?.nextButton;
    /** click 模式进入本步时不算完成，必须真的点过。 */
    let satisfied = false;

    const render = () => {
      // 键盘右方向键会绕过禁用按钮直接触发 onNextClick，因此拦截状态要单独记一份。
      gateBlocked = !satisfied;
      if (button) {
        button.disabled = !satisfied;
        button.classList.toggle("driver-popover-btn-disabled", !satisfied);
      }
      hint.textContent = satisfied ? "已完成，继续下一步" : waitingText;
      hint.classList.toggle("is-ok", satisfied);
    };

    /** 门控只放行一次：用户做完动作后自动翻页，重复触发不再排队。 */
    const onSatisfied = () => {
      if (satisfied) return;
      satisfied = true;
      render();
      // 刚做完动作就跳走会让人不确定自己点对没有，留一点时间把「已完成」显示出来。
      advanceTimer = window.setTimeout(() => {
        advanceTimer = null;
        goNextStep();
      }, GATE_ADVANCE_DELAY_MS);
    };

    const onEvent = () => {
      if (mode === "file" && !input?.files?.length) return;
      onSatisfied();
    };

    render();
    // 用户可能在进入本步之前就已经把文件选好了。
    if (mode === "file") onEvent();
    const eventName = mode === "file" ? "change" : "click";
    element.addEventListener(eventName, onEvent);
    releasePreset = () => element.removeEventListener(eventName, onEvent);
  };

  /** 播放到一步时执行它的门控与预设动作：自动填入、自动点开、以及「不做完不让走」。 */
  const applyStepPreset = (step: GuideStepConfig, highlighted: Element | undefined) => {
    const mode = presetModeOf(step);
    const advanceMode = advanceModeOf(step);
    if (mode === "off" && step.autoOpen !== true && advanceMode === "manual") return;

    const element = highlighted ?? resolveStepElement(step);
    if (!element || element.id === DUMMY_ELEMENT_ID) {
      releaseNextButton();
      return;
    }

    const value = step.presetValue ?? "";
    if (step.autoOpen === true) autoOpenControl(element);

    // 放行门控优先：它是「不放行」，与「预设值」同时配上没有意义。
    if (advanceMode !== "manual") {
      applyAdvanceGate(step, element);
      return;
    }

    if (!value) {
      // 只勾了「要求填入」却没写内容：不拦人，保存时后端也会拦下这种配置。
      if (mode === "require") releaseNextButton();
      return;
    }

    const control = findControl(element) ?? element;

    if (mode === "auto") {
      writeControlValue(control, value);
      return;
    }
    if (mode !== "require") return;

    const hint = mountGateHint(`请先填入「${value}」再继续`);
    presetHint = hint;
    const button = currentPopover?.nextButton;

    const sync = () => {
      const filled = readControlValue(control) === value;
      // 键盘右方向键会绕过禁用按钮直接触发 onNextClick，因此拦截状态要单独记一份。
      gateBlocked = !filled;
      if (button) {
        button.disabled = !filled;
        button.classList.toggle("driver-popover-btn-disabled", !filled);
      }
      hint.textContent = filled ? "已填入，可以继续" : `请先填入「${value}」再继续`;
      hint.classList.toggle("is-ok", filled);
    };

    sync();
    control.addEventListener("input", sync);
    control.addEventListener("change", sync);
    releasePreset = () => {
      control.removeEventListener("input", sync);
      control.removeEventListener("change", sync);
    };
  };

  /**
   * 掐掉落在表单控件上的方向键。
   *
   * driver.js 默认 allowKeyboardControl，把 ArrowRight / ArrowLeft 绑成翻页，且监听挂在
   * window 的冒泡阶段。用户在输入框里按左右方向键移动光标时，按键会一路冒泡到 window，
   * 引导就被「毫无操作」地翻到下一步——就是那种「我没点它自己跳了」的现象。
   * 在捕获阶段拦下这类按键（其余按键与输入行为原样保留），既修掉误跳又不牺牲键盘可用性。
   */
  const swallowArrowKeysInFields = (event: KeyboardEvent) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (!target.closest("input, textarea, select, [contenteditable='true']")) return;
    event.stopPropagation();
  };
  window.addEventListener("keydown", swallowArrowKeysInFields, true);

  tour = driver({
    steps,
    animate: true,
    duration: 320,
    overlayColor: "#0b2136",
    overlayOpacity: 0.62,
    stagePadding: 8,
    stageRadius: 14,
    popoverOffset: 12,
    smoothScroll: true,
    allowClose: true,
    showProgress: true,
    progressText: "{{current}} / {{total}}",
    nextBtnText: "下一步",
    prevBtnText: "上一步",
    doneBtnText: "开始使用",
    showButtons: ["next", "previous", "close"],
    // 提示卡每次重绘都换一套 DOM，这里跟着记下最新的，预设值逻辑要用它禁用「下一步」。
    onPopoverRender: (popover) => {
      currentPopover = popover;
      // 右上角的关闭按钮就是「跳过这段引导」，补个说明让用户知道点了会发生什么。
      popover.closeButton.title = "跳过这段引导";
      popover.closeButton.setAttribute("aria-label", "跳过这段引导");
    },
    onHighlighted: (element, _step, opts) => {
      const index = opts.index ?? 0;
      const step = task.steps[index];
      reachedLastStep = index >= steps.length - 1;
      clearPreset();
      if (step) applyStepPreset(step, element);
    },
    onNextClick: () => {
      // 键盘右方向键不走按钮，所以门控必须在这里再拦一道。
      if (gateBlocked) {
        pulsePresetHint();
        return;
      }
      goNextStep();
    },
    onPrevClick: () => {
      const index = tour?.getActiveIndex();
      if (index === undefined) return;
      switchPageFor(index, index - 1);
      tour?.movePrevious();
    },
    onDestroyed: () => {
      clearPreset();
      window.removeEventListener("keydown", swallowArrowKeysInFields, true);
      onFinish?.(reachedLastStep);
    },
  });

  // 子任务首步页可能不是当前页（如从 AI 处理切回采集），先请求切页再高亮。
  const firstPage = stepPages[0];
  if (firstPage) onRequestPage(firstPage);
  tour.drive(0);
  return tour;
}

type GuideBoardPanelProps = {
  onClose: () => void;
  onStartSubTask: (boardId: GuideBoardId, subTaskId: GuideSubTaskId) => void;
  /** 管理员专属：打开引导编辑器补充/修改教程内容。 */
  onEdit?: () => void;
};

/** 板块选择面板：一级板块展开后是二级子任务，每个子任务都能单独再看一遍。 */
export function GuideBoardPanel({ onClose, onStartSubTask, onEdit }: GuideBoardPanelProps) {
  const progress = getGuideProgress();
  // 默认展开「还有子任务没看完」的板块，其余收起，面板保持紧凑。
  const [expandedBoards, setExpandedBoards] = useState<GuideBoardId[]>(() =>
    GUIDE_BOARD_META.filter((board) => isGuideBoardReady(board.id) && !isGuideBoardDone(board.id)).map(
      (board) => board.id,
    ),
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const toggleBoard = (boardId: GuideBoardId) => {
    setExpandedBoards((current) =>
      current.includes(boardId) ? current.filter((id) => id !== boardId) : [...current, boardId],
    );
  };

  return (
    <div
      className="guide-board-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="新手引导"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="guide-board-panel">
        <header className="guide-board-head">
          <div className="guide-board-heading">
            <h2>新手引导</h2>
            <p>按板块挑一个开始，每个板块里的子任务都能单独再看一遍。</p>
          </div>
          <span className="guide-board-progress">
            已完成 <strong>{progress.done}</strong> / {progress.total}
          </span>
          <button type="button" className="guide-board-close" onClick={onClose} aria-label="关闭新手引导">
            ×
          </button>
        </header>
        <ul className="guide-board-list">
          {GUIDE_BOARD_META.map((board, index) => {
            const subTasks = guideSubTasks(board.id);
            const done = isGuideBoardDone(board.id);
            const ready = isGuideBoardReady(board.id);
            const open = expandedBoards.includes(board.id);
            return (
              <li key={board.id} className={`guide-board-card${done ? " is-done" : ""}${ready ? "" : " is-pending"}`}>
                <div className="guide-board-row">
                  <span className="guide-board-index" aria-hidden="true">{done ? "✓" : index + 1}</span>
                  <div className="guide-board-body">
                    <h3>
                      <i className={board.iconClass} aria-hidden="true" />
                      {board.label}
                    </h3>
                    <p>{board.description}</p>
                  </div>
                  <button
                    type="button"
                    className="guide-board-start"
                    onClick={() => toggleBoard(board.id)}
                    disabled={!ready}
                    aria-expanded={ready ? open : undefined}
                  >
                    {ready ? (open ? "收起教程" : "展开教程") : "引导准备中"}
                  </button>
                </div>
                {ready && open && (
                  <ul className="guide-board-subtasks">
                    {subTasks.map((task) => {
                      const taskReady = task.steps.length > 0;
                      const taskDone = taskReady && isGuideSubTaskDone(board.id, task.id);
                      return (
                        <li
                          key={task.id}
                          className={`guide-board-subtask${taskReady ? "" : " is-pending"}`}
                        >
                          <span className="guide-board-subtask-name">{task.label}</span>
                          <span className="guide-board-subtask-state">
                            {taskReady ? (taskDone ? "已看完" : "未看过") : "教程准备中"}
                          </span>
                          <button
                            type="button"
                            className="guide-board-subtask-start"
                            onClick={() => onStartSubTask(board.id, task.id)}
                            disabled={!taskReady}
                          >
                            {taskReady ? (taskDone ? "再看一遍" : "开始引导") : "准备中"}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ul>
        <footer className="guide-board-foot">
          {onEdit && (
            <button type="button" className="guide-board-edit" onClick={onEdit}>
              编辑引导
            </button>
          )}
          <button type="button" className="guide-board-skip" onClick={onClose}>
            跳过，直接进入工作台
          </button>
        </footer>
      </div>
    </div>
  );
}
