"""打分、干扰度与线索抽取。

这里的相似度是**字符二元组 / 词表命中**，不是语义向量。这是明确的取舍：
零依赖、可解释、词表可编辑，代价是同义不同词算不出干扰、也连不上边。
要换向量库，替换 ``noise_level`` 与 ``cue_match`` 两个函数即可（见 README 扩展方向）。
"""
from __future__ import annotations

import re
from typing import Iterable

from .config import Config
from .models import MemoryItem

# 没有人工标注时的兜底词表。宁可保守，也不要"猜这件事对他有多重要"。
EMO_WORDS = (
    "怕", "难受", "哭", "生气", "火大", "烦", "委屈", "心疼", "开心", "高兴",
    "喜欢", "讨厌", "舍不得", "想他", "想念", "后悔", "抱歉", "对不起", "谢",
    "感动", "慌", "焦虑", "担心", "尴尬", "丢人", "骄傲", "踏实", "安心",
)
IMPORTANT_WORDS = (
    "记住", "别再", "以后", "规矩", "决定", "定了", "约定", "答应", "偏好",
    "习惯", "不许", "必须", "讨厌", "喜欢", "重要", "考试", "截止", "生日",
)
# 「具体」只认时间/地点/编号；引号里的词不算（会把规矩误判成事件）。
CONCRETE_RE = re.compile(
    r"\d{1,2}[:：]\d{2}|\d{1,2}\s*月|\d{1,2}-\d{1,2}|\d{3,4}-\d{3,4}"
    r"|\d+\s*(?:点|号|楼|条|块|元|次|遍|笔)"
)
CUE_CODE_RES = (
    re.compile(r"\b\d{3,4}-\d{3,4}\b"),                # 教室号、编号
    re.compile(r"\b[A-Z][A-Za-z0-9.+#-]{1,14}\b"),     # 英文专名、型号
    re.compile(r"(?<![A-Za-z])v\d+(?:\.\d+)*\b"),      # 版本号
)
DEFAULT_LEXICON = [
    "面包", "牛奶", "早餐", "食堂", "图书馆", "教室", "宿舍", "课表", "账单",
    "闹钟", "表情包", "卫衣", "礼物", "咖啡", "生日", "考试", "作业", "梗图",
    "穿搭", "补丁", "权限", "接口", "向量库", "版本号",
]
# 时间戳不算线索：没人会因为看见 18:30 就想起某件事。
NO_CUE_RE = re.compile(r"^\d{1,2}[:：]\d{2}$")


def clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: max(1, limit - 1)] + "…"


def bigrams(text: str) -> set[str]:
    t = re.sub(r"[\s，。、：；！？「」『』（）()【】〔〕｜|]", "", text)
    return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else set()


def heuristic(item: MemoryItem, cfg: Config) -> dict:
    """缺标注时的保守初值。关键词表可编辑，但**判不出"这件事有多重要"**。

    优先级：手写标注 > 索引里上次的值 > 本函数（在 store.build() 里实现）。
    """
    text, sec = item.text, item.section
    n_emo = sum(1 for w in EMO_WORDS if w in text)
    n_imp = sum(1 for w in IMPORTANT_WORDS if w in text)
    n_con = len(CONCRETE_RE.findall(text))
    long_enough = len(text) >= cfg.integer("heuristic.long_text_chars", 55)

    g = lambda k, d=0.0: cfg.num(f"heuristic.{k}", d)  # noqa: E731
    s = g("s_base", 0.35)
    if "记住" in sec:
        s = g("s_remembered", 0.70)
    elif n_imp >= 2:
        s = g("s_imp2", 0.62)
    elif n_imp == 1:
        s = g("s_imp1", 0.50)
    if n_con >= 3:
        s += g("s_concrete_bonus", 0.05)
    s = min(g("s_cap", 0.92), s)

    d0 = g("d0_base", 0.55)
    if n_con >= 3:
        d0 = g("d0_con3", 0.85)
    elif n_con == 2:
        d0 = g("d0_con2", 0.75)
    elif n_con == 1:
        d0 = g("d0_con1", 0.65)
    if long_enough:
        d0 = min(g("d0_cap", 0.95), d0 + g("d0_long_bonus", 0.08))
    if item.kind == "rule":
        d0 = min(d0, g("d0_rule_cap", 0.70))

    e = (min(g("e_cap", 0.85), g("e_base", 0.20) + g("e_per_word", 0.16) * n_emo)
         if n_emo else g("e_none", 0.15))
    return {"S": round(s, 2), "D0": round(d0, 2), "E": round(e, 2)}


def noise_level(item: MemoryItem, all_items: Iterable[MemoryItem], cfg: Config) -> float:
    """N：与同库最相似那条的二元组 Jaccard，乘系数后封顶。

    复杂度 O(M^2 * L)。这是本实现**最主要的性能瓶颈**：M 是条目数，
    L 是正文长度。生产环境 50 条时无感，上千条要按 cue 倒排或换向量索引
    （见 docs/PERFORMANCE.md）。
    """
    a = bigrams(item.text)
    if not a:
        return 0.0
    best = 0.0
    for other in all_items:
        if other.id == item.id:
            continue
        b = bigrams(other.text)
        if not b:
            continue
        inter = len(a & b)
        if inter:
            best = max(best, inter / len(a | b))
    return round(min(cfg.num("noise.cap", 0.9),
                     best * cfg.num("noise.bigram_scale", 1.6)), 2)


def auto_cues(text: str, cfg: Config, lexicon: list[str] | None = None) -> list[str]:
    """没手写〔线索：…〕时，从正文抽真锚点：词表命中 / 编号 / 英文专名 / 版本号。"""
    words = lexicon if lexicon is not None else DEFAULT_LEXICON
    out: list[str] = []
    for w in words:
        if w in text and w not in out:
            out.append(w)
    for rx in CUE_CODE_RES:
        for m in rx.findall(text):
            m = m.strip()
            if 2 <= len(m) <= 16 and not NO_CUE_RE.match(m) and m not in out:
                out.append(m)
    return out[:cfg.integer("cue.auto_cue_max", 3)]


def cue_match(query: str, item: MemoryItem, cfg: Config) -> float:
    """线索命中给 0.9；否则退化成二元组重叠，低于阈值直接 0（不硬凑）。"""
    if not query:
        return 0.0
    match = 0.0
    for c in item.cues:
        if c and c in query:
            match = max(match, cfg.num("cue.manual_match", 0.9))
    if match == 0.0:
        grams, tg = bigrams(query), bigrams(item.text)
        if grams and tg:
            ov = len(grams & tg) / len(grams)
            if ov >= cfg.num("cue.bigram_overlap_min", 0.25):
                match = min(cfg.num("cue.bigram_match_cap", 0.7), ov)
    return round(match, 3)
