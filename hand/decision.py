# decision.py — 阿里决策模型适配层：System One 原生 + 兼容chat回退 + 决策账本
# 只做分类/是非/评分；不拥有工具、票据、浏览器或发布权限。
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from ledger import STATE_DIR, file_lock

LOG_FILE = STATE_DIR / "decision-log.jsonl"


class DecisionError(RuntimeError):
    pass


def new_id():
    return uuid.uuid4().hex


def _write(row):
    with file_lock(LOG_FILE):
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _questions_valid(questions):
    if not isinstance(questions, dict) or not questions or len(questions) > 16:
        raise DecisionError("questions须为1-16个")
    for qid, q in questions.items():
        if q.get("type") not in ("choice", "noul", "score"):
            raise DecisionError(f"非法题型:{qid}")
        if q.get("type") in ("choice", "score") and not q.get("criteria"):
            raise DecisionError(f"缺少criteria:{qid}")


def ask(state, questions, scene="generic", account="wcixot", native=True,
        fallback_chat=True, decision_id=None):
    """执行结构化决策。native优先；失败可在无副作用阶段回退兼容chat/Kimi。
    返回 {decision_id, answers, channel, model, latency_ms, confidence...}。"""
    _questions_valid(questions)
    from llm import LLM
    llm = LLM()
    cfg = llm.conf("decision")
    if not cfg:
        raise DecisionError("llm.decision未配置")
    did = decision_id or new_id()
    t0 = time.time()
    if native:
        body = {"model": cfg["model"], "state": state, "questions": questions}
        req = urllib.request.Request(cfg["base_url"].rstrip("/") + "/systemone",
            data=json.dumps(body, ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['api_key']}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read())
            answers = d.get("answers")
            if not isinstance(answers, dict):
                raise DecisionError("System One缺少answers")
            out = {"decision_id": did, "answers": answers, "channel": "systemone",
                   "model": d.get("model") or cfg["model"],
                   "latency_ms": d.get("latency_ms") or int((time.time() - t0) * 1000),
                   "usage": d.get("usage") or {}, "request_id": d.get("request_id")}
            _write({"at": datetime.now().isoformat(), "decision_id": did,
                    "scene": scene, "account": account, "channel": "systemone",
                    "model": out["model"], "latency_ms": out["latency_ms"],
                    "questions": list(questions), "answers": answers,
                    "usage": out["usage"], "request_id": out["request_id"],
                    "state_preview": str(state)[:500]})
            return out
        except Exception as e:
            native_error = f"{type(e).__name__}: {e}"[:180]
            if not fallback_chat:
                _write({"at": datetime.now().isoformat(), "decision_id": did,
                        "scene": scene, "account": account, "channel": "systemone",
                        "error": native_error})
                raise
    else:
        native_error = "disabled"

    # 兼容chat回退：仍只返回决策数据，不生成内容；llm.decision可再回退default
    spec = json.dumps(questions, ensure_ascii=False)
    system = ("你是简单决策模型。只输出JSON：{\"answers\":{问题ID:{\"value\":\"...\"}}}。"
              "choice值必须是criteria选项之一；noul值用0到1表示是概率；score值用等级索引数值。"
              "无法判断时用other/unknown，不得编造候选。")
    user = f"【业务状态】\n{state}\n\n【问题定义】\n{spec}"
    out = llm.chat("decision", system, user, max_tokens=2200, timeout=70, retries=2)
    try:
        d = json.loads(out.strip())
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", out or "", re.S)
        if not m:
            raise DecisionError("兼容决策输出非JSON")
        d = json.loads(m.group(0))
    answers = d.get("answers") or d
    if not isinstance(answers, dict):
        raise DecisionError("兼容决策缺少answers")
    result = {"decision_id": did, "answers": answers, "channel": "chat_fallback",
              "model": cfg["model"], "latency_ms": int((time.time() - t0) * 1000),
              "native_error": native_error}
    _write({"at": datetime.now().isoformat(), "decision_id": did,
            "scene": scene, "account": account, "channel": "chat_fallback",
            "model": cfg["model"], "latency_ms": result["latency_ms"],
            "questions": list(questions), "answers": answers,
            "native_error": native_error, "state_preview": str(state)[:500]})
    return result


def log_shadow_outcome(decision_id, actual):
    """影子决策只记录实际处理结果，不反向改变任何流程。"""
    _write({"at": datetime.now().isoformat(), "decision_id": decision_id,
            "scene": "qq_shadow_outcome", "actual": str(actual)[:500]})


def qq_shadow(message, account, active_ticket="", candidates=None, decision_id=None):
    """QQ只读影子路由：输出封闭意图/候选/风险；不调用工具，不执行动作。"""
    cands = candidates or []
    criteria = {"query": "查询状态/票据/资讯", "read_ticket": "读取某张票",
                "revise": "修改当前票", "publish": "请求发布或批准",
                "chat": "普通对话", "other": "意图不清或其他"}
    ticket_criteria = {str(x): "候选票据" for x in cands[-10:]}
    ticket_criteria.update({"none": "无明确票据", "other": "多候选或无法确定"})
    state = {"message": message, "active_ticket": active_ticket or None,
             "candidate_tickets": cands[-10:]}
    questions = {
        "intent": {"type": "choice", "instructions": "这条QQ消息属于哪种意图？", "criteria": criteria},
        "ticket_ref": {"type": "choice", "instructions": "它明确指向哪张候选票？不确定用other，无票据用none。", "criteria": ticket_criteria},
        "risk": {"type": "choice", "instructions": "意图风险级别？", "criteria": {
            "read": "只读", "revise": "改稿", "publish": "发布/批准", "other": "普通或不清"}}
    }
    return ask(state, questions, scene="qq_shadow", account=account,
               decision_id=decision_id)
