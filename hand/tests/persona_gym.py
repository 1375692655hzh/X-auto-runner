# persona_gym.py — P4-6 验收：30 用例批量实跑（离线，零写动作）
# 每用例：装配 L1+L2+L3（cognitive.assemble 同一函数）→ k3.1-pro（reasoning low）
#   → 机械检查（SKIP 判定/质量门/红线正则）→ LLM 判官逐条裁 pass_checks/fail_checks
#   → 用例 pass = 机械全过 AND pass_checks 全真 AND fail_checks 全假
# 产出：state/persona-gym-<ts>.json + .md（分场景得分、逐检查通过率、失败明细）
# 达标线：≥95% 用例通过（build-plan P4 验收）
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cognitive import assemble  # noqa: E402
from llm import LLM  # noqa: E402
from quality import check as qcheck  # noqa: E402

GYM_DIR = Path("/etc/xar/prompts/persona-gym")
OUT = Path("/opt/xar/state")

FN = {"post-original": "draft", "post-mutual": "draft",
      "reply-original": "reply", "reply-mutual": "reply", "reply-visit": "reply",
      "comment-mutual-visit": "comment", "comment-hot": "comment",
      "comment-quality-follow": "comment", "report": "report"}
MAXTOK = {"draft": 3000, "reply": 2500, "comment": 2500, "report": 3500}
TASK = ("执行上述场景任务。只输出正文本身（回复/评论/成帖/报告），"
        "或按契约整条输出 SKIP。")

# 机械红线正则：只留自动化暴露（低误报）。买卖建议类红线不搞机械正则——
# "止损我不给""目标价先放一边"这类讨论/否定语境会误杀，统一交判官 B 组裁决
RE_BOTEXPOSE = re.compile(r"(作为AI|作为人工智能|我是(一个)?(AI|机器人|脚本|程序)|自动(发布|回复)的)")

# 场景质量门档位（与场景文件元信息一致：互关系 casual，豁免套话开场与结尾标点）
CASUAL = {"post-mutual": True, "reply-mutual": True, "comment-mutual-visit": True}


def mech_checks(scene, out, case):
    """返回 (机械失败列表, is_skip)"""
    fails = []
    is_skip = out.strip().upper() == "SKIP"
    if is_skip:
        for fc in case["fail_checks"]:
            if "SKIP" in fc and "错误" in fc or "不得以 SKIP" in fc:
                fails.append("机械:本例不应 SKIP 却输出了 SKIP")
                break
        return fails, True
    if scene != "report":
        r = qcheck(out, casual=CASUAL.get(scene, False))
        if not r["ok"]:
            fails.append(f"机械:质量门未过——{r['reason']}")
    if RE_BOTEXPOSE.search(out):
        fails.append("机械:疑似自动化身份暴露（红线R5）")
    return fails, False


def _parse_verdicts(d):
    """判官 schema 归一（k3.1-pro 会自作主张用中文键名 A组/B组——三跑实证）：
    返回 (pass列表[(check, 满足bool)], fail列表[(check, 触发bool)])"""
    pv, fv = [], []
    if isinstance(d.get("pass"), list):
        for r in d["pass"]:
            pv.append((str(r.get("check", "")), bool(r.get("satisfied"))))
    if isinstance(d.get("fail"), list):
        for r in d["fail"]:
            fv.append((str(r.get("check", "")), bool(r.get("triggered"))))
    for akey in ("A组", "A 组", "A"):
        a = d.get(akey)
        if isinstance(a, dict):
            for k, v in a.items():
                ok = v.get("通过", v.get("satisfied", v.get("passed"))) if isinstance(v, dict) else v
                pv.append((str(k), bool(ok)))
    for bkey in ("B组", "B 组", "B"):
        b = d.get(bkey)
        if isinstance(b, dict):
            for k, v in b.items():
                trig = v.get("触发", v.get("triggered")) if isinstance(v, dict) else v
                fv.append((str(k), bool(trig)))
    if isinstance(d.get("results"), list):  # 首跑旧 schema
        for r in d["results"]:
            (fv if r.get("kind") == "fail" else pv).append(
                (str(r.get("check", "")), bool(r.get("passed"))))
    return pv, fv


def judge(llm, case, out, is_skip):
    """LLM 判官逐条裁 pass/fail checks → (判官失败列表, 明细)"""
    if is_skip:
        # SKIP 输出只裁 pass_checks 中与 SKIP 语义冲突的（其余对 SKIP 无从判定，不算失败）
        fails = [f"判官:{pc}" for pc in case["pass_checks"]
                 if "SKIP" in pc and "不得" in pc or "不得以 SKIP" in pc]
        return fails, []
    prompt = f"""你是严格的文案验收判官。只依据给定材料裁决，不猜不脑补。

【场景】{case['scene']}
【输入材料】{json.dumps(case['input'], ensure_ascii=False)}
【被检输出】
{out}

【A 组：合格条件（每条须"满足"）】
{chr(10).join('- ' + c for c in case['pass_checks'])}

【B 组：负面清单（每条判"是否被触发"——该坏行为在输出中确实出现才算触发；仅被讨论、否认或排除不算）】
{chr(10).join('- ' + c for c in case['fail_checks'])}

逐条裁决两组，只输出 JSON（无其他文字）：
{{"pass":[{{"check":"<A组原文前30字>","satisfied":true或false,"reason":"<15字内>"}}],
 "fail":[{{"check":"<B组原文前30字>","triggered":true或false,"reason":"<15字内>"}}]}}"""
    # 判官两轮机会：JSON 截断/缺失时带修复提示重试（首二跑实证：k3.1-pro 思考吃 token
    # 会把 JSON 吐一半——5k 预算 + 重试兜底）
    last_err = ""
    for attempt in range(2):
        try:
            raw = llm.chat("report", prompt + last_err, "只输出上述 JSON。",
                           max_tokens=5000, timeout=150, retries=1)
            m = (re.search(r"```json\s*(\{.*\})\s*```", raw, re.S)
                 or re.search(r"(\{.*\})", raw, re.S))
            if m is None:
                last_err = "\n\n（上次没有输出 JSON。重新裁决，这次只输出一个 JSON 对象本身。）"
                continue
            pv, fv = _parse_verdicts(json.loads(m.group(1)))
            if not pv and not fv:
                # 陌生 schema 零检查——绝不静默判过（三跑实证：旧版此处漏判成通过）
                last_err = ("\n\n（上次 JSON 结构无法识别。重新裁决，严格用如下结构："
                            '{"pass":[{"check":"...","satisfied":true,"reason":"..."}],'
                            '"fail":[{"check":"...","triggered":false,"reason":"..."}]}）')
                continue
            fails, detail = [], []
            for check, ok in pv:
                detail.append(f"[{'✓' if ok else '✗'}]须满足:{check[:30]}")
                if not ok:
                    fails.append(f"判官:须满足未满足|{check[:30]}")
            for check, trig in fv:
                detail.append(f"[{'✗' if trig else '✓'}]红线未触:{check[:30]}")
                if trig:
                    fails.append(f"判官:红线触发|{check[:30]}")
            return fails, detail
        except json.JSONDecodeError:
            last_err = "\n\n（上次 JSON 输出被截断。重新裁决，务必输出完整 JSON，reason 压到 10 字内。）"
            continue
        except Exception as e:
            last_err = f"\n\n（上次调用异常 {str(e)[:50]}。重新裁决，只输出完整 JSON。）"
            continue
    return ["判官:两轮未出合法 JSON（未裁决，需人工看输出）"], []


def main():
    cases = []
    for f in sorted(GYM_DIR.glob("cases-*.json")):
        cases.extend(json.loads(f.read_text(encoding="utf-8")))
    print(f"用例总数 {len(cases)}", flush=True)
    llm = LLM()
    rows, byscene = [], {}
    for i, c in enumerate(cases):
        scene = c["scene"]
        slots = {k: (json.dumps(v, ensure_ascii=False, indent=1)
                     if isinstance(v, (dict, list)) else v)
                 for k, v in c["input"].items()}
        t0 = time.time()
        try:
            out = llm.chat(FN.get(scene, "draft"), assemble(scene, slots), TASK,
                           max_tokens=MAXTOK.get(FN.get(scene, "draft"), 1500))
        except Exception as e:
            rows.append({"id": c["id"], "scene": scene, "pass": False,
                         "fails": [f"LLM:{str(e)[:100]}"], "output": "", "detail": []})
            print(f"[{i+1}/{len(cases)}] {c['id']} ✗ LLM失败", flush=True)
            continue
        out = out.strip()
        mfails, is_skip = mech_checks(scene, out, c)
        jfails, detail = judge(llm, c, out, is_skip)
        ok = not mfails and not jfails
        rows.append({"id": c["id"], "scene": scene, "pass": ok,
                     "fails": mfails + jfails, "output": out[:500], "detail": detail})
        byscene.setdefault(scene, [0, 0])
        byscene[scene][0] += int(ok)
        byscene[scene][1] += 1
        print(f"[{i+1}/{len(cases)}] {c['id']} {'✓' if ok else '✗ ' + '; '.join((mfails+jfails))[:80]}"
              f" ({time.time()-t0:.0f}s)", flush=True)
        time.sleep(1)

    npass = sum(r["pass"] for r in rows)
    rate = npass / len(rows) * 100
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [f"# PersonaGym 实跑报告 {ts}", "",
             f"**通过 {npass}/{len(rows)}（{rate:.1f}%）｜达标线 95%｜{'✅ 达标' if rate >= 95 else '❌ 未达标'}**", "",
             "## 分场景"]
    for s, (p, n) in sorted(byscene.items()):
        lines.append(f"- {s}: {p}/{n}")
    lines += ["", "## 未过用例"]
    for r in rows:
        if not r["pass"]:
            lines.append(f"### {r['id']}（{r['scene']}）")
            for f_ in r["fails"]:
                lines.append(f"- {f_}")
            lines.append(f"- 输出：{r['output'][:200]}")
    (OUT / f"persona-gym-{ts}.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / f"persona-gym-{ts}.json").write_text(
        json.dumps({"ts": ts, "rate": round(rate, 1), "rows": rows},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n=== 通过 {npass}/{len(rows)}（{rate:.1f}%）{'✅达标' if rate >= 95 else '❌未达标'}"
          f" → persona-gym-{ts}.md ===", flush=True)


if __name__ == "__main__":
    main()
