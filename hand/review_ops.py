# review_ops.py — QQ 人审编辑台：自然语言改待审票；只更新票据，不执行发布。
from datetime import datetime
import hashlib

from ledger import Ledger, log_event, file_lock, _atomic_write
from tickets import Tickets, TicketError


def _item_from_ticket(doc):
    n = (doc.get("payload") or {}).get("news_detail") or {}
    if not n.get("source_text"):
        raise TicketError("该票没有原始素材，不能走主脑改稿；可用「改 <id> <正文>」")
    return {"id": n.get("item_id"), "title": n.get("title"), "text": n.get("source_text"),
            "source": n.get("source"), "source_id": n.get("source_id"),
            "form": n.get("form"), "time": n.get("time"),
            "markets": n.get("markets") or [], "cluster_id": n.get("cluster_id") or "",
            "url": n.get("url") or "", "source_truncated": False}


def revise_pending_ticket(ticket_id, instruction, account, by="qq", base_revision=None):
    """按自然语言修改待审票；绑定读取时版本，重新过作者与编辑。失败不改原票。"""
    from editorial import revise_ticket
    from llm import LLM

    if not instruction.strip():
        raise TicketError("修改要求为空")
    if base_revision is None:
        raise TicketError("缺少基准版本：请先读取票据详情再改稿")
    base_revision = int(base_revision)
    tk = Tickets(Ledger(account))
    doc = tk.get(ticket_id)
    if not doc:
        raise TicketError(f"票据不存在: {ticket_id}")
    if doc.get("status") != "pending_review":
        raise TicketError(f"票据状态 {doc.get('status')} 不可改")
    if int(doc.get("revision_count") or 0) != base_revision:
        raise TicketError("票据已被其他修改更新，请重新读取后再改")
    payload = doc.get("payload") or {}
    item = _item_from_ticket(doc)
    produced = revise_ticket(LLM(), item, instruction,
                             current_draft=str(payload.get("text") or ""))
    path = tk._path(ticket_id)
    with file_lock(path):
        latest = tk.get(ticket_id)
        if latest.get("status") != "pending_review":
            raise TicketError("修改期间票据状态已变化，未覆盖")
        if int(latest.get("revision_count") or 0) != base_revision:
            raise TicketError("修改期间版本已变化，拒绝覆盖")
        p = latest["payload"]
        history = latest.get("revision_history") or []
        history.append({"at": datetime.now().isoformat(), "by": by,
                        "instruction": instruction[:300],
                        "base_revision": base_revision,
                        "old_text": p.get("text") or "",
                        "new_text": produced["text"],
                        "old_hash": hashlib.sha256(str(p.get("text") or "").encode()).hexdigest()[:12],
                        "new_hash": hashlib.sha256(produced["text"].encode()).hexdigest()[:12]})
        latest["revision_history"] = history[-10:]
        latest["revision_count"] = int(latest.get("revision_count") or 0) + 1
        p["text"] = produced["text"]
        p.pop("analysis_pack", None)  # 新作者制不再保留旧立论合约
        p["value_reviews"] = produced["reviews"]
        _atomic_write(path, latest)
    log_event("review", account, action="revise", ticket=ticket_id,
              by=by, revisions=latest["revision_count"])
    return {"ticket": ticket_id, "new_text": produced["text"],
            "revision_count": latest["revision_count"],
            "reviews": produced["reviews"],
            "note": "已按你的要求改稿并重新通过审核；仍未发布"}
