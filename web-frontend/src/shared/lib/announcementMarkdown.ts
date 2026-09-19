// 公告正文的 Markdown 渲染。
//
// 后台发布页（wh-admin，原生 JS、无构建）用 marked 的 UMD 版本渲染预览，
// 这里保持**完全相同的 marked 配置与清洗策略**，两端输出同一份 HTML，
// 再各自套同一套 Changelog 卡片风样式，做到「后台预览 = 客户端弹窗」。
import DOMPurify from "dompurify";
import { Marked } from "marked";
import type { Token, TokenizerAndRendererExtension } from "marked";

/** 提示块类型 → 默认标题（`::: tip` 不写标题时用它）。 */
const CALLOUT_TITLES: Record<string, string> = {
  tip: "小贴士",
  info: "说明",
  warning: "注意",
  danger: "重要提醒",
};

type CalloutToken = {
  type: "callout";
  raw: string;
  kind: string;
  titleTokens: Token[];
  tokens: Token[];
};

/**
 * VitePress 风格的提示块：
 *
 * ```
 * ::: tip 领取方式
 * 每周签到即可领取 **500 积分**。
 * :::
 * ```
 */
const calloutExtension: TokenizerAndRendererExtension = {
  name: "callout",
  level: "block",
  start(src) {
    const index = src.search(/^:::[ \t]*[a-zA-Z]+/m);
    return index < 0 ? undefined : index;
  },
  tokenizer(src) {
    const match = /^:::[ \t]*([a-zA-Z]+)[ \t]*([^\n]*)\n([\s\S]*?)\n:::[ \t]*(?:\n+|$)/.exec(src);
    if (!match) return undefined;
    const kind = match[1].toLowerCase();
    if (!(kind in CALLOUT_TITLES)) return undefined;
    const title = match[2].trim();
    return {
      type: "callout",
      raw: match[0],
      kind,
      titleTokens: title ? this.lexer.inlineTokens(title) : [],
      tokens: this.lexer.blockTokens(match[3].trim(), []),
    } as unknown as Token;
  },
  renderer(token) {
    const callout = token as unknown as CalloutToken;
    const titleHtml = callout.titleTokens.length
      ? this.parser.parseInline(callout.titleTokens)
      : CALLOUT_TITLES[callout.kind];
    return (
      `<div class="ann-callout is-${callout.kind}">` +
      `<p class="ann-callout-title">${titleHtml}</p>` +
      `<div class="ann-callout-body">${this.parser.parse(callout.tokens)}</div>` +
      `</div>\n`
    );
  },
};

// breaks: true —— 正文里手打的单个换行也算换行，老公告（纯文本换行）继续按原样显示。
const marked = new Marked({ gfm: true, breaks: true });
marked.use({ extensions: [calloutExtension] });

/**
 * 兼容老公告的写法。历史内容习惯敲成 `1.对免费用户更友好`、`-要点`（标记后不带空格），
 * 而 GFM 要求标记后必须有空格才算列表，否则整段会挤成一坨纯文本 —— 这正是老公告「看着很朴素」的原因。
 * 这里在交给 marked 之前补一个空格，让老公告也能吃到新的排版；只兜底最明确的情况，避免误伤正文：
 *   - 有序：仅 1~9 且后面紧跟的不是数字/空白（`1.4.6版本`、`2.0版` 不受影响）
 *   - 无序：仅 `-` / `+` 且后面紧跟中文或英文字母（`---` 分割线、`*斜体*`、`-20 度` 不受影响）
 *   - 围栏代码块内保持原样
 */
const LEGACY_ORDERED_RE = /^([1-9])\.(?=[^\s\d])/;
const LEGACY_BULLET_RE = /^([-+])(?=[\u4e00-\u9fffA-Za-z])/;

function normalizeLegacyMarkers(source: string): string {
  let inFence = false;
  return source
    .split("\n")
    .map((line) => {
      if (/^\s*(?:```|~~~)/.test(line)) {
        inFence = !inFence;
        return line;
      }
      if (inFence) return line;
      return line.replace(LEGACY_ORDERED_RE, "$1. ").replace(LEGACY_BULLET_RE, "$1 ");
    })
    .join("\n");
}

/** 把公告正文（Markdown）渲染成可直接插入 DOM 的 HTML 字符串。 */
export function renderAnnouncementHtml(markdown: string): string {
  const source = markdown?.trim();
  if (!source) return "";
  const html = marked.parse(normalizeLegacyMarkers(source)) as string;
  return DOMPurify.sanitize(html);
}

/** 消息中心列表专用：把 Markdown 压成一行纯文本摘要，避免露出 `**`、`-` 这类记号。 */
export function toAnnouncementSummary(markdown: string): string {
  const source = markdown?.trim();
  if (!source) return "";
  return source
    .replace(/^:::[ \t]*[a-zA-Z]+[^\n]*$/gm, "")
    .replace(/^[ \t]*#{1,6}[ \t]*/gm, "")
    .replace(/^[ \t]*>[ \t]?/gm, "")
    .replace(/^[ \t]*[-*+][ \t]+/gm, "")
    .replace(/^[ \t]*\d+[.)][ \t]+/gm, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__)(.*?)\1/g, "$2")
    .replace(/(\*|_)(.*?)\1/g, "$2")
    .replace(/`{1,3}([^`]*)`{1,3}/g, "$1")
    .replace(/^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}
