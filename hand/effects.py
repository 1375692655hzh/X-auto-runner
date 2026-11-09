# effects.py — 已发布内容效果采样（SP5）：FxTwitter 匿名只读，零账号风险
# 口径：done intents/tickets → T+2/8/24/48h → /2/status/{id}；无结果 ID 时才用
#       公开时间线的规范正文唯一匹配。无法确认/接口失败均不落样本，下轮重试。
# 存储：state/effects-<account>.jsonl；(intent_id, age_hours) 幂等，锁内重读后原子替换。
# 用法：python effects.py [--account HANDLE]；python effects.py --summary [--days 7]
import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, file_lock  # noqa: E402

API = "https://api.fxtwitter.com"
AGES = (2, 8, 24, 48)
KINDS = ("post", "reply", "comment")
METRICS = ("replies", "likes", "reposts", "views", "quotes")
INTERACTION_METRICS = ("replies", "likes", "reposts", "quotes")
REQUEST_GAP = 1.2
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
NORM = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()  # noqa: E731
RESULT_KEYS = ("result_status_id", "resultStatusId", "published_status_id",
               "publishedStatusId", "tweet_id", "tweetId")
METRIC_KEYS = {
    "replies": ("replies", "reply_count", "replyCount"),
    "likes": ("likes", "like_count", "favorite_count", "favorites"),
    "reposts": ("reposts", "retweets", "repost_count", "retweet_count"),
    "views": ("views", "view_count", "views_count"),
    "quotes": ("quotes", "quote_count", "quoteCount"),
}


def get_json(url, timeout=15):
    """与 health 相同的匿名 GET 包装；失败只返回错误，不触碰任何 X 写接口。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return {"status": r.status, "ok": r.status == 200, "data": json.loads(body)}
            except json.JSONDecodeError:
                return {"status": r.status, "ok": False, "err": "bad-json"}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "ok": False, "err": f"http {e.code}"}
    except Exception as e:
        return {"ok": False, "err": type(e).__name__}


def _parse_time(value):
    if isinstance(value, datetime):
        d = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # 秒/毫秒时间戳均兼容；创建时间的常规 ISO 字符串走下面分支。
        d = datetime.fromtimestamp(value / 1000 if value > 10**11 else value, timezone.utc)
    else:
        try:
            d = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)  # 现有账本 created_at 的 naive 值按 UTC 解释
    return d.astimezone(timezone.utc)


def _now(value=None):
    d = _parse_time(value) if value is not None else datetime.now(timezone.utc)
    if d is None:
        raise ValueError("无效当前时间")
    return d


def _account_name(account):
    name = str(account or "").strip().lstrip("@").lower()
    if not name or name in (".", "..") or not re.fullmatch(r"[a-z0-9_.-]+", name):
        raise ValueError(f"非法账号名: {account!r}")
    return name


def _effects_path(account):
    return STATE_DIR / f"effects-{_account_name(account)}.jsonl"


def _sample_age(row):
    value = row.get("age_hours", row.get("age"))
    try:
        return int(str(value).removeprefix("T+").removesuffix("h"))
    except (ValueError, TypeError, AttributeError):
        return None


def _read_samples(path):
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rows
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path.name} 第{n}行损坏，拒绝覆盖") from e
        if not isinstance(row, dict):
            raise ValueError(f"{path.name} 第{n}行不是对象，拒绝覆盖")
        rows.append(row)
    return rows


def _atomic_write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def _append_samples(path, new_rows):
    """网络请求不持锁；落盘时锁内重读，避免两个定时进程互相覆盖或重复。"""
    if not new_rows:
        return 0
    with file_lock(path):
        old = _read_samples(path)
        seen = {(str(r.get("intent_id") or ""), _sample_age(r)) for r in old}
        rows = list(old)
        added = 0
        for row in new_rows:
            key = (row["intent_id"], row["age_hours"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        if added:
            _atomic_write_jsonl(path, rows)
        return added


def _load_actions():
    """同票在 tickets/intents 各有一份：只保留 done 文档，按 intent_id 合并而非双采。"""
    actions = {}
    for dirname in ("tickets", "intents"):  # intents 后合并，优先采用其正文/元数据
        for p in sorted((STATE_DIR / dirname).glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue  # 半截/损坏状态不阻断其他动作
            if not isinstance(d, dict) or d.get("status") != "done" or d.get("kind") not in KINDS:
                continue
            payload = d.get("payload") or {}
            who = str(d.get("account") or payload.get("account") or "").strip().lstrip("@").lower()
            iid = str(d.get("id") or payload.get("intent_id") or p.stem)
            actions.setdefault(iid, {"intent_id": iid, "documents": [], "accounts": set()})
            actions[iid]["documents"].append(d)
            if who:
                actions[iid]["accounts"].add(who)
    return actions


def _kind(action):
    kinds = {d.get("kind") for d in action["documents"]}
    return kinds.pop() if len(kinds) == 1 else None


def _created(action):
    for d in reversed(action["documents"]):
        for key in ("created_at", "createdAt"):
            ts = _parse_time(d.get(key))
            if ts is not None:
                return ts
    return None


def _payload(action):
    out = {}
    for d in action["documents"]:
        if isinstance(d.get("payload"), dict):
            out.update(d["payload"])
    return out


def _status_id(value):
    if isinstance(value, bool) or value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return text
    m = re.search(r"/status(?:es)?/(\d+)(?:\D|$)", text)
    return m.group(1) if m else None


def _result_status_ids(action):
    """只认结果字段；reply/comment 的 payload.status_id 是被回复原帖，绝不可采它。"""
    ids = set()

    def add(value):
        sid = _status_id(value)
        if sid:
            ids.add(sid)

    for d in action["documents"]:
        payload = d.get("payload") or {}
        for key in RESULT_KEYS:
            add(d.get(key))
            add(payload.get(key))
        for key in ("status_id", "statusId"):
            add(d.get(key))
            if d.get("kind") == "post":
                add(payload.get(key))
        for key in ("result_url", "status_url"):
            add(d.get(key))
        for result in (d.get("result"), payload.get("result")):
            if isinstance(result, dict):
                for key in RESULT_KEYS + ("status_id", "statusId", "id", "url", "status_url"):
                    add(result.get(key))
    return ids


def _topic(action):
    payload = _payload(action)
    for d in reversed(action["documents"]):
        detail = d.get("news_detail") or payload.get("news_detail") or {}
        for obj in (d, payload, detail):
            for key in ("topic", "topic_label", "category", "pillar", "题材", "tier"):
                value = obj.get(key)
                if isinstance(value, (str, int)) and str(value).strip():
                    return str(value).strip()[:80]
    return None


def _response_data(res):
    if not isinstance(res, dict):
        return None
    if "ok" in res:
        return res.get("data") if res.get("ok") else None
    return res  # 便于离线 mock/兼容 cognitive 的裸 JSON 包装


def _fx_results(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("results", "tweets", "statuses"):
        if isinstance(data.get(key), list):
            return data[key]
    return _fx_results(data.get("data")) if isinstance(data.get("data"), (dict, list)) else []


def _author_handle(status):
    author = status.get("author") or status.get("user") or {}
    if isinstance(author, str):
        return author.lstrip("@").lower()
    return str(author.get("screen_name") or author.get("username") or "").lstrip("@").lower()


def _text_matches(wanted, remote):
    wanted, remote = NORM(wanted), NORM(remote)
    if not wanted:
        return False
    if wanted == remote:
        return True
    # 与 verify_unknown 同族的 20 字前缀兜底；短正文必须全等，且调用方要求唯一命中。
    return len(wanted) >= 20 and remote.startswith(wanted[:20])


def _timeline_match(action, statuses, handle):
    kind = _kind(action)
    payload = _payload(action)
    wanted = payload.get("text") or ""
    hits = []
    for s in statuses:
        if not isinstance(s, dict) or s.get("reposted_by"):
            continue
        if _author_handle(s) != handle.lstrip("@").lower():
            continue
        if (kind == "post" and s.get("replying_to")) or (kind in ("reply", "comment")
                                                               and not s.get("replying_to")):
            continue
        if _text_matches(wanted, s.get("text")):
            sid = _status_id(s.get("id") or s.get("status_id"))
            if sid:
                hits.append(sid)
    unique = sorted(set(hits))
    return unique[0] if len(unique) == 1 else None


def _extract_status(data):
    if not isinstance(data, dict):
        return None
    if _status_id(data.get("id") or data.get("status_id")):
        return data
    for key in ("tweet", "status", "result", "data"):
        found = _extract_status(data.get(key))
        if found is not None:
            return found
    return None


def _number(value):
    if isinstance(value, dict):
        value = value.get("count")
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(n) or n < 0:
        return None
    return int(n) if n.is_integer() else n


def _metric(status, name):
    containers = [status]
    containers.extend(status.get(k) for k in ("metrics", "stats") if isinstance(status.get(k), dict))
    for obj in containers:
        for key in METRIC_KEYS[name]:
            if key in obj:
                n = _number(obj.get(key))
                if n is not None:
                    return n
    return None


def _confirmed_status(res, status_id, handle):
    status = _extract_status(_response_data(res))
    if status is None or _status_id(status.get("id") or status.get("status_id")) != status_id:
        return None
    author = _author_handle(status)
    if author and author != handle.lstrip("@").lower():
        return None
    metrics = {name: _metric(status, name) for name in METRICS}
    return metrics if any(metrics[name] is not None for name in ("replies", "likes", "reposts", "views")) else None


def sample(account, handle=None, now=None):
    """采集本轮所有到期且未采的时点；返回本轮计数。now 可注入 datetime 供离线测试。"""
    account = _account_name(account)
    handle = str(handle or account).lstrip("@")
    at = _now(now)
    path = _effects_path(account)
    old = _read_samples(path)
    seen = {(str(r.get("intent_id") or ""), _sample_age(r)) for r in old}
    known = {}
    for row in old:
        sid = _status_id(row.get("status_id"))
        if sid:
            known.setdefault(str(row.get("intent_id") or ""), set()).add(sid)

    pending = []
    for action in _load_actions().values():
        if action["accounts"] and account not in action["accounts"]:
            continue
        kind, created = _kind(action), _created(action)
        if kind is None or created is None:
            continue
        ages = [age for age in AGES
                if at >= created + timedelta(hours=age) and (action["intent_id"], age) not in seen]
        if ages:
            pending.append((action, created, ages))

    resolved, via, need_timeline = {}, {}, []
    for action, _, _ in pending:
        iid = action["intent_id"]
        ids = _result_status_ids(action)
        previous = known.get(iid, set())
        if len(ids) == 1:
            resolved[iid], via[iid] = next(iter(ids)), "result_id"
        elif not ids and len(previous) == 1:
            resolved[iid], via[iid] = next(iter(previous)), "previous_sample"
        elif not ids and not previous:
            need_timeline.append(action)
        # 多个互相冲突的结果 ID = 不可确认，不猜。

    if need_timeline:
        url = f"{API}/2/profile/{urllib.parse.quote(handle, safe='')}/statuses?with_replies=1"
        statuses = _fx_results(_response_data(get_json(url)))
        claims = {}
        for action in need_timeline:
            sid = _timeline_match(action, statuses, handle)
            if sid:
                iid = action["intent_id"]
                resolved[iid], via[iid] = sid, "timeline"
                claims.setdefault(sid, []).append(iid)
        for sid, iids in claims.items():
            if len(iids) > 1:  # 同一远端帖不能凭正文任意分配给多张票
                for iid in iids:
                    resolved.pop(iid, None)
                    via.pop(iid, None)

    new_rows, failed, skipped = [], 0, 0
    for action, created, ages in pending:
        iid, kind = action["intent_id"], _kind(action)
        sid = resolved.get(iid)
        if not sid:
            skipped += len(ages)
            continue
        for age in ages:
            url = f"{API}/2/status/{sid}"
            metrics = _confirmed_status(get_json(url), sid, handle)
            if REQUEST_GAP > 0:
                time.sleep(REQUEST_GAP)
            if metrics is None:
                failed += 1
                continue
            row = {"intent_id": iid, "account": account, "kind": kind,
                   "created_at": created.isoformat(), "age_hours": age,
                   "status_id": sid, "sampled_at": at.isoformat(),
                   "resolution": via[iid], **metrics}
            topic = _topic(action)
            if topic:
                row["topic"] = topic
            new_rows.append(row)
    added = _append_samples(path, new_rows)
    return {"account": account, "due": sum(len(x[2]) for x in pending),
            "sampled": added, "failed": failed, "skipped": skipped}


def _bucket():
    return {"samples": 0, "intents": 0, "interactions": 0,
            **{name: 0 for name in METRICS}, "_intent_ids": set()}


def _add_bucket(bucket, row, count_sample=True, add_metrics=True):
    if count_sample:
        bucket["samples"] += 1
    bucket["_intent_ids"].add(str(row.get("intent_id") or ""))
    if add_metrics:
        for name in METRICS:
            n = _number(row.get(name))
            if n is not None:
                bucket[name] += n
    bucket["interactions"] = sum(bucket[name] for name in INTERACTION_METRICS)


def _finish_bucket(bucket):
    bucket["intents"] = len(bucket.pop("_intent_ids"))
    return bucket


def summary(account, days=7):
    """近 days 天采样事实：样本/互动总量及 kind、已标注题材分组；views 不算互动。"""
    account = _account_name(account)
    days = int(days)
    if days < 0:
        raise ValueError("days 不能为负数")
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    total, by_kind, by_topic = _bucket(), {}, {}
    grouped = {}
    for row in _read_samples(_effects_path(account)):
        sampled_at = _parse_time(row.get("sampled_at") or row.get("at"))
        if sampled_at is None or sampled_at < cutoff:
            continue
        if row.get("account") and _account_name(row["account"]) != account:
            continue
        iid = str(row.get("intent_id") or "")
        grouped.setdefault(iid, []).append(row)
        # 样本数记录每个到期时点；指标只在下面取每 intent 的最新时点，避免累计值重复相加。
        for bucket in (total, by_kind.setdefault(str(row.get("kind") or "unknown"), _bucket())):
            _add_bucket(bucket, row, add_metrics=False)
        topic = str(row.get("topic") or "").strip()
        if topic:
            _add_bucket(by_topic.setdefault(topic, _bucket()), row, add_metrics=False)
    for rows in grouped.values():
        row = max(rows, key=lambda r: _sample_age(r) or 0)
        for bucket in (total, by_kind[str(row.get("kind") or "unknown")]):
            _add_bucket(bucket, row, count_sample=False)
        topic = str(row.get("topic") or "").strip()
        if topic:
            _add_bucket(by_topic[topic], row, count_sample=False)
    return {"account": account, "days": days, **_finish_bucket(total),
            "by_kind": {k: _finish_bucket(v) for k, v in sorted(by_kind.items())},
            "by_topic": {k: _finish_bucket(v) for k, v in sorted(by_topic.items())}}


def main():
    parser = argparse.ArgumentParser(description="X 已发布内容只读效果采样（FxTwitter）")
    parser.add_argument("--account", help="X handle；缺省读 /etc/xar/instance.yaml")
    parser.add_argument("--summary", action="store_true", help="只输出聚合，不发起 HTTP")
    parser.add_argument("--days", type=int, default=7, help="summary 窗口，默认 7 天")
    args = parser.parse_args()
    handle = args.account
    cfg = {}
    if not handle:
        import yaml
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        handle = cfg["account"]["handle"]
    account = _account_name(handle)
    enabled = ((cfg.get("ops") or {}).get("effects") or {}).get("enabled", True)
    if not args.summary and not enabled:
        out = {"account": account, "disabled": True, "sampled": 0}
    else:
        out = summary(account, args.days) if args.summary else sample(account, handle=handle)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


if __name__ == "__main__":
    main()
