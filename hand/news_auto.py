# news_auto.py — 资讯源自动发布：小时级信息增量调度器（活跃窗与工位统一，默认 06:30-24:00）
# 固定信息库：数据站「资讯 + 精品文章」；主脑评分 → 每小时择优 0-5 条 → 单源成稿。
# dry-run 只读数据站与 LLM 评分，不创建票据；正常 tick 的一切 X 写动作仍走 /execute 人审。
# 时间真相单一化：资讯窗默认跟随 runtime.active_window（与浏览器工位同窗），
# 避免“05:00 生产、06:30 才能发”的积压补发；news_auto.active_window 可显式覆盖。
import json
import hashlib
import os
import random
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import STATE_DIR, log_event, check_pause, PauseActive, file_lock  # noqa: E402

STATE_FILE = STATE_DIR / "news-auto.json"
AUDIT_FILE = STATE_DIR / "news-decisions.jsonl"
POLICY_BASE = "news-auto-20261109-v14"   # v14: auto 结果闭环、素材新鲜度与异常熔断
MAX_PER_HOUR = 5
CIRCUIT_FILE = STATE_DIR / "news-auto-circuit.json"
SCORE_MIN = 70
TIER_ORDER = {"S": 0, "A": 1, "B": 2, "C": 3, "N": 4}

SCORE_PROMPT = """你是美股交易观察者账号的资讯主编。对候选素材逐条评分，判断它是否值得在当下发布成一条 X 原创帖。不要写帖。

【评分（总分100）】
- 信息增量 0-35：相比近期素材/已发内容，是否新增事实、数字、官方确认或重要进展。
- 人设匹配 0-25：是否属于下方关注范围。
- 事件重要性 0-20：实际影响范围与持续性，不看标题夸张程度。
- 素材完整度 0-10：事实是否足够把事情讲清楚。
- 分析提炼空间 0-10：能否从素材自然得出一层可靠分析。

【题材优先级】
S：美联储/重大宏观（利率决议、CPI/PCE/非农、流动性与美债重大变化）；重大政策变动（出口管制、关税、AI/芯片监管、反垄断、重要财政产业政策）；足以改变市场预期的特大企业事件。
A：美股科技与AI产业链（财报/指引/资本开支/自由现金流/算力供需/云/半导体/模型商业化）；中国科技公司腾讯、阿里、字节的财报、云与AI投入、商业化、重大战略组织变化；中国模型公司智谱、Kimi（月之暗面）、DeepSeek 的模型能力、开源、API/价格、商业化、融资、算力约束或重要合作；有明确数字和催化的异常市场波动。
B：其他有明确产业或市场增量的科技事件；传统巨头向AI/机器人延伸；有真实传导路径的其他宏观事件。
C：加密仅重大监管/ETF/立法、稳定币脱锚、大型平台事故或黑客、系统性清算可评高分；地缘社会新闻仅中性判断市场传导。

【主体门槛：名企/头部优先，且是硬约束】
默认只选知名或行业头部企业、主要模型公司、关键平台与供应链主体、央行/监管/主流机构。普通中小企业、冷门公司、小型医疗/生物科技公司、普通新股IPO，不因“营收利润高增、上市大涨、订单数字大”而入选。
非知名企业只有在素材能明确证明其对 AI/科技产业有全行业级影响，或其本身属于关键供应链/平台瓶颈时才可例外；仅“公司沾科技/客户有名/被机构点评”不算例外。主体是否名企无法从素材判断时，不得自行编造行业地位，宁可淘汰。

【直接淘汰（N，0-49分）】
港股已被移出人设，不作为选题方向；日常涨跌播报、普通分析师评级、小版本更新、无实质影响的产品更新、纯观点/营销/广告、未证实传言、普通币价波动、无新增量的重复报道。
医药/生物科技等赛道外公司，即使业绩超预期、上调指引或股价大涨也不选，除非有明确且实质的 AI/科技产业传导；不能仅凭“财报好+涨得多”进入人设范围。
“即将/预告/本周关注/前瞻”本身不是增量：仅复述已知日期和事件日历的淘汰；只有新增具体一致预期数字、官方安排或独特可验证证据时才保留，且不得评S。选战进展、民调、地方博弈和政治背景评论淘汰，除非已有正式政策/行政动作及其明确市场传导。
字节未上市，不得虚构股价或财务数据。中国模型公司的小版本和宣传稿不选。只有“与美股科技沾边”但无信息增量，也不得入选。

【同一事件】
近期已评/已发事件的后续，只有官方确认、关键数字更新、指引改变、影响扩大等实质增量才保留；换措辞、重复转载、单纯市场评论均淘汰。
若候选与【近期已评/已发事件】中的某条属于同一事件，event 字段必须原样复用该条的事件名，便于系统归入同一 Story；只有文字近似但事件不同，才使用新事件名。

只输出 JSON：{"results":[{"key":"候选key","head":"候选标题头，逐字复制","score":0-100,"tier":"S/A/B/C/N","event":"公司或主题+核心事件（12字内）","increment":"新增量（没有就写无）","reason":"一句话理由","publish":true或false}]}
results 必须覆盖每个候选；key 与 head 必须逐字对应同一候选，禁止把甲公司的数字/事件配给乙公司。score>=70 且确实值得发布时 publish=true，否则 false。"""


def _load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _hash_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def _news_cfg():
    try:
        import yaml
        return (yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
                or {}).get("news_auto") or {}
    except Exception:
        return {}


def _max_age_hours(form):
    """auto 发布前的素材年龄上限；快讯慢文分开，非法值 fail-closed 为更严默认值。"""
    configured = _news_cfg().get("max_age_hours") or {}
    default = 6 if form == "资讯" else 24
    try:
        value = float(configured.get(form, default))
        return value if 0 < value <= 168 else default
    except (TypeError, ValueError):
        return default


def policy_version():
    payload = json.dumps({"base": POLICY_BASE, "window": "-".join(_active_window()[2]),
                          "forms": ["资讯", "精品文章"], "max_per_hour": MAX_PER_HOUR,
                          "score_min": SCORE_MIN, "score_prompt": SCORE_PROMPT,
                          "max_age_hours": {f: _max_age_hours(f) for f in ("资讯", "精品文章")}},
                         ensure_ascii=False, sort_keys=True)
    return f"{POLICY_BASE}-{_hash_text(payload)}"


def prompt_version():
    parts = []
    for p in (Path("/etc/xar/prompts/author-system.md"),
              Path("/etc/xar/prompts/author-task.md"),
              Path(__file__).with_name("editorial.py")):
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except Exception:
            parts.append("")
    return "news-prompt-" + _hash_text("\n---\n".join(parts))


def _configured_mode():
    """auto 只能由 /etc/xar/instance.yaml 的 news_auto.mode 显式开启；缺省永远 review。"""
    return "auto" if str(_news_cfg().get("mode") or "review").lower() == "auto" else "review"


def _auto_circuit():
    try:
        return json.loads(CIRCUIT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_circuit(doc):
    tmp = CIRCUIT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CIRCUIT_FILE)


def _record_auto_result(ticket, status):
    """auto 异常只切断新的自动授权，不篡改配置；review 和人工批准仍可继续。"""
    doc = _auto_circuit()
    if status == "unknown":
        doc.update({"blocked": True, "reason": f"{ticket}:unknown",
                    "updated_at": datetime.now().isoformat()[:19], "consecutive_failed": 0})
    elif status == "failed":
        n = int(doc.get("consecutive_failed") or 0) + 1
        doc.update({"consecutive_failed": n, "updated_at": datetime.now().isoformat()[:19]})
        if n >= 2:
            doc.update({"blocked": True, "reason": f"连续{n}次执行失败"})
    elif status == "done":
        doc["consecutive_failed"] = 0
    else:
        return
    _save_circuit(doc)
    log_event("news_auto", "wcixot", action="autoCircuit", ticket=ticket,
              status=status, blocked=bool(doc.get("blocked")), reason=doc.get("reason", ""))


DEFAULT_WINDOW = ("06:30", "24:00")


def _parse_hhmm(s):
    try:
        h, m = str(s).strip().split(":")
        val = int(h) * 60 + int(m)
        return val if 0 <= val <= 24 * 60 else None
    except Exception:
        return None


def _active_window():
    """单一时间真相：资讯窗优先取 news_auto.active_window，否则跟随
    runtime.active_window（与浏览器工位同窗），缺失/非法回退默认 06:30-24:00。
    返回 (start_min, end_min, 原样字符串对)。"""
    pair = None
    try:
        import yaml
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8")) or {}
        v = (cfg.get("news_auto") or {}).get("active_window") \
            or (cfg.get("runtime") or {}).get("active_window")
        if isinstance(v, (list, tuple)) and len(v) == 2:
            pair = (str(v[0]), str(v[1]))
    except Exception:
        pair = None
    start = _parse_hhmm(pair[0]) if pair else None
    end = _parse_hhmm(pair[1]) if pair else None
    if start is None or end is None or start >= end:
        pair = DEFAULT_WINDOW
        start, end = 6 * 60 + 30, 24 * 60
    return start, end, list(pair)


def make_mandate(r, story_id, analysis_pack=None):
    """轻量授权边界：review/auto由实例配置决定；auto仍生成并自动批准可审计票据。"""
    mode = _configured_mode()
    out = {"mode": mode, "scope": "news-auto-post",
           "source_forms": ["资讯", "精品文章"], "active_window": _active_window()[2],
           "max_per_hour": MAX_PER_HOUR, "score_min": SCORE_MIN,
           "score": r["score"], "story_id": story_id,
           "created_at": datetime.now().isoformat()[:19],
           "policy_version": policy_version(), "prompt_version": prompt_version(),
           "auto_execute": mode == "auto"}
    if analysis_pack:
        out["contract_hash"] = _hash_text(json.dumps(analysis_pack.get("contract") or {},
                                                      ensure_ascii=False, sort_keys=True))
    return out


def _parse_source_time(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _story_updated_after_ticket(m):
    """同 Story 在成稿后出现新进展时，不自动发旧稿，转人工重新判断。"""
    created = _parse_source_time(m.get("created_at"))
    if not created:
        return True
    for story in _load_state().get("stories") or []:
        if story.get("story_id") != m.get("story_id"):
            continue
        changed = _parse_source_time(story.get("last_at"))
        return bool(changed and changed > created + timedelta(seconds=2))
    return False


def auto_authorized(intent, now=None):
    """服务端最终校验：auto配置、版本、完整素材、编辑accept、活跃窗、素材新鲜度、
    Story未更新且熔断未触发。任一缺失/异常都回review，不猜、不放行。"""
    if _configured_mode() != "auto" or intent.get("kind") != "post":
        return False
    if not in_active_window(now) or _auto_circuit().get("blocked"):
        return False
    m = intent.get("mandate") or {}
    if not (m.get("mode") == "auto" and m.get("auto_execute") is True
            and m.get("scope") == "news-auto-post"
            and m.get("policy_version") == policy_version()
            and m.get("prompt_version") == prompt_version()):
        return False
    try:
        if int(m.get("score")) < SCORE_MIN:
            return False
    except (TypeError, ValueError):
        return False
    nd = intent.get("news_detail") or {}
    form = nd.get("form")
    if form not in ("资讯", "精品文章") or nd.get("source_truncated"):
        return False
    if not nd.get("source_text") or not nd.get("story_id"):
        return False
    source_time = _parse_source_time(nd.get("time"))
    current = now or datetime.now()
    if source_time is None:
        return False
    age = (current - source_time).total_seconds()
    if age < -900 or age > _max_age_hours(form) * 3600:
        return False
    if _story_updated_after_ticket(m):
        return False
    reviews = intent.get("value_reviews") or {}
    return (reviews.get("editor") or {}).get("decision") == "accept"


def _execute_route(res):
    """执行响应分类：已有票据/执行结局=落账；明确拒绝=终局；其余才可恢复重试。"""
    status = (res or {}).get("status")
    if status in ("pending_review", "approved", "done", "failed", "unknown"):
        return status
    if (res or {}).get("denied"):
        return "denied"
    return "retry"


def _audit(action, **fields):
    """Trigger→Decision→Ticket 处置流；追加写且加锁，断电后仍能还原。"""
    row = {"at": datetime.now().isoformat(), "action": action,
           "policy_version": policy_version(), **fields}
    with file_lock(AUDIT_FILE):
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


ENTITY_GROUPS = {
    "苹果": ("苹果", "apple", "aapl"), "微软": ("微软", "microsoft", "msft"),
    "谷歌": ("谷歌", "google", "alphabet", "googl", "goog"),
    "Meta": ("meta", "meta platforms"), "亚马逊": ("亚马逊", "amazon", "amzn"),
    "英伟达": ("英伟达", "nvidia", "nvda"), "英特尔": ("英特尔", "intel", "intc"),
    "AMD": ("amd", "超威半导体"), "特斯拉": ("特斯拉", "tesla", "tsla"),
    "甲骨文": ("甲骨文", "oracle", "orcl"), "Palantir": ("palantir", "pltr"),
    "Anthropic": ("anthropic",), "OpenAI": ("openai",),
    "腾讯": ("腾讯", "tencent"), "阿里": ("阿里巴巴", "阿里", "alibaba"),
    "字节": ("字节跳动", "字节", "bytedance"), "智谱": ("智谱", "zhipu"),
    "Kimi": ("kimi", "月之暗面", "moonshot"), "DeepSeek": ("deepseek", "深度求索"),
    "台积电": ("台积电", "tsmc"), "三星": ("三星电子", "三星", "samsung"),
    "SK海力士": ("sk海力士", "sk hynix"), "铠侠": ("铠侠", "kioxia"),
    "DoorDash": ("doordash", "dash"), "Coinbase": ("coinbase", "coin"),
}
EVENT_CATEGORIES = (
    ("earnings", ("财报", "业绩", "营收", "利润", "eps", "毛利率", "季报", "财季")),
    ("financing", ("融资", "发债", "股债", "发行", "配售", "债券")),
    ("acquisition", ("收购", "并购", "合并")),
    ("policy", ("政策", "监管", "关税", "出口管制", "反垄断", "立法")),
    ("model", ("模型", "开源", "api", "token", "降价")),
)


def _entities(text):
    low = (text or "").lower()
    return {name for name, aliases in ENTITY_GROUPS.items()
            if any(alias in low for alias in aliases)}


def _event_category(text):
    low = (text or "").lower()
    for name, words in EVENT_CATEGORIES:
        if any(w in low for w in words):
            return name
    return ""


def _entity_mismatch(r):
    """模型在批处理中可能张冠李戴：标题主体与event主体明确冲突即拒绝。"""
    title_entities = _entities(r["item"].get("title") or "")
    event_entities = _entities(r.get("event") or "")
    return bool(title_entities and event_entities and title_entities.isdisjoint(event_entities))


def _source_unreadable(it):
    """识别 UTF-8 被误当 Latin-1 的乱码源（大量Latin-1补充字符且几乎无CJK）。"""
    text = it.get("text") or ""
    if len(text) < 80:
        return False
    bad = sum(1 for ch in text if 0x80 <= ord(ch) <= 0xFF)
    cjk = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF)
    return bad >= 30 and bad > cjk * 2


def _source_title_body_mismatch(it):
    """数据站源内容自检：标题主体必须能在正文找到。
    已知公司走别名表；其余英文公司/产品名做字面核对。查不出主体则交给模型，不硬拒。"""
    title, text = (it.get("title") or ""), (it.get("text") or "").lower()
    if not title or not text:
        return False
    title_entities = _entities(title)
    if title_entities:
        return not any(any(alias in text for alias in ENTITY_GROUPS[e])
                       for e in title_entities)
    stop = {"the", "and", "for", "with", "from", "eps", "etf", "api", "ceo",
            "ipo", "cpu", "gpu", "rss", "app", "usd"}
    tokens = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,}", title)
              if w.lower() not in stop]
    if tokens:
        return not any(w in text for w in tokens)
    return False


def _reject_row(it, reason):
    return {"item": it, "score": 0, "tier": "N",
            "event": (it.get("title") or "")[:30], "increment": "",
            "reason": reason, "publish": False}


def _structured_gate_enabled():
    try:
        import yaml
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        mode = str((cfg.get("news_auto") or {}).get("structured_gate") or "shadow").lower()
        return mode if mode in ("off", "shadow", "enforce") else "shadow"
    except Exception:
        return "shadow"


def _answer_value(a):
    if not isinstance(a, dict):
        return a
    for k in ("choice", "noul", "score", "value"):
        if k in a:
            return a[k]
    return None


def _structured_gate(it):
    """System One 结构化选题门：只硬拦截赛道外/普通或无法判断主体；
    信息增量等级只记录，不直接当分数阈值。"""
    from decision import ask
    state = {"title": it.get("title"), "source": it.get("source"),
             "time": it.get("time"), "text": (it.get("text") or "")[:6000]}
    questions = {
        "in_scope": {"type": "noul", "instructions": "素材是否属于美股科技/AI、重大宏观政策、腾讯/阿里/字节或智谱/Kimi/DeepSeek等中国头部模型公司的实质变动？港股/普通币价/小医疗/无关行业为否。"},
        "major_entity": {"type": "choice", "instructions": "主体是否为名企、头部公司、主要模型公司、关键平台/供应链、央行或主流机构？", "criteria": {
            "headline": "名企/头部/主要模型公司/关键主体", "ordinary": "普通中小企业或冷门公司", "other": "无法判断"}},
        "excluded_domain": {"type": "noul", "instructions": "素材是否属于医药/生物科技、普通新股上市、与科技或重大宏观无关的小公司新闻？是即为排除项。"},
        "increment": {"type": "score", "instructions": "这条素材的信息增量等级？", "criteria": ["无新增量", "轻微补充", "明确新增事实", "重大状态变化"]}
    }
    r = ask(state, questions, scene="news_gate")
    a = r["answers"]
    try:
        in_scope = float(_answer_value(a.get("in_scope")))
        major = str(_answer_value(a.get("major_entity")))
        excluded = float(_answer_value(a.get("excluded_domain")))
        increment = float(_answer_value(a.get("increment")))
    except (TypeError, ValueError) as e:
        raise ValueError(f"结构化选题答案无效:{a}") from e
    passed = in_scope >= 0.75 and major == "headline" and excluded < 0.5
    return {"pass": passed, "in_scope": in_scope, "major_entity": major,
            "excluded_domain": excluded, "increment": increment,
            "decision_id": r["decision_id"], "channel": r["channel"]}


def _story_id(r):
    cluster = str(r["item"].get("cluster_id") or "").strip()
    if cluster:
        return "cluster:" + cluster
    # 无cluster时：单一公司+同类事件归入同一 Story（7天状态窗内财报不会跨季度）
    text = (r.get("event") or "") + " " + (r["item"].get("title") or "")
    entities, category = _entities(text), _event_category(text)
    if len(entities) == 1 and category:
        return f"{next(iter(entities))}:{category}"
    key = _norm_title(r.get("event") or r["item"].get("title") or "")
    return "event:" + _hash_text(key) if key else "item:" + str(r["item"].get("id"))


def _story_views(st):
    stories = st.get("stories") or []
    return [{"story_id": s.get("story_id"), "event": s.get("event"),
             "increment": (s.get("increments") or [""])[-1]}
            for s in stories[-30:]]


def _update_stories(st, scored, selected, at):
    selected_ids = {id(r) for r in selected}
    stories = st.setdefault("stories", [])
    by_id = {s.get("story_id"): s for s in stories}
    for r in scored:
        sid = _story_id(r)
        s = by_id.get(sid)
        if s is None:
            s = {"story_id": sid, "event": r.get("event") or r["item"].get("title"),
                 "increments": [], "item_ids": [], "titles": [], "created_at": at}
            stories.append(s)
            by_id[sid] = s
        inc = (r.get("increment") or "").strip()
        if inc and _norm_title(inc) not in ("", "无", "没有", "无新增量"):
            if all(_norm_title(inc) != _norm_title(old) for old in s["increments"]):
                s["increments"].append(inc[:120])
        s["increments"] = s["increments"][-20:]
        iid = str(r["item"].get("id"))
        if iid not in s["item_ids"]:
            s["item_ids"].append(iid)
        s["item_ids"] = s["item_ids"][-30:]
        title = r["item"].get("title") or ""
        if title and title not in s["titles"]:
            s["titles"].append(title[:100])
        s["titles"] = s["titles"][-5:]
        s["last_at"] = at
        s["last_score"] = r["score"]
        s["last_disposition"] = "selected" if id(r) in selected_ids else "not_selected"
    st["stories"] = stories[-200:]


def _repeat_without_increment(r, stories):
    for s in stories:
        if s.get("story_id") != _story_id(r):
            continue
        inc = _norm_title(r.get("increment"))
        if inc in ("", "无", "没有", "无新增量", "无明显增量"):
            return True
        return any(inc == _norm_title(old) for old in s.get("increments") or [])
    return False


def _reconcile_tickets(st):
    """把人工批准后的票据终局接回处置流，不停留在“已开票”。"""
    known = st.setdefault("ticket_status", {})
    changed = False
    for p in (STATE_DIR / "tickets").glob("news-p-*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        tid, status = d.get("id"), d.get("status")
        if tid and status and known.get(tid) != status:
            _audit("ticket_outcome", ticket=tid, status=status,
                   note=str(d.get("note") or d.get("error") or "")[:120])
            known[tid] = status
            if ((d.get("payload") or {}).get("mandate") or {}).get("mode") == "auto":
                _record_auto_result(tid, status)
            changed = True
    # 只留最近 300 张票的结局缓存；JSONL 审计本身永久保留
    if len(known) > 300:
        st["ticket_status"] = dict(list(known.items())[-300:])
    return changed


def in_active_window(now=None):
    """资讯任务活跃窗，与浏览器工位共用 runtime.active_window（单一时间真相，
    默认 06:30-24:00）。窗外不开新小时、不生成待执行票；早窗内容由 since 位置
    留待开窗后首个小时桶统一评估，不丢失、不积压补发。"""
    now = now or datetime.now()
    cur = now.hour * 60 + now.minute
    start, end, _ = _active_window()
    return start <= cur < end


def _norm_title(s):
    return re.sub(r"[\W_]+", "", (s or "").lower())[:100]


def _dedup_items(items):
    out, seen = [], set()
    for it in items:
        text = (it.get("text") or "").strip()
        if len(text) < 20:
            continue
        key = _norm_title(it.get("title") or text[:60])
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _item_key(it):
    return _hash_text(str(it.get("id") or it.get("title") or it.get("text") or ""))


def _item_head(it):
    return (it.get("title") or it.get("text") or "")[:12]


def _parse_score_rows(out):
    """评分输出解析：优先完整 JSON；单个对象转义坏掉时逐对象抢救。
    最终覆盖完整性由 score_items 强制校验，抢救不等于允许漏评。"""
    m = re.search(r"\{.*\}", out or "", re.S)
    if not m:
        raise ValueError("评分输出非JSON")
    try:
        rows = json.loads(m.group(0)).get("results")
        if isinstance(rows, list):
            return rows
    except json.JSONDecodeError:
        pass
    rows = []
    for chunk in re.findall(r"\{[^{}]*\}", m.group(0), re.S):
        try:
            rows.append(json.loads(chunk))
            continue
        except json.JSONDecodeError:
            pass
        mi = re.search(r'"key"\s*:\s*"([0-9a-f]{10})"', chunk)
        if not mi:
            continue
        row = {"key": mi.group(1)}
        for key in ("event", "increment", "reason", "tier", "head"):
            mv = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)"', chunk, re.S)
            if mv:
                row[key] = mv.group(1)
        for key in ("score",):
            mv = re.search(rf'"{key}"\s*:\s*(\d+)', chunk)
            if mv:
                row[key] = int(mv.group(1))
        mv = re.search(r'"publish"\s*:\s*\"?(true|false)\"?', chunk, re.I)
        if mv:
            row["publish"] = mv.group(1).lower() == "true"
        rows.append(row)
    if not rows:
        raise ValueError("评分JSON无效且无对象可抢救")
    return rows


def _score_batch(llm, items, history, recent_events):
    lines = []
    for it in items:
        head = _item_head(it)
        lines.append(f"【key={_item_key(it)}｜head={head}】"
                     f"[{it.get('form')}·{it.get('source')}·{it.get('time')}] "
                     f"{it.get('title') or ''}\n{(it.get('text') or '')[:350]}")
    hist = "\n".join(f"· {x[:100]}" for x in history[-8:]) or "（无）"
    events = "\n".join(f"· [{e.get('story_id')}] {e.get('event')}｜最近增量:{e.get('increment','')}" for e in recent_events[-30:]) or "（无）"
    user = (f"【候选素材】\n" + "\n\n".join(lines)
            + f"\n\n【近期已发帖】\n{hist}\n\n【近期已评/已发事件】\n{events}")
    out = llm.chat("decision", SCORE_PROMPT, user, max_tokens=7000, timeout=120, retries=2)
    # 保留最后一次原始输出，便于字段/截断问题取证；不含凭据，仅素材评分
    Path("/tmp/news-score-last.txt").write_text(out or "", encoding="utf-8")
    return _parse_score_rows(out)


def _score_batch_checked(llm, batch, history, recent_events):
    rows = _score_batch(llm, batch, history, recent_events)
    by_key = {_item_key(it): it for it in batch}
    covered = set()
    for row in rows:
        key = str(row.get("key") or "")
        if key not in by_key or key in covered:
            raise ValueError(f"评分key未知或重复:{key}")
        covered.add(key)
    if covered != set(by_key):
        raise ValueError(f"评分覆盖不全:{len(covered)}/{len(batch)}")
    results = []
    for row in rows:
        key = str(row.get("key"))
        item = by_key[key]
        # 标题头用于抓“key复制正确、内容却串到别的候选”的隐蔽串稿；
        # 模型常多带 1-2 字，只要标准头是返回头的前缀（规范化空白后）即认可
        expected_head = re.sub(r"\s+", "", _item_head(item))
        returned_head = re.sub(r"\s+", "", str(row.get("head") or ""))
        if not (returned_head.startswith(expected_head) or expected_head.startswith(returned_head)):
            raise ValueError(f"评分标题头串稿:{key}")
        probe = {"item": item, "event": str(row.get("event") or "")}
        if _entity_mismatch(probe):
            raise ValueError(f"评分主体串稿:{key}")
        try:
            score = int(row.get("score"))
            tier = str(row.get("tier") or "").upper()
            pub = row.get("publish")
            if isinstance(pub, str) and pub.lower() in ("true", "false"):
                pub = pub.lower() == "true"
            if pub is None and tier in TIER_ORDER:
                pub = score >= SCORE_MIN and tier != "N"
            if tier not in TIER_ORDER or not isinstance(pub, bool):
                raise ValueError("评分缺少合法tier/publish")
        except (ValueError, TypeError) as e:
            raise ValueError(f"评分字段无效:{str(e)[:60]}") from e
        results.append({"item": item, "score": max(0, min(100, score)),
                        "tier": tier, "event": str(row.get("event") or "")[:30],
                        "increment": str(row.get("increment") or "")[:100],
                        "reason": str(row.get("reason") or "")[:120],
                        "publish": pub})
    return results


def score_items(llm, items, history=None, recent_events=None, batch_size=4):
    """源内容一致性硬门 + 小批评分；串稿拆单，仍冲突则明确淘汰。"""
    results, eligible = [], []
    for it in items:
        if _source_unreadable(it):
            log_event("news_auto", "wcixot", action="sourceUnreadable", item_id=it.get("id"))
            results.append(_reject_row(it, "源正文乱码，淘汰"))
        elif not it.get("text_truncated") and _source_title_body_mismatch(it):
            log_event("news_auto", "wcixot", action="sourceContentMismatch",
                      item_id=it.get("id"), note="标题主体在正文找不到")
            results.append(_reject_row(it, "标题与正文主体不一致，淘汰"))
        else:
            gate_mode = _structured_gate_enabled()
            if gate_mode != "off":
                gate = _structured_gate(it)
                log_event("news_auto", "wcixot",
                          action="decisionGateShadow" if gate_mode == "shadow" else "decisionGate",
                          item_id=it.get("id"), note=json.dumps(gate, ensure_ascii=False)[:160])
                # 原生结构化判断未通过校准前只影子记录；enforce 是以后单独授权的模式
                if gate_mode == "enforce" and not gate["pass"]:
                    results.append(_reject_row(it, "结构化决策门淘汰：赛道外或非头部主体"))
                    continue
            eligible.append(it)
    for off in range(0, len(eligible), batch_size):
        batch = eligible[off:off + batch_size]
        try:
            results.extend(_score_batch_checked(llm, batch, history or [], recent_events or []))
        except Exception as e:
            if len(batch) == 1:
                if "主体串稿" in str(e) or "标题头串稿" in str(e):
                    # 单条仍冲突：可能是源标题/正文错位，也可能模型误读；
                    # 两种都不得发布。明确淘汰而不是让整小时卡死或无限重试。
                    it = batch[0]
                    log_event("news_auto", "wcixot", action="sourceMismatch",
                              item_id=it.get("id"), note=str(e)[:80])
                    results.append(_reject_row(it, "标题与正文主体不一致，淘汰"))
                    continue
                raise
            log_event("news_auto", "wcixot", action="scoreResplit",
                      note=f"{len(batch)}条拆单重评:{str(e)[:80]}")
            for it in batch:
                results.extend(score_items(llm, [it], history or [], recent_events or [],
                                           batch_size=1))
    return results


def select_hour(scored, max_n=MAX_PER_HOUR, stories=None):
    """每小时择优：达阈值、同事件只留最高价值一条、普通题材同源最多两条。
    stories 提供跨小时事件记忆；同 Story 无新增量或重复增量不得再次入选。"""
    stories = stories or []
    qualified = [r for r in scored
                 if r["publish"] and r["score"] >= SCORE_MIN and r["tier"] != "N"
                 and not _repeat_without_increment(r, stories)]
    qualified.sort(key=lambda r: (-r["score"], TIER_ORDER.get(r["tier"], 9),
                                  -_iso_ts(r["item"].get("time"))))
    selected, events, sources = [], {}, Counter()
    for r in qualified:
        ekey = _story_id(r)
        source = r["item"].get("source") or "?"
        if ekey and ekey in events:
            continue
        if sources[source] >= 2 and r["tier"] != "S":
            continue
        selected.append(r)
        if ekey:
            events[ekey] = True
        sources[source] += 1
        if len(selected) >= max_n:
            break
    return selected


def replay(hours=24, resume=False):
    """按时间顺序逐小时评分并累积 Story；不写线上调度状态、不创建票据。
    resume=True 从证据文件最后完成的小时之后续跑（失败小时本身会重评）。"""
    from datastation import fetch_material
    from llm import LLM
    from cognitive import recent_history
    raw = (fetch_material(forms=("资讯",), limit=160, hours=hours, text_limit=1400)
           + fetch_material(forms=("精品文章",), limit=60, hours=hours, text_limit=1800))
    items = _dedup_items(raw)
    print(f"回放原料 {len(raw)} 条，去重/过短过滤后 {len(items)} 条", flush=True)
    raw_by_hour = {}
    for it in items:
        raw_by_hour.setdefault(str(it.get("time") or "未知")[:13], []).append(it)
    llm, history = LLM(), recent_history()
    story_state = {"stories": []}
    report, evaluations = [], []
    out_path = Path("/tmp/news-auto-dry-run.json")
    if resume and out_path.exists():
        prev = json.loads(out_path.read_text(encoding="utf-8"))
        report = prev.get("report") or []
        evaluations = prev.get("evaluations") or []
        selected_keys = {(h["hour"], str(r["item"].get("id")))
                         for h in report for r in h.get("selected", [])}
        for r in sorted(evaluations, key=lambda x: str(x["item"].get("time") or "")):
            hour = str(r["item"].get("time") or "未知")[:13]
            chosen = [r] if (hour, str(r["item"].get("id"))) in selected_keys else []
            _update_stories(story_state, [r], chosen, hour)
        if report:
            last_done = report[-1]["hour"]
            raw_by_hour = {k: v for k, v in raw_by_hour.items() if k > last_done}
            print(f"从 {last_done} 之后续跑；保留此前 {len(evaluations)} 条评分", flush=True)
    for hour in sorted(raw_by_hour):
        batch = raw_by_hour[hour]
        scored = score_items(llm, batch, history, _story_views(story_state))
        evaluations.extend(scored)
        chosen = select_hour(scored, stories=story_state["stories"])
        _update_stories(story_state, scored, chosen, hour)
        report.append({"hour": hour, "selected": chosen})
        # 每小时即落证据：后续批次失败也保留已完成的验收结果
        out_path.write_text(json.dumps({"partial": True, "generated_at": datetime.now().isoformat(),
                                        "hours": hours, "raw": len(raw),
                                        "scored": len(evaluations), "report": report,
                                        "evaluations": evaluations,
                                        "policy_version": policy_version(),
                                        "prompt_version": prompt_version()},
                                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{hour}: 候选{len(batch)} 评分{len(scored)} 入选{len(chosen)}", flush=True)
        for r in chosen:
            title = r["item"].get("title") or r["item"].get("text", "")[:40]
            print(f"  {r['score']:3} {r['tier']} [{r['item'].get('form')}·{r['item'].get('source')}] {title[:60]}", flush=True)
            print(f"      Story：{_story_id(r)}｜增量：{r['increment'][:80]}", flush=True)
    print(f"\n有效评分 {len(evaluations)} 条；"
          f"publish=true {sum(r['publish'] for r in evaluations)} 条；"
          f"分数达标 {sum(r['publish'] and r['score'] >= SCORE_MIN for r in evaluations)} 条；"
          f"Story {len(story_state['stories'])} 个", flush=True)
    out = {"generated_at": datetime.now().isoformat(), "hours": hours,
           "raw": len(raw), "scored": len(evaluations), "report": report,
           "evaluations": evaluations, "policy_version": policy_version(),
           "prompt_version": prompt_version()}
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完整证据：{out_path}", flush=True)


def _fetch_new(st):
    """两类 Source 各自保存 last_time；当批用 next_cursor 完整翻页。
    返回（未评条目, 本次观察到的各源最大时间）；只有评分成功后才推进位置。"""
    from datastation import fetch_incremental
    sources = st.setdefault("sources", {})
    positions = {}
    raw = []
    for key, form, default_hours, limit, text_limit in (
            ("news", "资讯", 2, 50, 1400),
            ("article", "精品文章", 12, 20, 1800)):
        last_time = (sources.get(key) or {}).get("last_time") or ""
        if not last_time:
            last_time = (datetime.now() - timedelta(hours=default_hours)).strftime("%Y-%m-%d %H:%M")
        rows = fetch_incremental(form, since=last_time, limit=limit, text_limit=text_limit)
        raw.extend(rows)
        times = [str(it.get("time") or "") for it in rows if it.get("time")]
        if times:
            positions[key] = max(times)
    seen = st.get("evaluated") or {}
    return _dedup_items([it for it in raw if not seen.get(str(it.get("id")))]), positions


def tick():
    """正常调度入口（systemd 每 5min）。选中结果先落盘，再逐张成稿/开票；
    未完成小时优先恢复，票据 ID 按小时+序号固定，重启不重复评估/开票。"""
    if not in_active_window():
        return
    try:
        check_pause()
    except PauseActive:
        return
    from cognitive import recent_history, xc
    from datastation import fetch_full_item, MaterialDefect
    from editorial import produce_post, EditorialSkip
    from llm import LLM
    st = _load_state()
    reconciled = _reconcile_tickets(st)
    now = datetime.now()
    now_iso = now.isoformat()[:19]
    hours = st.setdefault("hours", {})
    llm = LLM()

    # 恢复优先：任何已选但未完成的小时先于新小时执行
    pending = sorted((k, v) for k, v in hours.items()
                     if not v.get("done") and v.get("pending_selection") is not None)
    recovering = bool(pending)
    if recovering:
        bucket, h = pending[0]
        selected = h["pending_selection"]
    else:
        bucket = now.strftime("%Y-%m-%dT%H")
        new_bucket = bucket not in hours
        h = hours.setdefault(bucket, {"run_minute": random.SystemRandom().randint(2, 54)})
        if h.get("done"):
            if reconciled:
                _save_state(st)
            return
        if now.minute < h["run_minute"]:
            if new_bucket or reconciled:
                _save_state(st)
            return
        try:
            items, positions = _fetch_new(st)
        except Exception as e:
            log_event("news_auto", "wcixot", action="sourceFail", note=str(e)[:120])
            return
        # Trigger 先落盘；received 不等于 consumed，评分失败仍会在下拍重试
        received = st.setdefault("received", {})
        for it in items:
            iid = str(it.get("id"))
            if iid not in received:
                _audit("received", item_id=iid, form=it.get("form"),
                       source=it.get("source"), title=it.get("title"),
                       item_time=it.get("time"), cluster_id=it.get("cluster_id"))
                received[iid] = now_iso
        _save_state(st)
        try:
            scored = score_items(llm, items, recent_history(), _story_views(st)) if items else []
        except Exception as e:
            log_event("news_auto", "wcixot", action="scoreFail", note=str(e)[:120])
            return
        selected = select_hour(scored, stories=st.get("stories") or [])
        selected_keys = {id(r) for r in selected}
        for r in scored:
            _audit("decision", item_id=r["item"].get("id"), story_id=_story_id(r),
                   title=r["item"].get("title"), score=r["score"], tier=r["tier"],
                   event=r["event"], increment=r["increment"], reason=r["reason"],
                   publish=r["publish"], selected=id(r) in selected_keys)
        for it in items:
            st.setdefault("evaluated", {})[str(it.get("id"))] = now_iso
        for key, pos in positions.items():
            st.setdefault("sources", {}).setdefault(key, {})["last_time"] = pos
        _update_stories(st, scored, selected, now_iso)
        h.update({"candidates": len(items), "selected": len(selected),
                  "pending_selection": selected, "drafts": [],
                  "ticketed_ids": [], "denied_ids": [], "skipped_ids": [],
                  "policy_version": policy_version(), "prompt_version": prompt_version()})
        st["last_run_at"] = now_iso
        _save_state(st)

    # 选中结果已经持久化；逐张成稿与开票，每张结束立即保存
    drafted = [d["text"] for d in h.get("drafts", []) if d.get("text")]
    for i, r in enumerate(selected, 1):
        hour_id = datetime.strptime(bucket, "%Y-%m-%dT%H").strftime("%Y%m%d-%H")
        iid = f"news-p-{hour_id}-{i}"
        if iid in h.get("ticketed_ids", []) or iid in h.get("denied_ids", []) or iid in h.get("skipped_ids", []):
            continue
        it = r["item"]
        story_id = _story_id(r)
        try:
            # 立论前恢复全文；源标题/正文主体错位在完整素材上再验一次
            full = fetch_full_item(it)
            if _source_unreadable(full):
                raise EditorialSkip("全文乱码，禁止立论")
            if _source_title_body_mismatch(full):
                raise EditorialSkip("全文主体与标题不一致")
            produced = produce_post(llm, full,
                                    next((s for s in st.get("stories", [])
                                          if s.get("story_id") == story_id), {}),
                                    recent_history() + drafted)
            text = produced["text"]
        except EditorialSkip as e:
            _audit("editorial_skip", item_id=it.get("id"), story_id=story_id,
                   ticket=iid, note=str(e)[:160])
            h.setdefault("skipped_ids", []).append(iid)
            _save_state(st)
            continue
        except MaterialDefect as e:
            # 确定性素材缺陷（原条目消失/全文超长）：重试永远同结果，
            # 跳过该条并留审计，避免一条毒素材堵死整个小时及后续所有小时
            _audit("editorial_skip", item_id=it.get("id"), story_id=story_id,
                   ticket=iid, note=f"素材缺陷:{str(e)[:140]}")
            h.setdefault("skipped_ids", []).append(iid)
            _save_state(st)
            continue
        except Exception as e:
            log_event("news_auto", "wcixot", action="editorialError", ticket=iid, note=str(e)[:120])
            return
        _audit("drafted", item_id=it.get("id"), story_id=story_id, ticket=iid,
               prompt_version=prompt_version(), chars=len(text),
               editor_decision=produced["reviews"]["editor"]["decision"])
        news_detail = {"item_id": full.get("id"), "source": full.get("source"),
                       "source_id": full.get("source_id"), "form": full.get("form"),
                       "time": full.get("time"), "title": full.get("title"),
                       "markets": full.get("markets") or [], "url": full.get("url"),
                       "source_text": full.get("text") or "", "source_truncated": False,
                       "cluster_id": full.get("cluster_id"),
                       "story_id": story_id, "score": r["score"], "tier": r["tier"],
                       "increment": r["increment"], "selection_reason": r["reason"]}
        rationale = (f"资讯源自动发布：{r['tier']}·{r['score']}分·{full.get('form')}·{full.get('source')}；"
                     f"增量：{r['increment'][:70]}")
        res = xc("POST", "/execute", {"kind": "post", "intent_id": iid,
                                      "text": text.strip(), "rationale": rationale,
                                      "news_detail": news_detail,
                                      "value_reviews": produced["reviews"],
                                      "mandate": make_mandate(r, story_id)}, timeout=120)
        status = _execute_route(res)
        if status in ("pending_review", "approved", "done", "failed", "unknown"):
            # auto 会在同一次 /execute 内完成真实动作；这些都不是“开票失败”，
            # 必须立即记票并停止同票重试。approved=工位不可用后的确定性待重试。
            _audit("ticketed", item_id=it.get("id"), story_id=story_id,
                   ticket=iid, status=status)
            h.setdefault("ticketed_ids", []).append(iid)
            h.setdefault("drafts", []).append({"ticket": iid, "text": text.strip()})
            st.setdefault("ticket_status", {})[iid] = status
            drafted.append(text.strip())
            if _configured_mode() == "auto":
                _record_auto_result(iid, status)
            log_event("news_auto", "wcixot", action="ticket",
                      ticket=iid, score=r["score"], status=status)
            _save_state(st)
        elif res.get("denied"):
            _audit("ticket_denied", item_id=it.get("id"), story_id=story_id,
                   ticket=iid, note=str(res.get("denied"))[:120])
            h.setdefault("denied_ids", []).append(iid)
            log_event("news_auto", "wcixot", action="deny",
                      ticket=iid, score=r["score"], note=str(res.get("denied"))[:100])
            _save_state(st)
        else:
            # 只有无业务状态的传输/内部错误才允许下拍同票号恢复。
            _audit("ticket_error", item_id=it.get("id"), story_id=story_id,
                   ticket=iid, note=str(res)[:120])
            log_event("news_auto", "wcixot", action="ticketError",
                      ticket=iid, note=str(res)[:100])
            _save_state(st)
            return
    h.update({"done": True, "done_at": datetime.now().isoformat()[:19],
              "pending_selection": None, "ticketed": len(h.get("ticketed_ids", []))})
    st["hours"] = {k: v for k, v in hours.items() if _hour_ts(k) >= time.time() - 72 * 3600}
    st["evaluated"] = {k: v for k, v in st.get("evaluated", {}).items()
                       if _iso_ts(v) >= time.time() - 48 * 3600}
    st["received"] = {k: v for k, v in st.get("received", {}).items()
                      if _iso_ts(v) >= time.time() - 48 * 3600}
    st["stories"] = [s for s in st.get("stories", [])
                     if _iso_ts(s.get("last_at")) >= time.time() - 7 * 86400][-200:]
    _save_state(st)
    log_event("news_auto", "wcixot", action="hourDone", bucket=bucket,
              candidates=h.get("candidates", 0), selected=h.get("selected", 0),
              ticketed=h.get("ticketed", 0), recovered=recovering)


def _iso_ts(s):
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        return 0


def _hour_ts(bucket):
    try:
        return datetime.strptime(bucket, "%Y-%m-%dT%H").timestamp()
    except Exception:
        return 0


if __name__ == "__main__":
    if "--dry-run" in sys.argv:
        hours = 24
        for arg in sys.argv[1:]:
            if arg.startswith("--hours="):
                hours = max(1, min(48, int(arg.split("=", 1)[1])))
        replay(hours, resume="--resume" in sys.argv)
    else:
        tick()
