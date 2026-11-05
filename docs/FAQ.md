# 常见问题排查

## A. 装不上 / 跑不起来

**Q：`ModuleNotFoundError: No module named 'layered_memory'`**
把仓库根加进 `PYTHONPATH`，或在仓库根运行：

```bash
PYTHONPATH=. python -m layered_memory --help
pip install -e .        # 或装成包，之后直接用 layered-memory 命令
```

**Q：`SyntaxError` / 类型标注报错**
Python 版本低于 3.10。本项目用了 `X | Y` 与 `dataclasses`；请升级到 3.10+。

**Q：`ValueError: 配置校验失败: ...`**
`Config.validate()` 拒绝了非法配置（负数、`p<=0`、生命周期阈值倒置、预算 ≤0、
`growth_to_group=true`）。它**不会静默修正**——请按提示改 `my-memory.json`，
不要绕过校验。

**Q：`stats()["unknown_config_keys"]` 里有东西**
配置里写了代码不认识的键，多半是拼写错误（`tau0_typo` 之类）。
未知键不会生效，删掉或改正；完整键清单见 `docs/CONFIG.md`。

## B. 记忆不生效 / 分数不对

**Q：`add` 返回 `{"ok": false, "reason": "正文太短"}`**
正文 <8 字被筛选掉。写完整一句，或调 `ingest.entry_min_chars`（不建议低于 8）。
同理 `quote 不在原文里` 说明你传的 `--quote` 与 `--raw-text` 对不上，
这是**防模型编原话**的校验，别删掉它。

**Q：`build` 之后一条都没有**
检查：① 文件名必须是 `YYYY-MM-DD.md`（日期取自文件名）；
② 条目必须以 `- ` 开头；③ 文件在 `daily/` 或 `live/`，不是 `raw/`；
④ 前面有没有被 `validate_entry` 挡掉。

```bash
python -m layered_memory --root ./bot-memory stats
```

**Q：规矩（「以后别再…」）还是会衰减**
规矩判定看**段落名**和有没有具体日期：只有 `## 记住的` 段里不带日期的条目才算
`rule`；`live/` 条目一律按事件算。用 `--section "记住的" --folder daily` 写，
或直接在日记文件里放到 `## 记住的` 下。带具体日期（如 11-03 考试）会被降级成事件——这是设计。

**Q：S/E/D 全挤在 0.35~0.62，重事轻事分不开**
启发式判不出「这件事有多重要」。接一个 LLM 标注器（见 `examples/adapter.py`）
或写入时手工传 `--S --E --D`。调 `k_s` 只是放大已有差距，造不出差距。

**Q：`calibrate` 的两个案例对不上**
你改过 `decay.*`。恢复 `config.example.json` 的默认值再跑，确认基准：
重事 5 年 D≈0.18 / S≈0.88，琐事 D≈0.04。详见 `docs/TUNING.md`。

**Q：`--touch` 之后原始日记被改了吗？**
没有。`touch()` 只改派生索引里的 `C / D0 / recalls / last_recall`。
注意生产实现头部注释曾写「D0 不变」，实际行为是 `D0×1.04`（上限 0.98）；
本仓库以代码行为为准。

## C. 检索与注入

**Q：问一句什么都召不回**
`min_cue=0.5` 以下直接不返回（不硬凑）。先给条目写〔线索：…〕，
或临时 `recall --min-cue 0.3`。含糊的问法本来就该回「没印象」，
别把空结果当成「没发生过」。

**Q：召回太吵，无关旧事老被提起**
调高 `cue.default_min_cue`（0.5→0.6）、调小 `cue.default_k`（3→2）、
或降 `retrievability.w_cue`。检查 `audit` 里 R 的分布。

**Q：注入件里「沉淀下来的看法」被从中间切一半**
说明走到了最后硬截。看 `render` 返回的 `dropped`：正常应先砍 `cues`。
若 `dropped` 包含 `growth` 或 `facts`，是 `budget.context_max_chars` 太小，
调大预算或减少段内条数 `budget.section_limits.*`，**不要**改成硬截。

**Q：模型把「想起」段说成「你刚才说」**
「想起」段标题已写明「不是刚发生的」，每行还带确定度。若模型仍犯错，
在你的 system prompt 里补一句：「`## 想起` 是旧事，不是刚发生的；按括号里的确定度说」。

**Q：群聊里出现了不该出现的私事**
按三道查：① `facts-group.md` 里有没有 quiet/secret 的值（值不过界）；
② `privacy.hard_block` 词表是否覆盖你的敏感字段；
③ 群版是不是误读了 `self/`（默认 fail-closed，`render(channel="group")` 不含 growth）。

```bash
python -m layered_memory --root ./bot-memory render --channel group
```

另外 `LayeredView.audit_outbound(reply)` 能查「登记/报一下/资料库/权限」这类
把系统端到人前的话术。

**Q：L1/L2/L3 和 L0~L3 到底哪个是哪个**
两套编号不通用。`time_layers()` 的 L1/L2/L3 是**按时间远近**（近 3 天 / 4~30 天 / 更早），
`Layer` 枚举里的 GROWTH_BASE/FEEL/SEDIMENT 与事件层是**按抽象程度**。
本仓库代码里一律用语义名，只有旧视图保留数字编号并注明。

## D. 生长层 / 情绪

**Q：`deposit()` 跑完，`self/` 还是空的**
这是**故意的**：引擎只出候选到 `index/deposit-candidates.json`，
必须由人审后调用 `approve_candidates()` 才写 `self/`。
引擎自己写深层文件 = 自己编记忆。

**Q：沉淀候选一条都不出**
门槛是同主题 ≥5 条、ΣS ≥3.0、跨度 ≥14 天。检查 `audit` 与 `cues` 是否聚到同一主题；
样本太少时先攒数据，别把门槛调到 1 条——那样长出来的判断会非常不稳。

**Q：好感度记分总是 `rejected: 冷却中`**
同人 10 分钟冷却是防刷设计。日结用 `adjust`/`delta`（`bypass_cooldown=True`），
回合内用事件名。检查 `affect.cooldown_minutes` 是否被改过。

**Q：情绪一段对话变好几次**
`switch_guard=0.4` + 30 分钟窗口限制换情绪；`annoyed` 半衰期只有 3 分钟。
调 `affect.switch_window_min` 45、或把相关情绪半衰期拉长。

**Q：群氛围设不低**
`mood()` 单次最多 ±3，之后每 10 分钟向 50 回正 1 点。要一次设到很低，
在测试里直接改 `aff.state["group"]["score"]`；生产里不要绕过回正机制。

## E. 性能 / 运维

**Q：`build()` 越来越慢**
N 是全库两两比二元组，O(M²·L)。跑 `pressure` 看 `noise_pairs`。
M>1000 时按 cue 倒排只比候选对，或换向量索引（见 `docs/PERFORMANCE.md`）。

**Q：磁盘一直涨**
原始证据只读永不删，这是设计（可回溯）。`gc()` 只回收派生件：
live 合并、备份轮转、history/seen/log 上限。要控总量得按年归档 `daily/`，
**别删索引后不重建**。

**Q：两个进程同时写，条目丢了**
本项目用原子写但不提供跨进程锁。改成单写者（一个蒸馏/日记进程），
其他进程只读 `out/` 注入件。

**Q：改了 `facts.json`，两个频道还吃旧投影**
事实层是「单一事实源 → 两版投影」。改完必须重新 `render`（或
`project_facts("private")` / `project_facts("group")`），否则投影文件还是旧的。

**Q：升级后不确定有没有回归**
按顺序：`python -m unittest discover -s tests -v` → `python examples/quickstart.py`
→ `python -m layered_memory --root <你的目录> self-test` → `pressure`。
详见 `docs/TESTING.md`。
