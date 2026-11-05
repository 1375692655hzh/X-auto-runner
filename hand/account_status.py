# account_status.py — 账号能力状态：蓝 V 决定长文权限（24h 缓存）
# fxtwitter profile.user.verification.verified=true 视为已验证/蓝 V；
# 查询失败回落缓存；无缓存按非蓝 V（280 加权上限）保守处理。
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ledger import STATE_DIR, log_event

CACHE = STATE_DIR / "account-status.json"
TTL_S = 24 * 3600


def current_handle():
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    return (cfg.get("account") or {}).get("handle") or "WCixot"


def _read_cache(handle):
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
        return d if (d.get("handle") or "").lower() == handle.lower() else None
    except Exception:
        return None


def is_blue_verified(handle=None):
    handle = handle or current_handle()
    cached = _read_cache(handle)
    if cached and time.time() - float(cached.get("fetched_ts") or 0) < TTL_S:
        return bool(cached.get("blue_verified"))
    url = "https://api.fxtwitter.com/2/profile/" + urllib.parse.quote(handle)
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
        user = d.get("user") or (d.get("data") or {}).get("user") or {}
        verification = user.get("verification") or {}
        blue = bool(verification.get("verified"))
        out = {"handle": handle, "blue_verified": blue,
               "verified_type": verification.get("type"),
               "fetched_ts": time.time(), "source": "fxtwitter"}
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, CACHE)
        log_event("account", handle.lower(), action="verification",
                  blue=blue, note=str(verification.get("type") or ""))
        return blue
    except Exception as e:
        log_event("account", handle.lower(), action="verificationFail", note=str(e)[:100])
        # 有旧缓存用旧值；完全不知道时按非蓝 V，避免越权限发长文
        return bool(cached and cached.get("blue_verified"))


def post_max_len(handle=None):
    """质量门参数：蓝 V=None（不检查上限），非蓝 V=280。"""
    return None if is_blue_verified(handle) else 280


if __name__ == "__main__":
    h = current_handle()
    print({"handle": h, "blue_verified": is_blue_verified(h),
           "post_max_len": post_max_len(h)})
