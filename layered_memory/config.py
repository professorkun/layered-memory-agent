"""配置加载：所有可调参数集中在一处，且**全部有默认值和默认值来源**。

设计取舍
--------
* 参数不放进代码常量里，而是走一份 JSON。别人接自己的机器人时，只需要复制
  ``config.example.json`` 改几个数，不用改源码。
* ``DEFAULTS`` 是兜底：配置文件缺项不会崩，只会用生产系统校准过的默认值。
* ``Config.get("decay.tau0")`` 用点路径取值，新增参数不必改 dataclass。
* ``validate()`` 只挡明显越界的值（负数、概率 > 1），不做"智能纠正"——
  配置写错了应该报错，不该被悄悄改掉。

本文件里的默认值来自一套实际跑过的聊天机器人（2026-10 ~ 2026-11 校准），
含义见 docs/CONFIG.md 与 docs/TUNING.md。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

# --------------------------------------------------------------------------
# 默认值：与仓库根目录 config.example.json 一一对应。
# 测试 tests/test_all.py 会断言两份一致，防止"文档里写了、代码里藏着别的数"。
# --------------------------------------------------------------------------
DEFAULTS: dict = {
    # 事件层的遗忘曲线（幂律）。详见 docs/ARCHITECTURE.md 第 3 节。
    "decay": {
        "tau0": 41.0,             # 基准时间尺度（天）
        "k_s": 1.5,               # 意义强度拉长 tau 的系数（指数形式）
        "k_e": 1.0,               # 情绪残留拉长 tau 的系数
        "k_c": 0.5,               # 巩固程度拉长 tau 的系数
        "k_n": 1.5,               # 干扰压短 tau 的系数
        "p": 1.0,                 # 幂律形状；p=1 时半衰期 == tau
        "lambda_s": 0.00004,      # S 的日衰减（五年约掉 7%）
        "lambda_e": 0.00033,      # E 的日衰减（五年约掉一半）
        "recency_tau": 30.0,      # 近因项时间常数（天）
        "min_tau": 0.05,          # tau 下限，防除零
    },
    # 可检索性 R 的线性权重；cue 由检索时命中度补上，不在此配置内。
    "retrievability": {
        "w_s": 0.30, "w_e": 0.22, "w_cue": 0.28,
        "w_recency": 0.10, "w_c": 0.10, "w_n": 0.15,
    },
    # 生命周期阈值：按细节完整度 D 分档，规矩(rule)不参与 D 衰减。
    "lifecycle": {
        "d_intact": 0.72, "d_episodic": 0.42, "d_fragment": 0.15,
        "s_residue": 0.45, "e_residue": 0.30,
        "rule_resident_days": 60,   # 规矩在注入件常驻多久，之后交给长期层
    },
    # 巩固 / 重固化：每次回忆的代价与收益，全部公开。
    "consolidation": {
        "c_base": 0.10, "c_per_recall": 0.18, "c_cap": 0.90,
        "touch_d0_factor": 1.04, "d0_cap": 0.98,
        "p_alive_min_s": 0.30,      # 观点(P)还立着所需的最小 S
    },
    # 没有人工标注时的兜底打分（宁可保守，不猜"这件事有多重要"）。
    "heuristic": {
        "s_base": 0.35, "s_remembered": 0.70, "s_imp2": 0.62, "s_imp1": 0.50,
        "s_concrete_bonus": 0.05, "s_cap": 0.92,
        "d0_base": 0.55, "d0_con3": 0.85, "d0_con2": 0.75, "d0_con1": 0.65,
        "d0_long_bonus": 0.08, "d0_cap": 0.95, "d0_rule_cap": 0.70,
        "long_text_chars": 55,
        "e_base": 0.20, "e_per_word": 0.16, "e_cap": 0.85, "e_none": 0.15,
        "default_inference": 0.2,   # 〔推断：x〕缺失时的重建惩罚
    },
    # 干扰 N：字符二元组 Jaccard 近似（不是语义相似度，见"局限"）。
    "noise": {"bigram_scale": 1.6, "cap": 0.9},
    # 线索 / 检索：手写线索命中、二元组退化、top-k、预算。
    "cue": {
        "manual_match": 0.9, "bigram_overlap_min": 0.25, "bigram_match_cap": 0.7,
        "default_min_cue": 0.5, "default_k": 3, "recall_max_chars": 700,
        "recall_clip_chars": 110, "auto_cue_max": 3, "cue_rows": 16,
    },
    # 写入前的筛选：从原始对话里挑出值得记的事。
    "ingest": {
        "window_hours": 3, "overlap_seconds": 90, "max_window_hours": 26,
        "min_rows": 4, "max_rows": 120, "max_prompt_chars": 6000,
        "max_entries": 3, "quote_min_chars": 6, "entry_min_chars": 8,
        "entry_max_chars": 160, "cues_max": 6, "lock_stale_seconds": 900,
    },
    # 注入预算与分段硬上限：内存控制的第一道闸。
    "budget": {
        "context_max_chars": 6000, "merged_max_chars": 8000,
        "cue_rows": 16, "clip": {"rule": 95, "intact": 150, "episodic": 110,
                                  "fragment": 60, "residue": 70, "digest": 80},
        "section_limits": {"rule": 14, "intact": 12, "episodic": 12,
                            "fragment": 12, "residue": 8},
        "conscious_max_chars": 1400, "facts_order": 89, "memory_order": 90,
        "recall_order": 91, "ltm_order": 50,
    },
    # 存储 / 回收：备份轮转、历史条数上限、seen 上限、缓存淘汰。
    "storage": {
        "max_backups": 10, "history_cap": 30, "seen_cap": 500,
        "seen_trim": 100, "log_max_bytes": 200000, "recall_timeout_ms": 4000,
        "recall_max_buffer": 1048576,
    },
    # 生长层：事件 -> 感觉 -> 沉淀 -> 联结。
    "growth": {
        "feel_e_min": 0.30, "feel_residue_days": 7,
        "sediment_min_items": 5, "sediment_min_s": 3.0,
        "sediment_span_days": 14, "sediment_drop": 0.15, "sediment_floor": 0.40,
        "w_cue": 0.30, "w_cue_max": 0.60, "w_entity": 0.40,
        "w_near": 0.10, "w_mood": 0.20, "w_manual": 0.80,
        "edge_tau": 90.0, "edge_p": 0.5, "edge_reinforce": 0.10,
        "base_feel_min": 5, "base_feel_max": 9,
        "curiosity_per_day": 1, "state_fresh_hours": 36, "state_drop_days": 7,
    },
    # 意识层：激活竞争（不是所有记忆都该被想起来）+ 深层模式（改系数不改条目）。
    "conscious": {
        "theta": 0.34, "cap": 4, "suppress": 0.78,
        "strong_mood_threshold": 0.6, "strong_arousal": 0.70,
        "strong_theta": 0.40, "strong_cap": 2, "absent_factor": 0.55,
        "misses_bonus": 0.02, "misses_cap": 0.06, "misses_min_m": 0.35,
        "attention_base": 1.10, "attention_load": 0.55,
        "attention_arousal": 0.10, "attention_min": 0.55, "attention_max": 1.25,
        "w_m": 0.45, "w_p": 0.16, "w_emo": 0.16, "w_t": 0.23,
        "lambda_e": 0.00038,        # 注意：意识层独立实现用了略不同的值，故意保留
        "temperament": {
            "threat_sensitivity": 0.55, "novelty_pull": 0.70, "caution": 0.40,
            "ambiguity_tolerance": 0.45, "warmth": 0.60,
            "persistence": 0.50, "arousal_gain": 0.50,
        },
        "deep_traits": ["trust_update_bias", "absent_sensitivity", "promise_weight",
                        "abandon_speed", "repair_ease", "attention_need",
                        "social_prediction_bias"],
    },
    # 好感度 / 情绪：内账与短期状态，接口见 docs/INTEGRATION.md。
    "affect": {
        "default_score": 50, "creator_score": 95,
        "delta_cap": 2, "delta_cap_max": 3, "daily_net_cap": 8,
        "daily_event_cap": 6, "cooldown_minutes": 10,
        "saturate_80": 0.3, "saturate_95": 0.1,
        "decay_after_days": 7, "decay_per_day": 1, "decay_floor": 35,
        "hysteresis": 3, "mood_default": 50, "mood_drift_per_tick": 1,
        "mood_drift_tick_min": 10, "mood_low": 35, "mood_high": 70,
        "tiers": [{"tier": "自家人", "min": 90}, {"tier": "死党", "min": 75},
                  {"tier": "朋友", "min": 55}, {"tier": "熟人", "min": 25},
                  {"tier": "对头", "min": 0}],
        "events": {"talk": 2, "care": 2, "reply": 1, "joke": 1,
                   "ignore": -1, "command": -1, "repeat": -2,
                   "mute": -3, "insult": -3},
        "emotion_half_life_min": {"angry": 10, "annoyed": 3, "hurt": 10,
                                  "happy": 10, "sleepy": 30, "cold": 60,
                                  "calm": 60, "friendly": 60},
        "emotion_zero_at": 0.2, "emotion_max": 1.0, "stack_step": 0.2,
        "switch_guard": 0.4, "switch_window_min": 30,
        "tier_baseline": {"自家人": "happy", "死党": "happy", "朋友": "friendly",
                          "熟人": "calm", "对头": "cold"},
        "negative": ["angry", "annoyed", "hurt", "cold"],
    },
    # 隐私边界：事实层三档可见性 + 出口硬挡 + 别名替换。
    "privacy": {
        "vis_levels": ["public", "quiet", "secret"],
        "quiet_phrase": "知道，不在这儿报",
        "secret_phrase": "这个不在群里聊",
        "hard_block": ["手机", "住址", "学号", "身份证", "账目"],
        "alias": {},                 # {"私下称呼": "xiaobei"} 之类，按部署环境改
        "growth_to_group": False,    # 生长层（self/）默认不进群，fail-closed
    },
}


def _merge(base: dict, override: dict) -> dict:
    """深合并，只覆盖已存在的键路径；未知键保留但会在 validate() 里提示。"""
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """点路径访问的配置对象。所有数值都可用 ``cfg.get("decay.tau0")`` 取到。"""

    def __init__(self, data: dict | None = None):
        self.data = _merge(DEFAULTS, data or {})

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        if not path:
            return cls()
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"配置文件不存在: {p}")
        return cls(json.loads(p.read_text(encoding="utf-8")))

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def num(self, dotted: str, default: float = 0.0) -> float:
        v = self.get(dotted, default)
        return float(v)

    def integer(self, dotted: str, default: int = 0) -> int:
        return int(self.num(dotted, default))

    def section(self, name: str) -> dict:
        v = self.get(name, {})
        return v if isinstance(v, dict) else {}

    def unknown_keys(self, data: dict | None = None, prefix: str = "") -> list[str]:
        """列出配置里 DEFAULTS 没有的键：防拼写错误，也防"藏参数"。

        参照必须走 DEFAULTS 原始树，不能用 self.data（那份已含未知键，会漏报）。
        """
        data = self.data if data is None else data
        ref: dict = DEFAULTS
        for part in (prefix.split(".") if prefix else []):
            ref = ref.get(part, {}) if isinstance(ref, dict) else {}
        extra = []
        for k, v in data.items():
            if k.startswith("_"):
                continue
            path = f"{prefix}.{k}" if prefix else k
            if not isinstance(ref, dict) or k not in ref:
                extra.append(path)
            elif isinstance(v, dict) and isinstance(ref.get(k), dict):
                extra.extend(self.unknown_keys(v, path))
        return extra

    def validate(self) -> list[str]:
        """返回问题列表；空列表表示配置可用。不做静默修正。"""
        problems: list[str] = []
        for key in ("tau0", "p", "lambda_s", "lambda_e", "recency_tau"):
            if self.num(f"decay.{key}") < 0:
                problems.append(f"decay.{key} 不能为负")
        if self.num("decay.p") <= 0:
            problems.append("decay.p 必须 > 0（幂律形状参数）")
        for group, keys in {
            "lifecycle": ("d_intact", "d_episodic", "d_fragment", "s_residue", "e_residue"),
            "heuristic": ("s_base", "d0_base", "e_base"),
        }.items():
            for key in keys:
                v = self.num(f"{group}.{key}")
                if not 0.0 <= v <= 1.0:
                    problems.append(f"{group}.{key} 应在 0~1，当前 {v}")
        if not (self.num("lifecycle.d_intact") > self.num("lifecycle.d_episodic")
                > self.num("lifecycle.d_fragment")):
            problems.append("生命周期阈值必须严格递减: intact > episodic > fragment")
        if self.num("budget.context_max_chars") <= 0:
            problems.append("budget.context_max_chars 必须 > 0")
        if self.num("storage.max_backups") < 1:
            problems.append("storage.max_backups 至少 1")
        if self.get("privacy.growth_to_group") is True:
            problems.append("privacy.growth_to_group=true 会破坏 fail-closed 边界，请显式确认")
        return problems

    def to_dict(self) -> dict:
        return copy.deepcopy(self.data)
