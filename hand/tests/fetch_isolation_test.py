#!/usr/bin/env python3
# fetch_isolation_test.py — _fetch_new 单源隔离 + 截断推进回归（2026-11-10）
# 背景：精品文章超 8 页 → 整拍 sourceFail，资讯源被拖停 46h；
# 修复：单源失败只跳过该源；翻页超限截断取新段并推进位置。
import os
import sys
import tempfile
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="xar-fetch-")
os.environ["XAR_STATE"] = TMP
sys.path.insert(0, str(Path(__file__).parents[1]))

import datastation                          # noqa: E402
import news_auto as N                       # noqa: E402

AUDITS = []
LOGS = []
N._audit = lambda action, **kw: AUDITS.append((action, kw))
N.log_event = lambda *a, **kw: LOGS.append((a, kw))
REAL_FETCH = datastation.fetch_incremental

_TXT = "这是一条用于测试的素材正文，长度超过二十字，包含足够的文字内容。"
NEWS_ROWS = [{"id": "n1", "time": "2026-11-10 09:00", "form": "资讯",
              "title": "测试资讯一", "text": _TXT},
             {"id": "n2", "time": "2026-11-10 09:30", "form": "资讯",
              "title": "测试资讯二", "text": _TXT}]
ART_TRUNC_ROWS = [{"id": "a1", "time": "2026-11-10 08:00", "form": "精品文章",
                   "title": "测试文章甲", "text": _TXT}]
ART_ROWS = [{"id": "a2", "time": "2026-11-10 09:10", "form": "精品文章",
             "title": "测试文章乙", "text": _TXT}]


def run_case(fetch_impl, st):
    datastation.fetch_incremental = fetch_impl
    AUDITS.clear()
    LOGS.clear()
    return N._fetch_new(st)


# 1 截断：article 超限抛 IncrementalTruncated → 资讯照常，文章截断段入列且位置推进
def fetch_truncated(form, **kw):
    if form == "精品文章":
        raise datastation.IncrementalTruncated(form, 8, list(ART_TRUNC_ROWS))
    return list(NEWS_ROWS)

items, pos = run_case(fetch_truncated, {"sources": {}, "evaluated": {}})
ids = {str(i["id"]) for i in items}
assert ids == {"n1", "n2", "a1"}, ids
assert pos.get("news") == "2026-11-10 09:30", pos
assert pos.get("article") == "2026-11-10 08:00", pos
assert any(a == "source_truncated" for a, _ in AUDITS), AUDITS
print("PASS 1: 截断取新段 + 双源位置推进 + 审计留痕")

# 2 单源失败隔离：article 抛普通错 → 资讯照常，文章无位置
def fetch_broken_article(form, **kw):
    if form == "精品文章":
        raise RuntimeError("精品文章 增量读取失败:_http 500")
    return list(NEWS_ROWS)

items, pos = run_case(fetch_broken_article, {"sources": {}, "evaluated": {}})
assert {str(i["id"]) for i in items} == {"n1", "n2"}
assert "news" in pos and "article" not in pos, pos
assert any(kw.get("action") == "sourceFail" and "精品文章" in str(kw.get("note"))
           for _, kw in LOGS), LOGS
print("PASS 2: 单源失败不拖垮另一源")

# 3 正常路径：双源齐全
def fetch_ok(form, **kw):
    return list(NEWS_ROWS) if form == "资讯" else list(ART_ROWS)

items, pos = run_case(fetch_ok, {"sources": {}, "evaluated": {}})
assert {str(i["id"]) for i in items} == {"n1", "n2", "a2"}
assert pos == {"news": "2026-11-10 09:30", "article": "2026-11-10 09:10"}, pos
print("PASS 3: 正常双源路径不变")

# 4 evaluated 去重仍生效（截断段里已评的旧条目被过滤）
items, pos = run_case(fetch_truncated, {"sources": {}, "evaluated": {"a1": "x"}})
assert "a1" not in {str(i["id"]) for i in items}
print("PASS 4: 已评去重不受截断影响")

datastation.fetch_incremental = REAL_FETCH
print("_fetch_new 隔离/截断测试全部通过 ✅")
