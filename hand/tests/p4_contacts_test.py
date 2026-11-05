#!/usr/bin/env python3
# p4_contacts_test.py — P4-4 互动池+模板库单元测试（隔离状态目录）
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["XAR_SIMULATE"] = "1"
os.environ["XAR_STATE"] = tempfile.mkdtemp(prefix="xar-p4-")
sys.path.insert(0, "/opt/xar/hand")

from contacts import Contacts, is_greeting  # noqa: E402
from templates import Templates  # noqa: E402
from quality import check  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("四档默认与设置")
def _():
    c = Contacts("t1")
    assert c.tier("someone") == "normal"
    c.set_tier("someone", "bigv", "财报拆解号")
    assert c.tier("someone") == "bigv"
    c.set_tier("someone", "blacklist", "杠精")
    assert c.tier("@Someone") == "blacklist"   # @前缀/大小写归一


@case("熟络度计数轴")
def _():
    c = Contacts("t2")
    assert c.familiar("a") == "陌生"
    c.record_their("a")
    c.record_our("a", "reply")
    assert c.familiar("a") == "眼熟"
    for _ in range(3):
        c.record_our("a", "comment")
    assert c.familiar("a") == "老熟人"


@case("关系槽注入：四档+熟络度+称呼配额提示")
def _():
    c = Contacts("t3")
    slot = c.relation_slot("b")
    assert "正常网友" in slot and "陌生" in slot
    c.set_tier("b", "mutual")
    c.record_our("b", "reply")
    c.record_our("b", "reply")
    slot = c.relation_slot("b", salutation_cap=3)
    assert "互关" in slot and "眼熟" in slot and "配额" not in slot
    c.salutation_used("谢谢老师指点", 3)
    c.salutation_used("老哥说得对", 3)
    c.salutation_used("大佬牛", 3)
    assert c.salutation_left(3) == 0
    slot = c.relation_slot("b", salutation_cap=3)
    assert "配额已用完" in slot


@case("称呼词计数只认称呼词")
def _():
    c = Contacts("t4")
    c.salutation_used("今天纳指跌了 2%")   # 无称呼词
    assert c.salutation_left(3) == 3


@case("对账回填：done intents 投影 + 幂等")
def _():
    d = Path(os.environ["XAR_STATE"]) / "intents"
    d.mkdir(exist_ok=True)
    (d / "i1.json").write_text(json.dumps({
        "status": "done", "kind": "reply", "created_at": "2026-10-27T01:00:00",
        "payload": {"target_handle": "@FanA", "text": "x"}}), encoding="utf-8")
    (d / "i2.json").write_text(json.dumps({
        "status": "done", "kind": "like", "created_at": "2026-10-27T02:00:00",
        "payload": {"target_handle": "FanB"}}), encoding="utf-8")
    (d / "i3.json").write_text(json.dumps({
        "status": "planned", "kind": "reply", "created_at": "2026-10-27T03:00:00",
        "payload": {"target_handle": "FanC"}}), encoding="utf-8")
    c = Contacts("t5")
    n = c.reconcile()
    assert n == 2, n
    assert c.interaction_count("fana") == 1
    assert c.interaction_count("fanc") == 0     # 非 done 不计
    assert c.reconcile() == 0                    # 幂等


@case("互关自动判定：双向关注→mutual；取关→normal")
def _():
    c = Contacts("t6")
    c._p("d")["they_follow"] = True
    c.save()
    c.set_follow("d", True)
    assert c.tier("d") == "mutual"
    c.set_follow("d", False)
    assert c.tier("d") == "normal"


@case("招呼类判定：词表驱动，只放行无歧义纯应答")
def _():
    for t in ("1", "111", "来了", "来咯～", "来了来了", "来啦", "嗯", "666",
              "hello", "gm", "👍", "👍🔥", "你好", "👋👋", "老板"):
        assert is_greeting(t), t
    for t in ("", "   ", "这分析到位", "1万亿刀成交额怎么算", "老师我亏麻了", "翻了几倍？"):
        assert not is_greeting(t), t


@case("模板库 v2：句号收尾/严格档过门/双维度不重复/mark_used 才记账")
def _():
    tp = Templates("t7")
    seen = set()
    for _ in range(50):
        t = tp.next_text("fanA", "眼熟", 3)
        assert t is not None and t.endswith("。"), t
        assert t not in seen, f"重复: {t}"
        seen.add(t)
        r = check(t)
        assert r["ok"], (t, r)
        tp.mark_used(t, "fanA")          # 票据成立后记账
    assert len(seen) == 50
    assert tp.used_count() == 50


@case("模板库 v2：熟络度分档 + 称呼配额过滤 + 同 handle 去重 + 耗尽降级 None")
def _():
    tp = Templates("t8")
    # 配额耗尽时身份标签不带受配额词（兄弟）
    for _ in range(20):
        t = tp.next_text("fanB", "老熟人", 0)
        assert t is not None and "兄弟" not in t, t
        tp.mark_used(t, "fanB")
    # 同 handle 去重：fanB 用过的句子不再给 fanB
    used_b = {u["text"] for u in tp.data["per_handle"]["fanb"]}
    t = tp.next_text("fanB", "老熟人", 3)
    if t is not None:
        assert t not in used_b, "同 handle 既视感"
    # 陌生档不抽兄弟
    t = tp.next_text("fanC", "陌生", 3)
    assert t is not None and "兄弟" not in t
    # 耗尽：枚举全部可能组合（钉 hour=12 掐掉时段加词）灌入 used → None（降级信号）
    import templates as TM
    tp2 = Templates("t9")
    for o in TM.OPENERS:
        for i in ("朋友", "Mutuals", ""):
            for tl in TM.TAILS:
                s = (o + ("，" + i if i else "") + tl).strip()
                if not s.endswith("。"):
                    s += "。"
                tp2.mark_used(s, "fanD")
    assert tp2.next_text("fanD", "陌生", 3, hour=12) is None


def main():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}: {e}")
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    shutil.rmtree(os.environ["XAR_STATE"], ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
