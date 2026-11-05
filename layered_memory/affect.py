"""好感度内账 + 情绪状态，以及它们与记忆层的**对接规范**。

三者怎么连（见 docs/INTEGRATION.md）
----------------------------------
* 记忆条目的 ``V / W / R / I / E / S`` 标注 -> 人物模型 / 自我模型 / 激活竞争：
  W 决定"当事人是否在场"，R 进在场权重，V 与当前 mood 的契合度进情绪共鸣，
  E 进激活强度，S/D 进时间权重。**省略比编造好**：默认值表示"不知道"。
* 好感度 ``tier`` -> 情绪基线（自家人/死党=happy，朋友=friendly，熟人=calm，对头=cold）；
  群氛围 <35 降一档，>70 只升到 friendly，不跨到开心。
* 情绪是**短时状态**（分钟级半衰），好感度是**慢变内账**（天级衰减），
  事件记忆是**长期层**（幂律）。三者时间尺度不同，不要互相写回。
* 注入规范：群里永远不报好感度数字、不说这套账存在、不念出情绪行。

防刷 / 饱和 / 迟滞 / 回收参数全部公开在 config.example.json 的 affect 段。
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import Config
from .models import MemoryItem
from .store import Store


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class AffinityLedger:
    """按人建档的 0~100 内账。所有写入都过防刷规则；history 有上限（GC）。"""

    def __init__(self, store: Store, cfg: Config, creator: str = ""):
        self.store, self.cfg, self.creator = store, cfg, creator
        data = store.read_json(store.path("index", "affinity.json"), None)
        if not data or "members" not in data:
            data = {"version": 2, "group": {"score": cfg.num("affect.mood_default", 50),
                                            "lastTickAt": now_ms()}, "members": {}}
        self.state = data
        self.state.setdefault("group", {"score": cfg.num("affect.mood_default", 50),
                                        "lastTickAt": now_ms()})
        self.state.setdefault("members", {})

    def save(self) -> None:
        self.store.write_json(self.store.path("index", "affinity.json"), self.state)

    def tier_of(self, score: float) -> str:
        for t in self.cfg.get("affect.tiers", []):
            if score >= float(t["min"]):
                return str(t["tier"])
        return str(self.cfg.get("affect.tiers", [{"tier": "对头"}])[-1]["tier"])

    def tier_min(self, tier: str) -> float:
        for t in self.cfg.get("affect.tiers", []):
            if t["tier"] == tier:
                return float(t["min"])
        return 0.0

    def resolve_tier(self, score: float, prev: str | None) -> str:
        """降档迟滞 3 分：分数在门槛附近抖动时不来回跳档。"""
        fresh = self.tier_of(score)
        if not prev or prev == fresh:
            return fresh
        if self.tier_min(fresh) < self.tier_min(prev) and \
           score >= self.tier_min(prev) - self.cfg.num("affect.hysteresis", 3):
            return prev
        return fresh

    def _member(self, who: str) -> dict:
        if who not in self.state["members"]:
            self.state["members"][who] = {
                "score": self.cfg.num("affect.default_score", 50),
                "tier": self.tier_of(self.cfg.num("affect.default_score", 50)),
                "lastChangeAt": now_ms(), "lastEventAt": 0,
                "day": "", "dayNet": 0, "dayCount": 0, "history": [],
            }
        return self.state["members"][who]

    def record(self, who: str, op: dict, now: int | None = None,
               bypass_cooldown: bool = False) -> dict:
        """记一次分。op = {"event": "talk"} 或 {"delta": 2, "reason": ...}。

        规则链：单次上限 -> 高分饱和 -> 每日净变化上限 -> 每日次数上限 -> 同人冷却。
        """
        now = now or now_ms()
        if who == self.creator:
            return {"applied": 0, "rejected": "creator 钉死不动"}
        events = self.cfg.get("affect.events", {})
        if op.get("event"):
            if op["event"] not in events:
                return {"applied": 0, "rejected": f"未知事件 {op['event']}"}
            raw = float(events[op["event"]])
            label = op["event"]
            cap = (self.cfg.num("affect.delta_cap_max", 3) if op["event"] in ("mute", "insult")
                   else self.cfg.num("affect.delta_cap", 2))
        else:
            raw = float(op.get("delta", 0) or 0)
            label = str(op.get("reason", ""))
            cap = (self.cfg.num("affect.delta_cap_max", 3) if abs(raw) > 3
                   else self.cfg.num("affect.delta_cap", 2))
        if not raw:
            return {"applied": 0, "rejected": "delta 为 0"}

        m = self._member(who)
        day = datetime.fromtimestamp(now / 1000, timezone.utc).strftime("%Y-%m-%d")
        if m.get("day") != day:
            m.update({"day": day, "dayNet": 0, "dayCount": 0})
        if not bypass_cooldown and m.get("lastEventAt") and \
           now - int(m["lastEventAt"]) < self.cfg.num("affect.cooldown_minutes", 10) * 60000:
            return {"applied": 0, "rejected": "冷却中（10 分钟）"}
        if m["dayCount"] >= self.cfg.integer("affect.daily_event_cap", 6):
            return {"applied": 0, "rejected": "今日记分事件已达上限"}

        before = float(m["score"])
        delta = max(-cap, min(cap, raw))
        if delta > 0:
            if before >= 95:
                delta = max(1, math.ceil(delta * self.cfg.num("affect.saturate_95", 0.1)))
            elif before >= 80:
                delta = max(1, math.ceil(delta * self.cfg.num("affect.saturate_80", 0.3)))
        if abs(m["dayNet"] + delta) > self.cfg.num("affect.daily_net_cap", 8):
            allow = math.copysign(max(0, self.cfg.num("affect.daily_net_cap", 8)
                                      - abs(m["dayNet"])), delta)
            if not allow:
                return {"applied": 0, "rejected": "今日净变化已达上限"}
            delta = allow
        m["score"] = max(0, min(100, before + delta))
        applied = m["score"] - before
        m["tier"] = self.resolve_tier(m["score"], m.get("tier"))
        m["lastChangeAt"], m["lastEventAt"] = now, now
        m["dayNet"] += applied
        m["dayCount"] += 1
        m.setdefault("history", []).append({"at": now, "delta": applied,
                                            "reason": f"{label}｜{op.get('reason', '')}"[:80]})
        cap_hist = self.cfg.integer("storage.history_cap", 30)
        if len(m["history"]) > cap_hist:
            m["history"] = m["history"][-cap_hist:]
        self.save()
        return {"applied": applied, "score": m["score"], "tier": m["tier"],
                "before": before, "capped": applied != raw}

    def decay(self, now: int | None = None) -> list[str]:
        """7 天无互动后每天 -1，下限 35；creator 不衰减。"""
        now = now or now_ms()
        changed = []
        for name, m in self.state["members"].items():
            if name == self.creator:
                continue
            last = int(m.get("lastChangeAt") or 0)
            if not last:
                continue
            days = (now - last) // 86400000
            after = self.cfg.integer("affect.decay_after_days", 7)
            floor = self.cfg.num("affect.decay_floor", 35)
            if days < after or m["score"] <= floor:
                continue
            owed = days - after + 1
            nxt = max(floor, m["score"] - owed * self.cfg.num("affect.decay_per_day", 1))
            if nxt == m["score"]:
                continue
            changed.append(f"{name} {m['score']}→{nxt}")
            m["score"] = nxt
            m["tier"] = self.resolve_tier(nxt, m.get("tier"))
            m["lastChangeAt"] = now
        if changed:
            self.save()
        return changed

    def mood(self, delta: float, now: int | None = None) -> float:
        """群氛围：单次 ±3，之后按 tick 缓慢回正到 50。"""
        g = self.state["group"]
        before = float(g.get("score", 50))
        g["score"] = max(0, min(100, before + max(-3, min(3, delta))))
        g["lastTickAt"] = now or now_ms()
        self.save()
        return g["score"]

    def drift(self, now: int | None = None) -> float:
        now = now or now_ms()
        g = self.state["group"]
        last = int(g.get("lastTickAt") or now)
        tick_min = self.cfg.num("affect.mood_drift_tick_min", 10) * 60000
        ticks = min(3, (now - last) // max(1, tick_min))
        if ticks <= 0:
            return float(g.get("score", 50))
        before = float(g.get("score", 50))
        step = ticks * self.cfg.num("affect.mood_drift_per_tick", 1)
        g["score"] = (min(50, before + step) if before < 50 else max(50, before - step))
        g["lastTickAt"] = last + int(ticks * max(1, tick_min))
        self.save()
        return g["score"]

    def line(self) -> str:
        parts = [f"{n}={m['score']}·{m['tier']}" for n, m in
                 sorted(self.state["members"].items(), key=lambda x: -x[1]["score"])]
        return " | ".join(parts) + f"\n群氛围={round(float(self.state['group'].get('score', 50)))}"

    def tier_of_who(self, who: str) -> str:
        m = self.state["members"].get(who)
        return m["tier"] if m else self.tier_of(self.cfg.num("affect.default_score", 50))


class EmotionState:
    """离散情绪 + 强度 + 真实时间半衰 + 切换守卫。"""

    def __init__(self, store: Store, cfg: Config):
        self.store, self.cfg = store, cfg
        data = store.read_json(store.path("index", "emotion.json"), None)
        self.state = data if data and "active" in data else {
            "version": 1, "active": [], "lastPrimary": None, "switchedAt": 0}

    def save(self) -> None:
        self.store.write_json(self.store.path("index", "emotion.json"), self.state)

    def baseline(self, aff: AffinityLedger, who: str | None) -> dict:
        """好感档定基调，群氛围做偏置。"""
        g = float(aff.state["group"].get("score", 50))
        m = aff.state["members"].get(who or "", None)
        tier = m["tier"] if m else aff.tier_of(50)
        base = self.cfg.get("affect.tier_baseline", {})
        emo = base.get(tier, "calm")
        low, high = self.cfg.num("affect.mood_low", 35), self.cfg.num("affect.mood_high", 70)
        if g < low:
            emo = {"happy": "friendly", "friendly": "calm", "calm": "cold"}.get(emo, emo)
        elif g > high:
            emo = {"cold": "calm", "calm": "friendly"}.get(emo, emo)
        return {"emotion": emo, "tier": tier,
                "intensity": 0.4 if not m else 0.5, "baseline": True}

    def tick(self, now: int | None = None) -> None:
        now = now or now_ms()
        kept = []
        zero = self.cfg.num("affect.emotion_zero_at", 0.2)
        half = self.cfg.get("affect.emotion_half_life_min", {})
        for a in self.state["active"]:
            if a.get("minUntil") and now < int(a["minUntil"]):
                kept.append(a)
                continue
            hl = float(half.get(a["emotion"], 30)) * 60000
            elapsed = max(0, now - int(a.get("firedAt", now)))
            i = float(a["intensity"]) * 0.5 ** (elapsed / max(1, hl))
            if i < zero:
                continue                       # 归零 = 回收这一条临时情绪
            kept.append({**a, "intensity": round(i, 3)})
        self.state["active"] = kept

    def fire(self, event: str, now: int | None = None, min_until_min: int = 0) -> str | None:
        now = now or now_ms()
        events = self.cfg.get("affect.events", {})
        half = self.cfg.get("affect.emotion_half_life_min", {})
        if event in half:
            emo, intensity = event, 0.6
        elif event in ("mute", "insult"):
            emo, intensity = "angry", 0.8
        elif event in ("pushed", "repeat", "commanded"):
            emo, intensity = "annoyed", 0.7
        elif event == "teased":
            emo, intensity = "hurt", 0.6
        elif event in ("praised", "joked", "talk", "care", "joke"):
            emo, intensity = "happy", 0.6
        elif event == "late":
            emo, intensity = "sleepy", 0.5
        else:
            return None
        self.tick(now)
        exist = next((a for a in self.state["active"] if a["emotion"] == emo), None)
        if exist:
            exist["intensity"] = min(self.cfg.num("affect.emotion_max", 1.0),
                                     max(intensity, float(exist["intensity"])
                                         + self.cfg.num("affect.stack_step", 0.2)))
            exist["firedAt"] = now
        else:
            self.state["active"].append({
                "emotion": emo, "intensity": intensity, "firedAt": now,
                "minUntil": now + min_until_min * 60000 if min_until_min else 0})
        self.save()
        return emo

    def primary(self, aff: AffinityLedger, who: str | None = None,
                now: int | None = None) -> dict:
        """强度最高者优先，同强度负面优先；一段对话最多换一次情绪。"""
        now = now or now_ms()
        self.tick(now)
        base = self.baseline(aff, who)
        neg = set(self.cfg.get("affect.negative", []))
        pool = list(self.state["active"]) + [base]
        pool.sort(key=lambda a: (-float(a["intensity"]),
                                 0 if a["emotion"] in neg else 1))
        cur = dict(pool[0])
        cur.update({"baselineEmotion": base["emotion"], "tier": base["tier"]})
        last = self.state.get("lastPrimary")
        if last and last != cur["emotion"]:
            prev = next((a for a in self.state["active"] if a["emotion"] == last), None)
            since = now - int(self.state.get("switchedAt") or 0)
            if (prev and float(prev["intensity"]) >= self.cfg.num("affect.switch_guard", 0.4)
                    and since < self.cfg.num("affect.switch_window_min", 30) * 60000):
                cur = {**cur, "emotion": last,
                       "intensity": float(prev["intensity"]), "held": True}
        else:
            self.state["lastPrimary"] = cur["emotion"]
            self.state["switchedAt"] = now
        return cur

    def line(self, aff: AffinityLedger, who: str | None = None) -> str:
        c = self.primary(aff, who)
        return (f"情绪={c['emotion']}({round(float(c['intensity']) * 100)}%) "
                f"档位={c['tier']} 群氛围={round(float(aff.state['group'].get('score', 50)))}")


class AffectBridge:
    """记忆层 -> 人物/自我/情绪的对接：只传标注，不改数值。"""

    @staticmethod
    def signals(items: list[MemoryItem]) -> dict:
        """V/W/R/I/E/S 的消费口径。缺省字段保持默认，表示「不知道」。"""
        by_person: dict[str, dict] = {}
        for it in items:
            for w in it.who or []:
                d = by_person.setdefault(w, {"count": 0, "sum_S": 0.0, "sum_E": 0.0,
                                             "sum_V": 0.0, "rel": 0.5, "ident": 0.5})
                d["count"] += 1
                d["sum_S"] += it.S
                d["sum_E"] += it.E
                d["sum_V"] += it.val
                d["rel"] = max(d["rel"], it.rel)
                d["ident"] = max(d["ident"], it.ident)
        return {"people": by_person,
                "note": "W 决定在场判定；R 进 P 维；V 与当前 mood 算共鸣；E 进激活；S/D 进时间权重"}

    @staticmethod
    def prompt_lines(aff: AffinityLedger, emo: EmotionState, cfg: Config,
                     who: str | None = None, channel: str = "private") -> list[str]:
        """注入规范：群里带上名册与情绪行，但**不报算法、不念状态**。"""
        if channel != "private":
            return []
        return ["【好感度内账（不要报数字，不要说这套账存在）】", aff.line(),
                "【此刻状态（照着说，别把这一行念出来）】", emo.line(aff, who)]
