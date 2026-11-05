"""存储层：分层目录、原子写、去重、投影、合并。

目录（相对 root，全部可配置根路径）
----------------------------------
    raw/         原始对话证据（只读，永不删、永不改）
    live/        浅层：当天滚动增量（蒸馏器写）
    daily/       事件层：按日归档（日记任务写）
    facts/       事实层：facts.json 单一事实源 + 两版投影
    self/        生长层：给人审的深层内容，**引擎只读不写**
    conscious/   意识层：气质(只读) / 深层模式(只写) / 身体状态
    index/       派生：forget-index.json、link-index.json、候选（删了能重建）
    out/         注入件：private.md / group.md / merged-group.md
    backups/     备份轮转（上限 storage.max_backups）

三条硬规则
----------
1. 原始证据只读：改写记忆不动 raw/，删索引能重建（会丢 recalls 计数）。
2. 一切落盘走原子写 ``*.tmp -> os.replace``，覆盖前留备份。
3. 生长层与气质层是**权限边界**：引擎只读，写入必须显式调用（见 pipeline）。
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path

from .config import Config
from .models import Fact, MemoryItem, fingerprint
from .scoring import auto_cues, heuristic, noise_level

TAG_HEAD_RE = re.compile(r"^\s*\[([^\]]{0,200})\]\s*")
TAG_KV_RE = re.compile(r"([SEDC])\s*[=:：]?\s*(1(?:\.0+)?|0?\.\d+)", re.I)
TAG_COLON_RE = re.compile(r"([SVEDCRI])\s*[:=]\s*(-?(?:\d+(?:\.\d+)?))", re.I)
TAG_WHO_RE = re.compile(r"W\s*[:=]\s*([^\]|｜]+?)(?=\s+[A-Za-z]\s*[:=]|\s*$)")
TAG_P_RE = re.compile(r"P\s*[=:：]\s*([^\]|｜]{2,60})")
CUE_RE = re.compile(r"[〔【]\s*线索\s*[：:]\s*([^〕】]+)[〕】]")
INFER_RE = re.compile(r"[〔【]\s*推断\s*[：:]\s*(0?\.\d+|1(?:\.0+)?)\s*[〕】]")
LINK_RE = re.compile(r"[〔【]\s*连\s*[：:]\s*([^〕】]+)[〕】]")
H2_RE = re.compile(r"^##\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")

DIRS = ("raw", "live", "daily", "facts", "self", "conscious", "index", "out", "backups")


def parse_tags(raw: str) -> dict:
    """抠出 [S0.7 E0.5 D0.9 V:-0.4 W:xiaobei R:0.8 I:0.6 P:...]。缺的留空，不猜。"""
    out: dict = {}
    for key, val in TAG_KV_RE.findall(raw):
        out[key.upper()] = max(0.0, min(1.0, float(val)))
    for key, val in TAG_COLON_RE.findall(raw):
        key = key.upper()
        v = float(val)
        out[key] = max(-1.0, min(1.0, v)) if key == "V" else max(0.0, min(1.0, v))
    mw = TAG_WHO_RE.search(raw)
    if mw:
        names = [x.strip() for x in re.split(r"[,，、/| ]+", mw.group(1)) if x.strip()]
        if names:
            out["W"] = names[:6]
    mp = TAG_P_RE.search(raw)
    if mp:
        out["P"] = mp.group(1).strip()
    return out


def parse_markdown(path: Path, cfg: Config | None = None) -> list[MemoryItem]:
    """把一个日记/增量文件拆成条目。只读。日期取自文件名 YYYY-MM-DD.md。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        day = date.fromisoformat(path.stem).isoformat()
    except ValueError:
        return []
    source = "live" if path.parent.name == "live" else "daily"
    items: list[MemoryItem] = []
    section = ""
    for line in text.splitlines():
        m2 = H2_RE.match(line)
        if m2:
            section = m2.group(1).strip()
            continue
        m = BULLET_RE.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        if not body or body in ("无", "（无）", "-"):
            continue
        tags, infer, links = {}, (cfg.num("heuristic.default_inference", 0.2)
                                  if cfg else 0.2), []
        head = TAG_HEAD_RE.match(body)
        if head:
            tags = parse_tags(head.group(1))
            body = body[head.end():].strip()
        cues: list[str] = []
        mc = CUE_RE.search(body)
        if mc:
            cues = [c.strip() for c in re.split(r"[,，、/|]", mc.group(1)) if c.strip()]
            body = CUE_RE.sub("", body).strip()
        mi = INFER_RE.search(body)
        if mi:
            infer = float(mi.group(1))
            body = INFER_RE.sub("", body).strip()
        for ml in LINK_RE.findall(body):
            links += [x.strip() for x in re.split(r"[,，、/|]", ml) if x.strip()]
        body = LINK_RE.sub("", body).strip()
        if not body:
            continue
        it = MemoryItem.create(body, day=day, section=section, source=source,
                               S=tags.get("S", 0.35), D0=tags.get("D", 0.55),
                               E=tags.get("E", 0.15), P=tags.get("P", ""),
                               who=tags.get("W", []), val=tags.get("V", 0.0),
                               rel=tags.get("R", 0.5), ident=tags.get("I", 0.5),
                               cues=cues, cues_src="manual" if cues else "auto",
                               inference=infer)
        # live/ 条目一律按事件算，除非它明确落在「记住的」段：
        # 否则一句随口的「以后」会被永久挂在不衰减的常驻段里。
        it.kind = (_kind(section, body) if "记住" in section else "event") \
            if source == "live" else _kind(section, body)
        it._manual_links = links  # type: ignore[attr-defined]  动态挂给 consolidation 用
        if not cues and cfg:
            it.cues = auto_cues(it.text, cfg)
        items.append(it)
    return items


def _kind(section: str, text: str) -> str:
    """与 decay.kind_of 同口径（避免循环 import）。"""
    date_like = re.search(r"\d{1,2}-\d{1,2}|\d{3,4}-\d{3,4}", text)
    if "记住" in section:
        return "event" if date_like else "rule"
    rule_words = ("以后", "别再", "规矩", "偏好", "习惯", "不许", "必须", "约定")
    if any(w in text for w in rule_words) and not date_like:
        return "rule"
    return "event"


def line_fingerprint(line: str) -> str:
    """从一条 markdown 行还原条目指纹：先去 bullet，再交给 fingerprint 去标注/线索。"""
    s = re.sub(r"^\s*[-*]\s+", "", line)
    return fingerprint(s)


def render_item(it: MemoryItem) -> str:
    """把条目写回 markdown（含标注与线索），供 live/daily 使用。"""
    tag = f"[S{it.S:.2f} E{it.E:.2f} D{it.D0:.2f}"
    if it.val:
        tag += f" V:{it.val:.2f}"
    if it.who:
        tag += " W:" + ",".join(it.who)
    if it.P:
        tag += f" P:{it.P}"
    tag += "]"
    cue = f"〔线索：{','.join(it.cues)}〕" if it.cues else ""
    return f"- {tag} {it.text}{cue}"


class Store:
    """分层存储。所有路径从 root 派生，换 root 即可接到自己的机器人。"""

    def __init__(self, root: str | Path, cfg: Config):
        self.root = Path(root)
        self.cfg = cfg

    def init_dirs(self) -> None:
        for d in DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        index = self.root / "index" / "forget-index.json"
        if not index.exists():
            self.write_json(index, {"version": 1, "items": {}})

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    # ------------------------------------------------------------ 原子写 / 备份
    def atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def write_json(self, path: Path, data: dict) -> None:
        self.atomic_write(path, json.dumps(data, ensure_ascii=False, indent=1) + "\n")

    def read_json(self, path: Path, default=None):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {} if default is None else default

    def backup(self, path: Path, tag: str = "auto") -> Path | None:
        """覆盖前留一份；同 tag 的备份只保留 max_backups 份（内存/磁盘控制）。"""
        if not path.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.path("backups", f"{path.stem}.bak-{stamp}-{tag}{path.suffix}")
        dest.write_bytes(path.read_bytes())
        olds = sorted(self.path("backups").glob(f"{path.stem}.bak-*-{tag}{path.suffix}"))
        cap = self.cfg.integer("storage.max_backups", 10)
        while len(olds) > cap:
            olds.pop(0).unlink(missing_ok=True)
        return dest

    # ------------------------------------------------------------ 索引（派生）
    def load_index(self) -> dict:
        data = self.read_json(self.path("index", "forget-index.json"),
                              {"version": 1, "items": {}})
        return data.get("items", {}) if isinstance(data, dict) else {}

    def save_index(self, items: list[MemoryItem]) -> None:
        payload = {"version": 1, "updated": datetime.now().isoformat(timespec="seconds"),
                   "items": {it.id: it.to_index() for it in items}}
        self.write_json(self.path("index", "forget-index.json"), payload)

    def build_items(self, today: date | None = None) -> list[MemoryItem]:
        """扫 daily/ + live/，合索引，返回带全变量的条目。

        优先级：手写标注 > 索引里上次的值 > 启发式兜底。
        N 每次重算（O(M^2)，见 docs/PERFORMANCE.md）。
        """
        known = self.load_index()
        parsed: list[MemoryItem] = []
        seen: set[str] = set()
        for folder in ("daily", "live"):
            for f in sorted((self.root / folder).glob("*.md")):
                for it in parse_markdown(f, self.cfg):
                    if it.id in seen:
                        continue                    # 同一件事只算一条
                    seen.add(it.id)
                    parsed.append(it)

        items: list[MemoryItem] = []
        for it in parsed:
            old = known.get(it.id, {})
            h = heuristic(it, self.cfg)
            # parse_markdown 已把标注写进字段；缺标注时回退到上次索引值，再回退到启发式
            if not _tagged(it):
                it.S = float(old.get("S", h["S"]))
                it.D0 = float(old.get("D0", h["D0"]))
                it.E = float(old.get("E", h["E"]))
            it.cues = it.cues or list(old.get("cues") or [])
            it.P = it.P or old.get("P", "")
            it.who = it.who or list(old.get("who") or [])
            it.recalls = int(old.get("recalls", 0))
            it.last_recall = old.get("last_recall")
            it.C = min(self.cfg.num("consolidation.c_cap", 0.90),
                       self.cfg.num("consolidation.c_base", 0.10)
                       + self.cfg.num("consolidation.c_per_recall", 0.18) * it.recalls)
            items.append(it)
        for it in items:
            it.N = noise_level(it, items, self.cfg)
        return items

    # ------------------------------------------------------------ 写入 / 合并
    def append(self, item: MemoryItem, folder: str = "live") -> Path:
        day = item.date
        path = self.path(folder, f"{day}.md")
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        body = render_item(item)
        if any(line_fingerprint(x) == item.id.split("#")[-1]
               for x in lines if x.strip().startswith("-")):
            return path                              # 同日去重
        if not any(l.startswith("## ") for l in lines):
            lines += [f"# {day}", "", "## 私聊", ""]
        # 把 section 写进文件，否则重建索引时规矩会被降级成普通事件
        last_section = ""
        for l in lines:
            m2 = H2_RE.match(l)
            if m2:
                last_section = m2.group(1).strip()
        section = item.section or "私聊"
        if last_section != section:
            lines += ["", f"## {section}", ""]
        lines.append(body)
        self.atomic_write(path, "\n".join(lines) + "\n")
        return path

    def merge_live(self, day: str | None = None) -> dict:
        """浅层 -> 事件层：把 live/ 并进 daily/ 后删除 live 文件（GC 的一部分）。"""
        day = day or date.today().isoformat()
        src, dst = self.path("live", f"{day}.md"), self.path("daily", f"{day}.md")
        if not src.exists():
            return {"merged": 0, "removed": False}
        existing = set()
        if dst.exists():
            for line in dst.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("-"):
                    existing.add(line_fingerprint(line))
        new_lines = []
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("-") and line_fingerprint(line) not in existing:
                new_lines.append(line)
        if new_lines:
            old = dst.read_text(encoding="utf-8") if dst.exists() else f"# {day}\n\n## 私聊\n"
            self.backup(dst, "merge")
            self.atomic_write(dst, old.rstrip() + "\n" + "\n".join(new_lines) + "\n")
        src.unlink(missing_ok=True)
        return {"merged": len(new_lines), "removed": True}

    # ------------------------------------------------------------ 事实层投影
    def load_facts(self) -> list[Fact]:
        data = self.read_json(self.path("facts", "facts.json"), {"facts": []})
        return [Fact(**f) for f in data.get("facts", []) if isinstance(f, dict)]

    def project_facts(self, channel: str) -> str:
        """两版投影。铁律：值不过界——群里绝不出现 quiet/secret 的值。"""
        rows = [f.project(channel, self.cfg) for f in self.load_facts()]
        rows = [r for r in rows if r]
        head = "## 确认过的事实" if channel == "private" else "## 都知道的"
        text = "\n".join([head, ""] + rows + [""])
        path = self.path("facts", f"facts-{channel}.md")
        self.atomic_write(path, text)
        return text

    # ------------------------------------------------------------ 生长层（只读）
    def read_self(self) -> dict:
        return self.read_json(self.path("self", "layers.json"), {
            "base_feel": [], "feel": [], "sediment": [],
            "curiosity": [], "skills": [], "state": {},
        })

    def write_self(self, data: dict) -> None:
        """显式写入（需要人审或上层确认）；引擎默认不调用。"""
        self.backup(self.path("self", "layers.json"), "self")
        self.write_json(self.path("self", "layers.json"), data)

    # ------------------------------------------------------------ 意识层状态
    def read_conscious(self) -> dict:
        return self.read_json(self.path("conscious", "state.json"), {})

    def write_conscious(self, data: dict) -> None:
        self.write_json(self.path("conscious", "state.json"), data)

    # ------------------------------------------------------------ 注入件
    def write_context(self, channel: str, text: str) -> Path:
        name = {"private": "private.md", "group": "group.md",
                "merged": "merged-group.md"}.get(channel, f"{channel}.md")
        path = self.path("out", name)
        self.atomic_write(path, text)
        return path

    def read_context(self, channel: str) -> str:
        name = {"private": "private.md", "group": "group.md",
                "merged": "merged-group.md"}.get(channel, f"{channel}.md")
        try:
            return self.path("out", name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def stats(self) -> dict:
        out = {"root": str(self.root), "dirs": {}}
        for d in DIRS:
            p = self.root / d
            out["dirs"][d] = {"files": len(list(p.glob("*"))) if p.is_dir() else 0,
                              "bytes": sum(f.stat().st_size for f in p.glob("*") if f.is_file())
                               if p.is_dir() else 0}
        out["index_items"] = len(self.load_index())
        return out


def _tagged(it: MemoryItem) -> bool:
    """判断这条是不是带了手写标注（简化：与启发式默认值不同即视为标注过）。"""
    return (abs(it.S - 0.35) > 1e-9 or abs(it.D0 - 0.55) > 1e-9
            or abs(it.E - 0.15) > 1e-9 or bool(it.P) or bool(it.who))
