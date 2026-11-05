# layered-memory：可单独接进自己机器人的分层记忆系统

> **主题**：记忆为什么要分浅层/深层、层之间怎么流动、每层谁有权写入。
> **不是**讲遗忘曲线怎么推导——那是我另一篇文章的内容，见下方「与既有公开内容的关系」。

一套从实际跑过的聊天机器人里抽出来、脱敏重写、可独立运行的参考实现：
上下文 / 事实层 / 事件层 / 生长层（底感-感觉-沉淀）/ 意识层 / 知识库，
配完整的衰减打分、筛选、存储、检索、巩固、淘汰、内存回收，以及好感度与情绪的对接规范。

**零第三方依赖**（Python 3.10+ 标准库）、**全部参数公开**在 `config.example.json`、
**29 项测试** + 三个端到端示例 + 多版本 CI 示例配置（`docs/ci-workflow-example.yml`）。

---

## 与既有公开内容的关系（重要）

- 同作者此前在 CSDN 发过《为什么 AI 的记忆曲线必须是幂律：一套按分量衰减的记忆系统》
  （2026-10-29，文章 ID `169454033`），主题是**遗忘曲线**，只覆盖本项目的**事件层内部机制**。
- 本次发布的是**另一个主题**：分层架构与层间治理。旧文不改写、不重复展开；
  本仓库在 `decay.py` 里给出那条曲线的完整实现，并把它当作其中一层来用。
- 发布前核查过 GitHub 上的既有仓库（`proactive-messaging-agent` 是主动消息、
  `qq-group-chat-gate` 是群聊门闸、`csdn-blog-publishing-skill` 是发布工具），
  **没有同主题仓库**，因此本仓库为新建，不是重复开坑。两篇内容是按序阅读的关系。

---

## 30 秒上手

```bash
git clone https://github.com/professorkun/layered-memory-agent.git
cd layered-memory-agent
python -m unittest discover -s tests -v          # 29 passed
python examples/quickstart.py                    # 端到端 10 步
```

```python
from layered_memory import MemorySystem

mem = MemorySystem("./bot-memory")
mem.init()
mem.ingest("他半夜说别送了，我觉得他其实在硬撑",
           tags={"S": 0.95, "E": 0.90, "D": 0.90},
           cues=["别送了"], P="他其实在硬撑")
mem.build()
print(mem.render(channel="private", query="别送了")["text"])
print(mem.recall("别送了")["block"])
```

接自己的 LLM / IM 框架：直接看 `examples/adapter.py`（`BotAdapter`），
只依赖 5 个方法，标注器可以换成你自己的模型。

## 分层架构

```
┌─ 上下文 ──────────── 本轮这几句，不落盘；冲突时「刚发生」以上下文为准
├─ 事实层 facts/ ───── 身份与稳定字段：public / quiet / secret 三档，值不过界
├─ 事件层 live/→daily/ 发生过什么；浅层增量 → 每日归档 → 派生索引（可重建）
│    └─ 六变量 + 幂律：S 意义 / D 细节 / E 情绪 / C 巩固 / N 干扰 / R 可检索
│       τ = τ0·exp(k_s·S0+k_e·E0+k_c·C)/(1+k_n·N)
│       D(t) = D0·(1+t/τ)^(-p)      λ(t) = p/(τ+t)
│       生命周期 rule → intact → episodic → fragment → residue → faded
├─ 生长层 self/ ────── L0 底感（不衰减）← L1 感觉 ← residue；L3 沉淀（置信可升可降）
│    └─ 权限：引擎只读不写，只出候选；人审后 approve_candidates() 才落盘
├─ 意识层 conscious/ ─ 气质只读 / 深层模式只写（反复发生的事**改系数**而非存条目）
│    └─ 激活竞争：过了阈值才进意识，其余后台攒分
└─ 知识库 ──────────── 确认过的事实，谁都能查；**库里写着 ≠ 我记得**

注入顺序：我是谁 → 接着上次 → 感觉 → 看法 → 好奇 → 确认过的事实 → 事件 → 想起 → 触景
出口边界：群版换称呼 + 硬挡 PII；self/ 永不进群（fail-closed）
```

还有一套**按保鲜度**的读取视图（`time_layers()`）：L1 近 3 天原文 / L2 4~30 天要点 /
L3 更早一句话。它和「L0~L3 抽象分层」编号撞名但含义不同——代码里一律用语义名，
只有旧视图保留数字并注明，避免接错。

## 全链路（每层对应一个模块）

| 环节 | 模块 | 要点 |
| --- | --- | --- |
| 配置 | `config.py` + `config.example.json` | **全部参数公开**；示例配置与代码默认值由测试强制校验一致 |
| 变量/公式 | `models.py` / `decay.py` | 六变量、幂律、R/Q、六种生命周期、确定度口径 |
| 打分/筛选 | `scoring.py` / `pipeline.validate_entry` | 启发式兜底、二元组干扰、quote 必须出自原文（防编造） |
| 存储 | `store.py` | 分层目录、原子写、去重、live→daily 合并、事实层两版投影 |
| 检索 | `retrieval.py` | 只读派生索引、线索命中 + R 排序、700 字符「想起」段 |
| 巩固 | `consolidation.py` | `C=min(.9,.1+.18×recalls)`；touch 重固化但原始日记不动 |
| 生长 | `consolidation.py` / `conscious.py` | 分化 / 沉淀 / 联结候选；激活竞争；深层模式改系数 |
| 淘汰/回收 | `layers.py` / `gc.py` | 分段预算裁剪、faded 只留索引、备份/history/日志/缓存上限 |
| 对接 | `affect.py` | 好感度内账（防刷/饱和/迟滞）+ 情绪半衰与基线 + V/W/R/I 信号 |
| 主 API | `pipeline.py` / `cli.py` | `MemorySystem` 一个类 + `python -m layered_memory` |

## 配置与示例文件

| 文件 | 用途 |
| --- | --- |
| `config.example.json` | **完整配置**：约 100 项、全部默认值与生产校准值，无隐藏参数 |
| `config/minimal.json` | 只改三项的最小示例 |
| `examples/sample_daily.md` | 带 `[S E D V W R I P]` 标注和〔线索〕的示例日记（虚构、脱敏） |
| `examples/quickstart.py` | 端到端 10 步 |
| `examples/adapter.py` | 任意机器人接入模板（含规则版标注器） |
| `examples/affect_demo.py` | 好感度/情绪/记忆标注对接演示 |

```bash
python -m layered_memory --root ./bot-memory init
python -m layered_memory --root ./bot-memory add "……" --S 0.9 --E 0.7 --D 0.9 --cues "……"
python -m layered_memory --root ./bot-memory build
python -m layered_memory --root ./bot-memory render --channel private
python -m layered_memory --root ./bot-memory calibrate     # 改参数前先跑
python -m layered_memory --root ./bot-memory self-test     # 部署后 8 项自检
python -m layered_memory --root ./bot-memory pressure      # 容量与复杂度报告
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [INSTALL.md](docs/INSTALL.md) | 环境依赖、三种安装方式、初始化、定时任务、迁移回滚、验证清单 |
| [CONFIG.md](docs/CONFIG.md) | **逐项**配置项说明（默认值 / 作用 / 调优方向） |
| [TUNING.md](docs/TUNING.md) | 参数调优指引：先校准、症状→改哪里、参数耦合、何时停止调参 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 设计原理、变量定义、公式、全链路、取舍与局限 |
| [INTEGRATION.md](docs/INTEGRATION.md) | 好感度 / 情绪对接规范、共享数值、注入话术 |
| [PERFORMANCE.md](docs/PERFORMANCE.md) | **性能瓶颈与内存控制方案**、实测数字、升级路线 |
| [TESTING.md](docs/TESTING.md) | 测试示例、可复现流程、CI、故障注入建议 |
| [FAQ.md](docs/FAQ.md) | 20+ 条常见问题排查 |
| [blog-post.md](docs/blog-post.md) | **博客文稿版本**（与 README 分开的公开发行正文） |

## 测试

```bash
python -m unittest discover -s tests -v     # 29 项，约 0.6 秒
python examples/quickstart.py               # 末尾 8/8 内置自测
```

CI 示例在 Python 3.10 / 3.11 / 3.12 / 3.13 上跑全量测试 + 三个示例 + 内置自测。
把 `docs/ci-workflow-example.yml` 复制到 `.github/workflows/tests.yml` 即启用
（通过 API 创建 workflow 文件需要 `workflow` 权限，第一次建议在网页端添加）。

## 性能瓶颈与内存控制（摘要，完整版见 PERFORMANCE.md）

- **头号瓶颈**：干扰度 N 是全库两两比字符二元组，**O(M²·L)**。M>1000 必须换倒排或向量索引。
  生产实测 50 条 → 129 条边；全量重扫 ~0.7 s，改走只读派生索引后 ~170 ms，
  插件侧一轮 ~0.37 s（按消息文本缓存，一轮只跑一次）。
- **注入预算**：6000 字符（融合件 8000 / 想起段 700 / 意识层 1400）。
  **分段裁剪而不是最后硬截**：按优先级先砍触景、想起、意识层，保住生长层与沉淀；
  `render()` 返回 `dropped` 供运维核查。
- **回收**：备份轮转 10 份、好感度 history 每人 30 条、seen 上限 500、
  日志 200 KB 停写、临时情绪 <0.2 归零、会话结束清检索缓存、live 合并后删除。
- **不删原始证据**：`raw/` 与 `daily/` 只读，faded 条目只留索引不渲染；
  代价是磁盘单调增长，需外部归档策略。
- **模型侧压缩**：阈值驱动（20 万 token / 保留 0.05），旧版定时压缩已弃用。

## 扩展方向

1. **自定义遗忘系数**：在 `decay.*` 里改 `tau0/k_s/k_e/k_c/k_n/p`，
   或给 `MemoryItem` 加 `category` 字段，在 `compute()` 里按类别查系数表
   （工作记忆忘得快、约定忘得慢）。改完先跑 `calibrate`。
2. **接入向量库**：只替换 `scoring.noise_level()` 与 `scoring.cue_match()`
   两个函数（embedding + FAISS/LanceDB/SQLite），`decay`、`layers`、`Store` 全部不用动；
   顺带解决「同义不同词算不出干扰」的已知局限。>1000 条时把 N 改成按 cue 倒排的候选对。
3. **记忆可视化接口**：`audit()` 已输出每条的 `S/D/E/C/N/R/Q/stage/tau/half_life`，
   `pressure()` 输出容量与复杂度，`index/link-index.json` 是带权边表，
   `deposit-candidates.json` 是生长轨迹。接一个 `/api/graph` 就能画
   「事件 → 感觉 → 沉淀」的演化图与遗忘曲线；建议只读、不要暴露 `raw/` 原文。
4. 其他容易加的：多用户分库（一个 root/人）、按会话动态预算、
   LLM 标注器（替换 `examples/adapter.py` 的规则版）、周度人审队列、
   `vis` 分级与审计日志、把 `Store` 换成 SQLite 后端（保持 `MemorySystem` 接口不变）。

## 局限（说在前面）

- 启发式标注判不出「这件事对他有多重要」，S 会集中在 0.35~0.62；
  接标注器比调参有效得多。
- 二元组 Jaccard 不是语义相似度：同义不同词算不出干扰、连不上边。
- 层多会稀释注意力：11 段注入件，模型很可能只读前几段，所以每段都有硬预算。
- 沉淀最容易长歪，置信度必须能降、周任务必须审；好奇最容易编，宁可空着。
- 生长层冷启动时是空的——底感得从真实对话里长，不该替它编。
- 引擎只读 `self/`，牺牲了自动化，换来「不会自己编记忆」。
- 纯文件存储：无事务、无跨进程锁，多写者场景请改单写者或加文件锁。

## 脱敏与许可

所有示例均为虚构数据；不含真实昵称、内部路径、聊天原文、凭据。
私有实现里的绝对路径、人名、群组信息一概未进入本仓库。
代码与文档采用 [MIT](LICENSE)。

> 博客正文见 [docs/blog-post.md](docs/blog-post.md)——与本页面是**两份不同的文稿**：
> 博客讲设计思路、原理、取舍与局限；README 讲安装、配置、运行与扩展。
