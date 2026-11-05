# tickets.py — 票据制审批（P3）：写意图的人工审批生命周期
# 设计出处：kimi-k3 round5 §四.4 / cursor-opus-5.5 票据制 / SP6
# 流程：需人审的写意图 → 开票（预算预留占位）→ 立即返回 pending_review，agent 不阻塞
#   → 人审（P3 开发期=本 CLI；QQ 上线后=卡片三按钮）→ 获批 → hand 下次单飞空档执行
#   （执行前重过全闸：STOP/熔断/预算/间隔/正文哈希；幂等键=ticket_id 同票最多一次）
#   → 驳回/过期 → 释放预留 + 事件进下次简报；旧票不可复活，agent 可重新提案
# 票据时效（只能紧）：post 6h / reply+comment 2h / like 1h / follow+unfollow 1h
# 存储：state/tickets/<id>.json（hand 侧真相；core 侧 SQLite 主档 P3 后期接）
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import (STATE_DIR, Ledger, log_event, _atomic_write,  # noqa: E402
                    _load_json_fail_closed, file_lock)

TTL_HOURS = {"post": 6, "reply": 2, "comment": 2, "like": 1, "follow": 1, "unfollow": 1}
TERMINAL = {"done", "unknown", "failed", "rejected", "expired", "cancelled", "executing"}


class Tickets:
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.dir = STATE_DIR / ("tickets-test" if _sim() else "tickets")
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, tid):
        return self.dir / f"{tid}.json"

    def create(self, kind: str, payload: dict, rationale: str = "") -> dict:
        """开票 + 预算预留（kimi §四.4 之①）。开不出（额度尽/键冲突）抛 TicketError。"""
        tid = payload.get("intent_id")
        if not tid:
            raise TicketError("缺 intent_id")
        if self._path(tid).exists():
            raise TicketError(f"幂等键已存在: {tid}")
        if not self.ledger.reserve(kind, 1, tid):
            raise TicketError(f"{kind} 预算预留失败（额度尽）")
        doc = {
            "id": tid, "kind": kind, "payload": payload, "rationale": rationale[:400],
            "budget_snapshot": {"used": self.ledger.used(kind), "cap": self.ledger.caps.get(kind)},
            "status": "pending_review", "created_at": _now(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=TTL_HOURS.get(kind, 1))).isoformat(),
        }
        _atomic_write(self._path(tid), doc)
        log_event("tickets", self.ledger.account, action="create", ticket=tid, kind=kind)
        return doc

    def get(self, tid):
        return _load_json_fail_closed(self._path(tid))

    def decide(self, tid, approve: bool, by: str, edited_text: str | None = None) -> dict:
        """人审决定：approve=通过（可带改稿，改稿后仍需过质量门）。
        锁内读-判-写（review 中4：sweep 与 worker 双副本竞态的封堵）"""
        with file_lock(self._path(tid)):
            doc = self.get(tid)
            if doc is None:
                raise TicketError(f"票据不存在: {tid}")
            if doc["status"] != "pending_review":
                raise TicketError(f"票据状态 {doc['status']} 不可审")
            if self._expired(doc):
                # 内联过期处置（flock 不可重入——expire 内部持同把锁，嵌套调用自锁）
                doc["status"] = "expired"
                doc["expired_at"] = _now()
                self._release_once(doc)
                _atomic_write(self._path(tid), doc)
                log_event("tickets", self.ledger.account, action="expire", ticket=tid)
                return doc
            doc["status"] = "approved" if approve else "rejected"
            doc["decided_at"] = _now()
            doc["decided_by"] = by
            if approve and edited_text is not None:
                # 已批准票据保存唯一规范正文：与后续质量门/执行器同一清洗口径，
                # 避免“检查的是cleaned，实际发送raw”以及CRLF/段落结构漂移
                from quality import clean
                doc["payload"]["text"] = clean(edited_text)[0]
                doc["edited"] = True
            if not approve:
                self._release_once(doc)  # 驳回即释放预留
            _atomic_write(self._path(tid), doc)
        log_event("tickets", self.ledger.account, action="approve" if approve else "reject",
                  ticket=tid, by=by)
        return doc

    def expire(self, tid) -> dict:
        with file_lock(self._path(tid)):
            doc = self.get(tid)
            if doc is None or doc.get("released"):   # 已终局/已释放：幂等返回
                return doc or {}
            doc["status"] = "expired"
            doc["expired_at"] = _now()
            self._release_once(doc)
            _atomic_write(self._path(tid), doc)
        log_event("tickets", self.ledger.account, action="expire", ticket=tid)
        return doc

    def _release_once(self, doc):
        """预留释放幂等（released 旗标防顺序双放；expire/finish 的 file_lock 防双副本竞态
        ——review 中4：锁内重读后 based on 最新 doc 判 released，裸旗标防不住双副本）"""
        if doc.get("released"):
            return
        self.ledger.release(doc["kind"], 1)
        doc["released"] = True

    def sweep_expired(self) -> int:
        """过期清扫：pending/approved/executing 过期→释放预留（expire 内部持锁幂等）"""
        n = 0
        for p in self.dir.glob("*.json"):
            doc = _load_json_fail_closed(p)
            if doc and doc.get("status") in ("pending_review", "approved", "executing") \
                    and self._expired(doc):
                self.expire(p.stem)
                n += 1
        return n

    def claim_for_execution(self, tid) -> dict | None:
        """执行前取票：approved 且未过期 → executing（占位防并发双发）；
        获批但已过期 → 过期处置（释放预留）后 None。
        注意 expire 内部持同把锁——此处内联过期逻辑，不嵌套调用（flock 不可重入）"""
        with file_lock(self._path(tid)):
            doc = self.get(tid)
            if doc is None:
                return None
            if doc.get("status") != "approved":
                return None
            if self._expired(doc):
                doc["status"] = "expired"
                doc["expired_at"] = _now()
                self._release_once(doc)
                _atomic_write(self._path(tid), doc)
                log_event("tickets", self.ledger.account, action="expire", ticket=tid)
                return None
            doc["status"] = "executing"
            _atomic_write(self._path(tid), doc)
            return doc

    def requeue(self, tid, note: str = "") -> None:
        """工位暂不可用：executing→approved，保留预留与批准，等待有效期内重试。
        仅用于确定性未执行（如 WORKSTATION_DOWN）；unknown 不得调用。"""
        with file_lock(self._path(tid)):
            doc = self.get(tid)
            if doc is None:
                return
            if doc.get("status") != "executing":
                return
            doc["status"] = "approved"
            doc["retry_count"] = int(doc.get("retry_count") or 0) + 1
            doc["next_retry_at"] = time.time() + 60
            doc["retry_note"] = note[:160]
            _atomic_write(self._path(tid), doc)
        log_event("tickets", self.ledger.account, action="requeue", ticket=tid,
                  note=note[:80])

    def finish(self, tid, status: str, note: str = "") -> None:
        with file_lock(self._path(tid)):
            doc = self.get(tid)
            if doc is None:
                return
            if doc.get("status") == "executing":
                doc["status"] = status  # done | unknown | failed
            else:
                # 非 executing（已被 sweep 过期等）：只留终局注记，不再抢改状态
                doc.setdefault("late_finish", []).append(
                    {"wanted": status, "at": _now(), "note": note[:80]})
            doc["result"] = note[:200]
            doc["executed_at"] = _now()
            # 终局即释放预留：执行尝试已由 mark_intent 计入 used，预留占位不释放会双重占额
            # （2026-10-26 首演实证：post used=2 + reserved=2 → cap_left=0 堵死后续开票）
            self._release_once(doc)
            _atomic_write(self._path(tid), doc)
        log_event("tickets", self.ledger.account, action="finish", ticket=tid, status=status)

    @staticmethod
    def _expired(doc) -> bool:
        return datetime.now(timezone.utc) > datetime.fromisoformat(doc["expires_at"])

    def list_open(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            doc = _load_json_fail_closed(p)
            if doc and doc.get("status") == "pending_review":
                out.append(doc)
        return out


class TicketError(Exception):
    pass


def _sim():
    import os
    return os.environ.get("XAR_SIMULATE") == "1"


def _now():
    return datetime.now(timezone.utc).isoformat()


# ── 开发期审批 CLI（QQ 上线后由 bot 按钮替代，本 CLI 保留作管理通道） ──
# 用法: tickets.py list | approve <id> [改稿文本] | reject <id>
def main():
    import yaml
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    led = Ledger(cfg["account"]["handle"].lower())
    tk = Tickets(led)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        tk.sweep_expired()
        rows = tk.list_open()
        if not rows:
            print("无待审票据")
            return
        for d in rows:
            text = (d["payload"].get("text") or "")[:60]
            print(f"[{d['id']}] {d['kind']} @{d['payload'].get('target_handle') or '-'} "
                  f"剩{int((datetime.fromisoformat(d['expires_at']) - datetime.now(timezone.utc)).total_seconds() // 60)}分 "
                  f"理由:{d['rationale'][:40]}")
            print(f"    正文: {text}")
    elif cmd in ("approve", "reject"):
        tid = sys.argv[2]
        edited = sys.argv[3] if len(sys.argv) > 3 and cmd == "approve" else None
        try:
            d = tk.decide(tid, cmd == "approve", by="cli", edited_text=edited)
            print(f"{tid} → {d['status']}")
        except TicketError as e:
            print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("用法: tickets.py list | approve <id> [改稿] | reject <id>")


if __name__ == "__main__":
    main()
