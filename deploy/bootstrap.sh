#!/usr/bin/env bash
# xar-agent 黄金底座一键安装（幂等，可重复执行）
# 用法: sudo bash bootstrap.sh        （服务用户默认 ubuntu，可用 XAR_USER 覆盖）
# 依据: 规范与约束.md §九 / docs/build-plan.md P0
set -euo pipefail

XAR_USER="${XAR_USER:-ubuntu}"
XAR_ROOT="/opt/xar"
XAR_ETC="/etc/xar"
XAR_VENV="$XAR_ROOT/venv"
HERE="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" -eq 0 ] || { echo "需要 sudo 运行"; exit 1; }

echo "== [1/7] 系统包 =="
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  python3-venv python3-pip xvfb fontconfig git jq curl \
  fonts-noto-cjk fonts-noto-color-emoji \
  libgtk-3-0t64 libdbus-glib-1-2 libxt6 libasound2t64 libx11-xcb1 \
  libxcomposite1 libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 libcairo2

echo "== [2/7] 内核参数（Camoufox 沙箱所需） =="
echo "kernel.apparmor_restrict_unprivileged_userns=0" > /etc/sysctl.d/99-camoufox.conf
sysctl -q -w kernel.apparmor_restrict_unprivileged_userns=0 || true

echo "== [3/7] 目录布局 =="
mkdir -p "$XAR_ROOT"/hand "$XAR_ROOT"/core "$XAR_ROOT"/deploy "$XAR_ROOT"/logs
mkdir -p "$XAR_ROOT/state"/intents "$XAR_ROOT/state"/tickets "$XAR_ROOT/state"/health-snapshots
mkdir -p "$XAR_ETC"
chown -R "$XAR_USER":"$XAR_USER" "$XAR_ROOT"

echo "== [4/7] Python 环境 =="
sudo -u "$XAR_USER" python3 -m venv "$XAR_VENV" 2>/dev/null || true
sudo -u "$XAR_USER" "$XAR_VENV/bin/pip" install -q -U pip
sudo -u "$XAR_USER" "$XAR_VENV/bin/pip" install -q "camoufox[geoip]" pyyaml websocket-client

echo "== [5/7] Camoufox 浏览器本体（已存在则跳过） =="
if [ ! -d "/home/$XAR_USER/.cache/camoufox/browsers" ]; then
  sudo -u "$XAR_USER" "$XAR_VENV/bin/python" -m camoufox fetch 2>&1 | tail -1
else
  echo "已存在，跳过（$(du -sh /home/$XAR_USER/.cache/camoufox 2>/dev/null | cut -f1)）"
fi

echo "== [6/7] swap 核查（红线：OOM 杀浏览器=bot 信号） =="
SWAP_KB=$(awk 'NR>1 {s+=$3} END {print s+0}' /proc/swaps)
if [ "${SWAP_KB:-0}" -lt 1500000 ]; then
  echo "警告: swap ${SWAP_KB}kB < 1.5G"
fi

echo "== [7/7] systemd 单元（安装但不启用；启用须实例配置校验通过） =="
for u in xar-resident.service xar-core.service xar-watchdog.service xar-watchdog.timer \
         xar-health.service xar-health.timer xar-browse.service xar-browse.timer \
         xar-verify.service xar-verify.timer xar-qqbot.service \
         xar-heartbeat.service xar-heartbeat.timer; do
  [ -f "$HERE/$u" ] && cp "$HERE/$u" /etc/systemd/system/
done
systemctl daemon-reload

if [ ! -f "$XAR_ETC/instance.yaml" ]; then
  SRC="$HERE/../config/instance.yaml.example"
  [ -f "$SRC" ] && install -m 600 -o "$XAR_USER" "$SRC" "$XAR_ETC/instance.yaml" \
    && echo "已生成 $XAR_ETC/instance.yaml（600，待填凭据）"
fi

echo "== 完成，自检 =="
sudo -u "$XAR_USER" "$XAR_VENV/bin/python" - <<'PY'
import camoufox, sys
print("camoufox 包 OK")
sys.exit(0)
PY
fc-list :lang=zh | head -1
echo "底座就绪。下一步: 填写 /etc/xar/instance.yaml"
