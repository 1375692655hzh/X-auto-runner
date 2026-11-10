# patrol.py — 互关关键词巡逻引擎（2026-11-11 运营立项，参考 x-auto-tool 激进档）
# 节拍：60-90 分钟一拍（活跃窗内，state 自管 next_beat_at）；每拍：
#   搜索关键词（live tab）→ 候选过滤（黑名单/已关注/7天冷却/本人）→ 决策模型批路由
#   （低质帖→模板池，高质帖→LLM 现写，运营拍板 2026-11-11）→ 关注+浇灌评论
#   （动作间隔 action_gap_sec，连败 fail_abort 熔断本拍）。
# 量：follow 走 budget_24h 帽（180/日）；comment 帽 80/日；回关由通知链路处理
#   （cognitive._try_inbox 每拍提量 followback_per_beat）。
# 安全：只走既有票道（幂等/工位串行/预算），engage 评论过专用质量门（模板池成员
#   或 LLM casual 档）；绝不绕过任何闸。审计 task=patrol 全程留痕。
import json
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402

ACCOUNT = "wcixot"
ST_PATH = STATE_DIR / "patrol-state.json"

DEFAULTS = {
    "enabled": True,
    "keywords": ["#蓝V互关", "#交朋友", "#浇朋友", "#互关"],
    "engage_per_beat": 10,        # 每拍关注+浇灌对数
    "llm_per_beat": 3,            # 每拍最多 LLM 现写条数（其余走模板）
    "followback_per_beat": 5,     # 通知回关每拍上限（cognitive 读）
    "beat_minutes": [60, 90],
    "action_gap_sec": 20,
    "author_cooldown_days": 7,
    "fail_abort": 3,
    "engage_pool": ["浇一浇", "互关一下", "串门来了", "已关，回关啦", "互关互关"],
}

ROUTE_PROMPT = """你是互关评论路由器。输入一组互关社区帖子（JSON 数组，每项 {i, text}），
逐条判断：这条帖是否值得用 LLM 现写一条针对性评论。
- llm：帖内有实质内容（观点/数据/行业讨论/真实经历），一句具体回应能展示水平
- template：纯仪式性互关帖（"互关吗""来""求关""互关走起"等无实质内容）
只输出 JSON：{"routes":[{"i":0,"r":"llm|template"},...]}，i 与输入对应，一条不缺。"""


def _cfg():
    try:
        import yaml
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8")) or {}
        sec = cfg.get("patrol") or {}
    except Exception:
        sec = {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in sec.items() if v is not None})
    return out


def _load_state():
    try:
        return json.loads(ST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"next_beat_at": "", "engaged": {}, "kw_idx": 0}


def _save_state(st):
    ST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ST_PATH)


def _active_ok(cfg):
    try:
        from news_auto import in_active_window
        return in_active_window()
    except Exception:
        return False


def filter_candidates(posts, pool, st, self_handle, cooldown_days):
    """候选过滤：非本人/非黑名单/未关注过/冷却外/有正文。返回按序候选列表。"""
    now = datetime.now()
    cut = now - timedelta(days=cooldown_days)
    out, seen = [], set()
    for p in posts or []:
        author = str(p.get("author") or "").strip().lstrip("@").lower()
        sid = str(p.get("id") or p.get("status_id") or "").strip()
        text = str(p.get("text") or "").strip()
        if not author or not sid or author in seen:
            continue
        seen.add(author)
        if author == (self_handle or "").lower():
            continue
        if pool.tier(author) == "blacklist":
            continue
        if (pool.get(author) or {}).get("we_follow"):
            continue
        last = st["engaged"].get(author)
        if last:
            try:
                if datetime.fromisoformat(last) > cut:
                    continue
            except Exception:
                pass
        if not text:
            continue
        out.append({"author": author, "sid": sid, "text": text[:200]})
    return out


def parse_routes(out, n, llm_cap):
    """决策路由解析：{"routes":[{"i":0,"r":"llm"},...]} → 长度 n 的 'llm'/'template' 列表。
    解析失败/缺项 fail-open 到 template（便宜、安全）；llm 数量截到 cap。"""
    routes = ["template"] * n
    try:
        import re
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (out or "").strip())
        m = re.search(r"\{.*\}", s, re.S)
        d = json.loads(m.group(0) if m else s)
        used = 0
        for row in d.get("routes") or []:
            try:
                i = int(row.get("i"))
            except (TypeError, ValueError):
                continue
            if not (0 <= i < n):
                continue
            if str(row.get("r") or "").lower() == "llm" and used < llm_cap:
                routes[i] = "llm"
                used += 1
    except Exception:
        pass
    return routes


def beat():
    """巡逻拍入口（xar-patrol.timer 每 5 分钟调；自带节拍门）。"""
    cfg = _cfg()
    if not cfg.get("enabled"):
        return
    try:
        from ledger import check_pause
        check_pause()
    except Exception:
        return
    st = _load_state()
    now = datetime.now()
    if st.get("next_beat_at"):
        try:
            if now < datetime.fromisoformat(st["next_beat_at"]):
                return
        except Exception:
            pass
    if not _active_ok(cfg):
        return
    lo, hi = cfg["beat_minutes"]
    st["next_beat_at"] = (now + timedelta(minutes=random.randint(int(lo), int(hi)))).isoformat()
    _save_state(st)

    from contacts import Contacts
    from llm import LLM
    from cognitive import xc, _draft_scene
    account = ACCOUNT
    pool = Contacts(account)
    kws = cfg["keywords"]
    kw = kws[st.get("kw_idx", 0) % len(kws)]
    st["kw_idx"] = (st.get("kw_idx", 0) + 1) % len(kws)

    r = xc("POST", "/read/search", {"q": kw, "tab": "live", "limit": 30}, timeout=150)
    posts = r.get("posts") or []
    cands = filter_candidates(posts, pool, st, account, cfg["author_cooldown_days"])[: cfg["engage_per_beat"]]
    log_event("patrol", account, action="searched", note=f"kw={kw} posts={len(posts)} cands={len(cands)}")
    if not cands:
        _save_state(st)
        return

    llm = LLM()
    routes = parse_routes(
        llm.chat("decision", ROUTE_PROMPT,
                 json.dumps([{"i": i, "text": c["text"]} for i, c in enumerate(cands)],
                            ensure_ascii=False),
                 max_tokens=800, timeout=30, retries=1),
        len(cands), cfg["llm_per_beat"])

    fails = 0
    for c, route in zip(cands, routes):
        if fails >= cfg["fail_abort"]:
            log_event("patrol", account, action="failAbort", note=f"连续失败×{fails}")
            break
        ok = _engage_one(xc, llm, _draft_scene, account, pool, c, route, cfg)
        if ok is None:                      # 帽尽/工位断：本拍收兵
            break
        if ok:
            st["engaged"][c["author"]] = datetime.now().isoformat()
            log_event("patrol", account, action="engaged",
                      note=f"{c['author']}:{route}")
        else:
            fails += 1
            st["engaged"][c["author"]] = datetime.now().isoformat()  # 失败也冷却，防反复撞
        _save_state(st)
        time.sleep(min(int(cfg["action_gap_sec"]), 20))
    _save_state(st)


def _engage_one(xc, llm, draft_scene, account, pool, cand, route, cfg):
    """一对动作：关注 + 浇灌评论。True=成 / False=单点失败 / None=本拍收兵（帽尽/工位）。"""
    author, sid = cand["author"], cand["sid"]
    # ① 关注（幂等键由 sid 派生；IDEMPOTENCY_CONFLICT=已互动过，按成功处理）
    r = xc("POST", "/execute", {
        "kind": "follow", "intent_id": f"patrol-f-{sid[-12:]}",
        "target_handle": author, "origin": "chain:patrol",
        "rationale": f"互关巡逻（tag候选）@{author}"}, timeout=120)
    code = (r.get("denied") or {}).get("code")
    if r.get("status") in ("done", "approved", "pending_review", "unknown") \
            or code == "IDEMPOTENCY_CONFLICT":
        pass
    elif code == "CAP_EXHAUSTED":
        log_event("patrol", account, action="capHold", note=f"follow帽尽 @{author}")
        return None
    elif code in ("WORKSTATION_DOWN", "QUEUE_TIMEOUT"):
        log_event("patrol", account, action="wsDown", note=str(code))
        return None
    else:
        log_event("patrol", account, action="followDeny", note=f"@{author} {str(r)[:120]}")
        return False
    pool.set_we_follow(author, True)

    # ② 浇灌评论：低质帖走模板池，高质帖 LLM 现写（决策路由，2026-11-11 运营拍板）
    if route == "llm":
        text, skipped = draft_scene(llm, "comment-engage",
                                    {"target_post": cand["text"],
                                     "target_author": author,
                                     "recent_history": ""},
                                    "comment", 400)
        if skipped or not text:
            text = random.choice(cfg["engage_pool"])
            route = "template"
    else:
        text = random.choice(cfg["engage_pool"])
    r2 = xc("POST", "/execute", {
        "kind": "comment", "intent_id": f"patrol-c-{sid[-12:]}",
        "text": text, "status_id": sid, "target_handle": author,
        "target_len": len(cand["text"]), "casual": True, "engage": route,
        "origin": "chain:patrol",
        "rationale": f"互关巡逻浇灌（{route}）@{author}"}, timeout=120)
    code2 = (r2.get("denied") or {}).get("code")
    if r2.get("status") in ("done", "approved", "pending_review", "unknown") \
            or code2 == "IDEMPOTENCY_CONFLICT":
        try:
            pool.record_our(author, "comment")
            pool.salutation_used(text)
        except Exception:
            pass
        return True
    if code2 == "CAP_EXHAUSTED":
        log_event("patrol", account, action="capHold", note=f"comment帽尽 @{author}")
        return True     # 关注已成，评论帽尽不算失败
    log_event("patrol", account, action="commentDeny", note=f"@{author} {str(r2)[:120]}")
    return True         # 关注已成即算半成；评论被质量门拒是设计内行为


if __name__ == "__main__":
    beat()
