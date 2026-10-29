import type { PersonalCenterPanelId, WorkspaceModuleId } from "../../app/navigation/modules";
import { BRAND_MANUAL_URL } from "../brand";

/**
 * 工作台推广位配置。
 *
 * 只放「自营 / 合作方」的静态推广位，不加载任何第三方广告脚本：
 * 工作台是登录后使用的桌面端应用，没有可供广告联盟审核的公开内容页，
 * AdSense、百度联盟这类按页面内容计费的联盟进来容易被判无效流量。
 * 需要接合作方素材时，把文案、落地页填进下面的条目即可，组件不用改。
 *
 * 上线 / 下线一条推广只改这个文件：增删数组项、调整顺序即可。
 */
export type AdSlotId = "sidebar" | "footer";

/** 点击推广后去哪里：工作台模块、个人中心面板，或外部落地页。 */
export type AdTarget =
  | { type: "module"; moduleId: WorkspaceModuleId }
  | { type: "panel"; panelId: PersonalCenterPanelId }
  | { type: "external"; href: string };

export type AdItem = AdCardItem | AdCodeItem;

export type AdBase = {
  id: string;
  /** 左上角角标。广告法要求可识别，默认「推广」。 */
  tag?: string;
};

/** 形态一：图片 + 链接（联盟 banner、直投素材都属于这种）。 */
export type AdCardItem = AdBase & {
  kind?: "card";
  title: string;
  description?: string;
  ctaLabel: string;
  target: AdTarget;
  /** banner 图地址；不填就是纯文字卡。 */
  imageUrl?: string;
  imageAlt?: string;
};

/** 形态二：第三方广告代码（AdSense、部分联盟），整段代码原样执行。 */
export type AdCodeItem = AdBase & {
  kind: "code";
  /** 仅用于关闭按钮和读屏的标题，不会显示成卡片文案。 */
  title?: string;
  customHtml: string;
};

/** 同一位置配置多条时按顺序展示：关闭一条后自动轮到下一条，全部关闭后不再出现。 */
export const AD_ITEMS: Record<AdSlotId, AdItem[]> = {
  sidebar: [
    {
      id: "sidebar-topup",
      title: "积分充值",
      description: "余额不足会中断任务，随时补充。",
      ctaLabel: "去充值",
      target: { type: "panel", panelId: "wallet" },
    },
    {
      id: "sidebar-ai-video",
      tag: "新功能",
      title: "AI 视频",
      description: "脚本、素材到成片，批量出片。",
      ctaLabel: "去看看",
      target: { type: "module", moduleId: "ai_video" },
    },
  ],
  footer: [
    {
      id: "footer-pod-replica",
      tag: "推荐",
      title: "POD 爆款复刻",
      description: "上传爆款链接，一键复刻同款商品图与标题，直接进处理流程。",
      ctaLabel: "试一次",
      target: { type: "module", moduleId: "pod_replica" },
    },
    {
      id: "footer-manual",
      title: "新手使用手册",
      description: "从装插件、连工作台到导出店小秘，全流程图文说明。",
      ctaLabel: "查看手册",
      target: { type: "external", href: BRAND_MANUAL_URL },
    },
  ],
};

/*
 * 接入真实广告时，把下面任意一种写法加到对应插槽的数组里即可：
 *
 * 1) 图片 + 链接（直投素材、联盟 banner）
 *    {
 *      id: "footer-partner-a",
 *      tag: "广告",
 *      imageUrl: "https://cdn.example.com/banner.png", // 平台/合作方给的图片地址
 *      imageAlt: "合作方名称",
 *      title: "合作方名称",
 *      description: "一句话卖点",
 *      ctaLabel: "去看看",
 *      target: { type: "external", href: "https://partner.example.com/?utm_source=mainpg" },
 *    }
 *
 * 2) 第三方广告代码（广告后台复制整段，原样粘贴，脚本会自动执行）
 *    {
 *      id: "footer-adsense",
 *      kind: "code",
 *      customHtml: `
 *        <ins class="adsbygoogle" style="display:block" data-ad-client="ca-pub-xxxx" data-ad-slot="1234567890" data-ad-format="auto" data-full-width-responsive="true"></ins>
 *        <script>(adsbygoogle = window.adsbygoogle || []).push({});</script>
 *      `,
 *    }
 *
 * 图片位的点击统一走上面的 external 目标并在新窗口打开；代码位里的链接由广告平台自己处理。
 */

const DISMISS_STORAGE_KEY = "mainpg_ad_dismissed_v1";

/** 读取本机已关闭的推广 id；localStorage 不可用（隐私模式）时按未关闭处理。 */
export function readDismissedAdIds(): string[] {
  try {
    const raw = window.localStorage.getItem(DISMISS_STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((id): id is string => typeof id === "string") : [];
  } catch {
    return [];
  }
}

export function dismissAd(id: string): string[] {
  const next = Array.from(new Set([...readDismissedAdIds(), id]));
  try {
    window.localStorage.setItem(DISMISS_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // 隐私模式等场景写不进去：仅本次会话内生效。
  }
  return next;
}

export type AdEventType = "impression" | "click";

export const AD_EVENT_NAME = "mainpg:ad-event";

/**
 * 曝光 / 点击只派发本地事件，不请求任何第三方。
 * 后续要做结算或统计时，监听该事件上报到自建接口即可。
 */
export function reportAdEvent(type: AdEventType, slot: AdSlotId, id: string): void {
  window.dispatchEvent(new CustomEvent(AD_EVENT_NAME, { detail: { type, slot, id, at: Date.now() } }));
}
