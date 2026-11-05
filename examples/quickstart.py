#!/usr/bin/env python3
"""端到端最小示例：从空目录到可注入的分层记忆。

运行（仓库根目录）::

    python examples/quickstart.py
    python examples/quickstart.py --root ./my-bot-memory --keep

不联网、不装依赖、不写仓库目录之外的路径（默认用临时目录）。
"""
from __future__ import annotations

import argparse
import tempfile
from datetime import date
from pathlib import Path

# 当从 examples/ 直接运行时，把仓库根加进 sys.path（必须在 import 之前）
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layered_memory import MemorySystem  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=None, help="数据目录；不传用临时目录")
    ap.add_argument("--keep", action="store_true", help="保留临时目录并打印路径")
    ap.add_argument("--today", default="2026-11-05")
    args = ap.parse_args()

    tmp = None
    root = args.root
    if not root:
        if args.keep:
            root = tempfile.mkdtemp(prefix="layered-memory-demo-")
        else:
            tmp = tempfile.TemporaryDirectory(prefix="layered-memory-demo-")
            root = tmp.name
    cfg = str(Path(__file__).resolve().parents[1] / "config.example.json")
    mem = MemorySystem(root, config=cfg, today=date.fromisoformat(args.today))
    print("① 初始化分层目录")
    print("  ", mem.init()["dirs"])

    print("\n② 写入：一条重的事、一条琐事、一条规矩、一条白天增量")
    samples = [
        ("他半夜说别送了，语气很轻，我觉得他其实在硬撑",
         {"S": 0.95, "E": 0.9, "D": 0.9}, ["别送了"], "他其实在硬撑", "daily"),
        ("今天中午随便吃了碗面，没什么特别的",
         {"S": 0.35, "E": 0.15, "D": 0.85}, ["午饭"], "", "daily"),
        ("以后说话别带括号旁白，也别重复同一个梗",
         {"S": 0.8, "E": 0.2, "D": 0.6}, [], "", "daily"),
        ("刚聊到一半说下午再继续，先记一笔增量",
         {"S": 0.30, "E": 0.15, "D": 0.70}, ["下午"], "", "live"),
    ]
    for text, tags, cues, p, folder in samples:
        r = mem.ingest(text, tags=tags, cues=cues, P=p,
                       section="记住的" if "以后" in text else "", folder=folder)
        print(f"   {r['ok']} {r.get('id') or r.get('reason')}")
    items = mem.build()
    print(f"   索引条目：{len(items)} 条")

    print("\n③ 参数校准（改配置前先跑这个）")
    print("  ", mem.calibrate())

    print("\n④ 分层注入件（私聊，先给主体再给记忆）")
    rendered = mem.render(channel="private", query="别送了")
    print(f"   预算 {rendered['used']}/{rendered['budget']}，段={rendered['sections']}")
    print("   ---- 前 24 行 ----")
    print("\n".join(rendered["text"].splitlines()[:24]))

    print("\n⑤ 按当前这句检索（不是刚发生的事）")
    print(mem.recall("别送了")["block"])

    print("\n⑥ 记一次回忆：C 上升、D0 微升，原始日记不动")
    first = items[0].id
    print("  ", {k: mem.touch(first)[k] for k in ("ok",)})
    print("  ", mem.audit()[0])

    print("\n⑦ 生长层只出候选，不自动写深层")
    cand = mem.deposit()
    print(f"   感觉候选 {len(cand['feel'])}，沉淀候选 {len(cand['sediment'])}，边 {len(cand['edges'])}")

    print("\n⑧ 好感度 / 情绪一轮 + 意识层激活")
    print("  ", mem.affect_tick(event="talk", who="xiaobei", emotion_event="praised"))
    print("  ", mem.conscious_tick(query="别送了"))

    print("\n⑨ 回收与容量体检")
    gc_report = mem.gc()
    print("  ", [a["step"] for a in gc_report["actions"]])
    print("  ", mem.pressure())

    print("\n⑩ 内置 8 项自测")
    st = mem.self_test()
    print(f"   {st['passed']}/{st['total']} 通过")

    if tmp:
        tmp.cleanup()
    if args.keep:
        print("\n数据保留在：", root)


if __name__ == "__main__":
    main()
