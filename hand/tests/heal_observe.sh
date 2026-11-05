#!/usr/bin/env bash
# heal_observe.sh — P1-3 自愈验收观察器：杀浏览器→三阶段观察→输出审计轨迹
set -u
pkill -9 -f camoufox-bin
echo "已杀浏览器 $(date +%T)"
for t in 80 200 320; do
  sleep 80
  python3 - <<'PY'
import json, time, datetime, subprocess
d = json.load(open("/opt/xar/state/resident-wcixot.json"))
hb = d.get("heartbeatAt")
if hb:
    age = int(time.time() - datetime.datetime.fromisoformat(hb).timestamp())
    hbs = f"fresh {age}s"
else:
    hbs = "CLEARED"
procs = subprocess.run(["pgrep", "-c", "-f", "camoufox-bin"], capture_output=True, text=True).stdout.strip() or "0"
print(f"[观察] hb={hbs} degraded={d.get('degradedRounds')} 浏览器进程={procs}")
PY
done
echo "=== 审计（自愈轨迹） ==="
grep -E "probeFail|workstationOpen|residentFatal|cookieInject" /opt/xar/state/log-2026-10.jsonl | tail -6
