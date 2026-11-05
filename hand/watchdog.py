# watchdog.py — 看门狗（P2）：双健康判定 + PAUSE 静默（systemd timer 每 10min）
# 移植自 x-auto-run resident.js --watchdog 模式（行号对照）：
#   hbFresh+pagesFresh 双判据   resident.js:269-271（10-21 僵尸事故：心跳照写骗看门狗 7h）
#   PAUSE 存在时静默不拉起      （账号级人工接管期）
#   不在时段不动                resident.js:274
# 本版判定（与 resident.py 写入字段一致）：
#   活 = heartbeatAt < 20min 且 pid 活；resident 自身已做页面探针，心跳新鲜即代表页面活
#   死 → systemctl restart xar-resident（exec 需 root；本服务以 root 跑）
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402

HB_FRESH_MS = 20 * 60 * 1000


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def resident_alive() -> bool:
    """活 = ①心跳新鲜(<20min)且pid活，或 ②启动声明新鲜(<5min)且pid活（开工位中——防重启竞态）"""
    now_ms = time.time() * 1000
    for p in STATE_DIR.glob("resident-*.json"):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not (st.get("pid") and pid_alive(int(st["pid"]))):
            continue
        hb = st.get("heartbeatAt")
        try:
            hb_fresh = bool(hb) and now_ms - datetime.fromisoformat(hb).timestamp() * 1000 < HB_FRESH_MS
        except Exception:
            hb_fresh = False
        claimed = bool(st.get("startAt")) and \
            now_ms - datetime.fromisoformat(st["startAt"]).timestamp() * 1000 < 5 * 60 * 1000
        if hb_fresh or claimed:
            return True
    return False


def in_window(cfg_path="/etc/xar/instance.yaml") -> bool:
    try:
        import yaml
        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
        w = cfg.get("runtime", {}).get("active_window", ["09:00", "22:00"])
        now = datetime.now().hour * 60 + datetime.now().minute
        a = int(w[0].split(":")[0]) * 60 + int(w[0].split(":")[1])
        b = int(w[1].split(":")[0]) * 60 + int(w[1].split(":")[1])
        return a <= now < b
    except Exception:
        return True  # 配置读不出时保守视为在时段（宁可多拉起判活，由 resident 自身时段纪律兜底）


def main():
    try:  # 账号从实例配置取（MoA 审查：硬编码 wcixot 让 fullwin777 的处置记错账）
        import yaml
        account = yaml.safe_load(
            Path("/etc/xar/instance.yaml").read_text(encoding="utf-8")
        )["account"]["handle"].lower()
    except Exception:
        account = "unknown"
    if (STATE_DIR / "PAUSE").exists():
        print("PAUSE 生效中，看门狗静默（人工接管期）")
        return
    if resident_alive():
        print("resident 心跳新鲜，不动")
        return
    if not in_window():
        print("不在活跃时段，不动")
        return
    log_event("watchdog", account, action="watchdogRelaunch")
    r = subprocess.run(["systemctl", "restart", "xar-resident"], capture_output=True, text=True)
    print(f"resident 失活，已拉起: rc={r.returncode} {r.stderr.strip()[:100]}")


if __name__ == "__main__":
    main()
