#!/usr/bin/env python3
"""接入模板：把分层记忆接进**任意**机器人，不绑定任何 LLM / IM 框架。

只用到 ``MemorySystem`` 的 5 个方法：ingest / build / render / recall / affect_tick。
``annotator`` 是你自己的标注器（可以是规则、你调的 LLM、或人工）；本例给一个
零依赖的规则版，接口保持 ``(text, raw_text) -> dict``，换成模型即可。

典型接线::

    adapter = BotAdapter("./bot-memory")
    prompt = adapter.on_message("你还记得上次那件事吗", who="alice", channel="private")
    reply = my_llm.chat(system=prompt["system"], messages=prompt["messages"])
    adapter.after_reply(reply, who="alice")
    # 每晚 cron：
    adapter.daily_close()
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from layered_memory import MemorySystem  # noqa: E402

EMO_WORDS = ("怕", "难受", "哭", "生气", "烦", "委屈", "开心", "喜欢", "讨厌",
             "舍不得", "想念", "后悔", "感动", "担心")
IMPORTANT_WORDS = ("记住", "以后", "规矩", "决定", "约定", "必须", "重要", "别再")


def rule_annotator(text: str, raw_text: str = "") -> dict:
    """示例标注器：保守给分，判不出的一律省略（省略比编造好）。

    生产里建议改用 LLM 输出 JSON；字段口径见 docs/CONFIG.md 的 ingest/标注段。
    """
    n_emo = sum(1 for w in EMO_WORDS if w in text)
    n_imp = sum(1 for w in IMPORTANT_WORDS if w in text)
    n_con = len(re.findall(r"\d{1,2}[:：]\d{2}|\d{1,2}-\d{1,2}|\d+\s*(?:点|号|元|次)", text))
    quotes = re.findall(r"[「『\"]([^」』\"]{2,30})[」』\"]", text)
    return {
        "S": 0.70 if n_imp >= 2 else 0.50 if n_imp == 1 else 0.35,
        "E": min(0.85, 0.20 + 0.16 * n_emo) if n_emo else 0.15,
        "D": 0.85 if n_con >= 3 else 0.65 if n_con else 0.55,
        "cues": quotes[:3],
        "P": "",                       # 观点只能靠模型写，规则版不编
        "V": 0.0, "who": [], "R": 0.5, "I": 0.5,
    }


class BotAdapter:
    """一个会话/一个 root；多用户可用不同 root 或不同 channel 投影。"""

    def __init__(self, root: str = "./bot-memory", config: str | None = None,
                 annotator: Callable[[str, str], dict] | None = None,
                 system_base: str = "你是一个有长期记忆的助手。"):
        self.mem = MemorySystem(root, config=config, today=date.today())
        self.mem.init()
        self.annotate = annotator or rule_annotator
        self.system_base = system_base
        self._pending: dict = {}

    def on_message(self, text: str, who: str = "user", channel: str = "private",
                   raw_text: str = "", remember: bool = True) -> dict:
        """一轮开始：判定是否值得记 -> 写浅层 -> 生成分层 prompt。"""
        if remember and len(text.strip()) >= 8:
            tags = self.annotate(text, raw_text)
            self.mem.ingest(
                text, tags={k: tags[k] for k in ("S", "E", "D")},
                cues=tags.get("cues", []), P=tags.get("P", ""),
                who=tags.get("who", []) or [who],
                val=tags.get("V", 0.0), rel=tags.get("R", 0.5),
                ident=tags.get("I", 0.5), section="",
                quote=None, raw_text=raw_text)
        self.mem.build()

        # render 里若传 query 会自带「想起」段；这里单独拼，避免重复注入
        rendered = self.mem.render(channel=channel)
        recalled = self.mem.recall(text)["block"]
        conscious = self.mem.conscious_tick(query=text)
        affect_lines = self.mem.prompt_lines(who, channel)
        system = "\n\n".join(filter(None, [
            self.system_base, rendered["text"], recalled, conscious,
            "\n".join(affect_lines),
        ]))
        self._pending = {"who": who, "channel": channel, "text": text}
        return {"system": system, "messages": [{"role": "user", "content": text}],
                "debug": {"budget": rendered["budget"], "used": rendered["used"],
                          "dropped": rendered["dropped"],
                          "outbound_warnings": self.mem.view.audit_outbound(system)}}

    def after_reply(self, reply: str, who: str = "user",
                    event: str | None = "reply", emotion_event: str | None = None) -> dict:
        """一轮结束：记好感度、推情绪、检查出站话术。"""
        result = self.mem.affect_tick(event=event, who=who,
                                     emotion_event=emotion_event)
        result["outbound_warnings"] = self.mem.view.audit_outbound(reply)
        return result

    def daily_close(self) -> dict:
        """夜间任务：浅层并入事件层 -> 出生长候选 -> 回收。"""
        self.mem.build()
        cand = self.mem.deposit()
        gc_report = self.mem.gc()
        return {"candidates": {"feel": len(cand["feel"]),
                               "sediment": len(cand["sediment"]),
                               "edges": len(cand["edges"])},
                "gc_steps": [a["step"] for a in gc_report["actions"]],
                "note": "候选写到 index/，深层文件要人审后 approve_candidates() 才落盘"}

    def correct(self, item_id: str, new_text: str) -> dict:
        """用户纠正旧事：不删原始证据，写一条新条目并让旧条目标记过期（由上层处理）。"""
        return self.mem.ingest(new_text, section="", folder="daily",
                               tags={"S": 0.9, "E": 0.2, "D": 0.9})


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bot = BotAdapter(d)
        round1 = bot.on_message("以后别再叫我全名，直接叫小北就好", who="alice")
        print("--- system prompt（前 25 行）---")
        print("\n".join(round1["system"].splitlines()[:25]))
        print("\n--- debug ---", round1["debug"])
        print("\n--- after_reply ---", bot.after_reply("好，记住了", who="alice"))
        print("\n--- daily_close ---", bot.daily_close())
