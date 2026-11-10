#!/usr/bin/env python3
# tickers_test.py — 产业链关联标的判定器回归（2026-11-10 运营拍板）
# 覆盖：白名单完整性、resolve 别名/去噪、LLM 输出解析+非白名单丢弃、
# 排序（直接→关联度→知名度）、上限10、分层标签、长度预算放弃。
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

# 2 resolve：中文名/英文名/ticker/带括号噪声/$前缀
assert K.resolve("英伟达") == "NVDA"
assert K.resolve("NVIDIA") == "NVDA"
assert K.resolve("$TSM") == "TSM"
assert K.resolve("台积电（TSMC）") == "TSM"
assert K.resolve("摩根大通") == "JPM"
assert K.resolve("不存在的公司xyz") is None
assert K.resolve("字节跳动") is None      # 未上市 → 不猜码
assert K.resolve("腾讯") is None          # 无美股主上市 → 不挂 $
print("PASS 2: resolve 别名命中与拒绝")

# 3 LLM 输出解析：非白名单丢弃 + direct 优先 + 排序
class MockLLM:
    def __init__(self, payload):
        self.payload = payload
    def chat(self, fn, system, user, **kw):
        assert fn == "decision"
        return self.payload

mock = MockLLM('{"direct":[{"company":"台积电","relevance":"high"},'
               '{"company":"智谱AI","relevance":"high"}],'
               '"chain":[{"company":"英伟达","relevance":"high"},'
               '{"company":"应用材料","relevance":"med"},'
               '{"company":"某私募股权基金","relevance":"low"}]}')
pairs = K.related_tickers(mock, {"title": "t", "text": "x"})
assert pairs == [("TSM", "direct"), ("NVDA", "chain"), ("AMAT", "chain")], pairs
print("PASS 3: 解析+白名单过滤+排序（非白名单2家被丢弃）")

# 4 同公司 direct/chain 双报去重（direct 优先）
mock2 = MockLLM('{"direct":[{"company":"英伟达","relevance":"med"}],'
                '"chain":[{"company":"NVDA","relevance":"high"}]}')
pairs2 = K.related_tickers(mock2, {"title": "t", "text": "x"})
assert pairs2 == [("NVDA", "direct")], pairs2
print("PASS 4: 同标的去重 direct 优先")

# 5 上限10
many = ",".join(f'{{"company":"c{i}","relevance":"low"}}' for i in range(20))
rows = [{"company": n, "relevance": "med"} for n in
        ("英伟达", "微软", "苹果", "谷歌", "亚马逊", "Meta", "特斯拉", "博通", "台积电",
         "AMD", "甲骨文", "美光", "戴尔", "高通", "英特尔")]
mock3 = MockLLM('{"direct":[],"chain":' + str(rows).replace("'", '"') + '}')
pairs3 = K.related_tickers(mock3, {"title": "t", "text": "x"})
assert len(pairs3) == 10, len(pairs3)
# 全 med/chain 时按知名度(fame1)再字母序：AAPL 打头
assert pairs3[0] == ("AAPL", "chain") and ("MU", "chain") in pairs3
print(f"PASS 5: 上限10 截断（15 入 {len(pairs3)} 出，知名度优先）")

# 6 分层标签
assert K.format_line([("TSM", "direct")]) == "涉及标的：$TSM"
assert K.format_line([("TSM", "direct"), ("NVDA", "chain"), ("AMAT", "chain")]) == \
       "涉及标的：$TSM｜关联：$NVDA $AMAT"
assert K.format_line([("NVDA", "chain")]) == "关联标的：$NVDA"
assert K.format_line([]) == ""
print("PASS 6: 分层标签格式")

# 7 apply：长度预算不足自动放弃；命中即追加
import account_status  # noqa: E402
account_status.post_max_len = lambda *a, **k: 280
out, line = K.apply_ticker_line("短正文。", MockLLM('{"direct":[{"company":"英伟达"}],"chain":[]}'),
                                {"title": "t", "text": "x"}, max_len=280)
assert line == "涉及标的：$NVDA" and out.endswith(line)
out2, line2 = K.apply_ticker_line("短正文。", MockLLM('{"direct":[{"company":"英伟达"}],"chain":[]}'),
                                  {"title": "t", "text": "x"}, max_len=10)
assert line2 == "" and out2 == "短正文。"
print("PASS 7: 追加与长度预算放弃")

# 8 解析失败不炸
out3, line3 = K.apply_ticker_line("x", MockLLM("这不是JSON"), {"title": "t", "text": "x"}, max_len=None)
assert line3 == ""
print("PASS 8: 坏输出安全返回空")

print("标的判定器测试全部通过 ✅")
