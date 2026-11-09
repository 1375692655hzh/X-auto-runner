#!/usr/bin/env python3
# effects_test.py — 效果采样离线测试：mock FxTwitter HTTP + 隔离 state，不访问任何真实服务
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import effects  # noqa: E402

CASES = []
TEMP_DIRS = []
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def isolated_state():
    root = Path(tempfile.mkdtemp(prefix="xar-effects-test-"))
    TEMP_DIRS.append(root)
    effects.STATE_DIR = root
    effects.REQUEST_GAP = 0
    (root / "intents").mkdir()
    (root / "tickets").mkdir()
    return root


def put_action(root, dirname, iid, kind, created, **fields):
    doc = {"id": iid, "kind": kind, "status": "done",
           "created_at": created.isoformat(), **fields}
    (root / dirname / f"{iid}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def read_rows(root, account="tester"):
    path = root / f"effects-{account}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def status_response(sid, **metrics):
    status = {"id": str(sid), "author": {"screen_name": "TestEr"},
              "replies": 1, "likes": 10, "retweets": 2,
              "views": {"count": 100}, "quote_count": 1, **metrics}
    return {"ok": True, "status": 200, "data": {"tweet": status}}


@case("按创建时间选择 T+2/8/24/48h，且只处理 done 的发布动作")
def _():
    root = isolated_state()
    put_action(root, "intents", "old-post", "post", NOW - timedelta(hours=50),
               result_status_id="101", payload={"text": "旧帖"})
    put_action(root, "intents", "new-reply", "reply", NOW - timedelta(hours=9),
               status_id="102", payload={"text": "新回复", "status_id": "900"})
    put_action(root, "intents", "too-new", "comment", NOW - timedelta(hours=1),
               result_status_id="103", payload={"text": "尚未到期"})
    put_action(root, "intents", "wrong-kind", "like", NOW - timedelta(hours=50),
               result_status_id="104")
    put_action(root, "intents", "not-done", "post", NOW - timedelta(hours=50),
               result_status_id="105", status="planned")
    calls = []

    def fake_http(url, timeout=15):
        calls.append(url)
        return status_response(url.rsplit("/", 1)[-1])

    effects.get_json = fake_http
    result = effects.sample("Tester", handle="TestEr", now=NOW)
    rows = read_rows(root)
    keys = {(r["intent_id"], r["age_hours"]) for r in rows}
    assert result == {"account": "tester", "due": 6, "sampled": 6,
                      "failed": 0, "skipped": 0}, result
    assert keys == {("old-post", 2), ("old-post", 8), ("old-post", 24),
                    ("old-post", 48), ("new-reply", 2), ("new-reply", 8)}, keys
    assert all(r["status_id"] in ("101", "102") for r in rows)
    assert rows[0]["reposts"] == 2 and rows[0]["views"] == 100
    assert rows[0]["quotes"] == 1
    assert len(calls) == 6 and all("/2/status/" in url for url in calls), calls


@case("同一 intent+age 幂等；tickets/intents 同票合并不双采")
def _():
    root = isolated_state()
    created = NOW - timedelta(hours=49)
    for dirname in ("tickets", "intents"):
        put_action(root, dirname, "same-ticket", "post", created,
                   result_status_id="201", payload={"text": "同一动作"})
    calls = []

    def fake_http(url, timeout=15):
        calls.append(url)
        return status_response("201")

    effects.get_json = fake_http
    first = effects.sample("tester", handle="tester", now=NOW)
    assert first["sampled"] == 4 and len(read_rows(root)) == 4
    call_count = len(calls)
    second = effects.sample("tester", handle="tester", now=NOW + timedelta(minutes=30))
    assert second["due"] == 0 and second["sampled"] == 0
    assert len(calls) == call_count and len(read_rows(root)) == 4


@case("无结果 ID 且时间线无法唯一确认时跳过，不采回复目标原帖")
def _():
    root = isolated_state()
    put_action(root, "intents", "unmatched", "reply", NOW - timedelta(hours=50),
               payload={"status_id": "900", "text": "这是需要唯一确认的规范正文，不能靠猜测采样。"})
    calls = []

    def fake_http(url, timeout=15):
        calls.append(url)
        assert "/2/profile/tester/statuses" in url, url
        return {"ok": True, "data": {"results": [
            {"id": "901", "author": {"screen_name": "someone-else"},
             "replying_to": "target", "text": "这是需要唯一确认的规范正文，不能靠猜测采样。"},
            {"id": "902", "author": {"screen_name": "tester"},
             "text": "这是需要唯一确认的规范正文，不能靠猜测采样。"},
            {"id": "903", "author": {"screen_name": "tester"},
             "replying_to": "target", "text": "完全不同的正文"},
        ]}}

    effects.get_json = fake_http
    result = effects.sample("tester", now=NOW)
    assert result["due"] == 4 and result["sampled"] == 0 and result["skipped"] == 4, result
    assert read_rows(root) == []
    assert len(calls) == 1, calls  # 尤其没有对父帖 900 或其他候选发起状态采样


@case("无结果 ID 时按规范正文唯一匹配公开时间线，再采真实状态")
def _():
    root = isolated_state()
    text = "第一段规范正文，包含足够长度用于可靠匹配。\n\n第二段保留段落但时间线折叠空白。"
    put_action(root, "intents", "timeline-comment", "comment", NOW - timedelta(hours=50),
               payload={"status_id": "900", "target_handle": "target", "text": text,
                        "topic": "AI行业"})
    calls = []

    def fake_http(url, timeout=15):
        calls.append(url)
        if "/2/profile/" in url:
            return {"ok": True, "data": {"results": [
                {"id": "303", "author": {"screen_name": "TestEr"},
                 "replying_to": "target",
                 "text": "第一段规范正文，包含足够长度用于可靠匹配。 第二段保留段落但时间线折叠空白。"},
            ]}}
        assert url.endswith("/2/status/303"), url
        return {"ok": True, "data": {"tweet": {
            "id": "303", "author": {"screen_name": "tester"},
            "metrics": {"reply_count": 3, "like_count": 8,
                        "retweet_count": 2, "view_count": 500}}}}

    effects.get_json = fake_http
    result = effects.sample("tester", handle="TestEr", now=NOW)
    rows = read_rows(root)
    assert result["sampled"] == 4 and len(rows) == 4, result
    assert all(r["status_id"] == "303" and r["resolution"] == "timeline" for r in rows)
    assert rows[0]["replies"] == 3 and rows[0]["likes"] == 8
    assert rows[0]["reposts"] == 2 and rows[0]["views"] == 500
    assert rows[0]["topic"] == "AI行业"
    assert len(calls) == 5


@case("summary 聚合近窗口样本、互动量、kind 与已有题材")
def _():
    root = isolated_state()
    at = datetime.now(timezone.utc)
    rows = [
        {"intent_id": "p1", "account": "tester", "kind": "post", "age_hours": 2,
         "status_id": "1", "sampled_at": at.isoformat(), "topic": "AI",
         "replies": 1, "likes": 2, "reposts": 1, "views": 100},
        {"intent_id": "p1", "account": "tester", "kind": "post", "age_hours": 24,
         "status_id": "1", "sampled_at": at.isoformat(), "topic": "AI",
         "replies": 2, "likes": 5, "reposts": 1, "views": 200},
        {"intent_id": "r1", "account": "tester", "kind": "reply", "age_hours": 2,
         "status_id": "2", "sampled_at": at.isoformat(),
         "replies": 4, "likes": 3, "reposts": 0, "views": None},
        {"intent_id": "old", "account": "tester", "kind": "comment", "age_hours": 48,
         "status_id": "3", "sampled_at": (at - timedelta(days=8)).isoformat(),
         "replies": 99, "likes": 99, "reposts": 99, "views": 999},
    ]
    path = root / "effects-tester.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    out = effects.summary("TestEr", days=7)
    assert out["samples"] == 3 and out["intents"] == 2
    assert out["replies"] == 6 and out["likes"] == 8
    assert out["reposts"] == 1 and out["views"] == 200
    assert out["interactions"] == 15  # 每 intent 取最新时点；views 不计互动
    assert out["by_kind"]["post"]["samples"] == 2
    assert out["by_kind"]["post"]["intents"] == 1
    assert out["by_kind"]["post"]["interactions"] == 8
    assert out["by_kind"]["reply"]["samples"] == 1
    assert out["by_topic"]["AI"]["samples"] == 2
    assert out["by_topic"]["AI"]["views"] == 200
    assert "未标注" not in out["by_topic"]


def main():
    failed = 0
    try:
        for name, fn in CASES:
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:
                failed += 1
                print(f"  FAIL {name}: {type(e).__name__}: {e}")
        print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    finally:
        for root in TEMP_DIRS:
            shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
