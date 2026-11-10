# datastation.py — 信息源：腾讯云海外数据站接入（2026-10-29 运营者提供）
# 用途：①认知层 post-original 场景的 {source_material} 上游（替代时间线现采权宜方案）
#       ②主脑工具：运营者 QQ 里直接问「今天有什么新闻」
# API：https://data.haiwai.ltd/v1/items / v1/sources
# 内容类型：周期报告（每日栏目）/ 精品文章（深度分析）/ 资讯（滚动新闻）/ 大V文
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import log_event  # noqa: E402

import yaml

BASE = "https://data.haiwai.ltd"
KEY = None   # 惰性从 instance.yaml 读取


def _key() -> str:
    global KEY
    if KEY is None:
        cfg = yaml.safe_load(Path("/etc/xar/instance.yaml").read_text(encoding="utf-8"))
        KEY = cfg.get("datastation", {}).get("api_key", "")
    return KEY


def _get(path: str, params: dict = None, timeout: int = 15) -> dict:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_key()}",
        "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"_err": str(e)[:80]}


def _normalize_items(items, text_limit):
    out = []
    for it in items:
        title = (it.get("title") or it.get("title_norm") or "")[:80]
        raw_text = str(it.get("text") or "")
        iid = (it.get("id") or it.get("item_id")
               or f"{it.get('source','')}|{it.get('time','')}|{title}")
        out.append({"id": str(iid),
                    "title": title,
                    "text": raw_text[:text_limit],
                    "text_length": len(raw_text),
                    "text_truncated": len(raw_text) > text_limit,
                    "source": it.get("source") or "",
                    "source_id": it.get("source_id") or "",
                    "form": it.get("form") or "",
                    "time": it.get("time") or "",
                    "markets": it.get("markets") or [],
                    "cluster_id": it.get("cluster_id") or "",
                    "event_type": it.get("event_type") or "",
                    "tickers": it.get("tickers") or [],
                    "url": it.get("canonical_url") or it.get("url") or ""})
    return out


def fetch_material(forms=("精品文章", "周期报告"), limit=8, hours=6,
                   text_limit=400) -> list:
    """素材采集：按指定内容类型取近 N 小时条目，按时效倒序。"""
    since = datetime.fromtimestamp(time.time() - hours * 3600).strftime("%Y-%m-%d %H:%M")
    d = _get("/v1/items", {"limit": limit, "forms": ",".join(forms), "since": since})
    if d.get("_err"):
        log_event("datastation", "system", action="fetchFail", note=d["_err"])
        return []
    items = d.get("items") or []
    log_event("datastation", "system", action="fetch", n=len(items))
    return _normalize_items(items, text_limit)


class IncrementalTruncated(RuntimeError):
    """增量翻页达上限。rows = 已读到的最新段（新→旧），更旧的段落未读。
    调用方可截断推进（丢弃旧段、记审计），避免生产线被积压永久锁死。"""

    def __init__(self, form, max_pages, rows):
        super().__init__(f"{form} 增量翻页超过 {max_pages} 页，未完整读取")
        self.rows = rows


def fetch_incremental(form, since="", limit=50, text_limit=1400, max_pages=8):
    """Source 增量读取：since=上次最大素材时间；next_cursor 用于向旧页完整翻页。
    持续运行不能保存 next_cursor 当未来游标——它只表示本批的旧页位置。
    任何一页失败抛错；翻页不完整抛 IncrementalTruncated（携带已读段）。"""
    params = {"forms": form, "limit": limit}
    if since:
        params["since"] = since
    out, pages = [], 0
    cursor = ""
    while pages < max_pages:
        if cursor:
            params["cursor"] = cursor
        d = _get("/v1/items", params, timeout=25)
        if d.get("_err"):
            raise RuntimeError(f"{form} 增量读取失败:{d['_err']}")
        page = d.get("items") or []
        out.extend(_normalize_items(page, text_limit))
        pages += 1
        nxt = d.get("next_cursor") or ""
        if not page or not nxt or nxt == cursor:
            break
        cursor = nxt
    else:
        # 截断：返回已读的最新段；旧段未读，由调用方决定截断推进或拒绝
        dedup_partial = {str(it["id"]): it for it in out}
        raise IncrementalTruncated(form, max_pages, list(dedup_partial.values()))
    # 跨页/同批幂等去重
    dedup = {str(it["id"]): it for it in out}
    rows = list(dedup.values())
    log_event("datastation", "system", action="fetchIncremental",
              form=form, n=len(rows), pages=pages, since=since)
    return rows


class MaterialDefect(RuntimeError):
    """确定性素材缺陷（原条目消失/全文超长拿不全）：重试永远同结果，
    调用方应跳过该条继续，而不是当瞬态错误无限重试堵死整批。"""


def fetch_full_item(item, max_chars=20000):
    """选中素材的全文恢复：短条目直接用；截断条目按 source_id+since 重取。
    找不到或仍截断则抛错——主脑不得在残缺证据上立论。
    API 瞬态失败抛 RuntimeError（可重试）；确定性缺陷抛 MaterialDefect（应跳过）。"""
    if item.get("text") and not item.get("text_truncated"):
        return dict(item, source_truncated=False)
    params = {"limit": 200}
    if item.get("form"):
        params["forms"] = item["form"]
    if item.get("source_id"):
        params["sources"] = item["source_id"]
    if item.get("time"):
        try:
            dt = datetime.strptime(str(item["time"])[:16], "%Y-%m-%d %H:%M")
            params["since"] = (dt - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    if not params.get("sources") and item.get("title"):
        params["q"] = item["title"][:40]
    d = _get("/v1/items", params, timeout=30)
    if d.get("_err"):
        raise RuntimeError(f"全文重取失败:{d['_err']}")
    rows = _normalize_items(d.get("items") or [], max_chars)
    full = next((it for it in rows if str(it.get("id")) == str(item.get("id"))), None)
    if not full:
        raise MaterialDefect("全文重取未找到原条目")
    if full.get("text_truncated"):
        raise MaterialDefect(f"全文仍超过 {max_chars} 字，需分段读取，禁止假装完整")
    full["source_truncated"] = False
    full["full_text_refetched"] = True
    log_event("datastation", "system", action="fetchFull",
              item_id=item.get("id"), chars=len(full.get("text") or ""))
    return full


def format_material(items: list) -> str:
    """把素材列表格式化成 post-original 提示词的 {source_material} 槽位文本"""
    if not items:
        return ""
    out = []
    for i, it in enumerate(items, 1):
        mkt = "/".join(it.get("markets", [])[:2]) or "综合"
        head = it.get("title") or it.get("text", "")[:40]
        out.append(f"【素材{i}】[{it['form']}·{it['source']}·{mkt}] {head}\n{it['text']}")
    return "\n\n".join(out)


def latest_news_summary(limit=5) -> str:
    """主脑工具：给运营者看今日要闻摘要"""
    items = fetch_material(forms=("资讯", "精品文章"), limit=limit, hours=12)
    if not items:
        return "数据站暂无新内容或连接失败"
    lines = []
    for it in items:
        lines.append(f"· [{it['source']}] {it['title'] or it['text'][:50]}")
    return "\n".join(lines)


def list_sources(kind: str = None, enabled_only: bool = False) -> dict:
    """主脑工具：源目录（GET /v1/sources，客户端筛选 kind/启用态）。
    返回 {total, returned, sources:[{id,title,kind,markets,enabled}]}"""
    d = _get("/v1/sources", {"limit": 200}, timeout=20)
    if d.get("_err"):
        log_event("datastation", "system", action="sourcesFail", note=d["_err"])
        return {"error": d["_err"]}
    src = d.get("sources") or []
    if kind:
        src = [s for s in src if s.get("kind") == kind]
    if enabled_only:
        src = [s for s in src if s.get("enabled")]
    rows = [{"id": s.get("id"), "title": s.get("title"), "kind": s.get("kind"),
             "markets": "/".join((s.get("markets") or [])[:2]),
             "enabled": bool(s.get("enabled"))} for s in src]
    log_event("datastation", "system", action="sources", n=len(rows))
    return {"total": d.get("total"), "returned": len(rows), "sources": rows}


def api_get(path: str, params: dict = None, timeout: int = 25) -> dict:
    """主脑通用查询代理：数据站任意 GET 端点自服务（专项工具没覆盖的走这里）。
    安全：路径白名单 ^/v1/[A-Za-z0-9/_-.]*$（仅本站、仅 GET）；key 服务端注入，不进 LLM 上下文"""
    import re as _re
    if not _re.match(r"^/v1/[A-Za-z0-9/_\-.]*$", str(path or "")):
        return {"error": f"路径不合规（仅 /v1/ 字母数字路径）: {path!r}"}
    d = _get(path, params or None, timeout=timeout)
    if d.get("_err"):
        log_event("datastation", "system", action="apiGetFail", path=path, note=d["_err"])
    else:
        log_event("datastation", "system", action="apiGet", path=path)
    return d


if __name__ == "__main__":
    for it in fetch_material(limit=5):
        print(f"[{it['form']}] {it['title'][:40]} | {it['source']} | {len(it['text'])}字")
