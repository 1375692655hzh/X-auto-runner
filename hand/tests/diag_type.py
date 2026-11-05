# diag_type.py — 输入失败取证（2026-10-27 cog-p-20261027-09 failed: 输入失败）
# 用生产同款 type_text 在 compose 弹窗输入当时的确切文案，逐步 dump 框内实际内容（字符级）。
# 用法: 停 xar-resident 后独立运行，跑完重启服务。
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, TYPE_JS, _FOCUS_JS, _CLICK_FOCUS_JS, _text_matches, box_len, sleep  # noqa: E402
from resident import Resident  # noqa: E402

TEXT = ("美国从沙特撤人上周已定→特朗普周日称今天不打，但撤离令未收回，表态与安全安排并不同步。"
        "对美股暂按无直接传导处理，先看原油与避险资产是否联动，不凭单一消息推断局势方向。"
        "信息仍有限，维持观察：看撤离令是否调整，以及后续表态与实际行动是否一致。")

DUMP_JS = """() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const t = el.textContent || '';
  return { len: t.length,
           head: Array.from(t.slice(0, 12)).map(c => c + '(U+' + c.codePointAt(0).toString(16).padStart(4,'0') + ')').join(' '),
           tail: Array.from(t.slice(-12)).map(c => c + '(U+' + c.codePointAt(0).toString(16).padStart(4,'0') + ')').join(' '),
           match_expect: t.replace(/\\s+/g,' ').trim() === %s };
}""" % json.dumps(TEXT.replace("\n", " ").strip(), ensure_ascii=False)


def main():
    r = Resident()
    if not r.claim():
        print("claim 失败（常驻在跑？先 systemctl stop xar-resident）")
        return 1
    r.open_workstation()
    page = r.ctx.new_page()
    try:
        # 热身：先开 home 等 feed（冷启动直接进 compose 弹窗不稳，2026-10-27 取证扑空教训）
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        for _ in range(15):
            sleep(3000)
            if page.evaluate("() => document.querySelectorAll('article').length") > 0:
                break
        print("热身 feed arts:", page.evaluate("() => document.querySelectorAll('article').length"))
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
        for _ in range(15):
            try:
                page.wait_for_selector(SEL["reply_box"], timeout=3000)
                break
            except Exception:
                sleep(2000)
        sleep(1500)
        st = page.evaluate(DUMP_JS)
        print("初始框态:", json.dumps(st, ensure_ascii=False))
        if st is None:
            print("框缺失现场:", json.dumps(page.evaluate(
                "() => ({url: location.pathname, arts: document.querySelectorAll('article').length,"
                " login: !!document.querySelector('[data-testid=loginButton]'),"
                " dlg: !!document.querySelector('[role=dialog]'),"
                " head: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,100)})"), ensure_ascii=False))
            return 2
        page.evaluate(TYPE_JS, [TEXT, "any"])
        sleep(700)
        print("粘贴后:", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False))
        for attempt, focus_js, settle in ((1, _FOCUS_JS, 400), (2, _CLICK_FOCUS_JS, 800)):
            page.evaluate(focus_js, "any")
            sleep(settle)
            if box_len(page, "any") > 0:
                page.keyboard.press("Control+a")
                page.keyboard.press("Delete")
                sleep(300)
            bl_before = box_len(page, "any")
            page.keyboard.type(TEXT, delay=60)
            sleep(700)
            print(f"键盘轮{attempt}(聚焦后框长{bl_before}):", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False))
            print(f"  _text_matches ==", _text_matches(page, TEXT, "any"))
        # 清场
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        sleep(300)
        page.keyboard.press("Escape")
        return 0
    finally:
        try:
            page.close()
        except Exception:
            pass
        r.close_workstation()


if __name__ == "__main__":
    sys.exit(main())
