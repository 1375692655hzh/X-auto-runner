#!/usr/bin/env python3
# manual_priority_test.py — 人工任务排队绝对优先回归（2026-11-10 运营拍板）
# 既有四级：post(0) > reply/comment(1) > like/follow(2) > browse(3)；
# 新增：origin=manual → -1 级，到队即插队，排一切 auto 前面。
# 注：execute() 同步等结果，须多线程提交制造排队窗口。
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-prio-")
os.environ["XAR_STATE"] = TMP
os.environ["XAR_SIMULATE"] = "0"
sys.path.insert(0, str(Path(__file__).parents[1]))

from ledger import Ledger, Intents         # noqa: E402
import server                              # noqa: E402

assert server.SIMULATE is False

led = Ledger("wcixot", caps={"post": 50, "like": 50, "follow": 50})
gw = server.Gateway(led, Intents(led), writes_enabled=True,
                    approval={"default": "none"})

TEXT = "优先级验证正文：台积电10月营收超预期，AI订单满载，本文用于排队顺序断言。"
order = []
gate = threading.Event()


def _slow_perform(intent):
    iid = intent["intent_id"]
    order.append(iid)
    if iid == "hold-1":
        held.set()                 # 工位已抓住 hold（确定性同步点）
        gate.wait(5)               # 占住工位，制造排队窗口
    gw.intents.put(iid, intent["kind"], intent)
    gw.intents.mark_intent(iid)
    return {"intent_id": iid, "status": "done"}


gw._perform = _slow_perform
held = threading.Event()


def submit(iid, **kw):
    payload = {"intent_id": iid, **kw}
    th = threading.Thread(target=lambda: gw.execute(payload), daemon=True)
    th.start()
    time.sleep(0.15)               # 确保按此顺序入队
    return th


submit("hold-1", kind="post", text=TEXT)
assert held.wait(3), "hold 未上工位"
submit("auto-like-1", kind="like", status_id="123")          # P2
submit("auto-fo-1", kind="follow", target_handle="a")        # P2
submit("man-fo-1", kind="follow", target_handle="b",
       origin="manual", rationale="运营者指令关注")            # -1 插队
time.sleep(0.3)
gate.set()
time.sleep(0.8)

assert order[0] == "hold-1", order
assert order.index("man-fo-1") < order.index("auto-like-1"), order
assert order.index("man-fo-1") < order.index("auto-fo-1"), order
print(f"PASS 1: 执行顺序 {order} —— manual(-1) 插队于 auto like/follow(2) 之前")

assert server.PRIORITY["post"] == 0 and server.PRIORITY["reply"] == 1 \
       and server.PRIORITY["like"] == 2 and server.PRIORITY["browse"] == 3
print("PASS 2: 既有四级（post0>互动1>赞关2>browse3）不变")

print("人工任务排队绝对优先测试通过 ✅")
