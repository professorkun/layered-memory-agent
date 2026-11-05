# 性能瓶颈与内存控制方案

> 这一节是硬性要求：不藏瓶颈、给出可量化的控制手段与升级路线。

## 1. 复杂度

| 操作 | 实现 | 复杂度 | 触发频率 | 备注 |
| --- | --- | --- | --- | --- |
| `build()` 重算 N | 全库两两比字符二元组 | **O(M² · L)** | 每天 1 次 + 每次写入后 | **头号瓶颈**，M=条目数、L=正文长度 |
| `build()` 解析 | 扫 `daily/*.md` + `live/*.md` | O(文件总行数) | 同上 | 纯文本，无数据库 |
| `recall()` | 只读派生索引，逐条算 `compute` + 线索匹配 | O(M · L) | 每轮对话 1 次 | 不重扫日记 |
| `render()` | 分桶 + 各桶排序 | O(M log M) | 每天 1 次 / 每轮（含想起） | 段数固定，排序规模小 |
| `build_edges()` | **先按 cue 倒排，只算有共享线索的对** | O(Σ|C|²) | 每周 / 候选生成时 | 生产实测 50 条 → 129 条边 |
| `gc()` | 遍历目录、备份轮转、日志截断 | O(文件数) | 每天 1 次 | 幂等，可重复跑 |

## 2. 生产实测数字（来自实际运行的那套系统，供量级参考）

| 指标 | 数值 | 出处 |
| --- | --- | --- |
| 全量重扫 + 重算（`forget.py --cue`） | ~0.7 s | 每轮跑会把回复拖慢，因此改走只读索引 |
| 只读派生索引的召回（62 条） | ~170 ms（含 Python 启动） | 索引是派生的，避免重扫 |
| 插件侧一轮召回 | ~0.37 s，按消息文本缓存、一轮只跑一次 | 相对模型调用可忽略 |
| 注入件体积 | 约 4100~4500 字符（预算 6000） | 层多之后先从 4500 降到 4102 |
| 融合件预算 | 8000 字符 | 群会话专用 |
| 意识层注入件 | ~900 字符（预算 1400），主记忆约 10 KB | 每轮变，慢变件与快变件分开 |
| 蒸馏模型调用 | 关掉思考后 **84 s → 10 s** | `temperature=0.2`, `max_tokens=1200`, `timeout=60` |
| 上下文压缩 | 20 万 token 阈值、保留比例 0.05（约 3.7 万 token 近期原文） | 阈值驱动，in-turn 压缩 |
| 压得太早的代价 | 刚过 12 万就连压两次，每轮多烧 12~24 s | 所以阈值不能随手调低 |
| 各类超时 | 召回 4 s / 情绪 3 s / 判官 10 s / render 120 s / 蒸馏 90 s | 外部子进程一律设超时 |

## 3. 内存 / 磁盘控制：五道闸

### 3.1 注入预算（最重要的一道）

| 预算 | 值 | 配置 |
| --- | --- | --- |
| 私聊/群聊注入件 | 6000 字符 | `budget.context_max_chars` |
| 融合件 | 8000 字符 | `budget.merged_max_chars` |
| 「想起」段 | 700 字符 | `cue.recall_max_chars` |
| 意识层 | 1400 字符 | `budget.conscious_max_chars` |
| 触景段 | 16 行 | `cue.cue_rows` |
| 各阶段条数 | rule 14 / intact 12 / episodic 12 / fragment 12 / residue 8 | `budget.section_limits` |
| 单行截断 | 60~150 字符 | `budget.clip` |

**裁剪顺序是设计，不是兜底**：`LayeredView.render()` 给每段一个优先级，
从最低的开始砍：`cues(40) → recall(30) → conscious(20) → 低档事件 → … → growth(0)`。
先砍触景（同一件事被不同线索重复列出，最冗余），而不是最后硬截——
硬截会把「沉淀下来的看法」从中间切一半。
只有全部砍完仍超预算才硬截，并在末尾标注「（记忆太长，后面截掉了。）」。
`render()` 返回 `dropped` 与 `used`，运维可以直接看被砍了哪几段。

### 3.2 派生件回收（不碰原始证据）

| 对象 | 上限 / 动作 | 配置 |
| --- | --- | --- |
| 备份 | 同 tag 保留 10 份，超出删最旧 | `storage.max_backups` |
| 好感度 history | 每人 30 条 | `storage.history_cap` |
| 自我模型 seen | >500 删最旧 100 条 | `storage.seen_cap` / `seen_trim` |
| 日志 | >200000 字节停写/截断 | `storage.log_max_bytes` |
| 临时情绪 | 强度 <0.2 归零 | `affect.emotion_zero_at` |
| 检索缓存 | 会话结束/GC 时清空 | `gc.collect(cache=...)` |
| `live/` | 并入 `daily/` 后删除 | `Store.merge_live()` |
| 规矩 | 常驻 60 天后交给长期层 | `lifecycle.rule_resident_days` |
| faded 条目 | **保留在索引，不渲染** | 架构边界，不是旋钮 |

> **原始证据永不删**：`raw/` 与 `daily/` 只读，索引删了能重建。
> 代价是磁盘单调增长，需要外部归档策略（见下）。

### 3.3 不把全部数据装进内存

`build()` 只把条目正文与派生量放进列表，`raw/` 从不整体读入；
`recall()` 逐条流式算 `compute()`，峰值内存约等于「一份索引 + 一份注入件」。
对 1 万条、每条 200 字的库，索引 JSON 约 3~5 MB，是可接受量级。

### 3.4 上下文压缩（模型侧，本仓库不实现压缩器）

蒸馏/注入件是「慢变件」，会话上下文是「快变件」。生产里用
**20 万 token 阈值 + 保留比例 0.05** 的 in-turn 压缩；旧版定时压缩因会话已从
store 释放、`sessions.flush()` 报 `not live in this store` 而弃用。
本仓库通过 `budget.facts_order / memory_order / recall_order / ltm_order`
给出对接位，压缩器由你的运行时提供。

### 3.5 写放大控制

- 原子写 `*.tmp → os.replace`，不会留下半个文件；
- 覆盖前备份，但备份有轮转上限；
- `daily/` 一天一个文件，`live/` 当天一个文件，不做全库重写；
- 索引只在 `build()` / `touch()` 时整体写一次。

## 4. 什么时候会撑不住，怎么升级

| 规模 / 症状 | 判断 | 升级动作 |
| --- | --- | --- |
| M > 1000，`build()` 变慢 | N 的 O(M²) 爆发 | 把 `scoring.noise_level` 换成按 cue 倒排的候选对（只比有共享线索的），或预算一个「最近 K 条」窗口 |
| 需要语义相似（同义不同词） | 二元组 Jaccard 失效 | 换 embedding + 向量库；只替换 `noise_level` / `cue_match` 两个函数，`decay` 与分层不用动 |
| 全库检索延迟高 | 逐条 Python 循环 | 把索引搬进 SQLite FTS5 / LanceDB；`retrieval.recall` 改成 SQL/向量查询，返回结构保持 `Hit` |
| 注入件段太多、模型只读前两段 | 11 段注意力稀释 | 减少段数、合并生长层成一句话；或把低优先级段改成按需检索（只给「想起」） |
| 原始日记磁盘增长 | 只增不删 | 按年归档 `daily/` 到冷存储；**索引与 raw 不要一起删**，保留可重建性 |
| 多进程同时写索引 | 本项目无跨进程锁 | 单写者（一个蒸馏/日记进程）+ 其他进程只读；或加文件锁（`ingest.lock_stale_seconds` 是示例值） |
| 标注靠启发式，分数挤在中间 | 参数再调也没用 | 接标注器（LLM 输出 S/E/D/P/V/W/R/I），见 `examples/adapter.py` |

## 5. 自检命令

```bash
python -m layered_memory --root ./bot-memory pressure       # 条目数、N 的两两比较对数、各项预算
python -m layered_memory --root ./bot-memory render ...     # 看 stderr 的 used/budget/dropped
python -m layered_memory --root ./bot-memory gc             # 回收前后目录体积对照
python -m layered_memory --root ./bot-memory stats          # 各目录文件数与字节数
```

`pressure()` 会直接告诉你需要比较多少对（`M*(M-1)/2`），以及当前预算是否偏小。
