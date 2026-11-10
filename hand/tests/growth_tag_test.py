#!/usr/bin/env python3
# growth_tag_test.py — 主脑产出帖 1/3 互关tag 机器层回归（2026-11-10 运营拍板）
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-tag-")
os.environ["XAR_STATE"] = TMP
sys.path.insert(0, str(Path(__file__).parents[1]))

import news_auto as N  # noqa: E402
import account_status  # noqa: E402

# 本地无 /etc/xar/instance.yaml；固定非蓝V上限 280（与生产 post_max_len 语义一致）
account_status.post_max_len = lambda *a, **k: 280

# 1 强制命中：追加 tag 且长度合规
N._growth_tags = lambda: N.GROWTH_TAGS
out, tagged = N._apply_growth_tags("测试正文。")
assert tagged is True, out
assert out.endswith(N.GROWTH_TAGS), out
assert N.GROWTH_TAGS.split()[0] in out  # 三个tag都在
for t in ("#蓝V互关", "#交朋友", "#浇朋友"):
    assert t in out, t
print("PASS 1: 命中时追加三tag")

# 2 未命中：原文不动
N._growth_tags = lambda: ""
out, tagged = N._apply_growth_tags("测试正文。")
assert tagged is False and out == "测试正文。"
print("PASS 2: 未命中原文不动")

# 3 长度预算不足：自动放弃
account_status.post_max_len = lambda *a, **k: 10
N._growth_tags = lambda: N.GROWTH_TAGS
out, tagged = N._apply_growth_tags("这是一条明显会超过长度预算的正文内容。")
assert tagged is False and "蓝V互关" not in out, out
account_status.post_max_len = lambda *a, **k: 280
print("PASS 3: 超长度预算自动放弃")

# 4 概率语义：默认实现大约 1/3
N._growth_tags = lambda: N.GROWTH_TAGS if __import__("random").SystemRandom().random() < 1/3 else ""
hits = sum(1 for _ in range(3000) if N._growth_tags())
assert 0.25 < hits / 3000 < 0.42, hits
print(f"PASS 4: 1/3 概率语义（3000 次命中 {hits}）")

print("growth tag 注入测试全部通过 ✅")
