# radar.py — R5：shadowbanradar 四维限流雷达（ref radar.js 移植，纯 HTTP 零账号风险）
# 检测四维：搜索建议限制 / 搜索限制 / 幽灵封禁 / 回复降权——限流最早的信号。
# 用法: /opt/xar/venv/bin/python radar.py [--handle Fullwin777]
# 输出: state/health-snapshots/<今日>.json 的 radar 字段（与 health 快照合并）
# 日护栏: 当日已查 ≥5 次拒跑（免费档 ~30/天，固定 1-2 + 复测 ≤5）
import json
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402

VERDICT_OK = re.compile(r"未见|正常|not detected", re.I)
VERDICT_BAD = re.compile(r"检测到|detected", re.I)
LOADING = re.compile(r"正在计算|\[\d+/4\]|请启用", re.I)


def fetch_result(handle: str) -> dict:
    url = f"https://shadowbanradar.com/zh/r/{handle}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:   # 秒（review M1：非毫秒）
            html = r.read().decode("utf-8", "replace")
    except Exception as e:
        return {"error": str(e)[:80]}
    # 剥 script/style（review M3：原始 HTML 内联 JS 文案会假匹配）
    import re as _re
    html = _re.sub(r"<script[\s\S]*?</script>", " ", html, flags=_re.I)
    html = _re.sub(r"<style[\s\S]*?</style>", " ", html, flags=_re.I)
    # 四维判定在 aria-label（服务器端渲染，无需浏览器）
    m = (re.search(r'aria-label="([^"]*搜索建议限制[^"]*)"', html)
         or re.search(r'aria-label="([^"]*Search suggestion[^"]*)"', html))
    aria = m.group(1) if m else None
    if not aria or LOADING.search(aria):
        return {"error": "loading_or_no_aria"}
    # 解析四维
    four = {}
    for part in aria.replace("&amp;", "&").split(","):
        pm = re.match(r"\s*([^:：]+)[:：]\s*(.+?)\s*$", part)
        if not pm:
            continue
        k, v = pm.group(1), pm.group(2)
        if not (VERDICT_OK.search(v) or VERDICT_BAD.search(v)):
            continue
        if re.search(r"搜索建议|suggestion", k, re.I):
            four["suggestion"] = v
        elif re.search(r"搜索|search", k, re.I) and "建议" not in k and "suggestion" not in k.lower():
            four["search"] = v
        elif re.search(r"回复隐藏|ghost", k, re.I):
            four["ghost"] = v
        elif re.search(r"回复排序|deboost", k, re.I):
            four["deboost"] = v
    # 附加信息
    sm = re.search(r"(\d+)\s*/\s*100", html)
    four["score"] = sm.group(1) if sm else None
    vm = re.search(r"未检测到限流|检测到限流", html)
    four["verdict"] = vm.group(0) if vm else None
    am = re.search(r"账户年龄[^\d]*(\d+)\s*天", html)
    four["accountAgeDays"] = am.group(1) if am else None
    return {"four": four, "aria": aria[:200]}


def radar_checks_today(account: str) -> int:
    try:
        log = (STATE_DIR / f"log-{datetime.now().strftime('%Y-%m')}.jsonl").read_text("utf-8")
        today = datetime.now().strftime("%Y-%m-%d")
        return sum(1 for line in log.splitlines()
                   if '"task": "radar"' in line and today in line[:30])
    except Exception:
        return 0


def main():
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    handle = sys.argv[sys.argv.index("--handle") + 1] if "--handle" in sys.argv \
        else cfg["account"]["handle"]
    account = handle.lower()
    if (STATE_DIR / "PAUSE").exists():
        print("PAUSE 生效中，跳过")
        return
    n_today = radar_checks_today(account)
    if n_today >= 5:
        print(f"雷达日护栏：今日已 {n_today} 次（上限 5），拒跑")
        return
    result = fetch_result(handle)
    log_event("radar", account, action="check", handle=handle,
              verdict=result.get("four", {}).get("verdict"))
    # 合并进今日健康快照（review M2：全空不覆盖——ref 三态合并语义）
    snap_dir = STATE_DIR / "health-snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / f"{datetime.now().strftime('%Y-%m-%d')}.json"
    try:
        snap = json.loads(snap_file.read_text(encoding="utf-8"))
    except Exception:
        snap = {}
    four = result.get("four") or {}
    ok = any(v for k, v in four.items() if k in ("suggestion", "search", "ghost", "deboost"))
    if ok:
        snap.setdefault("radar", {})
        snap["radar"] = {"fetchedAt": datetime.now().isoformat(), "results": {handle: result}}
    else:
        snap.setdefault("radar", {})["channelError"] = result.get("error", "parse_empty")
    snap_file.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(four if ok else result, ensure_ascii=False))


if __name__ == "__main__":
    main()
