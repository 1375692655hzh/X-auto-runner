# quality_test.py — 质量门用例（ref quality.js 三缺陷回归 + 移植口径）
# 运行: python hand/tests/quality_test.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from quality import check, clean, similarity, weighted_len  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


@case("空文案拒发")
def _():
    assert check("")["ok"] is False
    assert check("   ")["ok"] is False


@case("清洗后为空拒发（只剩链接/标签）")
def _():
    r = check("https://t.co/abc #AI新闻")
    assert r["ok"] is False and "清洗后为空" in r["reason"], r


@case("中文套话开场拒发（缺陷①回归：具体中文评论不误伤）")
def _():
    for t in ("顶，说得好", "学习了！", "大佬牛"):
        assert check(t)["ok"] is False, t
    # 具体有信息量的中文评论应放行
    r = check("蒸馏到 8B 后显存占用确实降了，但长上下文的 KV cache 还是大头。")
    assert r["ok"] is True, r


@case("英文套话开场拒发")
def _():
    assert check("Great point about the scheduler!")["ok"] is False
    assert check("Well said, this matches my experience.")["ok"] is False


@case("中文 hashtag 清洗（缺陷②回归）")
def _():
    cleaned, _ = clean("推理成本降了一半 #AI推理 #大模型 值得跟进")
    assert "#" not in cleaned and "＃" not in cleaned, cleaned
    assert "推理成本" in cleaned and "值得跟进" in cleaned


@case("全角＃与“回复：”前缀清洗")
def _():
    cleaned, _ = clean("回复：这个批调度的思路有意思＃AI infra")
    assert cleaned.startswith("这个批调度"), cleaned
    assert "＃" not in cleaned


@case("整体引号包裹剥离")
def _():
    cleaned, _ = clean("“批调度才是瓶颈。”")
    assert cleaned == "批调度才是瓶颈。", cleaned


@case("加权长度：全中文按2计")
def _():
    assert weighted_len("一二三") == 6
    assert weighted_len("abc") == 3
    # 140 个汉字 = 280 加权 → 放行边界
    assert weighted_len("汉" * 140) == 280
    assert check("汉" * 141 + "。")["ok"] is False  # 282 超长


@case("过短拒发")
def _():
    assert check("好。")["ok"] is False


@case("emoji ≤2")
def _():
    assert check("批调度优化效果显著🔥🔥。")["ok"] is True
    assert check("批调度优化效果显著🔥🔥🔥。")["ok"] is False


@case("结尾无标点疑似截断拒发（casual 豁免）")
def _():
    assert check("这个批调度的思路确实降低了尾部延迟")["ok"] is False  # 无句号
    assert check("这个批调度的思路确实降低了尾部延迟。")["ok"] is True
    assert check("来了来了", casual=True)["ok"] is True  # 口语应答豁免


@case("casual 仍管长度/emoji/相似度")
def _():
    assert check("好", casual=True)["ok"] is False            # 过短照拒
    assert check("冲🔥🔥🔥🔥", casual=True)["ok"] is False    # emoji 照拒


@case("相似度查重（缺陷③回归：不合格不放行）")
def _():
    hist = ["推理成本降了一半，主要靠批调度优化。"]
    same = check("推理成本降了一半，主要靠批调度优化～", history=hist)
    assert same["ok"] is False and "相似" in same["reason"], same
    diff = check("KV cache 的分页管理才是这代框架真正的大头。", history=hist)
    assert diff["ok"] is True, diff


@case("发送用清洗版（URL/标签剥离后的文本）")
def _():
    r = check("长上下文的显存大头在 KV cache 分页，这个结论我们复测过。 https://example.com/a #LLM")
    assert r["ok"] is True, r
    assert "https" not in r["cleaned"] and "#" not in r["cleaned"], r["cleaned"]
    # 清洗后悬在半截（尾句无标点）按截断拒收——ref 同口径
    r2 = check("长上下文的显存大头在 KV cache 分页，详情 https://example.com/a")
    assert r2["ok"] is False and "截断" in r2["reason"], r2


@case("纯文本排版：双段/三段/列表换行保留")
def _():
    assert clean("甲段。\n\n乙段。")[0] == "甲段。\n\n乙段。"
    assert clean("甲。\n\n乙。\n\n丙。")[0] == "甲。\n\n乙。\n\n丙。"
    assert clean("三点：\n- 一。\n- 二。\n\n总结。")[0] == "三点：\n- 一。\n- 二。\n\n总结。"


@case("纯文本排版：CRLF/CR统一且不吞换行")
def _():
    assert clean("甲。\r\n乙。")[0] == "甲。\n乙。"
    assert clean("甲。\r\n\r\n乙。")[0] == "甲。\n\n乙。"
    assert clean("甲。\r乙。")[0] == "甲。\n乙。"


@case("纯文本排版：空行收敛/行尾空白/幂等")
def _():
    assert clean("甲。\n\n\n\n乙。")[0] == "甲。\n\n乙。"
    assert clean("甲。\n \t\n乙。")[0] == "甲。\n\n乙。"
    assert clean("甲  乙。  \n丙。")[0] == "甲 乙。\n丙。"
    for s in ("甲。\n\n乙。", "甲。\n- 乙\n- 丙。", "甲。\n \t\n\n乙。"):
        once = clean(s)[0]
        assert clean(once)[0] == once


@case("纯文本排版：URL/hashtag/围栏清洗不毁段落")
def _():
    assert clean("第一段。 https://example.com/a #AI\n\n第二段。＃标签")[0] == "第一段。\n\n第二段。"
    assert clean("```text\n第一段。\n\n- 第二项。\n```")[0] == "第一段。\n\n- 第二项。"


@case("纯文本排版：长度与相似度口径不绕过")
def _():
    assert weighted_len("ab\ncd") == 5
    assert check("汉" * 137 + "\n\n乙。")["ok"] is True
    assert check("汉" * 138 + "\n\n乙。")["ok"] is False
    assert check("汉" * 141 + "\n\n乙。", max_len=None)["ok"] is True
    assert similarity("甲段。\n\n乙段。", "甲段。 乙段。") == 1.0
    assert check("甲段。\n\n乙段。", history=["甲段。 乙段。"])["ok"] is False


@case("纯文本排版：普通回复与casual规则不变")
def _():
    assert check("这个判断有依据，但还要看自由现金流。")["cleaned"] == "这个判断有依据，但还要看自由现金流。"
    assert check("来了来了", casual=True)["ok"] is True
    assert check("顶，说得好")["ok"] is False


def main():
    failed = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  ✓ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}: {e}")
    print(f"{len(CASES) - failed}/{len(CASES)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
