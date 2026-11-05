#!/usr/bin/env python3
"""好感度 / 情绪 / 记忆标注的对接演示：三者时间尺度不同，不互相写回。

    python examples/affect_demo.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layered_memory import MemorySystem  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    mem = MemorySystem(d)
    mem.init()

    # 记忆层：条目带 V/W/R/I/E，喂给人物模型 / 自我模型 / 激活竞争
    mem.ingest("他又主动接了我说的话，聊得挺顺的", who=["alice"], val=0.6,
               tags={"S": 0.6, "E": 0.7, "D": 0.8, "R": 0.8, "I": 0.6},
               cues=["接话"], folder="daily")
    mem.build()
    print("① 记忆标注流向：", mem.memory_signals())

    # 好感度：事件表记分（有冷却 / 单次上限 / 高分饱和 / 日净上限）
    print("② 第一次记分：", mem.affect_tick(event="talk", who="alice")["affinity"])
    print("   立刻再记一次（应被冷却挡住）：",
          mem.affect_tick(event="talk", who="alice")["affinity"])

    # 情绪：好感档 -> 基线；事件 -> 临时情绪；半衰归零
    print("③ 被夸之后的情绪行：", mem.affect_tick(emotion_event="praised")["emotion"])
    print("   注入行（群里不报数字、不念状态）：", mem.prompt_lines("alice"))

    # 群氛围偏置：单次 mood() 最多 ±3，这里直接设到低氛围演示降档
    mem.aff.state["group"]["score"] = 20
    print("④ 群氛围降到 20 时的基线（应比正常更冷）：",
          mem.emo.baseline(mem.aff, "alice"))

    print("⑤ 容量体检：", mem.pressure())
