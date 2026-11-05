"""全量测试：``python -m unittest discover -s tests -v``（零第三方依赖）。

覆盖：配置一致性（防"藏参数"）、衰减公式校准、分层权限、隐私投影、
检索/巩固/生长/回收、好感度与情绪对接、端到端 quickstart。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from layered_memory import (Config, DEFAULTS, Layer, MemoryItem, MemorySystem,
                            Stage, calibrate, compute)
from layered_memory import retrieval
from layered_memory.scoring import cue_match, heuristic, noise_level

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 5)


def flat(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        if k.startswith("_"):
            continue
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flat(v, key))
        else:
            out[key] = v
    return out


class TestConfig(unittest.TestCase):
    def test_example_config_matches_defaults(self):
        """示例配置必须列出全部参数且与代码默认值一致：不隐藏关键参数。"""
        data = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        a, b = flat(DEFAULTS), flat(data)
        self.assertEqual(set(a), set(b), f"缺项={set(a)-set(b)} 多项={set(b)-set(a)}")
        for k in a:
            self.assertEqual(a[k], b[k], f"{k} 默认值与示例配置不一致")

    def test_unknown_key_detected(self):
        cfg = Config({"decay": {"tau0_typo": 1}})
        self.assertIn("decay.tau0_typo", cfg.unknown_keys())

    def test_validate_rejects_bad_values(self):
        self.assertTrue(any("p" in x for x in Config({"decay": {"p": 0}}).validate()))
        self.assertTrue(any("阈值" in x for x in Config(
            {"lifecycle": {"d_intact": 0.1, "d_episodic": 0.42}}).validate()))
        self.assertEqual(Config().validate(), [])


class TestDecay(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()

    def test_calibration_cases(self):
        cal = calibrate(self.cfg)
        self.assertAlmostEqual(cal["heavy"]["D"], 0.18, delta=0.02)
        self.assertAlmostEqual(cal["heavy"]["S"], 0.88, delta=0.02)
        self.assertAlmostEqual(cal["heavy"]["E"], 0.49, delta=0.03)
        self.assertAlmostEqual(cal["heavy"]["D_after_1y"], 0.50, delta=0.02)
        self.assertLess(cal["light"]["D"], 0.05)
        self.assertAlmostEqual(cal["heavy"]["half_life_days"], cal["heavy"]["tau_days"], delta=0.05)

    def test_rule_never_fades(self):
        it = MemoryItem.create("说话别带括号旁白", day="2020-01-01", kind="rule")
        m = compute(it, TODAY, self.cfg)
        self.assertEqual(m.stage, Stage.RULE)
        self.assertEqual(m.D, 1.0)

    def test_power_law_has_long_tail(self):
        """用「一年后 D=0.5」反解出的指数速率，五年后必然归零；幂律必须留下长尾。"""
        import math
        it = MemoryItem.create("校准", day="2021-11-05", S=0.95, E=0.9, D0=0.9)
        m = compute(it, TODAY, self.cfg)
        lam_fitted = -math.log(0.5 / 0.9) / 365        # 拟合前 1 年的指数衰减率
        exp_5y = 0.9 * math.exp(-lam_fitted * 1825)
        self.assertLess(exp_5y, 0.06)
        self.assertGreater(m.D, exp_5y * 2)
        self.assertGreater(m.D, 0.1)

    def test_p_outlives_details(self):
        it = MemoryItem.create("旧事", day="2021-11-05", S=0.95, E=0.9,
                               D0=0.9, P="他其实在硬撑")
        m = compute(it, TODAY, self.cfg)
        self.assertTrue(m.P_alive)
        self.assertLess(m.D, 0.3)

    def test_lifecycle_boundaries(self):
        for d, stage in ((0.72, "intact"), (0.42, "episodic"), (0.15, "fragment")):
            it = MemoryItem.create("x", day=TODAY.isoformat(), D0=d, S=0.1, E=0.1)
            # 当天 t=0，D == D0
            self.assertEqual(compute(it, TODAY, self.cfg).stage.value, stage)
        it = MemoryItem.create("x", day="2020-01-01", S=0.1, E=0.1, D0=0.1)
        self.assertEqual(compute(it, TODAY, self.cfg).stage, Stage.FADED)

    def test_heuristic_and_noise(self):
        a = MemoryItem.create("随便聊了两句")
        b = MemoryItem.create("11-03 18:30 在 11-605 考试，别忘了，很重要！")
        ha, hb = heuristic(a, self.cfg), heuristic(b, self.cfg)
        self.assertGreater(hb["S"], ha["S"])
        self.assertGreater(hb["D0"], ha["D0"])
        similar = MemoryItem.create("面包配牛奶当早餐呀", day="2026-11-04")
        n = noise_level(MemoryItem.create("面包配牛奶当早餐"),
                        [similar, a], self.cfg)
        self.assertGreater(n, 0.5)


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.cfg = Config()
        self.items = [
            MemoryItem.create("编译期末分值：选择20填空10应用40", day="2026-11-01",
                              cues=["编译考试"], S=0.7),
            MemoryItem.create("面包配牛奶是固定早餐", day="2026-11-02", cues=["面包", "早餐"]),
        ]

    def test_manual_cue_and_threshold(self):
        self.assertAlmostEqual(cue_match("编译考试什么时候", self.items[0], self.cfg), 0.9)
        res = retrieval.recall(self.items, "编译考试", self.cfg, today=TODAY)
        self.assertEqual(res["total"], 1)
        self.assertIn("编译", res["block"])
        self.assertTrue(res["block"].startswith("## 想起"))

    def test_empty_result_is_empty(self):
        res = retrieval.recall(self.items, "今天天气怎么样", self.cfg, today=TODAY)
        self.assertEqual(res["block"], "")
        self.assertEqual(res["total"], 0)

    def test_recall_budget(self):
        many = [MemoryItem.create(f"面包配牛奶第{i}次记录", day="2026-11-02",
                                 cues=["面包"]) for i in range(30)]
        res = retrieval.recall(many, "面包", self.cfg, today=TODAY, k=30)
        self.assertLessEqual(len(res["block"]), self.cfg.integer("cue.recall_max_chars"))


class TestSystem(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemorySystem(self.tmp.name, config=ROOT / "config.example.json",
                                today=TODAY)
        self.mem.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_build_and_validate(self):
        self.assertFalse(self.mem.validate_entry("嗯")["ok"])
        self.assertFalse(self.mem.validate_entry("他说了句挺重要的话",
                                                 quote="编的原话", raw_text="真实原文")["ok"])
        r = self.mem.ingest("他半夜说别送了，我觉得他在硬撑",
                            tags={"S": 0.9, "E": 0.7, "D": 0.9},
                            cues=["别送了"], P="他其实在硬撑", who=["xiaobei"])
        self.assertTrue(r["ok"])
        self.assertEqual(len(self.mem.build()), 1)
        self.assertEqual(self.mem.store.load_index()[r["id"]]["P"], "他其实在硬撑")

    def test_dedup_same_item(self):
        self.mem.ingest("同一件事在一天里被说了两遍，内容完全一样", tags={"S": 0.6})
        self.mem.ingest("同一件事在一天里被说了两遍，内容完全一样", tags={"S": 0.6})
        self.assertEqual(len(self.mem.build()), 1)

    def test_facts_value_never_leaks_to_group(self):
        self.mem.store.project_facts("private")
        group = self.mem.store.project_facts("group")
        self.assertNotIn("（示例：不填真实值）", group)
        self.assertIn("知道，不在这儿报", group)
        self.assertNotIn("住址", group)     # secret 连字段名都不出现

    def test_group_render_skips_growth_layer(self):
        self.mem.store.write_self({"base_feel": ["怕被丢下"], "feel": [],
                                   "sediment": [], "curiosity": [], "skills": [],
                                   "state": {"mood": "平静"}})
        r = self.mem.render(channel="group")
        self.assertNotIn("growth", r["sections"])
        self.assertNotIn("怕被丢下", r["text"])
        p = self.mem.render(channel="private")
        self.assertIn("growth", p["sections"])

    def test_touch_consolidates_without_touching_raw(self):
        r = self.mem.ingest("一件值得记住的事", tags={"S": 0.8, "D": 0.9},
                            folder="daily")
        self.mem.build()
        raw_before = self.mem.store.path("daily", f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
        before = self.mem.audit()[0]
        out = self.mem.touch(r["id"])
        self.assertTrue(out["ok"])
        self.assertGreater(out["after"]["C"], before["C"])
        self.assertGreaterEqual(out["after"]["D"], before["D"])
        raw_after = self.mem.store.path("daily", f"{TODAY.isoformat()}.md").read_text(encoding="utf-8")
        self.assertEqual(raw_before, raw_after)   # 原始证据不动

    def test_budget_drops_cues_first(self):
        from datetime import timedelta
        self.mem.store.write_self({"base_feel": ["怕被丢下"], "feel": [],
                                   "sediment": [], "curiosity": [], "skills": [],
                                   "state": {"mood": "平静"}})
        for i in range(40):
            self.mem.ingest(f"第{i}件事，细节挺多但不重要，还牵扯到不少别的人和地点",
                            tags={"S": 0.4, "D": 0.8}, cues=[f"线索{i}"],
                            folder="daily")
        # 用小预算逼出裁剪；换个 Config 但复用同一数据目录
        small = MemorySystem(self.tmp.name,
                             config=Config({"budget": {"context_max_chars": 900}}),
                             today=TODAY)
        small.build()
        r = small.render(channel="private", query="线索1")
        self.assertLessEqual(r["used"], r["budget"] + 60)
        self.assertIn("cues", r["dropped"])        # 触景是最先被砍的
        self.assertNotIn("growth", r["dropped"])   # 主体段保住

    def test_growth_candidates_are_not_auto_written(self):
        from datetime import timedelta
        for i in range(6):
            day = (date(2026, 9, 1) + timedelta(days=4 * i)).isoformat()
            self.mem.ingest(f"第{i}次又没吃早饭，说是不赶时间", tags={"S": 0.7, "E": 0.5},
                            cues=["早餐"], day=day, folder="daily")
        self.mem.build()
        cand = self.mem.deposit()
        self.assertGreater(len(cand["edges"]), 0)
        self.assertEqual(self.mem.store.read_self().get("feel", []), [])
        added = self.mem.approve_candidates(
            sediment_texts={"早餐": "他早上经常顾不上吃饭"})
        self.assertEqual(added["sediment"], 1)

    def test_time_layers_are_separate_from_abstraction_layers(self):
        self.mem.ingest("今天刚说的一件小事，还没忘", day="2026-11-05", folder="daily")
        self.mem.ingest("十天前说过的一件小事，有点模糊了", day="2026-10-26", folder="daily")
        self.mem.ingest("两个月前说过的一件小事，只剩一句话", day="2026-09-01", folder="daily")
        self.mem.build()
        t = self.mem.time_layers()
        self.assertIn("刚说的", t["L1_recent"])
        self.assertIn("十天前", t["L2_weekly"])
        self.assertIn("两个月前", t["L3_oneline"])
        self.assertIn("编号不通用", t["note"])

    def test_gc_is_idempotent_and_keeps_raw(self):
        self.mem.ingest("这是一条写在浅层的当日增量记忆", folder="live")
        self.mem.build()
        r1 = self.mem.gc()
        r2 = self.mem.gc()
        self.assertTrue(r1["actions"][0]["removed"])
        self.assertFalse(r2["actions"][0]["removed"])
        self.assertTrue(self.mem.store.path("daily", f"{TODAY.isoformat()}.md").exists())

    def test_self_test_all_pass(self):
        r = self.mem.self_test()
        self.assertEqual(r["passed"], r["total"],
                         str([x for x in r["results"] if not x["ok"]]))

    def test_pressure_report(self):
        self.mem.build()
        p = self.mem.pressure()
        self.assertIn("noise_pairs", p)
        self.assertEqual(p["context_budget_chars"], 6000)


class TestAffect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mem = MemorySystem(self.tmp.name, today=TODAY)
        self.mem.init()

    def tearDown(self):
        self.tmp.cleanup()

    def test_anti_spam_caps(self):
        r1 = self.mem.aff.record("A", {"event": "talk"})
        r2 = self.mem.aff.record("A", {"event": "talk"})
        self.assertEqual(r1["applied"], 2)
        self.assertIn("冷却", r2["rejected"])
        self.assertLessEqual(self.mem.aff.state["members"]["A"]["score"], 52)

    def test_saturation_and_daily_cap(self):
        self.mem.aff.state["members"]["B"] = {
            "score": 96, "tier": "自家人", "lastChangeAt": 0, "lastEventAt": 0,
            "day": "", "dayNet": 0, "dayCount": 0, "history": []}
        r = self.mem.aff.record("B", {"event": "talk"})
        self.assertEqual(r["applied"], 1)       # 95+ 正增益打折到 1
        self.assertEqual(len(self.mem.aff.state["members"]["B"]["history"]), 1)

    def test_hysteresis(self):
        self.assertEqual(self.mem.aff.resolve_tier(54, "朋友"), "朋友")   # 差 1 分不跳档
        self.assertEqual(self.mem.aff.resolve_tier(50, "朋友"), "熟人")   # 跌穿 3 分才降

    def test_decay_floor(self):
        self.mem.aff.record("C", {"event": "talk"})
        m = self.mem.aff.state["members"]["C"]
        m["lastChangeAt"] = 0
        self.mem.aff.decay(now=30 * 86400000)
        self.assertGreaterEqual(m["score"], 35)

    def test_emotion_half_life_and_baseline(self):
        import time
        now = int(time.time() * 1000)
        self.mem.emo.fire("insult", now=now, min_until_min=15)
        self.assertEqual(len(self.mem.emo.state["active"]), 1)
        self.mem.emo.tick(now=now + 3600_000)     # 1 小时后 = 6 个 10 分钟半衰期
        self.assertEqual(len(self.mem.emo.state["active"]), 0)
        self.mem.aff.state["members"]["D"] = {
            "score": 60, "tier": "朋友", "lastChangeAt": now, "lastEventAt": 0,
            "day": "", "dayNet": 0, "dayCount": 0, "history": []}
        base = self.mem.emo.baseline(self.mem.aff, "D")
        self.assertEqual(base["emotion"], "friendly")   # 朋友档

    def test_bridge_signals(self):
        self.mem.ingest("今天和他聊了整整一小时，挺久的", who=["xiaobei"], val=-0.4, tags={"E": 0.8})
        self.mem.build()
        sig = self.mem.memory_signals()
        self.assertIn("xiaobei", sig["people"])
        self.assertEqual(sig["people"]["xiaobei"]["sum_V"], -0.4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
