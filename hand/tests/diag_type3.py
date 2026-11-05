# diag_type3.py — execCommand 框内 238 字符之谜：剥离期望文本看残渣
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, box_len, sleep  # noqa: E402
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
EXEC_JS = """
(text) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return 'nobox';
  el.focus();
  return document.execCommand('insertText', false, text) ? 'ok' : 'cmd-false';
}
"""
RESIDUE_JS = """
(expected) => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const t = el.textContent || '';
  const parts = t.split(expected);
  const residue = parts.join('|D|');
  const cp = (s) => Array.from(s).map(c => c.codePointAt(0)).join(',');
  return { len: t.length, occ: parts.length - 1,
           partsLens: parts.map(p => p.length),
           residueHead: residue.slice(0, 80),
           residueCp: cp(residue.replace('|D|','')).slice(0, 200) };
}
"""


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
        if not page.evaluate(FOCUS_JS):
            print("弹窗未开"); return 2
        if box_len(page, "any") > 0:
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            sleep(400)
        rc = page.evaluate(EXEC_JS, TEXT)
        sleep(1000)
        print("execCommand:", rc)
        print(json.dumps(page.evaluate(RESIDUE_JS, TEXT), ensure_ascii=False))
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
