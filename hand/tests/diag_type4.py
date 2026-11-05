# diag_type4.py — 终验：生产通道 type_text v3（exec→双份修正→校验）真实通过
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
import primitives as P  # noqa: E402
from primitives import SEL, box_len, sleep  # noqa: E402
from resident import Resident  # noqa: E402

TEXT = ("美国从沙特撤人上周已定→特朗普周日称今天不打，但撤离令未收回，表态与安全安排并不同步。"
        "对美股暂按无直接传导处理，先看原油与避险资产是否联动，不凭单一消息推断局势方向。"
        "信息仍有限，维持观察：看撤离令是否调整，以及后续表态与实际行动是否一致。")

DUMP_JS = """() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const t = el.textContent || '';
  return { len: t.length, head: Array.from(t.slice(0, 8)).join(''),
           tail: Array.from(t.slice(-8)).join('') };
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
        for attempt in range(3):   # compose 弹窗冷启动时开时不开，三轮重试
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
        ok = P.type_text(page, TEXT)   # ← 生产函数本体
        sleep(500)
        print("type_text 返回:", ok, "| 框态:", json.dumps(page.evaluate(DUMP_JS), ensure_ascii=False))
        print("按钮态:", page.evaluate(
            "() => {const d=document.querySelector('[role=dialog]');"
            "const bs=[...(d?d.querySelectorAll('[data-testid=tweetButton]'):[])];"
            "const b=bs[bs.length-1];return b? (b.disabled||b.getAttribute('aria-disabled')==='true'?'disabled':'ENABLED'):'none'}"))
        # 清场不发送
        P.sleep(300)
        page.keyboard.press("Control+a")
        page.keyboard.press("Delete")
        sleep(300)
        page.keyboard.press("Escape")
        return 0 if ok else 3
    finally:
        try:
            page.close()
        except Exception:
            pass
        r.close_workstation()


if __name__ == "__main__":
    sys.exit(main())
