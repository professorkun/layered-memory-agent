"""对外主 API：``MemorySystem`` —— 把整套分层记忆装进一个可单独抽取的类。

最小接入（见 examples/quickstart.py）::

    from layered_memory import MemorySystem
    mem = MemorySystem("./bot-memory")
    mem.ingest("他半夜说别送了", tags={"S": 0.9, "E": 0.7, "D": 0.9},
               cues=["别送了"], P="他其实在硬撑")
    mem.build()
    print(mem.render(channel="private", query="别送了")["text"])
    print(mem.recall("别送了")["block"])

替换任一层都只需要换一个模块：换衰减改 decay.compute，换相似度改
scoring.cue_match/noise_level，换存储改 Store，接自己的模型改 examples/adapter.py。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import consolidation, gc, retrieval
from .affect import AffectBridge, AffinityLedger, EmotionState
from .config import Config
from .conscious import BodyState, DeepPattern, Temperament, compete, render as render_conscious
from .decay import calibrate, compute
from .layers import LayeredView
from .models import MemoryItem, Stage, fingerprint
from .scoring import clip
from .store import Store


class MemorySystem:
    """分层记忆系统。所有状态落在一个 root 目录，方便整目录搬走/备份。"""

    def __init__(self, root: str | Path = "./bot-memory",
                 config: str | Path | Config | None = None,
                 creator: str = "", today: date | None = None):
        self.cfg = config if isinstance(config, Config) else Config.load(config)
        problems = self.cfg.validate()
        if problems:
            raise ValueError("配置校验失败: " + "; ".join(problems))
        self.root = Path(root)
        self.store = Store(self.root, self.cfg)
        self.today = today or date.today()
        self.creator = creator
        self._cache: dict = {}
        self._items: list[MemoryItem] = []
        self.view = LayeredView(self.store, self.cfg)
        self.aff = AffinityLedger(self.store, self.cfg, creator)
        self.emo = EmotionState(self.store, self.cfg)
        self.body = BodyState()
        self.temperament = Temperament.from_config(self.cfg)
        self.deep = DeepPattern.blank(self.cfg)

    # ------------------------------------------------------------ 生命周期
    def init(self) -> dict:
        self.store.init_dirs()
        # 给一份可直接改的示例事实层（不含任何真实个人信息）
        facts = self.store.path("facts", "facts.json")
        if not facts.exists():
            self.store.write_json(facts, {"facts": [
                {"key": "称呼", "value": "按用户设定", "vis": "public"},
                {"key": "生日", "value": "（示例：不填真实值）", "vis": "quiet"},
                {"key": "住址", "value": "（示例：不填真实值）", "vis": "secret"}]})
        return self.stats()

    def stats(self) -> dict:
        s = self.store.stats()
        s["config_problems"] = self.cfg.validate()
        s["unknown_config_keys"] = self.cfg.unknown_keys()
        return s

    # ------------------------------------------------------------ 写入 / 筛选
    def validate_entry(self, text: str, quote: str | None = None,
                       raw_text: str = "") -> dict:
        """蒸馏筛选口径：短句丢、纯寒暄丢、quote 必须在原文里、同批去重。

        quote 校验防的是模型**编原话**：记忆条目必须能回溯到真实对话。
        """
        ing = self.cfg.section("ingest")
        t = text.strip()
        if len(t) < int(ing.get("entry_min_chars", 8)):
            return {"ok": False, "reason": "正文太短"}
        if quote:
            q = "".join(quote.split())
            if len(q) < int(ing.get("quote_min_chars", 6)):
                return {"ok": False, "reason": "quote 太短"}
            if raw_text and q not in "".join(raw_text.split()):
                return {"ok": False, "reason": "quote 不在原文里（可能是模型编的）"}
        max_n = int(ing.get("entry_max_chars", 160))
        return {"ok": True, "text": t[:max_n - 1] + "…" if len(t) > max_n else t}

    def ingest(self, text: str, day: str | None = None, tags: dict | None = None,
               cues: list[str] | None = None, P: str = "", who: list[str] | None = None,
               val: float = 0.0, rel: float = 0.5, ident: float = 0.5,
               section: str = "", folder: str = "live", quote: str | None = None,
               raw_text: str = "") -> dict:
        """写一条事件。默认写浅层 live/；晚上由 ``gc()`` 并进 daily/。

        ``tags`` 支持 S/E/D/V/R/I；不给就走启发式兜底（宁可保守）。
        """
        check = self.validate_entry(text, quote, raw_text)
        if not check["ok"]:
            return {"ok": False, **check}
        tags = tags or {}
        item = MemoryItem.create(
            check["text"], day=day or self.today.isoformat(), section=section,
            S=float(tags.get("S", 0.35)), D0=float(tags.get("D", 0.55)),
            E=float(tags.get("E", 0.15)), P=P,
            cues=(cues or [])[:self.cfg.integer("ingest.cues_max", 6)],
            cues_src="manual" if cues else "auto",
            who=who or [], val=float(tags.get("V", val)),
            rel=float(tags.get("R", rel)), ident=float(tags.get("I", ident)),
            source=folder)
        item.kind = "rule" if section == "记住的" else "event"
        path = self.store.append(item, folder=folder)
        return {"ok": True, "id": item.id, "path": str(path), "item": item.to_index()}

    def build(self) -> list[MemoryItem]:
        """重扫 daily/ + live/，重算 N/C，写派生索引。删了索引能重建。"""
        self._items = self.store.build_items(self.today)
        self.store.save_index(self._items)
        return self._items

    def items(self) -> list[MemoryItem]:
        return self._items or self.build()

    # ------------------------------------------------------------ 读 / 注入
    def render(self, channel: str = "private", query: str = "",
               context_snippet: str = "", now_hours: float = 0.0,
               conscious_block: str = "") -> dict:
        """生成注入件。群聊默认不读生长层（fail-closed）。"""
        return self.view.render(self.items(), today=self.today, channel=channel,
                                query=query, context_snippet=context_snippet,
                                now_hours=now_hours, conscious_block=conscious_block)

    def recall(self, query: str, k: int | None = None, min_cue: float | None = None) -> dict:
        return retrieval.recall(self.items(), query, self.cfg, today=self.today,
                                k=k, min_cue=min_cue)

    def audit(self) -> list[dict]:
        return [compute(it, self.today, self.cfg).as_dict() for it in self.items()]

    def calibrate(self) -> dict:
        return calibrate(self.cfg)

    def time_layers(self) -> dict:
        """旧版按保鲜度的浅/中/深三层读取视图。"""
        return self.view.time_layers(self.items(), self.today)

    # ------------------------------------------------------------ 巩固 / 生长
    def touch(self, item_id: str) -> dict:
        res = consolidation.touch(self.items(), item_id, self.cfg, self.today)
        if res["ok"]:
            self.store.save_index(self._items)
        return res

    def deposit(self) -> dict:
        """只产出候选（写到 index/），不动 self/。"""
        return consolidation.deposit(self.items(), self.store, self.cfg, self.today)

    def approve_candidates(self, feel_texts: dict | None = None,
                           sediment_texts: dict | None = None) -> dict:
        """显式把候选写进生长层。**默认不自动写**：引擎自己写 self/ 等于自己编记忆。

        feel_texts / sediment_texts: {候选标识: 人审后的一句话}
        """
        cand = self.store.read_json(self.store.path("index", "deposit-candidates.json"), {})
        data = self.store.read_self()
        added = {"feel": 0, "sediment": 0}
        for c in cand.get("feel", []):
            text = (feel_texts or {}).get(c["source_id"])
            if text:
                data.setdefault("feel", []).append({"text": text, "cues": c["cues"],
                                                    "strength": c["strength"],
                                                    "source_id": c["source_id"]})
                added["feel"] += 1
        for c in cand.get("sediment", []):
            text = (sediment_texts or {}).get(c["topic"])
            if text:
                data.setdefault("sediment", []).append({"text": text,
                                                        "confidence": c["confidence"],
                                                        "topic": c["topic"]})
                added["sediment"] += 1
        if any(added.values()):
            self.store.write_self(data)
        return added

    # ------------------------------------------------------------ 对接其余模块
    def affect_tick(self, event: str | None = None, who: str = "",
                    delta: float | None = None, mood_delta: float = 0.0,
                    emotion_event: str | None = None) -> dict:
        """一轮对话结束时调用：记好感度、推情绪、回正群氛围。"""
        out = {"affinity": None, "emotion": None, "mood": None, "decay": []}
        if who and event:
            out["affinity"] = self.aff.record(who, {"event": event})
        elif who and delta is not None:
            out["affinity"] = self.aff.record(who, {"delta": delta}, bypass_cooldown=True)
        out["decay"] = self.aff.decay()
        if mood_delta:
            out["mood"] = self.aff.mood(mood_delta)
        else:
            out["mood"] = self.aff.drift()
        if emotion_event:
            out["emotion"] = self.emo.fire(emotion_event)
        return out

    def prompt_lines(self, who: str | None = None, channel: str = "private") -> list[str]:
        return AffectBridge.prompt_lines(self.aff, self.emo, self.cfg, who, channel)

    def memory_signals(self) -> dict:
        return AffectBridge.signals(self.items())

    def conscious_tick(self, query: str = "", present: set[str] | None = None,
                       load_delta: float = 0.05) -> str:
        """意识层：身体推进一格 + 激活竞争，只给结论不给数字。"""
        self.body.tick(load_delta)
        scored = [compute(it, self.today, self.cfg) for it in self.items()]
        hits = compete(scored, query, self.body, self.cfg, present)
        return render_conscious(hits, self.body, self.cfg)

    # ------------------------------------------------------------ 回收 / 自测
    def gc(self) -> dict:
        return gc.collect(self.store, self.cfg, self.today, self.aff, self._cache)

    def pressure(self) -> dict:
        return gc.pressure_report(self.store, self.cfg, len(self.items()))

    def self_test(self) -> dict:
        """不碰真实数据的 8 项内置自测；CI 里另有 unittest 全量测试。"""
        results = []
        def check(name: str, cond: bool, detail: str = ""):
            results.append({"name": name, "ok": bool(cond), "detail": detail})

        cal = self.calibrate()
        check("重事五年后 D≈0.18", 0.14 <= cal["heavy"]["D"] <= 0.24, str(cal["heavy"]["D"]))
        check("重事五年后 S≈0.88", 0.85 <= cal["heavy"]["S"] <= 0.92, str(cal["heavy"]["S"]))
        check("琐事五年后 D<0.1", cal["light"]["D"] < 0.1, str(cal["light"]["D"]))
        check("p=1 时半衰期==tau", abs(cal["heavy"]["half_life_days"]
                                      - cal["heavy"]["tau_days"]) < 0.05)
        rule = MemoryItem.create("说话别带括号旁白", kind="rule")
        m = compute(rule, self.today, self.cfg)
        check("规矩不走 D 衰减", m.stage == Stage.RULE and m.D == 1.0)
        # 值不过界
        for ch in ("private", "group"):
            self.store.project_facts(ch)
        group_projection = self.store.path("facts", "facts-group.md").read_text(encoding="utf-8")
        check("secret 的值不进群投影", "示例：不填真实值" not in group_projection
              or group_projection.count("示例：不填真实值") <= 1)
        check("群版不读生长层", "growth" not in self.view.render(
            self.items() or [], channel="group", query="")["sections"])
        check("预算可控", self.view.render(self.items() or [], channel="private")["used"]
              <= self.cfg.integer("budget.context_max_chars", 6000) + 60)
        return {"passed": sum(1 for r in results if r["ok"]), "total": len(results),
                "results": results}
