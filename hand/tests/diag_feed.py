# diag_feed.py — browse feed_load_failed 现场诊断（独立实例，非 resident 内）
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
import primitives as P  # noqa: E402
import yaml  # noqa: E402
from camoufox.sync_api import Camoufox  # noqa: E402

cfg = yaml.safe_load(open("/etc/xar/instance.yaml"))
p = cfg["proxy"]
PROXY = {"server": f"{p['scheme']}://{p['host']}:{p['port']}",
         "username": p["username"], "password": p["password"]}
ck = json.load(open("/etc/xar/cookies.json"))
COOKIES = [{"name": n, "value": v, "domain": ".x.com", "path": "/", "secure": True}
           for n, v in ck.items()]

with Camoufox(headless="virtual", os="windows", proxy=PROXY, geoip=True) as browser:
    page = browser.new_page()
    ctx = page.context
    ctx.add_cookies(COOKIES)
    ok = P.goto_feed(page, "https://x.com/home")
    st = page.evaluate("""() => ({
        url: location.pathname, title: (document.title||'').slice(0,60),
        arts: document.querySelectorAll('article[data-testid="tweet"]').length,
        loginBtn: !!document.querySelector('[data-testid="loginButton"]'),
        appTab: !!document.querySelector('a[data-testid="AppTabBar_Profile_Link"]'),
        bodyHead: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,150)
    })""")
    print("goto:", ok)
    print(json.dumps(st, ensure_ascii=False, indent=1))
