#!/usr/bin/env python3
# audit_digest_test.py — 审计日聚合回归（阶段二运营研究环第一环）
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-dg-")
os.environ["XAR_STATE"] = TMP
sys.path.insert(0, str(Path(__file__).parents[1]))

from ledger import STATE_DIR              # noqa: E402
import audit_digest as AD                 # noqa: E402

DAY = datetime.now().strftime("%Y-%m-%d")
now = datetime.now(timezone.utc).isoformat()

# 造数：判定器审计 + intents 产出（manual / chain:deep / other）
logf = STATE_DIR / f"log-{DAY[:7]}.jsonl"
rows = [
    {"at": now, "task": "tickers", "action": "directScan", "note": "TSM:named;AAPL:named"},
    {"at": now, "task": "tickers", "action": "chainWhy", "note": "ASML:supplier/扩产带动设备订单"},
    {"at": now, "task": "tickers", "action": "rowDropped", "note": "智谱AI:非白名单;某私募:非白名单"},
    {"at": now, "task": "tickers", "action": "rowDropped", "note": "智谱AI:非白名单;AMAT:why超长"},
    {"at": now, "task": "tickers", "action": "verifyApplied", "note": "drop VST:素材无电力信号"},
    {"at": now, "task": "other", "action": "chat"},
]
logf.parent.mkdir(parents=True, exist_ok=True)
with logf.open("a", encoding="utf-8") as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

ints = STATE_DIR / "intents"
ints.mkdir(parents=True, exist_ok=True)
docs = [
    ("m1", {"kind": "post", "payload": {"origin": "manual"}, "created_at": now}),
    ("d1", {"kind": "post", "payload": {"origin": "chain:deep"}, "created_at": now}),
    ("d2", {"kind": "post", "payload": {"origin": "chain:deep"}, "created_at": now}),
    ("o1", {"kind": "follow", "payload": {}, "created_at": now}),
    ("old", {"kind": "post", "payload": {"origin": "chain:deep"}, "created_at": "2020-01-01"}),
]
for iid, d in docs:
    (ints / f"{iid}.json").write_text(json.dumps(d), encoding="utf-8")

# 1 ticker_digest：计数 + topN + 抽样
td = AD.ticker_digest()
assert td["counts"]["rowDropped"] == 2 and td["counts"]["chainWhy"] == 1
assert td["dropped_top"][0] == ("智谱AI", 2), td["dropped_top"]
assert any("ASML" in w for w in td["chainWhy_sample"])
print("PASS 1: 判定器计数/白名单候选TOP/why抽样")

# 2 output_digest：按来源分组，跨日剔除
od = AD.output_digest()
assert od["manual"] == {"post": 1}
assert od["chain"]["deep"] == {"post": 2}
assert od["other"] == {"follow": 1}
print("PASS 2: 产出按 manual/chain:deep/other 分组（旧数据剔除）")

# 3 text_block：文本拼装
tb = AD.text_block()
assert "今日产出" in tb and "deep:post×2" in tb and "人工:post×1" in tb
assert "白名单增补候选" in tb and "智谱AI(2)" in tb and "why抽样" in tb
print("PASS 3: 日报文本块拼装（无数据段自动省略见下）")

# 4 空日不炸、返回空块
empty = AD.text_block(day="1999-01-01")
assert "今日产出" not in empty and "标的判定器" not in empty
print("PASS 4: 空数据安全")

print("审计日聚合测试全部通过 ✅")
