# cognitive.py — P4-3 心跳认知循环：醒来→收上下文→选场景→L1+L2+L3 组装→LLM→SKIP/票据
# 场景（v1）：reply-original（自家帖下未回评论，#4）｜post-original（时间线素材成帖，#1）｜
#             report（日推运营报告，#13——走 QQ 文件通道，不经质量门/票据，非写动作）
# 铁律不变：本模块零直接写，一切 X 写动作经 loopback /execute → 质量门 → 票据 → 人审。
# 提示词装配口径：L1 人设卡全文 → L2「System Prompt 正文」→ L3 场景「正文」+「输出契约」
# （prompts/* 各文件「备注」声明的拼接范围；SKIP 哨兵在质量门前拦截）。
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402
from contacts import Contacts, is_greeting  # noqa: E402
from templates import Templates  # noqa: E402

PROMPTS = Path("/etc/xar/prompts")
PERSONA = Path("/etc/xar/persona-card.md")
COG_STATE = STATE_DIR / "cognitive.json"


# ── 基础设施 ──
def _token():
    return Path("/etc/xar/hand.token").read_text(encoding="utf-8").strip()


def xc(method, path, body=None, timeout=90):
    """loopback XClient 调用（与 qq_bot 同口径）"""
    req = urllib.request.Request(f"http://127.0.0.1:8741{path}", method=method,
                                 headers={"Authorization": f"Bearer {_token()}",
                                          "Content-Type": "application/json"})
    if body is not None:
        req.data = json.dumps(body, ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code}
    except Exception as e:
        return {"_err": str(e)[:120]}


def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0",
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def _load_state():
    try:
        return json.loads(COG_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"consumed": {}, "last_report": "", "last_post_date": "", "beats": 0}


def _save_state(st):
    import os
    tmp = COG_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, COG_STATE)   # 原子写（MoA 审查：半截状态文件）


# ── 提示词装配 ──
def _section(text, heading, level=2):
    """取 heading 标题之后到下一个 ≤level 级标题之前（### 子节不切断正文）"""
    out, capturing = [], False
    for ln in text.splitlines():
        m = re.match(r"^(#{2,6})\s", ln)
        if m:
            if capturing and len(m.group(1)) <= level:
                break
            if ln.strip() == heading:
                capturing = True
                continue
        if capturing:
            out.append(ln)
    return "\n".join(out).strip()


def assemble(scene_id, slots):
    l1 = PERSONA.read_text(encoding="utf-8").strip()
    l2 = _section((PROMPTS / "L2-通用行为准则.md").read_text(encoding="utf-8"),
                  "## System Prompt 正文")
    sc = (PROMPTS / "scenes" / f"{scene_id}.md").read_text(encoding="utf-8")
    body = _section(sc, "## System Prompt 正文")
    contract = _section(sc, "## 输出契约")
    prompt = (f"{l1}\n\n---\n\n{l2}\n\n---\n\n{body}\n\n---\n\n"
              f"# 输出契约（最终裁决）\n{contract}")
    for k, v in slots.items():
        prompt = prompt.replace("{" + k + "}", str(v))
    # 可选槽未注入（场景规格：未注入视为无）
    return re.sub(r"\{[a-z_]+\}", "（未注入）", prompt)


# ── 上下文采集 ──
def recent_history(n=8):
    """近 7 天已发文案（intents 目录即事实源，与 server._history_texts 同口径）"""
    cutoff = time.time() - 7 * 86400
    out = []
    for p in sorted((STATE_DIR / "intents").glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("status") == "done" and d.get("kind") in ("post", "reply", "comment"):
            try:
                ts = datetime.fromisoformat(d["created_at"]).timestamp()
            except Exception:
                ts = 0
            if ts >= cutoff:
                t = (d.get("payload") or {}).get("text")
                if t:
                    out.append(str(t))
    return out[-n:]


def _fx_results(d):
    """fxtwitter 当前 results 在顶层；兼容旧 data.results 包装。"""
    return d.get("results") or (d.get("data") or {}).get("results") or []


def own_recent_posts(handle, hours=48):
    d = get_json(f"https://api.fxtwitter.com/2/profile/{handle}/statuses")
    res = _fx_results(d)
    cutoff = (time.time() - hours * 3600) * 1000
    return [x for x in res
            if not x.get("replying_to") and not x.get("reposted_by")
            and (x.get("created_timestamp") or 0) > cutoff]


def _has_media(s) -> bool:
    m = s.get("media") or {}
    return (len(m.get("photos") or []) + len(m.get("videos") or [])
            + len(m.get("all") or [])) > 0


def member_latest_post(handle, hours=72):
    """互关成员专属采样器（ref planner.fetchLatestPost 移植）：fxtwitter 取最新一条
    自发非回复帖（≤72h），带图帖沿时间线向下找无媒体帖；全带图/无新帖 → None 不凑数。
    不赌首页时间线算法——2026-10-28 串门供给必达改造。"""
    d = get_json(f"https://api.fxtwitter.com/2/profile/{handle}/statuses")
    res = _fx_results(d)
    h = handle.lower()
    cutoff = (time.time() - hours * 3600) * 1000
    own = [s for s in res
           if ((s.get("author") or {}).get("screen_name") or "").lower() == h
           and not s.get("reposted_by") and not s.get("replying_to")
           and (s.get("created_timestamp") or 0) > cutoff]
    own.sort(key=lambda x: -(x.get("created_timestamp") or 0))
    for s in own:
        if not _has_media(s):   # 带媒体帖默认不评论（requirements 裁决①）
            return s
    return None


def _judge_worth(llm, post_text, author, fail="skip") -> str:
    """价值预判（ref llm.judgeWorthiness 移植）：灌水/纯表情帖不值得评。
    失败语义按场景取：热门路 fail=skip（陌生人 turf 谨慎，MoA 审查改判 fail-closed）；
    低风险路可传 fail=open（宁多判不漏判，交给主场景 SKIP 纪律兜底）。
    注：简单价值判定走 llm.decision（阿里决策通道），失败回退实例 default。"""
    try:
        r = llm.chat("decision",
                     "你是 X 社区的帖子筛选器。判断这条帖子值不值得一个认真做交易观察的"
                     "博主去评论。标准：有具体内容或明确互动意图=值得；纯表情/单字灌水/"
                     "无意义字符=不值得。只输出一个字：值 或 不值",
                     f"@{author}: {str(post_text)[:200]}",
                     max_tokens=500, retries=0)
        return "不值" if "不" in (r or "").strip()[:3] else "值"
    except Exception:
        return "skip" if fail == "skip" else "值"


CASUAL_SCENES = {"post-mutual", "reply-mutual", "comment-mutual-visit"}


def _draft_scene(llm, scene, slots, fn, maxtok, extra_task=""):
    """场景起草 + 质量门未过重写一次（ref「交付必达」语义移植：门淘汰不丢计划量；
    两版都败则交第一版给权威票据闸裁决）。返回 (text, skipped)。"""
    from quality import check as qcheck
    qopts = {}
    if scene == "post-original":
        from account_status import post_max_len
        qopts["max_len"] = post_max_len()
    task = ("执行上述场景任务。只输出正文本身（回复/评论/成稿），或按契约整条输出 SKIP。"
            + (extra_task or ""))
    out = (llm.chat(fn, assemble(scene, slots), task, max_tokens=maxtok) or "").strip()
    if out.upper() == "SKIP" or out.upper().startswith("SKIP\n"):
        return out, True
    r = qcheck(out, casual=scene in CASUAL_SCENES, **qopts)
    if r["ok"]:
        return out, False
    try:  # 重写一版（带上失败原因）
        out2 = (llm.chat(fn,
                         assemble(scene, slots)
                         + f"\n\n（上一版未过质量门：{r['reason']}。重写一版，务必满足输出契约与长度。）",
                         task, max_tokens=maxtok) or "").strip()
        if out2 and out2.upper() != "SKIP":
            r2 = qcheck(out2, casual=scene in CASUAL_SCENES, **qopts)
            if r2["ok"]:
                return out2, False
    except Exception:
        pass
    return out, False   # 两版皆败：交第一版给权威门（deny 即弃，不硬凑第三版）


def _harvest(account, posts):
    """素材收割（ref planner 互关帖预制参考池移植）：采样帖滚动留 30 条，供发帖场景参考"""
    try:
        f = STATE_DIR / f"material-{account}.json"
        m = {"posts": []}
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
        for p in posts:
            if p.get("text"):
                m["posts"].append({"at": datetime.now().isoformat()[:19],
                                   "author": p.get("author"), "text": str(p["text"])[:80]})
        m["posts"] = m["posts"][-30:]
        f.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# ── 场景执行 ──
def _try_inbox(llm, account, handle, st, pool):
    """统一通知入口：回复/提及分场景起草；新关注只出回关待审票。
    每轮最多一张票，人工接管线程不自动续回。"""
    from inbox import InboxState, normalize_events
    r = xc("POST", "/read/notifications", {"limit": 30}, timeout=150)
    if r.get("error") or r.get("_http") or r.get("_err"):
        log_event("cognitive", account, action="inboxAbort", note=str(r)[:160])
        return False
    ib = InboxState(account)
    events = [e for e in normalize_events(r.get("notifications"), handle)
              if not ib.consumed(e["notification_id"])]
    for e in [x for x in events if x["kind"] in ("reply", "mention")]:
        nid, sid, author = e["notification_id"], e["status_id"], e["actor"]
        if not sid or not author or pool.tier(author) == "blacklist":
            ib.mark_consumed(nid)
            continue
        conv = ib.conversation(sid)
        if conv.get("origin") == "human":
            ib.mark_consumed(nid)
            log_event("cognitive", account, action="humanThreadSkip", status_id=sid)
            continue
        detail = xc("POST", "/read/post", {"status_id": sid}, timeout=120)
        if detail.get("error") or not detail.get("post"):
            # 读取失败不烧通知；本轮终止，避免旧回复路由对同一评论再开票
            log_event("cognitive", account, action="inboxReadRetry", status_id=sid)
            return True
        parent = detail["post"]
        if (parent.get("author") or "").lower() == handle.lower():
            scene = "reply-original"
        elif pool.tier(author) == "mutual":
            scene = "reply-mutual"
        elif conv:
            scene = "reply-visit"
        else:
            ib.mark_consumed(nid)  # 无上下文的陌生提及不强行回复
            continue
        slots = {"target_author": author, "target_post": parent.get("text") or "",
                 "thread_context": e.get("text") or "", "relation_tier": pool.relation_slot(author),
                 "recent_history": "\n".join(f"· {t}" for t in recent_history())}
        text, skipped = _draft_scene(llm, scene, slots, "reply", 1800)
        if skipped:
            ib.mark_consumed(nid)
            continue
        iid = f"cog-i-{nid}"
        res = xc("POST", "/execute", {
            "kind": "reply", "intent_id": iid, "text": text, "status_id": sid,
            "post_url": f"https://x.com/i/web/status/{sid}", "target_handle": author,
            "target_len": len(parent.get("text") or ""), "casual": scene != "reply-original",
            "rationale": f"通知回复（{scene}）：@{author}"}, timeout=120)
        status = res.get("status")
        if status in ("pending_review", "approved", "done", "unknown", "failed"):
            ib.mark_consumed(nid)
            ib.register(sid, "automation", iid)
            pool.record_their(author)
            pool.salutation_used(text)
            log_event("cognitive", account, action="ticket", scene=scene,
                      ticket=res.get("ticket_id") or iid, status=status)
            return True
        code = (res.get("denied") or {}).get("code")
        if code in ("CAP_EXHAUSTED", "WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
            # 可恢复拒因保留通知并停止本轮，防止 _try_reply 对同一条重复起草
            log_event("cognitive", account, action="inboxRetry", scene=scene, code=code)
            return True
        ib.mark_consumed(nid)
        log_event("cognitive", account, action="deny", scene=scene, note=str(res)[:240])

    for e in [x for x in events if x["kind"] == "follow"]:
        nid, author = e["notification_id"], e["actor"]
        if not author:
            ib.mark_consumed(nid)
            continue
        person = pool.get(author)
        pool.set_they_follow(author, True)
        if person.get("we_follow") or person.get("tier") == "blacklist":
            ib.mark_consumed(nid)
            continue
        iid = f"cog-f-{nid}"
        res = xc("POST", "/execute", {
            "kind": "follow", "intent_id": iid, "target_handle": author,
            "rationale": "条件回关建议：收到新关注通知，未关注且非黑名单"}, timeout=120)
        status = res.get("status")
        if status in ("pending_review", "approved", "done", "unknown", "failed"):
            ib.mark_consumed(nid)
            log_event("cognitive", account, action="followbackTicket",
                      ticket=res.get("ticket_id") or iid, status=status)
            return True
        code = (res.get("denied") or {}).get("code")
        if code in ("CAP_EXHAUSTED", "WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
            log_event("cognitive", account, action="inboxRetry", scene="followback", code=code)
            return True
        ib.mark_consumed(nid)
    return False


def _try_reply(llm, account, handle, st, pool):
    """#4 reply-original：最新原创帖（≤48h）下未处理的评论 → 回复票
    P4-4：① 招呼类评论走模板库旁路（butcher 光速回复路，零 LLM）
          ② {relation_tier} 注入真实档位+熟络度+称呼配额（互动池数据面）"""
    posts = own_recent_posts(handle)
    if not posts:
        return False
    parent = posts[0]
    sid = str(parent.get("id"))
    r = xc("POST", "/read/post", {"status_id": sid}, timeout=120)
    if r.get("error") or r.get("_http") or r.get("_err"):
        log_event("cognitive", account, action="replyAbort",
                  note=f"read/post 不可用:{str(r)[:60]}")
        return False
    me = handle.lower()
    cands = [c for c in (r.get("replies") or [])
             if (c.get("author") or "").lower() != me
             and (c.get("text") or "").strip()
             and pool.tier(c.get("author") or "") != "blacklist"  # 黑名单不互动
             and not st["consumed"].get(c.get("id"))]
    if not cands:
        return False
    c = cands[0]
    cid = str(c.get("id"))
    author = c.get("author") or ""
    pool.record_their(author)  # 对方来互动，计入熟络度
    text, src = "", ""
    if is_greeting(c.get("text") or ""):
        # 模板旁路（v2）：接上下文（熟络度/称呼配额/时段）；耗尽返回 None 降级 LLM
        text = Templates(account).next_text(
            author, pool.familiar(author), pool.salutation_left(),
            datetime.now().hour)
        src = "模板库" if text else "LLM降级"
    if not text:
        slots = {
            "target_author": author,
            "thread_context": c.get("text") or "",
            "target_post": parent.get("text") or "",
            "relation_tier": pool.relation_slot(author),
            "recent_history": "\n".join(f"· {t}" for t in recent_history()),
        }
        out = llm.chat("reply", assemble("reply-original", slots),
                       "执行上述场景任务。只输出回复正文本身，或整条输出 SKIP。",
                       max_tokens=1500)
        text = (out or "").strip()
        if not src:
            src = "LLM"
    if text.upper() == "SKIP" or text.upper().startswith("SKIP\n") or text == "SKIP":
        st["consumed"][cid] = datetime.now().isoformat()[:19]
        log_event("cognitive", account, action="skip", scene="reply-original")
        return False
    r2 = xc("POST", "/execute", {
        "kind": "reply", "intent_id": f"cog-r-{cid[-10:]}",
        "text": text, "status_id": sid,
        "post_url": f"https://x.com/i/web/status/{sid}",
        "target_handle": author,
        "target_len": len(parent.get("text") or ""),
        "rationale": f"自动起草（{src}）：回复 @{author} 在我帖下的评论",
    }, timeout=120)
    if r2.get("status") == "pending_review":
        st["consumed"][cid] = datetime.now().isoformat()[:19]
        if src == "模板库":   # 名额在票据成立后才烧（deny 不烧，MoA 审查高③）
            Templates(account).mark_used(text, author)
        pool.salutation_used(text)
        from inbox import InboxState
        InboxState(account).register(sid, "automation", f"cog-r-{cid[-10:]}")
        log_event("cognitive", account, action="ticket", scene="reply-original",
                  via=src, ticket=r2.get("ticket_id"))
        return True
    d = r2.get("denied") or {}
    if d.get("code") in ("CAP_EXHAUSTED", "WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
        st["consumed"].pop(cid, None)
    log_event("cognitive", account, action="deny", scene="reply-original",
              note=str(r2.get("denied") or r2)[:300])
    return False


def _try_visit(llm, account, st, pool, media_gate):
    """#7 comment-mutual-visit：互关成员新帖首访评论。
    v2 采样器制（ref planner.planVisit 移植）：逐成员 fxtwitter 直取最新帖（不赌时间线
    算法）；冷却：同成员 7 天 1 条；72h 无新帖不凑数；素材顺带收割。"""
    mutuals = [h for h, p in (pool.data.get("people") or {}).items()
               if p.get("tier") == "mutual"]
    if not mutuals:
        return False
    cand = None
    for h in sorted(mutuals):   # 稳定顺序：先到先服务可预期
        s = member_latest_post(h)
        if not s:
            continue
        sid = str(s.get("id"))
        if st["consumed"].get(sid):
            continue
        last = ((pool.data["people"][h].get("stats") or {}).get("last_interaction"))
        if last:
            try:
                if (datetime.now() - datetime.fromisoformat(last)).days < 7:
                    continue   # 冷却：7 天内已互动过的成员跳过（ref 互关 7 天 1 条）
            except Exception:
                pass
        cand = (h, s)
        break
    if not cand:
        return False
    h, s = cand
    sid = str(s.get("id"))
    _harvest(account, [{"author": h, "text": s.get("text")}])
    slots = {
        "target_author": h,
        "target_post": s.get("text") or "",
        "relation_tier": pool.relation_slot(h),
        "recent_history": "\n".join(f"· {t}" for t in recent_history()),
    }
    text, skipped = _draft_scene(llm, "comment-mutual-visit", slots, "comment", 2500)
    st["consumed"][sid] = datetime.now().isoformat()[:19]
    if skipped:
        log_event("cognitive", account, action="skip", scene="comment-mutual-visit")
        return False
    r2 = xc("POST", "/execute", {
        "kind": "comment", "intent_id": f"cog-v-{sid[-10:]}",
        "text": text, "status_id": sid,
        "post_url": f"https://x.com/i/web/status/{sid}",
        "target_handle": h, "target_len": len(s.get("text") or ""),
        "casual": True,
        "rationale": f"自动起草：串门评论互关成员 @{h} 的新帖",
    }, timeout=120)
    if r2.get("status") == "pending_review":
        pool.record_our(h, "comment")   # 冷却自开票起算（防票 pending 期间重复开票）
        pool.salutation_used(text)
        from inbox import InboxState
        InboxState(account).register(sid, "automation", f"cog-v-{sid[-10:]}")
        log_event("cognitive", account, action="ticket", scene="comment-mutual-visit",
                  ticket=r2.get("ticket_id"))
        return True
    d = r2.get("denied") or {}
    if d.get("code") in ("CAP_EXHAUSTED", "WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
        st["consumed"].pop(sid, None)   # 可重试拒因：素材不弃（MoA 审查：deny 永久烧素材）
    log_event("cognitive", account, action="deny", scene="comment-mutual-visit",
              note=str(r2.get("denied") or r2)[:300])
    return False


def _try_hot(llm, account, handle, st, pool, media_gate):
    """#8 comment-hot：explore 面热门帖评论（陌生人 turf，高级档严格口径）。
    管线（ref 价值预判 + 10-22 巡逻语义移植）：explore 采样 → 基础过滤（自家/池内/
    已消费/媒体帖/灌水长度）→ 价值预判（不值即弃，fail-open）→ #8 场景起草 → 票据。
    作者冷却 21 天（ref「其他 21 天 1 条」）。"""
    tl = xc("POST", "/read/timeline", {"surface": "explore", "limit": 20}, timeout=150)
    if tl.get("error") or tl.get("_http") or tl.get("_err"):
        log_event("cognitive", account, action="hotAbort",
                  note=f"read/timeline(explore) 不可用:{str(tl)[:300]}")
        return False
    me = handle.lower()
    hot_authors = st.setdefault("hot_authors", {})
    hot_authors = {k: v for k, v in hot_authors.items()
                   if (datetime.now() - datetime.fromisoformat(v)).days < 21}
    cands = []
    for p in (tl.get("posts") or []):
        author = (p.get("author") or "").lower()
        if (not p.get("text") or not author or author == me
                or st["consumed"].get(p.get("id"))):
            continue
        if pool.tier(author) != "normal":     # 池内成员走各自专属场景（串门/#9），不走热门路
            continue
        if hot_authors.get(author):
            continue                            # 21 天冷却
        if media_gate == "skip" and p.get("has_media"):
            continue
        if len(p.get("text") or "") < 20:       # 灌水第一道（<20 字不值得高级档出手）
            continue
        cands.append(p)
    if not cands:
        return False
    _harvest(account, cands[:5])
    for p in cands[:5]:
        author = p.get("author") or ""
        w = _judge_worth(llm, p.get("text"), author)
        if w != "值":   # 不值=灌水；skip=判定不可用——陌生人 turf 一律谨慎放弃
            st["consumed"][str(p.get("id"))] = datetime.now().isoformat()[:19]
            log_event("cognitive", account, action="worthSkip" if w == "不值" else "worthUnknown",
                      who=author[:20])
            continue
        sid = str(p.get("id"))
        slots = {
            "target_author": author,
            "target_post": p.get("text") or "",
            "thread_context": "（未注入）",
            "relation_tier": "正常网友（陌生·热门帖首评，恒 stranger）",
            "recent_history": "\n".join(f"· {t}" for t in recent_history()),
        }
        text, skipped = _draft_scene(llm, "comment-hot", slots, "comment", 2500)
        st["consumed"][sid] = datetime.now().isoformat()[:19]
        if skipped:
            log_event("cognitive", account, action="skip", scene="comment-hot")
            return False
        r2 = xc("POST", "/execute", {
            "kind": "comment", "intent_id": f"cog-h-{sid[-10:]}",
            "text": text, "status_id": sid,
            "post_url": f"https://x.com/i/web/status/{sid}",
            "target_handle": author, "target_len": len(p.get("text") or ""),
            "rationale": f"自动起草：热门帖评论（explore 采样+价值预判通过）",
        }, timeout=120)
        if r2.get("status") == "pending_review":
            hot_authors[author] = datetime.now().isoformat()[:19]
            st["hot_authors"] = hot_authors
            pool.salutation_used(text)
            from inbox import InboxState
            InboxState(account).register(sid, "automation", f"cog-h-{sid[-10:]}")
            log_event("cognitive", account, action="ticket", scene="comment-hot",
                      ticket=r2.get("ticket_id"))
            return True
        d = r2.get("denied") or {}
        if d.get("code") in ("CAP_EXHAUSTED", "WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
            st["consumed"].pop(sid, None)
        log_event("cognitive", account, action="deny", scene="comment-hot",
                  note=str(r2.get("denied") or r2)[:300])
        return False
    return False


def _select_news_material(llm, items, history):
    """资讯源自动发布的选题闸：主脑从两类信息库中挑一条，没值得发的就明确放弃。
    返回 (item|None, 理由, 是否完成有效评判)；协议/模型失败不消费素材，允许下拍重试。"""
    lines = []
    for i, it in enumerate(items, 1):
        head = it.get("title") or (it.get("text") or "")[:60]
        lines.append(f"【{i}】[{it.get('form')}·{it.get('source')}·{it.get('time')}] "
                     f"{head}\n{(it.get('text') or '')[:260]}")
    hist = "\n".join(f"· {t[:100]}" for t in history[-6:]) or "（无）"
    system = """你是美股交易观察者账号的资讯编辑。只从候选素材里选一条最值得发成 X 原创帖的，不负责写正文。

选题标准：
1. 有具体、鲜活的一手信息：明确事件/数据/变化，不是空泛评论或广告。
2. 对美股科技、宏观、中国科技公司（腾讯/阿里/字节）或中国模型公司（智谱/Kimi/DeepSeek）读者有实际信息价值；资讯与精品文章平等竞争，不因长短优先。
3. 能把事情讲清楚，并能从素材本身提炼出一层可靠分析；事实太少的不要选。
   中国模型公司只选能力、开源、价格、商业化、融资、算力或重大组织变化等实质增量，小版本更新和宣传稿不选。
4. 避开近 7 天已发主题；同主题没有新增量也不要选。
5. 没有合格素材就选 0，宁可不发，不硬凑。

只输出 JSON：{"pick": 候选序号, "why": "一句话选题理由"}；全部不值得发用 {"pick": 0, "why": "原因"}。"""
    user = f"【候选素材】\n" + "\n\n".join(lines) + f"\n\n【近7天已发】\n{hist}"
    out = llm.chat("draft", system, user, max_tokens=700, timeout=60, retries=2)
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        return None, "选题输出非JSON", False
    try:
        d = json.loads(m.group(0))
        pick, why = int(d.get("pick", -1)), str(d.get("why") or "")[:100]
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, "选题JSON无效", False
    if pick == 0:
        return None, why or "主脑判定无值得发布的素材", True
    if not 1 <= pick <= len(items):
        return None, f"选题序号越界:{pick}", False
    return items[pick - 1], why, True


def _try_post(llm, account, st):
    """#1 资讯源自动发布：固定信息库（资讯+精品文章）→ 主脑选题 → 单源全文成稿。
    数据站不可用/无值得发素材时不退到时间线，确保任务边界纯粹；每日最多一票。"""
    from datastation import fetch_material, format_material
    now_iso = datetime.now().isoformat()[:19]
    try:
        # 分开取，避免高频资讯把低频精品文章挤出候选池
        items = (fetch_material(forms=("资讯",), limit=8, hours=8, text_limit=1400)
                 + fetch_material(forms=("精品文章",), limit=6, hours=24,
                                  text_limit=1800))
    except Exception as e:
        log_event("cognitive", account, action="postAbort",
                  note=f"固定信息库读取失败:{str(e)[:120]}")
        return False
    items = [it for it in items
             if (it.get("text") or "").strip()
             and not st["consumed"].get(f"ds:{it.get('id')}")]
    if not items:
        log_event("cognitive", account, action="skip", scene="news-select",
                  note="固定信息库暂无未评素材")
        return False
    try:
        selected, why, judged = _select_news_material(llm, items, recent_history())
    except Exception as e:
        log_event("cognitive", account, action="selectFail", note=str(e)[:120])
        return False
    if not judged:
        log_event("cognitive", account, action="selectFail", note=why)
        return False
    # 完成有效评判即消费本批：无价值不反复评，落选素材不在后续拍里复活
    for it in items:
        st["consumed"][f"ds:{it.get('id')}"] = now_iso
    if selected is None:
        log_event("cognitive", account, action="skip", scene="news-select", note=why)
        return False
    log_event("cognitive", account, action="select", scene="news-select",
              source=selected.get("source"), form=selected.get("form"), note=why)
    slots = {"source_material": format_material([selected]),
             "recent_history": "\n".join(f"· {t}" for t in recent_history())}
    text, skipped = _draft_scene(llm, "post-original", slots, "draft", 3000)
    text = (text or "").strip()
    if skipped or not text:
        log_event("cognitive", account, action="skip", scene="post-original")
        return False
    from news_auto import _apply_growth_tags
    from tickers import apply_ticker_line
    from account_status import post_max_len
    text, ticker_line = apply_ticker_line(text, llm, selected, max_len=post_max_len())
    text, growth_tagged = _apply_growth_tags(text)
    today = datetime.now().strftime("%Y-%m-%d")
    rationale = (f"资讯源自动发布：{selected.get('form')}·{selected.get('source')}·"
                 f"{(selected.get('title') or '')[:36]}；选题：{why[:60]}")
    r2 = xc("POST", "/execute", {
        "kind": "post", "intent_id": f"cog-p-{datetime.now().strftime('%Y%m%d-%H%M')}",
        "text": text, "rationale": rationale, "growth_tags": growth_tagged,
        "ticker_line": ticker_line or False,
    }, timeout=120)
    if r2.get("status") == "pending_review":
        st["last_post_date"] = today
        log_event("cognitive", account, action="ticket", scene="post-original",
                  ticket=r2.get("ticket_id"))
        return True
    log_event("cognitive", account, action="deny", scene="post-original",
              note=str(r2.get("denied") or r2)[:120])
    return False


def _draft_report(llm, account, led):
    """#13 report：日推运营报告 → state/daily-report-<date>.md（QQ「报」拉取；非写动作不过质量门）"""
    prof = (get_json(f"https://api.fxtwitter.com/2/profile/{account}").get("user") or {})
    snaps = sorted((STATE_DIR / "health-snapshots").glob("*.json"))
    snap_acc = {}
    if snaps:
        try:
            snap_acc = (json.loads(snaps[-1].read_text(encoding="utf-8"))
                        .get("accounts", {}).get(account, {}))
        except Exception:
            pass
    used = led.get("rolling24h_used", {})
    cap = led.get("rolling24h_cap", {})
    stats = {
        "粉丝数": prof.get("followers"), "累计帖数": prof.get("statuses"),
        "关注数": prof.get("following"),
        "24h额度使用": {k: f"{used.get(k, 0)}/{cap.get(k)}" for k in ("post", "reply", "comment", "like")},
        "今日熔断": led.get("aborted_today"), "急停中": led.get("paused"),
        "最近健康判定": snap_acc.get("verdict"),
        "健康快照日": snaps[-1].stem if snaps else None,
        "认知心跳累计": int((led.get("_beats") or 0)),
    }
    token_total = 0
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        with (STATE_DIR / f"log-{datetime.now():%Y-%m}.jsonl").open(encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                if row.get("action") == "chat" and str(row.get("at", "")).startswith(today):
                    token_total += int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
    except Exception:
        pass
    stats["今日LLM_tokens"] = token_total
    out = llm.chat("report", assemble("report", {"account_stats": json.dumps(stats, ensure_ascii=False, indent=1)}),
                   "执行上述场景任务，输出今日运营报告正文。", max_tokens=3500)
    text = (out or "").strip()
    if not text or text.upper() == "SKIP":
        log_event("cognitive", account, action="reportSkip")
        return False
    try:
        from feedback import summary_text
        text += "\n\n【近7天反馈（票据实数）】\n" + summary_text(account, 7)
    except Exception:
        pass
    try:
        from effects import summary
        effect = summary(account, 7)
        if effect:
            text += "\n\n【近7天发布效果（只读采样）】\n" + json.dumps(effect, ensure_ascii=False)[:700]
    except Exception:
        pass
    try:
        # 审计日聚合（阶段二运营研究环）：今日产出按来源/链分组 + 标的判定器审计块
        from audit_digest import text_block as _audit_block
        block = _audit_block()
        if block:
            text += "\n\n" + block
    except Exception:
        pass
    day = datetime.now().strftime("%Y-%m-%d")
    (STATE_DIR / f"daily-report-{day}.md").write_text(text, encoding="utf-8")
    log_event("cognitive", account, action="reportDrafted", file=f"daily-report-{day}.md")
    return True


# ── 心跳主入口（heartbeat.py 调用；异常全部就地消化，绝不影响调度节律） ──
def beat():
    import yaml
    from llm import LLM
    cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
    handle = cfg["account"]["handle"]
    account = handle.lower()
    llm = LLM()
    st = _load_state()
    st["beats"] = int(st.get("beats") or 0) + 1
    today = datetime.now().strftime("%Y-%m-%d")
    # consumed/去重集修剪（review 低20：单调增长 7 天前的清除）
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()[:19]
    st["consumed"] = {k: v for k, v in (st.get("consumed") or {}).items() if v >= cutoff}
    try:
        led = xc("GET", "/ledger")
        pool = Contacts(account)
        pool.reconcile()   # 互动池对账：done intents → 熟络度统计（幂等增量）
        # 过期票据定时清扫（MoA 审查：懒清扫让过期票长期占预留）+ 当日 cog-p 票全终局
        # 且无一 done 时解锁重试（开票即烧 last_post_date 的补偿路径；键格式 %Y%m%d-
        # 与开票一致——review 中5：%Y-%m-%d glob 永不匹配，整段是死代码）
        from tickets import Tickets
        from ledger import Ledger as _L
        Tickets(_L(account)).sweep_expired()
        try:
            ymd = datetime.now().strftime("%Y%m%d")
            today_p = [json.loads(p.read_text(encoding="utf-8"))
                       for p in (STATE_DIR / "tickets").glob(f"cog-p-{ymd}-*")]
            if (today_p and all(d.get("status") in ("expired", "rejected", "failed")
                                for d in today_p)
                    and not any(d.get("status") == "done" for d in today_p)):
                st["last_post_date"] = ""   # 今日尝试全部终局未成 → 允许再试
        except Exception:
            pass
        media_gate = ((cfg.get("cognitive") or {}).get("gates") or {}).get(
            "media_comment", "skip")
        # 1) 日推（20 点后每日一次）
        if datetime.now().hour >= 20 and st.get("last_report") != today:
            try:
                if _draft_report(llm, account, led):
                    st["last_report"] = today
            except Exception as e:
                log_event("cognitive", account, action="reportFail", note=str(e)[:120])
        # 2) 一票 X 写动作：自家评论回复 → 互关串门 → 热门评论 → 原创帖（每日一票）
        acted = False
        try:
            if (led.get("rolling24h_used", {}).get("reply", 99)
                    < led.get("rolling24h_cap", {}).get("reply", 0)):
                acted = _try_inbox(llm, account, handle, st, pool)
                if not acted:
                    acted = _try_reply(llm, account, handle, st, pool)
        except Exception as e:
            log_event("cognitive", account, action="replyFail", note=str(e)[:120])
        if not acted:
            try:
                if (led.get("rolling24h_used", {}).get("comment", 99)
                        < led.get("rolling24h_cap", {}).get("comment", 0)):
                    acted = _try_visit(llm, account, st, pool, media_gate)
            except Exception as e:
                log_event("cognitive", account, action="visitFail", note=str(e)[:120])
        if not acted:
            try:
                if (led.get("rolling24h_used", {}).get("comment", 99)
                        < led.get("rolling24h_cap", {}).get("comment", 0)):
                    acted = _try_hot(llm, account, handle, st, pool, media_gate)
            except Exception as e:
                log_event("cognitive", account, action="hotFail", note=str(e)[:120])
        # 原创帖已迁至 news_auto.py 小时级增量调度（与工位同窗 06:30-24:00、每小时0-5条）；
        # 旧的每日一票路径停用，避免两套调度重复开票。
        if not acted and st.get("last_report") != today:
            log_event("cognitive", account, action="idle", beats=st["beats"])
    finally:
        _save_state(st)
    return st["beats"]


if __name__ == "__main__":
    if "--report" in sys.argv:
        # 独立日推入口（xar-report.timer 20:30 调度——2026-10-27 实证：beat 依赖运气，
        # 19:53 后抽到 135min 长间隔直接跳过 20-22 点区间，日推整晚没触发）
        import yaml
        from llm import LLM
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        account = cfg["account"]["handle"].lower()
        st = _load_state()
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if st.get("last_report") != today and _draft_report(LLM(), account,
                                                                xc("GET", "/ledger")):
                st["last_report"] = today
                _save_state(st)
        except Exception as e:
            log_event("cognitive", account, action="reportFail", note=str(e)[:120])
    else:
        print("beats =", beat())
