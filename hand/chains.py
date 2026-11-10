# chains.py — 多链内容生产框架·链注册表（2026-11-10 运营立项）
# 一条链 = 一个 auto 内容类目。首条链：deep/深度文（原资讯原创生产线整体降格为链）。
# 运营后续将逐条推进新类目（短评/盘前/周末盘点等）——本模块只做骨架：
# 注册、校验、链级约束隔离；各链的生产逻辑在其引擎模块（engine）里。
#
# 链级约束隔离（2026-11-10 运营裁决"长度要求按链各自定义、不互相传染"的框架化）：
#   budget_24h   链内日上限：引擎开票前自查（发稿意图计数）；全局 kind 帽仍在 Gateway 兜底
#   length       长度策略 {mode: none|max, max_len}：深度文不设（裁决原文），后续链自带
#   ticker_line  帖尾标的行开关（tickers.py 判定器）
#   growth_tags  互关tag {enabled, prob}
#
# origin 约定（与额度/排队联动，见 server.py / ledger.py）：
#   "manual"     运营者增量任务——豁免额度 + 排队绝对优先(-1级)
#   "chain:<id>" 该链 auto 产出——正常计额度、正常排队（按 kind 四级）
#   缺省         主脑散帖/其他来源
import re
from pathlib import Path

try:
    import yaml
except ImportError:      # 纯展示场景（qq 前缀）无 yaml 也能跑
    yaml = None

# 内置缺省链（instance.yaml 缺 chains 段时的兜底；生产配置应显式声明）
DEFAULTS = {
    "deep": {
        "display": "深度文",
        "engine": "news_auto",
        "enabled": True,
        "budget_24h": 10,                    # 链内日上限（对齐开跑时的 post 全局帽）
        "length": {"mode": "none"},          # 深度文不设长度硬约束（2026-11-10 裁决）
        "ticker_line": True,
        "growth_tags": {"enabled": True, "prob": 1 / 3},
    },
}

_ENGINES = ("news_auto",)   # 可用生产引擎模块；新链接入时在此注册

_CFG_PATHS = [Path("/etc/xar/instance.yaml")]


class ChainError(RuntimeError):
    pass


def _validate(cid, c):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", cid):
        raise ChainError(f"链 id 非法: {cid}（小写字母数字下划线）")
    if c.get("engine") not in _ENGINES:
        raise ChainError(f"链 {cid} 引擎未注册: {c.get('engine')}（可用: {_ENGINES}）")
    b = c.get("budget_24h")
    if not isinstance(b, int) or b <= 0:
        raise ChainError(f"链 {cid} budget_24h 须为正整数")
    ln = c.get("length") or {}
    if ln.get("mode") not in ("none", "max"):
        raise ChainError(f"链 {cid} length.mode 须为 none|max")
    if ln.get("mode") == "max" and not isinstance(ln.get("max_len"), int):
        raise ChainError(f"链 {cid} length.max=max 时须给 max_len")
    gt = c.get("growth_tags") or {}
    if gt.get("enabled") and not (0 < float(gt.get("prob") or 0) <= 1):
        raise ChainError(f"链 {cid} growth_tags.prob 须在 (0,1]")
    if not str(c.get("display") or "").strip():
        raise ChainError(f"链 {cid} 缺 display 显示名")
    return True


def load(cfg=None):
    """链注册表：内置缺省 ← instance.yaml chains 段覆盖/新增；逐链校验 fail-closed。"""
    out = {cid: {**c} for cid, c in DEFAULTS.items()}
    if cfg is None and yaml is not None:
        for p in _CFG_PATHS:
            try:
                cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
                break
            except FileNotFoundError:
                cfg = {}
    section = (cfg or {}).get("chains") or {}
    for cid, c in section.items():
        if not isinstance(c, dict):
            raise ChainError(f"链 {cid} 配置须为字典")
        base = out.get(cid, {"display": cid, "engine": "news_auto", "enabled": False,
                             "budget_24h": 5, "length": {"mode": "none"},
                             "ticker_line": False,
                             "growth_tags": {"enabled": False, "prob": 1 / 3}})
        merged = {**base, **{k: v for k, v in c.items() if v is not None}}
        merged["id"] = cid                      # 自带 id 便于 payload 引用
        _validate(cid, merged)
        out[cid] = merged
    for cid in out:
        out[cid]["id"] = cid
    return out


def get(cid):
    c = load().get(cid)
    if c is None:
        raise ChainError(f"未注册的链: {cid}")
    return c


def display(chain_field):
    """payload.chain → 展示名；无链信息返回 ''（主脑散帖/人工票不前缀）。"""
    if not chain_field:
        return ""
    try:
        return get(chain_field).get("display") or ""
    except (ChainError, Exception):
        return str(chain_field)
