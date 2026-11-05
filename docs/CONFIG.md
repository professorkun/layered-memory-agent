# 配置项说明

**配置文件**：仓库根 `config.example.json`（完整）、`config/minimal.json`（最小）。
所有项都在代码 `layered_memory/config.py::DEFAULTS` 里有同名默认值；
`tests/test_all.py::test_example_config_matches_defaults` 会强制校验两份一致，
所以「代码里藏着另一个数」会被测试挡住。

- 加载：`Config.load("my-memory.json")`；缺项走默认值；未知键出现在
  `MemorySystem.stats()["unknown_config_keys"]`（防拼写错误）。
- 校验：`Config.validate()` 挡负数、概率越界、阈值倒置、`p<=0`、预算 <= 0。
- 访问：`cfg.get("decay.tau0")` 点路径；模块内一律用这个，不写死常量。

## decay — 事件层衰减（幂律）

| 键 | 默认 | 作用 | 调大 / 调小 |
| --- | --- | --- | --- |
| `tau0` | 41.0 | 基准时间尺度（天） | 调大 = 全体忘得慢 |
| `k_s` | 1.5 | 意义强度拉长 τ 的指数系数 | 调大 = 重的事与琐事差距更大 |
| `k_e` | 1.0 | 情绪残留拉长 τ | 调大 = 情绪浓的留更久 |
| `k_c` | 0.5 | 巩固程度拉长 τ | 调大 = 常回忆的更抗忘 |
| `k_n` | 1.5 | 干扰压短 τ | 调大 = 相似记忆互相串味更明显 |
| `p` | 1.0 | 幂律形状；`p=1` 时半衰期 = τ | >1 长尾更陡，<1 更平 |
| `lambda_s` | 0.00004 | S 的日衰减（五年约 -7%） | 调大 = 意义也会淡 |
| `lambda_e` | 0.00033 | E 的日衰减（五年约一半） | 调大 = 执念退得快 |
| `recency_tau` | 30.0 | 近因项时间常数（天） | 调大 = 近期加权持续更久 |
| `min_tau` | 0.05 | τ 下限，防除零 | 不建议改 |

## retrievability — 可检索性 R 的权重

`R = w_s·S + w_e·E + w_c·C + w_recency·e^(-t/recency_tau) − w_n·N`；
检索时再加 `w_cue × 线索命中度`（见 retrieval.py）。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `w_s` / `w_e` / `w_c` | 0.30 / 0.22 / 0.10 | 意义 / 情绪 / 巩固对「容易被想起」的贡献 |
| `w_cue` | 0.28 | 检索命中加权；调大 = 当前这句话更主导排序 |
| `w_recency` | 0.10 | 近因加权 |
| `w_n` | 0.15 | 干扰扣分；调大 = 相似记忆更难被单独想起 |

## lifecycle — 生命周期阈值

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `d_intact` | 0.72 | D ≥ 此值 → `intact`，可以说具体细节 |
| `d_episodic` | 0.42 | → `episodic`，只说大概 |
| `d_fragment` | 0.15 | → `fragment`，只提碎片 |
| `s_residue` | 0.45 | D 掉光后 S ≥ 此值 → `residue`（只剩意义） |
| `e_residue` | 0.30 | 或 E ≥ 此值 → `residue`（只剩情绪） |
| `rule_resident_days` | 60 | 规矩在注入件常驻天数，之后交给长期层 |

> 阈值必须严格递减 `d_intact > d_episodic > d_fragment`，否则 `validate()` 报错。

## consolidation — 巩固 / 重固化

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `c_base` | 0.10 | 未被回忆过的初始巩固度 |
| `c_per_recall` | 0.18 | 每回忆一次 +0.18 |
| `c_cap` | 0.90 | C 上限（`C = min(cap, base + per_recall × recalls)`） |
| `touch_d0_factor` | 1.04 | `--touch` 时 D0 ×1.04（重固化会**轻微改变**记忆） |
| `d0_cap` | 0.98 | D0 上限；永远到不了 1.0，避免「越回忆越清晰到失真」 |
| `p_alive_min_s` | 0.30 | 观点 P 还立着所需的最小 S |

## heuristic — 无标注时的兜底打分

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `s_base` / `s_remembered` / `s_imp2` / `s_imp1` | 0.35 / 0.70 / 0.62 / 0.50 | S 基础值 / 「记住的」段 / 命中 2 个重要词 / 1 个 |
| `s_concrete_bonus` / `s_cap` | 0.05 / 0.92 | 具体细节加分与上限 |
| `d0_base` / `d0_con3` / `d0_con2` / `d0_con1` | 0.55 / 0.85 / 0.75 / 0.65 | D0 按「具体信息」个数分档 |
| `d0_long_bonus` / `d0_cap` / `d0_rule_cap` | 0.08 / 0.95 / 0.70 | 长文本加分 / D0 上限 / 规矩的 D0 上限 |
| `long_text_chars` | 55 | 多长算「长文本」 |
| `e_base` / `e_per_word` / `e_cap` / `e_none` | 0.20 / 0.16 / 0.85 / 0.15 | 情绪词命中打分 |
| `default_inference` | 0.2 | 缺 `〔推断：x〕` 时的重建惩罚（压低 Q） |

## noise / cue / ingest — 干扰、线索、写入筛选

| 组.键 | 默认 | 说明 |
| --- | --- | --- |
| `noise.bigram_scale` / `noise.cap` | 1.6 / 0.9 | 二元组 Jaccard → N 的放大系数与上限 |
| `cue.manual_match` | 0.9 | 手写〔线索〕命中即给 0.9 |
| `cue.bigram_overlap_min` / `cue.bigram_match_cap` | 0.25 / 0.7 | 退化匹配的最低重叠率与得分上限 |
| `cue.default_min_cue` / `cue.default_k` | 0.5 / 3 | 召回阈值与默认 top-k |
| `cue.recall_max_chars` / `cue.recall_clip_chars` | 700 / 110 | 「想起」段预算与单行截断 |
| `cue.auto_cue_max` / `cue.cue_rows` | 3 / 16 | 自动抽线索条数 / 触景段行数 |
| `ingest.window_hours` / `ingest.overlap_seconds` / `ingest.max_window_hours` | 3 / 90 / 26 | 蒸馏窗口、游标回退、窗口上限 |
| `ingest.min_rows` / `ingest.max_rows` / `ingest.max_prompt_chars` | 4 / 120 / 6000 | 不足 4 条不蒸馏；最多 120 条、6000 字符 |
| `ingest.max_entries` | 3 | 一次蒸馏最多产出 3 条 |
| `ingest.quote_min_chars` / `ingest.entry_min_chars` / `ingest.entry_max_chars` | 6 / 8 / 160 | quote 最短、正文最短/最长（防模型编原话） |
| `ingest.cues_max` / `ingest.lock_stale_seconds` | 6 / 900 | 单条最多线索数 / 示例锁过期时间 |

## budget — 注入预算与分段硬上限

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `context_max_chars` | 6000 | 私聊/群聊注入件总预算 |
| `merged_max_chars` | 8000 | 融合件预算（群会话读的那份） |
| `cue_rows` | 16 | 触景段行数；**超预算时第一个被砍** |
| `clip.*` | rule 95 / intact 150 / episodic 110 / fragment 60 / residue 70 / digest 80 | 各阶段单行字符上限 |
| `section_limits.*` | rule 14 / intact 12 / episodic 12 / fragment 12 / residue 8 | 各阶段最多渲染几条 |
| `conscious_max_chars` | 1400 | 意识层注入预算 |
| `facts_order` / `memory_order` / `recall_order` / `ltm_order` | 89 / 90 / 91 / 50 | 对接现有 prompt-inject 框架时的段顺序（先事实、再记忆、再想起） |

## storage — 回收上限

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `max_backups` | 10 | 同 tag 备份保留份数 |
| `history_cap` | 30 | 每人好感度历史条数 |
| `seen_cap` / `seen_trim` | 500 / 100 | 自我模型 seen 表上限与超限删除条数 |
| `log_max_bytes` | 200000 | 单个日志超过就停写/截断 |
| `recall_timeout_ms` / `recall_max_buffer` | 4000 / 1048576 | 对接外部子进程时的超时与缓冲区上限 |

## growth — 生长层（分化 / 沉淀 / 联结）

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `feel_e_min` / `feel_residue_days` | 0.30 / 7 | residue 且 E≥0.30、待够 7 天才出感觉候选 |
| `sediment_min_items` / `sediment_min_s` / `sediment_span_days` | 5 / 3.0 / 14 | 沉淀门槛：≥5 条、ΣS≥3、跨度≥14 天 |
| `sediment_drop` / `sediment_floor` | 0.15 / 0.40 | 出现强反例置信 -0.15；低于 0.40 标注「我可能想错了」 |
| `w_cue` / `w_cue_max` | 0.30 / 0.60 | 每个共享线索的边权与封顶 |
| `w_entity` / `w_near` / `w_mood` / `w_manual` | 0.40 / 0.10 / 0.20 / 0.80 | 同实体 / 同日 / 情绪同极性 / 手写〔连：id〕 |
| `edge_tau` / `edge_p` / `edge_reinforce` | 90.0 / 0.5 / 0.10 | 边权 `(1+t/τ)^-p` 衰减与共同回忆加强 |
| `base_feel_min` / `base_feel_max` | 5 / 9 | L0 底感条数范围 |
| `curiosity_per_day` | 1 | 每天最多新增好奇条数（模型天生爱表演好奇） |
| `state_fresh_hours` / `state_drop_days` | 36 / 7 | 超 36h 写「接着上次（N 天前）」，超 7 天不置顶 |

## conscious — 意识层

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `theta` / `cap` | 0.34 / 4 | 激活阈值与每轮最多进意识的条数 |
| `suppress` | 0.78 | 同线索组侧抑制：非最强者 A×0.78 |
| `strong_mood_threshold` / `strong_arousal` / `strong_theta` / `strong_cap` | 0.6 / 0.70 / 0.40 / 2 | 情绪强时收紧：阈值升高、上限减半 |
| `absent_factor` / `misses_bonus` / `misses_cap` / `misses_min_m` | 0.55 / 0.02 / 0.06 / 0.35 | 当事人不在场折减、连续未命中的补偿 |
| `attention_*` | 1.10 / 0.55 / 0.10 / 0.55 / 1.25 | 注意力 = base − load×负载 + arousal×唤醒，夹在 min~max |
| `w_m` / `w_p` / `w_emo` / `w_t` | 0.45 / 0.16 / 0.16 / 0.23 | 激活分 A 的四项指数权重 |
| `lambda_e` | 0.00038 | **意识层独立**的情绪衰减（与事件层 0.00033 不同，故意保留） |
| `temperament.*` | 7 个系数 | 先天气质，**只读**；改它等于换一个机器人 |
| `deep_traits` | 7 个名字 | 深层模式系数表，只写、由事件累积 |

## affect — 好感度 / 情绪

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `default_score` / `creator_score` | 50 / 95 | 新人初始分 / 特殊「钉死」分 |
| `delta_cap` / `delta_cap_max` | 2 / 3 | 单次常规/重事件记分上限 |
| `daily_net_cap` / `daily_event_cap` | 8 / 6 | 每人每天净变化与记分次数上限 |
| `cooldown_minutes` | 10 | 同人冷却，防盯着一个人刷 |
| `saturate_80` / `saturate_95` | 0.3 / 0.1 | ≥80 正增益 ×0.3，≥95 ×0.1 |
| `decay_after_days` / `decay_per_day` / `decay_floor` | 7 / 1 / 35 | 7 天无互动后每天 -1，下限 35 |
| `hysteresis` | 3 | 降档迟滞：分数没跌穿「旧档下界 -3」就不降档 |
| `mood_default` / `mood_drift_per_tick` / `mood_drift_tick_min` | 50 / 1 / 10 | 群氛围默认、每 tick 回正点数、tick 间隔（分钟） |
| `mood_low` / `mood_high` | 35 / 70 | 低于 35 降一档，高于 70 只升半档 |
| `tiers` | 90/75/55/25/0 | 自家人 / 死党 / 朋友 / 熟人 / 对头下界 |
| `events` | talk+2 … insult-3 | 事件记分表（传事件名，不传裸 delta） |
| `emotion_half_life_min` | angry 10 … friendly 60 | 各情绪半衰期（分钟） |
| `emotion_zero_at` / `emotion_max` / `stack_step` | 0.2 / 1.0 / 0.2 | 低于 0.2 归零、强度上限、同情绪叠加步长 |
| `switch_guard` / `switch_window_min` | 0.4 / 30 | 一段对话最多换一次情绪 |
| `tier_baseline` | 档位→情绪 | 好感档决定基线情绪 |
| `negative` | 4 个 | 同强度时优先的负面情绪 |

## privacy — 出口边界

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `vis_levels` | public/quiet/secret | 事实层三档：直说 / 只说知道 / 群里连知道都不表态 |
| `quiet_phrase` / `secret_phrase` | 「知道，不在这儿报」/「这个不在群里聊」 | 群聊拒答口径 |
| `hard_block` | 手机/住址/学号/身份证/账目 | 命中即整行替换为〔不出口〕，**不参与判断** |
| `alias` | `{}` | 群版称呼替换，如 `{"私下称呼": "xiaobei"}`；按部署环境填 |
| `growth_to_group` | `false` | **fail-closed**：生长层不进群；置 true 会被 `validate()` 拒绝 |

## 配置里没有的东西

以下刻意**不做成配置项**，因为它们是架构边界而不是旋钮：

- 原始证据只读、索引可重建、生长层引擎只读不写；
- 「先给主体，再给记忆」的段顺序；
- faded 条目只留索引不渲染（不物理删除）；
- 群版不读 `self/`（由 `privacy.growth_to_group` 守死）。
