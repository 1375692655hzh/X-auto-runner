#!/usr/bin/env python3
# probe_endpoints.py — 新供应商端点打通实测（2026-11-10 换链前探针）
# 每个端点发最小请求，只打印 HTTP 状态/模型/回复前几字，绝不打印 key。
import json, urllib.request, urllib.error, sys

KEY_GLM = sys.argv[1]
KEY_DS = sys.argv[2]
KEY_ALI = sys.argv[3]


def probe(name, base, key, model, candidates=None, max_tokens=16):
    url = base.rstrip("/") + "/chat/completions"
    models = [model] + (candidates or [])
    for m in models:
        body = {"model": m, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": "回复OK两个字符即可"}]}
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            content = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            usage = d.get("usage") or {}
            print(f"[OK]   {name} model={m} → {content[:40]!r} "
                  f"(prompt={usage.get('prompt_tokens')}, completion={usage.get('completion_tokens')})")
            return m
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:120]
            print(f"[FAIL] {name} model={m} → HTTP {e.code}: {msg}")
        except Exception as e:
            print(f"[FAIL] {name} model={m} → {type(e).__name__}: {str(e)[:100]}")
    return None


print("== 1. GLM 自动化主链 ==")
glm_model = probe("glm", "https://open.bigmodel.cn/api/coding/paas/v4",
                  KEY_GLM, "glm-5.3-flash", candidates=["GLM-5.3-Flash", "glm-5.3"])

print("== 2. 阿里决策模型（新 key） ==")
probe("decision", "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
      KEY_ALI, "decision-model-preview")

print("== 3. DeepSeek 官方（搜索 key 有效性） ==")
probe("deepseek", "https://api.deepseek.com", KEY_DS, "deepseek-chat", max_tokens=8)

print("== 4. 阿里图像模型（鉴权探测 GET /models） ==")
try:
    req = urllib.request.Request(
        "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/models",
        headers={"Authorization": f"Bearer {KEY_ALI}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read())
    ids = [m.get("id") for m in (d.get("data") or [])]
    img = [i for i in ids if i and "image" in i.lower()]
    print(f"[OK]   ali /models → {len(ids)} 个模型，图像相关: {img[:5]}")
except urllib.error.HTTPError as e:
    print(f"[FAIL] ali /models → HTTP {e.code}: {e.read().decode('utf-8','replace')[:120]}")
except Exception as e:
    print(f"[FAIL] ali /models → {type(e).__name__}: {str(e)[:100]}")

print("PROBE_GLM_MODEL=" + str(glm_model))
