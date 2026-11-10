#!/usr/bin/env python3
# manual_quota_test.py — 人工任务豁免额度回归（2026-11-10 运营拍板）
# 口径：origin=manual 的任务（发帖/回复/关注/取关）是运营者增量任务——
# ①池满（cap_left=0）时 manual 照常执行；②manual 不消耗池子；③auto 照旧受限。
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-mq-")
os.environ["XAR_STATE"] = TMP
os.environ["XAR_SIMULATE"] = "0"
sys.path.insert(0, str(Path(__file__).parents[1]))

from ledger import Ledger, Intents         # noqa: E402
import server                              # noqa: E402
from server import Deny                    # noqa: E402

assert server.SIMULATE is False

TEXT = "运营者手动加稿测试：台积电10月营收超预期，AI订单满载，本文为豁免额度验证正文。"

# 池子 post=1 且已被 auto 用满
led = Ledger("wcixot", caps={"post": 1})
led.record("post")
assert led.cap_left("post") == 0
gw = server.Gateway(led, Intents(led), writes_enabled=True,
                    approval={"default": "required", "post": "none"})


def _stub_perform(intent):
    # 复刻真实 _perform 的额度语义（manual 不查不占），不触工位
    manual = intent.get("origin") == "manual"
    kind, iid = intent["kind"], intent["intent_id"]
    if not manual and led.cap_left(kind) <= 0:
        raise Deny("CAP_EXHAUSTED", f"{kind} 滚动24h额度用尽（fail-closed）")
    gw.intents.put(iid, kind, intent)
    gw.intents.mark_intent(iid, count_quota=not manual)
    gw.intents.mark(iid, "done", "")
    return {"intent_id": iid, "status": "done", "_stub": True}


gw._perform = _stub_perform

# 1 池满时 auto 帖被拒（既有行为不回归）
try:
    gw.execute({"kind": "post", "intent_id": "t-auto-1", "text": TEXT,
                "rationale": "auto 帖"})
    raise SystemExit("FAIL: auto 未被额度拦截")
except Deny as d:
    assert "CAP_EXHAUSTED" in str(d.body.get("code") or d), d.body
print("PASS 1: 池满时 auto 照旧被拒（CAP_EXHAUSTED）")

# 2 池满时 manual 帖照常执行
res = gw.execute({"kind": "post", "intent_id": "t-manual-1", "text": TEXT,
                  "origin": "manual", "rationale": "运营者直发（QQ 人工帖）"})
assert res.get("status") == "done", res
print("PASS 2: 池满时 manual 帖照常执行")

# 3 manual 不消耗池子：used 仍为 1
assert led.used("post") == 1, led.used("post")
print("PASS 3: manual 不计入滚动池（used 仍 1）")

# 4 ledger 侧：mark_intent(count_quota=False) 只落状态不记账
led2 = Ledger("wcixot", caps={"post": 1})
ints2 = Intents(led2)
before = led2.used("post")
ints2.put("t-x", "post", {"text": TEXT})
ints2.mark_intent("t-x", count_quota=False)
assert led2.used("post") == before
doc = __import__("json").loads((ints2.dir / "t-x.json").read_text(encoding="utf-8"))
assert doc["status"] == "intent"
ints2.put("t-y", "post", {"text": TEXT})
ints2.mark_intent("t-y")
assert led2.used("post") == before + 1
print("PASS 4: mark_intent 状态照落、count_quota=False 不记账")

print("人工任务豁免额度测试全部通过 ✅")
