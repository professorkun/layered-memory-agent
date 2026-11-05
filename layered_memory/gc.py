"""淘汰与内存控制：**不靠"删掉旧记忆"来省内存**。

原则
----
* 原始证据（raw/、daily/）只读，永不删。删了就再也重建不回当时的原话。
* faded 条目**保留在索引里**（可审计、可被重新召回），只是不进注入件。
* 真正回收的是派生件：live/ 合并后删除、备份轮转、history/seen 上限、
  临时情绪归零、检索缓存淘汰、日志停写。
* 上下文预算是**分段裁剪**，不是最后硬截：先砍触景/想起/意识层等冗余段，
  保住「我是谁 / 沉淀下来的看法」这些主体段（见 layers.LayeredView.render）。

性能瓶颈（见 docs/PERFORMANCE.md）
--------------------------------
* ``scoring.noise_level`` 全库两两比二元组，O(M^2·L)；上千条要换倒排/向量。
* 每轮检索只读派生索引，不重扫日记（生产实测 0.7s -> ~0.17s）。
* 注入件 6000 字符 / 融合件 8000 字符 / 想起段 700 字符，全部可配。
"""
from __future__ import annotations

from datetime import date

from .config import Config
from .store import Store


def collect(store: Store, cfg: Config, today: date | None = None,
            aff=None, cache: dict | None = None) -> dict:
    """跑一遍回收，返回前后对照报告。可安全重复执行（幂等）。"""
    today = today or date.today()
    before = store.stats()
    report: dict = {"date": today.isoformat(), "actions": []}

    # 1) 浅层 -> 事件层：live/ 并进 daily/ 后删 live 文件
    merged = store.merge_live(today.isoformat())
    report["actions"].append({"step": "merge_live", **merged})

    # 2) 备份轮转：同 tag 超过上限的删最旧（store.backup 每次写入时也会做）
    cap = cfg.integer("storage.max_backups", 10)
    removed = 0
    for stem in sorted({p.name.split(".bak-")[0] for p in (store.root / "backups").glob("*.bak-*")}):
        olds = sorted((store.root / "backups").glob(f"{stem}.bak-*"))
        while len(olds) > cap:
            olds.pop(0).unlink(missing_ok=True)
            removed += 1
    report["actions"].append({"step": "backup_rotate", "removed": removed, "cap": cap})

    # 3) 好感度 history 上限（每人 30 条），只留最近
    hist_cap = cfg.integer("storage.history_cap", 30)
    trimmed = 0
    if aff is not None:
        for _name, m in aff.state.get("members", {}).items():
            h = m.get("history", [])
            if len(h) > hist_cap:
                trimmed += len(h) - hist_cap
                m["history"] = h[-hist_cap:]
        if trimmed:
            aff.save()
    report["actions"].append({"step": "history_trim", "removed": trimmed, "cap": hist_cap})

    # 4) seen/self-model 上限：超过 seen_cap 删最旧 seen_trim 条
    seen_cap = cfg.integer("storage.seen_cap", 500)
    seen_trim = cfg.integer("storage.seen_trim", 100)
    schema = store.path("self", "schema.json")
    if schema.exists():
        data = store.read_json(schema, {})
        seen = data.get("seen", {}) if isinstance(data, dict) else {}
        if len(seen) > seen_cap:
            for k in list(seen)[:seen_trim]:
                seen.pop(k, None)
            store.write_json(schema, data)
            report["actions"].append({"step": "seen_trim", "removed": seen_trim})
    else:
        report["actions"].append({"step": "seen_trim", "removed": 0})

    # 5) 日志超过上限就停写（生产规则），这里截断尾部并留痕，避免无限增长
    log_cap = cfg.integer("storage.log_max_bytes", 200000)
    logs_trimmed = []
    for p in store.root.rglob("*.log"):
        try:
            if p.stat().st_size > log_cap:
                text = p.read_text(encoding="utf-8", errors="replace")
                p.write_text(text[-log_cap:] + "\n[GC] 日志达到上限，已保留最近部分\n",
                             encoding="utf-8")
                logs_trimmed.append(str(p.relative_to(store.root)))
        except OSError:
            continue
    report["actions"].append({"step": "log_cap", "trimmed": logs_trimmed})

    # 6) 检索缓存淘汰：会话结束/超量时清空，防每轮累积
    if cache is not None:
        n = len(cache)
        cache.clear()
        report["actions"].append({"step": "cache_clear", "removed": n})

    # 7) 注入件预算体检：只报告，不擅自重写（重写要走 render）
    budgets = []
    limit = cfg.integer("budget.context_max_chars", 6000)
    for p in (store.root / "out").glob("*.md"):
        budgets.append({"file": p.name, "chars": len(p.read_text(encoding="utf-8")),
                        "limit": limit, "ok": len(p.read_text(encoding="utf-8")) <= limit})
    report["actions"].append({"step": "budget_audit", "files": budgets})

    # 8) faded 与原始证据：明确不删
    report["actions"].append({"step": "keep_raw", "note": "raw/ 与 daily/ 只读；faded 只留在索引不渲染"})
    report["before"] = before
    report["after"] = store.stats()
    return report


def pressure_report(store: Store, cfg: Config, item_count: int) -> dict:
    """给运维看的容量/复杂度估算，便于决定何时换向量索引。"""
    idx = store.path("index", "forget-index.json")
    index_bytes = idx.stat().st_size if idx.exists() else 0
    return {
        "items": item_count,
        "noise_pairs": item_count * (item_count - 1) // 2,
        "noise_complexity": "O(M^2 * L) 字符二元组，M>1000 建议换倒排或向量",
        "index_bytes": index_bytes,
        "context_budget_chars": cfg.integer("budget.context_max_chars", 6000),
        "merged_budget_chars": cfg.integer("budget.merged_max_chars", 8000),
        "recall_budget_chars": cfg.integer("cue.recall_max_chars", 700),
        "backup_cap": cfg.integer("storage.max_backups", 10),
        "history_cap": cfg.integer("storage.history_cap", 30),
    }
