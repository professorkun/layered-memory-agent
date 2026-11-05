"""分层编排：把各层拼成一份可注入的 Markdown，并守住出口边界。

三套正交的分层维度（博客主线，见 docs/blog-post.md）
--------------------------------------------------
1. **按职责**：上下文（不落盘）/ 我的记忆（经历与感觉）/ 知识库（确认过的事实）。
   查到的 ≠ 记得的，两者不互相搬运。
2. **按保鲜度（浅 -> 深）**：L1 近 3 天原文 / L2 4~30 天要点 / L3 更早一句话。
   这是旧版"按天数硬切"的分层，本项目保留为**读取视图**（time_layers），
   与衰减引擎正交：一个管"给多少原文"，一个管"细节还剩多少"。
3. **按抽象程度（浅 -> 深）**：事件 -> 感觉 -> 沉淀 -> 底感 / 深层模式。
   越深的层越不靠细节活着，越不该被每轮对话改写。

注入顺序：**先给主体，再给记忆**。反过来就成了「我有一堆记忆，其中一些关于我」，
那是数据库不是人。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date

from .config import Config
from .consolidation import state_snapshot
from .decay import compute, parse_date
from .models import MemoryItem, STAGE_LABEL, Stage
from .retrieval import recall
from .scoring import clip
from .store import Store

# 一出口就把记忆系统端到人前的词：比漏一条信息更伤
FORBIDDEN_OUTBOUND = ("登记", "报一下", "资料库", "权限", "我没有这个信息")
SECTION_TITLES = {
    "rule": "一直记着的（不靠细节活着，也忘不掉）",
    "intact": "最近，细节还在", "episodic": "记得轮廓的",
    "fragment": "只剩几个碎片的", "residue": "细节没了、但还在影响我的",
}


class LayeredView:
    """分层视图渲染器。纯编排：数值来自 decay，内容来自 store，边界来自 privacy。"""

    def __init__(self, store: Store, cfg: Config):
        self.store = store
        self.cfg = cfg

    # ------------------------------------------------------------ 主体段
    def growth_block(self, now_hours: float = 0.0) -> str:
        """L0 底感 -> 状态快照 -> L1 感觉 -> L3 沉淀 -> 好奇。**群聊不读这一段**。"""
        data = self.store.read_self()
        lines: list[str] = []
        base = data.get("base_feel", [])[:self.cfg.integer("growth.base_feel_max", 9)]
        if base:
            lines += ["## 我是谁", ""] + [f"- {x}" for x in base] + [""]
        snap = state_snapshot(self.store, self.cfg, now_hours)
        if snap["show"]:
            st = snap["state"]
            lines += [f"## {snap['title']}", "",
                      f"- 心情：{st.get('mood', '（空）')}",
                      f"- 还挂着：{st.get('carrying', '（空）')}",
                      f"- 没做完：{st.get('unfinished', '（空）')}", ""]
        feels = data.get("feel", [])
        if feels:
            lines += ["## 现在的感觉", ""] + [f"- {x if isinstance(x, str) else x.get('text', '')}" for x in feels] + [""]
        seds = data.get("sediment", [])
        if seds:
            floor = self.cfg.num("growth.sediment_floor", 0.40)
            rows = []
            for s in seds:
                if isinstance(s, dict):
                    conf = float(s.get("confidence", 0.8))
                    rows.append("- " + ("" if conf >= floor else "（我可能想错了）") + s.get("text", ""))
                else:
                    rows.append(f"- {s}")
            lines += ["## 沉淀下来的看法", ""] + rows + [""]
        curious = data.get("curiosity", [])
        if curious:
            lines += ["## 还没弄明白的", ""] + [f"- {x if isinstance(x, str) else x.get('text', '')}" for x in curious] + [""]
        return "\n".join(lines)

    # ------------------------------------------------------------ 事件段
    def _buckets(self, items: list[MemoryItem], today: date) -> dict[str, list]:
        buckets: dict[str, list] = defaultdict(list)
        for it in items:
            m = compute(it, today, self.cfg)
            buckets[m.stage.value].append((it, m))
        # intact 按时间倒序（读起来像日记）；其余按可检索性；规矩按意义
        buckets["intact"].sort(key=lambda p: p[0].date, reverse=True)
        buckets["rule"].sort(key=lambda p: (-p[1].S, p[0].date))
        for k in ("episodic", "fragment", "residue"):
            buckets[k].sort(key=lambda p: (-p[1].R, p[0].date))
        # 规矩常驻 rule_resident_days 天后交给长期层，不在这儿重复
        keep = self.cfg.num("lifecycle.rule_resident_days", 60)
        buckets["rule"] = [p for p in buckets["rule"] if p[1].days <= keep]
        return buckets

    def _event_block(self, stage: str, rows: list) -> str:
        limit = self.cfg.integer(f"budget.section_limits.{stage}", 12)
        clip_n = self.cfg.integer(f"budget.clip.{stage}", 110)
        out = [f"## {SECTION_TITLES[stage]}", ""]
        for it, m in rows[:limit]:
            p = f"（我当时觉得：{m.item.P}）" if m.P_alive else ""
            if stage == "rule":
                out.append(f"- {clip(it.text, clip_n)}{p}")
            elif stage == "intact":
                tail = f"（D {m.D:.2f}）" if m.D < 0.85 else ""
                out.append(f"- {it.date[5:]}｜{clip(it.text, clip_n)}{tail}{p}")
            elif stage == "episodic":
                note = f"记得大概，D {m.D:.2f}"
                if m.P_alive:
                    note += f"；我当时觉得：{it.P}"
                out.append(f"- {it.date[5:]}｜{clip(it.text, clip_n)}（{note}）")
            elif stage == "fragment":
                bits = "、".join(it.cues[:4]) if it.cues else "零散画面"
                out.append(f"- {it.date[5:]}｜{clip(it.text, clip_n)}〔碎片：{bits}〕{p}")
            elif stage == "residue":
                note = f"细节没了，S {m.S:.2f} / E {m.E:.2f} 还在"
                if m.P_alive:
                    note += f"；想法还在：{it.P}"
                out.append(f"- {it.date[5:]}｜{clip(it.text, clip_n)}（{note}）")
        return "\n".join(out + [""])

    def _cue_block(self, items: list[MemoryItem], today: date) -> str:
        """触景：线索 -> 旧事。最冗余的一段，超预算时**第一个被砍**。"""
        rows = []
        for it in items:
            m = compute(it, today, self.cfg)
            if m.stage == Stage.FADED:
                continue
            for c in it.cues[:2]:
                if c and len(c) <= 10:
                    rows.append((c, m.R, it, m.stage.value))
        rows.sort(key=lambda r: -r[1])
        seen, uniq = set(), []
        for c, _r, it, stage in rows:
            if c in seen:
                continue
            seen.add(c)
            uniq.append((c, it, stage))
        cap = self.cfg.integer("cue.cue_rows", 16)
        lines = ["## 触景（对话里碰到这些词，对应的旧事会自己浮上来）", ""]
        for c, it, stage in uniq[:cap]:
            mark = "·" if stage in ("intact", "episodic", "rule") else "※"
            lines.append(f"- {c} → {it.date[5:]} {clip(it.text, 34)}{mark}")
        return "\n".join(lines + [""])

    # ------------------------------------------------------------ 主编排
    def render(self, items: list[MemoryItem], today: date | None = None,
               channel: str = "private", query: str = "",
               now_hours: float = 0.0, context_snippet: str = "",
               conscious_block: str = "") -> dict:
        """产出注入件。返回 {"text", "budget", "used", "dropped", "sections"}。

        超预算的裁剪顺序是设计的一部分：
        触景 -> 想起 -> 意识层 -> 事件低档 -> ... -> 最后才硬截。
        硬截会把「沉淀下来的看法」从中间切一半，那是最坏的结果。
        """
        today = today or date.today()
        limit = (self.cfg.integer("budget.merged_max_chars", 8000) if channel == "merged"
                 else self.cfg.integer("budget.context_max_chars", 6000))
        buckets = self._buckets(items, today)
        private = channel == "private"

        # 按重要性排序的段；越靠前越不该被砍
        sections: list[tuple[str, int, str]] = []   # (name, priority, text)
        if private:
            growth = self.growth_block(now_hours)
            if growth.strip():
                sections.append(("growth", 0, growth))
        facts = self.store.project_facts("private" if private else "group")
        if facts.strip():
            sections.append(("facts", 1, facts))
        if context_snippet.strip():
            sections.append(("context", 2, f"## 刚才这几句（上下文，不是记忆）\n\n{context_snippet.strip()}\n"))
        for stage in ("rule", "intact", "episodic", "fragment", "residue"):
            rows = buckets.get(stage, [])
            if rows:
                sections.append((stage, 3 + list(SECTION_TITLES).index(stage),
                                 self._event_block(stage, rows)))
        if conscious_block.strip():
            sections.append(("conscious", 20, conscious_block))
        if query:
            blk = recall(items, query, self.cfg, today)["block"]
            if blk:
                sections.append(("recall", 30, blk))
        sections.append(("cues", 40, self._cue_block(items, today)))

        # 从优先级最低的开始砍，直到装得下
        dropped: list[str] = []
        def total() -> int:
            return len("\n".join(s[2] for s in sections))
        while total() > limit and len(sections) > 1:
            victim = max(range(len(sections)), key=lambda i: sections[i][1])
            dropped.append(sections[victim][0])
            sections.pop(victim)

        head = "# " + ("我的记忆" if private else "群里的记忆") + "\n\n"
        tail = ("\n---\n※ 是细节已经掉了的：提起来只说有这么回事、当时什么感觉，别复述情节。\n"
                "细节彻底没了的事，多半已经变成了我的习惯和判断——那些在更深的层里。\n")
        text = head + "\n".join(s[2] for s in sections) + tail
        if len(text) > limit:
            text = text[: max(1, limit - 40)] + "\n\n（记忆太长，后面截掉了。）\n"
            dropped.append("hard-truncate")
        text = self.project(channel, text)
        self.store.write_context(channel, text)
        return {"text": text, "budget": limit, "used": len(text),
                "dropped": dropped, "sections": [s[0] for s in sections],
                "faded_kept": len(buckets.get("faded", []))}

    # ------------------------------------------------------------ 出口边界
    def project(self, channel: str, text: str) -> str:
        """群版只做两件机械转换：换称呼 + 硬挡 PII；其余原文照给，出口由上层判。"""
        for src, dst in (self.cfg.get("privacy.alias", {}) or {}).items():
            text = text.replace(src, dst)
        if channel != "private":
            blocked = self.cfg.get("privacy.hard_block", [])
            lines = []
            for line in text.splitlines():
                if any(w in line for w in blocked):
                    lines.append("〔不出口〕")
                else:
                    lines.append(line)
            text = "\n".join(lines)
        return text

    @staticmethod
    def audit_outbound(text: str) -> list[str]:
        """出站自检：命中这些词说明把记忆系统端到人前了，应改成口语拒答。"""
        return [w for w in FORBIDDEN_OUTBOUND if w in text]

    def merge(self, private_text: str, group_text: str, facts_text: str) -> str:
        """融合件：群会话吃一份合成记忆，但生长层绝不进群（fail-closed）。

        生产系统 2026-11-04 从「单向通道」改成「同一份记忆 + 出口自己判」：
        分库不再用来藏事，只用来分来源；PII 硬挡留机械层，其余是判断题。
        """
        body = "\n".join([facts_text.strip(), "", group_text.strip(), "",
                          "## 别处听来的（群里只接半句，不摊私事）", "",
                          clip(private_text.replace("\n", " "), 600)])
        return self.project("group", body)

    # ------------------------------------------------------------ 按保鲜度分层
    def time_layers(self, items: list[MemoryItem], today: date | None = None) -> dict:
        """旧版浅/中/深三层读取视图：按**时间远近**决定给多少原文。

        L1 近 3 个自然日：原文（每轮必读，预算内全给）
        L2 4~30 天：按周压成要点（每周最多 5 条，每条缩短）
        L3 31 天以上：每天一句话，最多 20 条

        这一层与 decay 正交：它管"给多少原文"，decay 管"细节还剩多少"。
        混用两套编号是最常见的接线错误，见 docs/ARCHITECTURE.md。
        """
        today = today or date.today()
        l1, l2, l3 = [], defaultdict(list), defaultdict(list)
        for it in items:
            age = (today - parse_date(it.date, today)).days
            if age <= 3:
                l1.append(f"- {it.date[5:]}｜{it.text}")
            elif age <= 30:
                week = (today - parse_date(it.date, today)).days // 7
                l2[week].append(it)
            else:
                l3[it.date].append(it)
        l2_text = []
        for week in sorted(l2):
            l2_text.append(f"### 第 {week + 1} 周")
            l2_text += [f"- {clip(x.text, 60)}" for x in l2[week][:5]]
        l3_text = [f"- {d[5:]}｜{clip(group[0].text, 40)}" for d, group in
                   sorted(l3.items(), reverse=True)[:20]]
        return {"L1_recent": "\n".join(l1), "L2_weekly": "\n".join(l2_text),
                "L3_oneline": "\n".join(l3_text),
                "note": "L1/L2/L3 是时间分层；L0 底感/L1 感觉/L2 事件/L3 沉淀是抽象分层，编号不通用。"}

    def compress(self, text: str, level: int) -> str:
        """旧版注入预算 4000 字的逐级压缩：单条截断 -> 每轮留最新几行 -> 只留最近 12 轮。"""
        lines = text.splitlines()
        if level >= 1:
            lines = [l[:80] + "…" if len(l) > 80 else l for l in lines]
        if level >= 2:
            lines = [l for l in lines if l.startswith("-")][-12:]
        if level >= 3:
            lines = lines[-6:]
        return "\n".join(lines)
