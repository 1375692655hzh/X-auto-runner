# contacts.py — P4-4 互动账号池（四档数据面）
# 档位（requirements-actions.md §二）：互关 mutual / 大V bigv / 正常网友 normal / 黑名单 blacklist
#   大V、黑名单 = 人工确认（QQ 指令设）；互关 = 互关层动作或人工设；正常网友 = 默认
# 熟络度（butcher 借入）：按累计双向互动计数映射 老熟人(≥5)/眼熟(≥2)/陌生(<2)
# 称呼词日限（butcher 借入）：全局每日称呼词配额（老师/老哥/大佬/兄弟类），用完注入提示
# 对账：done intents（reply/comment/like 目标）回填统计——intents 目录即事实源，本池只做投影
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event, file_lock  # noqa: E402

TIERS = ("mutual", "bigv", "normal", "blacklist")
SALUT_WORDS = ("老师", "老哥", "大佬", "兄弟", "大神")


class Contacts:
    def __init__(self, account: str):
        self.account = account
        self.path = STATE_DIR / f"contacts-{account}.json"
        self.data = None   # 惰性：每次公开操作在 file_lock 内重读（跨进程写覆盖的修复）

    def _load(self) -> dict:
        if self.data is None:
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}
        self.data.setdefault("people", {})
        self.data.setdefault("salutation", {"date": "", "used": 0})
        return self.data

    # ── 基础 ──
    def _p(self, handle: str) -> dict:
        h = (handle or "").lstrip("@").strip().lower()
        if not h:
            raise ValueError("空 handle")
        self._load()   # 惰性自愈：内部调用方（含测试直摸）不必先显式 _load
        p = self.data["people"].setdefault(h, {
            "handle": h, "first_seen": datetime.now().isoformat()[:19],
            "updated_at": None, "tier": "normal",
            "we_follow": None, "they_follow": None,
            "stats": {"our_replies": 0, "our_comments": 0, "our_likes": 0,
                      "their_events": 0, "last_interaction": None, "last_seen": None},
            "note": None,
        })
        return p

    def save(self):
        import os
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        os.replace(tmp, self.path)   # 原子写（MoA 审查：半截文件连环损坏链）

    # ── 档位（全部公开操作走 file_lock + 读时重载：QQ 进程与 beat 进程并发安全） ──
    def tier(self, handle: str) -> str:
        with file_lock(self.path):
            self._load()
            try:
                return self._p(handle)["tier"]
            except ValueError:
                return "normal"

    def set_tier(self, handle: str, tier: str, note: str = "") -> dict:
        if tier not in TIERS:
            raise ValueError(f"未知档位 {tier}（合法：{TIERS}）")
        with file_lock(self.path):
            self._load()
            p = self._p(handle)
            p["tier"] = tier
            p["note"] = note[:80] or p.get("note")
            p["updated_at"] = datetime.now().isoformat()[:19]
            self.save()
        log_event("contacts", self.account, action="setTier", who=p["handle"], tier=tier)
        return p

    def set_follow(self, handle: str, we: bool):
        """follow/unfollow 动作回写 we_follow；互关判定=we_follow 且 they_follow"""
        with file_lock(self.path):
            self._load()
            p = self._p(handle)
            p["we_follow"] = we
            p["updated_at"] = datetime.now().isoformat()[:19]
            if we and p.get("they_follow") and p["tier"] == "normal":
                p["tier"] = "mutual"
            if not we and p["tier"] == "mutual":
                p["tier"] = "normal"
            self.save()

    def get(self, handle: str) -> dict:
        with file_lock(self.path):
            self._load()
            try:
                import copy
                return copy.deepcopy(self._p(handle))
            except ValueError:
                return {}

    def set_they_follow(self, handle: str, they: bool = True):
        """新关注通知回填 they_follow；只有双方关注都确认才升互关档。"""
        with file_lock(self.path):
            self._load()
            p = self._p(handle)
            p["they_follow"] = they
            p["updated_at"] = datetime.now().isoformat()[:19]
            if they and p.get("we_follow") and p["tier"] == "normal":
                p["tier"] = "mutual"
            self.save()

    # ── 熟络度（butcher 借入：累计互动计数轴） ──
    def familiar(self, handle: str) -> str:
        with file_lock(self.path):
            self._load()
            s = self._p(handle)["stats"]
        n = s["our_replies"] + s["our_comments"] + s["their_events"]
        if n >= 5:
            return "老熟人"
        if n >= 2:
            return "眼熟"
        return "陌生"

    def interaction_count(self, handle: str) -> int:
        with file_lock(self.path):
            self._load()
            s = self._p(handle)["stats"]
        return s["our_replies"] + s["our_comments"] + s["their_events"]

    # ── 关系槽注入值（场景 {relation_tier} 的四档枚举 + 熟络度轴，含称呼配额提示） ──
    def relation_slot(self, handle: str, salutation_cap: int = 3) -> str:
        zh = {"mutual": "互关", "bigv": "大V", "normal": "正常网友",
              "blacklist": "黑名单"}[self.tier(handle)]
        fam = self.familiar(handle)
        quota = self.salutation_left(salutation_cap)
        tip = "" if quota > 0 else "；今日称呼词配额已用完，开头不带任何称呼词"
        return f"{zh}（熟络度：{fam}{tip}）"

    # ── 称呼词日限（全局） ──
    def salutation_left(self, cap: int = 3) -> int:
        with file_lock(self.path):
            self._load()
            return self._salutation_left(cap)

    def _salutation_left(self, cap: int = 3) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        s = self.data["salutation"]
        if s.get("date") != today:
            return cap
        return max(0, cap - int(s.get("used") or 0))

    def salutation_used(self, text: str, cap: int = 3):
        """生成完成后调用：文案里带了称呼词则计数"""
        if not any(w in text for w in SALUT_WORDS):
            return
        with file_lock(self.path):
            self._load()
            today = datetime.now().strftime("%Y-%m-%d")
            s = self.data["salutation"]
            if s.get("date") != today:
                s.update({"date": today, "used": 0})
            s["used"] = int(s.get("used") or 0) + 1
            self.save()

    # ── 记账 ──
    def record_our(self, handle: str, kind: str):
        with file_lock(self.path):
            self._load()
            p = self._p(handle)
            key = {"reply": "our_replies", "comment": "our_comments",
                   "like": "our_likes", "follow": "our_likes"}.get(kind)
            if key:
                p["stats"][key] = p["stats"].get(key, 0) + 1
            p["stats"]["last_interaction"] = datetime.now().isoformat()[:19]
            p["updated_at"] = p["stats"]["last_interaction"]
            self.save()

    def record_their(self, handle: str):
        with file_lock(self.path):
            self._load()
            p = self._p(handle)
            p["stats"]["their_events"] = p["stats"].get("their_events", 0) + 1
            p["stats"]["last_seen"] = datetime.now().isoformat()[:19]
            p["updated_at"] = p["stats"]["last_seen"]
            self.save()

    # ── 对账回填：intents 目录（事实源）→ 池统计投影 ──
    def reconcile(self, intents_dir: Path = None) -> int:
        intents_dir = intents_dir or (STATE_DIR / "intents")
        n = 0
        with file_lock(self.path):
            self._load()
            marker = self.data.setdefault("_reconciled", {})
            for p in sorted(intents_dir.glob("*.json")):
                iid = p.stem
                if marker.get(iid) == "done":
                    continue   # 只跳过已入账的（非 done 不记 marker——grok S4：避免"见过未完成"永久漏计）
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue   # 半截文件：不入 marker，下轮重试
                if d.get("status") != "done":
                    continue
                payload = d.get("payload") or {}
                who = (payload.get("target_handle") or "").lstrip("@").lower()
                if who:
                    rec = self._p(who)
                    key = {"reply": "our_replies", "comment": "our_comments",
                           "like": "our_likes", "follow": "our_likes",
                           "unfollow": None}.get(d.get("kind"))
                    if key:
                        rec["stats"][key] = rec["stats"].get(key, 0) + 1
                    try:   # created_at 是 UTC——转本地再落（MoA 审查：与冷却的本地 now 混算失真）
                        ts = datetime.fromisoformat(d["created_at"]).astimezone()
                        rec["stats"]["last_interaction"] = ts.isoformat()[:19]
                    except Exception:
                        pass
                    n += 1
                marker[iid] = "done"
            if n:
                self.save()
        if n:
            log_event("contacts", self.account, action="reconcile", applied=n)
        return n

    # ── 视图 ──
    def summary(self) -> dict:
        with file_lock(self.path):
            self._load()
            people = {h: dict(p) for h, p in self.data["people"].items()}
        out = {"mutual": [], "bigv": [], "normal": [], "blacklist": []}
        for h, p in people.items():
            s = p.get("stats") or {}
            cnt = s.get("our_replies", 0) + s.get("our_comments", 0) + s.get("their_events", 0)
            fam = "老熟人" if cnt >= 5 else ("眼熟" if cnt >= 2 else "陌生")
            out[p.get("tier") or "normal"].append({"handle": h, "fam": fam, "n": cnt})
        for k in out:
            out[k].sort(key=lambda x: -x["n"])
        return {k: v for k, v in out.items() if v}


# ── 招呼类判定：词表驱动的唯一实现移驻 templates.py（闸库同源），此处再导出兼容旧引用 ──
from templates import is_greeting  # noqa: E402,F401
