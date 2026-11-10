# editorial.py — 独立作者制：最小作者 system + 任务 user + 单道底线编辑
# QQ主脑、选题评分、L1/L2大提示词均不进入作者/编辑消息链。
import json
import re
from pathlib import Path

from ledger import log_event


class EditorialSkip(Exception):
    """素材不值得写或二次审核仍失败：合法不发，不是基础设施错误。"""


REVIEW_PROMPT = """你是 @WCixot 的底线编辑，不是第二作者。不要代写，不要把自然稿改造成你偏好的内容形态。

输入只有完整素材、拟发布正文和用于查重的近期正文。检查：
1. fact：主体、数字、时间、单位、来源归因、计划/完成/恢复等状态是否忠于素材；是否补了素材外事实。
2. standalone：脱离素材是否知道谁发生了什么；是否用”不能/还不能证明”等回答正文未建立的问题。**首屏自检（2026-11-10 起）：时间线只展示前约140加权字符（约70汉字），超长稿折叠点之前必须能独立成立——读者只看首屏应知道发生了什么+作者基本态度；纯铺垫式开头按 standalone 打回。**
3. logic：主线是否成立，段落是否服务同一事件；时间先后不得冒充因果，服务可用性不得外推模型能力；连接词不得制造素材没有的关系。**篇幅与增量密度匹配：单一事实/单一数字的单薄增量硬撑长文属注水，按 logic 打回；反之厚重增量压成一句话导致信息丢失，同样按 logic 处理。**
4. repeat：是否与近期内容实质重复而没有新增量。
5. redline：投资建议、虚构经历、自动化暴露、**转述型媒体署名残留**（”据智通财经/财联社快讯等报道/获悉/讯”、聚合渠道媒体名；官方归因”据公告/据财报”和一线通讯社署名”据路透/彭博/美联社/道琼斯/金融时报/华尔街日报”不算硬伤，中文转载搬运一线通讯社时应归因还原到原通讯社）、一般媒体的评论性内容被当作观点源、**标的罗列违规**（正文公司名后顺带 $代码、帖尾"涉及标的/关联标的"行、带机构名与动作的评级目标价转述均不因出现 $代码 本身打回；但 $代码与公司错配、非美股主体挂 $、未上市公司挂码或影子标的视同事实错误；标的与方向性受益词直接绑定——"受益/利好/可关注/布局/埋伏 $X"——一律打回）、**交易指令式表达**（入场价/止损位/仓位/做多做空/抄底上车等一切对读者操作的暗示，引用机构评级与目标价作为事实陈述的除外）、**渲染性情绪词**（"爆仓""血洗""崩盘"式音效词）等。
6. format：仅当输出协议或纯文本排版有硬伤时使用。

相对日期、数字单位和比较口径必须核算。没有观点、反证、未知项、失效条件、观察点，篇幅短，自然分段，先叙后议，均不是拒绝理由。只有具体且一次可修复的问题才 revise；不值得发或无法可靠修复则 drop。

只输出 JSON：{"decision":"accept|revise|drop","issues":[{"kind":"fact|standalone|logic|repeat|redline|format","excerpt":"正文原句（如适用）","problem":"具体问题","fix":"最小修复"}]}
accept 时 issues=[]。revise 必须有具体issues。"""


def _json_from(out):
    s = (out or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise ValueError("编辑输出非JSON")
        return json.loads(m.group(0))


def _author_draft(llm, item, history, operator_instruction="", review_feedback="", current_draft=""):
    """作者消息独立装配：system只做静态身份/底线；所有动态数据只进user。"""
    from quality import check
    from account_status import post_max_len

    system = Path("/etc/xar/prompts/author-system.md").read_text(encoding="utf-8")
    task = Path("/etc/xar/prompts/author-task.md").read_text(encoding="utf-8")
    user = (task.replace("{source_material}", str(item.get("text") or ""))
                .replace("{current_draft}", current_draft or "")
                .replace("{recent_history}", "\n".join(f"· {x}" for x in (history or [])))
                .replace("{operator_instruction}", operator_instruction or "")
                .replace("{review_feedback}", review_feedback or ""))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    out = llm.chat_messages("draft", messages, max_tokens=4000, timeout=110, retries=2).strip()
    if out.upper().startswith("SKIP"):
        return out, True
    q = check(out, history=history or [], max_len=post_max_len())
    if q["ok"]:
        return q["cleaned"], False
    # 质量反馈只追加到作者 user，不回写 system
    messages += [{"role": "assistant", "content": out},
                 {"role": "user", "content": f"上一版仅因以下质量门问题需修复：{q['reason']}。只修该问题，保持其余内容。"}]
    out2 = llm.chat_messages("draft", messages, max_tokens=4000, timeout=110, retries=2).strip()
    if out2.upper().startswith("SKIP"):
        return out2, True
    q2 = check(out2, history=history or [], max_len=post_max_len())
    if not q2["ok"]:
        raise EditorialSkip("作者质量门二次失败:" + q2["reason"])
    return q2["cleaned"], False


def _review(llm, item, text, history):
    hist = "\n".join(f"· {x[:160]}" for x in (history or [])[-8:]) or "（无）"
    user = f"【完整素材】\n{item.get('text')}\n\n【拟发布正文】\n{text}\n\n【近期正文】\n{hist}"
    out = llm.chat("review", REVIEW_PROMPT, user, max_tokens=1400, timeout=100, retries=2)
    d = _json_from(out)
    decision = str(d.get("decision") or "").lower()
    issues = d.get("issues")
    if decision not in ("accept", "revise", "drop") or not isinstance(issues, list):
        raise ValueError("编辑审核协议无效")
    if decision == "accept" and issues:
        raise ValueError("accept不得携带issues")
    if decision == "revise" and not issues:
        raise ValueError("revise必须有具体issues")
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("kind") not in ("fact", "standalone", "logic", "repeat", "redline", "format"):
            raise ValueError("编辑越权或问题类型无效")
        excerpt = str(issue.get("excerpt") or "")
        if excerpt and excerpt not in text:
            raise ValueError("编辑指出的excerpt不在正文中")
    return {"decision": decision, "issues": issues}


def produce_post(llm, item, story_context=None, history=None, analysis_pack=None,
                 revision_instruction="", current_draft=""):
    """自由作者 + 单道底线编辑；具体硬伤最多修复一次。story_context/analysis_pack不参与生成。"""
    reviews = []
    feedback = ""
    for attempt in range(2):
        text, skipped = _author_draft(llm, item, history, revision_instruction,
                                      feedback, current_draft)
        if skipped or not text.strip():
            raise EditorialSkip("作者判定素材不值得独立成稿")
        review = _review(llm, item, text, history)
        reviews.append(review)
        if review["decision"] == "accept":
            return {"text": text.strip(), "reviews": {"editor": review, "history": reviews}}
        if review["decision"] == "drop":
            raise EditorialSkip("编辑判定不值得发布")
        if attempt == 0:
            log_event("editorial", "wcixot", action="revise",
                      note=json.dumps(review["issues"], ensure_ascii=False)[:120])
            feedback = json.dumps(review["issues"], ensure_ascii=False)
            continue
        raise EditorialSkip("二次编辑审核仍失败")
    raise EditorialSkip("编辑审核轮次耗尽")


def revise_ticket(llm, item, instruction, current_draft=""):
    """运营者原话+当前票稿进入作者 user；不解析成新合约，不读取QQ记忆。"""
    from cognitive import recent_history
    return produce_post(llm, item, {}, recent_history(),
                        revision_instruction=instruction or "", current_draft=current_draft)
