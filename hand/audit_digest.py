# audit_digest.py — 审计日聚合（2026-11-10 阶段二"运营研究环"第一环）
# 日志只攒不看 → 日日出账：①按 origin/链分组的今日产出 ②标的判定器审计块
# （rowDropped/verifyApplied/parseFail 计数、chainWhy 抽样、droppedNonWhitelist
# topN=白名单增补候选）。消费方：20:30 日报（cognitive._draft_report）。
import json
import re
from datetime import datetime
from pathlib import Path

from ledger import STATE_DIR


def _iter_rows(day):
    logf = STATE_DIR / f"log-{day[:7]}.jsonl"
    try:
        with logf.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("at", "")).startswith(day):
                    yield row
    except FileNotFoundError:
        return


def output_digest(day=None):
    """今日写动作产出按 origin 分组：manual（运营者）/ chain:<id>（链）/ other。
    数据源=state/intents/*.json（created_at 当日），status 全态计入（产出=开票意图）。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    out = {"manual": {}, "chain": {}, "other": {}}
    for p in (STATE_DIR / "intents").glob("*.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not str(doc.get("created_at", "")).startswith(day):
            continue
        origin = str((doc.get("payload") or {}).get("origin") or "")
        kind = str(doc.get("kind") or "?")
        if origin == "manual":
            bucket = out["manual"]
        elif origin.startswith("chain:"):
            bucket = out["chain"].setdefault(origin[6:], {})
        else:
            bucket = out["other"]
        bucket[kind] = bucket.get(kind, 0) + 1
    return out


def ticker_digest(day=None, sample=3, topn=5):
    """标的判定器当日审计聚合：事件计数 + chainWhy 抽样 + 非白名单丢弃 topN。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    counts = {}
    dropped = {}
    whys = []
    for row in _iter_rows(day):
        if row.get("task") != "tickers":
            continue
        act = str(row.get("action") or "?")
        counts[act] = counts.get(act, 0) + 1
        note = str(row.get("note") or "")
        if act == "rowDropped" or act == "droppedNonWhitelist":
            for seg in note.split(";"):
                m = re.match(r"(.+?):非白名单", seg.strip())
                if m:
                    name = m.group(1)[:20]
                    dropped[name] = dropped.get(name, 0) + 1
        elif act == "chainWhy" and note:
            whys.append(note[:120])
    return {
        "counts": counts,
        "chainWhy_sample": whys[:sample],
        "dropped_top": sorted(dropped.items(), key=lambda kv: -kv[1])[:topn],
    }


def text_block(day=None):
    """日报文本块（两段拼装，无数据段自动省略）。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    lines = []
    od = output_digest(day)
    parts = []
    for cid, kinds in sorted(od.get("chain", {}).items()):
        parts.append(f"{cid}:{'/'.join(f'{k}×{v}' for k, v in sorted(kinds.items()))}")
    if od.get("manual"):
        parts.append("人工:" + "/".join(f"{k}×{v}" for k, v in sorted(od["manual"].items())))
    if od.get("other"):
        parts.append("其他:" + "/".join(f"{k}×{v}" for k, v in sorted(od["other"].items())))
    if parts:
        lines.append("【今日产出（按来源）】" + " ｜ ".join(parts))
    td = ticker_digest(day)
    c = td["counts"]
    if c:
        lines.append("【标的判定器】" + " ｜ ".join(f"{k}×{v}" for k, v in sorted(c.items())))
        if td["dropped_top"]:
            cand = "、".join(f"{n}({v})" for n, v in td["dropped_top"])
            lines.append(f"  白名单增补候选（非白名单丢弃TOP）: {cand}")
        for w in td["chainWhy_sample"]:
            lines.append(f"  why抽样: {w}")
    return "\n".join(lines)
