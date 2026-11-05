# diag_multiline.py — X compose 多行排版取证；绝不点击发布、不走 /execute。
import json
import sys

sys.path.insert(0, "/opt/xar/hand")
from primitives import SEL, _text_matches, sleep  # noqa: E402
from resident import Resident  # noqa: E402

CASES = [
    {"name": "双段", "text": "第一段事实。\n\n第二段判断。"},
    {"name": "三段+列表", "text": "核心数据：\n- 第一项。\n- 第二项。\n\n继续观察。"},
    {"name": "单段回归", "text": "这是一段不需要额外排版的普通正文。"},
]

DUMP_JS = """() => {
  const sel = '[data-testid="tweetTextarea_0"]';
  const el = document.querySelector('[role="dialog"] ' + sel) || document.querySelector(sel);
  if (!el) return null;
  const blocks = [...el.querySelectorAll('[data-block="true"]')].map(b => b.textContent || '');
  return {blocks, innerText: el.innerText || '', textContent: el.textContent || ''};
}"""


def open_compose(r):
    for _ in range(3):
        page = r.ctx.new_page()
        try:
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(SEL["reply_box"], timeout=12000)
            sleep(600)
            return page
        except Exception:
            page.close()
            sleep(2000)
    raise RuntimeError("compose 未打开")


def main():
    r = Resident()
    if not r.claim():
        print("claim 失败"); return 1
    r.open_workstation()
    results = []
    try:
        # 每个用例独立新页面，不依赖 Draft.js 的全选/清空行为；关页即丢弃草稿。
        for c in CASES:
            page = open_compose(r)
            try:
                page.keyboard.insert_text(c["text"])
                sleep(1000)
                dump = page.evaluate(DUMP_JS)
                exact = _text_matches(page, c["text"], "any")
                flattened_negative = (_text_matches(page, c["text"].replace("\n\n", " "), "any")
                                      if "\n\n" in c["text"] else None)
                row = {"name": c["name"], "expected": c["text"], "dump": dump,
                       "exact": exact, "flattened_negative_match": flattened_negative}
                results.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                if not exact or flattened_negative is True:
                    raise AssertionError(c["name"] + " 排版校验失败")
            finally:
                page.close()   # 未提交草稿随页面销毁
        with open("/tmp/multiline-diag.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        print("MULTILINE-DIAG-PASS（未发布）", flush=True)
        return 0
    finally:
        r.close_workstation()


if __name__ == "__main__":
    sys.exit(main())
