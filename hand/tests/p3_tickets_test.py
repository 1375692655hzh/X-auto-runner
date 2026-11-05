#!/usr/bin/env python3
# p3_tickets_test.py — 票据生命周期单元测试（SIMULATE，直接测 Tickets/Ledger 联动）
# 覆盖：开票预留/审批执行/驳回释放/过期清扫/终态不可复活/幂等冲突
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["XAR_SIMULATE"] = "1"
os.environ["XAR_STATE"] = tempfile.mkdtemp(prefix="xar-p3-")
sys.path.insert(0, "/opt/xar/hand")

from ledger import Ledger  # noqa: E402
from tickets import Tickets, TicketError  # noqa: E402

led = Ledger("wcixot", caps={"like": 5, "reply": 3})
tk = Tickets(led)

# 1 开票+预留
assert led.cap_left("like") == 5
t1 = tk.create("like", {"intent_id": "t-1", "status_id": "123"}, "测试理由")
assert t1["status"] == "pending_review"
assert led.cap_left("like") == 4 and led.reserved("like") == 1
assert tk.create("like", {"intent_id": "t-1"}, "") if False else True
try:
    tk.create("like", {"intent_id": "t-1"}, "")
    raise SystemExit("FAIL: 幂等冲突未拦截")
except TicketError:
    pass
print("PASS 1: 开票预留 + 幂等冲突拒")

# 2 审批→执行→完成
tk.decide("t-1", True, by="tester")
doc = tk.claim_for_execution("t-1")
assert doc["status"] == "executing"
tk.finish("t-1", "done", "ok")
assert tk.get("t-1")["status"] == "done"
assert led.cap_left("like") == 5  # 2026-10-26 新语义：终局即释放预留（used 由网关 mark_intent 记，单测无网关）
try:
    tk.decide("t-1", False, by="x")  # 终态不可复活
    raise SystemExit("FAIL: 终态可再审")
except TicketError:
    pass
print("PASS 2: 审批→执行→done；终态不可再审")

# 3 驳回→释放
t2 = tk.create("like", {"intent_id": "t-2", "status_id": "456"}, "r")
assert led.cap_left("like") == 4
tk.decide("t-2", False, by="tester")
assert tk.get("t-2")["status"] == "rejected"
assert led.cap_left("like") == 5  # 预留已释放
print("PASS 3: 驳回即释放预留")

# 4 过期清扫→释放
t3 = tk.create("like", {"intent_id": "t-3", "status_id": "789"}, "r")
assert led.cap_left("like") == 4
p = tk._path("t-3")
doc = json.loads(p.read_text())
doc["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
p.write_text(json.dumps(doc), encoding="utf-8")
n = tk.sweep_expired()
assert n == 1 and tk.get("t-3")["status"] == "expired"
assert led.cap_left("like") == 5
print("PASS 4: 过期清扫 + 释放")

# 5 approved 但过期 → claim 拒
t4 = tk.create("like", {"intent_id": "t-4", "status_id": "000"}, "r")
tk.decide("t-4", True, by="tester")
p = tk._path("t-4")
doc = json.loads(p.read_text())
doc["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
p.write_text(json.dumps(doc), encoding="utf-8")
assert tk.claim_for_execution("t-4") is None
assert tk.get("t-4")["status"] == "expired"  # claim 内部过期即 expire+释放
assert led.cap_left("like") == 5
print("PASS 5: approved 过期不可执行 + 释放")

# 6 改稿 approve
t5 = tk.create("reply", {"intent_id": "t-5", "text": "原稿"}, "r")
tk.decide("t-5", True, by="tester", edited_text="改后稿")
assert tk.get("t-5")["payload"]["text"] == "改后稿" and tk.get("t-5")["edited"]
print("PASS 6: 改后发（edited 标记）")

# 7 executing 僵票过期 → 清扫释放（2026-10-26 首演 1026b 事故回归）
t6 = tk.create("like", {"intent_id": "t-6", "status_id": "111"}, "r")
tk.decide("t-6", True, by="tester")
assert tk.claim_for_execution("t-6")["status"] == "executing"
assert led.cap_left("like") == 4
p = tk._path("t-6")
doc = json.loads(p.read_text())
doc["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
p.write_text(json.dumps(doc), encoding="utf-8")
assert tk.sweep_expired() == 1 and tk.get("t-6")["status"] == "expired"
assert led.cap_left("like") == 5
print("PASS 7: executing 僵票过期清扫释放")

shutil.rmtree(os.environ["XAR_STATE"], ignore_errors=True)
print("P3 票据生命周期测试全部通过 ✅")
