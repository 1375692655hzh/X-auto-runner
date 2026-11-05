# heartbeat.py — P4-1 认知层心跳调度器（tick 模式，TLN 节律）
# build-plan P4：TLN(median=35min) × 日类型，活跃窗外不醒；所有动作仍走票据+预算。
# 形态：xar-heartbeat.timer 每 5min tick 本脚本一次；state/heartbeat.json 存 next_wake，
#   未到点静默退；到点干一票认知活并抽下一个 TLN 间隔。崩溃/重启无状态损耗。
# P4-1 骨架动作 = 记账（beat 日志 + sightings 计数面）；P4-2 起接 LLM 起草票据。
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event, check_pause, PauseActive  # noqa: E402

STATE_FILE = STATE_DIR / "heartbeat.json"
DEFAULTS = {"median_min": 35, "sigma": 0.8, "min_min": 5, "max_min": 240,
            "weekend_factor": 1.2}  # 人活动节律对数正态；周末拉长间隔


def _cfg():
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    hb = ((cfg.get("cognitive") or {}).get("heartbeat") or {})
    opts = dict(DEFAULTS)
    opts.update({k: float(v) for k, v in hb.items()
                 if isinstance(v, (int, float)) and k in DEFAULTS})
    return cfg, opts


def _window(cfg):
    aw = (cfg.get("runtime") or {}).get("active_window") or ["09:00", "22:00"]

    def m2(s):
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    return m2(aw[0]), m2(aw[1])


def _next_tln(opts):
    mu = math.log(opts["median_min"])
    while True:
        v = random.lognormvariate(mu, opts["sigma"])
        if opts["min_min"] <= v <= opts["max_min"]:
            break
    if datetime.now().weekday() >= 5:
        v = min(v * opts["weekend_factor"], opts["max_min"])
    return v


def _save(st):
    import os
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)   # 原子写（review 低16：半截 heartbeat.json）


def main():
    cfg, opts = _cfg()
    account = ((cfg.get("account") or {}).get("handle") or "wcixot").lower()
    st = {}
    if STATE_FILE.exists():
        try:
            st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            st = {}
    now = datetime.now().astimezone()

    lo, hi = _window(cfg)
    cur = now.hour * 60 + now.minute
    if not (lo <= cur < hi):  # 窗外：睡到下个窗口开（+0-30min 抖动，免整点指纹）
        wake = now.replace(hour=int(lo) // 60, minute=int(lo) % 60,
                           second=0, microsecond=0)
        if cur >= hi:
            wake += timedelta(days=1)
        wake += timedelta(minutes=random.uniform(0, 30))
        st["next_wake"] = wake.isoformat()
        _save(st)
        log_event("heartbeat", account, action="sleep", until=wake.isoformat()[:16])
        return
    if st.get("next_wake") and now < datetime.fromisoformat(st["next_wake"]):
        return  # 未到点，静默退

    try:
        check_pause()
    except PauseActive:
        st["next_wake"] = (now + timedelta(minutes=30)).isoformat()
        _save(st)
        return

    # ── 到点：认知活（P4-3 起接认知循环；异常就地消化不伤调度节律）──
    interval = _next_tln(opts)
    st["beats"] = int(st.get("beats") or 0) + 1
    st["last_beat_at"] = now.isoformat()
    st["next_wake"] = (now + timedelta(minutes=interval)).isoformat()
    _save(st)
    log_event("heartbeat", account, action="beat", beats=st["beats"],
              next_in_min=round(interval, 1))
    try:
        import cognitive
        cognitive.beat()
    except Exception as e:
        log_event("cognitive", account, action="beatFail", note=str(e)[:160])


if __name__ == "__main__":
    main()
