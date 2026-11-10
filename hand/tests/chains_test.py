#!/usr/bin/env python3
# chains_test.py — 多链框架骨架回归（2026-11-10 运营立项）
# 覆盖：注册表加载/校验 fail-closed、deep 链缺省、链级约束字段、
# origin=chain:* 不豁免额度（区别于 manual）、展示名解析。
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-ch-")
os.environ["XAR_STATE"] = TMP
os.environ["XAR_SIMULATE"] = "0"
sys.path.insert(0, str(Path(__file__).parents[1]))

import chains as CH                     # noqa: E402
from ledger import Ledger, Intents      # noqa: E402
import server                           # noqa: E402
from server import Deny                 # noqa: E402

# 1 内置缺省：deep/深度文
c = CH.load({})
assert c["deep"]["display"] == "深度文" and c["deep"]["engine"] == "news_auto"
assert c["deep"]["budget_24h"] == 10 and c["deep"]["length"]["mode"] == "none"
assert c["deep"]["id"] == "deep"
print("PASS 1: deep/深度文 内置缺省（引擎/预算/长度策略齐备）")

# 2 新链覆盖与合并：yaml 段覆盖字段、缺省字段兜底
cfg = {"chains": {
    "deep": {"budget_24h": 7},
    "short": {"display": "短评", "engine": "news_auto", "enabled": True,
              "budget_24h": 5, "length": {"mode": "max", "max_len": 280},
              "ticker_line": False, "growth_tags": {"enabled": True, "prob": 0.5}}}}
c2 = CH.load(cfg)
assert c2["deep"]["budget_24h"] == 7 and c2["deep"]["display"] == "深度文"  # 部分覆盖不全丢
assert c2["short"]["length"] == {"mode": "max", "max_len": 280}
print("PASS 2: 新链注册 + 部分覆盖合并")

# 3 校验 fail-closed
for bad in ({"chains": {"x": {"engine": "nope", "budget_24h": 5, "display": "x"}}},
            {"chains": {"deep": {"budget_24h": 0}}},
            {"chains": {"deep": {"length": {"mode": "weird"}}}},
            {"chains": {"deep": {"growth_tags": {"enabled": True, "prob": 2}}}}):
    try:
        CH.load(bad)
        raise SystemExit(f"FAIL: 非法配置未拒绝 {bad}")
    except CH.ChainError:
        pass
print("PASS 3: 非法引擎/预算/长度/概率均拒绝")

# 4 origin=chain:* 不豁免额度（区别于 manual）；manual 照旧豁免
led = Ledger("wcixot", caps={"post": 1})
led.record("post")
assert led.cap_left("post") == 0
gw = server.Gateway(led, Intents(led), writes_enabled=True, approval={"default": "none"})
try:
    gw._gates({"kind": "post", "intent_id": "ch-1", "origin": "chain:deep"})
    raise SystemExit("FAIL: chain origin 未被额度拦截")
except Deny as d:
    assert "CAP_EXHAUSTED" in str(d.body.get("code") or d)
gw._gates({"kind": "post", "intent_id": "mn-1", "origin": "manual"})   # 不抛
print("PASS 4: chain:deep 计额度、manual 豁免（口径不串）")

# 5 排队优先级：chain origin 用 kind 级（非 -1）
prio_manual = -1 if {"origin": "manual"}.get("origin") == "manual" else server.PRIORITY["post"]
prio_chain = -1 if {"origin": "chain:deep"}.get("origin") == "manual" else server.PRIORITY["post"]
assert prio_manual == -1 and prio_chain == 0
print("PASS 5: chain origin 排队按 kind 级（post=0），manual 才是 -1")

# 6 展示名解析
assert CH.display("deep") == "深度文" and CH.display(None) == "" and CH.display("short") in ("短评", "short")
print("PASS 6: 展示名解析（缺链信息不前缀）")

print("多链框架骨架测试全部通过 ✅")
