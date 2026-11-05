"""检索：按**当前这一句**勾起最相关的几条旧事。

为什么不重新扫全部日记
----------------------
重扫 + 重算 N 是 O(M^2)，生产实测约 0.7 秒；每轮对话都跑会把回复拖慢。
这里只读派生索引（~170ms 含 Python 启动），衰减/打分/生命周期全部复用 decay.compute()
——只有一份实现，不会漂移。

输出带确定度：模型容易把「我想起来了」说成「你刚才说」，所以每段都标
「不是刚发生的」+ 括号里的 stage 口径。
"""
from __future__ import annotations

from datetime import date

from .config import Config
from .decay import compute
from .models import Hit, MemoryItem, SHORT_LABEL
from .scoring import clip, cue_match

HEADER = "## 想起（这句勾起来的旧事，不是刚发生的。括号里是能说多确定）"


def recall(items: list[MemoryItem], query: str, cfg: Config,
           today: date | None = None, k: int | None = None,
           min_cue: float | None = None) -> dict:
    """按 cue 命中 + 可检索性排序，返回 top-k 与可直接注入的 Markdown 块。"""
    today = today or date.today()
    k = cfg.integer("cue.default_k", 3) if k is None else max(1, k)
    min_cue = cfg.num("cue.default_min_cue", 0.5) if min_cue is None else min_cue
    w_cue = cfg.num("retrievability.w_cue", 0.28)

    hits: list[Hit] = []
    for it in items:
        match = cue_match(query, it, cfg)
        if match < min_cue:
            continue
        m = compute(it, today, cfg)
        hits.append(Hit(item=it, cue=match, R=min(1.0, m.R + w_cue * match),
                        stage=m.stage, D=m.D))
    hits.sort(key=lambda h: (h.R, h.cue), reverse=True)
    top = hits[:k]
    return {"query": query, "total": len(hits),
            "hits": [{"id": h.item.id, "date": h.item.date, "text": h.item.text,
                      "cue": round(h.cue, 2), "R": round(h.R, 3),
                      "stage": h.stage.value, "D": round(h.D, 3)} for h in top],
            "block": render_recall(top, cfg)}


def render_recall(hits: list[Hit], cfg: Config) -> str:
    """逐行累加，超 ``recall_max_chars`` 就停；宁可少给也不硬截断半句话。"""
    limit = cfg.integer("cue.recall_max_chars", 700)
    clip_chars = cfg.integer("cue.recall_clip_chars", 110)
    lines = [HEADER]
    used = len(HEADER)
    for h in hits:
        tail = SHORT_LABEL.get(h.stage.value, h.stage.value)
        if h.stage.value == "episodic":
            tail = f"记得大概，D {h.D:.2f}"
        elif h.stage.value == "intact" and h.D < 0.85:
            tail = f"细节还在，D {h.D:.2f}"
        line = f"- {h.item.date[5:]}｜{clip(h.item.text, clip_chars)}（{tail}）"
        if used + len(line) + 1 > limit:
            break
        used += len(line) + 1
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""
