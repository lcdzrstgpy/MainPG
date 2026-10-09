"""本地 FAQ 三层匹配算法。

设计目标：**零第三方依赖**（只用标准库），因为本项目是 PyInstaller 打包分发的桌面客户端，
每多一个依赖都会进入每次构建，而中文分词库（jieba）带来的词典文件（~10MB）与此处
的收益不成正比。

为什么不用真分词器：
    FAQ 场景的要害是「用户问法 vs 人工写好的 keywords」。keywords 是人写的，
    已经隐含了分词结果，因此**关键词子串匹配**比通用分词更贴合场景。
    中文虽无空格，但**一个汉字就是一个字符**，n-gram 天然可用。

三层匹配（从严到松）：
    ① match_keywords   —— 关键词命中打分
    ② apply_synonyms   —— 归一化后重试（"传图" → "上传"）
    ③ fuzzy_candidates —— difflib + 字符 bigram 相似度，返回候选（不给答案）

阈值（由调用方传入或使用默认）：
    score >= HIGH_CONFIDENCE      → 直接给答案
    CANDIDATE_FLOOR <= score < HIGH_CONFIDENCE → 只给候选问题，用户点选后才取答案
    score <  CANDIDATE_FLOOR      → 不返回候选，走兜底（引导反馈）
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# 阈值常量（调优入口）
# ---------------------------------------------------------------------------

#: 关键词层达到该分数即视为高置信命中，直接返回答案
KEYWORD_HIT_THRESHOLD = 0.55
#: 模糊层达到该分数即视为可推荐的候选
CANDIDATE_FLOOR = 0.35
#: 模糊层直接命中的分数（高于此值可直接给答案，无需用户确认）
FUZZY_HIT_THRESHOLD = 0.72
#: 默认返回的候选数量上限
DEFAULT_TOP_K = 3

#: 权重：关键词命中是主要信号，标题相似度是补充信号
_KEYWORD_WEIGHT = 0.75
_QUESTION_WEIGHT = 0.25
#: 关键词覆盖率折算系数。覆盖率用「命中数 / 关键词数」的平方根，削弱长关键词列表
#: 的分母惩罚：一条 FAQ 写 8 个关键词而用户只命中 2 个，语义上仍属强相关。
_COVERAGE_DAMPING = 0.5
#: 命中关键词**在用户问句中的字符占比**的权重。
#: 短问句里命中一个长关键词（如 5 字句中命中"上传"）已经是很强的信号，
#: 单看"命中率"会严重低估这种情形。
_MATCH_DENSITY_WEIGHT = 0.55

#: 出现频率极高、区分度极低的词，参与匹配会制造噪声
_STOPWORDS = frozenset(
    {
        "怎么办", "为什么", "怎么", "如何", "什么", "可以吗", "能不能",
        "是否", "有没有", "请问", "谢谢", "一下", "一个", "这个", "那个",
        "问题", "的", "了", "吗", "呢", "啊", "呀", "我", "你",
    }
)

#: 查询侧的填充语（语气词、疑问词、叙述性套话）。
#:
#: ⚠️ **只用于估算「问句的信息长度」，不参与关键词子串匹配**：匹配仍在完整问句上做，
#: 删词不会破坏 keywords 的子串命中（例如 keywords 里的「怎么上传图片」照旧能命中）。
#:
#: 为什么需要它：density（命中字符数 ÷ 问句长度）是关键词层的主信号，而用户把话说完整时
#: 多出来的多半是这类填充语 —— 「我在…页面点了…，但是一直没有反应，是不是卡住了」里
#: 真正有信息量的只有「没有反应」「卡住」几个字。用完整长度做分母会让问得越认真的用户
#: 越难命中，与设计目标相反。
#:
#: 用**固定顺序的 tuple**（而不是 set），保证结果在任意进程中都一致。
_QUERY_FILLERS: tuple[str, ...] = (
    "可不可以", "怎么回事", "是不是", "有没有", "能不能", "为什么", "怎么样",
    "怎么办", "什么", "请问", "你们", "我们", "这个", "那个", "刚才", "已经",
    "但是", "然后", "不过", "而且", "因为", "所以", "还是", "就是", "有点",
    "老是这样", "哪里", "一直", "一下", "一个", "我", "你", "的", "了", "是",
    "吗", "呢", "啊", "吧", "呀", "嘛",
)

#: 信息长度下限。问句被填充语剪短后仍要留下可比较的分母，且不能小于命中字符数。
_MIN_INFORMATIVE_LENGTH = 2


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FaqHit:
    """一条匹配结果。

    ``answer`` 可能为空：**候选阶段刻意不带答案**，见 :func:`search` 的文档。
    """

    faq_id: str
    question: str
    score: float
    answer: str = ""
    category: str = ""


@dataclass(frozen=True)
class SearchResult:
    """一次检索的完整结果。"""

    query: str
    normalized_query: str
    #: 高置信命中（有答案）
    hit: FaqHit | None = None
    #: 低置信候选（**只有问题文本，没有答案**）
    candidates: list[FaqHit] = field(default_factory=list)
    #: 是否需要引导用户去反馈
    need_feedback: bool = False
    #: 命中的是哪一层（keyword / synonym / fuzzy / none），便于观测与调试
    matched_layer: str = "none"


# ---------------------------------------------------------------------------
# 文本归一化
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """归一化文本：全角转半角、去标点与空白、英文小写、统一 Unicode。

    中文之间**不加空格**，因为后续按字符比对（n-gram 与 difflib 都按字符算）。
    """
    if not text:
        return ""
    # NFKC 会把全角字母/数字/标点转成半角，同时统一异体字
    text = unicodedata.normalize("NFKC", str(text))
    text = text.lower()
    # 去掉所有非字母数字非中日韩字符（标点、空格、emoji 等）
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _strip_stopwords(text: str) -> str:
    """去掉区分度极低的停用词。

    ⚠️ **只用于相似度计算，不用于关键词子串匹配**。停用词表里既有"怎么"这类
    疑问词、也有"的/了"这类虚词，粗暴删除会把句子切得七零八落（例如
    "三个策略有什么区别" → "三个策略有区别"，反倒留下孤立的"有"制造噪声）。
    关键词匹配依赖的是子串命中，用户句子里多几个疑问词完全不影响，无需清理。
    """
    for word in _STOPWORDS:
        if word in text and len(text) > len(word):
            text = text.replace(word, "")
    return text


def _informative_length(query_norm: str, matched_chars: int = 0) -> int:
    """估算问句的**信息长度**：扣掉 :data:`_QUERY_FILLERS` 里的填充语后剩下的字符数。

    只用于给 density 当分母，不改变任何匹配行为。这样「我在产品处理页面点了开始处理，
    但是一直没有反应，是不是卡住了」和「任务卡住」会得到接近的密度，而不是被 27 个
    字符的分母压到几乎为零。

    下限取 ``max(剪枝后长度, 命中字符数)``：命中词也可能包含填充语（如 keywords 里的
    「怎么上传图片」），此时密度封顶为 1.0，不会出现大于 1 的值。
    """
    stripped = query_norm
    for word in _QUERY_FILLERS:
        if word in stripped:
            stripped = stripped.replace(word, "")
    return max(len(stripped), matched_chars, _MIN_INFORMATIVE_LENGTH)


# ---------------------------------------------------------------------------
# 相似度工具
# ---------------------------------------------------------------------------


def _bigrams(text: str) -> set[str]:
    """字符 bigram 集合。中文单字为一个字符，bigram 即相邻两字。"""
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def similarity(query: str, target: str) -> float:
    """综合相似度：difflib 序列比 与 字符 bigram Jaccard 的加权融合。

    两者各有短板，融合更稳：

    * ``SequenceMatcher`` 对**字序敏感**，长句里挪动语序会显著掉分；
    * bigram Jaccard 对**语序不敏感**，但短文本噪声大。

    权重取 0.5/0.5：FAQ 问句普遍不长，两者可信度接近。
    """
    q = normalize(query)
    t = normalize(target)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0

    # 疑问词/虚词对"是否同一问题"没有区分度，比对前剔除
    q_stripped = _strip_stopwords(q) or q
    t_stripped = _strip_stopwords(t) or t

    seq_score = SequenceMatcher(None, q_stripped, t_stripped).ratio()
    gram_score = _jaccard(_bigrams(q_stripped), _bigrams(t_stripped))
    return 0.5 * seq_score + 0.5 * gram_score


# ---------------------------------------------------------------------------
# 第 1 层：关键词命中
# ---------------------------------------------------------------------------


def _keyword_hits(query_norm: str, faq: Mapping[str, Any]) -> tuple[int, int, int]:
    """统计一条 FAQ 的**关键词命中强度**：``(命中个数, 命中总字符数, 关键词总数)``。

    关键词层（打分）与模糊层（候选排序）共用同一份统计，避免两处口径漂移。
    命中判定就是「keywords 里的词是问句的子串」，与是否命中填充语无关。
    """
    keywords = [str(k) for k in (faq.get("keywords") or []) if str(k).strip()]
    matched = 0
    matched_chars = 0
    for kw in keywords:
        kw_norm = normalize(kw)
        if not kw_norm:
            continue
        if kw_norm in query_norm:
            matched += 1
            matched_chars += len(kw_norm)
    return matched, matched_chars, len(keywords)


def _keyword_score(query_norm: str, faq: Mapping[str, Any]) -> float:
    """计算单条 FAQ 的关键词命中得分。

    两个信号融合：

    * **匹配密度**（主信号）：命中的关键词字符数占**问句信息长度**的比例。
      短问句里命中一个长关键词就很说明问题 —— 用户只说了 4 个字，其中 2 个字
      正是这条 FAQ 的关键词，相关性已经很强。
      分母用 :func:`_informative_length` 而不是问句原始长度：用户把话说完整时，
      多出来的多是「我在…但是…是不是…」这类填充语，不代表更多信息量。
    * **覆盖率**（副信号）：命中数 / 关键词总数，用平方根削弱分母惩罚。
      一条 FAQ 的关键词写得越全（这正是我们鼓励的），分母越大，用户只命中
      其中两三个时反而拿不到高分 —— 会出现"写得越认真、越难命中"的荒谬结果。

    两者都单调于"命中更多关键词"，融合后可兼顾长短问句。

    标题相似度作为低权重补充，用于在关键词打平时区分。
    """
    question = str(faq.get("question", ""))
    question_sim = similarity(query_norm, question)

    matched, matched_chars, keyword_total = _keyword_hits(query_norm, faq)
    if keyword_total == 0:
        return question_sim

    if matched == 0:
        # 一个关键词都没命中：只有标题高度相似时才给分，且权重较低
        return question_sim * _QUESTION_WEIGHT

    coverage = (matched / keyword_total) ** _COVERAGE_DAMPING
    # 命中字符占问句信息量的比例，封顶 1.0（命中词可能比剪枝后的问句还长）
    density = min(1.0, matched_chars / _informative_length(query_norm, matched_chars))
    # 命中数本身给少量加成，避免"命中 1 个"与"命中 3 个"拿到一样的分
    breadth = min(matched, 4) * 0.04

    keyword_part = (
        _MATCH_DENSITY_WEIGHT * density + (1 - _MATCH_DENSITY_WEIGHT) * coverage
    )
    return min(1.0, _KEYWORD_WEIGHT * keyword_part + _QUESTION_WEIGHT * question_sim + breadth)


def match_keywords(
    query: str,
    faqs: Sequence[Mapping[str, Any]],
) -> list[FaqHit]:
    """第 1 层：按关键词命中打分，返回按分数降序的全部结果。"""
    # 不做停用词清理：关键词匹配靠子串命中，疑问词不影响；
    # 而删词反而可能破坏 keywords 的完整子串（见 _strip_stopwords 说明）。
    query_norm = normalize(query)
    if not query_norm:
        return []

    hits: list[FaqHit] = []
    for faq in faqs:
        score = _keyword_score(query_norm, faq)
        if score <= 0:
            continue
        hits.append(
            FaqHit(
                faq_id=str(faq.get("id", "")),
                question=str(faq.get("question", "")),
                score=score,
                answer=str(faq.get("answer", "")),
                category=str(faq.get("category", "")),
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits


# ---------------------------------------------------------------------------
# 第 2 层：同义词归一
# ---------------------------------------------------------------------------


def apply_synonyms(text: str, synonyms: Mapping[str, Iterable[str]]) -> str:
    """把文本里的同义词替换成标准词。

    ``synonyms`` 形如 ``{"上传": ["传图", "传不上去"]}``，意为出现右侧任一说法
    就替换为左侧的标准词。

    实现要点（两个易踩的坑）：

    1. **长变体优先**：否则 "上传图片" 会被 "上传" 先吃掉，剩下孤立的 "图片"。
    2. **替换结果不参与后续匹配**：用占位符隔离已替换的片段。否则当标准词本身
       含变体字时会出现连锁替换 —— 例如 "传图" → "上传" 后，其中的 "传" 若也是
       某个变体，会被二次替换成 "上传传" 这类垃圾结果。
    """
    if not text or not synonyms:
        return text

    # (变体, 标准词) 按变体长度降序，保证长变体先匹配
    pairs: list[tuple[str, str]] = []
    for canonical, variants in synonyms.items():
        canonical_norm = normalize(str(canonical))
        if not canonical_norm:
            continue
        for variant in variants or []:
            variant_norm = normalize(str(variant))
            if variant_norm and variant_norm != canonical_norm:
                pairs.append((variant_norm, canonical_norm))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)

    result = text
    for index, (variant_norm, canonical_norm) in enumerate(pairs):
        if variant_norm not in result:
            continue
        # 用不可见占位符包裹替换结果，阻止后续轮次再次匹配到它
        placeholder = f"\ue000{index}\ue001"
        result = result.replace(variant_norm, placeholder)
        result = result.replace(placeholder, f"\ue000{canonical_norm}\ue001")

    # 还原占位符（去掉标记，保留标准词）
    result = re.sub(r"\ue000(.*?)\ue001", r"\1", result)
    return result


# ---------------------------------------------------------------------------
# 第 3 层：模糊相似度
# ---------------------------------------------------------------------------


def fuzzy_candidates(
    query: str,
    faqs: Sequence[Mapping[str, Any]],
    *,
    top_k: int = DEFAULT_TOP_K,
    floor: float = CANDIDATE_FLOOR,
) -> list[FaqHit]:
    """第 3 层：模糊相似度，返回候选。

    打分取两条路径的**较高值**：

    * **字符相似度**：用户问句 vs FAQ 问题 / 各关键词。适合"问法不同但用字接近"。
    * **关键词软命中**：用户问句里出现了该 FAQ 的关键词（子串命中）就给保底分。
      适合"只说了一两个词"的短问句 —— 例如用户只说"传图怎么传"，字符相似度
      不高，但它确实提到了"上传"，理应进候选让用户确认。

    ⚠️ **返回的 FaqHit 一律不含答案**（``answer=""``）。候选必须由用户点选确认后
    才去取答案 —— 模糊匹配可能推荐错，直接把答案甩给用户会造成"答非所问"。

    ⚠️ **排序按「关键词命中强度」而不是相似度**（``命中字符数 → 命中个数 → 相似度``）。
    用户原样打出的长短语（如"导出店小秘表格"）是最强的意图证据，命中字符数正是它的度量；
    若按相似度排，长问句里各条保底分都挤在 0.4~0.5，顺序会退化成 ``faqs.json`` 的行序。
    ``score`` 字段仍是相似度口径，供 :func:`search` 判断是否达到直接命中阈值。
    """
    query_norm = normalize(query)
    if not query_norm:
        return []

    scored: list[tuple[tuple[int, int, float], FaqHit]] = []
    for faq in faqs:
        # 路径一：字符相似度（问题 与 关键词中取较高者）
        best = similarity(query_norm, str(faq.get("question", "")))
        for kw in faq.get("keywords") or []:
            kw_norm = normalize(str(kw))
            if not kw_norm:
                continue
            score = similarity(query_norm, kw_norm)
            if score > best:
                best = score

        matched, matched_chars, _ = _keyword_hits(query_norm, faq)
        # 路径二：子串命中即给保底分，命中的关键词越多越长给得越高（封顶 0.5）
        soft_hit = min(0.5, 0.3 + 0.1 * matched_chars) if matched else 0.0

        final = max(best, soft_hit)
        if final >= floor:
            scored.append(
                (
                    # 排序以「用户说到的那个关键词有多长」为主：用户原样打出的长短语
                    # （如"导出店小秘表格"）是最强的意图证据，命中字符数能直接反映它。
                    # 命中个数、相似度作为次级区分；_keyword_score 只用于判定是否直接命中。
                    (matched_chars, matched, final),
                    FaqHit(
                        faq_id=str(faq.get("id", "")),
                        question=str(faq.get("question", "")),
                        score=final,
                        answer="",  # ← 刻意留空：候选阶段不暴露答案
                        category=str(faq.get("category", "")),
                    ),
                )
            )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [hit for _, hit in scored[:top_k]]


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


def search(
    query: str,
    faqs: Sequence[Mapping[str, Any]],
    synonyms: Mapping[str, Iterable[str]] | None = None,
) -> SearchResult:
    """三层匹配总入口。

    行为：

    1. 第 1 层关键词命中 >= ``KEYWORD_HIT_THRESHOLD`` → 直接给答案；
    2. 否则用同义词归一后重试第 1 层（命中则标记 ``matched_layer="synonym"``）；
    3. 仍不中 → 第 3 层模糊匹配：
       * 有 ``>= FUZZY_HIT_THRESHOLD`` 的 → 直接给答案；
       * 只有 ``>= CANDIDATE_FLOOR`` 的 → **只给候选（无答案）**；
    4. 都没有 → ``need_feedback=True``。
    """
    raw_query = str(query or "").strip()
    normalized = normalize(raw_query)
    if not normalized:
        return SearchResult(
            query=raw_query, normalized_query="", need_feedback=True
        )

    # --- 第 1 层 ---
    hits = match_keywords(raw_query, faqs)
    if hits and hits[0].score >= KEYWORD_HIT_THRESHOLD:
        return SearchResult(
            query=raw_query,
            normalized_query=normalized,
            hit=hits[0],
            matched_layer="keyword",
        )

    # --- 第 2 层：同义词归一后重试 ---
    # rewritten 也要留给第 3 层用：用户说的是"传图"，归一成"上传"后才
    # 可能命中关键词，否则模糊层会漏掉这一条。
    rewritten = normalized
    if synonyms:
        rewritten = apply_synonyms(normalized, synonyms)
        if rewritten != normalized:
            rewritten_hits = match_keywords(rewritten, faqs)
            if rewritten_hits and rewritten_hits[0].score >= KEYWORD_HIT_THRESHOLD:
                return SearchResult(
                    query=raw_query,
                    normalized_query=normalized,
                    hit=rewritten_hits[0],
                    matched_layer="synonym",
                )

    # --- 第 3 层：模糊候选 ---
    # 用归一化后的文本参与模糊匹配：问句被同义词改写后，关键词软命中才能生效
    candidates = fuzzy_candidates(rewritten, faqs) if rewritten != normalized else []
    if not candidates:
        candidates = fuzzy_candidates(raw_query, faqs)
    # 候选按「关键词命中强度」排序，但**能否直接给答案只看相似度**：
    # 相似度足够高才敢替用户做决定，否则只给候选让用户点一下。
    top_by_similarity = max(candidates, key=lambda c: c.score) if candidates else None
    if top_by_similarity is not None and top_by_similarity.score >= FUZZY_HIT_THRESHOLD:
        for faq in faqs:
            if str(faq.get("id", "")) == top_by_similarity.faq_id:
                return SearchResult(
                    query=raw_query,
                    normalized_query=normalized,
                    hit=FaqHit(
                        faq_id=top_by_similarity.faq_id,
                        question=str(faq.get("question", "")),
                        score=top_by_similarity.score,
                        answer=str(faq.get("answer", "")),
                        category=str(faq.get("category", "")),
                    ),
                    matched_layer="fuzzy",
                )

    if candidates:
        return SearchResult(
            query=raw_query,
            normalized_query=normalized,
            candidates=list(candidates),
            matched_layer="fuzzy",
        )

    # --- 兜底 ---
    return SearchResult(
        query=raw_query,
        normalized_query=normalized,
        need_feedback=True,
        matched_layer="none",
    )


def find_by_id(
    faq_id: str, faqs: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """按 id 取 FAQ（供用户点选候选后取答案）。"""
    target = str(faq_id or "")
    for faq in faqs:
        if str(faq.get("id", "")) == target:
            return faq
    return None
