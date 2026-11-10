#!/usr/bin/env python3
# approval_none_test.py — approval=none 路由回归（2026-11-10 停摆修复）
# 覆盖：none → 建票+自动批准+同链执行；default required → 仍待审；幂等冲突。
# 背景：开跑当晚 8 个自动回关 4ms 全败「票据不可执行」，因 none 路径未建票。
import os
import shutil
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-none-")
os.environ["XAR_STATE"] = TMP
os.environ["XAR_SIMULATE"] = "0"          # 需要真实票据路由段
sys.path.insert(0, str(Path(__file__).parents[1]))

from ledger import Ledger, Intents         # noqa: E402
import server                           # noqa: E402
from server import Deny                 # noqa: E402

assert server.SIMULATE is False, "本测试要求 SIMULATE=0"

led = Ledger("wcixot", caps={"follow": 5, "reply": 3})
gw = server.Gateway(led, Intents(led), writes_enabled=True,
                    approval={"default": "required", "follow": "none"})
# stub 掉浏览器执行段：复刻真实 _perform 的幂等检查，不触工位
def _stub_perform(intent):
    try:
        gw.intents.put(intent["intent_id"], intent["kind"], intent)
    except ValueError:
        raise server.Deny("IDEMPOTENCY_CONFLICT", f"幂等键已存在: {intent['intent_id']}")
    gw.intents.mark_intent(intent["intent_id"])
    gw.intents.mark(intent["intent_id"], "done", "")
    return {"intent_id": intent["intent_id"], "status": "done", "_stub": True}


gw._perform = _stub_perform

# 1 approval=none：建票 + 自动批准 + 执行
res = gw.execute({"kind": "follow", "intent_id": "t-none-1",
                  "target_handle": "someone", "rationale": "免审回关"})
assert res.get("status") == "done", res
t = gw.tickets.get("t-none-1")
assert t is not None and t["status"] == "approved", t
assert t.get("decided_by") == "policy:approval-none", t
print("PASS 1: approval=none 建票+自动批准+执行")

# 2 幂等：同 intent_id 再来 → 拒
try:
    gw.execute({"kind": "follow", "intent_id": "t-none-1", "target_handle": "someone"})
    raise SystemExit("FAIL: 幂等冲突未拦截")
except Deny as d:
    assert "IDEMPOTENCY" in str(d.body.get("code") or d), d.body
print("PASS 2: 免审路径幂等冲突仍拒")

# 3 default required：reply 仍回待审（不被 none 传染）
led2 = Ledger("wcixot", caps={"reply": 3})
gw2 = server.Gateway(led2, Intents(led2), writes_enabled=True,
                     approval={"default": "required", "follow": "none"})
r2 = gw2.execute({"kind": "reply", "intent_id": "t-none-2",
                  "text": "感谢补充，这个数据口径确实容易看错，已核对修正。", "status_id": "9988776655"})
assert r2.get("status") == "pending_review", r2
assert gw2.tickets.get("t-none-2")["status"] == "pending_review"
print("PASS 3: default=required 不受影响")

shutil.rmtree(TMP, ignore_errors=True)
print("approval=none 路由测试全部通过 ✅")
