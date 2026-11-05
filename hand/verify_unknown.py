# verify_unknown.py — unknown 条目匿名核验（P3）：移植 x-auto-run verify-unknown.js
# 语义（红线回归版 2026-10-28，review 高1 + grok 复核）：
#   已发出 → intents 记 done；时间线未见 → 留 unknown 交人工（不再自动 requeue——
#   缺席证据不可靠：fxtwitter 索引延迟会误判，requeue 曾踩"unknown 绝不重发"红线）。
#   人工核实确实没发出去的：换 intent_id 重新提案走票据。
# FxTwitter 失败 → 不动任何条目（unknown≠异常）
# 数据源适配：fxtwitter statuses 端点已不接受 since 参数（2026-10-24 实测）——6h 过滤客户端做
# 用法: /opt/xar/venv/bin/python verify_unknown.py   （消费 state/intents/*.json 的 unknown）
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, Intents, Ledger, log_event  # noqa: E402
from tickets import Tickets  # noqa: E402

API = "https://api.fxtwitter.com"
NORM = lambda s: re.sub(r"\s+", " ", str(s or "")).strip()  # noqa: E731


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"data": json.loads(r.read().decode("utf-8", "replace"))}
    except Exception as e:
        return {"err": str(e)[:80]}


def main():
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    handle = cfg["account"]["handle"]
    account = handle.lower()
    if (STATE_DIR / "PAUSE").exists():
        print("state/PAUSE 急停生效中，退出")
        return
    led = Ledger(account)
    ints = Intents(led)
    tks = Tickets(led)

    unknowns = []
    for p in sorted((STATE_DIR / ("intents-test" if _sim() else "intents")).glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            log_event("verifyUnknown", account, action="skipCorrupt", file=p.name)
            continue   # 半截/损坏 intent 跳过不炸全场（grok S3）
        if d.get("status") == "unknown" and d.get("kind") in ("reply", "post"):
            unknowns.append((p, d))
    if not unknowns:
        print("无 unknown 回复/帖条目，收工")
        return
    print(f"待核验 {len(unknowns)} 条")

    res = get_json(f"{API}/2/profile/{handle}/statuses")
    if res.get("err") or not isinstance(res.get("data"), dict):
        log_event("verifyUnknown", account, action="skip", note=f"timeline 失败 {res.get('err')}")
        print("FxTwitter 时间线拉取失败，本轮不动任何条目（unknown≠异常）")
        return
    cutoff_ms = time.time() * 1000 - 6 * 3600 * 1000
    mine = [x for x in (res["data"].get("results") or [])
            if ((x.get("author") or {}).get("screen_name") or "").lower() == account
            and not x.get("reposted_by") and (x.get("created_timestamp") or 0) > cutoff_ms]

    confirmed = kept = 0
    for p, d in unknowns:
        payload = d.get("payload") or {}
        key = NORM(payload.get("text"))[:20]
        hit = next((x for x in mine
                    if key and NORM(x.get("text")).startswith(key)
                    and (bool(x.get("replying_to")) if d["kind"] == "reply" else not x.get("replying_to"))), None)
        iid = p.stem
        if hit:
            ints.mark(iid, "done", f"核验已发（{str(hit.get('id',''))[-8:]}）")
            confirmed += 1
            log_event("verifyUnknown", account, action="confirmed", id=iid)
            print(f"  ✓ {iid} 已发出 → done")
        else:
            # 红线回归（review 高1 + grok 复核修正叙事）：未发出不自动 requeue——
            # fxtwitter 索引延迟会把已发出的误判成未发（缺席证据），requeue 曾踩
            # "unknown 绝不重发"红线并制造 requeue 僵尸（live 实证 1026b 卡 3 天）。
            # 核验 2 次未见 → 留 unknown 交人工；人工核实未发可换 intent_id 重新提案。
            kept += 1
            retries = int(d.get("verify_retries") or 0) + 1
            log_event("verifyUnknown", account, action="keepUnknown",
                      id=iid, retries=retries)
            print(f"  ? {iid} 时间线未见（第{retries}次）→ 留 unknown 交人工")
        time.sleep(1.2)
    log_event("verifyUnknown", account, action="summary",
              confirmed=confirmed, kept=kept)
    print(f"核验完成：已发 {confirmed} / 留人工 {kept}")


def _sim():
    return __import__("os").environ.get("XAR_SIMULATE") == "1"


if __name__ == "__main__":
    main()
