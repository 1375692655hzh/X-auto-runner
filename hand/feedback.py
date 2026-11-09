# feedback.py — 票据反馈聚合：只读事实，不自动改提示词/题材/预算
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from ledger import STATE_DIR

REASON_LABELS = {
    "选题不相关": "选题不相关", "不相关": "选题不相关",
    "主体不名企": "主体不名企", "非名企": "主体不名企",
    "无实质增量": "无实质增量", "无增量": "无实质增量",
    "事实错误": "事实错误", "事实": "事实错误",
    "署名残留": "署名残留", "署名": "署名残留",
    "不可独立阅读": "不可独立阅读", "不完整": "不可独立阅读",
    "重复": "重复", "表达不自然": "表达不自然", "不自然": "表达不自然",
}


def normalize_reason(value):
    text = str(value or "").strip()
    if not text:
        return "未分类"
    return REASON_LABELS.get(text, text[:24])


def _ts(value):
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except (ValueError, TypeError):
        return 0


def summary(account="", days=7):
    """票据即反馈真相；返回可机读计数，供 QQ/日报共用。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    out = {"days": days, "tickets": 0, "approved": 0, "rejected": 0,
           "done": 0, "failed": 0, "unknown": 0, "expired": 0, "cancelled": 0,
           "first_pass_done": 0, "revised_done": 0, "revised_rejected": 0,
           "manual_revisions": 0, "reason_labels": {}, "repeat_labels": []}
    labels = Counter()
    for p in (STATE_DIR / "tickets").glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if account and not str(d.get("id", "")).startswith(("news-", "cog-", "manual-")):
            continue
        when = _ts(d.get("decided_at") or d.get("created_at"))
        if when < cutoff:
            continue
        out["tickets"] += 1
        status = d.get("status")
        if status in ("approved", "executing"):
            out["approved"] += 1
        if status in out:
            out[status] += 1
        revisions = int(d.get("revision_count") or 0)
        out["manual_revisions"] += revisions
        if status == "done":
            out["revised_done" if revisions else "first_pass_done"] += 1
        elif status == "rejected" and revisions:
            out["revised_rejected"] += 1
        fb = d.get("feedback") or {}
        if status == "rejected":
            label = normalize_reason(fb.get("label"))
            labels[label] += 1
    out["reason_labels"] = dict(labels.most_common())
    out["repeat_labels"] = sorted(k for k, n in labels.items() if n >= 2)
    return out


def summary_text(account="", days=7):
    s = summary(account, days)
    labels = "、".join(f"{k}{n}" for k, n in s["reason_labels"].items()) or "无"
    return (f"近{days}天票据 {s['tickets']}｜完成 {s['done']}｜驳回 {s['rejected']}｜"
            f"过期 {s['expired']}｜失败 {s['failed']}｜unknown {s['unknown']}\n"
            f"一次通过完成 {s['first_pass_done']}｜改后完成 {s['revised_done']}｜"
            f"改后仍驳 {s['revised_rejected']}｜人工改稿 {s['manual_revisions']} 次\n"
            f"驳回标签：{labels}\n"
            f"重复问题：{'、'.join(s['repeat_labels']) or '无'}")
