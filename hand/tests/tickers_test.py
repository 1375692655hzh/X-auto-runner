#!/usr/bin/env python3
# tickers_test.py — 产业链关联标的判定器 v4 回归（2026-11-10 MoA 四岗汇总落地）
# 覆盖：白名单完整性、resolve 别名、direct 扫描器（精度/统称映射/双源并集/短码$前缀）、
# 五段管线主流程（扫描direct+LLM chain+逐行丢弃）、上限10、分层标签、长度预算、
# 硬校验四闸（role/why/方向词/结果性×customer）、质检员 verdict/fail-open。
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-tk-")
os.environ["XAR_STATE"] = TMP
sys.path.insert(0, str(Path(__file__).parents[1]))

import tickers as K  # noqa: E402

# 1 白名单完整性：ticker 合法格式；别名索引无冲突
for t, (name, fame, *aliases) in K.WHITELIST.items():
    assert t.isalpha() and t.isupper() and 1 <= len(t) <= 5, t
    assert 1 <= fame <= 3, t
assert "SOXL" not in K.WHITELIST and "TCEHY" not in K.WHITELIST
assert "TSM" in K.WHITELIST and "BABA" in K.WHITELIST
print(f"PASS 1: 白名单 {len(K.WHITELIST)} 家，格式合法，无 ETF/OTC")

# 2 resolve：中文名/英文名/ticker/带括号噪声/$前缀（保留供人工排查工具）
assert K.resolve("英伟达") == "NVDA"
assert K.resolve("NVIDIA") == "NVDA"
assert K.resolve("$TSM") == "TSM"
assert K.resolve("台积电（TSMC）") == "TSM"
assert K.resolve("摩根大通") == "JPM"
assert K.resolve("不存在的公司xyz") is None
assert K.resolve("字节跳动") is None      # 未上市 → 不猜码
assert K.resolve("腾讯") is None          # 无美股主上市 → 不挂 $
print("PASS 2: resolve 别名命中与拒绝")


class MockLLM:
    """双角色 mock：chain 搜集 / 质检员（按 system 内容分流）。"""

    def __init__(self, chain_payload, verify_payload='{"verdicts":[]}'):
        self.chain = chain_payload
        self.verify = verify_payload
        self.calls = []

    def chat(self, fn, system, user, **kw):
        assert fn == "tickers"
        self.calls.append((system, user))
        return self.verify if "质检" in system else self.chain


# 3 direct 扫描器：素材∪成稿并集、行业统称映射、短码$前缀精度
item1 = {"title": "台积电10月营收创纪录", "text": "台积电公布10月营收，苹果、英伟达为主要客户。"}
d1 = K.extract_direct(item1, final_text="台积电营收超预期，先进制程满载。")
assert dict(d1) == {"TSM": "named", "AAPL": "named", "NVDA": "named"}, d1
item2 = {"title": "存储芯片合约价上涨", "text": "美光引领DRAM合约价上调，三大原厂减产。"}
d2 = K.extract_direct(item2)
assert dict(d2) == {"MU": "named"}, d2                    # WDC 未被点名 → 不进 direct
assert K.extract_direct({"title": "AI热潮下的算力投资", "text": "资本开支加速"}) == []
assert dict(K.extract_direct({"title": "$AI 发布财报", "text": "C3.ai 收入"})) == {"AI": "named"}
assert dict(K.extract_direct({"title": "T-Mobile 用户增长", "text": ""})) == {"TMUS": "named"}  # 不误中 T
d3 = K.extract_direct({"title": "半导体设备商订单火热", "text": ""})
assert {t for t, _ in d3} == {"ASML", "AMAT", "LRCX", "KLAC"} and all(s == "industry" for _, s in d3)
print("PASS 3: 扫描器精度（并集/统称映射/短码$前缀，WDC 旧bug不复发）")

# 4 五段管线主流程：扫描 direct + LLM chain + 非白名单丢弃 + direct 优先
mock = MockLLM('{"event_type":"结果性","chain":['
               '{"ticker":"ASML","role":"supplier","why":"扩产带动设备订单","relevance":"high"},'
               '{"ticker":"应用材料","role":"supplier","why":"建厂拉动设备需求","relevance":"med"},'
               '{"ticker":"某私募基金","role":"peer","why":"重仓芯片股","relevance":"low"}]}')
pairs = K.related_tickers(mock, item1)
assert pairs == [("AAPL", "direct"), ("NVDA", "direct"), ("TSM", "direct"),
                 ("ASML", "chain"), ("AMAT", "chain")], pairs
assert any("TSM" in u and "不得重复输出" in u for _s, u in mock.calls)  # direct 清单随输入
assert any("候选池" in s for s, _u in mock.calls)                        # 白名单内嵌 prompt
# 同公司 direct/chain 双报去重：chain 报 NVDA 但素材已点名 → direct 优先
mock_dup = MockLLM('{"event_type":"结果性","chain":['
                   '{"ticker":"NVDA","role":"peer","why":"AI景气互证","relevance":"high"}]}')
pairs_dup = K.related_tickers(mock_dup, item1)
assert pairs_dup == [("AAPL", "direct"), ("NVDA", "direct"), ("TSM", "direct")], pairs_dup
print("PASS 4: 主流程（扫描direct + chain合并 + 非白名单丢弃 + direct优先去重）")

# 5 上限10
rows = ",".join(
    f'{{"ticker":"{n}","role":"peer","why":"景气互证","relevance":"med"}}' for n in
    ("英伟达", "微软", "苹果", "谷歌", "亚马逊", "Meta", "特斯拉", "博通", "台积电",
     "AMD", "甲骨文", "美光", "戴尔", "高通", "英特尔"))
mock3 = MockLLM('{"event_type":"变动性","chain":[' + rows + ']}')
pairs3 = K.related_tickers(mock3, {"title": "测试", "text": "一条不含公司名的普通素材。"})
assert len(pairs3) == 10, len(pairs3)
assert pairs3[0] == ("AAPL", "chain") and ("MU", "chain") in pairs3
print(f"PASS 5: 上限10 截断（15 入 {len(pairs3)} 出，知名度+字母序）")

# 6 分层标签
assert K.format_line([("TSM", "direct")]) == "涉及标的：$TSM"
assert K.format_line([("TSM", "direct"), ("NVDA", "chain"), ("AMAT", "chain")]) == \
       "涉及标的：$TSM｜关联：$NVDA $AMAT"
assert K.format_line([("NVDA", "chain")]) == "关联标的：$NVDA"
assert K.format_line([]) == ""
print("PASS 6: 分层标签格式")

# 7 apply：长度预算不足自动放弃；命中即追加（text 参与扫描）
import account_status  # noqa: E402
account_status.post_max_len = lambda *a, **k: 280
item7 = {"title": "英伟达财报", "text": "英伟达数据中心营收创新高。"}
out, line = K.apply_ticker_line("短正文。", MockLLM('{"event_type":"结果性","chain":[]}'),
                                item7, max_len=280)
assert line == "涉及标的：$NVDA" and out.endswith(line)
out2, line2 = K.apply_ticker_line("短正文。", MockLLM('{"event_type":"结果性","chain":[]}'),
                                  item7, max_len=20)
assert line2 == "" and out2 == "短正文。"
print("PASS 7: 追加与长度预算放弃")

# 8 坏输出降级：无 direct 且 chain 解析失败 → 空；有 direct → direct-only 不裸奔
out3, line3 = K.apply_ticker_line("x", MockLLM("这不是JSON"),
                                  {"title": "t", "text": "无公司名。"}, max_len=None)
assert line3 == ""
out4, line4 = K.apply_ticker_line("台积电营收超预期。", MockLLM("这不是JSON"), item1, max_len=None)
assert line4 == "涉及标的：$AAPL $NVDA $TSM", line4
print("PASS 8: 坏输出降级（direct-only 兜底，不裸奔）")

# 9 硬校验四闸：why超长 / 方向词 / 结果性×customer / role非法 → 逐行丢弃不拖垮整批
mock9 = MockLLM('{"event_type":"结果性","chain":['
                '{"ticker":"ASML","role":"supplier","why":"这一条为什么这么长的理由超过二十个字会被丢掉啊"},'
                '{"ticker":"AMAT","role":"supplier","why":"订单受益明显"},'
                '{"ticker":"AAPL","role":"客户","why":"采购成本数据确认"},'
                '{"ticker":"NVDA","role":"peer","why":"AI景气互证"}]}')
pairs9 = K.related_tickers(mock9, {"title": "测试", "text": "普通素材。"})
assert pairs9 == [("NVDA", "chain")], pairs9
print("PASS 9: 硬校验四闸逐行丢弃")

# 10 质检员：drop/fix 生效；自身坏输出 fail-open 放行
item10 = {"title": "台积电扩产", "text": "台积电宣布新厂建设，资本开支上调。"}
chain10 = ('{"event_type":"变动性","chain":['
           '{"ticker":"VST","role":"cycle","why":"数据中心扩张推升电力需求"},'
           '{"ticker":"WDC","role":"supplier","why":"扩产带动硬盘订单"}]}')
v = MockLLM(chain10, '{"verdicts":[{"ticker":"VST","action":"drop","reason":"素材无电力信号"},'
                     '{"ticker":"WDC","action":"fix","role":"peer","reason":"同行互证"}]}')
pairs10 = K.related_tickers(v, item10)
assert ("TSM", "direct") in pairs10 and ("WDC", "chain") in pairs10 \
       and ("VST", "chain") not in pairs10, pairs10
v_open = MockLLM(chain10, "这不是JSON")
pairs_open = K.related_tickers(v_open, item10)
assert ("VST", "chain") in pairs_open and ("WDC", "chain") in pairs_open, pairs_open
print("PASS 10: 质检员 verdict 生效 + fail-open 放行")

print("标的判定器 v4 测试全部通过 ✅")
