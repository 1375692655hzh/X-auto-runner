# quality.py — 中文文案质量门（P3-5）：移植 x-auto-run quality.js
# 审计实测三缺陷的修复语义：①纯中文具体评论不误判 generic ②中文 hashtag 清除
# ③两次不合格仍放行。本版：**不合格一律拒发**（QUALITY_REJECT，绝不放行不重试）。
# 用法:
#   from quality import check
#   r = check(text, history=[近7天已发文案])
#   r["ok"] 为 True 才可发送；发送用 r["cleaned"]（已去 hashtag/链接/白空）
import re
import unicodedata

CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3400-\u4DBF\u3040-\u30FF\uAC00-\uD7AF\u3000-\u303F\uFF01-\uFF60]")

# 通用开场黑名单：以这些开头=无信息量套话（只匹配开头，不误杀句中引用）
GENERIC_OPENERS = [
    # 中文
    "顶", "沙发", "前排", "支持", "赞", "好文", "好帖", "写得真好", "说得太好", "说得好", "讲得好",
    "学习了", "学到了", "受教了", "收藏了", "mark", "蹲一个", "蹲后续", "有意思", "不错", "很好",
    "厉害", "牛", "牛逼", "大佬", "强", "绝了", "爱了", "加油", "冲", "同感", "认同", "同意",
    # 英文（继承 XActions 思路）
    "great post", "great point", "great thread", "great take", "love this", "this is so true", "so true",
    "well said", "couldn't agree more", "could not agree more", "thanks for sharing", "thank you for sharing",
    "interesting take", "interesting perspective", "as an ai", "as a language model",
]

_FENCE_OPEN = re.compile(r"```[a-z]*\n?", re.I)
_FENCE_CLOSE = re.compile(r"```\s*$")
_REPLY_PREFIX = re.compile(r"^\s*(回复|reply|comment)\s*[:：]\s*", re.I)
_URL = re.compile(r"https?://\S+")
_HASHTAG = re.compile(r"[＃#][^\s＃#，。！？、]+")
_WRAP_QUOTE = re.compile(r'^["“”\'‘’](.+)["“”\'‘’]$', re.S)
_LEAD_PUNCT = re.compile(r"^[^a-z\u4e00-\u9fff]+")
# 结尾标点判据（ref quality.js 同义；emoji 以 Unicode So 类近似 \p{Extended_Pictographic}）
ENDING_PUNCT = set("。！？…～））》」』”’\"'")
OPENERS_LOWER = [o.lower() for o in GENERIC_OPENERS]


def weighted_len(s: str) -> int:
    """X 的 280 加权计数：CJK/全角算 2，其余算 1"""
    return sum(2 if CJK_RE.match(ch) else 1 for ch in s)


def clean(text: str):
    """清洗：去代码围栏/“回复：”前缀/URL/话题标签/多余水平空白；保留纯文本排版。
    CRLF/CR统一为LF；单换行用于短句/列表，双换行用于自然段，3+换行收敛为一个空行。
    返回 (cleaned, removed_len)"""
    before = text or ""
    t = before.replace("\r\n", "\n").replace("\r", "\n")
    t = _FENCE_OPEN.sub("", t)
    t = _FENCE_CLOSE.sub("", t)
    t = _REPLY_PREFIX.sub("", t)
    t = _URL.sub("", t)
    t = _HASHTAG.sub("", t)
    # 行尾水平空白先去掉，空白行由此成为真正的空行；绝不把换行当普通空白压缩
    t = re.sub(r"[^\S\n]+(?=\n|$)", "", t)
    t = re.sub(r"[^\S\n]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    m = _WRAP_QUOTE.match(t)
    if m:
        t = m.group(1).strip()
    return t, len(before) - len(t)


def _is_emoji(ch: str) -> bool:
    # stdlib re 无 \p{Extended_Pictographic}；So 类覆盖绝大多数 emoji（含 ©®等图形符号，与 ref 口径接近）
    return unicodedata.category(ch) == "So"


def _bigrams(s: str) -> set:
    t = re.sub(r"\s+", "", s or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


def similarity(a: str, b: str) -> float:
    """CJK 二元组 Jaccard 相似度（防模板化重复；inter/min 口径与 ref 一致）"""
    A, B = _bigrams(a), _bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def check(text, history=None, max_len=280, min_len=8, sim_threshold=0.55, casual=False):
    """质量门。返回 {ok, reason, weighted_len, cleaned}。
    max_len=None 表示账号有长文权限（蓝 V），不检查长度上限；其余质量规则不变。
    规则（任一不过即 ok=False，调用方拒发，不放行不重试）：
      1) 非空且清洗后仍非空  2) 加权长度 ≥8；非蓝 V 另须 ≤280  3) 不以通用套话开场（中英）
      4) emoji ≤2             5) 与近 7 天已发文案相似度 < 0.55
    casual=True：短口语回复场景——豁免“套话开场”与“结尾标点”两条
      （互关社区“来了来了”是合法社交应答，无句号是口语特征不是截断）；长度/emoji/相似度照常。"""
    history = history or []
    cleaned, _ = clean(text)
    if not (text or "").strip():
        return {"ok": False, "reason": "空文案", "weighted_len": 0, "cleaned": cleaned}
    if not cleaned:
        return {"ok": False, "reason": "清洗后为空（只剩链接/标签？）", "weighted_len": 0, "cleaned": cleaned}
    ln = weighted_len(cleaned)
    if max_len is not None and ln > max_len:
        return {"ok": False, "reason": f"超长({ln}>{max_len})", "weighted_len": ln, "cleaned": cleaned}
    if ln < min_len:
        return {"ok": False, "reason": f"过短({ln}<{min_len})，无信息量", "weighted_len": ln, "cleaned": cleaned}
    if not casual:
        lower = _LEAD_PUNCT.sub("", cleaned.lower(), count=1)
        hit = next((o for o in OPENERS_LOWER if lower.startswith(o)), None)
        if hit:
            # 单字套话（牛/强/顶/赞/冲…）只在"整句就是它+标点"时才误杀豁免——
            # 「牛市见顶信号…」「顶住压力」是合法内容（review 中6）；「牛。」「赞！」是灌水
            if len(hit) > 1 or not re.match(rf"^{re.escape(hit)}[！!。？?～\s]*$", cleaned):
                return {"ok": False, "reason": f"通用套话开场：“{hit}”", "weighted_len": ln, "cleaned": cleaned}
        if cleaned and not (cleaned[-1] in ENDING_PUNCT or _is_emoji(cleaned[-1])):
            return {"ok": False, "reason": "疑似截断（结尾无标点）", "weighted_len": ln, "cleaned": cleaned}
    emojis = sum(1 for ch in cleaned if _is_emoji(ch))
    if emojis > 2:
        return {"ok": False, "reason": f"emoji过多({emojis})", "weighted_len": ln, "cleaned": cleaned}
    for h in history:
        s = similarity(cleaned, h)
        if s >= sim_threshold:
            return {"ok": False, "reason": f"与已发文案相似({s:.2f})", "weighted_len": ln, "cleaned": cleaned}
    return {"ok": True, "reason": "", "weighted_len": ln, "cleaned": cleaned}


if __name__ == "__main__":
    # 快速自检（完整用例见 tests/quality_test.py）
    assert check("")["ok"] is False
    assert check("顶，说得好")["ok"] is False
    assert check("来了来了～", casual=True)["ok"] is True
    good = check("这波蒸馏把部署成本压下来了，但批调度还是吞吐瓶颈。")
    assert good["ok"] is True, good
    print("quality selftest OK")
