#!/usr/bin/env python3
# tickers_golden.py — 标的判定器金标回归库（真实 LLM，LIVE_LLM=1 才跑）
# 制度（2026-11-10 换链事故后确立）：换模型/换供应商/改 CHAIN_PROMPT 前，
# 必须在 VPS 上 `LIVE_LLM=1 python3 tickers_golden.py` 全过才放行。
# direct 断言由代码扫描器构造保证（与模型无关，100%）；chain 断言用
# 必含(must)/允许池(allow)/必不含(absent) 三档，覆盖事件定性与因果方向。
import os
import sys
from pathlib import Path

if os.environ.get("LIVE_LLM") != "1":
    print("SKIP: 真实 LLM 金标库未跑（需 LIVE_LLM=1，换模型/改 prompt 前必跑）")
    sys.exit(0)

sys.path.insert(0, str(Path(__file__).parents[1]))
from llm import LLM          # noqa: E402
from tickers import related_tickers, format_line  # noqa: E402

# 每案：素材 + direct_must（扫描必中）/ direct_absent（不得出现）
#       + chain_allow（chain 只准出自此池）/ chain_must（至少含其一）/ chain_absent
CASES = [
    {   # 回归①：v3 曾漏 $TSM 本尊（GLM 时代实锤）
        "name": "台积电营收案",
        "item": {"title": "台积电10月营收创纪录，AI芯片需求持续强劲",
                 "text": "台积电公布10月营收，同比增长超预期，主要受AI芯片订单推动。"
                         "苹果、英伟达为其主要客户。先进制程产能利用率维持高位，"
                         "半导体设备商订单能见度延长。"},
        "direct_must": ["TSM", "AAPL", "NVDA", "ASML", "AMAT", "LRCX", "KLAC"],  # 素材含"半导体设备商"统称→确定性映射
        "direct_absent": ["WDC"],
        "chain_allow": {"AVGO", "AMD", "TSM", "AAPL", "NVDA", "MU", "VST", "MRVL", "QCOM", "TER"},
        "chain_must": [],
        "chain_absent": [],
    },
    {   # 回归①：v3 曾漏 $MU、错抓素材没提的 $WDC 进 direct
        "name": "存储涨价案",
        "item": {"title": "存储芯片合约价Q4环比上涨，三大原厂减产见效",
                 "text": "DRAM与NAND合约价连续两个季度上涨，三星、海力士、美光三大原厂"
                         "此前宣布减产。PC与服务器需求回暖，模组厂开始回补库存。"},
        "direct_must": ["MU"],
        "direct_absent": ["WDC", "STX"],
        "chain_allow": {"WDC", "STX", "MU", "AMAT", "LRCX", "KLAC", "ASML", "NVDA", "SMCI",
                        "DELL", "INTC"},
        "chain_must": [],
        "chain_absent": [],
    },
    {   # 对照方→direct 的规则执行（v3 已验证，防回归）
        "name": "CDS vs 云厂商案",
        "item": {"title": "芯片商CDS与云厂商达成新一代AI算力协议",
                 "text": "定制化芯片商CDS宣布与多家超大云厂商达成合作，为其数据中心定制"
                         "推理芯片，直接对标英伟达GPU方案。微软、谷歌、亚马逊均在推进"
                         "自研芯片以降低算力成本。"},
        "direct_must": ["MSFT", "GOOGL", "AMZN", "NVDA", "META"],
        "direct_absent": [],
        "chain_allow": {"AMD", "AVGO", "TSM", "MRVL", "ARM", "ASML", "AMAT"},
        "chain_must": [],
        "chain_absent": [],
    },
    {   # 负例：无任何白名单公司 → 空输出不硬凑
        "name": "无关联负例",
        "item": {"title": "某地出台数据中心用电新规",
                 "text": "某地能源部门发布数据中心能耗管理征求意见稿，要求新建项目提交"
                         "能耗评估报告，公众可于下月提交意见。"},
        "direct_must": [],
        "direct_absent": [],
        "chain_allow": set(),   # 允许为空：chain 空是正确答案
        "chain_must": [],
        "chain_absent": ["NVDA", "TSM", "AAPL"],
    },
]


def main():
    llm = LLM()
    failed = 0
    for c in CASES:
        pairs = related_tickers(llm, c["item"])
        direct = {t for t, k in pairs if k == "direct"}
        chain = {t for t, k in pairs if k == "chain"}
        errs = []
        for t in c["direct_must"]:
            if t not in direct:
                errs.append(f"direct 漏 {t}")
        for t in c["direct_absent"]:
            if t in direct:
                errs.append(f"direct 多 {t}")
        bad = chain - c["chain_allow"] if c["chain_allow"] is not None else chain
        if bad:
            errs.append(f"chain 越池 {sorted(bad)}")
        for t in c["chain_must"]:
            if t not in chain:
                errs.append(f"chain 缺必含 {t}")
        for t in c["chain_absent"]:
            if t in chain:
                errs.append(f"chain 禁项出现 {t}")
        line = format_line(pairs)
        if errs:
            failed += 1
            print(f"[FAIL] {c['name']}：{'；'.join(errs)}")
            print(f"       输出：{line or '(空)'}")
        else:
            print(f"[PASS] {c['name']}：{line or '(空)'}")
    if failed:
        print(f"金标库 {failed}/{len(CASES)} 案失败 ❌ —— 按制度不得放行换模型/改prompt")
        sys.exit(1)
    print(f"金标库 {len(CASES)}/{len(CASES)} 全过 ✅")


if __name__ == "__main__":
    main()
