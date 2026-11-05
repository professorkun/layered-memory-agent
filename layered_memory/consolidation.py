"""巩固、分化、沉淀、联结：事件层怎么长出深层。

**权限边界**：本模块只产出**候选**写到 index/，绝不直接改 self/ 下的深层文件。
生产里由日记任务/人审决定要不要落；这里给 pipeline.approve_candidates() 显式写入。
引擎自己写 self/ 就等于自己编记忆。

三条规则（参数全部来自 config.example.json）
-------------------------------------------
分化  residue 且 E >= feel_e_min、待够 feel_residue_days 天 -> 感觉候选
沉淀  同主题 >= sediment_min_items 条、ΣS >= sediment_min_s、跨度 >= span_days
      -> 判断候选；出现强反例置信 -drop，低于 floor 渲染「（我可能想错了）」
联结  cue 交集 +w_cue/个(封顶)、同实体 +w_entity、同日 +w_near、
      情绪同极性 +w_mood、手写〔连：id〕 +w_manual；边权按 (1+t/edge_tau)^-edge_p 衰减
"""
from __future__ import annotations

import json
from datetime import date

from .config import Config
from .decay import compute, parse_date
from .models import Edge, Feeling, MemoryItem, Sediment
from .store import Store


def touch(items: list[MemoryItem], item_id: str, cfg: Config,
          today: date | None = None) -> dict:
    """记一次回忆：C 上升、D0 微升（重固化，上限 d0_cap）、原始日记不动。

    注意：每次回忆都在**重构**，所以反复提同一件事会让它更像自己编的版本。
    规则文档里明确要求「别反复拿同一件事当谈资」。
    """
    today = today or date.today()
    for it in items:
        if it.id != item_id:
            continue
        before = compute(it, today, cfg).as_dict()
        it.recalls += 1
        it.C = min(cfg.num("consolidation.c_cap", 0.90),
                   cfg.num("consolidation.c_base", 0.10)
                   + cfg.num("consolidation.c_per_recall", 0.18) * it.recalls)
        it.D0 = min(cfg.num("consolidation.d0_cap", 0.98),
                    it.D0 * cfg.num("consolidation.touch_d0_factor", 1.04))
        it.last_recall = today.isoformat()
        after = compute(it, today, cfg).as_dict()
        return {"ok": True, "id": item_id, "before": before, "after": after}
    return {"ok": False, "id": item_id, "error": "找不到这条记忆"}


def differentiation(items: list[MemoryItem], cfg: Config,
                    today: date | None = None) -> list[dict]:
    """residue -> 感觉候选。句子由上层写，引擎只挑候选。"""
    today = today or date.today()
    e_min = cfg.num("growth.feel_e_min", 0.30)
    days_min = cfg.integer("growth.feel_residue_days", 7)
    out = []
    for it in items:
        m = compute(it, today, cfg)
        if (m.stage.value == "residue" and m.E >= e_min
                and (today - parse_date(it.date, today)).days >= days_min):
            out.append({"type": "feel", "source_id": it.id,
                        "strength": round(m.E, 3), "cues": it.cues[:4],
                        "hint": f"由「{it.text[:30]}」沉下来的感觉，需上层写一句话"})
    return out


def sedimentation(items: list[MemoryItem], cfg: Config,
                  today: date | None = None) -> list[dict]:
    """同主题多条事件 -> 判断候选；置信度必须**能降**。"""
    today = today or date.today()
    clusters: dict[str, list[MemoryItem]] = {}
    for it in items:
        key = it.cues[0] if it.cues else "未分类"
        clusters.setdefault(key, []).append(it)
    out = []
    for topic, group in clusters.items():
        if len(group) < cfg.integer("growth.sediment_min_items", 5):
            continue
        dates = [parse_date(x.date, today) for x in group]
        span = (max(dates) - min(dates)).days
        if span < cfg.integer("growth.sediment_span_days", 14):
            continue
        total_s = sum(x.S for x in group)
        if total_s < cfg.num("growth.sediment_min_s", 3.0):
            continue
        # 反例：同为高 S 但情绪正负相反 -> 置信下降
        pos = [x for x in group if x.val >= 0 and x.S >= 0.6]
        neg = [x for x in group if x.val < 0 and x.S >= 0.6]
        conf = 0.80
        if pos and neg:
            conf -= cfg.num("growth.sediment_drop", 0.15)
        out.append({"type": "sediment", "topic": topic,
                    "items": len(group), "span_days": span,
                    "sum_S": round(total_s, 2), "confidence": round(conf, 2),
                    "label": ("" if conf >= cfg.num("growth.sediment_floor", 0.40)
                              else "（我可能想错了）"),
                    "hint": "多条同类事件攒出的判断，需上层写一句话"})
    return out


def build_edges(items: list[MemoryItem], cfg: Config,
                today: date | None = None) -> list[Edge]:
    """建边：先按 cue 倒排，**只算有共享线索的对**，避免全库 O(N^2)。

    生产实测 50 条记忆建出 129 条边。边表是派生的，删了能重建。
    """
    today = today or date.today()
    g = lambda k, d=0.0: cfg.num(f"growth.{k}", d)  # noqa: E731
    by_cue: dict[str, list[MemoryItem]] = {}
    for it in items:
        for c in it.cues:
            by_cue.setdefault(c, []).append(it)

    scores: dict[tuple[str, str], float] = {}
    for cue, group in by_cue.items():
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                key = tuple(sorted((a.id, b.id)))
                scores[key] = scores.get(key, 0.0) + g("w_cue", 0.30)
    for key in list(scores):
        scores[key] = min(g("w_cue_max", 0.60), scores[key])

    def bump(a: MemoryItem, b: MemoryItem, delta: float):
        key = tuple(sorted((a.id, b.id)))
        if key[0] == key[1]:
            return
        scores[key] = scores.get(key, 0.0) + delta

    # 同一批内补：同实体 / 同日 / 情绪同极性（只对已有候选对加，控制复杂度）
    for key in list(scores):
        a = next((x for x in items if x.id == key[0]), None)
        b = next((x for x in items if x.id == key[1]), None)
        if not a or not b:
            continue
        if set(a.who) & set(b.who):
            bump(a, b, g("w_entity", 0.40))
        if abs((parse_date(a.date, today) - parse_date(b.date, today)).days) <= 1:
            bump(a, b, g("w_near", 0.10))
        if a.val * b.val > 0 and abs(a.val) >= 0.3 and abs(b.val) >= 0.3:
            bump(a, b, g("w_mood", 0.20))
    for it in items:
        for target in getattr(it, "_manual_links", []):
            bump(it, MemoryItem(id=target, date=today.isoformat(), text=""),
                 g("w_manual", 0.80))
    return [Edge(a=k[0], b=k[1], weight=round(v, 3), born=today.isoformat())
            for k, v in scores.items() if v > 0]


def edge_decay(edge: Edge, cfg: Config, today: date | None = None) -> float:
    days = max(0, (today or date.today()) - parse_date(edge.born, today or date.today())).days
    return edge.decayed(days, cfg)


def deposit(items: list[MemoryItem], store: Store, cfg: Config,
            today: date | None = None) -> dict:
    """只写候选，不碰 self/。返回本轮候选清单供人审。"""
    today = today or date.today()
    candidates = {"updated": today.isoformat(),
                  "feel": differentiation(items, cfg, today),
                  "sediment": sedimentation(items, cfg, today),
                  "edges": [e.__dict__ for e in build_edges(items, cfg, today)]}
    store.write_json(store.path("index", "deposit-candidates.json"), candidates)
    store.write_json(store.path("index", "link-index.json"),
                     {"updated": today.isoformat(), "edges": candidates["edges"]})
    return candidates


def state_snapshot(store: Store, cfg: Config, now_hours: float) -> dict:
    """连续性不许假装：超 fresh_hours 写「接着上次（N 天前）」，超 drop_days 不置顶。"""
    state = store.read_self().get("state", {})
    fresh = cfg.num("growth.state_fresh_hours", 36)
    drop = cfg.num("growth.state_drop_days", 7) * 24
    if now_hours <= fresh:
        return {"show": bool(state), "title": "接着昨天", "state": state}
    if now_hours <= drop:
        return {"show": bool(state), "title": f"接着上次（{int(now_hours // 24)} 天前）",
                "state": state}
    return {"show": False, "title": "", "state": state}
