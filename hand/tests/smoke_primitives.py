# smoke_primitives.py — P1-2 只读冒烟：登录校验/goto_feed/collect_posts(含 has_media)/read_toast
# 只读不写，不碰任何写端点。运行: /opt/xar/venv/bin/python tests/smoke_primitives.py
import sys
import json
import re

sys.path.insert(0, "/opt/xar/hand")
import primitives as P
from camoufox.sync_api import Camoufox

ck = json.load(open("/etc/xar/cookies.json"))
ya = open("/etc/xar/instance.yaml").read()


def y(k):
    m = re.search(k + r':\s*"?([^"\n]+)"?', ya)
    return m.group(1).strip()


PROXY = {"server": f"{y('scheme')}://{y('host')}:{y('port')}",
         "username": y("username"), "password": y("password")}
COOKIES = [{"name": n, "value": v, "domain": ".x.com", "path": "/", "secure": True}
           for n, v in ck.items()]

with Camoufox(headless="virtual", os="windows", proxy=PROXY, geoip=True) as browser:
    page = browser.new_page()
    ctx = page.context
    ctx.add_cookies(COOKIES)
    print("注入 cookies:", sorted(ck.keys()))

    ok2 = P.goto_feed(page, "https://x.com/home")
    print("goto_feed(home):", "PASS" if ok2 else "FAIL", "| url:", page.url[:60])
    nav = page.evaluate("() => !!document.querySelector('a[data-testid=\"AppTabBar_Profile_Link\"]')")
    print("登录态元素 AppTabBar:", nav)

    ok = P.verify_login(page, ctx, "WCixot")
    print("verify_login:", "PASS" if ok else "FAIL", "| url now:", page.url[:60])
    print("cookies 现存:", sorted(c["name"] for c in ctx.cookies(["https://x.com"])))

    P.sleep(3000)  # 等内容水合（骨架卡片先出现）
    P.human_scroll(page, 600)
    P.sleep(1500)
    probe = page.evaluate("""() => ({
        arts: document.querySelectorAll('article[data-testid=\\"tweet\\"]').length,
        uname: document.querySelectorAll('[data-testid=\\"User-Name\\"]').length,
        ttext: document.querySelectorAll('[data-testid=\\"tweetText\\"]').length })""")
    print("选择器探针:", probe)
    posts = P.collect_posts(page, 10)
    print(f"collect_posts: {len(posts)} 条")
    for p in posts[:3]:
        print(f"  - @{(p['author'] or '?')[:15]} media={p['has_media']} locked={p['locked']} | {(p['text'] or '')[:40]}")
    media_n = sum(1 for p in posts if p["has_media"])
    print(f"has_media 字段: {media_n}/{len(posts)} 带媒体")

    print("read_toast:", P.read_toast(page))
    print("SMOKE OK" if ok and ok2 and len(posts) >= 5 else "SMOKE INCOMPLETE")
