# llm.py — P4-1 按职能路由的 LLM 客户端（OpenAI 兼容 chat/completions，零依赖手撸）
# 职能（requirements #1-#13）：draft(成稿) reply(回复) comment(评论) report(运营报告)
#   vision(多模态) —— 每个可独立配 model/api_key/base_url/temperature，缺省回落 default
# 配置（/etc/xar/instance.yaml）:
#   llm:
#     default: {model: glm-4.7, api_key: xxx, base_url: https://open.bigmodel.cn/api/paas/v4}
#     reply:   {model: ..., temperature: 0.9}   # 可选按职能覆盖
# 供应商链：主配置（fallback_models 同 key 换模型）→ fallback_providers（跨供应商
#   完整 key/端点按序互备，如 kimi→阿里deepseek→火山deepseek）；任一家 key 无效/
#   额度耗尽（401/403/404 或全模型429）熔断10分钟跳过，恢复后自动回归原顺位。
# 计量：每次调用 token 用量进审计日志（运营台"模型用量监控"的数据面）
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ledger import log_event  # noqa: E402

FUNCTIONS = ("draft", "reply", "comment", "report", "vision", "analysis", "review", "decision")


class LLMNotConfigured(RuntimeError):
    pass


class _EmptyContent(RuntimeError):
    """推理思考耗尽 max_tokens：确定性失败，不进重试循环"""


class _ProviderDead(RuntimeError):
    """供应商级确定性失败（401/403/404：key 无效/额度耗尽/模型不存在）：
    同 key 换模型无意义，应熔断该供应商并落到下一家。"""

    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


# 供应商熔断器（进程内共享）：tag → {"until": 死亡截止ts, "reason": str, "fails": 连续整链失败数}
_PROVIDER_HEALTH = {}
_BREAKER_SEC = 600


def _prov_tag(prov):
    host = (prov.get("base_url") or "").split("//")[-1].split("/")[0]
    return f"{host}|{(prov.get('api_key') or '')[:8]}"


def _breaker_dead(tag):
    return (_PROVIDER_HEALTH.get(tag) or {}).get("until", 0) > time.time()


def _breaker_kill(tag, reason):
    _PROVIDER_HEALTH[tag] = {"until": time.time() + _BREAKER_SEC,
                             "reason": str(reason)[:80], "fails": 0}


def _breaker_fail(tag):
    """非确定性整链失败计数：连续2次也熔断（偶发网络抖动只记一次不计死）"""
    h = _PROVIDER_HEALTH.setdefault(tag, {"until": 0, "reason": "", "fails": 0})
    h["fails"] += 1
    if h["fails"] >= 2:
        h.update({"until": time.time() + _BREAKER_SEC,
                  "reason": "连续整链失败×2", "fails": 0})


def _breaker_ok(tag):
    _PROVIDER_HEALTH.pop(tag, None)


class LLM:
    def __init__(self, cfg_path="/etc/xar/instance.yaml"):
        import yaml
        try:
            cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            cfg = {}
        self.account = ((cfg.get("account") or {}).get("handle") or "wcixot").lower()
        self.llm = cfg.get("llm") or {}

    def conf(self, fn):
        """职能 → 生效配置；default 回落。任一必填缺失 → None（fail-closed）。
        protocol 非 chat 的通道（如阿里 decision-model-preview 的 systemone 协议）
        对 chat/completions 消费不可达——跳过并回落 default。配置段仍保留在
        instance.yaml 作为资产，由协议适配层决定是否启用（2026-11-10 运营裁定：
        配置应该在，用不用按设计决定）。"""
        for c in (self.llm.get(fn), self.llm.get("default")):
            if (isinstance(c, dict) and c.get("protocol", "chat") == "chat"
                    and c.get("api_key") and c.get("model") and c.get("base_url")):
                return c
        return None

    def chat(self, fn, system, user, temperature=None, max_tokens=1024,
             timeout=60, retries=2) -> str:
        """fn 职能路由 → assistant 文本。未配置抛 LLMNotConfigured。"""
        return self.chat_messages(fn, [{"role": "system", "content": system},
                                       {"role": "user", "content": user}],
                                  temperature=temperature, max_tokens=max_tokens,
                                  timeout=timeout, retries=retries)

    def chat_messages(self, fn, messages, temperature=None, max_tokens=1024,
                      timeout=60, retries=2) -> str:
        """完整 messages 数组版（brain v2 多轮对话用；其余同 chat）。
        供应商链：主配置（fallback_models 同 key 换模型）→ fallback_providers
        （跨供应商完整 key/端点按序互备）。某家 key 无效/额度耗尽时熔断10分钟
        直接跳过，额度恢复后自动回归原顺位——kimi耗尽不再瘫掉主脑与成稿线"""
        c = self.conf(fn)
        if c is None:
            raise LLMNotConfigured(
                f"llm.{fn}（及 default）未配置完整 api_key/model/base_url")
        base = {"messages": messages, "max_tokens": max_tokens}
        # temperature 可省略：个别端点只接受特定值（k3.1-pro 仅允许 1），未配置即不发该参数
        t_conf = temperature if temperature is not None else c.get("temperature")
        if t_conf is not None:
            base["temperature"] = float(t_conf)
        providers = [c] + [fp for fp in (c.get("fallback_providers") or [])
                           if isinstance(fp, dict) and fp.get("model")
                           and fp.get("api_key") and fp.get("base_url")]
        # 多供应商时降低单家重试密度：failover 本身就是更可靠的重试
        eff_retries = retries if len(providers) == 1 else min(retries, 1)
        errs = []
        for pi, prov in enumerate(providers):
            tag = _prov_tag(prov)
            if _breaker_dead(tag):
                errs.append(f"{tag}: 熔断中（{_PROVIDER_HEALTH[tag].get('reason', '')}）")
                continue
            downstream = pi < len(providers) - 1
            pchain = [{"model": prov["model"], "effort": prov.get("reasoning_effort")}] + [
                {"model": fb.get("model"), "effort": fb.get("reasoning_effort")}
                for fb in (prov.get("fallback_models") or []) if fb.get("model")]
            prov_errs, all_429, dead = [], bool(pchain), False
            for mc in pchain:
                body = {**base, "model": mc["model"]}
                if mc.get("effort"):
                    body["reasoning_effort"] = str(mc["effort"])
                try:
                    out = self._post(fn, prov, body, timeout, eff_retries)
                    _breaker_ok(tag)
                    return out
                except _ProviderDead as e:
                    prov_errs.append(f"{mc['model']}: {str(e)[:90]}")
                    dead = True
                    break   # 同 key 同端点换模型无意义，直接下一供应商
                except _EmptyContent as e:
                    # 推理模型（deepseek v4.1 等）思考与输出共享 max_tokens：
                    # 思考吃尽预算时 content 为空。同模型加倍预算重试一次，仍空再走链
                    prov_errs.append(f"{mc['model']}: {str(e)[:70]}")
                    all_429 = False   # 空响应与限流无关，不参与全429熔断判定
                    if max_tokens * 2 <= 16384:
                        try:
                            out = self._post(fn, prov, {**body, "max_tokens": max_tokens * 2},
                                             timeout, eff_retries)
                            _breaker_ok(tag)
                            return out
                        except Exception as e2:
                            prov_errs.append(f"{mc['model']}(预算x2): {str(e2)[:70]}")
                except Exception as e:
                    prov_errs.append(f"{mc['model']}: {str(e)[:90]}")
                    if "HTTP 429" not in str(e):
                        all_429 = False
                    elif downstream:
                        # 429 且还有下家供应商：大概率账号级限流/额度尽，
                        # 不再耗同 key 逐个换模型硬撞，立即 failover（熔断由 all_429 判定）
                        break
            if dead:
                _breaker_kill(tag, "供应商级确定性失败")
            elif all_429:
                _breaker_kill(tag, "全模型429（疑似额度/限流耗尽）")
            else:
                _breaker_fail(tag)
            errs.append(f"{tag}: " + " | ".join(prov_errs))
        if fn != "default" and c.get("fallback_to_default"):
            # 跨职能兜底：如阿里 decision 通道失败，回到实例 default 供应商链
            d = self.conf("default")
            if d:
                log_event("llm", self.account, action="functionFallback", fn=fn,
                          note="决策通道失败，回退default")
                return self.chat_messages("default", messages, temperature=temperature,
                                          max_tokens=max_tokens, timeout=timeout, retries=retries)
        # 全链失败必须落审计——2026-11 阿里 decision 端点静默失败一整月
        # （4205 次 functionFallback 无人发现）的教训：没有日志就没有告警。
        log_event("llm", self.account, action="chainFail", fn=fn,
                  note=(" ‖ ".join(errs))[:200])
        raise RuntimeError(f"llm.{fn} 全供应商链失败（{len(providers)}家）: "
                           + " ‖ ".join(errs))

    def probe_heads(self, fns=("default", "decision", "review")):
        """主动健康探针：逐一 ping 各通道链上每家供应商（只测连通+额度，
        不碰熔断器、不触发回退、不写 token 审计）。返回 [(fn, ok, detail)]。
        2026-11-10 新增：qq_bot 每 30 分钟调用一次，供应商故障/额度耗尽
        在 30 分钟内上 QQ 告警，不再依赖生产调用撞墙后才发现。"""
        out, seen = [], set()
        for fn in fns:
            c = self.conf(fn)
            if not c:
                continue
            chain = [c] + [fp for fp in (c.get("fallback_providers") or [])
                           if isinstance(fp, dict) and fp.get("model")
                           and fp.get("api_key") and fp.get("base_url")]
            for prov in chain:
                tag = _prov_tag(prov)
                if tag in seen:
                    continue
                seen.add(tag)
                body = {"model": prov["model"], "max_tokens": 64,
                        "messages": [{"role": "user", "content": "ping"}]}
                if prov.get("temperature") is not None:
                    body["temperature"] = float(prov["temperature"])
                try:
                    self._post(fn, prov, body, timeout=30, retries=0, quiet=True)
                    out.append((fn, True, f"{prov['model']}@{_prov_tag(prov).split('|')[0]}"))
                except _EmptyContent:
                    # 端点+额度正常（HTTP 200 有 usage），只是思考型模型在小预算下
                    # 思考吃光 token —— 探针目的是连通性，算活
                    out.append((fn, True, f"{prov['model']}@{_prov_tag(prov).split('|')[0]}(思考占满预算,端点活)"))
                except Exception as e:
                    out.append((fn, False, f"{prov['model']}: {str(e)[:100]}"))
        return out

    def _post(self, fn, c, body, timeout, retries, quiet=False) -> str:
        """单模型请求+瞬态退避重试（429/5xx 长退避；4xx 确定性错误快失败）。
        401/402/403/404（key 无效/欠费/额度尽/模型不存在）抛 _ProviderDead，
        由供应商链熔断该家并落到下一 key。quiet=True 时不写 token 审计（探针用）。"""
        last, dead_code = None, None
        for i in range(retries + 1):
            transient = False
            req = urllib.request.Request(
                c["base_url"].rstrip("/") + "/chat/completions",
                data=json.dumps(body, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {c['api_key']}"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    d = json.loads(r.read())
                usage = d.get("usage") or {}
                if not quiet:
                    log_event("llm", self.account, action="chat", fn=fn, model=body["model"],
                              prompt_tokens=usage.get("prompt_tokens"),
                              completion_tokens=usage.get("completion_tokens"))
                ch = (d.get("choices") or [{}])[0]
                content = (ch.get("message") or {}).get("content") or ""
                if not content.strip():
                    # 推理模型思考吃尽 max_tokens 时 content 为空——确定性失败，重试无意义
                    raise _EmptyContent(
                        f"llm.{fn} content 为空（finish={ch.get('finish_reason')}，"
                        f"usage={usage}；推理模型请调大 max_tokens）")
                return content
            except _EmptyContent:
                raise
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:120]}"
                if e.code not in (429, 500, 502, 503, 504):
                    if e.code in (401, 402, 403, 404):
                        dead_code = e.code   # 供应商级死亡：key/额度/模型问题
                    break  # 401/403/400 等确定性错误，重试无意义，直接失败
                transient = True
            except Exception as e:
                last = f"{type(e).__name__}: {e}"[:160]
                transient = isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError))
            # 429/5xx 过载是瞬时的（实测 kimi 秒级恢复）：长退避多扛几次；
            # 网络类超时同理；其余未知错误短退避快失败
            time.sleep((4, 10, 20)[min(i, 2)] if transient else 2 * (i + 1))
        if dead_code is not None:
            raise _ProviderDead(f"llm.{fn} HTTP {dead_code}（key无效/欠费/额度尽/模型不存在）",
                                dead_code)
        raise RuntimeError(f"llm.{fn} 调用失败（重试{retries}次）{last}")


if __name__ == "__main__":
    # 自检：只查配置态，不发网络请求
    llm = LLM()
    for fn in FUNCTIONS:
        c = llm.conf(fn)
        print(f"  {fn}: {'OK ' + c['model'] if c else '未配置'}"
              + ("（default 回落）" if c and fn != 'default' and not llm.llm.get(fn) else ""))
    sys.exit(0)
