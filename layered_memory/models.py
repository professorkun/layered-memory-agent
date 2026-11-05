"""数据模型：分层记忆系统里流通的几种对象。

分层命名故意不用「L1/L2/L3」：生产系统里曾同时存在两套编号——
一套按**时间远近**（近 3 天原文 / 每周要点 / 更早一句话），一套按**抽象程度**
（底感 / 感觉 / 事件 / 沉淀），两套的 L2 指的不是同一个东西，非常容易接错。
这里一律用语义名，见 docs/ARCHITECTURE.md 第 2 节。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class Layer(str, Enum):
    """一层 = 一种生命周期 + 一种写入权限 + 一种衰减规则。"""

    CONTEXT = "context"            # 会话上下文：本轮这几句，不落盘
    FACTS = "facts"                # 事实层：身份与稳定字段，不衰减
    EPISODIC = "episodic"          # 事件层：发生过什么（live -> daily -> index）
    GROWTH_BASE = "growth_base"    # 生长层 L0 底感：无来源、不衰减
    GROWTH_FEEL = "growth_feel"    # 生长层 L1 感觉：极慢衰减
    GROWTH_SEDIMENT = "growth_sediment"  # 生长层 L3 沉淀：置信度可升可降
    CONSCIOUS = "conscious"        # 意识层：气质(只读) / 深层模式(只写) / 身体状态
    KNOWLEDGE = "knowledge"        # 外部知识库：确认过的事实，**不等于我的记忆**


class Stage(str, Enum):
    """事件层按细节完整度 D 的当前状态；决定了「能说多确定」。"""

    RULE = "rule"            # 规矩/偏好：不走细节衰减
    INTACT = "intact"        # D >= 0.72 细节还在
    EPISODIC = "episodic"    # 记得轮廓
    FRAGMENT = "fragment"    # 只剩碎片
    RESIDUE = "residue"      # 细节没了，意义/情绪还在
    FADED = "faded"          # 不渲染，只留索引

    @property
    def certainty(self) -> str:
        """给模型看的确定度口径：不许把脑补当回忆。"""
        return {
            "rule": "直接照做，不用犹豫",
            "intact": "可以说具体：几点、在哪、说了什么",
            "episodic": "只能说个大概，细节别硬编",
            "fragment": "只记得有这么几个碎片，顺序和细节都不保真",
            "residue": "只记得这件事很重要、当时什么感觉，别复述情节",
            "faded": "不该主动提起；被问到就说记不清了",
        }[self.value]


STAGE_LABEL = {
    "rule": "一直记着的", "intact": "细节还在", "episodic": "记得轮廓",
    "fragment": "只剩碎片", "residue": "只剩意义和情绪", "faded": "已经忘了",
}
SHORT_LABEL = {"rule": "一直记着的", "intact": "细节还在", "episodic": "记得大概",
               "fragment": "只剩碎片", "residue": "只剩意义和情绪", "faded": "几乎忘了"}


def fingerprint(text: str) -> str:
    """条目 id = 日期 + 正文前 8 位指纹。去标记、去线索后算，改标注不改 id。"""
    clean = re.sub(r"^\s*\[[^\]]{0,160}\]\s*", "", text)
    clean = re.sub(r"[〔【][^〕】]*[〕】]", "", clean)
    clean = "".join(clean.split())
    return hashlib.sha1(clean[:200].encode("utf-8")).hexdigest()[:8]


@dataclass
class MemoryItem:
    """事件层的一条记忆。原始证据只读；索引里的这份是派生的，可重建。"""

    id: str
    date: str                       # ISO YYYY-MM-DD
    text: str
    section: str = ""               # 日记里的 `## 段`，用于判定 rule/event
    layer: Layer = Layer.EPISODIC
    kind: str = "event"             # event | rule
    S: float = 0.35                 # 意义强度（写入时定，之后极慢衰减）
    D0: float = 0.55                # 初始细节完整度
    E: float = 0.15                 # 情绪残留
    C: float = 0.10                 # 巩固程度（由 recalls 推回，只涨不跌）
    N: float = 0.0                  # 干扰程度（相似记忆越多越大）
    cues: list[str] = field(default_factory=list)
    cues_src: str = "auto"          # manual | auto
    inference: float = 0.2          # 重建推断比例，压低 Q
    P: str = ""                     # 当时的判断/立场（不随 D 衰减）
    who: list[str] = field(default_factory=list)   # W：和谁有关
    val: float = 0.0                # V：情绪正负 -1~1
    rel: float = 0.5                # R：关系权重 0~1
    ident: float = 0.5              # I：身份相关性 0~1
    recalls: int = 0
    last_recall: str | None = None
    vis: str = "public"             # 事实层可见性；事件默认 public
    source: str = "daily"           # daily | live | manual

    @classmethod
    def create(cls, text: str, day: str | None = None, **kw) -> "MemoryItem":
        day = day or date.today().isoformat()
        return cls(id=f"{day}#{fingerprint(text)}", date=day, text=text.strip(), **kw)

    def to_index(self) -> dict:
        """索引只存派生量；原始日记永远不删，删了索引可重建（会丢 recalls）。"""
        d = {k: getattr(self, k) for k in (
            "id", "date", "text", "section", "kind", "S", "D0", "E", "C", "N",
            "cues", "cues_src", "inference", "P", "who", "val", "rel", "ident",
            "recalls", "last_recall", "vis", "source")}
        d["layer"] = self.layer.value
        return d

    @classmethod
    def from_index(cls, d: dict) -> "MemoryItem":
        kw = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "layer"}
        kw["layer"] = Layer(d.get("layer", "episodic"))
        return cls(**kw)

    def tags(self) -> dict[str, Any]:
        """给好感度/情绪/人物模型消费的标注，见 docs/INTEGRATION.md。"""
        return {"who": self.who, "V": self.val, "R": self.rel, "I": self.ident,
                "E": self.E, "S": self.S, "P": self.P}


@dataclass
class Scored:
    """一条记忆在**今天**的即时数值；不落盘，每次现算。"""

    item: MemoryItem
    days: int
    tau: float
    lam: float
    half_life: float
    S: float
    D: float
    E: float
    C: float
    N: float
    R: float
    Q: float
    stage: Stage
    P_alive: bool

    def as_dict(self) -> dict:
        return {"id": self.item.id, "date": self.item.date, "days": self.days,
                "tau": round(self.tau, 2), "lambda": round(self.lam, 5),
                "half_life": round(self.half_life, 1), "S": round(self.S, 3),
                "D": round(self.D, 3), "E": round(self.E, 3), "C": round(self.C, 3),
                "N": round(self.N, 3), "R": round(self.R, 3), "Q": round(self.Q, 3),
                "stage": self.stage.value, "certainty": self.stage.certainty,
                "P": self.item.P, "P_alive": self.P_alive}


@dataclass
class Fact:
    """事实层条目：稳定字段，不是事件，不参与衰减。"""
    key: str
    value: str
    vis: str = "public"             # public | quiet | secret

    def project(self, channel: str, cfg) -> str:
        """按出口投影。铁律：值不过界——群里绝不能出现 quiet/secret 的值。"""
        if channel == "private":
            return f"- {self.key}：{self.value}"
        if self.vis == "public":
            return f"- {self.key}：{self.value}"
        if self.vis == "quiet":
            return f"- {cfg.get('privacy.quiet_phrase')}（{self.key}）"
        return ""                    # secret：群里连「知道」都不表态


@dataclass
class Feeling:
    """生长层 L1：从 residue 沉下来的感觉；记忆掉了它还在。"""
    text: str
    cues: list[str] = field(default_factory=list)
    strength: float = 0.7
    source_id: str = ""


@dataclass
class Sediment:
    """生长层 L3：多条同类事件攒出的判断；置信度必须能降。"""
    text: str
    confidence: float = 0.8
    topic: str = ""

    def label(self, floor: float) -> str:
        return self.text if self.confidence >= floor else f"（我可能想错了）{self.text}"


@dataclass
class Edge:
    """联结：想到 A 带出 B。边权比事件慢衰减。"""
    a: str
    b: str
    weight: float
    born: str

    def decayed(self, days: float, cfg) -> float:
        tau = cfg.num("growth.edge_tau")
        p = cfg.num("growth.edge_p")
        return self.weight * (1 + days / max(tau, 0.01)) ** (-p)


@dataclass
class Hit:
    """检索命中：带确定度，避免模型把「想起」当成「刚发生」。"""
    item: MemoryItem
    cue: float
    R: float
    stage: Stage
    D: float
