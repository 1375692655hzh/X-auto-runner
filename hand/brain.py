# brain.py — B 路：对话式主脑 v2（agent 内核，平台中立）
# 入口: chat(user_msg, account, llm=None, user_id="console") → str
# 架构: 对话记忆（每用户滚动窗口）+ 多轮 agent 循环（LLM⇄工具，喂真实结果）
#       + 运营状态自动注入（省一轮工具调用）+ 全量工具面（只读/提案）
# 安全不变: 写动作只提案（开票），执行权在人审；急停/调档例外（低风险元数据）
# 协议: JSON in content——{"reply": "..."} 收工 | {"action": "...", "args": {...}} 调工具
#   （模型无关：不依赖原生 function calling，换任何 OpenAI 兼容端点都能跑）
import json
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event  # noqa: E402

MEM_DIR = STATE_DIR / "brain-memory"
MEM_WINDOW = 16          # 记忆窗口：user/assistant 消息各半，约 8 轮对话
MAX_TURNS = 7            # agent 循环上限（1 理解 + 最多 6 次工具）
TIME_BUDGET_S = 200      # 整轮预算（QQ 被动回复窗 ~5min，留余量）

# ── 工具定义（LLM 可调用的能力面）──
TOOLS = [
    {"name": "get_status", "desc": "查工位/心跳/额度/熔断/急停状态",
     "args": {}},
    {"name": "get_tickets", "desc": "查待审票据列表（发帖/关注等提案）",
     "args": {}},
    {"name": "get_ticket_detail", "desc": "读取单张票据的权威详情：当前正文、原始标题、来源、公司/市场等实体信息。"
     "用户提到票号时必须先用它；用户说“这个/他/刚才那条/新股”时可省略票号，工具取最近主动通知的票",
     "args": {"ticket": "票号；可省略，省略时取最近通知或唯一待审"}},
    {"name": "get_pool", "desc": "查互动账号池（互关/大V/正常/黑名单四档+熟悉度）",
     "args": {}},
    {"name": "get_report", "desc": "查今日运营报告（如果已生成）",
     "args": {}},
    {"name": "get_news", "desc": "查数据站最新财经资讯标题列表（运营者的信息源）",
     "args": {"limit": "条数(默认5)"}},
    {"name": "list_sources", "desc": "查数据站信息源目录（全量：名称/id/类型/市场/启用态，附类型分组统计）",
     "args": {"kind": "可选，按类型筛选", "enabled_only": "可选，只看已启用的源"}},
    {"name": "ds_api", "desc": "数据站通用查询（GET，自服务通道）。端点速查："
     "/v1/items 条目检索（参数:q=关键词全文搜索,markets,kinds,forms,sources,tickers,"
     "sentiments,event_types,sectors,since=起始时间,limit,cursor）；"
     "/v1/sources 源目录；/v1/sources/{sid} 单源详情；/v1/health 库存统计；"
     "/v1/status 服务状态；/v1/snapshot/latest 最新快照；/v1/alloc 与 /v1/alloc/{account} 配额。"
     "专项工具没覆盖的组合查询/详情/搜索都走这里",
     "args": {"path": "如 /v1/items 或 /v1/sources/cls_telegraph", "params": {"参数名": "值"}}},
    {"name": "get_material", "desc": "取数据站精品素材全文（发帖起草用的一手材料）",
     "args": {}},
    {"name": "radar", "desc": "查限流雷达最新快照（搜索建议/搜索/幽灵/降权四维度）",
     "args": {}},
    {"name": "read_timeline", "desc": "读 X 首页时间线近况（账号最近刷到的帖子）",
     "args": {"limit": "条数(默认8)"}},
    {"name": "recent_actions", "desc": "查今天系统实际做了什么（动作流水：发帖/回复/串门/心跳）",
     "args": {}},
    {"name": "revise_ticket", "desc": "把运营者的修改原话交给作者流水线修改待审资讯票，重新过作者与底线编辑，只改票不发布；必须先 get_ticket_detail 并传回读到的 revision_count",
     "args": {"ticket": "待审票号", "instruction": "运营者具体修改要求", "base_revision": "get_ticket_detail返回的revision_count"}},
    {"name": "set_tier", "desc": "设置互动池档位",
     "args": {"handle": "对方 handle", "tier": "mutual|bigv|normal|blacklist", "reason": "原因(可选)"}},
    {"name": "pause", "desc": "急停（低风险元数据，立即生效；恢复须 SSH 删 state/PAUSE）",
     "args": {}},
]


def _token():
    return Path("/etc/xar/hand.token").read_text(encoding="utf-8").strip()


def _xc(method, path, body=None, timeout=60):
    req = urllib.request.Request(f"http://127.0.0.1:8741{path}", method=method,
                                 headers={"Authorization": f"Bearer {_token()}",
                                          "Content-Type": "application/json"})
    if body is not None:
        req.data = json.dumps(body, ensure_ascii=False).encode()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)[:120]}


def exec_tool(name: str, args: dict, account: str, user_id: str = None) -> dict:
    """执行工具调用 → 结构化结果（喂回 LLM 继续推理）"""
    try:
        if name == "get_status":
            h = _xc("GET", "/health")
            l = _xc("GET", "/ledger")
            used, cap = l.get("rolling24h_used", {}), l.get("rolling24h_cap", {})
            return {"workstation": h.get("workstation"),
                    "heartbeat_age_s": h.get("heartbeat_age_s"),
                    "budget": {k: f"{used.get(k,0)}/{cap.get(k)}" for k in ("post","reply","comment","like")},
                    "aborted": l.get("aborted_today"), "paused": l.get("paused")}
        if name == "get_tickets":
            from tickets import Tickets
            from ledger import Ledger
            rows = Tickets(Ledger(account)).list_open()
            return {"pending": [{"id": r["id"], "kind": r["kind"],
                                 "title": ((r["payload"].get("news_detail") or {}).get("title") or ""),
                                 "preview": (r["payload"].get("text") or "")[:80],
                                 "rationale": (r.get("rationale") or "")[:60]}
                                for r in rows]}
        if name == "get_ticket_detail":
            from tickets import Tickets
            from ledger import Ledger
            tk = Tickets(Ledger(account))
            tid = str(args.get("ticket") or "").strip()
            if not tid and user_id:
                # 当前用户最近成功读取的票优先于全局通知，避免多话题交错串票
                tid = _active_ticket(user_id)
            if not tid:
                # 没有当前读取票时，才使用最近主动通知
                try:
                    sent = json.loads((STATE_DIR / "qq-notified.json").read_text(encoding="utf-8"))
                    sent_ids = [k for k, v in sent.items() if v == "sent"]
                    tid = sent_ids[-1] if sent_ids else ""
                except Exception:
                    tid = ""
            if not tid:
                rows = tk.list_open()
                if len(rows) == 1:
                    tid = rows[0]["id"]
                elif len(rows) > 1:
                    return {"error": "当前有多张待审票，未指定且没有最近通知记录",
                            "candidates": [{"id": r["id"],
                                            "title": (r["payload"].get("news_detail") or {}).get("title")}
                                           for r in rows[-5:]]}
            d = tk.get(tid) if tid else None
            if not d:
                return {"error": f"票据不存在或未指定: {tid or '（空）'}"}
            p = d.get("payload") or {}
            nd = p.get("news_detail") or {}
            _set_active_ticket(user_id, d.get("id"))
            return {"id": d.get("id"), "kind": d.get("kind"), "status": d.get("status"),
                    "title": nd.get("title"), "source": nd.get("source"),
                    "source_time": nd.get("time"), "url": nd.get("url"),
                    "markets": nd.get("markets") or [], "story_id": nd.get("story_id"),
                    "draft_text": p.get("text") or "",
                    "source_text": (nd.get("source_text") or "")[:4000],
                    "revision_count": d.get("revision_count") or 0}
        if name == "get_pool":
            from contacts import Contacts
            return Contacts(account).summary()
        if name == "get_report":
            rp = sorted(STATE_DIR.glob("daily-report-*.md"))
            today = datetime.now().strftime("%Y-%m-%d")
            if rp and rp[-1].stem == f"daily-report-{today}":
                return {"today_report": rp[-1].read_text(encoding="utf-8")[:800]}
            return {"today_report": None, "note": "今日报告尚未生成（20:30 自动）"}
        if name == "get_news":
            from datastation import latest_news_summary
            return {"news": latest_news_summary(int(args.get("limit", 5)))}
        if name == "list_sources":
            from datastation import list_sources as _ls
            r = _ls(kind=args.get("kind"),
                    enabled_only=bool(args.get("enabled_only")))
            if r.get("error"):
                return r
            src = r["sources"]
            kinds = {}
            for s in src:
                kinds[s["kind"] or "?"] = kinds.get(s["kind"] or "?", 0) + 1
            # 全量紧凑返回（~128 行 ×40 字符 ≈ 5KB，k3.1-pro 上下文无压力）：
            # 模型拿全数据自己分组作答，省掉逐 kind 下钻的轮次
            return {"total": r["total"], "matched": len(src), "by_kind": kinds,
                    "sources": [f"{s['title']}（{s['id']}，{s['kind']}，{s['markets']}）"
                                for s in src]}
        if name == "ds_api":
            from datastation import api_get
            params = args.get("params")
            r = api_get(str(args.get("path") or ""),
                        params if isinstance(params, dict) else {})
            return {"path": args.get("path"), "result": _slim(r)}
        if name == "get_material":
            from datastation import fetch_material, format_material
            items = fetch_material()
            if not items:
                return {"material": None, "note": "数据站近 6 小时无精品素材"}
            return {"count": len(items), "material": format_material(items)[:1500]}
        if name == "radar":
            snap_f = STATE_DIR / "health-snapshots" / f"{datetime.now():%Y-%m-%d}.json"
            try:
                snap = json.loads(snap_f.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"radar": None, "note": "今日尚无雷达快照"}
            rd = snap.get("radar") or {}
            if rd.get("channelError"):
                return {"radar": None, "note": f"雷达通道异常: {rd['channelError']}"}
            out = {h: (r.get("four") or {}) for h, r in (rd.get("results") or {}).items()}
            return {"fetchedAt": rd.get("fetchedAt"), "four_dimensions": out}
        if name == "read_timeline":
            r = _xc("POST", "/read/timeline", {"surface": "home",
                                               "limit": int(args.get("limit", 8))}, timeout=90)
            if r.get("_err") or r.get("error"):
                return {"error": str(r.get("_err") or r.get("error"))[:120]}
            posts = r.get("posts") or []
            return {"count": len(posts),
                    "posts": [f"@{p.get('author')}：{(p.get('text') or '')[:120]}" for p in posts]}
        if name == "recent_actions":
            today = datetime.now().strftime("%Y-%m-%d")
            log_f = STATE_DIR / f"log-{datetime.now():%Y-%m}.jsonl"
            lines = []
            try:
                raw = log_f.read_text(encoding="utf-8").strip().splitlines()
            except FileNotFoundError:
                raw = []
            for ln in raw:
                try:
                    j = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if (j.get("at") or "").startswith(today):
                    note = j.get("note") or j.get("tool") or j.get("kind") or ""
                    lines.append(f"{(j.get('at') or '')[11:16]} [{j.get('task')}] "
                                 f"{j.get('action') or ''} {str(note)[:60]}".rstrip())
            return {"today_count": len(lines), "actions": lines[-15:]}
        if name == "revise_ticket":
            from review_ops import revise_pending_ticket
            r = revise_pending_ticket(str(args.get("ticket") or ""),
                                      str(args.get("instruction") or ""), account,
                                      by="brain:operator",
                                      base_revision=args.get("base_revision"))
            if r.get("ticket") and not r.get("error"):
                _set_active_ticket(user_id, r["ticket"])
            return r
        if name == "set_tier":
            from contacts import Contacts
            pool = Contacts(account)
            pool.set_tier(args.get("handle", ""), args.get("tier", "normal"),
                          args.get("reason", ""))
            return {"ok": True, "handle": args.get("handle"),
                    "tier": args.get("tier")}
        if name == "pause":
            (STATE_DIR / "PAUSE").write_text("brain:对话主脑急停", encoding="utf-8")
            return {"ok": True, "note": "已急停（PAUSE），SSH 删 state/PAUSE 恢复"}
        return {"error": f"未知工具 {name}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"[:120]}


# ── 对话记忆（每用户滚动窗口，只存 user/assistant 终稿，不存工具中间态）──
def _mem_file(user_id: str) -> Path:
    safe = re.sub(r"[^\w-]", "_", user_id)[:64] or "anon"
    return MEM_DIR / f"{safe}.json"


def _load_mem(user_id: str) -> list:
    try:
        d = json.loads(_mem_file(user_id).read_text(encoding="utf-8"))
        return d.get("messages", [])[-MEM_WINDOW:]
    except Exception:
        return []


def _save_mem(user_id: str, messages: list) -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _mem_file(user_id).with_suffix(".tmp")
    tmp.write_text(json.dumps({"updated": datetime.now().isoformat(),
                               "messages": messages[-MEM_WINDOW:]},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(_mem_file(user_id))


ACTIVE_DIR = STATE_DIR / "brain-active"


def _active_file(user_id):
    return ACTIVE_DIR / (re.sub(r"[^\w-]", "_", user_id)[:64] + ".json")


def _active_ticket(user_id):
    try:
        return json.loads(_active_file(user_id).read_text(encoding="utf-8")).get("ticket") or ""
    except Exception:
        return ""


def _set_active_ticket(user_id, ticket):
    if not user_id or not ticket:
        return
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _active_file(user_id).with_suffix(".tmp")
    tmp.write_text(json.dumps({"ticket": ticket, "at": datetime.now().isoformat()},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(_active_file(user_id))


# ── 运营状态注入（本地快查，免一轮工具）──
def _context_block() -> str:
    try:
        h = _xc("GET", "/health", timeout=8)
        l = _xc("GET", "/ledger", timeout=8)
        used, cap = l.get("rolling24h_used", {}), l.get("rolling24h_cap", {})
        wd = "一二三四五六日"[datetime.now().weekday()]
        lines = [f"时间: {datetime.now():%Y-%m-%d %H:%M} 周{wd}",
                 f"工位: {h.get('workstation')}, 心跳 {h.get('heartbeat_age_s')}s 前"]
        if l.get("paused"):
            lines.append("⚠ 系统处于 PAUSE 急停状态")
        lines.append("24h额度 post {}/{} reply {}/{} comment {}/{} like {}/{}".format(
            used.get("post", 0), cap.get("post"), used.get("reply", 0), cap.get("reply"),
            used.get("comment", 0), cap.get("comment"), used.get("like", 0), cap.get("like")))
        return "\n".join(lines)
    except Exception:
        return "（状态获取失败，可调 get_status 工具重查）"


# ── 主脑系统提示 ──
SYSTEM = """你是 X 账号自动运营系统的主脑（agent），运营者通过即时通讯和你对话。你不是客服脚本，是运营搭档——像一个懂技术的运营合伙人独自照看这个账号，运营者是唯一能下写指令的人。

【当前运营状态】（系统实时注入，可信，不必再查）
__CONTEXT__

【可用工具】（JSON 数组）
__TOOLS__

【工作方式】
- 先理解问题再行动：直接回答 / 调工具拿数据 / 开票提案，自己判断
- 复杂问题可以连续调用多个工具再下结论（例：先查额度→再查票据→再给建议）
- 用数据说话：数字要具体；工具查不到就明说查不到，绝不编造
- 主动给判断和建议（例："reply 额度还剩 6 条，建议优先回互关档"），不等运营者逐项问
- 罗列/清单类：40 条以内直接全列；更多就按类型分组给统计+每类代表源，
  并说明"要看哪类完整名单，说类型名"——不硬塞全量长列表
- 写动作（发帖/关注/评论）你没有执行权；主脑也不是公开内容作者，不得自行起草正文、决定作者角度或修改创作提示词。人工发帖走固定指令；资讯改稿只能转交 revise_ticket 并复用作者与编辑链路。
- 运营者聊运营之外的话题也可以正常交流，但别丢掉你的运营者视角

【审稿/票据对话硬规则】
- 消息里出现票号：回答内容、识别公司或修改前，必须先调 get_ticket_detail(票号)。
- 出现“这个/他/刚才那条/新股/这家公司”等指代：调 get_ticket_detail（票号可省略）解析最近通知的票；工具返回多候选时才简短确认，不得沿用上一话题猜。
- 公司名、股票代码、上市市场、当前正文和状态只能以票据详情为准；对话记忆只用于理解上下文，不是实体证据。旧回答与票据冲突时直接纠正。
- 修改票据前也必须先读详情；revise_ticket 未明确成功不得说“已改”，未执行发布不得说“已发/已读”。
- “直接发吧/发布吧”有多个候选时先确认票号；主脑无批准与发布工具，只能引导固定「发 <票号>」指令，不得生成状态ID或成功回执。
- 不确定就查，查不到就明说；绝不把两家公司、两个上市市场或两张票混为一谈。

【回复风格】
- 像同事发消息：直接、有信息量；可用短列表；默认 1-4 句，要分析就给足分析
- 不复述问题、不说"作为AI"、不道歉铺垫

【输出协议】每次只输出一个 JSON 对象，无其他文字、无 markdown 代码块：
- 最终回复运营者：{"reply": "回复文本"}
- 调用工具：{"action": "工具名", "args": {...}}
工具结果会作为用户消息回喂；你可在给出最终回复前多次调用工具。"""


def _parse(out: str):
    """LLM 输出 → dict；解析失败返回 None（当纯文本回复处理）"""
    s = out.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*}", s, re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _slim(o, depth: int = 0):
    """通用查询结果结构化瘦身：长字符串截 300、列表截 40、深层折叠——
    保 JSON 合法（不像字符串切片会切出残缺 JSON）"""
    if isinstance(o, str):
        return o[:300] + ("…" if len(o) > 300 else "")
    if isinstance(o, list):
        return [_slim(x, depth + 1) for x in o[:40]]
    if isinstance(o, dict):
        if depth >= 4:
            return "…"
        return {k: _slim(v, depth + 1) for k, v in o.items()}
    return o


def chat(user_msg: str, account: str, llm=None, user_id: str = "console") -> str:
    """对话主脑入口（平台中立）：消息 → [记忆+状态注入] → 多轮 LLM⇄工具 → 回复"""
    from llm import LLM
    if llm is None:
        llm = LLM()
    t0 = time.time()
    mem = _load_mem(user_id)
    sys_prompt = (SYSTEM
                  .replace("__CONTEXT__", _context_block())
                  .replace("__TOOLS__", json.dumps(TOOLS, ensure_ascii=False)))
    messages = ([{"role": "system", "content": sys_prompt}] + mem
                + [{"role": "user", "content": user_msg}])

    reply, tool_calls = None, []
    successful_tools = set()
    for turn in range(MAX_TURNS):
        try:
            out = llm.chat_messages("reply", messages, max_tokens=3000,
                                    timeout=60, retries=2)
        except Exception as e:
            log_event("brain", account, action="llmFail", user=user_id,
                      note=str(e)[:80])
            return "（上游模型通道过载/超时，这轮没接上。稍等重发一次即可，或回「帮助」用固定指令）"
        d = _parse(out)
        if d is None or ("reply" not in d and "action" not in d):
            # 截断抢救：输出在 reply 值中间被 max_tokens 切断（JSON 不完整）——
            # 把残缺字符串抠出来补个引号还原，别把原始 JSON 漏给运营者
            m2 = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)', out, re.S)
            if m2:
                try:
                    reply = json.loads('"' + m2.group(1) + '"')[:3000]
                    break
                except json.JSONDecodeError:
                    pass
            reply = out.strip()[:3000]         # 模型没走协议，当纯文本回复兜底
            break
        if "reply" in d:
            r = str(d["reply"])
            # 模型偶发双重编码（reply 值又是一层 {"reply": ...}）——剥一层
            u = _parse(r)
            if isinstance(u, dict) and "reply" in u and str(u.get("reply")).strip():
                r = str(u["reply"])
            reply = r[:3000]
            break
        # action 分支：预算检查 → 执行 → 真实结果回喂
        if time.time() - t0 > TIME_BUDGET_S:
            reply = "（工具调用预算用尽，这一轮先到这。可拆成小问题再问）"
            break
        name, targs = str(d.get("action")), d.get("args") or {}
        result = exec_tool(name, targs, account, user_id)
        tool_calls.append(name)
        if isinstance(result, dict) and not result.get("error"):
            successful_tools.add(name)
        log_event("brain", account, action="tool", user=user_id, tool=name,
                  result=str(result)[:120])
        messages.append({"role": "assistant", "content": out[-600:]})
        messages.append({"role": "user", "content":
                         f"工具 {name} 执行结果（JSON）：\n"
                         f"{json.dumps(result, ensure_ascii=False)[:8000]}\n\n"
                         "数据够就给运营者最终回复（{\"reply\": ...}）；"
                         "还需要更多信息就继续调工具。"})
    else:
        reply = "（这轮工具调用太多次了。换个更具体的问题，或直接用固定指令）"

    # 动作回执闸：主脑没有发布工具，不能凭文本生成“已发布”；改稿必须有真实成功结果
    if re.search(r"已(?:直接|经)?发布|已直接发[，。：]|已发[，。：]", reply or ""):
        log_event("brain", account, action="claimBlocked", user=user_id, note="发布回执无执行证据")
        tid = _active_ticket(user_id)
        reply = (f"我还没有执行发布。确认具体票据后，请回复「发 {tid}」。" if tid
                 else "我还没有执行发布。请回复「发 <票号>」确认具体票据。")
    elif re.search(r"已(?:按.*)?改(?:好|了|完|成)?[，。！]|已修改", reply or "") and "revise_ticket" not in successful_tools:
        log_event("brain", account, action="claimBlocked", user=user_id, note="改稿回执无成功工具结果")
        reply = "改稿没有成功完成，原票未被修改。你可以让我重新读取后再改。"

    _save_mem(user_id, mem + [{"role": "user", "content": user_msg[:400]},
                              {"role": "assistant", "content": reply[:1500]}])
    log_event("brain", account, action="chat", user=user_id,
              turns=turn + 1, tools=",".join(tool_calls) or "-",
              ms=int((time.time() - t0) * 1000), chars=len(reply))
    return reply


if __name__ == "__main__":
    # CLI 直测（不依赖 QQ）：python brain.py "状态怎么样" [account]
    msg = " ".join(sys.argv[1:2]) or "状态怎么样"
    acct = sys.argv[2] if len(sys.argv) > 2 else "wcixot"
    print(chat(msg, acct))
