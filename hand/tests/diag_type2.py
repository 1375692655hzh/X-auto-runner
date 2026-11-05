# diag_type2.py — 输入通道对比取证：execCommand insertText vs 键盘（首字甩尾问题）
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, box_len, _text_matches, sleep  # noqa: E402
from resident import Resident  # noqa: E402

TEXT = ("美国从沙特撤人上周已定→特朗普周日称今天不打，但撤离令未收回，表态与安全安排并不同步。"
        "对美股暂按无直接传导处理，先看原油与避险资产是否联动，不凭单一消息推断局势方向。"
        "信息仍有限，维持观察：看撤离令是否调整，以及后续表态与实际行动是否一致。")

FOCUS_JS = """
(mode) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return false; el.focus(); return true;
}
"""
EXEC_JS = """
(text) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return 'nobox';
  el.focus();
  return document.execCommand('insertText', false, text) ? 'ok' : 'cmd-false';
}
"""
DUMP_JS = """() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const t = el.textContent || '';
  return { len: t.length, head: Array.from(t.slice(0, 6)).join(''), tail: Array.from(t.slice(-6)).join('') };
}"""


def clear(page):
    page.evaluate(FOCUS_JS, "any")
    if box_len(page, "any") > 0:
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        sleep(400)


def main():
    r = Resident()
    if not r.claim():
        print("claim 失败"); return 1
    r.open_workstation()
    page = r.ctx.new_page()
    try:
        page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=60000)
        for _ in range(15):
            sleep(3000)
            if page.evaluate("() => document.querySelectorAll('article').length") > 0:
                break
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
        for _ in range(15):
            try:
                page.wait_for_selector(SEL["reply_box"], timeout=3000)
                break
            except Exception:
                sleep(2000)
        sleep(1500)
        if page.evaluate(DUMP_JS) is None:
            print("弹窗未开，放弃"); return 2
        # A. execCommand insertText 整段
        clear(page)
        rc = page.evaluate(EXEC_JS, TEXT)
        sleep(800)
        d = page.evaluate(DUMP_JS)
        print("A execCommand:", rc, "|", json.dumps(d, ensure_ascii=False),
              "| match:", _text_matches(page, TEXT, "any"))
        # B. execCommand 分块（拟人化余地：3-8 字块）
        clear(page)
        i, ok = 0, True
        while i < len(TEXT):
            chunk = TEXT[i:i + 6]
            if page.evaluate(EXEC_JS, chunk) != "ok":
                ok = False
                break
            sleep(120)
            i += 6
        sleep(600)
        print("B execCommand分块:", "ok" if ok else "中断", "|", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False),
              "| match:", _text_matches(page, TEXT, "any"))
        # C. 键盘 priming 试验：先敲一个空格吸收首击，清空，再整段键盘输入
        clear(page)
        page.evaluate(FOCUS_JS, "any")
        page.keyboard.type(" ", delay=30)
        sleep(500)
        clear(page)
        page.keyboard.type(TEXT, delay=60)
        sleep(700)
        print("C 键盘(空格priming后):", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False),
              "| match:", _text_matches(page, TEXT, "any"))
        # 清场
        clear(page)
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
