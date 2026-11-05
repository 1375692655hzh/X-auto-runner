# diag_type5.py — IME 通道取证：keyboard.insert_text（合成事件，真实中文用户路径）
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, box_len, _text_matches, sleep  # noqa: E402
from resident import Resident  # noqa: E402

TEXT = ("美国从沙特撤人上周已定→特朗普周日称今天不打，但撤离令未收回，表态与安全安排并不同步。"
        "对美股暂按无直接传导处理，先看原油与避险资产是否联动，不凭单一消息推断局势方向。"
        "信息仍有限，维持观察：看撤离令是否调整，以及后续表态与实际行动是否一致。")

FOCUS_JS = """
() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return false; el.focus(); return true;
}
"""
DUMP_JS = """() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const t = el.textContent || '';
  const mid = Math.floor(t.length / 2);
  return { len: t.length, head: Array.from(t.slice(0, 6)).join(''),
           mid: Array.from(t.slice(mid - 3, mid + 3)).join(''),
           tail: Array.from(t.slice(-6)).join('') };
}"""


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
        opened = False
        for attempt in range(3):
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
            for _ in range(15):
                try:
                    page.wait_for_selector(SEL["reply_box"], timeout=3000)
                    opened = True
                    break
                except Exception:
                    sleep(2000)
            if opened:
                break
            print(f"第{attempt + 1}轮弹窗未开，重试")
            sleep(3000)
        sleep(1500)
        if not opened or page.evaluate(DUMP_JS) is None:
            print("三轮弹窗均未开"); return 2

        page.evaluate(FOCUS_JS)
        sleep(400)
        page.keyboard.insert_text(TEXT)   # ← IME/合成事件通道
        sleep(900)
        print("insert_text 整段:", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False),
              "| match:", _text_matches(page, TEXT, "any"))

        # 分段（拟人化余地验证）：清空后 6 字一段 insert
        page.evaluate(FOCUS_JS)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        sleep(400)
        for i in range(0, len(TEXT), 6):
            page.keyboard.insert_text(TEXT[i:i + 6])
            sleep(140)
        sleep(800)
        print("insert_text 分段:", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False),
              "| match:", _text_matches(page, TEXT, "any"))
        print("按钮态:", page.evaluate(
            "() => {const d=document.querySelector('[role=dialog]');"
            "const bs=[...(d?d.querySelectorAll('[data-testid=tweetButton]'):[])];"
            "const b=bs[bs.length-1];return b? (b.disabled||b.getAttribute('aria-disabled')==='true'?'disabled':'ENABLED'):'none'}"))
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
