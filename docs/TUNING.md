# 参数调优指引

## 0. 铁律：先校准，再动参数

任何调参前后都跑这两条，把输出记下来：

```bash
python -m layered_memory --root ./bot-memory calibrate
python -m layered_memory --root ./bot-memory audit | head -60
```

`calibrate` 复现两个原始案例，默认配置下应当接近：

| 案例 | 1 年后 D | 5 年后 D | 5 年后 S | 5 年后 E | τ |
| --- | --- | --- | --- | --- | --- |
| 很重的事（S0=.95 E0=.90 D0=.90） | 0.49 | **0.18** | 0.88 | 0.49 | 441 天 |
| 随口一句（S0=.35 E0=.15 D0=.85） | 0.16 | **0.04** | 0.33 | 0.08 | 85 天 |

如果这两个数对不上，先别改别的——说明你改过的参数已经破坏了「重事长尾、琐事归零」这个基本形状。

改完配置再跑一次 `python -m unittest discover -s tests -v`：测试会检查示例配置与代码默认值一致，
以及校准、生命周期、隐私投影、预算裁剪等 29 项不回归。

## 1. 调参顺序（一次只动一组）

1. **先修标注，再修公式。** S/E/D 是写入时由标注器给的。启发式只能判「篇幅、有几个具体信息」，
   判不出「这件事对他到底多重要」。把 `examples/adapter.py` 里的 `rule_annotator` 换成你自己的
   LLM 标注器，收益通常远大于调 `k_s`。
2. 动 `decay.*` 前先跑 `calibrate`；动 `retrievability.*` / `cue.*` 前先跑 `recall` 看排序。
3. 动 `budget.*` 前先看 `render` 返回的 `dropped`，确认被砍的是冗余段而不是主体段。
4. 动 `affect.*` 前先想清楚它影响的是**语气**，不是记忆内容；两者不要耦合。

## 2. 常见症状 → 改哪里

| 症状 | 优先检查 | 建议 |
| --- | --- | --- |
| 重要的事忘太快 | 标注 S/E 是否偏低 | 先修标注；再 `k_s` 1.5→1.8 或 `tau0` 41→55（会同时拖慢琐事） |
| 琐事赖着不走 | `tau0` / `k_s` | 调小 `tau0`（41→32）或调大 `k_n`；别动 `p`，动 p 会连带长尾 |
| 「五年前的事」说得像昨天 | `d_intact` / `lambda_s` | 调高 `d_intact` 0.72→0.78；确认 `D` 真的掉下来了（`audit`） |
| 注入件太长、模型只读前几段 | `budget.context_max_chars` / `cue_rows` | 先降 `cue_rows` 16→8；`render` 的 `dropped` 应只有 `cues`/`recall` |
| 主体段被截掉一半 | 裁剪顺序 | 不要靠最后硬截；`layers.py` 已按优先级砍段。若 `dropped` 里出现 `growth`，说明预算给得太小 |
| 召回太吵、无关旧事老被提起 | `cue.default_min_cue` / `cue.default_k` / `retrievability.w_cue` | 阈值 0.5→0.6、k 3→2；或调小 `w_cue` 0.28→0.20 |
| 问得含糊就一条都勾不着 | 线索质量 | 手写〔线索〕比自动抽有效；`cue.auto_cue_max` 3→5 只是权宜之计 |
| 相似的两件事串味 | `noise.*` / `k_n` | 调小 `noise.bigram_scale` 1.6→1.0；根本解法是换语义相似度（见 README 扩展方向） |
| 沉淀越来越固执、错判不改 | `growth.sediment_drop` / `sediment_floor` | 调大 drop 0.15→0.25 或抬高 floor；周任务必须人工审候选 |
| 感觉/好奇栏全是编的 | 生长层权限 | 候选必须由人审后 `approve_candidates()` 才落盘；`curiosity_per_day=1` 别放宽 |
| 好感度被几句话刷满 | `affect.delta_cap` / `daily_net_cap` / `cooldown_minutes` | 保持 2 / 8 / 10；`saturate_80/95` 不要关 |
| 档位在门槛附近来回跳 | `affect.hysteresis` | 3→5；注意太大会让降档迟钝 |
| 情绪一段对话里变三次 | `affect.switch_guard` / `switch_window_min` | 0.4→0.5、30→45；或调长 `emotion_half_life_min` |
| 群聊里漏了私事 | `privacy.*` | 先跑 `facts` 两版投影自检；`hard_block` 词表补全；**别**把 `growth_to_group` 打开 |

## 3. 几组参数的耦合关系（改之前先看）

- `tau0` 是**整体**尺度，`k_s/k_e/k_c` 是**区分度**。想让「重事与琐事差距更大」要调 `k_s`，
  不是把 `tau0` 拉大——后者会让所有事都变慢。
- `p` 控制长尾形状。默认 `p=1` 时半衰期正好等于 τ，方便口算；改成 1.2 会让前几周掉得更快、
  五年后更低，两个校准案例会同时失守。
- `w_cue` 与 `cue.default_min_cue` 是一对：前者决定「命中后加多少分」，后者决定「什么算命中」。
  只调其中一个容易出现排序变了但仍召不回/仍太吵。
- `budget.context_max_chars` 与 `section_limits.*` 是两层预算：总预算先砍整段，段内上限控制单段条数。
  总预算调小但段内上限不动，等于让裁剪算法替你决定砍哪一段。
- `growth.edge_tau=90` 天是**比事件慢**的边权衰减。不要照抄 `decay.tau0=41`，否则联结会跟事件一起消失。

## 4. 自定义遗忘系数（最常被问的扩展）

```json
{
  "decay": { "tau0": 60.0, "k_s": 1.2, "k_e": 0.8, "p": 1.0 }
}
```

```bash
python -m layered_memory --config my.json --root ./bot-memory calibrate
python -m layered_memory --config my.json --root ./bot-memory render | head -30
python -m layered_memory --config my.json --root ./bot-memory self-test
```

想让**不同类别**用不同系数（例如工作记忆比闲聊忘得快），不要复制一份 `decay.py`：
在 `MemoryItem` 上扩展一个 `category` 字段，在 `decay.compute()` 里按类别查一张
`{"category": {"k_s": ..., "tau0": ...}}` 表即可。配置结构见 README「扩展方向」。

## 5. 什么时候该停止调参

出现下面任一情况，说明瓶颈不在参数，而在架构：

- 全库条目 > 1000，`build()` 明显变慢（N 是 O(M²)）→ 换倒排 / 向量索引；
- 你需要「同义不同词」的干扰判断 → 二元组 Jaccard 做不到，换语义向量；
- 标注全靠启发式，S 永远在 0.35~0.62 之间 → 接标注器；
- 注入件再怎么砍，模型还是只读前两段 → 减少段数，或把生长层压成一句话。

具体方案见 `docs/PERFORMANCE.md`。
