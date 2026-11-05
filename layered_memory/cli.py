"""命令行入口：``python -m layered_memory <命令>``。

所有命令都接受 --root（数据目录）与 --config（配置文件），
不写死任何路径，方便直接接进别人的机器人或 cron。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .config import Config
from .pipeline import MemorySystem


def _out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="layered-memory",
                                 description="分层记忆系统：浅层/深层/事实层/意识层的可复现实现")
    ap.add_argument("--root", default="./bot-memory", help="数据目录（默认 ./bot-memory）")
    ap.add_argument("--config", default=None, help="JSON 配置；不传用内置默认值")
    ap.add_argument("--today", default=None, help="覆盖今天 YYYY-MM-DD（测试用）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建目录与示例事实层")
    p = sub.add_parser("add", help="写一条事件（默认进浅层 live/）")
    p.add_argument("text")
    p.add_argument("--day", default=None)
    p.add_argument("--S", type=float, default=None)
    p.add_argument("--E", type=float, default=None)
    p.add_argument("--D", type=float, default=None)
    p.add_argument("--P", default="")
    p.add_argument("--cues", default="", help="逗号分隔")
    p.add_argument("--who", default="", help="逗号分隔")
    p.add_argument("--V", type=float, default=0.0)
    p.add_argument("--section", default="")
    p.add_argument("--folder", default="live", choices=["live", "daily"])
    p.add_argument("--quote", default=None, help="原文逐字引语，用于防编造校验")
    p.add_argument("--raw-text", default="", help="原文全文，配合 --quote")

    sub.add_parser("build", help="重扫 daily/+live/，重建派生索引")
    p = sub.add_parser("render", help="生成注入件")
    p.add_argument("--channel", default="private", choices=["private", "group", "merged"])
    p.add_argument("--query", default="")
    p.add_argument("--context", default="")
    p = sub.add_parser("recall", help="按当前这句召回旧事")
    p.add_argument("query")
    p.add_argument("--k", type=int, default=None)
    p.add_argument("--min-cue", type=float, default=None)
    p = sub.add_parser("touch", help="记一次回忆（巩固 / 重固化）")
    p.add_argument("id")
    sub.add_parser("audit", help="逐条列出今天的变量与阶段")
    sub.add_parser("deposit", help="产出分化/沉淀/联结候选（不写 self/）")
    p = sub.add_parser("approve", help="把候选写进生长层（需人审后的句子）")
    p.add_argument("--feel", default="", help="格式 id=句子;id=句子")
    p.add_argument("--sediment", default="", help="格式 topic=句子;topic=句子")
    sub.add_parser("calibrate", help="跑参数校准的两个案例")
    sub.add_parser("time-layers", help="按保鲜度输出 L1/L2/L3 三层视图")
    p = sub.add_parser("affect", help="好感度/情绪一轮记分")
    p.add_argument("--who", default="")
    p.add_argument("--event", default=None)
    p.add_argument("--delta", type=float, default=None)
    p.add_argument("--mood-delta", type=float, default=0.0)
    p.add_argument("--emotion", default=None)
    p = sub.add_parser("conscious", help="意识层激活竞争")
    p.add_argument("--query", default="")
    sub.add_parser("gc", help="跑一遍回收与容量体检")
    sub.add_parser("stats", help="目录与索引统计")
    sub.add_parser("pressure", help="性能/容量压力报告")
    p = sub.add_parser("self-test", help="内置 8 项自测（不碰真实数据）")
    return ap


def _pairs(s: str) -> dict:
    out = {}
    for part in filter(None, (x.strip() for x in s.split(";"))):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config) if args.config else Config()
    today = date.fromisoformat(args.today) if args.today else None
    mem = MemorySystem(args.root, cfg, today=today)

    if args.cmd == "init":
        _out(mem.init())
    elif args.cmd == "add":
        tags = {k: v for k, v in {"S": args.S, "E": args.E, "D": args.D}.items()
                if v is not None}
        _out(mem.ingest(args.text, day=args.day, tags=tags,
                        cues=[x for x in args.cues.split(",") if x], P=args.P,
                        who=[x for x in args.who.split(",") if x], val=args.V,
                        section=args.section, folder=args.folder,
                        quote=args.quote, raw_text=args.raw_text))
    elif args.cmd == "build":
        _out({"items": len(mem.build()), "index": str(mem.store.path("index", "forget-index.json"))})
    elif args.cmd == "render":
        r = mem.render(channel=args.channel, query=args.query, context_snippet=args.context)
        print(r["text"])
        print(f"\n[budget {r['used']}/{r['budget']} | dropped={r['dropped']} | "
              f"faded_kept={r['faded_kept']}]", file=sys.stderr)
    elif args.cmd == "recall":
        print(mem.recall(args.query, args.k, args.min_cue)["block"])
    elif args.cmd == "touch":
        _out(mem.touch(args.id))
    elif args.cmd == "audit":
        _out(mem.audit())
    elif args.cmd == "deposit":
        _out(mem.deposit())
    elif args.cmd == "approve":
        _out(mem.approve_candidates(_pairs(args.feel), _pairs(args.sediment)))
    elif args.cmd == "calibrate":
        _out(mem.calibrate())
    elif args.cmd == "time-layers":
        _out(mem.time_layers())
    elif args.cmd == "affect":
        _out(mem.affect_tick(args.event, args.who, args.delta,
                             args.mood_delta, args.emotion))
        _out(mem.prompt_lines(args.who or None))
    elif args.cmd == "conscious":
        print(mem.conscious_tick(args.query))
    elif args.cmd == "gc":
        _out(mem.gc())
    elif args.cmd == "stats":
        _out(mem.stats())
    elif args.cmd == "pressure":
        _out(mem.pressure())
    elif args.cmd == "self-test":
        r = mem.self_test()
        _out(r)
        return 0 if r["passed"] == r["total"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
