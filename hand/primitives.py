# primitives.py — X 浏览器原语（P1-2）：x-auto-run executor.js/boost.js → Playwright-Python(Camoufox)
# 移植对照（行号为 ref/x-auto-run 源码）：
#   SEL/RISK_TOAST        executor.js:139-143
#   human_scroll          executor.js:149 / boost.js humanScroll（15% 回看在 browse 层，P4 humanize 增强）
#   human_click           executor.js:156
#   verify_login          executor.js:166（适配：Firefox 无 CDP，cookie 通道走 context.cookies()）
#   read_toast/risk_toast executor.js:183-186
#   probe_reply_composer  executor.js:190（10-22"回复变独立帖"事故修复，原样移植）
#   type_text             executor.js:213 typeReply（mode 严格作用域 + ClipboardEvent 粘贴）
#   submit_and_confirm    executor.js:227 submitReply（气泡判定/unknown/disabled/unbound 语义原样）
#   goto_feed             executor.js:259（expectSid 软重定向落地校验）
#   find_article_by_status executor.js:271（不取 arts[0]）
#   reply_pipeline        executor.js:278 replyToPost（statusId 守卫/读帖停顿∝字数/媒体帖不回/绑定闸循环）
#   follow/unfollow/like  executor.js:307/355/342（含互关徽标复查/幂等赞/maintain_dwell）
#   post_tweet/post_quote executor.js:411/428（C3 语义分叉：发过/already=重复非新发）
#   collect_posts         boost.js:289（升级：加 has_media 字段——媒体检测闸底座，需求表 #1-#9）
# 平台差异：puppeteer→playwright；CDP→context.cookies()；其余 1:1
import math
import random
import re
import time
import urllib.parse

SEL = {
    "article": 'article[data-testid="tweet"]',
    "reply": '[data-testid="reply"]',
    "reply_box": '[data-testid="tweetTextarea_0"]',
    "like": '[data-testid="like"]',
    "unlike": '[data-testid="unlike"]',
    "toast": '[data-testid="toast"]',
    "tweet_text": '[data-testid="tweetText"]',
    "user_name": '[data-testid="User-Name"]',
}
RISK_TOAST = re.compile(
    r"出错|稍后再试|无法|请重试|try again|something went wrong|limit|restrict|block|エラー|もう一度|制限|できません", re.I)

PACING = {  # config.json pacing 原值
    "actionDelayGaussMs": (5000, 2000), "postGapGaussMs": (25000, 10000),
    "postRestGaussMs": (80000, 40000), "communityReplyGapGaussMs": (45000, 15000),
    "taskGapGaussMs": (120000, 60000), "readDwellGaussMs": (5000, 2000),
    "warmupMs": (30000, 90000), "hoverMs": (300, 800),
}


def sleep(ms):
    time.sleep(ms / 1000)


def gauss(mean, sd):
    """executor.js:55 同款 Box-Muller（下限 mean*0.2 防负值/防过快）"""
    u1 = random.random() or 1e-9
    u2 = random.random()
    return max(mean * 0.2, mean + math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2) * sd)


def gsleep(pair):
    sleep(gauss(pair[0], pair[1]))


def rand(a, b):
    return a + random.random() * (b - a)


# ── 拟人操作 ──────────────────────────────────────────────
def human_scroll(page, total_px):
    """分段平滑滚动 5-12 段、每段 0.7-1.3 倍速；15% 概率"再看一眼"回滚 40%（boost.js:140-151）"""
    segs = 5 + int(random.random() * 8)
    for _ in range(segs):
        page.evaluate("p => window.scrollBy({top: p, behavior: 'smooth'})",
                      (total_px / segs) * (0.7 + random.random() * 0.6))
        sleep(100 + random.random() * 300)
    if random.random() < 0.15:
        page.evaluate("p => window.scrollBy({top: -p, behavior: 'smooth'})", total_px * 0.4)
        sleep(600 + random.random() * 1200)


def human_click(page, locator):
    """boundingBox 内随机落点 + 多步移动 + hover 停顿（executor.js:156）"""
    try:
        bb = locator.bounding_box()
        if bb:
            page.mouse.move(bb["x"] + bb["width"] * (0.3 + random.random() * 0.4),
                            bb["y"] + bb["height"] * (0.3 + random.random() * 0.4),
                            steps=6 + int(random.random() * 8))
            sleep(rand(*PACING["hoverMs"]))
    except Exception:
        pass
    locator.click()


# ── 登录校验（双通道，executor.js:166） ───────────────────
def verify_login(page, context, handle):
    try:
        page.wait_for_selector('a[data-testid="AppTabBar_Profile_Link"]', timeout=15000)
        href = page.evaluate(
            "() => { const a = document.querySelector('a[data-testid=\"AppTabBar_Profile_Link\"]'); return a ? a.getAttribute('href') : null; }")
        if href and href.lower() == "/" + handle.lower():
            return True
    except Exception:
        pass
    try:  # 通道二：twid cookie → intent 反查（适配：context.cookies() 替代 CDP）
        cookies = context.cookies(["https://x.com"])
        twid = next((c["value"] for c in cookies if c["name"] == "twid"), "")
        m = re.search(r"u=(\d+)", urllib.parse.unquote(twid))
        if not m:
            return False
        page.goto("https://x.com/intent/user?user_id=" + m.group(1),
                  wait_until="domcontentloaded", timeout=60000)
        sleep(4000)
        return ("screen_name=" + handle.lower()) in urllib.parse.unquote(page.url).lower()
    except Exception:
        return False


# ── Toast（executor.js:183） ─────────────────────────────
def read_toast(page):
    try:
        t = page.evaluate(
            "(sel) => { const t = document.querySelector(sel); return t ? (t.textContent||'').replace(/\\s+/g,' ').trim().slice(0,80) : null; }",
            SEL["toast"])
        return t
    except Exception:
        return None


def risk_toast(page):
    t = read_toast(page)
    return t if (t and RISK_TOAST.search(t)) else None


# ── 回复绑定闸（executor.js:190，10-22 事故修复，原样移植） ──
PROBE_JS = """
([sid, handle]) => {
  const boxSel = '[data-testid="tweetTextarea_0"]';
  const dlg = document.querySelector('[role="dialog"]');
  if (dlg && dlg.querySelector(boxSel)) {
    const art = dlg.querySelector('article');
    if (!art) return { ok: false, note: 'dialog无父帖卡片(独立发帖弹窗)' };
    if (handle) {
      const hit = [...art.querySelectorAll('a[href^="/"]')].some((a) => ((a.getAttribute('href')||'').split('/')[1]||'').toLowerCase() === handle);
      if (!hit) return { ok: false, note: 'dialog父帖作者非@' + handle };
    }
    return { ok: true, mode: 'dialog' };
  }
  const m = location.pathname.match(/^\\/[^/]+\\/status\\/(\\d+)/);
  if (m && sid && m[1] === String(sid) && document.querySelector(boxSel)) return { ok: true, mode: 'inline' };
  return { ok: false, note: '无绑定composer(' + location.pathname + ')' };
}
"""


def probe_reply_composer(page, sid, handle=None):
    try:
        return page.evaluate(PROBE_JS, [str(sid), handle])
    except Exception:
        return None


# ── 输入（executor.js:213：mode 严格作用域 + ClipboardEvent 粘贴） ──
TYPE_JS = """
([text, mode]) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return false;
  el.focus();
  const dt = new DataTransfer();
  dt.setData('text/plain', text);
  el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  return true;
}
"""


def type_text(page, text, mode=None):
    """输入通道 v4（2026-10-26/27 五轮真机取证定稿）：
    ① keyboard.insert_text 整段（IME 合成事件——真实中文用户的输入路径，Draft.js
       原生正确处理；**必须单发整段**，分段/分块会触发首块甩尾或内容爆炸）
    ② execCommand insertText：顺序正确但被双次应用（TEXT+TEXT），带"恰双份→删后半"修正
    ③ keyboard.type：首字符必甩尾（Draft.js 选区缺陷）——仅理论兜底
    ④ 合成 paste：被编辑器忽略——末位
    一切以内容逐字校验（零宽清洗+空白归一）通过为准；宁可 failed 不错字。"""
    m = mode or "any"
    try:
        if page.evaluate(_FOCUS_JS, m):
            sleep(400)
            if box_len(page, m) > 0:
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                sleep(300)
            page.keyboard.insert_text(text)
            sleep(700)
            if _text_matches(page, text, m):
                return True
        if "\n" not in text and page.evaluate(_EXEC_INSERT_JS, [text, m]) == "ok":
            # execCommand 已知可能双写；多行正文不走此通道，避免块结构下双份定位失真
            sleep(600)
            page.evaluate(_FIX_DOUBLE_JS, [text, m])
            sleep(300)
            if _text_matches(page, text, m):
                return True
        if page.evaluate(_FOCUS_JS, m):
            sleep(400)
            if box_len(page, m) > 0:
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                sleep(300)
            page.keyboard.type(text, delay=55)
            sleep(600)
            if _text_matches(page, text, m):
                return True
        if page.evaluate(TYPE_JS, [text, m]):
            sleep(500)
            if _text_matches(page, text, m):
                return True
        return False
    except Exception:
        return False


_EXEC_INSERT_JS = """
([text, mode]) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return 'nobox';
  el.focus();
  return document.execCommand('insertText', false, text) ? 'ok' : 'cmd-false';
}
"""

_FIX_DOUBLE_JS = """
([text, mode]) => {
  // execCommand 在此编辑器被双次应用：内容恰为 text+text 时选中后半并删除。
  // 选区定位用 TreeWalker 把全局字符偏移映射到具体文本节点。
  // （review 复核逮出：单实例 TreeWalker 游标跨两次 posAt 消费——每次 posAt
  //   必须 createTreeWalker 重置游标，否则第二次从半个文档继续走 → 恒 map-fail）
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return 'nobox';
  const t = el.textContent || '';
  if (t !== text + text) return 'not-double';
  const posAt = (target) => {
    const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    let acc = 0, n;
    while ((n = walk.nextNode())) {
      const L = (n.textContent || '').length;
      if (acc + L >= target && target >= acc) return { node: n, off: target - acc };
      acc += L;
    }
    return null;
  };
  const s = posAt(text.length), e = posAt(text.length * 2);
  if (!s || !e) return 'map-fail';
  const R = document.createRange();
  R.setStart(s.node, s.off);
  R.setEnd(e.node, e.off);
  const g = window.getSelection();
  g.removeAllRanges();
  g.addRange(R);
  return document.execCommand('delete') ? 'fixed' : 'del-fail';
}
"""

_TEXT_MATCH_JS = """
([text, mode]) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return false;
  // Draft.js 段落是多个 data-block，软换行是 <br>；textContent 会把块粘连，
  // 空白归一又会把丢换行误判为成功。按块/BR还原逻辑纯文本后再逐字比较。
  const readNode = (n) => {
    if (n.nodeType === Node.TEXT_NODE) return n.nodeValue || '';
    if (n.nodeName === 'BR') return '\\n';
    if (n.nodeName === 'IMG') return n.getAttribute('alt') || '';
    return Array.from(n.childNodes || []).map(readNode).join('');
  };
  const blocks = Array.from(el.querySelectorAll('[data-block="true"]'));
  const blockText = blocks.map(readNode).join('\\n');
  // 空占位块不能覆盖真实可见文本；innerText反映当前换行/空行的渲染结果。
  // 任一可靠表示与预期逐字一致即可，textContent仅在无innerText时兜底。
  const representations = [];
  if (blocks.length && (blocks.length > 1 || blockText.length)) representations.push(blockText);
  if (el.innerText) representations.push(el.innerText);
  if (!representations.length) representations.push(el.textContent || '');
  const canon = (s) => (s || '').replace(/\\r\\n?/g, '\\n')
    .replace(/[\\u200b-\\u200f\\u2060\\ufeff]/g, '')
    .replace(/\\u00a0/g, ' ').replace(/^\\n+|\\n+$/g, '');
  const expected = canon(text);
  return representations.some((s) => canon(s) === expected);
}
"""



_CLICK_FOCUS_JS = """
(mode) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return false;
  el.click();
  el.focus();
  return true;
}
"""


def _text_matches(page, text, mode="any"):
    try:
        return bool(page.evaluate(_TEXT_MATCH_JS, [text, mode]))
    except Exception:
        return False


_BOX_LEN_JS = """
(mode) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  return el && el.textContent ? el.textContent.length : 0;
}
"""

_FOCUS_JS = """
(mode) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = mode === 'dialog' ? document.querySelector('[role="dialog"] ' + sel)
    : mode === 'inline' ? document.querySelector(sel)
    : (document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel));
  if (!el) return false;
  el.focus();
  return true;
}
"""


def box_len(page, mode="any"):
    try:
        return int(page.evaluate(_BOX_LEN_JS, mode))
    except Exception:
        return 0


def _keyboard_fallback(page, text, mode="any"):
    """分块键盘输入（P4 拟人矩阵的雏形：3-8 字块、块间随机停顿）。
    两轮尝试：首轮 el.focus；首字符被甩尾等错位→清空后二轮点击聚焦+静置重敲。
    内容逐字校验通过才返回 True——宁可 failed 不错字。"""
    import random
    for attempt in range(2):
        focus_js = _FOCUS_JS if attempt == 0 else _CLICK_FOCUS_JS
        if not page.evaluate(focus_js, mode):
            return False
        sleep(400 if attempt == 0 else 800)  # 聚焦后等选区初始化稳定
        if box_len(page, mode) > 0:  # 上一轮残留/粘贴半截 → 清空重敲
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            sleep(300)
        i = 0
        while i < len(text):
            chunk = text[i:i + random.randint(3, 8)]
            page.keyboard.type(chunk, delay=random.randint(40, 90))
            i += len(chunk)
            if random.random() < 0.3:
                sleep(random.randint(150, 450))
        sleep(600)
        if _text_matches(page, text, mode):
            return True
    return False


# ── 提交与成功判定（executor.js:227，四态语义原样） ────────
def submit_and_confirm(page, sid=None, handle=None):
    """返回 True(成功) | 'unknown' | False(无气泡) | 'disabled' | 'unbound:原因'"""
    if sid:  # 提交前绑定复验：宁可漏发不可错发独立帖
        g = probe_reply_composer(page, sid, handle)
        if not g or not g.get("ok"):
            return "unbound:" + ((g or {}).get("note") or "探针异常")
    has_dialog = bool(page.evaluate("() => !!document.querySelector('[role=\"dialog\"]')"))

    def read_st():
        try:
            return page.evaluate("""() => {
                const box = document.querySelector('[data-testid=\"tweetTextarea_0\"]');
                return { dlg: !!document.querySelector('[role=\"dialog\"]'),
                         boxLen: box && box.textContent ? box.textContent.length : 0 };
            }""")
        except Exception:
            return None

    def ok_toast():
        t = read_toast(page) or ""
        return bool(re.search(r"已发送|发过了|送信|already|posted|sent", t, re.I))

    clicked = page.evaluate("""() => {
        const dlg = document.querySelector('[role=\"dialog\"]');
        let b = null;
        if (dlg) { const btns = [...dlg.querySelectorAll('[data-testid=\"tweetButton\"]')]; b = btns[btns.length - 1]; }
        else b = document.querySelector('[data-testid=\"tweetButtonInline\"]');
        if (!b || b.disabled || b.getAttribute('aria-disabled') === 'true') return 'disabled';
        b.click(); return true;
    }""")
    if clicked is not True:
        return clicked if clicked == "disabled" else False

    def wait_toast(ms):
        for _ in range(int(ms / 500)):
            sleep(500)
            if ok_toast():
                return True
        return False

    for _ in range(30):
        sleep(500)
        st = read_st()
        if st is None:
            continue
        if ok_toast():
            return True
        if has_dialog and not st["dlg"]:
            return True if wait_toast(4000) else "unknown"
        if not has_dialog and st["boxLen"] == 0:
            return True if wait_toast(4000) else "unknown"
    return False


# ── 导航（executor.js:259：expectSid 软重定向落地校验） ────
def goto_feed(page, url, tries=2, expect_sid=None, must_have_article=True):
    for i in range(tries):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if must_have_article:
                page.wait_for_selector(SEL["article"], timeout=45000)
            else:
                # X 改版后 explore 首屏是趋势 hub 壳（tab 导航无 article 卡，
                # 2026-10-29 hotAbort×9 事故根因）：短等后放行，内容靠滚动懒加载；
                # 登录墙仍然拒绝
                try:
                    page.wait_for_selector(SEL["article"], timeout=10000)
                except Exception:
                    if page.query_selector('[data-testid="loginButton"]'):
                        if i < tries - 1:
                            sleep(3000)
                            continue
                        return False
            if expect_sid and f"/status/{expect_sid}" not in (page.url or ""):
                if i < tries - 1:
                    sleep(3000)
                    continue
                return False
            return True
        except Exception:
            if i < tries - 1:
                sleep(3000)
    return False


def find_article_by_status(page, status_id):
    """按 statusId 定位（不取 arts[0]——审计坑）"""
    arts = page.query_selector_all(SEL["article"])
    for a in arts:
        try:
            if a.query_selector(f'a[href*="/status/{status_id}"]'):
                return a
        except Exception:
            continue
    return None


# ── Feed 抓取（boost.js:289 + has_media 升级） ───────────
COLLECT_JS = """
([sel, limit]) => {
  const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
  return [...document.querySelectorAll(sel.article)].slice(0, limit).map((art) => {
    const link = art.querySelector('a[href*="/status"]');
    const time = art.querySelector('time');
    const text = art.querySelector(sel.tweetText);
    const nameEl = art.querySelector(sel.userName + ' a[href^="/"]');
    return {
      id: (link && (link.getAttribute('href').match(/\\/status\\/(\\d+)/) || [])[1]) || null,
      link: link ? link.getAttribute('href') : null,
      time: time ? time.getAttribute('datetime') : null,
      text: text ? clean(text.textContent).slice(0, 300) : '',
      author: nameEl ? nameEl.getAttribute('href').replace(/^\\//, '').toLowerCase() : null,
      has_media: !!art.querySelector('[data-testid="tweetPhoto"], [data-testid="videoPlayer"]'),
      locked: [...art.querySelectorAll('button, [role="button"]')].some((b) => /subscribe to unlock|订阅.{0,2}解锁|ロック解除/i.test((b.textContent || '').trim())),
    };
  }).filter((p) => p.id && p.link);
}
"""


def collect_posts(page, limit=20):
    # 注意：JS 内用驼峰键（tweetText/userName），与 Python SEL 蛇形键在此对齐
    sel2 = {"article": SEL["article"], "tweetText": SEL["tweet_text"], "userName": SEL["user_name"]}
    try:
        return page.evaluate(COLLECT_JS, [sel2, limit]) or []
    except Exception:
        return []


# ── 动作管线 ──────────────────────────────────────────────
def reply_pipeline(page, entry):
    """executor.js:278 replyToPost 全管线。entry: {post_url, status_id, text, target_handle?, target_len?}
    返回 {ok, unknown?, note?}"""
    sid = str(entry.get("status_id") or "")
    if not re.fullmatch(r"\d+", sid):
        return {"ok": False, "note": "statusId非法:" + sid}
    if not goto_feed(page, entry["post_url"], 2, sid):
        return {"ok": False, "note": "页面加载失败/落地被重定向"}
    dwell_f = min(2.2, 0.6 + entry["target_len"] / 150) if entry.get("target_len") else 1  # 读帖停顿∝原帖字数
    gsleep((PACING["readDwellGaussMs"][0] * dwell_f, PACING["readDwellGaussMs"][1] * dwell_f))
    art = find_article_by_status(page, sid)
    if not art:
        return {"ok": False, "note": "按 statusId 未找到目标帖"}
    if art.query_selector('[data-testid="tweetPhoto"], [data-testid="videoPlayer"]'):
        return {"ok": False, "note": "带媒体帖不回（媒体检测闸默认策略）"}
    btn = art.query_selector(SEL["reply"])
    if not btn:
        return {"ok": False, "note": "回复按钮未找到"}
    target_handle = (entry.get("target_handle") or
                     ((re.search(r"/([^/]+)/status/", entry.get("post_url") or "") or [None, ""])[1] or "")).lower() or None
    human_click(page, btn)
    gate = None  # 绑定闸循环 6×2s（executor.js:295）
    for _ in range(6):
        sleep(2000)
        gate = probe_reply_composer(page, sid, target_handle)
        if gate and gate.get("ok"):
            break
    if not gate or not gate.get("ok"):
        return {"ok": False, "note": "composer无回复绑定:" + ((gate or {}).get("note") or "探针异常")}
    if not type_text(page, entry["text"], gate.get("mode")):
        return {"ok": False, "note": "输入失败"}
    sleep(800)
    r = submit_and_confirm(page, sid, target_handle)
    if r is True:
        return {"ok": True}
    if r == "unknown":
        return {"ok": False, "unknown": True}
    if isinstance(r, str) and r.startswith("unbound:"):
        return {"ok": False, "note": "提交前绑定丢失:" + r[8:]}
    if r == "disabled":
        return {"ok": False, "note": "发布按钮不可点(超限/禁用)"}
    return {"ok": False, "note": "点击后无成功气泡"}


def like_post(page, entry):
    """executor.js:342：幂等赞 + 1200ms 复核；maintain 模式先做权重停留"""
    if not goto_feed(page, entry["post_url"]):
        return {"ok": False, "note": "页面加载失败"}
    art = find_article_by_status(page, entry["status_id"])
    if not art:
        return {"ok": False, "note": "按 statusId 未找到目标帖"}
    if art.query_selector(SEL["unlike"]):
        return {"ok": True, "note": "已赞过"}
    if entry.get("kind") == "maintain":
        maintain_dwell(page, entry)
    btn = art.query_selector(SEL["like"])
    if not btn:
        return {"ok": False, "note": "点赞按钮未找到"}
    human_click(page, btn)
    sleep(1200)
    return ({"ok": True} if art.query_selector(SEL["unlike"])
            else {"ok": False, "unknown": True, "note": "结果未确认"})


def follow_user(page, handle):
    """executor.js:307"""
    if not goto_feed(page, "https://x.com/" + handle):
        return {"ok": False, "note": "主页加载失败"}
    gsleep(PACING["readDwellGaussMs"])
    btns = page.query_selector_all('button[data-testid$="-follow"]')
    if not btns:
        return {"ok": False, "note": "关注按钮未找到(可能已关注)"}
    human_click(page, btns[0])
    sleep(1500)
    if page.query_selector('button[data-testid$="-unfollow"]'):
        return {"ok": True}
    if page.query_selector_all('button[data-testid$="-follow"]'):
        return {"ok": False, "note": "点击后未生效"}
    return {"ok": False, "unknown": True, "note": "结果未确认"}


def unfollow_user(page, handle):
    """executor.js:355：互关徽标现场复查——互关一律保留"""
    if not goto_feed(page, "https://x.com/" + handle):
        return {"ok": False, "note": "主页加载失败"}
    sleep(1500)
    if page.query_selector('[data-testid="userFollowIndicator"]'):
        return {"ok": True, "note": "互关保留（现场复查发现互关徽标，跳过取关）", "skipped_mutual": True}
    unbtns = page.query_selector_all('button[data-testid$="-unfollow"]')
    if not unbtns:
        return {"ok": False, "note": "取关按钮未找到（可能本就未关注）"}
    gsleep((9000, 3000))  # 取关节奏比关注更慢
    human_click(page, unbtns[0])
    sleep(1200)
    confirm = page.query_selector('[data-testid="confirmationSheetConfirm"]')
    if confirm:
        human_click(page, confirm)
        sleep(1500)
    return ({"ok": True} if page.query_selector('button[data-testid$="-follow"]')
            else {"ok": False, "unknown": True, "note": "取关未确认"})


def post_tweet(page, text):
    """executor.js:411：compose 页；C3 语义分叉（发过/already=重复非新发）
    2026-10-27 实证：浏览器冷启动直奔 compose 弹窗时开时不开——第 2 轮前先回 home
    热身（等 feed 渲染）再进 compose，与取证脚本同款打法。"""
    box = None
    for attempt in range(2):
        try:
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
        except Exception:
            return {"ok": False, "note": "发布页加载失败"}
        try:
            page.wait_for_selector(SEL["reply_box"], timeout=20000)
        except Exception:
            pass
        sleep(2500)  # 等弹窗动画稳定
        box = page.query_selector(SEL["reply_box"])
        if box:
            break
        if attempt == 0:  # 热身：home 等 feed 渲染后再试一轮 compose
            try:
                page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
                for _ in range(6):
                    sleep(2500)
                    if page.evaluate("() => document.querySelectorAll('article').length") > 0:
                        break
            except Exception:
                pass
    if not box:
        return {"ok": False, "note": "输入框未出现(两轮+热身)"}
    if not type_text(page, text):
        return {"ok": False, "note": "输入失败"}
    sleep(800)
    r = submit_and_confirm(page)  # compose 弹窗与回复共用气泡机制；发帖不传 sid（无父帖）
    if r is not True:
        if r == "unknown":
            return {"ok": False, "unknown": True}
        if r == "disabled":
            return {"ok": False, "note": f"发布按钮不可点(框长{box_len(page)}——超限/未落字)"}
        return {"ok": False, "note": "点击后15s无成功气泡"}
    toast = read_toast(page) or ""
    if re.search(r"发过|already", toast, re.I):
        return {"ok": True, "note": "已存在（重复，非本次新发）"}
    return {"ok": True, "note": "新发"}


def maintain_dwell(page, entry):
    """executor.js:320：帖页停留 20-45s（停留=最强被动信号）+ 40% 概率顺访主页 15-30s"""
    try:
        dwell1 = 20000 + int(random.random() * 25000)
        page.evaluate("() => window.scrollBy({top: 200 + Math.floor(Math.random()*300), behavior: 'smooth'})")
        sleep(dwell1 * 0.5)
        page.evaluate("() => window.scrollBy({top: -150, behavior: 'smooth'})")
        sleep(dwell1 * 0.5)
        if random.random() < 0.4 and entry.get("target_handle"):
            page.evaluate("h => { location.href = 'https://x.com/' + h; }", entry["target_handle"])
            sleep(1500 + random.random() * 3000)
            page.evaluate("() => window.scrollBy({top: 300 + Math.floor(Math.random()*400), behavior: 'smooth'})")
            sleep(15000 + random.random() * 15000)
            goto_feed(page, entry["post_url"])
            sleep(1500)
    except Exception:
        pass  # 停留失败不阻断
