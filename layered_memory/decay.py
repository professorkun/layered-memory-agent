"""事件层的衰减算法（L2 内部机制）。

这一层只回答「细节还剩多少、现在有多容易被想起来」，**不负责分层**。
分层与层间流动见 layers.py；遗忘曲线为什么是幂律见 docs/blog-post.md 的引用前作。

公式（全部参数来自 config.example.json，无隐藏常量）
----------------------------------------------------
    tau = tau0 * exp(k_s*S0 + k_e*E0 + k_c*C) / (1 + k_n*N)     忘得多慢
    D(t) = D0 * (1 + t/tau)^(-p)                                 细节（幂律）
    lambda(t) = p / (tau + t)                                     瞬时遗忘速率
    S(t) = S0 * e^(-lambda_s * t)                                 意义（极慢）
    E(t) = E0 * e^(-lambda_e * t)                                 情绪（慢）
    R = w_s*S + w_e*E + w_c*C + w_recency*e^(-t/recency_tau) - w_n*N
    Q = D / (1 + inference)                                        重建可信度

两条容易踩的坑（都是实测踩过的）：
1. tau 用**当初**的 S0/E0/C 算，不用当下的 S/E。否则情绪一淡 tau 跟着缩，
   衰减二次加速，五年后会掉过头。
2. 必须用幂律不能用指数：要求「D0=0.90、五年后 0.18」且「初期快后期慢」，
   纯指数做不到——半衰期短到能解释前几周，五年后必然归零，长尾不存在。
"""
from __future__ import annotations

import math
from datetime import date

from .config import Config
from .models import MemoryItem, Scored, Stage

DATE_LIKE_RE = __import__("re").compile(r"\d{1,2}-\d{1,2}|\d{3,4}-\d{3,4}")
RULE_WORDS = ("以后", "别再", "规矩", "偏好", "习惯", "不许", "必须", "约定")


def parse_date(value: str, today: date) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return today


def kind_of(section: str, text: str) -> str:
    """规则型 vs 事件型。规矩不靠细节活着，不该跟着时间淡掉。

    带具体日期的「记住的」条目降级成事件（考试过了就过了）；
    live/ 蒸馏出来的条目一律按事件算，免得一句随口「以后」永久挂在常驻段。
    """
    if "记住" in section:
        return "event" if DATE_LIKE_RE.search(text) else "rule"
    if any(w in text for w in RULE_WORDS) and not DATE_LIKE_RE.search(text):
        return "rule"
    return "event"


def lifecycle(d: float, s: float, e: float, kind: str, cfg: Config) -> Stage:
    """按 D 分档；D 掉光后由 S/E 决定是「只剩意义」还是「真的忘了」。"""
    if kind == "rule":
        return Stage.RULE
    if d >= cfg.num("lifecycle.d_intact"):
        return Stage.INTACT
    if d >= cfg.num("lifecycle.d_episodic"):
        return Stage.EPISODIC
    if d >= cfg.num("lifecycle.d_fragment"):
        return Stage.FRAGMENT
    if (s >= cfg.num("lifecycle.s_residue")
            or e >= cfg.num("lifecycle.e_residue")):
        return Stage.RESIDUE
    return Stage.FADED


def compute(item: MemoryItem, today: date, cfg: Config,
            n_other: float | None = None) -> Scored:
    """把一条记忆算成**今天**的一组数值。纯函数，不碰文件。

    n_other: 干扰度 N。不传就用 item.N（索引里存的那份）。
    R 里的 cue 项在检索时按命中度补上（见 retrieval.py），静态注入件里 cue=0。
    """
    born = parse_date(item.date, today)
    t = max(0, (today - born).days)

    s0, d0, e0, c = item.S, item.D0, item.E, item.C
    n = item.N if n_other is None else float(n_other)

    # 意义与情绪各自慢衰
    s = s0 * math.exp(-cfg.num("decay.lambda_s") * t)
    e = e0 * math.exp(-cfg.num("decay.lambda_e") * t)

    # tau 取「当初的分量」，指数形式才拉得开量级差（线性最多差两三倍）
    tau = (cfg.num("decay.tau0")
           * math.exp(cfg.num("decay.k_s") * s0
                      + cfg.num("decay.k_e") * e0
                      + cfg.num("decay.k_c") * c)
           / (1 + cfg.num("decay.k_n") * n))
    tau = max(cfg.num("decay.min_tau"), tau)

    p = cfg.num("decay.p")
    d = d0 * (1 + t / tau) ** (-p)
    if item.kind == "rule":
        d = 1.0                       # 规矩没有「细节」这回事

    lam = p / (tau + t)               # 瞬时速率：一开始最大，越往后越小
    recency = math.exp(-t / max(cfg.num("decay.recency_tau"), 1e-6))
    r = (cfg.num("retrievability.w_s") * s
         + cfg.num("retrievability.w_e") * e
         + cfg.num("retrievability.w_c") * c
         + cfg.num("retrievability.w_recency") * recency
         - cfg.num("retrievability.w_n") * n)
    r = max(0.0, min(1.0, r))

    q = d / (1.0 + max(0.0, item.inference))
    stage = lifecycle(d, s, e, item.kind, cfg)
    half_life = tau * (2 ** (1 / p) - 1)

    # 观点 P 不跟细节一起掉：肉掉了，骨头还在。只按 S 那条极慢的曲线判活。
    p_alive = bool(item.P) and s >= cfg.num("consolidation.p_alive_min_s")

    return Scored(item=item, days=t, tau=tau, lam=lam, half_life=half_life,
                  S=s, D=d, E=e, C=c, N=n, R=r, Q=q,
                  stage=stage, P_alive=p_alive)


def calibrate(cfg: Config, years: int = 5) -> dict:
    """参数校准的可复现检查（生产系统 2026-10-29 反解的两个案例）。

    案例 A「很重的事」：S0=0.95, E0=0.90, D0=0.90, C=0.10
        tau ≈ 441 天 -> 1 年 D≈0.49、5 年 D≈0.18 / S≈0.88 / E≈0.49
    案例 B「随口一句」：S0=0.35, E0=0.15, D0=0.85, C=0.10
        tau ≈ 85 天  -> 1 年 D≈0.16、5 年 D≈0.04（已经忘了）
    改参数前先跑一遍，看这两行还成不成立，见 docs/TUNING.md。
    """
    today = date(2026, 11, 5)
    born = today.replace(year=today.year - years)
    out = {}
    for name, s, e, d0 in (("heavy", 0.95, 0.90, 0.90),
                           ("light", 0.35, 0.15, 0.85)):
        it = MemoryItem.create("校准样例", day=born.isoformat(),
                               S=s, E=e, D0=d0, C=0.10)
        m = compute(it, today, cfg, n_other=0.0)
        m1 = compute(it, born.replace(year=born.year + 1), cfg, n_other=0.0)
        out[name] = {"D": round(m.D, 3), "S": round(m.S, 3),
                     "E": round(m.E, 3), "tau_days": round(m.tau, 1),
                     "half_life_days": round(m.half_life, 1),
                     "D_after_1y": round(m1.D, 3)}
    return out
