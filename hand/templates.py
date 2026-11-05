# templates.py — P4-4 招呼类模板库 v2（MoA kimi+grok 审查后重做）
# v1 四处硬伤的修复：① 闸库同源（GREET_WORDS 单一词表驱动判定与词库）
#  ② 生成接上下文（熟络度分档身份标签、称呼配额过滤、时段开场）
#  ③ 名额语义：next_text 不写 used，票据 pending_review 后 mark_used（deny 不烧名额）
#  ④ 组合耗尽返回 None 由调用方降级 LLM（不再吐写死句）；双维度去重（全局 24h + 同 handle）
import json
import random
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR  # noqa: E402

# ── 招呼判定词表（唯一来源：判定闸与词库共用；加招呼语只改这张表） ──
GREET_WORDS = ["来了", "来咯", "来啦", "来了来了", "哈哈来了", "在的", "在", "收到", "嗯",
               "早", "早上好", "晚上好", "周末好", "你好", "您好", "嗨", "哈喽", "哈罗",
               "hello", "hi", "hey", "gm", "666", "老板", "兄弟", "1", "11", "111"]
_EMOJI_TAIL = "～~!！。,，. \t👍🔥💪🙏❤️🎉👏👋🙂😄😎🙌"
_GREET_ALT = "|".join(re.escape(w) for w in sorted(GREET_WORDS, key=len, reverse=True))
GREET_RE = re.compile(
    rf"^(?:(?:{_GREET_ALT})[{_EMOJI_TAIL}]*|[{_EMOJI_TAIL}]+)$")

# ── 回复词库 ──
OPENERS = ["来了", "来咯", "来了来了", "哈哈来了", "在的", "收到", "欢迎常来",
           "欢迎欢迎", "多交流", "互相学习", "常来坐", "见到你啦"]
IDENT_BY_FAM = {          # 身份标签按熟络度分档（陌生人客气，老熟人可称兄弟）
    "陌生": ["朋友", "Mutuals", "", "", ""],
    "眼熟": ["朋友", "老铁", "Mutuals", "", ""],
    "老熟人": ["兄弟", "老铁", "朋友", "", ""],
}
TAILS = ["多多交流。", "常来。", "慢聊。", "盘上见。", "🤝。", "一起盯。", ""]
SALUT_WORDS = ("老师", "老哥", "大佬", "兄弟", "大神")   # 与 contacts.SALUT_WORDS 同口径


def is_greeting(text: str) -> bool:
    """只放行无歧义纯招呼（词表驱动；空串不命中——v1 空串命中的修复）"""
    t = (text or "").strip()
    if not t:
        return False
    return bool(GREET_RE.match(t))


class Templates:
    def __init__(self, account: str):
        self.account = account
        self.path = STATE_DIR / f"templates-{account}.json"
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {"used": [], "per_handle": {}}   # 去重记忆（裸 except 收窄：只接可恢复态）
        self.data.setdefault("used", [])
        self.data.setdefault("per_handle", {})

    def _gc(self):
        cut = (datetime.now() - timedelta(hours=24)).isoformat()[:19]
        self.data["used"] = [u for u in self.data["used"] if u.get("at", "") >= cut]
        self.data["per_handle"] = {
            h: [t for t in ts if t.get("at", "") >= cut]
            for h, ts in self.data["per_handle"].items()}
        self.data["per_handle"] = {h: ts for h, ts in self.data["per_handle"].items() if ts}

    def next_text(self, handle: str, familiar: str = "陌生",
                  salutation_left: int = 3, hour: int = None) -> "str | None":
        """生成招呼回复；组合耗尽/全被去重滤掉 → None（调用方降级 LLM，v1 写死句已删）。
        不写 used——发送侧票据成立后由 mark_used 记账（deny 不烧名额）。"""
        from quality import check as _qcheck
        self._gc()
        h = datetime.now().hour if hour is None else hour
        openers = list(OPENERS)
        if 5 <= h < 11:
            openers += ["早", "早上好"]
        elif h >= 18:
            openers += ["晚上好"]
        idents = list(IDENT_BY_FAM.get(familiar, IDENT_BY_FAM["陌生"]))
        if salutation_left <= 0:   # 称呼配额用完：剔除受配额的身份标签
            idents = [i for i in idents if not any(w in i for w in SALUT_WORDS)] or [""]
        recent = {u["text"] for u in self.data["used"]}
        seen_h = {t["text"] for t in self.data["per_handle"].get(
            (handle or "").lower(), [])}
        for _ in range(60):
            ident = random.choice(idents)
            t = (random.choice(openers) + ("，" + ident if ident else "")
                 + random.choice(TAILS)).strip()
            if not t:
                continue
            if not t.endswith(("。", "！", "？", "～")):
                t += "。"
            if t in recent or t in seen_h or not _qcheck(t)["ok"]:
                continue
            return t
        return None   # 组合耗尽：降级信号

    def mark_used(self, text: str, handle: str):
        """票据 pending_review 后调用：全局 24h + 同 handle 双维度记账（原子写）"""
        import os
        now = datetime.now().isoformat()[:19]
        self.data["used"].append({"text": text, "at": now})
        self.data["per_handle"].setdefault((handle or "").lower(), []).append(
            {"text": text, "at": now})
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def used_count(self) -> int:
        self._gc()
        return len(self.data["used"])
