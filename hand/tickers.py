# tickers.py — 产业链关联标的判定器（2026-11-10 运营拍板）
# 定位：帖尾元数据索引，不属于正文创作。运营要求：不只列素材点名公司，
# 覆盖产业链关联方（上游/下游/竞对/替代），按关联度、知名度排序，上限 10 只。
# 安全边界（MoA 三岗共识保留项）：只标美股主上市白名单；标签恒中性；永不贴判断句；
# 直接涉及与产业链关联分层标注；非白名单一律丢弃绝不猜码。
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
    """公司名/别名/ticker → 白名单权威 ticker；查不到返回 None（宁缺毋错）。"""
    s = re.sub(r"[\s（）()·\.]", "", str(name_or_ticker or "")).lower()
    if not s:
        return None
    if s.startswith("$"):
        s = s[1:]
    # 直接命中（已去空格/括号）
    if s in _ALIAS:
        return _ALIAS[s]
    # 前缀/包含匹配兜底（"台积电(TSMC)"、"Nvidia Corp"）；
    # 短查询（<4字）禁止"查询⊂别名"方向——防"腾讯"误中"腾讯音乐"（宁缺毋错）
    for alias, t in _ALIAS.items():
        if len(alias) < 3:
            continue
        if s.startswith(alias) or (len(s) >= 3 and alias.startswith(s)) or alias in s:
            return t
        if len(s) >= 4 and s in alias:
            return t
    return None


_REL = {"high": 0, "med": 1, "medium": 1, "low": 2}

PROMPT = """你是美股产业关联判定器。输入一篇财经素材，判断事件沿产业链真实传导到哪些知名美股上市公司。目标：每帖给出 3-8 个真实关联（direct+chain 合计）；只有素材与任何美股上市公司都无真实关联时才允许清单为空——不要因为谨慎而交白卷。

一、先给事件定性：结果性事件（营收/财报/出货数据=既有需求的确认）还是变动性事件（涨价/断供/新技术/新协议/政策=改变某方的成本、供给或竞争格局）。

二、素材明示的行业统称必须映射为具体公司并归入 direct：超大云厂商→AMZN/MSFT/GOOGL/META；半导体设备商→ASML/AMAT/LRCX/KLAC；存储厂商→MU/WDC/STX；AI芯片商→NVDA/AMD/AVGO/MRVL；消费电子巨头→AAPL；电动车→TSLA；中国互联网→BABA/PDD/JD；矿机/加密→COIN/MSTR/MARA。素材点名对照方/被比较方（如"芯片商CDS vs 云厂商"）时，对照双方都是 direct。

三、结果性事件的 chain 传导方向（因果不倒置）：
- supplier 上游供应商：主体扩产/需求上行→设备与材料商接单受益（营收飙→ASML/AMAT/LRCX/KLAC）。
- peer 同行/竞争对手：景气度互证，一家的数据验证全行业周期。
- cycle 景气同向环节：事件确认某条周期时，同周期其他环节（AI资本开支→电力VRT/VST/CEG、散热、存储）。
- customer 下游客户：默认不推——客户需求是结果性事件的原因不是被改变方；仅当事件改变客户成本/供给/议价（涨价/断供/独家绑定）时推，why 必须写明。
- substitute 替代者：事件削弱某技术路线时的输家或新路线赢家。
每个环节取 1-2 家最强代表，环节多样性优先于数量；全部环节都无真实关联才留空。

四、只输出在 NYSE 或 NASDAQ 主板上市的公司；中国大陆/香港公司仅当存在美股主上市 ADR（如阿里巴巴=BABA）；ETF、OTC、未上市公司（字节/OpenAI/DeepSeek/智谱/xAI）一律不输出。
五、每个关联必须给 role 和 why（≤20字传导机制），给不出 why 就不列。relevance：high=核心主体或直接传导，med=明确环节（low 不输出）。
只输出 JSON：{"event_type":"结果性|变动性","direct":[{"company":"","ticker":"","relevance":"high|med"}],"chain":[{"company":"","ticker":"","role":"supplier|peer|cycle|customer|substitute","why":"","relevance":"high|med"}]}"""


def related_tickers(llm, item, cap=10):
    """LLM 判定 + 白名单校验。返回 [(ticker, kind)]，kind ∈ direct|chain；
    按直接涉及→关联度→知名度排序，截断 cap。未知公司丢弃并记日志（含 why 便于审计）。"""
    material = f"{item.get('title') or ''}\n{(item.get('text') or '')[:4000]}"
    out = llm.chat("decision", PROMPT, material, max_tokens=1200, timeout=45, retries=1)
    s = (out or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        d = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            log_event("tickers", "wcixot", action="parseFail", note=str(out)[:80])
            return []
        try:
            d = json.loads(m.group(0))
        except json.JSONDecodeError:
            log_event("tickers", "wcixot", action="parseFail", note=str(out)[:80])
            return []
    entries, dropped, whys = [], [], []
    for kind in ("direct", "chain"):
        for row in (d.get(kind) or []):
            if not isinstance(row, dict):
                continue
            t = resolve(row.get("company") or row.get("ticker") or "")
            if not t:
                dropped.append(str(row.get("company") or row.get("ticker"))[:20])
                continue
            rel = _REL.get(str(row.get("relevance") or "med").lower(), 1)
            fame = WHITELIST[t][1]
            entries.append({"t": t, "kind": kind, "key": (0 if kind == "direct" else 1, rel, fame, t)})
            if kind == "chain" and row.get("why"):
                whys.append(f"{t}:{str(row.get('role') or '?')}/{str(row['why'])[:18]}")
    # 同 ticker 去重：direct 优先保留
    seen = {}
    for e in entries:
        if e["t"] not in seen or e["kind"] == "direct":
            seen[e["t"]] = e
    ranked = sorted(seen.values(), key=lambda e: e["key"])[:cap]
    if dropped:
        log_event("tickers", "wcixot", action="droppedNonWhitelist", note=";".join(dropped)[:120])
    if whys:
        log_event("tickers", "wcixot", action="chainWhy", note=";".join(whys)[:200])
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
    """帖尾追加标的行（编辑后、互关tag前）。超长度预算自动放弃。
    返回 (最终文本, 标的行或"")。"""
    pairs = related_tickers(llm, item)
    line = format_line(pairs)
    if not line:
        return text, ""
    if max_len is not None:
        from quality import weighted_len
        if weighted_len(text) + weighted_len(line) + 2 > max_len:
            log_event("tickers", "wcixot", action="skipLenBudget", note=line[:60])
            return text, ""
    return f"{text}\n\n{line}", line
