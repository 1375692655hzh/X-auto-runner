# inbox.py — X 通知事件的统一归一化、去重与对话归属（无浏览器/写动作副作用）
import hashlib
import json
import os
import re
from datetime import datetime, timedelta

from ledger import STATE_DIR, file_lock

EVENT_KINDS = {"reply", "mention", "follow", "like", "other"}


def normalize_events(raw, own_handle=""):
    """把 DOM 通知转成稳定事件；外部文本只作数据，永不作为指令。"""
    out, seen = [], set()
    own = (own_handle or "").lstrip("@").lower()
    for row in raw or []:
        actor = str(row.get("actor") or "").lstrip("@").strip().lower()
        if actor and not re.fullmatch(r"[a-z0-9_]{1,15}", actor):
            actor = ""
        if actor and actor == own:
            continue
        sid = str(row.get("status_id") or "")
        if sid and not re.fullmatch(r"\d{5,30}", sid):
            sid = ""
        kind = str(row.get("kind") or "other")
        if kind not in EVENT_KINDS:
            kind = "other"
        text = str(row.get("text") or "").strip()[:2500]
        nid = str(row.get("notification_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", nid):
            nid = hashlib.sha256(
                f"{kind}|{actor}|{sid}|{text}".encode("utf-8")).hexdigest()[:20]
        if nid in seen:
            continue
        seen.add(nid)
        out.append({"notification_id": nid, "kind": kind, "actor": actor,
                    "status_id": sid, "text": text})
    return out


class InboxState:
    """通知消费状态 + 对话归属。人工接管线程只允许人工继续，不由自动场景续回。"""

    def __init__(self, account):
        self.account = account
        self.path = STATE_DIR / f"inbox-{account}.json"

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        data.setdefault("consumed", {})
        data.setdefault("conversations", {})
        cutoff = (datetime.now() - timedelta(days=30)).isoformat()[:19]
        data["consumed"] = {k: v for k, v in data["consumed"].items() if v >= cutoff}
        return data

    def _save(self, data):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def consumed(self, notification_id):
        with file_lock(self.path):
            return notification_id in self._load()["consumed"]

    def mark_consumed(self, notification_id):
        with file_lock(self.path):
            data = self._load()
            data["consumed"][notification_id] = datetime.now().isoformat()[:19]
            self._save(data)

    def conversation(self, status_id):
        with file_lock(self.path):
            return dict(self._load()["conversations"].get(str(status_id)) or {})

    def register(self, status_id, origin, intent_id=""):
        if origin not in ("automation", "human"):
            raise ValueError("origin 仅支持 automation/human")
        with file_lock(self.path):
            data = self._load()
            data["conversations"][str(status_id)] = {
                "origin": origin, "intent_id": intent_id,
                "updated_at": datetime.now().isoformat()[:19]}
            self._save(data)
