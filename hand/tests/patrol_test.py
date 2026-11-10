#!/usr/bin/env python3
# patrol_test.py — 互关巡逻引擎回归（2026-11-11 运营立项）
# 覆盖：候选过滤（黑名单/已关注/冷却/本人/去重）、决策路由解析（cap/fail-open）、
# engage 质量门（模板池成员放行/池外拒/LLM casual 档）、set_we_follow 升档。
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-pt-")
os.environ["XAR_STATE"] = TMP
os.environ["XAR_SIMULATE"] = "0"
sys.path.insert(0, str(Path(__file__).parents[1]))

import patrol as PT                    # noqa: E402
from contacts import Contacts          # noqa: E402
from ledger import Ledger, Intents     # noqa: E402
import server                          # noqa: E402
from server import Deny                # noqa: E402

# 1 候选过滤
pool = Contacts("wcixot")
pool.set_we_follow("already", True)                      # 已关注 → 跳过
pool.set_tier("badguy", "blacklist")                     # 黑名单 → 跳过
st = {"next_beat_at": "", "engaged": {"cooldown1": datetime.now().isoformat()},
      "kw_idx": 0}
posts = [
    {"author": "wcixot", "id": "111", "text": "本人帖"},                    # 本人
    {"author": "already", "id": "112", "text": "已关注"},                   # 已关注
    {"author": "badguy", "id": "113", "text": "黑名单"},                    # 黑名单
    {"author": "cooldown1", "id": "114", "text": "冷却中"},                 # 7天内
    {"author": "good1", "id": "115", "text": "互关走起，我主要聊美股AI"},
    {"author": "good1", "id": "116", "text": "同作者第二帖"},               # 去重
    {"author": "good2", "id": "117", "text": "台积电资本开支拆解长文"},
    {"author": "", "id": "118", "text": "无名"},
]
cands = PT.filter_candidates(posts, pool, st, "wcixot", 7)
assert [c["author"] for c in cands] == ["good1", "good2"], cands
print("PASS 1: 候选过滤（本人/已关注/黑名单/冷却/去重）")

# 2 决策路由解析：llm 上限截断 + 缺项 fail-open template
routes = PT.parse_routes('{"routes":[{"i":0,"r":"llm"},{"i":1,"r":"llm"},{"i":2,"r":"llm"},{"i":3,"r":"llm"}]}', 5, 2)
assert routes == ["llm", "llm", "template", "template", "template"], routes
assert PT.parse_routes("这不是JSON", 3, 2) == ["template"] * 3
print("PASS 2: 路由解析（llm_per_beat 截断 + fail-open 模板）")

# 3 engage 质量门：模板池成员放行 / 池外模板拒 / LLM casual 档过与拒
led = Ledger("wcixot", caps={"comment": 80})
gw = server.Gateway(led, Intents(led), writes_enabled=True)
server._ENGAGE_POOL_CACHE.update({"mtime": 0, "pool": set(PT.DEFAULTS["engage_pool"])})
import unittest.mock as _mk
with _mk.patch.object(server.Gateway, "_history_texts", return_value=[]):
    out = gw._quality_gate("comment", {"kind": "comment", "engage": "template",
                                       "text": "浇一浇", "intent_id": "x"})
    assert out["text"] == "浇一浇"
    try:
        gw._quality_gate("comment", {"kind": "comment", "engage": "template",
                                     "text": "1", "intent_id": "x"})
        raise SystemExit("FAIL: 池外模板未拒")
    except Deny:
        pass
    ok = gw._quality_gate("comment", {"kind": "comment", "engage": "llm",
                                      "text": "互关啦，这篇对台积电资本开支的拆解很扎实，学习。",
                                      "intent_id": "x"})
    assert ok["text"]
    try:
        gw._quality_gate("comment", {"kind": "comment", "engage": "llm",
                                     "text": "好的", "intent_id": "x"})   # 过短
        raise SystemExit("FAIL: 过短LLM评论未拒")
    except Deny:
        pass
print("PASS 3: engage 质量门（池内放行/池外拒/LLM档长度规则）")

# 4 set_we_follow：双方确认升 mutual
pool2 = Contacts("wcixot")
pool2.set_they_follow("friend1", True)
pool2.set_we_follow("friend1", True)
assert pool2.tier("friend1") == "mutual"
print("PASS 4: set_we_follow 双向确认升互关档")

print("互关巡逻引擎测试全部通过 ✅")
