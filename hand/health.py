# health.py — 账号健康日检（P2）：纯 HTTP 匿名，零账号风险，不开浏览器
# 移植自 x-auto-run health.js（行号对照）：
#   getJSON 超时控制        health.js:25
#   digestProfile 封禁判据  health.js:65（404+reason=suspended；其余=unknown）
#   digestStatuses 24h 口径 health.js:40（own/repost 归属、created_timestamp 数值排序）
#   digestTypeahead 软信号  health.js:82（仅记录不参与判定——原版 130 行拍板：fx 不稳致误判）
#   unknown 纪律           health.js:8（任一通道失败=unknown，不计健康不计异常）
#   快照: state/health-snapshots/YYYY-MM-DD.json，当日重跑覆盖更新，环比上一份历史
# 用法: /opt/xar/venv/bin/python health.py [--light]
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402
import yaml  # noqa: E402

API = "https://api.fxtwitter.com"
SNAP_DIR = STATE_DIR / "health-snapshots"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            try:
                return {"status": r.status, "ok": r.status == 200, "data": json.loads(body)}
            except json.JSONDecodeError:
                return {"status": r.status, "ok": False, "err": "bad-json"}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace") if e.fp else ""
        try:
            data = json.loads(body)
        except Exception:
            data = None
        return {"status": e.code, "ok": False, "data": data}
    except Exception as e:
        return {"ok": False, "err": type(e).__name__}


def digest_profile(res):
    if not res.get("ok"):
        suspended = res.get("status") == 404 and (res.get("data") or {}).get("reason") == "suspended"
        return {"state": "suspended" if suspended else "unknown",
                "err": res.get("err") or f"http {res.get('status')}"}
    u = (res.get("data") or {}).get("user") or {}
    return {"state": "ok", "name": (u.get("name") or "").strip() or None,
            "followers": u.get("followers"), "following": u.get("following"),
            "statuses": u.get("statuses"), "likes": u.get("likes"),
            "protected": bool(u.get("protected"))}


def digest_statuses(res, handle):
    if not res.get("ok"):
        return {"state": "unknown", "err": res.get("err") or f"http {res.get('status')}"}
    d = res.get("data") or {}
    lst = next((d[k] for k in ("results", "tweets", "statuses") if isinstance(d.get(k), list)), None)
    if lst is None and isinstance(d, list):
        lst = d
    if lst is None:
        return {"state": "unknown", "err": "shape"}
    h = (handle or "").lower()
    cutoff = time.time() * 1000 - 24 * 3600 * 1000
    own = [s for s in lst if ((s.get("author") or {}).get("screen_name") or "").lower() == h]
    own24h = [s for s in own if (s.get("created_timestamp") or 0) > cutoff]
    reposts = [s for s in lst if s.get("reposted_by")]

    def _views_of(s):
        v = s.get("views")
        if isinstance(v, dict):
            v = v.get("count")
        return v if isinstance(v, (int, float)) else None
    views = [v for v in (_views_of(s) for s in own) if v is not None]
    newest = max(own, key=lambda s: s.get("created_timestamp") or 0, default=None)
    return {"state": "ok", "count": len(lst), "ownCount": len(own), "ownCount24h": len(own24h),
            "repostCount": len(reposts),
            "newestAt": (newest or {}).get("created_at"),
            "minViews": min(views) if views else None,
            "sensitiveCount": sum(1 for s in own if s.get("possibly_sensitive"))}


def digest_typeahead(res, handle):
    if not res.get("ok"):
        return {"state": "unknown", "err": res.get("err") or f"http {res.get('status')}"}
    d = res.get("data") or {}
    users = d.get("users") or (d.get("result") or {}).get("users") or []
    h = (handle or "").lower()
    return {"state": "ok", "found": any(((u.get("screen_name") or u.get("handle") or "") + "").lower() == h for u in users)}


def main():
    light = "--light" in sys.argv
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    handle = cfg["account"]["handle"]
    account = handle.lower()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    f = SNAP_DIR / f"{today}.json"
    snap = {"date": today, "checkedAt": None, "accounts": {}}
    if f.exists():
        try:
            snap = {**snap, **json.loads(f.read_text(encoding="utf-8"))}
        except Exception:
            pass
    prevs = sorted(p for p in SNAP_DIR.glob("*.json") if p.name < f"{today}.json")
    prev = json.loads(prevs[-1].read_text(encoding="utf-8")) if prevs else None

    p = digest_profile(get_json(f"{API}/2/profile/{handle}"))
    time.sleep(2)
    # since 参数已致 404（2026-10-24 实测端点行为变更）；24h 口径改由 created_timestamp 客户端过滤
    s = ({"light": True, "followers": p.get("followers"), "following": p.get("following"),
          "statuses": p.get("statuses"), "state": p.get("state")}
         if light else
         digest_statuses(get_json(f"{API}/2/profile/{handle}/statuses"), handle))
    time.sleep(2)
    t = digest_typeahead(get_json(f"{API}/2/typeahead?q={handle}&result_type=user"), handle)

    entry = {"handle": handle, "profile": p, "statuses": s, "typeahead": t}
    # 判定唯一权威后续交给雷达四维（P4）；此处 verdict 仅 profile 口径（health.js:130 拍板）
    entry["verdict"] = "suspended" if p["state"] == "suspended" else ("green" if p["state"] == "ok" else "unknown")

    if prev:
        pv = (prev.get("accounts") or {}).get(account) or {}
        pp = pv.get("profile") or {}
        if isinstance(pp.get("followers"), int) and isinstance(p.get("followers"), int):
            entry["followersDelta"] = p["followers"] - pp["followers"]

    snap["accounts"][account] = entry
    snap["checkedAt"] = datetime.now(timezone.utc).isoformat()
    snap["light"] = light or None
    f.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    log_event("health", account, verdict=entry["verdict"],
              followers=p.get("followers"), followers_delta=entry.get("followersDelta"),
              own24h=(s.get("ownCount") if isinstance(s, dict) else None))
    # suspended = 立即全停（写 PAUSE 由人处置，检测进程只告警不自动动 PAUSE）
    if entry["verdict"] == "suspended":
        log_event("health", account, action="ALERT_SUSPENDED")
        print(f"ALERT: {handle} 疑似被封（profile 404 reason=suspended）——请人工核验", file=sys.stderr)
    print(json.dumps(entry, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
