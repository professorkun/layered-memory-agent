# 环境依赖与安装

## 1. 环境依赖

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | **3.10+**（3.11 / 3.13 已实测） | 用到 `dataclasses`、`X \| Y` 类型标注、`from __future__ import annotations` |
| 第三方包 | **无** | 全部标准库：`json / re / math / pathlib / dataclasses / unittest / argparse` |
| 操作系统 | Windows / macOS / Linux 均可 | 路径一律走 `pathlib`，写文件用 `os.replace` 原子替换 |
| 网络 | **不需要** | 不联网、不调模型 API；标注可由你自己的 LLM 产出后传进来 |
| 数据库 / 向量库 | **不需要** | 存储是纯文本 + JSON；向量检索是可选扩展（见 README 扩展方向） |
| 磁盘 | 视记忆量而定 | 每条约 200~400 字节索引；原始日记另计，**永不自动删除** |
| 权限 | 对数据目录可读写 | 建议单独给机器人一个数据目录，别放在仓库里 |

> `requirements.txt` 是空依赖声明（只有注释）。CI 用 `python -m unittest`，不需要 pytest。

## 2. 三种安装方式

### A. 克隆仓库（推荐，能跑文档和示例）

```bash
git clone https://github.com/professorkun/layered-memory-agent.git
cd layered-memory-agent
python -m unittest discover -s tests -v          # 29 项测试应全过
python examples/quickstart.py                    # 端到端演示
```

### B. 只拷一个包到自己的项目（最轻）

```bash
cp -r layered_memory/ /path/to/your-bot/
cp config.example.json /path/to/your-bot/memory-config.json
```

`layered_memory/` 不依赖仓库里任何其他文件；`Config` 内置 `DEFAULTS` 兜底，
不带配置文件也能跑。

### C. 作为包安装

```bash
pip install -e .                 # 会注册 layered-memory 命令
layered-memory --help
```

## 3. 初始化与首次运行

```bash
# 1) 建分层目录（默认 ./bot-memory，可用 --root 改）
python -m layered_memory --root ./bot-memory init

# 2) 写一条记忆（默认进浅层 live/）
python -m layered_memory --root ./bot-memory add \
  "他半夜说别送了，我觉得他其实在硬撑" \
  --S 0.95 --E 0.90 --D 0.90 --P "他其实在硬撑" --cues "别送了,半夜"

# 3) 重建派生索引
python -m layered_memory --root ./bot-memory build

# 4) 生成注入件（打印到 stdout；同时写到 out/private.md）
python -m layered_memory --root ./bot-memory render --channel private --query "别送了"

# 5) 看每一条现在算到哪儿了
python -m layered_memory --root ./bot-memory audit
```

生成的目录结构：

```
bot-memory/
├── raw/          原始对话证据（只读，永不删）
├── live/         浅层：当天增量
├── daily/        事件层：按日归档
├── facts/        事实层：facts.json + 两版投影
├── self/         生长层：给人审的深层内容（引擎只读）
├── conscious/    意识层状态
├── index/        派生索引、边表、候选、好感度/情绪状态
├── out/          注入件 private.md / group.md / merged-group.md
└── backups/      备份轮转（上限 10）
```

## 4. 配置

```bash
cp config.example.json my-memory.json
# 只改要改的项，其余走内置默认值；逐项说明见 docs/CONFIG.md
python -m layered_memory --config my-memory.json --root ./bot-memory render
```

`config/minimal.json` 是只改三项的最小示例。配置写错（负数、阈值倒置、
未知键）会在 `MemorySystem(...)` 构造时报错或出现在 `stats()["unknown_config_keys"]`，
不会被静默纠正。

## 5. 接进自己的机器人

参考 `examples/adapter.py`（零依赖模板）：

```python
from examples.adapter import BotAdapter

bot = BotAdapter("./bot-memory", annotator=my_llm_annotator)
r = bot.on_message("你还记得上次那件事吗", who="alice")
reply = my_llm.chat(system=r["system"], messages=r["messages"])
bot.after_reply(reply, who="alice")
```

夜间任务（每天一次）：

```python
bot.daily_close()      # 浅层并入 daily/ -> 出生长候选 -> 回收
```

**重要**：`deposit()` 只把候选写到 `index/deposit-candidates.json`，不会自动写 `self/`。
要落盘必须人审后调用 `approve_candidates()`，否则等于让引擎自己编记忆。

## 6. 定时任务（可选）

Linux crontab 示例（路径换成你的）：

```cron
# 每 30 分钟：把你自己的蒸馏脚本产出的增量写进 live/ 后重建索引
*/30 * * * * cd /srv/bot && /usr/bin/python3 -m layered_memory --root /srv/bot/memory build >> /srv/bot/memory/distill-cron.log 2>&1
# 每天 23:45：夜间归档（示例用 CLI 串联；实际项目建议直接调 BotAdapter.daily_close()）
45 23 * * * cd /srv/bot && /usr/bin/python3 -m layered_memory --root /srv/bot/memory gc >> /srv/bot/memory/nightly.log 2>&1
# 每周日 23:50：校准检查
50 23 * * 0 cd /srv/bot && /usr/bin/python3 -m layered_memory --root /srv/bot/memory calibrate >> /srv/bot/memory/calibrate.log 2>&1
```

Windows 计划任务同理，程序填 `python.exe`，参数填
`-m layered_memory --root C:\bot\memory gc`。

> 改动生产配置前先停掉定时任务，避免同一份索引被两个进程同时写。
> 本项目用原子写避免半个文件，但**不提供跨进程文件锁**；`ingest.lock_stale_seconds`
 只是给示例蒸馏脚本留的约定值。

## 7. 升级、迁移与回滚

- 升级：覆盖 `layered_memory/` 后先跑 `python -m unittest discover -s tests -v`，
  再跑 `python -m layered_memory --root <你的目录> self-test`。
- 迁移数据：整个 `bot-memory/` 目录拷走即可，路径全部相对 root，无绝对路径。
- 回滚：`index/` 是派生的，删掉 `forget-index.json` 后 `build` 会重建
  （**会丢 `recalls` 计数**，因为那是索引里的派生量；原始日记不受影响）。
- 覆盖 `self/layers.json` 与 `index/affinity.json` 前 `Store` 会自动备份到 `backups/`。

## 8. 卸载

删除你指定的 `--root` 目录即可；仓库本身不含运行数据。
**删除前先自行备份 `daily/` 与 `raw/`**——那是唯一的原始证据，删了无法重建。

## 9. 安装后验证清单

```bash
python -m unittest discover -s tests -v                 # 29 passed
python examples/quickstart.py                          # 10 步全流程，末尾 8/8 自测
python examples/adapter.py                             # 接入模板
python examples/affect_demo.py                         # 好感度/情绪对接
python -m layered_memory --root ./bot-memory self-test # 8/8
python -m layered_memory --root ./bot-memory pressure  # 容量与复杂度报告
```
