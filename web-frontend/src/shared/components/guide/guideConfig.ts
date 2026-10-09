/**
 * 新手引导的配置模型与读写。
 *
 * 一级板块（id / 名称 / 图标 / 描述）写死在 GuideTour.tsx，与工作台导航结构绑定；
 * 这里只描述「板块下有哪些子任务、每个子任务有哪些步骤」，并负责与服务端同步。
 * 服务端还没保存过配置时回退到内置默认值，保证全新安装开箱就有引导可用。
 */

import { httpJson } from "../../../transport/http/client";

export type GuideSide = "top" | "bottom" | "left" | "right";
export type GuideAlign = "start" | "center" | "end";

/**
 * 步骤里「预设值」的处理方式：
 * - off：不涉及预设值；
 * - require：目标控件必须是预设值才能点「下一步」，引导同时把值告诉用户；
 * - auto：进入本步时引导直接把预设值写进目标控件，用户不用手打。
 */
export type GuidePresetMode = "off" | "require" | "auto";

/**
 * 步骤的放行方式：决定「什么算这一步做完了」。
 * - manual：不做判定，用户点提示卡上的「下一步」自己走；
 * - click：用户点了高亮区域里的可点元素才算完成，完成后自动前进；
 * - file：用户在高亮区域里选到文件才算完成，完成后自动前进。
 *
 * click / file 既是拦截（没做完点不动「下一步」），也是验收（做完自动翻页，
 * 不再要求用户在提示卡上二次确认）。这不能用 presetMode 顶替：那个门控是拿
 * 「控件的值」和 presetValue 比对，只对输入类控件成立，按钮点没点、文件选没选
 * 它都看不见。
 */
export type GuideAdvanceMode = "manual" | "click" | "file";

/** 引导步骤的所在页面；取值是工作台模块 id，跨页时前端据此切页。 */
export type GuidePageId = string;

export type GuideStepConfig = {
  /** 候选选择器：按顺序取第一个「真实可见」的命中元素，前者失效时自动回退。 */
  selectors: string[];
  /** 本步所在页面；留空表示不切页。 */
  page: GuidePageId;
  title: string;
  description: string;
  side: GuideSide;
  align: GuideAlign;
  /**
   * 本步需要用户先在页面上填写/操作才能继续（如填采集条件、勾选商品）。
   * 播放到这类步骤时引导会放开蒙版的指针拦截，用户能照常点输入框、点按钮，
   * 操作完再点提示卡上的「下一步」。缺省视为只读讲解。
   */
  interactive?: boolean;
  /**
   * 预设值的处理方式，缺省 off（见 GuidePresetMode）。
   * require 时用户没把目标控件填成 presetValue 就点不动「下一步」；
   * auto 时进入本步引导会自动把 presetValue 填进去。
   */
  presetMode?: GuidePresetMode;
  /** 预设值：require 时是要求用户填入的内容，auto 时是引导自动填入的内容。 */
  presetValue?: string;
  /**
   * 进入本步时自动点开目标控件（如下拉框）。
   * 这类控件不点开就看不到选项，让引导替用户点一下更顺。
   */
  autoOpen?: boolean;
  /**
   * 本步的放行方式，缺省 manual（用户自己点「下一步」）。
   * 见 GuideAdvanceMode：click / file 会先禁掉「下一步」，用户真的做出对应动作后自动前进。
   */
  advanceOn?: GuideAdvanceMode;
};

export type GuideSubTaskConfig = {
  id: string;
  label: string;
  /** 空数组表示教程还没做，面板里置灰显示「准备中」。 */
  steps: GuideStepConfig[];
};

export type GuideBoardConfig = {
  subTasks: GuideSubTaskConfig[];
};

export type GuideConfig = {
  version: number;
  /** key 是板块 id（GuideBoardId）。 */
  boards: Record<string, GuideBoardConfig>;
};

export const GUIDE_SIDE_LABELS: Record<GuideSide, string> = {
  bottom: "下方",
  top: "上方",
  left: "左侧",
  right: "右侧",
};

export const GUIDE_ALIGN_LABELS: Record<GuideAlign, string> = {
  start: "靠前",
  center: "居中",
  end: "靠后",
};

export const GUIDE_PRESET_MODE_LABELS: Record<GuidePresetMode, string> = {
  off: "不涉及预设值",
  require: "必须填入才能下一步",
  auto: "引导自动填入",
};

export const GUIDE_ADVANCE_MODE_LABELS: Record<GuideAdvanceMode, string> = {
  manual: "用户自己点「下一步」",
  click: "点一下高亮区域就自动下一步",
  file: "选中文件就自动下一步",
};

/** 步骤数量上限等约束与后端 service.py 保持一致，避免提交后才被拒。 */
export const GUIDE_LIMITS = {
  subTaskLabel: 40,
  stepTitle: 60,
  stepDescription: 300,
  stepPresetValue: 120,
  selectorsPerStep: 5,
  stepsPerSubTask: 40,
  subTasksPerBoard: 40,
} as const;

/** 内置默认引导：服务端没有配置时使用，内容等同于改造前的写死版本。 */
export function defaultGuideConfig(): GuideConfig {
  return {
    version: 1,
    boards: {
      product_workflow: {
        subTasks: [
          {
            id: "collect",
            label: "采集",
            steps: [
              {
                selectors: [".daily-collection-workspace .daily-page-heading"],
                page: "daily_selection",
                title: "每日选品",
                description: "在这一步我们来采集需要批量出图的链接",
                side: "bottom",
                align: "start",
              },
              {
                selectors: [
                  ".daily-collection-workspace .collection-primary-fields",
                  ".daily-collection-surface",
                ],
                page: "daily_selection",
                title: "采集条件",
                description: "填写一些关键信息点击开始采集",
                side: "bottom",
                align: "center",
                interactive: true,
              },
              {
                selectors: [".daily-collection-workspace .results-actions .confirm-button"],
                page: "daily_selection",
                title: "确认入池",
                description: "勾选完之后点击确认入池",
                side: "bottom",
                align: "end",
                interactive: true,
              },
            ],
          },
          {
            id: "ai_process",
            label: "AI处理",
            steps: [
              {
                selectors: [".verify-page .verify-section"],
                page: "product_processing",
                title: "草稿池",
                description: "刚添加完的商品链接就会出现在草稿池啦",
                side: "bottom",
                align: "center",
              },
              {
                selectors: [".verify-page .verify-actions button.primary"],
                page: "product_processing",
                title: "开始处理",
                description: "选好商品之后就可以开始处理啦",
                side: "bottom",
                align: "end",
                interactive: true,
              },
            ],
          },
          { id: "history", label: "历史记录", steps: [] },
          { id: "dimension_canvas", label: "尺寸画布", steps: [] },
        ],
      },
      pod_customization: { subTasks: [] },
      sourcing_workflow: {
        subTasks: [{ id: "temu_quote", label: "拉取 Temu 核价信息", steps: [] }],
      },
    },
  };
}

export function cloneGuideConfig(config: GuideConfig): GuideConfig {
  // 配置是纯 JSON 结构，直接序列化克隆即可，也顺带丢掉外部引用。
  return JSON.parse(JSON.stringify(config)) as GuideConfig;
}

/** 服务端数据被手工改坏时的兜底：结构不对就当作「没配过」，回退内置默认值。 */
function isUsableConfig(value: unknown): value is GuideConfig {
  if (!value || typeof value !== "object") return false;
  const boards = (value as GuideConfig).boards;
  return !!boards && typeof boards === "object" && !Array.isArray(boards);
}

export type GuideConfigSnapshot = {
  config: GuideConfig | null;
  updatedBy: string;
  updatedAt: string;
};

type GuideConfigResponse = {
  ok?: boolean;
  config?: unknown;
  updated_by?: string;
  updated_at?: string;
};

export async function fetchGuideConfig(): Promise<GuideConfigSnapshot> {
  const payload = await httpJson<GuideConfigResponse>("/api/guide/config");
  return {
    config: isUsableConfig(payload.config) ? payload.config : null,
    updatedBy: payload.updated_by ?? "",
    updatedAt: payload.updated_at ?? "",
  };
}

export async function saveGuideConfig(config: GuideConfig): Promise<GuideConfigSnapshot> {
  const payload = await httpJson<GuideConfigResponse>("/api/guide/config", {
    method: "PUT",
    body: config,
  });
  return {
    config: isUsableConfig(payload.config) ? payload.config : config,
    updatedBy: payload.updated_by ?? "",
    updatedAt: payload.updated_at ?? "",
  };
}

/**
 * 当前生效的引导配置（播放引导时读它）。
 * 由 WorkspaceShell 在加载完服务端配置后写入，编辑器的「预览」也会临时覆盖它。
 */
let activeConfig: GuideConfig | null = null;

export function setActiveGuideConfig(config: GuideConfig | null): void {
  activeConfig = config;
}

/** 返回生效配置；未设置时是新建的默认配置，调用方不要直接改动它。 */
export function getActiveGuideConfig(): GuideConfig {
  return activeConfig ?? defaultGuideConfig();
}

export function getGuideBoardConfig(config: GuideConfig, boardId: string): GuideBoardConfig {
  return config.boards[boardId] ?? { subTasks: [] };
}

export function getGuideSubTaskConfig(
  config: GuideConfig,
  boardId: string,
  subTaskId: string,
): GuideSubTaskConfig | undefined {
  return getGuideBoardConfig(config, boardId).subTasks.find((task) => task.id === subTaskId);
}

/** 生成一个板块内不重复的子任务 id（子任务 id 参与完成标记的存储，必须稳定且唯一）。 */
export function nextSubTaskId(board: GuideBoardConfig): string {
  const used = new Set(board.subTasks.map((task) => task.id));
  let index = board.subTasks.length + 1;
  while (used.has(`task_${index}`)) index += 1;
  return `task_${index}`;
}
