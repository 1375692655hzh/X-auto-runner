# diag_compose.py — 发帖按钮禁用取证（2026-10-26 首发演练 failed: 发布按钮不可点）
# 只输入不发送：复刻 post_tweet 前半段，量测文字是否真的落框 + 按钮态 + 计数器。
# 用法: 停 xar-resident 后独立运行（同 profile 独占），跑完重启服务。
import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, TYPE_JS, sleep  # noqa: E402
from resident import Resident  # noqa: E402

TEXT = ("重读了 Transformer 原论文的 3.2 节。位置编码这十年从固定正弦走到 RoPE 再到 NoPE，"
        "争的其实是同一件事：模型该不该对 token 顺序有先验。现在看，该保留的不是先验本身，"
        "而是可插拔的归纳偏置。")

STATE_JS = """() => {
  const dlg = document.querySelector('[role="dialog"]');
  const box = (dlg && dlg.querySelector('[data-testid="tweetTextarea_0"]'))
           || document.querySelector('[data-testid="tweetTextarea_0"]');
  const pick = (d) => { if (!d) return null;
    const bs = [...d.querySelectorAll('[data-testid="tweetButton"]')];
    return bs[bs.length - 1] || null; };
  const b = pick(dlg) || document.querySelector('[data-testid="tweetButtonInline"]');
  return {
    url: location.pathname,
    dlg: !!dlg,
    boxInDlg: !!(box && dlg && dlg.contains(box)),
    boxLen: box && box.textContent ? box.textContent.length : 0,
    boxHead: box && box.textContent ? box.textContent.slice(0, 24) : null,
    btnTid: b ? b.getAttribute('data-testid') : null,
    btnDisabled: b ? !!(b.disabled || b.getAttribute('aria-disabled') === 'true') : null,
    counter: (document.querySelector('[data-testid="tweetLength"]') || {}).textContent
             || (dlg && dlg.querySelector('[data-testid="tweetLength"]')
                 ? dlg.querySelector('[data-testid="tweetLength"]').textContent : null),
    dlgText: dlg ? (dlg.innerText || '').replace(/\\s+/g, ' ').slice(0, 160) : null,
    draftHint: dlg ? (dlg.innerText || '').includes('草稿') : null,
  };
}"""


def main():
    r = Resident()
    if not r.claim():
        print("claim 失败（常驻在跑？先 systemctl stop xar-resident）")
        return 1
    r.open_workstation()
    assert r.ctx, "工位未开"
    page = r.ctx.new_page()
    try:
        page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector(SEL["reply_box"], timeout=20000)
        except Exception:
            pass
        sleep(2500)
        s0 = page.evaluate(STATE_JS)
        print("输入前:", json.dumps(s0, ensure_ascii=False))
        ok = page.evaluate(TYPE_JS, [TEXT, "any"])
        sleep(1200)
        s1 = page.evaluate(STATE_JS)
        print("粘贴后 ok=", ok, ":", json.dumps(s1, ensure_ascii=False))
        if s1.get("boxLen", 0) == 0:
            # 兜底尝试：逐字键盘输入（humanize 后备路径验证）
            page.keyboard.type(TEXT[:30], delay=55)
            sleep(600)
            s2 = page.evaluate(STATE_JS)
            print("keyboard.type 30字后:", json.dumps(s2, ensure_ascii=False))
        # 清场：全选删除 + Esc 关弹窗（不发送）
        try:
            page.keyboard.press("Control+a")
            page.keyboard.press("Delete")
            sleep(300)
            page.keyboard.press("Escape")
        except Exception:
            pass
        return 0
    finally:
        try:
            page.close()
        except Exception:
            pass
        r.close_workstation()


if __name__ == "__main__":
    sys.exit(main())
