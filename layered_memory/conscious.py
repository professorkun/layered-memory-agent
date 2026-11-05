"""意识层：状态连续体 + 激活竞争 + 深层模式。

为什么单独一层
--------------
只靠「人设 + 检索 + 规则」，每轮都把同一份记忆塞进 prompt，然后让模型现编一个
「我」：没有身体（累、烦、过载都不存在），也不会被经历改变——同一句「你怎么又没回」，
第一天和第一年的反应一模一样。

这一层给三件东西：
1. 状态连续体：身体、情绪、关系都带着上一次的值进来，不是每轮归零。
2. 激活竞争：不是所有记忆都该被想起来。过了阈值才进意识，其余在后台攒分。
3. **深层模式（种子熏习）**：反复发生的事不存成一条记忆，而是改一个系数；
   系数反过来改下一次的评价。这就是「记忆模糊了，但结构还在」。

边界（不能破）
-------------
* ``Temperament`` 只读：出生前定，改它等于换一个机器人，不是调参。
* ``DeepPattern`` 只写：由事件累积，不许手改、不许模型自评。
* 不让模型自己算分：所有数值在这里算完，模型只看到结论。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .config import Config
from .models import Scored
from .scoring import bigrams, clip


@dataclass
class Temperament:
    """先天气质：7 个系数，只读。不等于人格结论，只决定「先感觉到什么」。"""
    threat_sensitivity: float = 0.55
    novelty_pull: float = 0.70
    caution: float = 0.40
    ambiguity_tolerance: float = 0.45
    warmth: float = 0.60
    persistence: float = 0.50
    arousal_gain: float = 0.50

    @classmethod
    def from_config(cls, cfg: Config) -> "Temperament":
        t = cfg.section("conscious").get("temperament", {})
        return cls(**{k: float(v) for k, v in t.items() if k in cls.__dataclass_fields__})


@dataclass
class DeepPattern:
    """深层模式：只写。traits 是慢变的系数，seeds 是事件证据计数。"""
    traits: dict[str, float] = field(default_factory=dict)
    seeds: dict[str, int] = field(default_factory=dict)

    @classmethod
    def blank(cls, cfg: Config) -> "DeepPattern":
        return cls(traits={k: 0.0 for k in cfg.get("conscious.deep_traits", [])},
                   seeds={})

    def sow(self, signal: str, value: float) -> float:
        """种一颗种子：同类事件累积改系数，用 tanh 压到 (-1, 1)，不许无限涨。

        这是「深层」与「浅层」最本质的区别：浅层存条目，深层改评价函数。
        """
        self.seeds[signal] = self.seeds.get(signal, 0) + 1
        if signal not in self.traits:
            self.traits[signal] = 0.0
        # 每次事件只推一点点；次数越多越难推动（边际递减）
        step = value * 0.08 / math.sqrt(self.seeds[signal])
        self.traits[signal] = math.tanh(math.atanh(max(-0.99, min(0.99,
            self.traits[signal]))) + step)
        return round(self.traits[signal], 4)


@dataclass
class BodyState:
    """身体状态：每条消息推进一格，随时间回落。"""
    energy: float = 0.7
    fatigue: float = 0.2
    load: float = 0.2
    arousal: float = 0.3
    mood: float = 0.0            # -1 ~ 1
    misses: int = 0

    def tick(self, load_delta: float = 0.05, dt_hours: float = 0.0) -> None:
        self.load = max(0.0, min(1.0, self.load + load_delta))
        self.fatigue = max(0.0, min(1.0, self.fatigue + 0.01 - 0.02 * dt_hours))
        self.energy = max(0.0, min(1.0, self.energy - 0.005 + 0.03 * dt_hours))
        self.arousal = max(0.0, min(1.0, self.arousal * (1 - 0.05 * dt_hours)))


def compete(scored: list[Scored], query: str, body: BodyState,
            cfg: Config, present: set[str] | None = None) -> list[dict]:
    """激活竞争：这一轮哪些记忆能进意识（星光），其余留在后台。

        M      线索命中 1 - 0.45^命中数；否则退化成二元组 Jaccard
        P      clamp(0.35 + 0.45*在场 + 0.20*rel)
        Emo    clamp((0.25+0.75*e_now) * (0.45+0.55*cong))
        T      clamp(0.55*S + 0.45*D, 0.05, 1)
        A      (M^0.45 * P^0.16 * Emo^0.16 * T^0.23) * attention

    超过 theta 才放行，最多 cap 条；同组弱者侧抑制乘 suppress；
    当事人不在场再乘 absent_factor。被压住的**不删，只是这轮不上来**。
    """
    c = cfg.section("conscious")
    theta = float(c.get("theta", 0.34))
    cap = int(c.get("cap", 4))
    if abs(body.mood) >= float(c.get("strong_mood_threshold", 0.6)) or \
       body.arousal >= float(c.get("strong_arousal", 0.70)):
        theta, cap = float(c.get("strong_theta", 0.40)), int(c.get("strong_cap", 2))

    qg = bigrams(query)
    hits: list[dict] = []
    for m in scored:
        it = m.item
        cue_hits = sum(1 for cue in it.cues if cue and cue in query)
        M = (1 - 0.45 ** cue_hits) if cue_hits else max(
            0.02, 0.10 + 0.20 * (len(qg & bigrams(it.text)) /
                                  max(1, len(qg | bigrams(it.text)))))
        in_room = 1.0 if (present is None or not it.who or
                          any(w in present for w in it.who)) else 0.0
        P = max(0.0, min(1.0, 0.35 + 0.45 * in_room + 0.20 * it.rel))
        cong = 1 - abs(body.mood - it.val) / 2
        Emo = max(0.0, min(1.0, (0.25 + 0.75 * m.E) * (0.45 + 0.55 * cong)))
        T = max(0.05, min(1.0, 0.55 * m.S + 0.45 * m.D))
        attention = max(float(c.get("attention_min", 0.55)),
                        min(float(c.get("attention_max", 1.25)),
                            float(c.get("attention_base", 1.10))
                            - float(c.get("attention_load", 0.55)) * body.load
                            + float(c.get("attention_arousal", 0.10)) * body.arousal))
        A = (M ** float(c.get("w_m", 0.45)) * P ** float(c.get("w_p", 0.16))
             * Emo ** float(c.get("w_emo", 0.16)) * T ** float(c.get("w_t", 0.23))) * attention
        if it.who and present is not None and not any(w in present for w in it.who):
            A *= float(c.get("absent_factor", 0.55))
        if M >= float(c.get("misses_min_m", 0.35)):
            A += min(float(c.get("misses_cap", 0.06)),
                     float(c.get("misses_bonus", 0.02)) * body.misses)
        if A >= theta:
            hits.append({"id": it.id, "text": it.text, "A": round(A, 4),
                         "M": round(M, 3), "stage": m.stage.value,
                         "cue": (it.cues[0] if it.cues else ""),
                         "certainty": m.stage.certainty})

    # 侧抑制：同一线索组内，除最强那条外各乘 suppress
    groups: dict[str, list[dict]] = {}
    for h in hits:
        groups.setdefault(h["cue"] or "-", []).append(h)
    for group in groups.values():
        if len(group) > 1:
            group.sort(key=lambda x: -x["A"])
            for h in group[1:]:
                h["A"] = round(h["A"] * float(c.get("suppress", 0.78)), 4)
    hits.sort(key=lambda x: -x["A"])
    return [h for h in hits if h["A"] >= theta][:cap]


def render(hits: list[dict], body: BodyState, cfg: Config) -> str:
    """渲染意识层注入件：只给结论不给数字，且带硬预算。"""
    lines = ["# 此刻", "",
             f"- 身体：{'有点累' if body.fatigue > 0.6 else '还行'}；"
             f"负载{'偏高' if body.load > 0.7 else '正常'}。",
             f"- 情绪：{'偏负面' if body.mood < -0.3 else '偏正面' if body.mood > 0.3 else '平的'}。"]
    for h in hits:
        lines.append(f"- 想起：{clip(h['text'], 60)}（{h['certainty']}）")
    limit = cfg.integer("budget.conscious_max_chars", 1400)
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit - 20] + "\n（意识层超预算，后面截掉了。）"
    return text


def decay_e(e0: float, days: float, cfg: Config) -> float:
    """意识层独立的情绪衰减（lambda_e 与事件层略不同，故意保留并可配置）。"""
    return e0 * math.exp(-cfg.num("conscious.lambda_e", 0.00038) * days)
