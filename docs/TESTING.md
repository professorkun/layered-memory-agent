# 测试示例与可复现流程

## 1. 一键跑全量

```bash
git clone https://github.com/professorkun/layered-memory-agent.git
cd layered-memory-agent
python -m unittest discover -s tests -v
# 期望：Ran 29 tests ... OK
```

不需要 `pip install`、不需要网络、不需要准备数据。`tests/test_all.py` 全用
`tempfile.TemporaryDirectory()`，不会污染仓库目录。

## 2. 内置自测（部署后也能跑）

```bash
python -m layered_memory --root ./bot-memory self-test
# 期望：8/8 通过；失败时返回非零退出码，可直接接进健康检查
```

8 项是：重事五年后 D≈0.18、S≈0.88、琐事 D<0.1、`p=1` 时半衰期==τ、
规矩不走 D 衰减、secret 值不进群投影、群版不读生长层、注入预算可控。

## 3. 29 项测试覆盖什么

| 组 | 数量 | 覆盖 |
| --- | --- | --- |
| `TestConfig` | 3 | 示例配置与代码默认值逐项一致（防隐藏参数）、未知键检测、越界校验 |
| `TestDecay` | 6 | 两个校准案例、规矩不衰减、幂律长尾优于指数、P 比细节活得久、生命周期边界、启发式与干扰度 |
| `TestRetrieval` | 3 | 手写线索命中与阈值、无关句子返回空、召回预算逐行累加 |
| `TestSystem` | 12 | 写入筛选（含 quote 防编造）、同日去重、事实层值不过界、群版 fail-closed、touch 不动原始日记、预算先砍触景、生长候选不自动落盘、时间分层与抽象分层互不混用、GC 幂等、8 项自测、容量报告 |
| `TestAffect` | 6 | 冷却防刷、高分饱和与日上限、降档迟滞、衰减下限、情绪半衰与档位基线、V/W/R/I 信号流向 |

## 4. 三个端到端示例（本身就是可复现流程）

```bash
python examples/quickstart.py                 # 10 步：init→写→校准→渲染→检索→巩固→候选→情绪→GC→自测
python examples/quickstart.py --keep          # 保留数据目录，检查生成的文件
python examples/adapter.py                   # 任意机器人接入模板
python examples/affect_demo.py               # 好感度/情绪/记忆标注对接
```

`quickstart.py` 的输出里应能看到：
`calibrate` 的重事 `D≈0.175 / S≈0.883 / τ≈440.8`、琐事 `D≈0.038 / τ≈84.7`；
`8/8 通过`；`pressure` 的 `noise_pairs` 随条目数增长。

## 5. 手工复现一条完整链路（CLI 版）

```bash
R=./bot-memory
python -m layered_memory --root $R init
python -m layered_memory --root $R add "他半夜说别送了，我觉得他其实在硬撑" \
  --S 0.95 --E 0.90 --D 0.90 --P "他其实在硬撑" --cues "别送了,半夜"
python -m layered_memory --root $R build
python -m layered_memory --root $R audit
python -m layered_memory --root $R render --channel private --query "别送了"
python -m layered_memory --root $R recall "别送了"
python -m layered_memory --root $R calibrate
python -m layered_memory --root $R deposit
python -m layered_memory --root $R affect --who alice --event talk --emotion praised
python -m layered_memory --root $R gc
python -m layered_memory --root $R pressure
```

`--today YYYY-MM-DD` 可以固定日期，让衰减相关输出完全可复现（测试就是这么做的）。

## 6. 写你自己的测试

```python
import tempfile, unittest
from datetime import date
from layered_memory import MemorySystem, Config

class MyTest(unittest.TestCase):
    def test_my_annotator(self):
        with tempfile.TemporaryDirectory() as d:
            mem = MemorySystem(d, config=Config({"decay": {"tau0": 60.0}}),
                               today=date(2026, 11, 5))
            mem.init()
            mem.ingest("一件很长很长的事，用来测试自定义配置", tags={"S": 0.9})
            mem.build()
            self.assertGreater(mem.calibrate()["heavy"]["D"], 0.2)
```

要点：
- 用 `today=` 固定时间，别依赖 `date.today()`；
- 每个用例独立 `TemporaryDirectory`；
- 改配置用 `Config({...})` 深合并，不必复制整份 JSON；
- 断言隐私时读 `facts-group.md`，别只看返回值。

## 7. CI

仓库提供 `docs/ci-workflow-example.yml`：在 Python 3.10 / 3.11 / 3.12 / 3.13 上跑
`unittest` + 三个示例 + `self-test`，任何一步失败即红灯。
把它复制到 `.github/workflows/tests.yml` 即启用；若当前 GitHub 凭据只有 `repo` 权限，
请在仓库网页端添加（API 创建 workflow 需要 `workflow` scope，可用
`gh auth refresh -s workflow` 重新授权，但会要求本人在浏览器确认）。
本地等价于：

```bash
python -m unittest discover -s tests -v \
  && python examples/quickstart.py > /dev/null \
  && python examples/adapter.py > /dev/null \
  && python examples/affect_demo.py > /dev/null
```

## 8. 故障注入建议（上线前）

| 要验的 | 怎么造 |
| --- | --- |
| 索引可重建 | 删 `index/forget-index.json` → `build` → 条目回来（recalls 归零） |
| 原子写 | 在 `render` 中途 kill 进程 → `out/*.md` 应保持上一版完整内容 |
| 预算裁剪 | 把 `budget.context_max_chars` 调到 900 → 看 `dropped` 是否只有低优先级段 |
| 隐私 | 往 `facts.json` 加 `vis: secret` 的真实值占位 → 读 `facts-group.md` 断言不存在 |
| 并发 | 两个进程同时 `add` → 检查是否有条目丢失（本项目**不保证**，应改为单写者） |
