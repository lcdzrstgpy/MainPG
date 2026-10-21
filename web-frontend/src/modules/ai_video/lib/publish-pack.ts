/**
 * 来源：integrations/clipforge/src/lib/publish-pack.ts（只读同步；改动请先同步上游）
 * 免 Key 发布文案模板：按品类 + 平台拼标题/话题/正文/评论区话术，LLM 不可用时仍可复制发布。
 */

import { buildShopLink } from "./shop-link.ts";

export interface PublishPack {
  titles: string[];
  hashtags: string[];
  caption: string;
  shopLink?: string;
  aiDeclaration: { notice: string; line: string };
  commentKit: CommentKit;
}

export interface CommentKit {
  pinned: string;
  objections: { q: string; a: string }[];
  notice: string;
}

export interface PublishPackInput {
  productName?: string;
  category?: string;
  sellingPoints?: string;
  platform?: string;
  locale?: "zh" | "en";
  shopUrl?: string;
  affiliateCode?: string;
}

const CATEGORY_TAGS: Record<string, string[]> = {
  beauty: ["好物分享", "美妆", "护肤", "变美", "平价好物", "种草"],
  food: ["美食", "好吃推荐", "零食", "吃货日常", "干饭人", "种草"],
  home: ["家居好物", "居家生活", "生活好物", "收纳", "好物推荐", "种草"],
  fashion: ["穿搭", "时尚", "OOTD", "穿搭分享", "好物分享", "种草"],
  digital: ["数码", "数码好物", "科技", "实用好物", "好物推荐", "种草"],
  other: ["好物推荐", "种草", "好物分享", "值得买", "宝藏好物", "日常分享"],
};

const CATEGORY_TAGS_EN: Record<string, string[]> = {
  beauty: ["BeautyTok", "SkincareRoutine", "MakeupHacks", "BeautyFinds", "GlowUp", "TikTokMadeMeBuyIt"],
  food: ["FoodTok", "FoodieFinds", "SnackHaul", "TikTokFood", "MustTry", "TikTokMadeMeBuyIt"],
  home: ["HomeFinds", "HomeHacks", "CleanTok", "OrganizationTips", "CozyHome", "TikTokMadeMeBuyIt"],
  fashion: ["OOTD", "FashionTok", "StyleInspo", "OutfitIdeas", "FashionFinds", "TikTokMadeMeBuyIt"],
  digital: ["TechTok", "GadgetFinds", "TechReview", "CoolGadgets", "Innovation", "TikTokMadeMeBuyIt"],
  other: ["TikTokMadeMeBuyIt", "MustHave", "ProductReview", "WorthIt", "TikTokFinds", "DailyFinds"],
};

const PLATFORM_TAGS: Record<string, string[]> = {
  douyin: ["抖音好物", "抖音电商"],
  kuaishou: ["快手好物", "快手电商"],
  xiaohongshu: ["小红书", "好物推荐"],
  shipinhao: ["视频号", "视频号好物", "视频号小店"],
  tiktok: ["TikTokMadeMeBuyIt", "TikTokShop"],
  reels: ["Reels", "InstagramReels", "ReelsFinds"],
  shorts: ["Shorts", "YouTubeShorts"],
};

function firstSellingPoint(sp: string | undefined, max: number): string {
  if (!sp) return "";
  const first = sp.split(/[。.,，;；\n、]/).map((s) => s.trim()).find((s) => s.length > 0) || "";
  return clip(first, max);
}

function clip(s: string, max: number): string {
  const arr = Array.from(s.trim());
  return arr.length <= max ? s.trim() : arr.slice(0, max).join("").trim();
}

export function buildPublishPrompt(
  input: { productName: string; category?: string; productDescription?: string; platform?: string },
  locale: "zh" | "en" = "zh",
): string {
  const { productName, category, productDescription, platform } = input;
  if (locale === "en") {
    const platformHint = platform ? `Target platform: ${platform}.` : "Target platform: TikTok / Reels / Shorts.";
    return `You are a seasoned e-commerce short-video marketer. Write publishing copy for the product below, entirely in ENGLISH. ${platformHint}
Product: ${productName}
${category ? `Category: ${category}\n` : ""}${productDescription ? `Selling points: ${productDescription}\n` : ""}
Output STRICT JSON only (no extra text):
{
  "titles": ["3 catchy short titles with emotion/pain-point/number hooks, each <= 60 chars"],
  "hashtags": ["6-10 hashtags with #, TikTok-style; the FIRST must be a product-specific/branded hashtag (the product name, no spaces) for keyword-search discovery, the rest matching category and platform trends"],
  "caption": "one-line caption, conversational, with a clear call to action, <= 150 chars; lead with the main product keyword in the first ~30 characters for search discoverability",
  "commentKit": {
    "pinned": "a pinned self-Q&A comment: raise THE question buyers hesitate on, answer it from the creator's first-person real experience, invite questions; <= 200 chars",
    "objections": [{ "q": "recurring objection (price / does it work / hesitation)", "a": "friendly first-person reply, honest, no invented claims, <= 120 chars" }]
  }
}
commentKit rules: comments are the video's second landing page. 2-3 objections. NEVER write fake customer testimonials or seeded "I bought it and love it" comments — first-person creator replies only.`;
  }
  const platformHint = platform ? `目标平台：${platform}。` : "目标平台：抖音/快手/小红书。";
  return `你是资深电商带货短视频运营。请为以下商品生成发布文案。${platformHint}
商品名称：${productName}
${category ? `品类：${category}\n` : ""}${productDescription ? `卖点：${productDescription}\n` : ""}
要求严格输出 JSON（不要多余文字）：
{
  "titles": ["3 个吸睛短标题，含情绪/痛点/数字钩子，每个 ≤20 字"],
  "hashtags": ["6-10 个带 # 的话题标签；第 1 个必须是商品专属/品牌标签（商品名、不含空格），利于商品词搜索发现，其余贴合品类与平台热点"],
  "caption": "一句话种草文案，口语化，含行动号召，≤40 字；开头先点出商品核心关键词（利于平台搜索发现）",
  "commentKit": {
    "pinned": "一条置顶自问自答评论：提出买家最犹豫的那个问题，用博主第一人称真实体验回答，并邀请提问；≤80 字",
    "objections": [{ "q": "高频异议（价格贵/有没有用/还在犹豫）", "a": "第一人称友好回复，诚实不编造，≤50 字" }]
  }
}
commentKit 规则：评论区是视频的第二落地页，异议给 2-3 条；绝不写伪造顾客证言或「已买真香」式预埋假评论——只写博主第一人称回复。`;
}

const TITLE_POOL_ZH: Array<{ needsPoint?: boolean; render: (n: string, p: string) => string }> = [
  { render: (n) => `${n}也太好用了吧！后悔没早买` },
  { needsPoint: true, render: (n, p) => `${n}｜${p}，谁用谁回购` },
  { render: (n) => `三个理由让你入手${n}` },
  { render: (n) => `谁懂啊！${n}真的绝了` },
  { render: (n) => `别乱买了，${n}闭眼入不踩雷` },
  { render: (n) => `${n}凭什么这么火？` },
  { render: (n) => `用了${n}才知道之前白买了` },
  { render: (n) => `姐妹们冲！${n}平价宝藏` },
  { needsPoint: true, render: (n, p) => `${n}测评｜${p}` },
  { render: (n) => `入手${n}前，先看这条` },
];
const TITLE_POOL_EN: Array<{ needsPoint?: boolean; render: (n: string, p: string) => string }> = [
  { render: (n) => `This ${n} is a total game-changer 🤯` },
  { needsPoint: true, render: (n, p) => `${n} — ${p}, you'll want one` },
  { render: (n) => `3 reasons to grab the ${n}` },
  { render: (n) => `I can't stop using this ${n}` },
  { render: (n) => `Why is everyone obsessed with ${n}?` },
  { render: (n) => `The ${n} you won't regret buying` },
  { needsPoint: true, render: (n, p) => `${n}: ${p}` },
  { render: (n) => `Don't buy another until you've seen this ${n}` },
];

function hashStr(s: string): number {
  let h = 0;
  for (const c of s) h = (h + c.charCodeAt(0)) >>> 0;
  return h;
}

export function pickTitles(name: string, point: string, en: boolean): string[] {
  const pool = (en ? TITLE_POOL_EN : TITLE_POOL_ZH).filter((t) => point || !t.needsPoint);
  const start = hashStr(name) % pool.length;
  const out: string[] = [];
  for (let i = 0; i < 3; i++) {
    const s = pool[(start + i) % pool.length].render(name, point);
    out.push(en ? clip(s, 60) : clip(s, 22));
  }
  return out;
}

export function buildCommentKit(input: PublishPackInput): CommentKit {
  const en = input.locale === "en";
  const name = clip((input.productName || "").trim() || (en ? "this find" : "这款好物"), en ? 40 : 16);
  const point = firstSellingPoint(input.sellingPoints, en ? 40 : 12);
  if (en) {
    return {
      pinned: `Most-asked question first: is the ${name} actually worth it? I've been using it myself${point ? ` — ${point}` : ""}, ask me anything below 👇`,
      objections: [
        {
          q: "Too expensive / not worth it",
          a: "Break it down per use and it's less than a coffee — and you can return it if it's not for you.",
        },
        {
          q: "Does it really work?",
          a: `Fair question — the video shows exactly how I use it${point ? ` (${point})` : ""}. Happy to post a follow-up after longer use.`,
        },
        {
          q: "Still hesitating",
          a: "No rush — save this video, check the reviews, and grab it when you're ready.",
        },
      ],
      notice:
        "Reply with your real experience only — fabricated customer testimonials and seeded fake comments are an enforcement target on every platform.",
    };
  }
  return {
    pinned: `评论区问得最多的先答：${name}到底值不值？我自己在用${point ? `，${point}` : ""}，有问题评论区直接问👇`,
    objections: [
      { q: "太贵了/不值", a: "拆到每次使用算一下，比一杯奶茶还便宜；不合适也支持退，先看再定。" },
      { q: "真的有用吗", a: `问得好——视频里就是我的真实用法${point ? `（${point}）` : ""}，用久了我再来追评。` },
      { q: "还在犹豫", a: "不着急，先收藏这条，看看评价，想好了再入。" },
    ],
    notice: "回复只写自己的真实体验——伪造顾客证言、预埋假评论是各平台重点打击项，别碰。",
  };
}

export function buildPublishPack(input: PublishPackInput): PublishPack {
  const en = input.locale === "en";
  const name = clip((input.productName || "").trim() || (en ? "this find" : "这款好物"), en ? 40 : 16);
  const cat = (input.category || "other").toLowerCase();
  const point = firstSellingPoint(input.sellingPoints, en ? 40 : 12);

  const titles = pickTitles(name, point, en);

  const platform = (input.platform || "").toLowerCase();
  const catTags = en ? CATEGORY_TAGS_EN : CATEGORY_TAGS;
  const rawName = (input.productName || "").trim();
  const productTag = rawName ? `#${clip(rawName.replace(/[^\p{L}\p{N}]/gu, ""), en ? 24 : 12)}` : "";
  const tagWords = [
    ...(catTags[cat] || catTags.other),
    ...(PLATFORM_TAGS[platform] || []),
  ];
  const seen = new Set<string>();
  const hashtags: string[] = [];
  for (const tag of [productTag, ...tagWords.map((w) => `#${w}`)]) {
    if (!tag || tag === "#" || seen.has(tag)) continue;
    seen.add(tag);
    hashtags.push(tag);
    if (hashtags.length >= 10) break;
  }

  const cta = en ? " — tap the link below to grab it 🛒" : "，点下方小黄车带走它～";
  const lead = en
    ? `Obsessed with ${name}${point ? ", " + point : ""}`
    : `${name}真的绝了${point ? "，" + point : ""}`;
  const capMax = en ? 130 : 40;
  const caption = clip(lead, capMax - Array.from(cta).length) + cta;

  const shopLink = buildShopLink(input.shopUrl, { platform, affiliateCode: input.affiliateCode });

  return {
    titles,
    hashtags,
    caption,
    aiDeclaration: buildAiDeclaration(input.locale),
    commentKit: buildCommentKit(input),
    ...(shopLink && { shopLink }),
  };
}

export function buildAiDeclaration(locale?: "zh" | "en"): { notice: string; line: string } {
  return locale === "en"
    ? {
        notice:
          'Turn on the platform\'s "AI-generated content" toggle when posting — undeclared AI content gets auto-flagged and suppressed (TikTok C2PA detection); self-declared content distributes normally.',
        line: "Contains AI-generated content",
      }
    : {
        notice: "发布时记得勾选平台的「内容由 AI 生成」声明——未主动声明会被自动打「疑似AI生成」标并限流，主动声明基本不影响分发。",
        line: "本视频含 AI 生成内容",
      };
}
