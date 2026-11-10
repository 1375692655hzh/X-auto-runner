# tickers.py — 产业链关联标的判定器 v4（2026-11-10 MoA 四岗汇总，运营拍板）
# 结构（五段管线）：
#   ①代码 direct 扫描器：素材(title+text)∪成稿正文 白名单别名命中 + 行业统称映射表
#     ——确定性、永不失败、零 LLM。v3 漏 $TSM/$MU 的根因是检索活交给生成模型，此层根治。
#   ②LLM 搜集：只产 chain（事件定性 + supplier/peer/cycle/customer/substitute + why），
#     白名单内嵌 prompt（模型从菜单点选，不靠记忆）。
#   ③代码硬校验：schema 逐行（role 枚举/why≤20字）→ 白名单精确查 → 中性词扫描
#     → 结果性×customer 因果闸。坏行丢弃，不拖垮整批。
#   ④LLM 校验（质检员）：verdict-only（keep/drop/fix，只删不改不增），fail-open。
#   ⑤代码终审：去重(direct 优先)→排序→截10→分层拼行→长度预算。
# 降级阶梯：direct+已校验chain → direct+未校验chain → direct-only → 空行；永不阻塞发布。
# 安全边界（不变）：只标美股主上市白名单；标签恒中性；绝不猜码；不进首屏不贴落锤。
import json
import re

from ledger import log_event

# ── 美股白名单：ticker → (公司名, 知名度档 1=超级明星/2=知名/3=次知名, 中文别名…) ──
# 只收 NYSE/NASDAQ 主板（含 TSM/BABA 类 ADR）；ETF、OTC（如腾讯 TCEHY）、
# 未上市公司（字节/OpenAI/DeepSeek/智谱）不入表——映射不到就丢弃。
WHITELIST = {
    "AAPL": ("苹果", 1, "Apple"), "MSFT": ("微软", 1, "Microsoft"),
    "NVDA": ("英伟达", 1, "Nvidia", "NVIDIA"), "GOOGL": ("谷歌", 1, "Google", "Alphabet"),
    "AMZN": ("亚马逊", 1, "Amazon"), "META": ("Meta", 1, "Facebook", "脸书", "元宇宙"),
    "TSLA": ("特斯拉", 1, "Tesla"), "AVGO": ("博通", 1, "Broadcom"),
    "TSM": ("台积电", 1, "台积", "TSMC"), "AMD": ("AMD", 1, "超威"),
    "ORCL": ("甲骨文", 1, "Oracle"), "CRM": ("赛富时", 1, "Salesforce"),
    "NFLX": ("奈飞", 1, "Netflix"), "JPM": ("摩根大通", 1, "JPMorgan"),
    "V": ("Visa", 1, "维萨"), "MA": ("万事达", 1, "Mastercard"),
    "GS": ("高盛", 1, "Goldman Sachs"), "MS": ("摩根士丹利", 1, "Morgan Stanley", "大摩"),
    "BAC": ("美国银行", 2, "美银", "Bank of America"), "C": ("花旗", 2, "Citigroup", "花旗集团"),
    "WFC": ("富国银行", 2, "Wells Fargo", "富国"), "XOM": ("埃克森美孚", 1, "Exxon", "埃克森"),
    "CVX": ("雪佛龙", 1, "Chevron"), "OXY": ("西方石油", 2, "Occidental"),
    "COP": ("康菲石油", 2, "ConocoPhillips", "康菲"), "SLB": ("斯伦贝谢", 2, "SLB", "Schlumberger"),
    "WMT": ("沃尔玛", 1, "Walmart"), "COST": ("开市客", 1, "Costco", "好市多"),
    "HD": ("家得宝", 2, "Home Depot"), "TGT": ("塔吉特", 3, "Target"),
    "MCD": ("麦当劳", 2, "McDonald"), "SBUX": ("星巴克", 2, "Starbucks"),
    "NKE": ("耐克", 2, "Nike"), "KO": ("可口可乐", 1, "Coca-Cola"),
    "PEP": ("百事", 2, "Pepsi", "百事可乐"), "PG": ("宝洁", 2, "P&G", "Procter"),
    "DIS": ("迪士尼", 1, "Disney"), "BA": ("波音", 1, "Boeing"),
    "LMT": ("洛克希德马丁", 2, "Lockheed", "洛马"), "RTX": ("雷神技术", 2, "Raytheon", "雷神"),
    "GD": ("通用动力", 3, "General Dynamics"), "NOC": ("诺斯罗普格鲁曼", 3, "Northrop", "诺格"),
    "GE": ("通用电气", 2, "GE", "General Electric"), "CAT": ("卡特彼勒", 2, "Caterpillar", "卡特"),
    "DE": ("迪尔", 3, "Deere", "约翰迪尔"), "HON": ("霍尼韦尔", 2, "Honeywell"),
    "EMR": ("艾默生电气", 3, "Emerson", "艾默生"), "ETN": ("伊顿", 2, "Eaton"),
    "LLY": ("礼来", 1, "Eli Lilly"), "UNH": ("联合健康", 1, "UnitedHealth"),
    "JNJ": ("强生", 1, "Johnson & Johnson"), "MRK": ("默沙东", 2, "Merck"),
    "ABBV": ("艾伯维", 2, "AbbVie"), "PFE": ("辉瑞", 2, "Pfizer"),
    "ASML": ("阿斯麦", 1, "ASML Holding"), "AMAT": ("应用材料", 1, "Applied Materials"),
    "LRCX": ("泛林集团", 1, "Lam Research", "泛林"), "KLAC": ("科磊", 1, "KLA", "KLA-Tencor"),
    "MRVL": ("迈威尔", 2, "Marvell"), "QCOM": ("高通", 1, "Qualcomm"),
    "ARM": ("Arm", 2, "ARM Holdings", "安谋"), "MU": ("美光", 1, "Micron"),
    "INTC": ("英特尔", 1, "Intel"), "SMCI": ("超微电脑", 2, "Super Micro", "Supermicro"),
    "DELL": ("戴尔", 1, "Dell"), "ANET": ("Arista", 2, "Arista Networks"),
    "COHR": ("Coherent", 3, "高意"), "ALAB": ("Astera Labs", 3, "阿斯特拉实验室"),
    "CRWV": ("CoreWeave", 2, "Coreweave"), "NBIS": ("Nebius", 3, "Nebius Group"),
    "SNOW": ("Snowflake", 2, "雪花"), "PLTR": ("Palantir", 1, "帕兰泰尔"),
    "PANW": ("Palo Alto", 2, "Palo Alto Networks", "派拓"), "CRWD": ("CrowdStrike", 1),
    "NOW": ("ServiceNow", 2), "ADBE": ("Adobe", 1, "奥多比"), "INTU": ("Intuit", 2),
    "IBM": ("IBM", 1, "国际商业机器"), "CSCO": ("思科", 1, "Cisco"),
    "ACN": ("埃森哲", 1, "Accenture"), "SNPS": ("新思科技", 2, "Synopsys"),
    "CDNS": ("楷登电子", 2, "Cadence"), "ADI": ("亚德诺", 2, "Analog Devices"),
    "TXN": ("德州仪器", 1, "Texas Instruments"), "ON": ("安森美", 2, "Onsemi"),
    "MCHP": ("微芯", 3, "Microchip"), "NXPI": ("恩智浦", 2, "NXP"),
    "MPWR": ("Monolithic Power", 3, "MPS"), "TER": ("泰瑞达", 3, "Teradyne"),
    "VRT": ("Vertiv", 2, "维谛"),
    "VST": ("Vistra", 2, "Vistra Energy"), "CEG": ("Constellation Energy", 2, "星座能源"),
    "GEV": ("GE Vernova", 2, "GE维尔诺瓦"), "NRG": ("NRG Energy", 3),
    "FLR": ("Fluor", 3, "福陆"), "JCI": ("江森自控", 3, "Johnson Controls"),
    "UBER": ("Uber", 1, "优步"), "ABNB": ("Airbnb", 1, "爱彼迎"),
    "LYFT": ("Lyft", 3), "DASH": ("DoorDash", 2, "DoorDash Inc"),
    "SHOP": ("Shopify", 2), "PYPL": ("PayPal", 1, "贝宝"),
    "COIN": ("Coinbase", 2, "Coinbase Global"),
    "HOOD": ("Robinhood", 2, "罗宾汉"), "MSTR": ("MicroStrategy", 2, "Strategy", "微策略"),
    "MARA": ("MARA Holdings", 3, "Marathon Digital"), "RIOT": ("Riot Platforms", 3, "Riot"),
    "SPOT": ("Spotify", 1, "声田"), "RBLX": ("Roblox", 2, "罗布乐思"),
    "U": ("Unity", 3, "Unity Software"), "PATH": ("UiPath", 3),
    "AI": ("C3.ai", 3, "C3 AI"), "NET": ("Cloudflare", 2),
    "DDOG": ("Datadog", 2), "MDB": ("MongoDB", 3, "Mongo"),
    "BABA": ("阿里巴巴", 1, "Alibaba", "阿里"), "PDD": ("拼多多", 1, "Pinduoduo", "Temu"),
    "JD": ("京东", 1, "JD.com"), "BIDU": ("百度", 2, "Baidu"),
    "NTES": ("网易", 2, "NetEase"), "BILI": ("哔哩哔哩", 3, "Bilibili", "B站"),
    "TME": ("腾讯音乐", 3, "Tencent Music"), "BEKE": ("贝壳", 3, "KE Holdings"),
    "LI": ("理想汽车", 2, "Li Auto", "理想"), "NIO": ("蔚来", 2, "NIO Inc"),
    "XPEV": ("小鹏汽车", 2, "XPeng", "小鹏"), "ZK": ("极氪", 3, "Zeekr"),
    "YUMC": ("百胜中国", 3, "Yum China"), "QFIN": ("奇富科技", 3, "Qifu"),
    "F": ("福特", 2, "Ford"), "GM": ("通用汽车", 2, "General Motors"),
    "RIVN": ("Rivian", 2, "Rivian Automotive"), "LCID": ("Lucid", 3),
    "DKNG": ("DraftKings", 3), "TTWO": ("Take-Two", 2, "Take Two"),
    "EA": ("艺电", 3, "Electronic Arts"),
    "SQ": ("Block", 3, "Square"), "AFRM": ("Affirm", 3),
    # 电信/媒体/存储/航天（素材高频出现，2026-11-10 首跑实测补齐）
    "TMUS": ("T-Mobile", 1, "T-Mobile US"), "VZ": ("Verizon", 2, "威瑞森"),
    "T": ("AT&T", 2, "美国电话电报"), "CMCSA": ("康卡斯特", 2, "Comcast"),
    "SONY": ("索尼", 1, "Sony Group"), "WBD": ("华纳兄弟探索", 3, "Warner Bros"),
    "WDC": ("西部数据", 2, "Western Digital", "西数"), "STX": ("希捷", 3, "Seagate"),
    "RKLB": ("Rocket Lab", 2, "火箭实验室"), "IRDM": ("Iridium", 3, "铱星通信", "铱星"),
    "LUNR": ("Intuitive Machines", 3), "ASTS": ("AST SpaceMobile", 3),
}
# 杠杆/主题 ETF 一律不入表（MoA 共识）；未上市公司无代码不猜

_ALIAS = {}
for _t, (_name, _fame, *_aliases) in WHITELIST.items():
    for _a in (_name, _t, *_aliases):
        if _a:
            _ALIAS[_a.strip().lower()] = _t


def resolve(name_or_ticker: str) -> str | None:
    """公司名/别名/ticker → 白名单权威 ticker；查不到返回 None（宁缺毋错）。
    v4 起生产路径不再依赖本函数的模糊分支——扫描器正向匹配、LLM 输出精确查表；
    保留给测试与人工排查工具。"""
    s = re.sub(r"[\s（）()·\.]", "", str(name_or_ticker or "")).lower()
    if not s:
        return None
    if s.startswith("$"):
        s = s[1:]
    if s in _ALIAS:
        return _ALIAS[s]
    for alias, t in _ALIAS.items():
        if len(alias) < 3:
            continue
        if s.startswith(alias) or (len(s) >= 3 and alias.startswith(s)) or alias in s:
            return t
        if len(s) >= 4 and s in alias:
            return t
    return None


def _exact(name_or_ticker: str) -> str | None:
    """精确解析（生产用）：白名单/别名表命中才返回，模糊兜底一律 None。"""
    s = re.sub(r"[\s（）()·\.]", "", str(name_or_ticker or "")).lower()
    if s.startswith("$"):
        s = s[1:]
    return _ALIAS.get(s)


# ── ① direct 扫描器（确定性） ──────────────────────────────────────────────
# 拉丁别名用 ASCII 字母数字边界（CJK 字符视作边界，"NVDA大涨"能命中）；
# ≤2 字符的 ticker（V/T/C/F/U/AI…）裸词误报面大，只认 $ 前缀（$V、$AI）；
# CJK 别名子串匹配；最长别名优先（"腾讯音乐"先于"腾讯"类冲突）。
def _build_scan():
    rows = []
    for t, (name, fame, *aliases) in WHITELIST.items():
        for a in (name, t, *aliases):
            if not a:
                continue
            if a.isascii():
                if len(a) <= 2:
                    pat = re.compile(rf"(?<![A-Za-z0-9])\${re.escape(a)}(?![A-Za-z0-9])", re.I)
                else:
                    pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", re.I)
                rows.append((a, t, pat))
            else:
                rows.append((a, t, None))  # CJK：直接子串
    rows.sort(key=lambda r: len(r[0]), reverse=True)
    return rows


_SCAN = _build_scan()

# 行业统称映射（v3 prompt 第二节整体搬进代码——查表内容不该每次推理重烧 token）
INDUSTRY_MAP = {
    "超大云厂商": ("AMZN", "MSFT", "GOOGL", "META"),
    "云厂商": ("AMZN", "MSFT", "GOOGL", "META"),
    "半导体设备商": ("ASML", "AMAT", "LRCX", "KLAC"),
    "存储厂商": ("MU", "WDC", "STX"),
    "AI芯片商": ("NVDA", "AMD", "AVGO", "MRVL"),
    "消费电子巨头": ("AAPL",),
    "电动车巨头": ("TSLA",),
    "中国互联网巨头": ("BABA", "PDD", "JD"),
    "加密货币": ("COIN", "MSTR", "MARA"),
}


def extract_direct(item, final_text=""):
    """确定性 direct 抽取：素材(title+text) ∪ 成稿正文的白名单别名命中 + 行业统称映射。
    返回 [(ticker, source)]，source ∈ named|industry。永不失败、零 LLM。"""
    text = f"{item.get('title') or ''}\n{item.get('text') or ''}\n{final_text or ''}"
    found = {}
    for alias, t, pat in _SCAN:
        hit = bool(pat.search(text)) if pat is not None else (alias in text)
        if hit and t not in found:
            found[t] = "named"
    for phrase, tickers in INDUSTRY_MAP.items():
        if phrase in text:
            for t in tickers:
                if t not in found:
                    found[t] = "industry"
    return sorted(found.items(), key=lambda kv: (WHITELIST[kv[0]][1], kv[0]))


# ── ② LLM 搜集（chain 专用 prompt，白名单内嵌） ────────────────────────────
_WL_LINES = "\n".join(
    f"{t} {WHITELIST[t][0]}" + (f"（{'/'.join(a for a in aliases if a)}）" if aliases else "")
    for t, (name, fame, *aliases) in WHITELIST.items())

CHAIN_PROMPT = f"""你是美股产业链研究员。输入财经素材和已确定的直接涉及公司，你的唯一任务：找出素材未点名、但事件沿产业链真实传导到的关联上市公司（chain），每条给 ≤20 字传导理由。

先给事件定性：结果性（营收/财报/出货数据=既有需求的确认）还是 变动性（涨价/断供/新技术/新协议/政策=改变成本、供给或格局）。

传导方向（因果不倒置）：
- supplier 上游供应商：主体扩产/需求上行→设备与材料商接单
- peer 同行/竞争对手：景气互证，一家数据验证全行业周期
- cycle 景气同向环节：事件确认某周期→同周期其他环节（AI资本开支→电力/散热/存储）
- customer 下游客户：仅变动性事件且素材有涨价/断供/独家绑定信号时推，why 必须写明
- substitute 替代者：技术路线之争的输家或新路线赢家

候选池（ticker 只准从这个池里选，池外一律不输出）：
{_WL_LINES}

规则：chain 2-6 条，环节多样性优先；每条 why ≤20 字且必填，写传导机制不写观点；禁"受益/利好/利空/受损/承压/看多/看空"等方向词；直接涉及清单里的公司不得再输出；素材没有具体业务落点（无公司、产品、订单、金额、产能等实指，仅泛政策/泛趋势/征求意见）时 chain 必须为空——宁缺毋滥，不硬凑；除此之外全部环节都无真实关联才允许 chain 为空。
只输出 JSON：{{"event_type":"结果性|变动性","chain":[{{"ticker":"","role":"supplier|peer|cycle|customer|substitute","why":"","relevance":"high|med"}}]}}"""

# ── ④ LLM 校验（质检员，verdict-only，fail-open） ─────────────────────────
VERIFY_PROMPT = """你是标的质检员，只挑错、不添新。输入素材与待核 chain 候选，逐条判断：
1) why 所述传导在素材中有落点吗（素材没提的环节=空穴来风→drop）；
2) role 与 event_type 因果方向一致吗（结果性事件配 customer→drop）；
3) why 含方向词或空洞吗（→drop）；
4) 素材为泛政策/泛趋势且无具体落点（无公司、产品、订单、金额、产能实指）时全部 drop——宁缺毋滥。
证据不足宁可错杀。只输出 JSON：{"verdicts":[{"ticker":"","action":"keep|drop|fix","role":"仅在fix时给新role","reason":"≤15字"}]}"""

_REL = {"high": 0, "med": 1, "medium": 1, "low": 2}
_ROLES = {"supplier", "peer", "cycle", "customer", "substitute"}
_NEUTRAL_RE = re.compile(r"受益|利好|利空|受损|承压|跑赢|看多|看空|大涨|暴跌|爆仓|血洗|崩盘")


def _parse_json(out):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (out or "").strip())
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        try:
            d = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            d = None
    return d if isinstance(d, dict) else None


def _chain_rows(llm, material, direct_list):
    """LLM 搜集 + ③代码硬校验。返回 (event_type, rows)；rows 元素 {t,role,why,rel}。"""
    user = (f"素材：\n{material}\n\n"
            f"直接涉及（不得重复输出）：{' '.join(direct_list) if direct_list else '无'}")
    try:
        out = llm.chat("decision", CHAIN_PROMPT, user, max_tokens=800, timeout=40, retries=1)
    except Exception as e:
        log_event("tickers", "wcixot", action="chainSkip", note=str(e)[:100])
        return "", []
    d = _parse_json(out)
    if d is None:
        log_event("tickers", "wcixot", action="parseFail", note=str(out)[:80])
        return "", []
    ev = str(d.get("event_type") or "")
    rows, dropped, whys, seen = [], [], [], {}
    for row in (d.get("chain") or []):
        if not isinstance(row, dict):
            continue
        raw = str(row.get("ticker") or "").strip()
        t = _exact(raw)
        if not t:
            dropped.append(f"{raw[:16]}:非白名单")
            continue
        role = str(row.get("role") or "").strip().lower()
        why = str(row.get("why") or "").strip()
        rel = _REL.get(str(row.get("relevance") or "med").lower(), 1)
        reason = None
        if role not in _ROLES:
            reason = "role非法"
        elif not why or len(why) > 20:
            reason = "why缺失/超长"
        elif _NEUTRAL_RE.search(why):
            reason = "why方向词"
        elif ev == "结果性" and role == "customer":
            reason = "结果性×customer"
        if reason:
            dropped.append(f"{t}:{reason}")
            continue
        if t in seen:                       # 同 ticker 跨 role：保留关联度更高者
            if seen[t]["rel"] <= rel:
                continue
            rows.remove(seen[t])
        e = {"t": t, "role": role, "why": why, "rel": rel}
        seen[t] = e
        rows.append(e)
        whys.append(f"{t}:{role}/{why[:18]}")
    if dropped:
        log_event("tickers", "wcixot", action="rowDropped", note=";".join(dropped)[:160])
    if whys:
        log_event("tickers", "wcixot", action="chainWhy", note=";".join(whys)[:200])
    return ev, rows


def _verify_chain(llm, material, ev, rows):
    """④ 质检员：verdict-only。drop→删行，fix→改 role，其余 keep。
    任何失败 fail-open 放行（宁可放过不可错杀），绝不阻塞发布。"""
    if not rows:
        return rows
    listing = "\n".join(f"{r['t']} {r['role']}｜{r['why']}" for r in rows)
    user = f"素材：\n{material[:1500]}\n\nevent_type：{ev or '未知'}\n待核候选：\n{listing}"
    try:
        out = llm.chat("decision", VERIFY_PROMPT, user, max_tokens=400, timeout=30, retries=0)
    except Exception as e:
        log_event("tickers", "wcixot", action="verifySkip", note=str(e)[:80])
        return rows
    d = _parse_json(out)
    verdicts = {}
    if d:
        for v in (d.get("verdicts") or []):
            if isinstance(v, dict) and v.get("ticker"):
                verdicts[str(v["ticker"]).strip().upper()] = v
    if not verdicts:
        log_event("tickers", "wcixot", action="verifySkip", note="verdicts空/解析失败")
        return rows
    kept, applied = [], []
    for r in rows:
        v = verdicts.get(r["t"])
        act = str((v or {}).get("action") or "").lower()
        if act == "drop":
            applied.append(f"drop {r['t']}:{str(v.get('reason') or '')[:12]}")
            continue
        if act == "fix" and str(v.get("role") or "").lower() in _ROLES:
            applied.append(f"{r['t']}→{str(v['role']).lower()}")
            r = {**r, "role": str(v["role"]).lower()}
        kept.append(r)
    if applied:
        log_event("tickers", "wcixot", action="verifyApplied", note=";".join(applied)[:160])
    return kept


def related_tickers(llm, item, final_text="", cap=10):
    """v4 五段管线主入口。返回 [(ticker, kind)]，kind ∈ direct|chain；
    排序 直接涉及→关联度→知名度→字母，截 cap。"""
    material = f"{item.get('title') or ''}\n{(item.get('text') or '')[:4000]}"
    direct = extract_direct(item, final_text)
    if direct:
        log_event("tickers", "wcixot", action="directScan",
                  note=";".join(f"{t}:{s}" for t, s in direct)[:160])
    ev, rows = _chain_rows(llm, material, [t for t, _ in direct])
    rows = _verify_chain(llm, material, ev, rows)
    entries = {}
    for t, _src in direct:
        entries[t] = {"t": t, "kind": "direct", "key": (0, 0, WHITELIST[t][1], t)}
    for r in rows:
        if r["t"] in entries:              # direct 优先，chain 不覆盖
            continue
        entries[r["t"]] = {"t": r["t"], "kind": "chain",
                           "key": (1, r["rel"], WHITELIST[r["t"]][1], r["t"])}
    ranked = sorted(entries.values(), key=lambda e: e["key"])[:cap]
    return [(e["t"], e["kind"]) for e in ranked]


def format_line(pairs):
    """[(ticker, kind)] → 帖尾一行；直接涉及与产业链关联分层标注；空返回 ""。"""
    if not pairs:
        return ""
    direct = [f"${t}" for t, k in pairs if k == "direct"]
    chain = [f"${t}" for t, k in pairs if k == "chain"]
    if direct and chain:
        return f"涉及标的：{' '.join(direct)}｜关联：{' '.join(chain)}"
    if direct:
        return f"涉及标的：{' '.join(direct)}"
    return f"关联标的：{' '.join(chain)}"


def apply_ticker_line(text, llm, item, max_len=None):
    """帖尾追加标的行（编辑后、互关tag前）。text 即最终成稿，一并参与 direct 扫描。
    超长度预算自动放弃。返回 (最终文本, 标的行或"")。"""
    pairs = related_tickers(llm, item, final_text=text, cap=10)
    line = format_line(pairs)
    if not line:
        return text, ""
    if max_len is not None:
        from quality import weighted_len
        if weighted_len(text) + weighted_len(line) + 2 > max_len:
            log_event("tickers", "wcixot", action="skipLenBudget", note=line[:60])
            return text, ""
    return f"{text}\n\n{line}", line
